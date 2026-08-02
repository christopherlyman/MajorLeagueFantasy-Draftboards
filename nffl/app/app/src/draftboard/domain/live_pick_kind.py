from __future__ import annotations

from collections.abc import Mapping


def classify_live_pick_kind(
    *,
    current_round: int,
    current_team_key: str,
    chosen_player_key: str,
    current_qos: Mapping[
        str,
        Mapping[int, str],
    ],
    active_qo_rounds: int,
    team_name_by_key: Mapping[str, str] | None = None,
) -> str:
    """
    Return QO, POACH, or FA for a live draft selection.

    A reserved QO that is not legally selectable raises ValueError.
    """
    round_number = int(current_round)
    maximum_qo_round = int(active_qo_rounds)
    selecting_team = str(current_team_key)
    selected_player = str(chosen_player_key)

    if not (
        1
        <= round_number
        <= maximum_qo_round
    ):
        return "FA"

    qo_level_by_player: dict[str, int] = {}
    qo_team_by_player: dict[str, str] = {}

    for team_key, levels in (
        current_qos or {}
    ).items():
        for level, player_key in (
            levels or {}
        ).items():
            if not player_key:
                continue

            normalized_player = str(player_key)

            qo_level_by_player[
                normalized_player
            ] = int(level)

            qo_team_by_player[
                normalized_player
            ] = str(team_key)

    if selected_player not in qo_level_by_player:
        return "FA"

    holder_level = qo_level_by_player[
        selected_player
    ]

    holder_team = qo_team_by_player[
        selected_player
    ]

    if (
        holder_team == selecting_team
        and holder_level >= round_number
    ):
        return "QO"

    if (
        holder_team != selecting_team
        and holder_level > round_number
    ):
        return "POACH"

    names = team_name_by_key or {}
    holder_name = names.get(
        holder_team,
        holder_team,
    )

    raise ValueError(
        "Not poach-eligible. "
        f"Reserved for {holder_name} "
        f"at QO{holder_level}."
    )
