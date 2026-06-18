from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List


class RoundType(str, Enum):
    QO = "QO"
    STANDARD = "STANDARD"


class Position(str, Enum):
    # Baseball
    C = "C"
    _1B = "1B"
    _2B = "2B"
    _3B = "3B"
    SS = "SS"
    OF = "OF"
    UTIL = "UTIL"
    P = "P"
    SP = "SP"
    RP = "RP"

    # Football
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DEF = "DEF"


@dataclass(slots=True)
class Team:
    team_key: str
    name: str
    abbr: str
    color: Optional[str] = None


@dataclass(slots=True)
@dataclass
class Player:
    player_key: str
    name: str
    mlb_team: str                 # MLB team (e.g., "LAD", "NYY")
    positions: List[Position]

    # QO flags
    is_qo_eligible: bool = False
    qo_group: Optional[int] = None   # 1..5 if applicable
    is_poach_eligible: bool = False

    # Rank / ownership
    rank_value: Optional[float] = None
    percent_owned: Optional[float] = None

    # Batters (Yahoo 2025)
    h_ab: Optional[str] = None
    r: Optional[int] = None
    hr: Optional[int] = None
    rbi: Optional[int] = None
    sb: Optional[int] = None
    bb: Optional[int] = None
    k_hit: Optional[int] = None
    avg: Optional[float] = None

    # Pitchers (Yahoo 2025)
    ip: Optional[float] = None
    w: Optional[int] = None
    k_pit: Optional[int] = None
    tb: Optional[int] = None
    era: Optional[float] = None
    whip: Optional[float] = None
    qs: Optional[int] = None
    sv_h: Optional[int] = None
    @property
    def primary_position(self) -> Position:
        return self.positions[0]


@dataclass(slots=True)
class PickSlot:
    pick_id: str                  # "QO1-01", "R06-12"
    round_type: RoundType         # QO | STANDARD
    round_number: int             # 1..25
    slot: int                     # 1..16

    original_team_key: str
    owner_team_key: str

    selected_player_key: Optional[str] = None
    selected_ts_iso: Optional[str] = None


@dataclass(slots=True)
class PickLogEntry:
    event_id: str
    pick_id: str

    owner_team_key: str           # owner at time of pick
    player_key: str
    player_name: str
    primary_position: Position

    pick_kind: str                # "QO" | "POACH" | "FA"
    ts_iso: str
