from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
import requests

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    return int(raw) if raw else default


def env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    return float(raw) if raw else default


def find_value(blocks: list[Any], key: str) -> Any:
    for item in blocks:
        if isinstance(item, dict):
            if key in item:
                return item[key]
            for v in item.values():
                if isinstance(v, list):
                    found = find_value(v, key)
                    if found is not None:
                        return found
                elif isinstance(v, dict) and key in v:
                    return v[key]
    return None


def extract_player_blocks(payload: dict[str, Any]) -> list[list[Any]]:
    league = payload.get("fantasy_content", {}).get("league", [])
    if not isinstance(league, list) or len(league) < 2 or not isinstance(league[1], dict):
        return []

    players = league[1].get("players")
    if not isinstance(players, dict):
        return []

    blocks: list[list[Any]] = []
    for k, v in players.items():
        if k == "count":
            continue
        if isinstance(v, dict) and isinstance(v.get("player"), list) and v["player"]:
            player_blocks = v["player"][0]
            if isinstance(player_blocks, list):
                blocks.append(player_blocks)
    return blocks


def find_player_key(blocks: list[Any]) -> str:
    value = find_value(blocks, "player_key")
    return "" if value is None else str(value).strip()


def find_full_name(blocks: list[Any]) -> str:
    name = find_value(blocks, "name")
    if isinstance(name, dict):
        return str(name.get("full") or name.get("ascii_first_last") or "").strip()
    return "" if name is None else str(name).strip()


def find_editorial_team_abbr(blocks: list[Any]) -> str | None:
    value = find_value(blocks, "editorial_team_abbr")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def find_positions(blocks: list[Any]) -> list[str]:
    raw = find_value(blocks, "eligible_positions")
    out: list[str] = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("position"):
                out.append(str(item["position"]).strip())
            elif isinstance(item, str):
                out.append(item.strip())

    return [x for x in out if x]


def find_percent_owned(blocks: list[Any]) -> str | None:
    raw = find_value(blocks, "percent_owned")
    if isinstance(raw, dict):
        for key in ("value", "percentage", "percent_owned"):
            if raw.get(key) is not None:
                return str(raw[key]).strip()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                for key in ("value", "percentage", "percent_owned"):
                    if item.get(key) is not None:
                        return str(item[key]).strip()
    if raw is not None:
        return str(raw).strip()
    return None


def find_rank_value(blocks: list[Any]) -> str | None:
    for key in ("rank", "rank_value", "overall_rank"):
        raw = find_value(blocks, key)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()

    draft_analysis = find_value(blocks, "draft_analysis")
    if isinstance(draft_analysis, dict):
        for key in ("average_pick", "average_round", "average_cost"):
            if draft_analysis.get(key) not in (None, ""):
                return str(draft_analysis[key]).strip()

    return None


def make_row(league_key: str, season_year: int, blocks: list[Any]) -> dict[str, Any] | None:
    pkey = find_player_key(blocks)
    name = find_full_name(blocks)

    if not pkey or not name:
        return None

    return {
        "league_key": league_key,
        "season_year": season_year,
        "yahoo_player_key": pkey,
        "source_game_key": league_key.split(".")[0],
        "full_name": name,
        "editorial_team_abbr": find_editorial_team_abbr(blocks),
        "eligible_positions": json.dumps(find_positions(blocks)),
        "percent_owned": find_percent_owned(blocks),
        "rank_value": find_rank_value(blocks),
        "has_qo": False,
        "qo_level": None,
        "is_poachable_this_round": False,
        "raw_payload": json.dumps(blocks),
    }


def upsert_rows(dsn: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    sql = """
    INSERT INTO public.yahoo_league_player_pool (
        league_key,
        season_year,
        yahoo_player_key,
        source_game_key,
        full_name,
        editorial_team_abbr,
        eligible_positions,
        percent_owned,
        rank_value,
        has_qo,
        qo_level,
        is_poachable_this_round,
        raw_payload,
        created_at_utc,
        updated_at_utc
    ) VALUES (
        %(league_key)s,
        %(season_year)s,
        %(yahoo_player_key)s,
        %(source_game_key)s,
        %(full_name)s,
        %(editorial_team_abbr)s,
        %(eligible_positions)s::jsonb,
        NULLIF(%(percent_owned)s, '')::numeric,
        NULLIF(%(rank_value)s, '')::numeric,
        %(has_qo)s,
        %(qo_level)s,
        %(is_poachable_this_round)s,
        %(raw_payload)s::jsonb,
        now(),
        now()
    )
    ON CONFLICT (league_key, season_year, yahoo_player_key)
    DO UPDATE SET
        source_game_key = EXCLUDED.source_game_key,
        full_name = EXCLUDED.full_name,
        editorial_team_abbr = EXCLUDED.editorial_team_abbr,
        eligible_positions = EXCLUDED.eligible_positions,
        percent_owned = EXCLUDED.percent_owned,
        rank_value = EXCLUDED.rank_value,
        raw_payload = EXCLUDED.raw_payload,
        updated_at_utc = now();
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)

    return len(rows)


def main() -> None:
    league_key = str(os.environ.get("YAHOO_LEAGUE_KEY") or os.environ.get("LEAGUE_KEY") or "").strip()
    if not league_key:
        raise SystemExit("Missing YAHOO_LEAGUE_KEY or LEAGUE_KEY")

    season_year = env_int("SEASON_YEAR", 2026)
    dsn = str(os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN")

    page_size = env_int("YAHOO_COUNT", 25)
    max_pages = env_int("YAHOO_MAX_PAGES", 300)
    sleep_seconds = env_float("YAHOO_SLEEP_SECONDS", 0.25)
    replace_pool = str(os.environ.get("YAHOO_REPLACE_POOL", "0")).strip() == "1"

    out_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo/player_universe"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if replace_pool:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM public.yahoo_league_player_pool
                    WHERE league_key = %s
                      AND season_year = %s
                    """,
                    (league_key, season_year),
                )
        print(f"REPLACED_EXISTING_POOL league_key={league_key} season_year={season_year}", flush=True)

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    total_rows = 0
    total_written = 0

    print(
        f"BEGIN_NFFL_PLAYER_UNIVERSE league_key={league_key} season_year={season_year} "
        f"page_size={page_size} max_pages={max_pages}",
        flush=True,
    )

    for page_num in range(max_pages):
        start = page_num * page_size
        url = (
            f"{YAHOO_FANTASY_BASE}/league/{league_key}/players;"
            f"start={start};count={page_size};"
            f"out=percent_owned;out=ranks;out=draft_analysis?format=json"
        )

        resp = requests.get(url, headers=headers, timeout=45)
        print(f"page={page_num + 1} start={start} status={resp.status_code}", flush=True)

        if resp.status_code != 200:
            print(resp.text[:2000], flush=True)
            raise SystemExit(f"Yahoo request failed with status={resp.status_code}")

        payload = resp.json()
        raw_path = out_dir / f"league_{league_key.replace('.', '_')}_players_start_{start}.json"
        raw_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        blocks = extract_player_blocks(payload)
        rows = []
        for block in blocks:
            row = make_row(league_key, season_year, block)
            if row:
                rows.append(row)

        written = upsert_rows(dsn, rows)
        total_rows += len(rows)
        total_written += written

        print(
            f"page={page_num + 1} blocks={len(blocks)} rows={len(rows)} "
            f"written={written} cumulative_written={total_written}",
            flush=True,
        )

        if len(blocks) == 0:
            break

        if len(blocks) < page_size:
            break

        time.sleep(sleep_seconds)

    print(
        f"DONE_NFFL_PLAYER_UNIVERSE league_key={league_key} season_year={season_year} "
        f"rows_seen={total_rows} rows_written={total_written}",
        flush=True,
    )


if __name__ == "__main__":
    main()
