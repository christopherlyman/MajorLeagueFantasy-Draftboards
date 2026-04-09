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
) -> tuple[dict[str, PickSlot], list[str]]:
    """
    Canonical pick grid.
    - Team keyspace is whatever `teams` keys are (Yahoo keys in production).
    - Slot order is deterministic: ORDER BY team_key.
    - Snake affects pick traversal for standard rounds only; base slot ownership stays fixed.
    - QO rounds exist only when the active profile enables qualifying offers.
    """
    team_keys = sorted([str(k) for k in (teams or {}).keys()])
    if len(team_keys) != 16:
        raise RuntimeError(f"Expected 16 teams, found {len(team_keys)}")

    total_rounds = int(rounds_total)
    qo_rounds = int(QO_ROUNDS) if bool(qualifying_offers) else 0
    mode = str(order_mode or "straight").strip().lower()

    picks: dict[str, PickSlot] = {}
    pick_order: list[str] = []

    for rnd in range(1, total_rounds + 1):
        round_pick_ids: list[str] = []

        for slot in range(1, 17):
            owner_team_key = team_keys[slot - 1]
            original_team_key = owner_team_key

            if rnd <= qo_rounds:
                pick_id = f"QO{rnd}-{slot:02d}"
                round_type = RoundType.QO
            else:
                pick_id = f"R{rnd:02d}-{slot:02d}"
                round_type = RoundType.STANDARD

            ps = PickSlot(
                pick_id=pick_id,
                round_type=round_type,
                round_number=rnd,
                slot=slot,
                original_team_key=original_team_key,
                owner_team_key=owner_team_key,
                selected_player_key=None,
                selected_ts_iso=None,
            )
            picks[pick_id] = ps
            round_pick_ids.append(pick_id)

        if round_type == RoundType.STANDARD and mode == "snake":
            standard_round_index = int(rnd) - int(first_standard_round)
            if standard_round_index % 2 == 1:
                round_pick_ids = list(reversed(round_pick_ids))

        pick_order.extend(round_pick_ids)

    return picks, pick_order