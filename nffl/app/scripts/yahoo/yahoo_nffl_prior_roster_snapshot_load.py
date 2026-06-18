from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

from auth import get_access_token

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def _collect_player_fields(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "name" and isinstance(v, dict):
                full = v.get("full")
                if full:
                    out["player_name"] = str(full)
            elif k == "selected_position" and isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and item.get("position"):
                        out["roster_slot"] = str(item.get("position"))
            elif k == "player_stats" and isinstance(v, dict):
                out["player_stats"] = v
            elif k in (
                "player_key",
                "player_id",
                "editorial_team_abbr",
                "display_position",
                "status",
                "position_type",
            ) and not isinstance(v, (dict, list)):
                out[k] = str(v)
            elif isinstance(v, (dict, list)):
                out.update(_collect_player_fields(v))

    elif isinstance(obj, list):
        for item in obj:
            out.update(_collect_player_fields(item))

    return out


def extract_players(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidate_nodes: list[Any] = []

    for d in walk(payload):
        if isinstance(d, dict) and "player" in d:
            candidate_nodes.append(d["player"])

    for node in candidate_nodes:
        flat = _collect_player_fields(node)
        player_key = flat.get("player_key")
        player_name = flat.get("player_name")

        if not player_key or not player_name or player_key in seen:
            continue

        seen.add(str(player_key))
        out.append(
            {
                "source_yahoo_player_key": str(player_key),
                "source_player_id": str(flat.get("player_id") or str(player_key).split(".p.")[-1]),
                "player_name": str(player_name),
                "source_team_abbr": str(flat.get("editorial_team_abbr") or ""),
                "display_position": str(flat.get("display_position") or ""),
                "roster_slot": str(flat.get("roster_slot") or ""),
                "roster_status": str(flat.get("status") or ""),
                "raw_player_stats_json": flat.get("player_stats"),
            }
        )

    return out


def stats_to_dict(player_stats: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(player_stats, dict):
        return out

    stats = player_stats.get("stats")
    if not isinstance(stats, list):
        return out

    for item in stats:
        if not isinstance(item, dict):
            continue
        stat = item.get("stat")
        if not isinstance(stat, dict):
            continue
        stat_id = stat.get("stat_id")
        value = stat.get("value")
        if stat_id is not None:
            out[str(stat_id)] = "" if value is None else str(value)

    return out


def fetch_json(token: str, url: str, out_path: Path) -> dict[str, Any]:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=45,
    )
    print(f"GET {url}")
    print(f"HTTP {resp.status_code}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(resp.text, encoding="utf-8")

    if resp.status_code >= 400:
        print(resp.text[:1200])
        resp.raise_for_status()

    return resp.json()


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_context(conn) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select * from nffl.v_active_season_context limit 1")
        row = cur.fetchone()
    if not row:
        raise RuntimeError("No active season context found in nffl.v_active_season_context.")
    return dict(row)


def load_bridge(conn, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select
                b.current_league_key,
                b.current_season_year,
                b.current_team_key,
                b.source_league_key,
                b.source_season_year,
                b.source_team_key,
                t.team_name as current_team_name,
                t.owner_name as current_owner_name
            from nffl.team_season_bridge b
            join nffl.team t
              on t.league_key=b.current_league_key
             and t.season_year=b.current_season_year
             and t.team_key=b.current_team_key
            where b.league_code='NFFL'
              and b.current_league_key=%s
              and b.current_season_year=%s
              and b.source_league_key=%s
              and b.source_season_year=%s
            order by b.current_team_key
            """,
            (
                ctx["current_league_key"],
                ctx["current_season_year"],
                ctx["prior_league_key"],
                ctx["prior_season_year"],
            ),
        )
        rows = list(cur.fetchall())

    if len(rows) != 12:
        raise RuntimeError(f"Expected 12 team bridge rows, found {len(rows)}.")

    return [dict(r) for r in rows]


def load_current_player_id_map(conn, ctx: dict[str, Any]) -> dict[str, dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select
                yahoo_player_key,
                full_name,
                nfl_team_abbr,
                eligible_positions,
                split_part(yahoo_player_key, '.p.', 2) as player_id
            from nffl.player_universe
            where league_key=%s
              and season_year=%s
            """,
            (ctx["current_league_key"], ctx["current_season_year"]),
        )
        rows = list(cur.fetchall())

    return {str(r["player_id"]): dict(r) for r in rows}


def main() -> None:
    dsn = (os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN.")

    raw_root = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo"))
    sleep_seconds = float(os.environ.get("YAHOO_SLEEP_SECONDS", "0.25"))
    stats_batch_size = int(os.environ.get("YAHOO_STATS_BATCH_SIZE", "25"))

    token = get_access_token()

    with psycopg.connect(dsn) as conn:
        ctx = load_context(conn)
        bridge_rows = load_bridge(conn, ctx)
        current_by_player_id = load_current_player_id_map(conn, ctx)

    current_season = int(ctx["current_season_year"])
    prior_season = int(ctx["prior_season_year"])
    current_league = str(ctx["current_league_key"])
    prior_league = str(ctx["prior_league_key"])

    snapshot_id = f"nffl_{current_season}_from_{prior_season}_end_roster"
    snapshot_type = "END_OF_PRIOR_SEASON_ROSTER"
    snapshot_label = f"{prior_season} end-of-season roster for {current_season} NFFL offseason"

    print(
        "ACTIVE_CONTEXT "
        f"current={current_season} current_league={current_league} "
        f"prior={prior_season} prior_league={prior_league} "
        f"snapshot_id={snapshot_id}"
    )

    raw_dir = raw_root / f"nffl_{prior_season}_end_roster_load"

    roster_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []

    for bridge in bridge_rows:
        source_team_key = bridge["source_team_key"]
        current_team_key = bridge["current_team_key"]

        url = f"{BASE}/team/{source_team_key}/roster?format=json"
        payload = fetch_json(
            token,
            url,
            raw_dir / f"team_{source_team_key.replace('.', '_')}_roster.json",
        )

        players = extract_players(payload)
        print(f"TEAM_ROSTER {source_team_key} -> {current_team_key} players={len(players)}")

        for player in players:
            current = current_by_player_id.get(str(player["source_player_id"]))

            if not current:
                excluded_rows.append(
                    {
                        **player,
                        "current_team_key": current_team_key,
                        "source_team_key": source_team_key,
                        "exclude_reason": "NOT_IN_CURRENT_YAHOO_PLAYER_UNIVERSE",
                    }
                )
                continue

            roster_rows.append(
                {
                    **player,
                    "current_team_key": current_team_key,
                    "source_team_key": source_team_key,
                    "current_yahoo_player_key": current["yahoo_player_key"],
                    "current_full_name": current["full_name"],
                    "current_nfl_team_abbr": current["nfl_team_abbr"],
                    "current_eligible_positions": current["eligible_positions"],
                }
            )

        time.sleep(sleep_seconds)

    print(f"ROSTER_ROWS_ELIGIBLE={len(roster_rows)}")
    print(f"ROSTER_ROWS_EXCLUDED_NOT_CURRENT={len(excluded_rows)}")

    # Fetch season stats for source player keys.
    stats_by_source_key: dict[str, dict[str, Any]] = {}
    source_keys = [r["source_yahoo_player_key"] for r in roster_rows]

    for batch_num, batch in enumerate(chunks(source_keys, stats_batch_size), start=1):
        player_keys = ",".join(batch)
        url = f"{BASE}/players;player_keys={player_keys}/stats;type=season;season={prior_season}?format=json"
        payload = fetch_json(
            token,
            url,
            raw_dir / f"stats_batch_{batch_num:03d}.json",
        )

        for p in extract_players(payload):
            stats_by_source_key[p["source_yahoo_player_key"]] = {
                "stats_json": stats_to_dict(p.get("raw_player_stats_json")),
                "raw_player_stats_json": p.get("raw_player_stats_json"),
            }

        time.sleep(sleep_seconds)

    print(f"STATS_ROWS_PARSED={len(stats_by_source_key)}")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into nffl.roster_snapshot (
                    snapshot_id,
                    league_key,
                    season_year,
                    source_season_year,
                    snapshot_type,
                    snapshot_label,
                    source_note,
                    updated_at_utc
                )
                values (
                    %s, %s, %s, %s, %s, %s,
                    %s,
                    now()
                )
                on conflict (snapshot_id)
                do update set
                    league_key=excluded.league_key,
                    season_year=excluded.season_year,
                    source_season_year=excluded.source_season_year,
                    snapshot_type=excluded.snapshot_type,
                    snapshot_label=excluded.snapshot_label,
                    source_note=excluded.source_note,
                    updated_at_utc=now()
                """,
                (
                    snapshot_id,
                    current_league,
                    current_season,
                    prior_season,
                    snapshot_type,
                    snapshot_label,
                    f"Loaded from Yahoo prior league {prior_league}; filtered to players present in current league {current_league} player universe.",
                ),
            )

            cur.execute("delete from nffl.roster_snapshot_player where snapshot_id=%s", (snapshot_id,))

            for r in roster_rows:
                cur.execute(
                    """
                    insert into nffl.roster_snapshot_player (
                        snapshot_id,
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        roster_slot,
                        roster_status,
                        source_note,
                        updated_at_utc
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (
                        snapshot_id,
                        current_league,
                        current_season,
                        r["current_team_key"],
                        r["current_yahoo_player_key"],
                        r.get("roster_slot") or None,
                        r.get("roster_status") or None,
                        f"Source {prior_season} team={r['source_team_key']} player={r['source_yahoo_player_key']} name={r['player_name']}",
                    ),
                )

                stat = stats_by_source_key.get(r["source_yahoo_player_key"], {})
                cur.execute(
                    """
                    insert into nffl.roster_snapshot_player_stats (
                        snapshot_id,
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        source_yahoo_player_key,
                        stats_season_year,
                        stats_json,
                        raw_player_stats_json,
                        updated_at_utc
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
                    on conflict (snapshot_id, team_key, yahoo_player_key, stats_season_year)
                    do update set
                        source_yahoo_player_key=excluded.source_yahoo_player_key,
                        stats_json=excluded.stats_json,
                        raw_player_stats_json=excluded.raw_player_stats_json,
                        updated_at_utc=now()
                    """,
                    (
                        snapshot_id,
                        current_league,
                        current_season,
                        r["current_team_key"],
                        r["current_yahoo_player_key"],
                        r["source_yahoo_player_key"],
                        prior_season,
                        json.dumps(stat.get("stats_json") or {}),
                        json.dumps(stat.get("raw_player_stats_json")),
                    ),
                )

        conn.commit()

    excluded_path = raw_dir / f"{snapshot_id}_excluded_not_current_universe.json"
    excluded_path.write_text(json.dumps(excluded_rows, indent=2), encoding="utf-8")

    print(f"SNAPSHOT_ID={snapshot_id}")
    print(f"DB_ROSTER_ROWS_INSERTED={len(roster_rows)}")
    print(f"DB_STATS_ROWS_UPSERTED={len(roster_rows)}")
    print(f"EXCLUDED_RAW={excluded_path}")


if __name__ == "__main__":
    main()
