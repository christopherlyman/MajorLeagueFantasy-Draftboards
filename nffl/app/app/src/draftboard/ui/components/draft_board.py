from __future__ import annotations

from typing import Dict, List

import pandas as pd

from draftboard.domain.models import PickSlot, Player, Team
from draftboard.state.league_profile import get_active_qo_rounds


def _empty_cell_label(pick: PickSlot) -> str:
    if pick.round_number <= get_active_qo_rounds():
        return f"QO{pick.round_number}.{pick.slot}"
    return f"{pick.round_number}.{pick.slot}"


def _cell_text(
    pick: PickSlot,
    teams: Dict[str, Team],
    players: Dict[str, Player],
) -> str:
    if not pick.selected_player_key:
        return _empty_cell_label(pick)

    team = teams.get(pick.owner_team_key)
    team_name = team.name if team else pick.owner_team_key

    player = players[pick.selected_player_key]
    return f"{team_name} | {player.name} | {player.primary_position.value}"


def build_board_dataframe(
    picks: Dict[str, PickSlot],
    teams: Dict[str, Team],
    players: Dict[str, Player],
) -> pd.DataFrame:
    """
    DataFrame for the board:
      - Columns = full fantasy team names (for wrapping)
      - No index labels (removes the left "round" column in Streamlit tables)
    """
    team_keys: List[str] = list(teams.keys())
    columns = [teams[k].name for k in team_keys]

    ordered_picks = sorted(picks.values(), key=lambda p: (p.round_number, p.slot))

    # group by round_number
    by_round: Dict[int, List[PickSlot]] = {}
    for p in ordered_picks:
        by_round.setdefault(p.round_number, []).append(p)

    rows = []
    for round_number in sorted(by_round.keys()):
        round_picks = sorted(by_round[round_number], key=lambda p: p.slot)
        rows.append([_cell_text(p, teams, players) for p in round_picks])

    df = pd.DataFrame(rows, columns=columns)
    return df
