from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

from parse_players import (
    parse_players_payload,
)
from parse_teams import (
    parse_teams_payload,
)


LEAGUE_KEY = "477.l.10961"
SEASON_YEAR = 2026

DISCOVERY = Path(
    os.environ.get(
        "NFHL_YAHOO_DISCOVERY_DIR",
        "/input",
    )
)

TEAMS_PATH = (
    DISCOVERY
    / "league_477_l_10961_teams_sample.json"
)

PLAYERS_PATH = (
    DISCOVERY
    / "league_477_l_10961_players_sample.json"
)


teams_payload = json.loads(
    TEAMS_PATH.read_text(
        encoding="utf-8"
    )
)

players_payload = json.loads(
    PLAYERS_PATH.read_text(
        encoding="utf-8"
    )
)


teams = parse_teams_payload(
    teams_payload,
    league_key=LEAGUE_KEY,
    season_year=SEASON_YEAR,
)

players = parse_players_payload(
    players_payload,
    league_key=LEAGUE_KEY,
    season_year=SEASON_YEAR,
)


# ================================================================
# TEAM FIXTURE PROOF
# ================================================================

assert len(teams) == 4

team_map = {
    row["team_key"]: row
    for row in teams
}

assert (
    team_map[
        "477.l.10961.t.1"
    ]["team_name"]
    == "Drop The Gloves"
)

assert (
    team_map[
        "477.l.10961.t.2"
    ]["team_name"]
    == "Debbie BarDowners"
)

assert (
    team_map[
        "477.l.10961.t.3"
    ]["team_name"]
    == "Mantha Rays"
)

assert (
    team_map[
        "477.l.10961.t.4"
    ]["team_name"]
    == "Big Cat Habitat"
)

assert {
    row["team_id"]
    for row in teams
} == {
    "1",
    "2",
    "3",
    "4",
}

print(
    "TEAM_FIXTURE_PARSE=PASS"
)


# ================================================================
# PLAYER FIXTURE PROOF
# ================================================================

assert len(players) == 25

player_map = {
    row["yahoo_player_key"]: row
    for row in players
}

burns = player_map[
    "477.p.3358"
]

assert (
    burns["full_name"]
    == "Brent Burns"
)

assert (
    burns["nhl_team_abbr"]
    == "COL"
)

assert (
    burns["eligible_positions"]
    == ["D", "Util"]
)

assert (
    burns["primary_position"]
    == "D"
)

assert (
    burns["position_type"]
    == "P"
)

assert (
    burns["player_status"]
    is None
)

assert (
    burns["percent_owned"]
    == Decimal("24")
)

assert (
    burns["rank_value"]
    == Decimal("367")
)

assert (
    burns["percent_drafted"]
    == Decimal("0.21")
)

assert (
    burns[
        "preseason_percent_drafted"
    ]
    == Decimal("0.2")
)

print(
    "BRENT_BURNS_STRUCTURAL_PARSE=PASS"
)


# ================================================================
# NULL / STATUS SEMANTICS
# ================================================================

perry = player_map[
    "477.p.3365"
]

assert (
    perry["percent_drafted"]
    is None
)

assert (
    perry[
        "preseason_percent_drafted"
    ]
    is None
)


varlamov = player_map[
    "477.p.4003"
]

assert (
    varlamov["player_status"]
    == "IR"
)

assert (
    varlamov["position_type"]
    == "G"
)

assert (
    varlamov["primary_position"]
    == "G"
)

assert "IR+" in (
    varlamov[
        "eligible_positions"
    ]
)


marchand = player_map[
    "477.p.4351"
]

assert (
    marchand["player_status"]
    == "IR-LT"
)


reimer = player_map[
    "477.p.4369"
]

assert (
    reimer["player_status"]
    == "NA"
)


bogosian = player_map[
    "477.p.4473"
]

assert (
    bogosian["player_status"]
    == "O"
)


for row in players:
    assert not isinstance(
        row["player_status"],
        bool,
    )

print(
    "PLAYER_STATUS_SEMANTICS=PASS"
)


# ================================================================
# HOCKEY POSITION MODEL
# ================================================================

valid_native = {
    "C",
    "LW",
    "RW",
    "D",
    "G",
}

assert all(
    row[
        "primary_position"
    ] in valid_native
    for row in players
)

assert all(
    row[
        "position_type"
    ] in {"P", "G"}
    for row in players
)

eligible_tokens = sorted(
    {
        position
        for row in players
        for position
        in row[
            "eligible_positions"
        ]
    }
)

assert "Util" in eligible_tokens
assert "F" in eligible_tokens
assert "IR+" in eligible_tokens
assert "NA" in eligible_tokens

assert "Util" not in {
    row[
        "primary_position"
    ]
    for row in players
}

assert "F" not in {
    row[
        "primary_position"
    ]
    for row in players
}

print(
    "HOCKEY_POSITION_MODEL=PASS"
)


# ================================================================
# SUMMARY
# ================================================================

status_counts = Counter(
    row["player_status"]
    or "<NULL>"
    for row in players
)

primary_counts = Counter(
    row["primary_position"]
    for row in players
)

print(
    "TEAM_ROWS="
    + str(
        len(teams)
    )
)

print(
    "PLAYER_ROWS="
    + str(
        len(players)
    )
)

print(
    "STATUS_COUNTS="
    + json.dumps(
        dict(
            sorted(
                status_counts.items()
            )
        ),
        sort_keys=True,
    )
)

print(
    "PRIMARY_POSITION_COUNTS="
    + json.dumps(
        dict(
            sorted(
                primary_counts.items()
            )
        ),
        sort_keys=True,
    )
)

print(
    "ELIGIBLE_POSITION_TOKENS="
    + json.dumps(
        eligible_tokens
    )
)

print(
    "NFHL_OFFLINE_YAHOO_PARSER_TESTS=PASS"
)
