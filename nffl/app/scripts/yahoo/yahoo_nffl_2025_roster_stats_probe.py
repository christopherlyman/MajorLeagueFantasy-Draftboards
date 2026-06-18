from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
import psycopg

from auth import get_access_token

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

def load_active_context_from_db() -> dict[str, Any]:
    dsn = (
        os.environ.get("POSTGRES_DSN")
        or os.environ.get("MLF_POSTGRES_DSN")
        or ""
    ).strip()

    if not dsn:
        return {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_season_year,
                    current_league_key,
                    prior_season_year,
                    prior_league_key,
                    draft_key
                FROM nffl.v_active_season_context
                LIMIT 1
                """
            )
            row = cur.fetchone()

    if not row:
        return {}

    return {
        "current_season_year": row[0],
        "current_league_key": row[1],
        "prior_season_year": row[2],
        "prior_league_key": row[3],
        "draft_key": row[4],
    }



def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def fetch_json(token: str, url: str, out_path: Path) -> dict[str, Any]:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    print(f"GET {url}")
    print(f"HTTP {resp.status_code}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(resp.text, encoding="utf-8")

    if resp.status_code >= 400:
        print(resp.text[:1000])
        resp.raise_for_status()

    return resp.json()


def _collect_scalar_dicts(obj: Any) -> dict[str, str]:
    """
    Yahoo often represents a team as a list of one-key dicts:
      [{"team_key": "..."}, {"team_id": "..."}, {"name": "..."} ...]
    This flattens those scalar values.
    """
    out: dict[str, str] = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("team_key", "team_id", "name") and not isinstance(v, (dict, list)):
                out[k] = str(v)
            elif isinstance(v, (dict, list)):
                out.update(_collect_scalar_dicts(v))

    elif isinstance(obj, list):
        for item in obj:
            out.update(_collect_scalar_dicts(item))

    return out


def extract_teams(payload: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    # Preferred Yahoo shape:
    # fantasy_content.league[1].teams.{0..N}.team = [ {"team_key":...}, {"team_id":...}, {"name":...}, ...]
    teams_obj = None
    try:
        league = payload["fantasy_content"]["league"]
        if isinstance(league, list) and len(league) > 1:
            teams_obj = league[1].get("teams")
    except Exception:
        teams_obj = None

    candidate_nodes: list[Any] = []

    if isinstance(teams_obj, dict):
        for k, v in teams_obj.items():
            if str(k).isdigit() and isinstance(v, dict) and "team" in v:
                candidate_nodes.append(v["team"])

    # Fallback: walk everything looking for explicit team nodes/lists.
    for d in walk(payload):
        if isinstance(d, dict) and "team" in d:
            candidate_nodes.append(d["team"])

    for node in candidate_nodes:
        flat = _collect_scalar_dicts(node)
        team_key = flat.get("team_key")
        team_id = flat.get("team_id")
        name = flat.get("name")

        if team_key and team_id and name and team_key not in seen:
            seen.add(team_key)
            out.append(
                {
                    "team_key": team_key,
                    "team_id": team_id,
                    "team_name": name,
                }
            )

    return out


def _collect_player_fields(obj: Any) -> dict[str, str]:
    out: dict[str, str] = {}

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


def extract_players(payload: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    candidate_nodes: list[Any] = []

    # Preferred roster/stats shapes often contain {"player": [[...], {...stats...}]} or {"player": [...]}
    for d in walk(payload):
        if isinstance(d, dict) and "player" in d:
            candidate_nodes.append(d["player"])

    # Fallback: include any larger node that contains player_key somewhere under it.
    for d in walk(payload):
        if isinstance(d, dict):
            flat = _collect_player_fields(d)
            if flat.get("player_key") and flat.get("player_name"):
                candidate_nodes.append(d)

    for node in candidate_nodes:
        flat = _collect_player_fields(node)
        player_key = flat.get("player_key")
        player_name = flat.get("player_name")

        if not player_key or not player_name or player_key in seen:
            continue

        seen.add(player_key)
        out.append(
            {
                "player_key": player_key,
                "player_name": player_name,
                "editorial_team_abbr": flat.get("editorial_team_abbr", ""),
                "display_position": flat.get("display_position", ""),
                "roster_slot": flat.get("roster_slot", ""),
                "status": flat.get("status", ""),
            }
        )

    return out


def main() -> None:
    ctx = load_active_context_from_db()

    source_league_key = (
        os.environ.get("YAHOO_SOURCE_LEAGUE_KEY")
        or str(ctx.get("prior_league_key") or "")
    ).strip()

    if not source_league_key:
        raise SystemExit("Missing YAHOO_SOURCE_LEAGUE_KEY and no nffl.v_active_season_context row was found.")

    source_season_year = int(
        os.environ.get("YAHOO_SOURCE_SEASON_YEAR")
        or ctx.get("prior_season_year")
        or 0
    )

    if not source_season_year:
        raise SystemExit("Missing YAHOO_SOURCE_SEASON_YEAR and no prior_season_year was found in nffl.v_active_season_context.")

    print(
        "ACTIVE_CONTEXT "
        f"current={ctx.get('current_season_year')} "
        f"current_league={ctx.get('current_league_key')} "
        f"prior={source_season_year} "
        f"prior_league={source_league_key}"
    )
    roster_week = os.environ.get("YAHOO_ROSTER_WEEK", "").strip()
    raw_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo")) / "nffl_2025_roster_probe"

    token = get_access_token()

    teams_url = f"{BASE}/league/{source_league_key}/teams?format=json"
    teams_payload = fetch_json(token, teams_url, raw_dir / f"league_{source_league_key.replace('.', '_')}_teams.json")
    teams = extract_teams(teams_payload)

    print()
    print(f"TEAMS_FOUND={len(teams)}")
    for t in teams:
        print(f"{t['team_key']} | {t['team_id']} | {t['team_name']}")

    if not teams:
        raise SystemExit("No teams found; cannot probe roster.")

    team_key = os.environ.get("YAHOO_SOURCE_TEAM_KEY", teams[0]["team_key"])

    if roster_week:
        roster_url = f"{BASE}/team/{team_key}/roster;week={roster_week}?format=json"
        roster_label = f"week{roster_week}"
    else:
        roster_url = f"{BASE}/team/{team_key}/roster?format=json"
        roster_label = "default"

    roster_payload = fetch_json(
        token,
        roster_url,
        raw_dir / f"team_{team_key.replace('.', '_')}_roster_{roster_label}.json",
    )
    roster_players = extract_players(roster_payload)

    print()
    print(f"ROSTER_PROBE_TEAM={team_key}")
    print(f"ROSTER_PLAYERS_FOUND={len(roster_players)}")
    for p in roster_players[:30]:
        print(f"{p['player_key']} | {p['player_name']} | {p['editorial_team_abbr']} | {p['display_position']}")

    if roster_players:
        player_keys = ",".join(p["player_key"] for p in roster_players[:25])
        stats_url = f"{BASE}/players;player_keys={player_keys}/stats;type=season;season={source_season_year}?format=json"
        stats_payload = fetch_json(
            token,
            stats_url,
            raw_dir / f"team_{team_key.replace('.', '_')}_season{source_season_year}_stats_probe.json",
        )

        stats_players = extract_players(stats_payload)
        print()
        print(f"STATS_PLAYERS_FOUND={len(stats_players)}")

        sample_stats_nodes = []
        for d in walk(stats_payload):
            if "player_stats" in d:
                sample_stats_nodes.append(d["player_stats"])
                if len(sample_stats_nodes) >= 2:
                    break

        print(f"PLAYER_STATS_NODES_FOUND_SAMPLE={len(sample_stats_nodes)}")
        if sample_stats_nodes:
            print("FIRST_PLAYER_STATS_NODE_SAMPLE=")
            print(json.dumps(sample_stats_nodes[0], indent=2)[:2500])

    print()
    print(f"RAW_PROBE_DIR={raw_dir}")


if __name__ == "__main__":
    main()
