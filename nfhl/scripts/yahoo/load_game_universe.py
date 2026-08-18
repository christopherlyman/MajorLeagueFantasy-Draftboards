from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import psycopg


FILE_RE = re.compile(
    r"^game_477_players_start(\d+)_count(\d+)\.json$"
)


def direct(metadata: list[Any], key: str) -> Any:
    for item in metadata:
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def full_name(metadata: list[Any]) -> str | None:
    value = direct(metadata, "name")

    if not isinstance(value, dict):
        return None

    return text_or_none(
        value.get("full")
        or value.get("ascii_first_last")
    )


def eligible_positions(metadata: list[Any]) -> list[str]:
    raw = direct(metadata, "eligible_positions")

    if not isinstance(raw, list):
        return []

    result = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        position = text_or_none(
            item.get("position")
        )

        if position and position not in seen:
            seen.add(position)
            result.append(position)

    return result


def parse_page(payload: dict[str, Any]) -> list[dict[str, Any]]:
    game = (
        payload
        .get("fantasy_content", {})
        .get("game", [])
    )

    if (
        not isinstance(game, list)
        or len(game) < 2
        or not isinstance(game[1], dict)
    ):
        raise RuntimeError(
            "Unexpected Yahoo Game players payload."
        )

    container = game[1].get("players")

    if not isinstance(container, dict):
        raise RuntimeError(
            "Yahoo Game players container missing."
        )

    rows = []

    numeric_keys = sorted(
        (
            key
            for key in container
            if key != "count"
        ),
        key=lambda value: int(value),
    )

    for key in numeric_keys:
        obj = container[key]

        if not isinstance(obj, dict):
            continue

        outer = obj.get("player")

        if (
            not isinstance(outer, list)
            or not outer
            or not isinstance(outer[0], list)
        ):
            continue

        metadata = outer[0]

        player_key = text_or_none(
            direct(metadata, "player_key")
        )

        name = full_name(metadata)

        if not player_key:
            raise RuntimeError(
                "Yahoo player missing player_key."
            )

        if not player_key.startswith("477.p."):
            raise RuntimeError(
                f"Unexpected Yahoo Game 477 player key: {player_key}"
            )

        if not name:
            raise RuntimeError(
                f"{player_key}: missing full name."
            )

        status = direct(metadata, "status")

        if isinstance(status, bool):
            raise RuntimeError(
                f"{player_key}: boolean status leak."
            )

        position_type = text_or_none(
            direct(metadata, "position_type")
        )

        if position_type not in {None, "P", "G"}:
            raise RuntimeError(
                f"{player_key}: unexpected position_type={position_type!r}"
            )

        rows.append(
            {
                "league_key": "477.l.10961",
                "season_year": 2026,
                "yahoo_player_key": player_key,
                "source_game_key": "477",
                "full_name": name,
                "nhl_team_abbr": text_or_none(
                    direct(
                        metadata,
                        "editorial_team_abbr",
                    )
                ),
                "eligible_positions": json.dumps(
                    eligible_positions(metadata),
                    separators=(",", ":"),
                ),
                "primary_position": text_or_none(
                    direct(
                        metadata,
                        "primary_position",
                    )
                ),
                "position_type": position_type,
                "player_status": text_or_none(status),
                "raw_payload": json.dumps(
                    outer,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )

    return rows


SQL = """
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
    raw_payload = EXCLUDED.raw_payload,
    updated_at_utc = now()
"""


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--raw-dir",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--dsn",
        required=True,
    )

    args = parser.parse_args()

    page_files = []

    for path in args.raw_dir.iterdir():
        match = FILE_RE.match(path.name)

        if match:
            page_files.append(
                (
                    int(match.group(1)),
                    int(match.group(2)),
                    path,
                )
            )

    page_files.sort(
        key=lambda item: item[0]
    )

    if not page_files:
        raise RuntimeError(
            "No Game 477 player page files found."
        )

    all_rows = []
    seen = set()

    expected_start = 0
    page_size = None
    last_returned = None

    for start, count, path in page_files:
        if page_size is None:
            page_size = count

        if count != page_size:
            raise RuntimeError(
                "Yahoo capture used inconsistent page sizes."
            )

        if start != expected_start:
            raise RuntimeError(
                f"Missing Yahoo player page: "
                f"expected start={expected_start}, found={start}"
            )

        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        rows = parse_page(payload)

        print(
            f"PAGE start={start} rows={len(rows)}"
        )

        for row in rows:
            key = row["yahoo_player_key"]

            if key in seen:
                raise RuntimeError(
                    f"Duplicate player across pages: {key}"
                )

            seen.add(key)
            all_rows.append(row)

        last_returned = len(rows)
        expected_start += page_size

    if last_returned is None:
        raise RuntimeError(
            "No Yahoo player pages parsed."
        )

    if last_returned >= page_size:
        raise RuntimeError(
            "Capture appears incomplete: final page was full."
        )

    if not all_rows:
        raise RuntimeError(
            "Yahoo Game 477 universe is empty."
        )

    print(
        f"COMPLETE_GAME_477_PLAYER_ROWS={len(all_rows)}"
    )

    with psycopg.connect(args.dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:

                cur.executemany(
                    SQL,
                    all_rows,
                )

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM nfhl.player_universe
                    WHERE league_key = '477.l.10961'
                      AND season_year = 2026
                      AND source_game_key = '477'
                    """
                )

                db_count = int(
                    cur.fetchone()[0]
                )

    print(
        f"DB_GAME_477_PLAYER_ROWS={db_count}"
    )

    print(
        "NFHL_GAME_477_UNIVERSE_LOAD=PASS"
    )


if __name__ == "__main__":
    main()
