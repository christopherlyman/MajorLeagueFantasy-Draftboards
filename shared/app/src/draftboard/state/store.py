from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import streamlit as st

from draftboard.domain.models import Team, Player, PickSlot, PickLogEntry


@dataclass
class DraftClock:
    current_pick_id: str
    auto_advance: bool = True

    # Clock control
    is_running: bool = False
    # ISO timestamps in UTC (stored as naive ISO strings)
    pick_started_ts_iso: str | None = None
    pick_paused_ts_iso: str | None = None

    # Accumulated elapsed seconds captured when pausing (so pause truly freezes time)
    elapsed_paused_seconds: int = 0

    # Rules/config
    seconds_per_pick: int = 24 * 60 * 60  # default 24h
    weekends_count: bool = False          # default: weekends DO NOT count
    timezone: str = "America/New_York"


@dataclass(slots=True)
class DraftState:
    schema_version: str
    rules_qo_allows_free_agents: bool

    commissioner_mode: bool
    active_team_key: str
    view_mode: str  # "SLOT" | "MANAGER"

    clock: DraftClock

    teams: Dict[str, Team]
    players: Dict[str, Player]
    picks: Dict[str, PickSlot]
    pick_order: List[str]
    pick_log: List[PickLogEntry]

    pt_player_team_map: dict[str, str] = field(default_factory=dict)

    # ✅ Slot-based draft order: index 0 = Pick #1, index 15 = Pick #16
    draft_order_team_keys_by_slot: List[str] = field(default_factory=list)



SESSION_KEY = "draftboard_state_v1"


def has_state() -> bool:
    return SESSION_KEY in st.session_state


def get_state() -> DraftState:
    if SESSION_KEY not in st.session_state:
        raise RuntimeError("DraftState not initialized. Call init_state(...) once during app startup.")
    return st.session_state[SESSION_KEY]


def init_state(state: DraftState) -> None:
    """
    Initialize DraftState only if it doesn't already exist.
    This prevents Streamlit reruns/refreshes from wiping picks.
    """
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = state


def set_commissioner_mode(is_on: bool) -> None:
    s = get_state()
    s.commissioner_mode = is_on


def set_active_team(team_key: str) -> None:
    s = get_state()
    s.active_team_key = team_key


def set_current_pick(pick_id: str) -> None:
    s = get_state()
    s.clock.current_pick_id = pick_id
