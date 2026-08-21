from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from draftboard.data.db import (
    get_live_draft_board,
)

from draftboard.data.season_team_slots import (
    get_season_team_slots,
)


def _rank_value(
    player: dict[str, Any],
) -> tuple[
    bool,
    float,
    str,
]:
    rank = player.get(
        "rank_value"
    )

    try:
        numeric_rank = (
            float(rank)
            if rank is not None
            else None
        )
    except Exception:
        numeric_rank = None

    return (
        numeric_rank is None,
        (
            numeric_rank
            if numeric_rank is not None
            else 999999.0
        ),
        str(
            player.get(
                "full_name"
            )
            or ""
        ).casefold(),
    )


def _preview_rows(
    players: list[dict],
) -> list[dict[str, Any]]:
    """
    Representative PREP-only rows.

    These rows are generated in memory solely so the Commissioner
    can approve the Pick Tracker layout before the draft exists.
    Nothing is persisted.
    """
    slots = (
        get_season_team_slots()
    )

    ranked_players = [
        player
        for player in players
        if player.get(
            "yahoo_player_key"
        )
    ]

    ranked_players.sort(
        key=_rank_value
    )

    sample_players = (
        ranked_players[:8]
    )

    rows: list[
        dict[str, Any]
    ] = []

    for index, player in enumerate(
        sample_players,
        start=1,
    ):
        slot_index = (
            index - 1
        ) % max(
            len(slots),
            1,
        )

        slot = (
            slots[slot_index]
            if slots
            else {}
        )

        team_name = str(
            slot.get(
                "current_team_name"
            )
            or slot.get(
                "replacement_team_name"
            )
            or slot.get(
                "prior_team_name"
            )
            or "Preview Team"
        )

        owner_name = str(
            slot.get(
                "current_owner_name"
            )
            or slot.get(
                "replacement_manager_name"
            )
            or slot.get(
                "prior_manager_name"
            )
            or ""
        )

        rows.append(
            {
                "Owner": owner_name,
                "Team Name": team_name,
                "Round": 1,
                "Pick #": index,
                "Overall #": index,
                "Player Name": str(
                    player.get(
                        "full_name"
                    )
                    or ""
                ),
                "Pos": str(
                    player.get(
                        "primary_position"
                    )
                    or ""
                ),
                "NHL Team": str(
                    player.get(
                        "nhl_team_abbr"
                    )
                    or ""
                ),
            }
        )

    return rows


def render_pick_tracker(
    *,
    players: list[dict],
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
        preview_rows = (
            _preview_rows(
                players
            )
        )

        st.caption(
            "PRE-DRAFT PREVIEW — Representative rows are shown "
            "only so the Pick Tracker layout can be reviewed. "
            "These are not draft selections."
        )

        if not preview_rows:
            st.info(
                "No preview players are available."
            )
            return

        st.dataframe(
            pd.DataFrame(
                preview_rows
            ),
            hide_index=True,
            use_container_width=True,
        )

        return

    completed.sort(
        key=lambda row: (
            row.get(
                "selected_at_utc"
            ),
            int(
                row.get(
                    "round_number"
                )
                or 0
            ),
            int(
                row.get(
                    "slot_number"
                )
                or 0
            ),
        )
    )

    rows: list[
        dict[str, Any]
    ] = []

    for overall_number, row in enumerate(
        completed,
        start=1,
    ):
        rows.append(
            {
                "Owner": str(
                    row.get(
                        "current_owner_name"
                    )
                    or ""
                ),
                "Team Name": str(
                    row.get(
                        "current_owner_team_name"
                    )
                    or ""
                ),
                "Round": int(
                    row.get(
                        "round_number"
                    )
                    or 0
                ),
                "Pick #": int(
                    row.get(
                        "slot_number"
                    )
                    or 0
                ),
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
                    row.get(
                        "selected_nhl_team_abbr"
                    )
                    or ""
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            rows
        ),
        hide_index=True,
        use_container_width=True,
    )
