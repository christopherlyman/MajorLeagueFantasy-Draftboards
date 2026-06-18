from __future__ import annotations

from draftboard.domain.models import PickSlot, RoundType, Team
from draftboard.domain.rules import QO_ROUNDS, ROUNDS_TOTAL


def build_picks_grid(
    teams: dict[str, Team],
    *,
    order_mode: str = "straight",
    first_standard_round: int = 1,
    qualifying_offers: bool = True,
    rounds_total: int = ROUNDS_TOTAL,
    manager_count: int | None = None,
    qo_rounds: int | None = None,
) -> tuple[dict[str, PickSlot], list[str]]:
    """
    Canonical pick grid.

    Deterministic rules:
    - Team keyspace is whatever `teams` keys are.
    - Slot order is deterministic: ORDER BY team_key.
    - Team count must match active profile manager_count when provided.
    - Snake affects pick traversal for standard rounds only.
    - QO rounds exist only when the active profile enables qualifying offers.
    """
    team_keys = sorted([str(k) for k in (teams or {}).keys()])

    expected_teams = int(manager_count) if manager_count is not None else len(team_keys)
    if expected_teams <= 0:
        raise RuntimeError(
            "Cannot build draft grid: manager_count/team count is zero. "
            "Load league teams before initializing the draft board."
        )

    if len(team_keys) != expected_teams:
        raise RuntimeError(
            f"Cannot build draft grid: expected {expected_teams} teams, found {len(team_keys)}. "
            "Load/verify yahoo_team_map or the league team source before initializing."
        )

    total_rounds = int(rounds_total)
    if total_rounds <= 0:
        raise RuntimeError(f"Cannot build draft grid: rounds_total must be positive, got {total_rounds}")

    qo_rounds_count = int(qo_rounds) if qo_rounds is not None else int(QO_ROUNDS)
    qo_rounds_effective = qo_rounds_count if bool(qualifying_offers) else 0

    if qo_rounds_effective < 0:
        raise RuntimeError(f"Cannot build draft grid: qo_rounds must be non-negative, got {qo_rounds_effective}")

    if qo_rounds_effective > total_rounds:
        raise RuntimeError(
            f"Cannot build draft grid: qo_rounds={qo_rounds_effective} exceeds rounds_total={total_rounds}"
        )

    mode = str(order_mode or "straight").strip().lower()
    if mode not in {"straight", "snake"}:
        raise RuntimeError(f"Cannot build draft grid: unsupported order_mode={mode}")

    picks: dict[str, PickSlot] = {}
    pick_order: list[str] = []

    for rnd in range(1, total_rounds + 1):
        round_pick_ids: list[str] = []

        is_qo_round = rnd <= qo_rounds_effective

        for slot in range(1, expected_teams + 1):
            owner_team_key = team_keys[slot - 1]
            original_team_key = owner_team_key

            if is_qo_round:
                pick_id = f"QO{rnd}-{slot:02d}"
                round_type = RoundType.QO
            else:
                pick_id = f"R{rnd:02d}-{slot:02d}"
                round_type = RoundType.STANDARD

            picks[pick_id] = PickSlot(
                pick_id=pick_id,
                round_type=round_type,
                round_number=rnd,
                slot=slot,
                original_team_key=original_team_key,
                owner_team_key=owner_team_key,
                selected_player_key=None,
                selected_ts_iso=None,
            )
            round_pick_ids.append(pick_id)

        if (not is_qo_round) and mode == "snake":
            standard_round_index = int(rnd) - int(first_standard_round)
            if standard_round_index % 2 == 1:
                round_pick_ids = list(reversed(round_pick_ids))

        pick_order.extend(round_pick_ids)

    return picks, pick_order
