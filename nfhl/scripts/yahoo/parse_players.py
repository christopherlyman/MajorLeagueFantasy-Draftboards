from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from parse_common import (
    decimal_or_none,
    direct_value,
    object_list_to_dict,
    outer_segment_value,
    text_or_none,
)


VALID_PRIMARY_POSITIONS = {
    "C",
    "LW",
    "RW",
    "D",
    "G",
}

VALID_POSITION_TYPES = {
    "P",
    "G",
}


def _player_nodes(
    payload: dict[str, Any],
) -> tuple[int, list[list[Any]]]:
    league = (
        payload
        .get("fantasy_content", {})
        .get("league", [])
    )

    if (
        not isinstance(league, list)
        or len(league) < 2
        or not isinstance(league[1], dict)
    ):
        raise ValueError(
            "Unexpected Yahoo players payload: "
            "missing fantasy_content.league[1]."
        )

    container = league[1].get(
        "players"
    )

    if not isinstance(container, dict):
        raise ValueError(
            "Unexpected Yahoo players payload: "
            "league[1].players is not an object."
        )

    declared_raw = container.get(
        "count"
    )

    try:
        declared_count = int(
            declared_raw
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid Yahoo players count: {declared_raw!r}"
        ) from exc

    nodes: list[list[Any]] = []

    def key_order(value: str) -> tuple[int, str]:
        try:
            return (
                0,
                f"{int(value):012d}",
            )
        except (
            TypeError,
            ValueError,
        ):
            return (
                1,
                str(value),
            )

    for container_key in sorted(
        (
            key
            for key in container
            if key != "count"
        ),
        key=key_order,
    ):
        obj = container[
            container_key
        ]

        if not isinstance(obj, dict):
            continue

        outer = obj.get(
            "player"
        )

        if isinstance(outer, list):
            nodes.append(
                outer
            )

    if len(nodes) != declared_count:
        raise ValueError(
            "Yahoo player count mismatch: "
            f"declared={declared_count} "
            f"parsed={len(nodes)}"
        )

    return (
        declared_count,
        nodes,
    )


def _eligible_positions(
    metadata: list[Any],
) -> list[str]:
    raw = direct_value(
        metadata,
        "eligible_positions",
    )

    if not isinstance(raw, list):
        return []

    result: list[str] = []
    seen: set[str] = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        position = text_or_none(
            item.get("position")
        )

        if (
            position
            and position not in seen
        ):
            result.append(
                position
            )
            seen.add(
                position
            )

    return result


def _full_name(
    metadata: list[Any],
) -> str | None:
    raw = direct_value(
        metadata,
        "name",
    )

    if not isinstance(raw, dict):
        return None

    return text_or_none(
        raw.get("full")
        or raw.get(
            "ascii_first_last"
        )
    )


def _canonical_rank(
    outer: list[Any],
    *,
    season_year: int,
) -> Decimal | None:
    raw = outer_segment_value(
        outer,
        "player_ranks",
    )

    if not isinstance(raw, list):
        return None

    overall_values: list[Decimal] = []
    season_values: list[Decimal] = []

    for wrapper in raw:
        if not isinstance(
            wrapper,
            dict,
        ):
            continue

        record = wrapper.get(
            "player_rank"
        )

        if not isinstance(
            record,
            dict,
        ):
            continue

        rank_type = text_or_none(
            record.get(
                "rank_type"
            )
        )

        rank_season = text_or_none(
            record.get(
                "rank_season"
            )
        )

        rank_value = decimal_or_none(
            record.get(
                "rank_value"
            )
        )

        if rank_value is None:
            continue

        if rank_type == "OR":
            overall_values.append(
                rank_value
            )

        elif (
            rank_type == "S"
            and rank_season
            == str(season_year)
        ):
            season_values.append(
                rank_value
            )

    overall_unique = set(
        overall_values
    )
    season_unique = set(
        season_values
    )

    if len(overall_unique) > 1:
        raise ValueError(
            "Conflicting Yahoo OR rank values."
        )

    if len(season_unique) > 1:
        raise ValueError(
            "Conflicting Yahoo current-season rank values."
        )

    overall = (
        next(iter(overall_unique))
        if overall_unique
        else None
    )

    seasonal = (
        next(iter(season_unique))
        if season_unique
        else None
    )

    if (
        overall is not None
        and seasonal is not None
        and overall != seasonal
    ):
        raise ValueError(
            "Yahoo OR rank and current-season rank disagree: "
            f"OR={overall} "
            f"S/{season_year}={seasonal}"
        )

    if overall is not None:
        return overall

    return seasonal


def _percent_owned(
    outer: list[Any],
) -> Decimal | None:
    raw = outer_segment_value(
        outer,
        "percent_owned",
    )

    fields = object_list_to_dict(
        raw
    )

    return decimal_or_none(
        fields.get("value")
    )


def _draft_analysis(
    outer: list[Any],
) -> dict[str, Any]:
    raw = outer_segment_value(
        outer,
        "draft_analysis",
    )

    return object_list_to_dict(
        raw
    )


def parse_players_payload(
    payload: dict[str, Any],
    *,
    league_key: str,
    season_year: int,
) -> list[dict[str, Any]]:
    _, nodes = _player_nodes(
        payload
    )

    rows: list[dict[str, Any]] = []

    for outer in nodes:
        if (
            not outer
            or not isinstance(
                outer[0],
                list,
            )
        ):
            raise ValueError(
                "Yahoo player object is missing metadata list."
            )

        metadata = outer[0]

        player_key = text_or_none(
            direct_value(
                metadata,
                "player_key",
            )
        )

        full_name = _full_name(
            metadata
        )

        if not player_key:
            raise ValueError(
                "Yahoo player object is missing player_key."
            )

        if not full_name:
            raise ValueError(
                f"Yahoo player {player_key} is missing full name."
            )

        primary_position = (
            text_or_none(
                direct_value(
                    metadata,
                    "primary_position",
                )
            )
        )

        position_type = (
            text_or_none(
                direct_value(
                    metadata,
                    "position_type",
                )
            )
        )

        if (
            primary_position
            not in VALID_PRIMARY_POSITIONS
        ):
            raise ValueError(
                f"{player_key}: unsupported primary_position="
                f"{primary_position!r}"
            )

        if (
            position_type
            not in VALID_POSITION_TYPES
        ):
            raise ValueError(
                f"{player_key}: unsupported position_type="
                f"{position_type!r}"
            )

        status_raw = direct_value(
            metadata,
            "status",
        )

        if isinstance(
            status_raw,
            bool,
        ):
            raise ValueError(
                f"{player_key}: boolean status leak detected."
            )

        player_status = text_or_none(
            status_raw
        )

        draft_analysis = (
            _draft_analysis(
                outer
            )
        )

        rows.append(
            {
                "league_key":
                    league_key,
                "season_year":
                    season_year,
                "yahoo_player_key":
                    player_key,
                "source_game_key":
                    league_key.split(
                        ".",
                        1,
                    )[0],
                "full_name":
                    full_name,
                "nhl_team_abbr":
                    text_or_none(
                        direct_value(
                            metadata,
                            "editorial_team_abbr",
                        )
                    ),
                "eligible_positions":
                    _eligible_positions(
                        metadata
                    ),
                "primary_position":
                    primary_position,
                "position_type":
                    position_type,
                "player_status":
                    player_status,
                "percent_owned":
                    _percent_owned(
                        outer
                    ),
                "rank_value":
                    _canonical_rank(
                        outer,
                        season_year=
                            season_year,
                    ),
                "percent_drafted":
                    decimal_or_none(
                        draft_analysis.get(
                            "percent_drafted"
                        )
                    ),
                "preseason_percent_drafted":
                    decimal_or_none(
                        draft_analysis.get(
                            "preseason_percent_drafted"
                        )
                    ),
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

    keys = [
        row[
            "yahoo_player_key"
        ]
        for row in rows
    ]

    if len(keys) != len(set(keys)):
        raise ValueError(
            "Duplicate Yahoo player key detected."
        )

    return rows
