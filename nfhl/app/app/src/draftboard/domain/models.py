from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlayerPosition(str, Enum):
    C = "C"
    LW = "LW"
    RW = "RW"
    D = "D"
    G = "G"


class RosterSlot(str, Enum):
    C = "C"
    LW = "LW"
    RW = "RW"
    F = "F"
    D = "D"
    UTIL = "Util"
    G = "G"
    BN = "BN"
    IR_PLUS = "IR+"
    NA = "NA"


@dataclass(frozen=True)
class Player:
    yahoo_player_key: str
    name: str
    nhl_team: str | None
    eligible_positions: tuple[PlayerPosition, ...]
    primary_position: PlayerPosition | None
    position_type: str | None
    status: str | None
    rank_value: int | None = None
    percent_drafted: float | None = None


@dataclass(frozen=True)
class Team:
    yahoo_team_key: str
    team_name: str
    manager_name: str | None = None
