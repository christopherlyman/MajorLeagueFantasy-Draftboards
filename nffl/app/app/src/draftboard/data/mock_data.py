from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

from draftboard.domain.models import Player, Position, RoundType, Team, PickSlot


@dataclass(frozen=True, slots=True)
class LeagueConfig:
    teams_count: int = 16
    qo_rounds: int = 5
    rounds_total: int = 25


LEAGUE_TEAM_NAMES = [
    "Big Stick Energy",
    "Werth Wind & Fire",
    "The Tribe",
    "The Gunn Show",
    "Truth",
    "Three True Outcomes",
    "Terrible Rookies",
    "ScuttleButt Sluggers",
    "Nankai Hawks",
    "Rye Bread & Mustard2",
    "Judge's Chambers",
    "Prestige Worldwide",
    "Party on Garth",
    "Air Yordan",
    "The Bionic Elbow Factory",
    "Juan Solo",
]


def _team_color(i: int) -> str:
    colors = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
        "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
        "#bcbd22", "#17becf", "#003f5c", "#58508d",
        "#bc5090", "#ff6361", "#ffa600", "#2f4b7c",
    ]
    return colors[(i - 1) % len(colors)]


def _abbr_from_name(name: str, fallback_index: int) -> str:
    """
    v1: simple deterministic abbreviation. We'll replace with real owner keys later.
    """
    parts = [p for p in name.replace("'", "").split() if p]
    if len(parts) == 1:
        return parts[0][:3].upper()
    letters = "".join(p[0].upper() for p in parts[:3])
    if len(letters) >= 2:
        return letters[:3]
    return f"T{fallback_index}"


def make_mock_teams(cfg: LeagueConfig) -> Dict[str, Team]:
    teams: Dict[str, Team] = {}
    for i in range(1, cfg.teams_count + 1):
        key = f"TEAM_{i:02d}"
        name = LEAGUE_TEAM_NAMES[i - 1] if i - 1 < len(LEAGUE_TEAM_NAMES) else f"Team {i}"
        abbr = _abbr_from_name(name, i)
        teams[key] = Team(
            team_key=key,
            name=name,
            abbr=abbr,
            color=_team_color(i),
        )
    return teams


_MLB_TEAMS = [
    "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE",
    "COL", "DET", "HOU", "KCR", "LAA", "LAD", "MIA", "MIL",
    "MIN", "NYM", "NYY", "OAK", "PHI", "PIT", "SDP", "SFG",
    "SEA", "STL", "TBR", "TEX", "TOR", "WSN",
]

_FIRST_NAMES = [
    "Aaron", "Alex", "Andrew", "Ben", "Brandon", "Bryce", "Carlos", "Chris",
    "Daniel", "David", "Eddie", "Ethan", "Fernando", "George", "Henry", "Isaac",
    "Jack", "Jacob", "James", "Jason", "Javier", "John", "Jose", "Juan",
    "Kevin", "Kyle", "Logan", "Lucas", "Mark", "Matt", "Michael", "Miguel",
    "Nathan", "Nick", "Noah", "Owen", "Paul", "Peter", "Rafael", "Ryan",
    "Sam", "Steven", "Thomas", "Trevor", "Victor", "Will", "Zach",
]

_LAST_NAMES = [
    "Adams", "Alvarez", "Anderson", "Baker", "Barnes", "Bennett", "Brown", "Carter",
    "Castillo", "Cole", "Collins", "Davis", "Diaz", "Edwards", "Flores", "Garcia",
    "Gonzalez", "Green", "Harris", "Hernandez", "Hill", "Jackson", "Johnson", "Jones",
    "King", "Lee", "Lewis", "Lopez", "Martin", "Martinez", "Miller", "Moore",
    "Morgan", "Nelson", "Parker", "Perez", "Ramirez", "Reed", "Rivera", "Roberts",
    "Rodriguez", "Rogers", "Ross", "Sanders", "Smith", "Taylor", "Thomas", "Turner",
    "Walker", "White", "Williams", "Wilson", "Wright", "Young",
]


def make_mock_players(n: int = 250, seed: int = 42) -> Dict[str, Player]:
    random.seed(seed)

    positions = [
        Position.C, Position._1B, Position._2B, Position._3B,
        Position.SS, Position.OF, Position.UTIL, Position.SP, Position.RP
    ]

    players: Dict[str, Player] = {}

    # Ensure the longest name is present and easy to find
    key_long = "P_0001"
    players[key_long] = Player(
        player_key=key_long,
        name="Christian Encarnacion-Strand",
        mlb_team=random.choice(_MLB_TEAMS),
        positions=[Position._1B, Position._3B],
        is_qo_eligible=True,
        qo_group=1,
        is_poach_eligible=True,
    )

    for i in range(2, n + 1):
        key = f"P_{i:04d}"

        first = random.choice(_FIRST_NAMES)
        last = random.choice(_LAST_NAMES)
        name = f"{first} {last}"

        pos1 = random.choice(positions)
        pos_list = [pos1]
        if random.random() < 0.2:
            pos2 = random.choice([p for p in positions if p != pos1])
            pos_list.append(pos2)

        is_qo = random.random() < 0.15
        qo_group = random.randint(1, 5) if is_qo else None
        is_poach = is_qo and (random.random() < 0.4)

        mlb_team = random.choice(_MLB_TEAMS)

        players[key] = Player(
            player_key=key,
            name=name,
            mlb_team=mlb_team,
            positions=pos_list,
            is_qo_eligible=is_qo,
            qo_group=qo_group,
            is_poach_eligible=is_poach,
        )

    return players


def _round_label(round_number: int, qo_rounds: int) -> Tuple[str, RoundType]:
    if round_number <= qo_rounds:
        return f"QO{round_number}", RoundType.QO
    return f"R{round_number:02d}", RoundType.STANDARD


def make_mock_picks(teams: dict, cfg: "LeagueConfig"):
    """
    Canonical pick grid builder.

    IMPORTANT:
    - owner_team_key MUST be the canonical team_key from `teams` (Yahoo keys),
      never legacy TEAM_XX.
    - pick_id format MUST stay compatible with existing code:
        * QO rounds (1..5):  "QO{lvl}-{slot:02d}"
        * Standard rounds (6..25): "R{rnd:02d}-{slot:02d}"
    - Deterministic column order: ORDER BY team_key (string sort).
    """
    from draftboard.domain.models import PickSlot, RoundType

    team_keys = sorted([str(k) for k in (teams or {}).keys()])
    if len(team_keys) != 16:
        # deterministic hard stop (no guessing)
        raise RuntimeError(f"Expected 16 teams, found {len(team_keys)}")

    picks: dict[str, PickSlot] = {}
    pick_order: list[str] = []

    ROUNDS_TOTAL = 25
    QO_ROUNDS = 5

    for rnd in range(1, ROUNDS_TOTAL + 1):
        for slot in range(1, 16 + 1):
            owner_team_key = team_keys[slot - 1]

            if rnd <= QO_ROUNDS:
                pick_id = f"QO{rnd}-{slot:02d}"
                round_type = RoundType("QO") if "QO" in [x.value for x in RoundType] else RoundType.QO
            else:
                pick_id = f"R{rnd:02d}-{slot:02d}"
                round_type = RoundType("STANDARD") if "STANDARD" in [x.value for x in RoundType] else RoundType.STANDARD

            ps = PickSlot(
                pick_id=pick_id,
                round_type=round_type,
                round_number=rnd,
                slot=slot,
                original_team_key=owner_team_key,
                owner_team_key=owner_team_key,
                selected_player_key=None,
                selected_ts_iso=None,
            )
            picks[pick_id] = ps
            pick_order.append(pick_id)

    return picks, pick_order
