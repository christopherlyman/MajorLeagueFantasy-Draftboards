from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from draftboard.data.db import (
    get_live_draft_board,
)







def render_pick_tracker(
    *,
    players: list[dict],
    teams: list[dict],
) -> None:
    st.subheader(
        "Pick Tracker"
    )

    try:
        board_rows = (
            get_live_draft_board()
        )

    except Exception as exc:
        st.error(
            "Could not load Pick Tracker: "
            f"{exc}"
        )
        return

    completed = [
        row
        for row in board_rows
        if row.get(
            "selected_at_utc"
        )
        and row.get(
            "yahoo_player_key"
        )
    ]

    if not completed:
        st.info("No picks yet.")
        return

    total_slots = max(
        (
            int(
                row.get(
                    "slot_number"
                )
                or 0
            )
            for row
            in board_rows
        ),
        default=0,
    )

    if total_slots <= 0:
        st.error(
            "Could not determine Draft Board team count."
        )
        return

    def _draft_position(
        row: dict[str, Any],
    ) -> tuple[int, int, int]:
        round_number = int(
            row.get(
                "round_number"
            )
            or 0
        )

        slot_number = int(
            row.get(
                "slot_number"
            )
            or 0
        )

        pick_number = (
            slot_number
            if round_number % 2 == 1
            else (
                total_slots
                + 1
                - slot_number
            )
        )

        overall_number = (
            (
                round_number
                - 1
            )
            * total_slots
            + pick_number
        )

        return (
            round_number,
            pick_number,
            overall_number,
        )

    # Display completed selections in authoritative draft order,
    # independent of when a late or makeup selection was recorded.
    completed.sort(
        key=lambda row: (
            _draft_position(
                row
            )[2]
        )
    )

    player_lookup = {
        str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ).strip(): player
        for player in players
        if str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ).strip()
    }

    team_lookup = {
        str(
            team.get(
                "team_key"
            )
            or ""
        ).strip(): team
        for team in teams
        if str(
            team.get(
                "team_key"
            )
            or ""
        ).strip()
    }

    rows: list[
        dict[str, Any]
    ] = []

    for row in completed:
        (
            round_number,
            pick_number,
            overall_number,
        ) = _draft_position(
            row
        )
        owner_team = team_lookup.get(
            str(
                row.get(
                    "current_owner_team_key"
                )
                or ""
            ).strip(),
            {},
        )

        selected_player = player_lookup.get(
            str(
                row.get(
                    "yahoo_player_key"
                )
                or ""
            ).strip(),
            {},
        )

        rows.append(
            {
                "Owner": str(
                    owner_team.get(
                        "owner_name"
                    )
                    or ""
                ),
                "Team Name": str(
                    row.get(
                        "current_owner_team_name"
                    )
                    or ""
                ),
                "Round": round_number,
                "Pick #": pick_number,
                "Overall #": overall_number,
                "Player Name": str(
                    row.get(
                        "selected_player_name"
                    )
                    or ""
                ),
                "Pos": str(
                    row.get(
                        "selected_primary_position"
                    )
                    or ""
                ),
                "NHL Team": str(
                    selected_player.get(
                        "nhl_team_abbr"
                    )
                    or ""
                ),
            }
        )

    # Let the page own vertical scrolling rather than putting
    # a second scrollbar inside the Pick Tracker table.
    #
    # Streamlit dataframe rows are approximately 35 px tall.
    # Add room for the header/borders and size to the actual
    # completed-pick count.
    tracker_height = max(
        120,
        38 + (
            len(rows)
            * 35
        ),
    )

    st.dataframe(
        pd.DataFrame(
            rows
        ),
        hide_index=True,
        use_container_width=True,
        height=tracker_height,
    )
