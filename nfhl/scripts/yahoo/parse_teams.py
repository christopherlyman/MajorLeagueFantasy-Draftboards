from __future__ import annotations

from typing import Any

from parse_common import direct_value, text_or_none


def _numeric_container_key(value: str) -> tuple[int, str]:
    try:
        return (0, f"{int(value):012d}")
    except (TypeError, ValueError):
        return (1, str(value))


def _extract_owner(
    metadata: list[Any],
) -> tuple[str | None, str | None]:
    raw = direct_value(
        metadata,
        "managers",
    )

    if not isinstance(raw, list):
        return (None, None)

    managers: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        manager = item.get("manager")

        if isinstance(manager, dict):
            managers.append(manager)

    if not managers:
        return (None, None)

    chosen = next(
        (
            manager
            for manager in managers
            if str(
                manager.get(
                    "is_commissioner",
                    "",
                )
            ).strip()
            == "1"
        ),
        managers[0],
    )

    return (
        text_or_none(
            chosen.get("nickname")
        ),
        text_or_none(
            chosen.get("guid")
        ),
    )


def parse_teams_payload(
    payload: dict[str, Any],
    *,
    league_key: str,
    season_year: int,
) -> list[dict[str, Any]]:
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
            "Unexpected Yahoo teams payload: "
            "missing fantasy_content.league[1]."
        )

    container = league[1].get(
        "teams"
    )

    if not isinstance(container, dict):
        raise ValueError(
            "Unexpected Yahoo teams payload: "
            "league[1].teams is not an object."
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
            f"Invalid Yahoo teams count: {declared_raw!r}"
        ) from exc

    rows: list[dict[str, Any]] = []

    for container_key in sorted(
        (
            key
            for key in container
            if key != "count"
        ),
        key=_numeric_container_key,
    ):
        obj = container[
            container_key
        ]

        if not isinstance(obj, dict):
            continue

        outer = obj.get(
            "team"
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

        team_key = text_or_none(
            direct_value(
                metadata,
                "team_key",
            )
        )

        team_id = text_or_none(
            direct_value(
                metadata,
                "team_id",
            )
        )

        team_name = text_or_none(
            direct_value(
                metadata,
                "name",
            )
        )

        if not team_key:
            raise ValueError(
                "Yahoo team object is missing team_key."
            )

        if not team_name:
            raise ValueError(
                f"Yahoo team {team_key} is missing name."
            )

        owner_name, owner_guid = (
            _extract_owner(
                metadata
            )
        )

        rows.append(
            {
                "league_key":
                    league_key,
                "season_year":
                    season_year,
                "team_key":
                    team_key,
                "team_id":
                    team_id,
                "team_name":
                    team_name,
                "owner_name":
                    owner_name,
                "owner_guid":
                    owner_guid,
            }
        )

    if len(rows) != declared_count:
        raise ValueError(
            "Yahoo team count mismatch: "
            f"declared={declared_count} "
            f"parsed={len(rows)}"
        )

    keys = [
        row["team_key"]
        for row in rows
    ]

    if len(keys) != len(set(keys)):
        raise ValueError(
            "Duplicate Yahoo team_key detected."
        )

    return rows
