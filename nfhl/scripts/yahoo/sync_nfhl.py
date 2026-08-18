from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from parse_players import parse_players_payload
from parse_teams import parse_teams_payload


TEAMS_FILE = "league_477_l_10961_teams_sample.json"
PLAYERS_FILE = "league_477_l_10961_players_sample.json"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(payload, dict):
        raise ValueError(
            f"Expected JSON object in {path}"
        )

    return payload


def load_context(
    config_path: Path,
) -> dict[str, Any]:
    config = load_json(
        config_path
    )

    league = config.get(
        "league"
    )

    draft = config.get(
        "draft"
    )

    if not isinstance(
        league,
        dict,
    ):
        raise ValueError(
            "Config missing league object."
        )

    if not isinstance(
        draft,
        dict,
    ):
        raise ValueError(
            "Config missing draft object."
        )

    league_key = str(
        league.get(
            "league_key",
            "",
        )
    ).strip()

    season_year_raw = league.get(
        "season_year"
    )

    target_raw = league.get(
        "manager_count_target"
    )

    draft_key = str(
        draft.get(
            "draft_key",
            "",
        )
    ).strip()

    if not league_key:
        raise ValueError(
            "Config league_key is missing."
        )

    if not draft_key:
        raise ValueError(
            "Config draft_key is missing."
        )

    try:
        season_year = int(
            season_year_raw
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "Config season_year is invalid."
        ) from exc

    try:
        target_team_count = int(
            target_raw
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "Config manager_count_target is invalid."
        ) from exc

    if season_year <= 0:
        raise ValueError(
            "season_year must be positive."
        )

    if target_team_count <= 0:
        raise ValueError(
            "manager_count_target must be positive."
        )

    return {
        "league_key":
            league_key,
        "season_year":
            season_year,
        "target_team_count":
            target_team_count,
        "draft_key":
            draft_key,
    }


def readiness_status(
    *,
    current: int,
    target: int,
) -> str:
    return (
        "READY"
        if current == target
        else "PREP"
    )


def build_plan(
    *,
    source_dir: Path,
    config_path: Path,
) -> dict[str, Any]:
    ctx = load_context(
        config_path
    )

    teams_path = (
        source_dir
        / TEAMS_FILE
    )

    players_path = (
        source_dir
        / PLAYERS_FILE
    )

    teams_payload = load_json(
        teams_path
    )

    players_payload = load_json(
        players_path
    )

    teams = parse_teams_payload(
        teams_payload,
        league_key=
            ctx["league_key"],
        season_year=
            ctx["season_year"],
    )

    players = parse_players_payload(
        players_payload,
        league_key=
            ctx["league_key"],
        season_year=
            ctx["season_year"],
    )

    current_team_count = len(
        teams
    )

    target_team_count = ctx[
        "target_team_count"
    ]

    if (
        current_team_count
        > target_team_count
    ):
        raise ValueError(
            "Yahoo returned more teams than the "
            "configured NFHL target: "
            f"{current_team_count} > "
            f"{target_team_count}"
        )

    status = readiness_status(
        current=current_team_count,
        target=target_team_count,
    )

    teams_remaining = max(
        target_team_count
        - current_team_count,
        0,
    )

    return {
        "mode":
            "DRY_RUN",
        "league_key":
            ctx["league_key"],
        "season_year":
            ctx["season_year"],
        "draft_key":
            ctx["draft_key"],

        "teams": {
            "source":
                "Yahoo league teams fixture",
            "parsed_rows":
                current_team_count,
            "target_rows":
                target_team_count,
            "teams_remaining":
                teams_remaining,
            "readiness":
                status,
            "write_policy":
                "UPSERT_REAL_ROWS_ONLY",
            "delete_policy":
                "NO_DELETE",
            "rows": [
                {
                    "team_key":
                        row["team_key"],
                    "team_id":
                        row["team_id"],
                    "team_name":
                        row["team_name"],
                    "owner_name":
                        row["owner_name"],
                }
                for row in teams
            ],
        },

        "players": {
            "source":
                "Yahoo page-1 discovery fixture",
            "parsed_rows":
                len(players),

            # Critical safety property:
            # This saved response is only the first
            # 25-player discovery page.
            "source_complete":
                False,
            "production_write_eligible":
                False,
            "reason":
                (
                    "Discovery fixture contains only "
                    "Yahoo page 1; production player "
                    "sync requires complete pagination."
                ),

            "primary_position_counts":
                _count_values(
                    players,
                    "primary_position",
                ),

            "status_counts":
                _count_status(
                    players
                ),

            "sample_keys": [
                row[
                    "yahoo_player_key"
                ]
                for row in players[:5]
            ],
        },

        "safety": {
            "network_used":
                False,
            "postgres_connected":
                False,
            "database_writes":
                0,
            "team_deletes":
                0,
            "player_deletes":
                0,
        },
    }


def _count_values(
    rows: list[dict[str, Any]],
    key: str,
) -> dict[str, int]:
    result: dict[str, int] = {}

    for row in rows:
        value = row.get(
            key
        )

        label = (
            "<NULL>"
            if value is None
            else str(value)
        )

        result[label] = (
            result.get(
                label,
                0,
            )
            + 1
        )

    return dict(
        sorted(
            result.items()
        )
    )


def _count_status(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    return _count_values(
        rows,
        "player_status",
    )


def print_human_summary(
    plan: dict[str, Any],
) -> None:
    teams = plan["teams"]
    players = plan["players"]

    print(
        "SYNC_MODE="
        + plan["mode"]
    )

    print(
        "LEAGUE_KEY="
        + plan["league_key"]
    )

    print(
        "SEASON_YEAR="
        + str(
            plan["season_year"]
        )
    )

    print(
        "DRAFT_KEY="
        + plan["draft_key"]
    )

    print(
        "TEAM_PARSED_ROWS="
        + str(
            teams["parsed_rows"]
        )
    )

    print(
        "TEAM_TARGET_ROWS="
        + str(
            teams["target_rows"]
        )
    )

    print(
        "TEAM_READINESS="
        + teams["readiness"]
    )

    print(
        "TEAMS_REMAINING="
        + str(
            teams["teams_remaining"]
        )
    )

    print(
        "TEAM_WRITE_POLICY="
        + teams["write_policy"]
    )

    print(
        "TEAM_DELETE_POLICY="
        + teams["delete_policy"]
    )

    for row in teams["rows"]:
        print(
            "TEAM_PLAN "
            f"{row['team_key']} "
            f"id={row['team_id']} "
            f"name={row['team_name']!r} "
            f"owner={row['owner_name']!r}"
        )

    print(
        "PLAYER_PARSED_ROWS="
        + str(
            players["parsed_rows"]
        )
    )

    print(
        "PLAYER_SOURCE_COMPLETE="
        + (
            "YES"
            if players[
                "source_complete"
            ]
            else "NO"
        )
    )

    print(
        "PLAYER_PRODUCTION_WRITE_ELIGIBLE="
        + (
            "YES"
            if players[
                "production_write_eligible"
            ]
            else "NO"
        )
    )

    print(
        "PLAYER_WRITE_BLOCK_REASON="
        + players["reason"]
    )

    print(
        "PLAYER_PRIMARY_POSITION_COUNTS="
        + json.dumps(
            players[
                "primary_position_counts"
            ],
            sort_keys=True,
        )
    )

    print(
        "PLAYER_STATUS_COUNTS="
        + json.dumps(
            players[
                "status_counts"
            ],
            sort_keys=True,
        )
    )

    print(
        "NETWORK_USED=NO"
    )

    print(
        "POSTGRES_CONNECTED=NO"
    )

    print(
        "DATABASE_WRITES=0"
    )

    print(
        "NFHL_SYNC_DRY_RUN=PASS"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "NFHL offline Yahoo sync planner. "
            "This version performs no network "
            "or PostgreSQL operations."
        )
    )

    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--config",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Print complete plan as JSON "
            "instead of human summary."
        ),
    )

    args = parser.parse_args()

    plan = build_plan(
        source_dir=
            args.source_dir,
        config_path=
            args.config,
    )

    if args.json:
        print(
            json.dumps(
                plan,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_human_summary(
            plan
        )


if __name__ == "__main__":
    main()
