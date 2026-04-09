import os
import json
import requests
from pathlib import Path

import psycopg

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "data/raw/yahoo/"))


def _extract_from_blocks(team_blocks, key: str):
    for block in team_blocks:
        if isinstance(block, dict) and key in block:
            return block[key]
    return None


def _extract_owner(team_blocks):
    managers_block = _extract_from_blocks(team_blocks, "managers")
    if not isinstance(managers_block, list) or not managers_block:
        return (None, None)

    managers = []
    for item in managers_block:
        if isinstance(item, dict) and "manager" in item and isinstance(item["manager"], dict):
            managers.append(item["manager"])

    if not managers:
        return (None, None)

    # deterministic: commissioner if present else first manager
    chosen = None
    for m in managers:
        if str(m.get("is_commissioner", "")) == "1":
            chosen = m
            break
    if chosen is None:
        chosen = managers[0]

    return (chosen.get("nickname"), chosen.get("guid"))


def _upsert_yahoo_team_map(payload: dict, league_key: str, season_year: int, dsn: str):
    league_list = payload.get("fantasy_content", {}).get("league", [])
    if not isinstance(league_list, list) or len(league_list) < 2:
        raise SystemExit("Unexpected JSON shape: missing fantasy_content.league[1].teams")

    teams_container = league_list[1].get("teams")
    if not isinstance(teams_container, dict):
        raise SystemExit("Unexpected JSON shape: league[1].teams is not a dict")

    rows = []
    for _idx, team_obj in teams_container.items():
        if not isinstance(team_obj, dict):
            continue
        if "team" not in team_obj:
            continue

        team_outer = team_obj.get("team")
        if not isinstance(team_outer, list) or not team_outer:
            continue

        team_blocks = team_outer[0]
        if not isinstance(team_blocks, list):
            continue

        team_key = _extract_from_blocks(team_blocks, "team_key")
        team_id_raw = _extract_from_blocks(team_blocks, "team_id")
        team_name = _extract_from_blocks(team_blocks, "name")
        owner_name, owner_guid = _extract_owner(team_blocks)

        if not team_key:
            continue

        team_id = None
        if team_id_raw is not None and str(team_id_raw).strip() != "":
            try:
                team_id = int(team_id_raw)
            except ValueError:
                raise SystemExit(f"Invalid team_id {team_id_raw!r} for team_key={team_key}")

        rows.append((league_key, season_year, team_key, team_id, team_name, owner_name, owner_guid))

    if len(rows) != 16:
        raise SystemExit(f"Expected 16 teams, extracted {len(rows)}. Refusing to write.")

    upsert_sql = """
    INSERT INTO public.yahoo_team_map (league_key, season_year, team_key, team_id, team_name, owner_name, owner_guid)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (league_key, season_year, team_key)
    DO UPDATE SET
      team_id    = EXCLUDED.team_id,
      team_name  = EXCLUDED.team_name,
      owner_name = EXCLUDED.owner_name,
      owner_guid = EXCLUDED.owner_guid,
      updated_at = now();
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(upsert_sql, rows)

    print(f"Upserted {len(rows)} rows into public.yahoo_team_map for league_key={league_key} season_year={season_year}")


def main():
    league_key = os.environ.get("YAHOO_LEAGUE_KEY")
    if not league_key:
        raise SystemExit("Missing env var YAHOO_LEAGUE_KEY (e.g. 458.l.11506, 458.l.19074, 458.l.20783, 469.l.41640)")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"{YAHOO_FANTASY_BASE}/league/{league_key}/teams?format=json"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"league_{league_key.replace('.','_')}_teams.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("Wrote:", out_path.as_posix())

    # Optional DB upsert (no-op unless enabled)
    if os.environ.get("YAHOO_UPSERT_TEAM_MAP", "").strip() == "1":
        season_year_raw = os.environ.get("SEASON_YEAR")
        if not season_year_raw:
            raise SystemExit("Missing env var SEASON_YEAR (e.g. 2026)")
        try:
            season_year = int(season_year_raw)
        except ValueError:
            raise SystemExit(f"Invalid SEASON_YEAR: {season_year_raw!r}")

        dsn = os.environ.get("MLF_POSTGRES_DSN")
        if not dsn:
            raise SystemExit("Missing env var MLF_POSTGRES_DSN")

        _upsert_yahoo_team_map(payload, league_key=league_key, season_year=season_year, dsn=dsn)


if __name__ == "__main__":
    main()