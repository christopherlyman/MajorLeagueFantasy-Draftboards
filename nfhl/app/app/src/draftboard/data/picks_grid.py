from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PickSlot:
    round_number: int
    pick_in_round: int
    overall_pick: int
    team_key: str


def build_pick_grid(
    team_keys: Sequence[str],
    *,
    manager_count: int,
    rounds_total: int,
    order_mode: str,
) -> list[PickSlot]:
    """
    Build a production draft grid.

    This function intentionally requires the complete manager field.
    Roll-call / PREP mode must not fabricate missing Yahoo teams.
    """

    teams = list(team_keys)

    if manager_count <= 0:
        raise ValueError("manager_count must be positive")

    if rounds_total <= 0:
        raise ValueError("rounds_total must be positive")

    if len(teams) != manager_count:
        raise ValueError(
            f"Production draft requires exactly {manager_count} teams; "
            f"received {len(teams)}."
        )

    if len(set(teams)) != len(teams):
        raise ValueError("Duplicate team keys are not allowed")

    if order_mode not in {"straight", "snake"}:
        raise ValueError(
            "order_mode must be explicitly verified as 'straight' or 'snake'"
        )

    slots: list[PickSlot] = []
    overall_pick = 1

    # NFHL_SNAKE_FIXED_SLOT_ORDER
    #
    # pick_in_round is the PERMANENT board/lottery slot.
    # The returned list order is the chronological execution order.
    #
    # Example for 14-team snake:
    #   Round 1 list: slots 1..14
    #   Round 2 list: slots 14..1
    #   Round 3 list: slots 1..14
    #
    # The team assigned to a board slot never changes by round.
    fixed_slots = list(
        range(
            1,
            manager_count + 1,
        )
    )

    for round_number in range(
        1,
        rounds_total + 1,
    ):
        execution_slots = fixed_slots

        if (
            order_mode == "snake"
            and round_number % 2 == 0
        ):
            execution_slots = list(
                reversed(
                    fixed_slots
                )
            )

        for slot_number in execution_slots:
            team_key = teams[
                slot_number - 1
            ]

            slots.append(
                PickSlot(
                    round_number=round_number,
                    pick_in_round=slot_number,
                    overall_pick=overall_pick,
                    team_key=team_key,
                )
            )

            overall_pick += 1

    return slots
