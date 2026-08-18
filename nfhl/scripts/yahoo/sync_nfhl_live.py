from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
import requests

from parse_players import parse_players_payload
from parse_teams import parse_teams_payload


BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
APP_NAME = "mlf_tools"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")

    return value


def load_config(path: Path) -> dict[str, Any]:
    payload = load_json(path)

    league = payload.get("league")
    draft = payload.get("draft")

    if not isinstance(league, dict):
        raise RuntimeError("NFHL config missing league object.")

    if not isinstance(draft, dict):
        raise RuntimeError("NFHL config missing draft object.")

    league_key = str(league.get("league_key") or "").strip()
    season_year = int(league.get("season_year"))
    target_teams = int(league.get("manager_count_target"))
    draft_key = str(draft.get("draft_key") or "").strip()

    if not league_key:
        raise RuntimeError("NFHL league_key missing.")

    if not draft_key:
        raise RuntimeError("NFHL draft_key missing.")

    return {
        "league_key": league_key,
        "season_year": season_year,
        "target_teams": target_teams,
        "draft_key": draft_key,
    }


def stored_access_token(dsn: str) -> str:
    """
    Read the current Yahoo access token only.
    This loader does not refresh OAuth credentials.
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    access_token,
                    round(
                        extract(
                            epoch FROM (
                                updated_at
                                + make_interval(secs => expires_in)
                                - now()
                            )
                        )
                    )::bigint
                FROM public.yahoo_oauth_token
                WHERE app_name = %s
                """,
                (APP_NAME,),
            )

            row = cur.fetchone()

    if not row or not row[0]:
        raise RuntimeError("Stored Yahoo access token is unavailable.")

    seconds_remaining = int(row[1] or 0)

    print(
        f"YAHOO_ACCESS_SECONDS_REMAINING={seconds_remaining}",
        flush=True,
    )

    if seconds_remaining <= 300:
        raise RuntimeError(
            "Stored Yahoo access token has <= 300 seconds remaining. "
            "Refusing to begin a full sync."
        )

    return str(row[0])


def yahoo_get(
    session: requests.Session,
    url: str,
) -> dict[str, Any]:
    response = session.get(
        url,
        timeout=45,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Yahoo HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Yahoo response was not a JSON object.")

    return payload


def fetch_teams(
    session: requests.Session,
    league_key: str,
    season_year: int,
    raw_dir: Path,
) -> list[dict[str, Any]]:
    url = (
        f"{BASE}/league/{league_key}/teams"
        "?format=json"
    )

    payload = yahoo_get(session, url)

    path = raw_dir / "teams.json"
    path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    rows = parse_teams_payload(
        payload,
        league_key=league_key,
        season_year=season_year,
    )

    print(f"YAHOO_TEAM_ROWS={len(rows)}", flush=True)

    return rows


def fetch_all_players(
    session: requests.Session,
    league_key: str,
    season_year: int,
    raw_dir: Path,
    page_size: int = 25,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    reached_end = False

    for page_number in range(max_pages):
        start = page_number * page_size

        url = (
            f"{BASE}/league/{league_key}/players;"
            f"start={start};count={page_size};"
            "out=percent_owned;"
            "out=ranks;"
            "out=draft_analysis"
            "?format=json"
        )

        payload = yahoo_get(
            session,
            url,
        )

        raw_path = (
            raw_dir
            / f"players_start_{start}.json"
        )

        raw_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

        rows = parse_players_payload(
            payload,
            league_key=league_key,
            season_year=season_year,
        )

        print(
            f"YAHOO_PLAYER_PAGE="
            f"{page_number + 1} "
            f"START={start} "
            f"ROWS={len(rows)}",
            flush=True,
        )

        for row in rows:
            key = row["yahoo_player_key"]

            if key in seen_keys:
                raise RuntimeError(
                    f"Duplicate Yahoo player across pages: {key}"
                )

            seen_keys.add(key)
            all_rows.append(row)

        if len(rows) < page_size:
            reached_end = True
            break

        time.sleep(0.10)

    if not reached_end:
        raise RuntimeError(
            f"Yahoo player pagination did not terminate "
            f"within {max_pages} pages."
        )

    if not all_rows:
        raise RuntimeError(
            "Yahoo returned zero players."
        )

    print(
        f"YAHOO_COMPLETE_PLAYER_ROWS={len(all_rows)}",
        flush=True,
    )

    return all_rows


TEAM_SQL = """
INSERT INTO nfhl.team (
    league_key,
    season_year,
    team_key,
    team_id,
    team_name,
    owner_name,
    owner_guid,
    created_at_utc,
    updated_at_utc
)
VALUES (
    %(league_key)s,
    %(season_year)s,
    %(team_key)s,
    %(team_id)s,
    %(team_name)s,
    %(owner_name)s,
    %(owner_guid)s,
    now(),
    now()
)
ON CONFLICT (
    league_key,
    season_year,
    team_key
)
DO UPDATE SET
    team_id = EXCLUDED.team_id,
    team_name = EXCLUDED.team_name,
    owner_name = EXCLUDED.owner_name,
    owner_guid = EXCLUDED.owner_guid,
    updated_at_utc = now()
"""


PLAYER_SQL = """
INSERT INTO nfhl.player_universe (
    league_key,
    season_year,
    yahoo_player_key,
    source_game_key,
    full_name,
    nhl_team_abbr,
    eligible_positions,
    primary_position,
    position_type,
    player_status,
    percent_owned,
    rank_value,
    percent_drafted,
    preseason_percent_drafted,
    raw_payload,
    created_at_utc,
    updated_at_utc
)
VALUES (
    %(league_key)s,
    %(season_year)s,
    %(yahoo_player_key)s,
    %(source_game_key)s,
    %(full_name)s,
    %(nhl_team_abbr)s,
    %(eligible_positions)s::jsonb,
    %(primary_position)s,
    %(position_type)s,
    %(player_status)s,
    %(percent_owned)s,
    %(rank_value)s,
    %(percent_drafted)s,
    %(preseason_percent_drafted)s,
    %(raw_payload)s::jsonb,
    now(),
    now()
)
ON CONFLICT (
    league_key,
    season_year,
    yahoo_player_key
)
DO UPDATE SET
    source_game_key = EXCLUDED.source_game_key,
    full_name = EXCLUDED.full_name,
    nhl_team_abbr = EXCLUDED.nhl_team_abbr,
    eligible_positions = EXCLUDED.eligible_positions,
    primary_position = EXCLUDED.primary_position,
    position_type = EXCLUDED.position_type,
    player_status = EXCLUDED.player_status,
    percent_owned = EXCLUDED.percent_owned,
    rank_value = EXCLUDED.rank_value,
    percent_drafted = EXCLUDED.percent_drafted,
    preseason_percent_drafted =
        EXCLUDED.preseason_percent_drafted,
    raw_payload = EXCLUDED.raw_payload,
    updated_at_utc = now()
"""


def prepare_player_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared = []

    for row in rows:
        value = dict(row)

        value["eligible_positions"] = json.dumps(
            value["eligible_positions"],
            separators=(",", ":"),
        )

        prepared.append(value)

    return prepared


def write_database(
    dsn: str,
    *,
    teams: list[dict[str, Any]],
    players: list[dict[str, Any]],
    league_key: str,
    season_year: int,
    draft_key: str,
) -> tuple[int, int, str]:
    player_rows = prepare_player_rows(players)

    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:

                # Lock the NFHL draft context during sync.
                cur.execute(
                    """
                    SELECT status
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    FOR UPDATE
                    """,
                    (draft_key,),
                )

                draft = cur.fetchone()

                if not draft:
                    raise RuntimeError(
                        f"NFHL draft not found: {draft_key}"
                    )

                if str(draft[0]).upper() != "PREP":
                    raise RuntimeError(
                        "Live Yahoo sync is only allowed while "
                        f"NFHL draft status is PREP; found {draft[0]!r}."
                    )

                cur.executemany(
                    TEAM_SQL,
                    teams,
                )

                cur.executemany(
                    PLAYER_SQL,
                    player_rows,
                )

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM nfhl.team
                    WHERE league_key = %s
                      AND season_year = %s
                    """,
                    (
                        league_key,
                        season_year,
                    ),
                )

                team_count = int(
                    cur.fetchone()[0]
                )

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM nfhl.player_universe
                    WHERE league_key = %s
                      AND season_year = %s
                    """,
                    (
                        league_key,
                        season_year,
                    ),
                )

                player_count = int(
                    cur.fetchone()[0]
                )

                cur.execute(
                    """
                    SELECT status
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    """,
                    (draft_key,),
                )

                draft_status = str(
                    cur.fetchone()[0]
                )

    return (
        team_count,
        player_count,
        draft_status,
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--raw-dir",
        required=True,
        type=Path,
    )

    args = parser.parse_args()

    dsn = str(
        os.environ.get("POSTGRES_DSN") or ""
    ).strip()

    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required.")

    ctx = load_config(
        args.config
    )

    raw_dir = args.raw_dir
    raw_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    token = stored_access_token(
        dsn
    )

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}"
        }
    )

    # ------------------------------------------------------------
    # FETCH EVERYTHING BEFORE DATABASE WRITE
    # ------------------------------------------------------------

    teams = fetch_teams(
        session,
        ctx["league_key"],
        ctx["season_year"],
        raw_dir,
    )

    players = fetch_all_players(
        session,
        ctx["league_key"],
        ctx["season_year"],
        raw_dir,
    )

    if len(teams) > ctx["target_teams"]:
        raise RuntimeError(
            f"Yahoo returned {len(teams)} teams but "
            f"NFHL target is {ctx['target_teams']}."
        )

    print(
        "FETCH_AND_PARSE_COMPLETE=PASS",
        flush=True,
    )

    print(
        f"TEAM_READINESS="
        f"{len(teams)}/{ctx['target_teams']} "
        f"{'READY' if len(teams) == ctx['target_teams'] else 'PREP'}",
        flush=True,
    )

    # ------------------------------------------------------------
    # ONE TRANSACTIONAL UPSERT
    # ------------------------------------------------------------

    (
        db_team_count,
        db_player_count,
        draft_status,
    ) = write_database(
        dsn,
        teams=teams,
        players=players,
        league_key=ctx["league_key"],
        season_year=ctx["season_year"],
        draft_key=ctx["draft_key"],
    )

    print(
        f"DB_TEAM_ROWS={db_team_count}",
        flush=True,
    )

    print(
        f"DB_PLAYER_ROWS={db_player_count}",
        flush=True,
    )

    print(
        f"DRAFT_STATUS={draft_status}",
        flush=True,
    )

    print(
        "TEAM_DELETES=0",
        flush=True,
    )

    print(
        "PLAYER_DELETES=0",
        flush=True,
    )

    print(
        "NFHL_LIVE_YAHOO_SYNC=PASS",
        flush=True,
    )


if __name__ == "__main__":
    main()
