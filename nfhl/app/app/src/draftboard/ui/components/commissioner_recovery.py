from __future__ import annotations

from typing import Any

import streamlit as st

from draftboard.data.db import (
    delete_nfhl_draft_pick,
    get_live_draft_board,
    get_live_draft_state,
    set_nfhl_current_pick_remaining,
)

def _completed_label(
    row: dict[str, Any],
) -> str:
    return (
        f"{row.get('pick_id') or ''} — "
        f"{row.get('current_owner_team_name') or ''} — "
        f"{row.get('selected_player_name') or ''}"
        + (
            f" ({row.get('selected_primary_position')})"
            if row.get(
                "selected_primary_position"
            )
            else ""
        )
    )




def render_commissioner_recovery(
    *,
    gateway_context: dict[str, object],
) -> None:
    role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).lower()

    if role != "commissioner":
        return


    try:
        live_state = (
            get_live_draft_state()
        )

        board_rows = (
            get_live_draft_board()
        )

    except Exception as exc:
        st.error(
            "Unable to load draft recovery state: "
            f"{exc}"
        )
        return

    status = str(
        live_state.get(
            "status"
        )
        or ""
    ).upper()

    completed_rows = [
        row
        for row in board_rows
        if (
            row.get(
                "yahoo_player_key"
            )
            and row.get(
                "selected_at_utc"
            )
        )
    ]

    completed_rows.sort(
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
        ),
        reverse=True,
    )

    with st.expander(
        "Recovery",
        expanded=bool(
            st.session_state.pop(
                "nfhl_force_open_recovery",
                False,
            )
        ),
    ):

        if not completed_rows:
            st.info(
                "There are no completed selections to correct."
            )

            selected_pick_id = (
                None
            )
            return

        else:
            pick_lookup = {
                str(
                    row[
                        "pick_id"
                    ]
                ): row
                for row
                in completed_rows
            }

            pick_ids = list(
                pick_lookup.keys()
            )

            selected_pick_id = (
                st.selectbox(
                    "Completed pick",
                    options=pick_ids,
                    format_func=(
                        lambda pick_id: (
                            _completed_label(
                                pick_lookup[
                                    pick_id
                                ]
                            )
                        )
                    ),
                    index=0,
                    key=(
                        "nfhl_recovery_pick"
                    ),
                )
            )

        recovery_mode = (
            st.radio(
                "Recovery action",
                options=[
                    (
                        "Delete pick + rewind "
                        "clock to this pick"
                    ),
                    (
                        "Delete pick "
                        "(do not change clock)"
                    ),
                ],
                index=0,
                key=(
                    "nfhl_recovery_mode"
                ),
            )
        )

        rewind_clock = (
            recovery_mode.startswith(
                "Delete pick + rewind"
            )
        )

        if rewind_clock:
            st.info(
                "The selection will be removed and this pick "
                "will become the current pick again. The clock "
                "will be paused with a fresh 24-hour window."
            )

        else:
            st.info(
                "The selection will be removed but the live "
                "clock will stay exactly where it is. The "
                "deleted pick will reopen as a makeup pick for "
                "that manager."
            )

        confirm = st.checkbox(
            "I understand this changes the official "
            "NFHL draft record.",
            value=False,
            key=(
                "nfhl_recovery_confirm"
            ),
        )

        execute_disabled = (
            status != "ACTIVE"
            or not selected_pick_id
            or not confirm
        )

        if st.button(
            "Apply Fix",
            type="primary",
            use_container_width=True,
            disabled=execute_disabled,
            key=(
                "nfhl_recovery_apply"
            ),
        ):

            try:
                result = (
                    delete_nfhl_draft_pick(
                        pick_id=str(
                            selected_pick_id
                        ),
                        rewind_clock=(
                            rewind_clock
                        ),
                        actor=(
                            "commissioner_link"
                        ),
                    )
                )

                if rewind_clock:
                    # Reuse the canonical current-pick clock
                    # adjustment path so reminder reset semantics
                    # stay centralized.
                    set_nfhl_current_pick_remaining(
                        remaining_seconds=86400,
                        actor=(
                            "commissioner_link"
                        ),
                    )

            except Exception as exc:
                st.error(
                    "Draft correction failed: "
                    f"{exc}"
                )
                return

            if rewind_clock:
                st.success(
                    "Pick deleted. Draft rewound to "
                    f"{selected_pick_id}; clock paused "
                    "with 24:00 remaining."
                )

            else:
                st.success(
                    "Pick deleted without changing the "
                    "live clock. The pick is available "
                    "as a makeup selection."
                )

            st.cache_data.clear()
            st.session_state["nfhl_force_open_recovery"] = True
            st.rerun()
