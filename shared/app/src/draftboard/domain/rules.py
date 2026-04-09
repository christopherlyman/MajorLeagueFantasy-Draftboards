from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from draftboard.domain.models import PickSlot, Player, RoundType

QO_ROUNDS = 5
ROUNDS_TOTAL = 25
DEFAULT_QO_ALLOWS_FREE_AGENTS = True


@dataclass(frozen=True, slots=True)
class RulesConfig:
    qo_allows_free_agents: bool = True


class DraftRules:
    """
    Minimal v1 rules:
    - A player can only be drafted once.
    - In QO rounds, selection can be:
        * own QO (treated as "QO")
        * poach-eligible QO from another team (treated as "POACH")
        * any free agent if qo_allows_free_agents (treated as "FA")
      For v1, we infer kind from player flags + ownership.
    - STANDARD rounds: kind is always "FA" (placeholder).
    """

    def __init__(self, config: RulesConfig) -> None:
        self.config = config

    def is_player_already_drafted(self, picks: Dict[str, PickSlot], player_key: str) -> bool:
        for p in picks.values():
            if p.selected_player_key == player_key:
                return True
        return False

    def classify_pick_kind(
        self,
        pick: PickSlot,
        player: Player,
        picking_team_key: str,
    ) -> str:
        if pick.round_type != RoundType.QO:
            return "FA"

        # QO round logic (stubby but structured)
        if player.is_qo_eligible:
            # If picking team matches current owner, treat as "QO" (own QO use-case)
            if picking_team_key == pick.owner_team_key:
                return "QO"
            # Otherwise, if poach eligible, treat as poach
            if player.is_poach_eligible:
                return "POACH"

        # Not a QO/poach pick -> free agent path
        return "FA"

    def validate_pick(
        self,
        picks: Dict[str, PickSlot],
        pick_id: str,
        players: Dict[str, Player],
        player_key: str,
        picking_team_key: str,
    ) -> Tuple[bool, Optional[str]]:
        if pick_id not in picks:
            return False, f"Unknown pick_id: {pick_id}"

        pick = picks[pick_id]

        if pick.selected_player_key is not None:
            return False, f"Pick {pick_id} is already used."

        if player_key not in players:
            return False, f"Unknown player_key: {player_key}"

        if self.is_player_already_drafted(picks, player_key):
            return False, "Player already drafted."

        player = players[player_key]

        if pick.round_type == RoundType.QO:
            # If they choose a free agent and it's not allowed, block
            is_qo_or_poach = player.is_qo_eligible or player.is_poach_eligible
            if (not is_qo_or_poach) and (not self.config.qo_allows_free_agents):
                return False, "Free agents are not allowed during QO rounds."

            # If trying to poach but not eligible, block with clear reason
            if player.is_qo_eligible and picking_team_key != pick.owner_team_key and not player.is_poach_eligible:
                return False, "Poach not allowed for this player (not poach-eligible)."

        return True, None
