from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import requests


BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

REQUIRED_STATS = {
    "Games Played": "gp",

    "Goals": "g",
    "Assists": "a",
    "Penalty Minutes": "pim",
    "Powerplay Points": "ppp",
    "Shorthanded Points": "shp",
    "Shots on Goal": "sog",
    "Hits": "hit",
    "Blocks": "blk",

    "Wins": "w",
    "Goals Against": "ga",
    "Saves": "sv",
    "Shutouts": "sho",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(value, dict):
        raise RuntimeError(
            f"Expected JSON object: {path}"
        )

    return value


def direct(
    metadata: list[Any],
    key: str,
) -> Any:
    for item in metadata:
        if (
            isinstance(item, dict)
            and key in item
        ):
            return item[key]

    return None


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None

    value = str(value).strip()

    return value or None


def stat_integer(value: Any) -> int:
    if value is None:
        return 0

    raw = str(value).strip()

    if raw in {
        "",
        "-",
        "--",
    }:
        return 0

    return int(
        Decimal(raw)
    )


def decimal_weight(value: Any) -> Decimal:
    return Decimal(
        str(value)
    )


def load_context(
    config_path: Path,
) -> dict[str, Any]:
    config = load_json(
        config_path
    )

    league = config["league"]
    scoring = config["scoring"]
    historical = config[
        "historical_stats"
    ]

    return {
        "league_key":
            str(league["league_key"]),
        "draft_season_year":
            int(league["season_year"]),
        "stats_season_year":
            int(historical["season_year"]),
        "historical_game_key":
            str(historical["game_key"]),
        "scoring":
            scoring,
    }


def yahoo_get(
    session: requests.Session,
    url: str,
) -> dict[str, Any]:
    response = session.get(
        url,
        timeout=45,
    )

    response.raise_for_status()

    value = response.json()

    if not isinstance(value, dict):
        raise RuntimeError(
            "Yahoo response was not a JSON object."
        )

    return value


def stat_id_map(
    session: requests.Session,
    game_key: str,
    raw_dir: Path,
) -> dict[str, str]:
    payload = yahoo_get(
        session,
        (
            f"{BASE}/game/{game_key}/stat_categories"
            "?format=json"
        ),
    )

    (
        raw_dir
        / f"game_{game_key}_stat_categories.json"
    ).write_text(
        json.dumps(
            payload,
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        stats = (
            payload[
                "fantasy_content"
            ]["game"][1][
                "stat_categories"
            ]["stats"]
        )
    except Exception as exc:
        raise RuntimeError(
            "Could not locate Yahoo stat categories."
        ) from exc

    by_name: dict[str, str] = {}

    for wrapper in stats:
        if not isinstance(
            wrapper,
            dict,
        ):
            continue

        stat = wrapper.get(
            "stat"
        )

        if not isinstance(
            stat,
            dict,
        ):
            continue

        name = text_or_none(
            stat.get("name")
        )

        stat_id = text_or_none(
            stat.get("stat_id")
        )

        if name and stat_id:
            by_name[name] = stat_id

    missing = [
        name
        for name in REQUIRED_STATS
        if name not in by_name
    ]

    if missing:
        raise RuntimeError(
            "Yahoo historical game is missing required "
            "NFHL stat categories: "
            + ", ".join(missing)
        )

    result = {
        output_key: by_name[name]
        for name, output_key
        in REQUIRED_STATS.items()
    }

    print(
        "RESOLVED_STAT_IDS="
        + json.dumps(
            result,
            sort_keys=True,
        ),
        flush=True,
    )

    return result


def extract_player_stats(
    outer: list[Any],
) -> dict[str, Any]:
    for segment in outer:
        if (
            isinstance(segment, dict)
            and "player_stats" in segment
        ):
            value = segment[
                "player_stats"
            ]

            if isinstance(
                value,
                dict,
            ):
                return value

    raise RuntimeError(
        "Yahoo player object missing player_stats."
    )


def parse_page(
    payload: dict[str, Any],
    *,
    stat_ids: dict[str, str],
) -> list[dict[str, Any]]:
    try:
        players = (
            payload[
                "fantasy_content"
            ]["game"][1][
                "players"
            ]
        )
    except Exception as exc:
        raise RuntimeError(
            "Could not locate historical player container."
        ) from exc

    if not isinstance(
        players,
        dict,
    ):
        raise RuntimeError(
            "Historical players container is not an object."
        )

    rows = []

    keys = sorted(
        (
            key
            for key in players
            if key != "count"
        ),
        key=lambda value: int(value),
    )

    reverse_stat_ids = {
        yahoo_id: output_key
        for output_key, yahoo_id
        in stat_ids.items()
    }

    for key in keys:
        obj = players[key]

        if not isinstance(
            obj,
            dict,
        ):
            continue

        outer = obj.get(
            "player"
        )

        if (
            not isinstance(outer, list)
            or not outer
            or not isinstance(
                outer[0],
                list,
            )
        ):
            continue

        metadata = outer[0]

        source_player_key = text_or_none(
            direct(
                metadata,
                "player_key",
            )
        )

        player_id = text_or_none(
            direct(
                metadata,
                "player_id",
            )
        )

        source_position_type = (
            text_or_none(
                direct(
                    metadata,
                    "position_type",
                )
            )
        )

        if not source_player_key:
            raise RuntimeError(
                "Historical player missing player_key."
            )

        if not player_id:
            parts = source_player_key.split(
                "."
            )

            if len(parts) != 3:
                raise RuntimeError(
                    "Could not derive Yahoo player ID "
                    f"from {source_player_key!r}."
                )

            player_id = parts[2]

        player_stats = (
            extract_player_stats(
                outer
            )
        )

        raw_stats = player_stats.get(
            "stats"
        )

        if not isinstance(
            raw_stats,
            list,
        ):
            raise RuntimeError(
                f"{source_player_key}: stats list missing."
            )

        values = {
            key: 0
            for key
            in REQUIRED_STATS.values()
        }

        for wrapper in raw_stats:
            if not isinstance(
                wrapper,
                dict,
            ):
                continue

            stat = wrapper.get(
                "stat"
            )

            if not isinstance(
                stat,
                dict,
            ):
                continue

            stat_id = text_or_none(
                stat.get(
                    "stat_id"
                )
            )

            output_key = (
                reverse_stat_ids.get(
                    stat_id
                )
            )

            if not output_key:
                continue

            values[
                output_key
            ] = stat_integer(
                stat.get(
                    "value"
                )
            )

        rows.append(
            {
                "yahoo_player_id":
                    player_id,
                "source_player_key":
                    source_player_key,
                "source_position_type":
                    source_position_type,
                **values,
                "raw_payload":
                    json.dumps(
                        outer,
                        ensure_ascii=False,
                        separators=(
                            ",",
                            ":",
                        ),
                    ),
            }
        )

    return rows


def fetch_all_stats(
    session: requests.Session,
    *,
    game_key: str,
    stat_ids: dict[str, str],
    raw_dir: Path,
    page_size: int = 25,
) -> list[dict[str, Any]]:
    all_rows = []
    seen = set()

    start = 0

    while True:
        url = (
            f"{BASE}/game/{game_key}/players;"
            f"start={start};count={page_size}/"
            "stats;type=season"
            "?format=json"
        )

        payload = yahoo_get(
            session,
            url,
        )

        (
            raw_dir
            / (
                f"game_{game_key}_season_stats_"
                f"start{start}_count{page_size}.json"
            )
        ).write_text(
            json.dumps(
                payload,
                indent=2,
            ),
            encoding="utf-8",
        )

        rows = parse_page(
            payload,
            stat_ids=stat_ids,
        )

        print(
            f"HISTORICAL_PAGE_START={start} "
            f"ROWS={len(rows)}",
            flush=True,
        )

        for row in rows:
            player_id = row[
                "yahoo_player_id"
            ]

            if player_id in seen:
                raise RuntimeError(
                    "Duplicate Yahoo player ID across "
                    f"historical pages: {player_id}"
                )

            seen.add(
                player_id
            )

            all_rows.append(
                row
            )

        if len(rows) < page_size:
            break

        start += page_size

        time.sleep(
            0.10
        )

    if not all_rows:
        raise RuntimeError(
            "Historical Yahoo player stats returned zero rows."
        )

    print(
        "HISTORICAL_SOURCE_ROWS="
        + str(len(all_rows)),
        flush=True,
    )

    return all_rows


def calculate_fpts(
    row: dict[str, Any],
    *,
    position_type: str,
    scoring: dict[str, Any],
) -> tuple[Decimal, Decimal | None]:
    if position_type == "G":
        total = (
            decimal_weight(
                scoring["W"]
            )
            * row["w"]
            +
            decimal_weight(
                scoring["GA"]
            )
            * row["ga"]
            +
            decimal_weight(
                scoring["SV"]
            )
            * row["sv"]
            +
            decimal_weight(
                scoring["SHO"]
            )
            * row["sho"]
        )

    else:
        total = (
            decimal_weight(
                scoring["G"]
            )
            * row["g"]
            +
            decimal_weight(
                scoring["A"]
            )
            * row["a"]
            +
            decimal_weight(
                scoring["PIM"]
            )
            * row["pim"]
            +
            decimal_weight(
                scoring["PPP"]
            )
            * row["ppp"]
            +
            decimal_weight(
                scoring["SHP"]
            )
            * row["shp"]
            +
            decimal_weight(
                scoring["SOG"]
            )
            * row["sog"]
            +
            decimal_weight(
                scoring["HIT"]
            )
            * row["hit"]
            +
            decimal_weight(
                scoring["BLK"]
            )
            * row["blk"]
        )

    total = total.quantize(
        Decimal("0.001")
    )

    gp = int(
        row["gp"]
    )

    per_game = (
        (
            total
            / Decimal(gp)
        ).quantize(
            Decimal("0.0001")
        )
        if gp > 0
        else None
    )

    return (
        total,
        per_game,
    )


UPSERT_SQL = """
INSERT INTO nfhl.player_season_stats (
    league_key,
    draft_season_year,
    stats_season_year,
    yahoo_player_id,
    source_player_key,
    current_yahoo_player_key,
    source_game_key,
    source_position_type,

    gp,
    g,
    a,
    pim,
    ppp,
    shp,
    sog,
    hit,
    blk,

    w,
    ga,
    sv,
    sho,

    nfhl_fpts,
    nfhl_fpts_per_game,

    scoring_snapshot,
    raw_payload,
    created_at_utc,
    updated_at_utc
)
VALUES (
    %(league_key)s,
    %(draft_season_year)s,
    %(stats_season_year)s,
    %(yahoo_player_id)s,
    %(source_player_key)s,
    %(current_yahoo_player_key)s,
    %(source_game_key)s,
    %(source_position_type)s,

    %(gp)s,
    %(g)s,
    %(a)s,
    %(pim)s,
    %(ppp)s,
    %(shp)s,
    %(sog)s,
    %(hit)s,
    %(blk)s,

    %(w)s,
    %(ga)s,
    %(sv)s,
    %(sho)s,

    %(nfhl_fpts)s,
    %(nfhl_fpts_per_game)s,

    %(scoring_snapshot)s::jsonb,
    %(raw_payload)s::jsonb,
    now(),
    now()
)
ON CONFLICT (
    league_key,
    draft_season_year,
    stats_season_year,
    yahoo_player_id
)
DO UPDATE SET
    source_player_key =
        EXCLUDED.source_player_key,
    current_yahoo_player_key =
        EXCLUDED.current_yahoo_player_key,
    source_game_key =
        EXCLUDED.source_game_key,
    source_position_type =
        EXCLUDED.source_position_type,

    gp = EXCLUDED.gp,
    g = EXCLUDED.g,
    a = EXCLUDED.a,
    pim = EXCLUDED.pim,
    ppp = EXCLUDED.ppp,
    shp = EXCLUDED.shp,
    sog = EXCLUDED.sog,
    hit = EXCLUDED.hit,
    blk = EXCLUDED.blk,

    w = EXCLUDED.w,
    ga = EXCLUDED.ga,
    sv = EXCLUDED.sv,
    sho = EXCLUDED.sho,

    nfhl_fpts =
        EXCLUDED.nfhl_fpts,
    nfhl_fpts_per_game =
        EXCLUDED.nfhl_fpts_per_game,

    scoring_snapshot =
        EXCLUDED.scoring_snapshot,
    raw_payload =
        EXCLUDED.raw_payload,
    updated_at_utc = now()
"""


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
        os.environ.get(
            "POSTGRES_DSN",
            ""
        )
    ).strip()

    if not dsn:
        raise RuntimeError(
            "POSTGRES_DSN is required."
        )

    ctx = load_context(
        args.config
    )

    raw_dir = args.raw_dir

    raw_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # auth.py is mounted alongside this loader
    from auth import get_access_token

    token = get_access_token()

    session = requests.Session()

    session.headers.update(
        {
            "Authorization":
                f"Bearer {token}"
        }
    )

    ids = stat_id_map(
        session,
        ctx[
            "historical_game_key"
        ],
        raw_dir,
    )

    historical_rows = fetch_all_stats(
        session,
        game_key=
            ctx[
                "historical_game_key"
            ],
        stat_ids=ids,
        raw_dir=raw_dir,
    )

    # ------------------------------------------------------------
    # Match prior-season Yahoo IDs to the current draft universe.
    # Yahoo game prefixes change by season; numeric player IDs
    # provide the cross-season identity.
    # ------------------------------------------------------------

    with psycopg.connect(
        dsn
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    yahoo_player_key,
                    split_part(
                        yahoo_player_key,
                        '.',
                        3
                    ) AS yahoo_player_id,
                    position_type
                FROM nfhl.player_universe
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (
                    ctx[
                        "league_key"
                    ],
                    ctx[
                        "draft_season_year"
                    ],
                ),
            )

            current_rows = (
                cur.fetchall()
            )

    current_by_id = {
        str(player_id): {
            "current_yahoo_player_key":
                str(player_key),
            "position_type":
                text_or_none(
                    position_type
                ),
        }
        for (
            player_key,
            player_id,
            position_type,
        )
        in current_rows
    }

    prepared = []

    for row in historical_rows:
        current = current_by_id.get(
            row[
                "yahoo_player_id"
            ]
        )

        if current is None:
            continue

        position_type = (
            row[
                "source_position_type"
            ]
            or current[
                "position_type"
            ]
        )

        if position_type not in {
            "P",
            "G",
        }:
            raise RuntimeError(
                "Cannot determine skater/goalie type for "
                + row[
                    "source_player_key"
                ]
            )

        (
            nfhl_fpts,
            nfhl_fpts_per_game,
        ) = calculate_fpts(
            row,
            position_type=
                position_type,
            scoring=
                ctx["scoring"],
        )

        prepared.append(
            {
                **row,

                "league_key":
                    ctx[
                        "league_key"
                    ],

                "draft_season_year":
                    ctx[
                        "draft_season_year"
                    ],

                "stats_season_year":
                    ctx[
                        "stats_season_year"
                    ],

                "current_yahoo_player_key":
                    current[
                        "current_yahoo_player_key"
                    ],

                "source_game_key":
                    ctx[
                        "historical_game_key"
                    ],

                "source_position_type":
                    position_type,

                "nfhl_fpts":
                    nfhl_fpts,

                "nfhl_fpts_per_game":
                    nfhl_fpts_per_game,

                "scoring_snapshot":
                    json.dumps(
                        ctx[
                            "scoring"
                        ],
                        sort_keys=True,
                        separators=(
                            ",",
                            ":",
                        ),
                    ),
            }
        )

    current_ids = set(
        current_by_id
    )

    matched_ids = {
        row[
            "yahoo_player_id"
        ]
        for row in prepared
    }

    print(
        "CURRENT_PLAYER_UNIVERSE_ROWS="
        + str(
            len(current_ids)
        ),
        flush=True,
    )

    print(
        "HISTORICAL_MATCHED_CURRENT_ROWS="
        + str(
            len(prepared)
        ),
        flush=True,
    )

    print(
        "CURRENT_WITHOUT_PRIOR_STATS="
        + str(
            len(
                current_ids
                - matched_ids
            )
        ),
        flush=True,
    )

    if not prepared:
        raise RuntimeError(
            "No historical players matched the "
            "current NFHL player universe."
        )

    with psycopg.connect(
        dsn
    ) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    UPSERT_SQL,
                    prepared,
                )

    print(
        "NFHL_HISTORICAL_STATS_LOAD=PASS",
        flush=True,
    )


if __name__ == "__main__":
    main()
