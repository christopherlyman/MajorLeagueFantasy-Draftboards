from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import psycopg
import requests


def _find_yahoo_auth_module_path() -> str:
    candidates = [
        "/app/scripts/yahoo/auth.py",
        "/app/app/scripts/yahoo/auth.py",
        "/league_runtime/app/scripts/yahoo/auth.py",
        "/workspace/app/scripts/yahoo/auth.py",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return str(path)
    raise RuntimeError("Could not find Yahoo auth.py inside the app container.")


def get_yahoo_access_token() -> str:
    auth_path = _find_yahoo_auth_module_path()
    spec = importlib.util.spec_from_file_location("nffl_yahoo_auth", auth_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Yahoo auth module from {auth_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.get_access_token())


def fetch_yahoo_json(token: str, url: str) -> dict[str, Any]:
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Yahoo API returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    return response.json()


def _extract_from_blocks(team_blocks: list[Any], key: str) -> Any:
    for block in team_blocks:
        if isinstance(block, dict) and key in block:
            return block[key]
    return None


def _extract_owner(team_blocks: list[Any]) -> tuple[str | None, str | None]:
    managers_block = _extract_from_blocks(team_blocks, "managers")
    if not isinstance(managers_block, list) or not managers_block:
        return None, None

    managers: list[dict[str, Any]] = []

    for item in managers_block:
        if (
            isinstance(item, dict)
            and isinstance(item.get("manager"), dict)
        ):
            managers.append(item["manager"])

    if not managers:
        return None, None

    chosen = next(
        (
            manager
            for manager in managers
            if str(manager.get("is_commissioner", "")) == "1"
        ),
        managers[0],
    )

    nickname = chosen.get("nickname")
    guid = chosen.get("guid")

    return (
        str(nickname).strip() if nickname is not None else None,
        str(guid).strip() if guid is not None else None,
    )


def extract_yahoo_team_rows(
    payload: dict[str, Any],
    *,
    league_key: str,
    season_year: int,
    expected_manager_count: int,
) -> list[dict[str, Any]]:
    league_key = str(league_key or "").strip()
    season_year = int(season_year)
    expected_manager_count = int(expected_manager_count)

    if not league_key:
        raise ValueError("league_key is required.")

    if season_year <= 0:
        raise ValueError("season_year must be positive.")

    if expected_manager_count <= 0:
        raise ValueError("expected_manager_count must be positive.")

    if not isinstance(payload, dict):
        raise ValueError("Yahoo payload must be a mapping.")

    fantasy_content = payload.get("fantasy_content")
    if not isinstance(fantasy_content, dict):
        raise ValueError(
            "Unexpected Yahoo JSON shape: fantasy_content is missing."
        )

    league_list = fantasy_content.get("league")
    if not isinstance(league_list, list) or len(league_list) < 2:
        raise ValueError(
            "Unexpected Yahoo JSON shape: "
            "missing fantasy_content.league[1].teams."
        )

    league_details = league_list[1]
    if not isinstance(league_details, dict):
        raise ValueError(
            "Unexpected Yahoo JSON shape: league[1] is not a mapping."
        )

    teams_container = league_details.get("teams")
    if not isinstance(teams_container, dict):
        raise ValueError(
            "Unexpected Yahoo JSON shape: league[1].teams is not a mapping."
        )

    expected_prefix = f"{league_key}.t."
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    for team_obj in teams_container.values():
        if not isinstance(team_obj, dict):
            continue

        team_outer = team_obj.get("team")
        if not isinstance(team_outer, list) or not team_outer:
            continue

        team_blocks = team_outer[0]
        if not isinstance(team_blocks, list):
            continue

        team_key_raw = _extract_from_blocks(team_blocks, "team_key")
        if team_key_raw is None:
            continue

        team_key = str(team_key_raw).strip()
        if not team_key:
            continue

        if not team_key.startswith(expected_prefix):
            raise ValueError(
                f"Yahoo team_key {team_key!r} does not belong to "
                f"league {league_key!r}."
            )

        if team_key in seen:
            raise ValueError(
                f"Duplicate Yahoo team_key returned: {team_key}."
            )

        seen.add(team_key)

        team_id_raw = _extract_from_blocks(team_blocks, "team_id")
        team_id = None

        if team_id_raw is not None and str(team_id_raw).strip():
            try:
                team_id = int(team_id_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid team_id {team_id_raw!r} "
                    f"for team_key={team_key}."
                ) from exc

        team_name_raw = _extract_from_blocks(team_blocks, "name")
        team_name = (
            str(team_name_raw).strip()
            if team_name_raw is not None
            else None
        )

        owner_name, owner_guid = _extract_owner(team_blocks)

        rows.append(
            {
                "league_key": league_key,
                "season_year": season_year,
                "team_key": team_key,
                "team_id": team_id,
                "team_name": team_name,
                "owner_name": owner_name,
                "owner_guid": owner_guid,
            }
        )

    if len(rows) != expected_manager_count:
        raise ValueError(
            f"Expected {expected_manager_count} Yahoo teams, "
            f"extracted {len(rows)}. Refusing to write."
        )

    return sorted(rows, key=lambda row: str(row["team_key"]))



YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def _walk_yahoo_json(value: Any):
    yield value

    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_yahoo_json(child)

    elif isinstance(value, list):
        for child in value:
            yield from _walk_yahoo_json(child)


def _flatten_yahoo_scalars(value: Any) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    for node in _walk_yahoo_json(value):
        if not isinstance(node, dict):
            continue

        for key, child in node.items():
            if isinstance(child, (str, int, float, bool)) or child is None:
                flattened.setdefault(str(key), child)

    return flattened


def extract_yahoo_game_key(
    payload: dict[str, Any],
    *,
    sport_code: str,
    season_year: int,
) -> str:
    sport_code = str(sport_code or "").strip().lower()
    season_year = int(season_year)

    if not sport_code:
        raise ValueError("sport_code is required.")

    if season_year <= 0:
        raise ValueError("season_year must be positive.")

    matches: set[str] = set()

    for node in _walk_yahoo_json(payload):
        if not isinstance(node, dict) or "game" not in node:
            continue

        flattened = _flatten_yahoo_scalars(node["game"])

        game_key = str(flattened.get("game_key") or "").strip()
        code = str(flattened.get("code") or "").strip().lower()
        season_raw = str(flattened.get("season") or "").strip()

        if not game_key or code != sport_code:
            continue

        try:
            season = int(season_raw)
        except ValueError:
            continue

        if season == season_year:
            matches.add(game_key)

    if not matches:
        raise ValueError(
            f"Yahoo has not exposed a {sport_code} game for "
            f"season {season_year} to the authenticated user."
        )

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one Yahoo {sport_code} game for "
            f"season {season_year}, found {sorted(matches)}."
        )

    return next(iter(matches))


def resolve_yahoo_league_key(
    league_id: str | int,
    *,
    season_year: int,
    sport_code: str = "nfl",
) -> str:
    league_id_text = str(league_id or "").strip()

    if not league_id_text.isdigit() or int(league_id_text) <= 0:
        raise ValueError(
            "Yahoo League ID must be the positive numeric ID "
            "shown in the Yahoo league header or URL."
        )

    token = get_yahoo_access_token()
    url = (
        f"{YAHOO_FANTASY_BASE}/"
        "users;use_login=1/games?format=json"
    )
    payload = fetch_yahoo_json(token, url)

    game_key = extract_yahoo_game_key(
        payload,
        sport_code=sport_code,
        season_year=season_year,
    )

    return f"{game_key}.l.{league_id_text}"


def replace_yahoo_team_map(
    dsn: str,
    *,
    league_key: str,
    season_year: int,
    rows: list[dict[str, Any]],
) -> int:
    league_key = str(league_key or "").strip()
    season_year = int(season_year)

    if not league_key:
        raise ValueError("league_key is required.")

    if season_year <= 0:
        raise ValueError("season_year must be positive.")

    if not rows:
        raise ValueError(
            "Refusing to replace yahoo_team_map with zero rows."
        )

    for row in rows:
        if (
            str(row.get("league_key") or "").strip() != league_key
            or int(row.get("season_year") or 0) != season_year
        ):
            raise ValueError(
                "Yahoo team row identity does not match "
                "the requested league/season."
            )

    values = [
        (
            row["league_key"],
            row["season_year"],
            row["team_key"],
            row["team_id"],
            row["team_name"],
            row["owner_name"],
            row["owner_guid"],
        )
        for row in rows
    ]

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM public.yahoo_team_map
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (league_key, season_year),
            )

            cur.executemany(
                """
                INSERT INTO public.yahoo_team_map (
                    league_key,
                    season_year,
                    team_key,
                    team_id,
                    team_name,
                    owner_name,
                    owner_guid
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                values,
            )

    return len(rows)


def refresh_yahoo_team_map(
    dsn: str,
    *,
    league_key: str,
    season_year: int,
    expected_manager_count: int,
) -> list[dict[str, Any]]:
    league_key = str(league_key or "").strip()

    if not league_key:
        raise ValueError("league_key is required.")

    token = get_yahoo_access_token()

    url = (
        f"{YAHOO_FANTASY_BASE}/league/"
        f"{league_key}/teams?format=json"
    )

    payload = fetch_yahoo_json(token, url)

    rows = extract_yahoo_team_rows(
        payload,
        league_key=league_key,
        season_year=season_year,
        expected_manager_count=expected_manager_count,
    )

    replace_yahoo_team_map(
        dsn,
        league_key=league_key,
        season_year=season_year,
        rows=rows,
    )

    return rows
