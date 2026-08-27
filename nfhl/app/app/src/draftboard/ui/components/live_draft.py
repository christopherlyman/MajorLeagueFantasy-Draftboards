from __future__ import annotations

from html import escape


def _nfhl_clock_urgency_color(
    remaining_label: str,
) -> str:
    """
    Picker urgency colors:

      12:00 through 24:00 -> green
      06:00 through 11:59 -> yellow
      00:00 through 05:59 -> red

    Boundary contract:
      exactly 12:00 is GREEN
      exactly 06:00 is YELLOW
    """
    try:
        parts = str(
            remaining_label
            or ""
        ).strip().split(":")

        if len(parts) != 2:
            raise ValueError(
                "Expected HH:MM."
            )

        hours = int(
            parts[0]
        )

        minutes = int(
            parts[1]
        )

        total_minutes = (
            hours * 60
            + minutes
        )

    except Exception:
        # Fail safe visually: if the clock cannot be parsed,
        # use the high-visibility warning state.
        return "#EF4444"

    if total_minutes >= 12 * 60:
        return "#22C55E"

    if total_minutes >= 6 * 60:
        return "#FACC15"

    return "#EF4444"


from typing import Any

import streamlit as st

from draftboard.data.db import (
    get_draft_clock_snapshot,
    get_live_draft_board,
    get_live_draft_state,
    get_oldest_unresolved_expired_pick_id,
    process_live_draft_clock,
    submit_manual_draft_pick,
)

from draftboard.ui.components.player_search import (
    filter_player_keys_by_query,
)

from draftboard.data.season_team_slots import (
    get_season_team_slots,
)

from draftboard.ui.components.postgres_board_html import (
    render_postgres_board_html,
)




def _fmt_remaining(
    snapshot: dict[str, Any],
) -> str:
    value = None

    for key in (
        "seconds_remaining",
        "remaining_seconds",
        "clock_seconds_remaining",
    ):
        if snapshot.get(key) is not None:
            value = snapshot.get(key)
            break

    if value is None:
        return "—"

    try:
        total_seconds = max(
            0,
            int(
                float(value)
            ),
        )
    except Exception:
        return "—"

    hours, remainder = divmod(
        total_seconds,
        3600,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}"
    )


def _pick_target_for_principal(
    *,
    gateway_context: dict[str, object],
    board_lookup: dict[str, dict[str, Any]],
    current_pick_id: str,
) -> tuple[str | None, bool]:
    """
    Return:
      (pick_id, is_makeup_pick)

    Commissioner:
      current live pick only.

    Manager:
      current live pick if it belongs to their team;
      otherwise oldest unresolved expired pick.
    """
    role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).strip().lower()

    if role == "commissioner":
        if (
            current_pick_id
            and current_pick_id
            in board_lookup
        ):
            return (
                current_pick_id,
                False,
            )

        return (
            None,
            False,
        )

    if role != "manager":
        return (
            None,
            False,
        )

    team_key = str(
        gateway_context.get(
            "team_key"
        )
        or ""
    ).strip()

    if not team_key:
        return (
            None,
            False,
        )

    current_pick = (
        board_lookup.get(
            current_pick_id
        )
        if current_pick_id
        else None
    )

    if current_pick:
        current_owner = str(
            current_pick.get(
                "current_owner_team_key"
            )
            or ""
        ).strip()

        if current_owner == team_key:
            return (
                current_pick_id,
                False,
            )

    try:
        expired_pick_id = (
            get_oldest_unresolved_expired_pick_id(
                team_key
            )
        )
    except Exception:
        expired_pick_id = None

    if (
        expired_pick_id
        and expired_pick_id
        in board_lookup
    ):
        expired_row = board_lookup[
            expired_pick_id
        ]

        if not expired_row.get(
            "yahoo_player_key"
        ):
            return (
                expired_pick_id,
                True,
            )

    return (
        None,
        False,
    )


def _player_rank(
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
            if numeric_rank
            is not None
            else 999999.0
        ),
        str(
            player.get(
                "full_name"
            )
            or ""
        ).casefold(),
    )


def _player_display(
    player: dict[str, Any],
) -> str:
    name = str(
        player.get(
            "full_name"
        )
        or ""
    ).strip()

    nhl_team = str(
        player.get(
            "nhl_team_abbr"
        )
        or "-"
    ).strip()

    primary_position = str(
        player.get(
            "primary_position"
        )
        or "-"
    ).strip()

    rank = player.get(
        "rank_value"
    )

    try:
        rank_label = (
            f"#{int(float(rank))}"
            if rank is not None
            else "NR"
        )
    except Exception:
        rank_label = "NR"

    return (
        f"{rank_label} — "
        f"{name} — "
        f"{nhl_team} — "
        f"{primary_position}"
    )


def render_live_draft_experience(
    *,
    gateway_context: dict[str, object],
    players: list[dict],
    status_only: bool = False,
) -> None:
    """
    NFFL-style NFHL live Draft Board and manual picker.
    """
    try:
        live_state = (
            get_live_draft_state()
        )

        draft_status = str(
            live_state.get(
                "status"
            )
            or ""
        ).upper()

        if (
            draft_status == "ACTIVE"
            and not status_only
        ):
            process_live_draft_clock()

            live_state = (
                get_live_draft_state()
            )

            draft_status = str(
                live_state.get(
                    "status"
                )
                or ""
            ).upper()

        board_rows = (
            get_live_draft_board()
        )

    except Exception as exc:
        st.error(
            "Unable to load the live "
            f"NFHL draft: {exc}"
        )
        return

    # NFHL_PREP_VISUAL_VALIDATION_START
    #
    # PREP must expose the REAL live picker component for visual
    # review without creating production draft_pick rows.
    #
    # We inject one non-persistent synthetic current-pick row only
    # into this UI function. The real PostgreSQL draft remains
    # untouched and SUBMIT is disabled below.
    preview_mode = False

    if not board_rows:
        preview_mode = True

        try:
            preview_slots = get_season_team_slots()
        except Exception as exc:
            st.warning(
                "Picker preview could not load league slots: "
                f"{exc}"
            )
            return

        if not preview_slots:
            st.warning(
                "Picker preview requires league-slot data."
            )
            return

        preview_slot = preview_slots[0]

        preview_team_name = str(
            preview_slot.get("current_team_name")
            or preview_slot.get("replacement_team_name")
            or preview_slot.get("prior_team_name")
            or "Preview Team"
        ).strip()

        preview_team_key = str(
            preview_slot.get("current_team_key")
            or (
                "NFHL_PREVIEW_SLOT_"
                + str(
                    preview_slot.get("league_slot_number")
                    or 1
                )
            )
        ).strip()

        board_rows = [
            {
                "draft_key": "NFHL_PREVIEW_ONLY",
                "pick_id": "R01.1",
                "round_number": 1,
                "slot_number": 1,
                "round_label": "R01",
                "column_team_key": preview_team_key,
                "column_team_name": preview_team_name,
                "current_owner_team_key": preview_team_key,
                "current_owner_team_name": preview_team_name,
                "traded_flag": False,
                "ownership_note": "",
                "yahoo_player_key": None,
                "selected_player_name": "",
                "selected_at_utc": None,
                "selected_primary_position": "",
            }
        ]

    # NFHL_PREP_VISUAL_VALIDATION_END

    state_json = (
        live_state.get(
            "state_json"
        )
        or {}
    )

    clock = (
        state_json.get(
            "clock"
        )
        or {}
    )

    current_pick_id = str(
        clock.get(
            "current_pick_id"
        )
        or ""
    ).strip()

    if preview_mode:
        current_pick_id = "R01.1"

    board_lookup = {
        str(
            row["pick_id"]
        ): row
        for row in board_rows
        if row.get(
            "pick_id"
        )
    }

    current_pick = (
        board_lookup.get(
            current_pick_id
        )
        if current_pick_id
        else None
    )

    try:
        clock_snapshot = (
            get_draft_clock_snapshot()
        )
    except Exception:
        clock_snapshot = {}

    remaining_label = (
        "24:00"
        if preview_mode
        else _fmt_remaining(
            clock_snapshot
        )
    )

    current_team_name = (
        str(
            current_pick.get(
                "current_owner_team_name"
            )
            or ""
        ).strip()
        if current_pick
        else ""
    )

    role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).strip().lower()

    if preview_mode:
        target_pick_id = current_pick_id
        is_makeup_pick = False

    else:
        (
            target_pick_id,
            is_makeup_pick,
        ) = _pick_target_for_principal(
            gateway_context=(
                gateway_context
            ),
            board_lookup=board_lookup,
            current_pick_id=(
                current_pick_id
            ),
        )

    target_pick = (
        board_lookup.get(
            target_pick_id
        )
        if target_pick_id
        else None
    )

    # ============================================================
    # TERMINAL DRAFT STATE
    # ============================================================

    if (
        not preview_mode
        and draft_status == "COMPLETE"
    ):
        if status_only:
            return

        render_postgres_board_html(
            board_rows,
            players=players,
        )

        return

    # ============================================================
    # FIXED DESKTOP PICK DOCK
    # ============================================================

    with st.container(
        key="nfhl_fixed_pick_dock"
    ):
        st.markdown(
            """
            <style>
              .nfhl-kpi-line {
                  margin: 0;
                  display: inline-flex;
                  align-items: center;
                  gap: 0.28rem;
              }

              .nfhl-kpi-label {
                  font-size: 0.95rem;
                  opacity: 0.78;
                  font-weight: 900;
                  text-transform: uppercase;
                  letter-spacing: 0.045em;
              }

              .nfhl-kpi-value {
                  font-size: 1.58rem;
                  color: #22C55E;
                  font-weight: 950;
                  line-height: 1.0;
              }

              .st-key-nfhl_fixed_pick_dock {
                  position: fixed;

                  top: 3.25rem;
                  left: 0;
                  right: 0;

                  z-index: 999989;

                  padding:
                      0.42rem 1.0rem
                      0.35rem 1.0rem;

                  border-bottom:
                      1px solid
                      rgba(148,163,184,0.35);

                  background:
                      rgba(0,27,63,0.985);

                  box-shadow:
                      0 8px 22px
                      rgba(0,0,0,0.32);

                  backdrop-filter:
                      blur(8px);
              }

              .st-key-nfhl_fixed_pick_dock
              div[data-testid="stVerticalBlock"] {
                  gap: 0.10rem;
              }

              .st-key-nfhl_fixed_pick_dock
              div[data-testid="column"] {
                  padding-left: 0.16rem;
                  padding-right: 0.16rem;
              }

              .st-key-nfhl_fixed_pick_dock
              div[data-testid="stTextInput"] label,
              .st-key-nfhl_fixed_pick_dock
              div[data-testid="stSelectbox"] label {
                  display: none;
              }

              .st-key-nfhl_fixed_pick_dock
              div[data-testid="stButton"] button {
                  width: 100%;
                  min-height: 2.35rem;
                  font-weight: 900;
                  white-space: nowrap;
              }

              .nfhl-fixed-dock-info {
                  display: flex;
                  align-items: center;

                  gap: 1.05rem;

                  min-height: 2.35rem;

                  white-space: nowrap;
                  overflow: hidden;
              }

              .nfhl-fixed-dock-pill {
                  display: inline-flex;
                  align-items: baseline;
                  gap: 0.25rem;
              }

              div.block-container {
                  padding-top:
                      6.40rem !important;
              }

              @media (
                  max-width: 850px
              ) {
                  .st-key-nfhl_fixed_pick_dock {
                      position: relative;
                      padding: 0.45rem;
                  }

                  div.block-container {
                      padding-top:
                          1rem !important;
                  }

                  .nfhl-fixed-dock-info {
                      flex-wrap: wrap;
                      white-space: normal;
                  }
              }
            </style>
            """,
            unsafe_allow_html=True,
        )

        if (
            preview_mode
            or (
                role in {
                    "manager",
                    "commissioner",
                }
                and target_pick
                and draft_status == "ACTIVE"
            )
        ):
            info_col, action_col = (
                st.columns(
                    [
                        2.85,
                        3.15,
                    ],
                    gap="small",
                    vertical_alignment=(
                        "center"
                    ),
                )
            )

        else:
            info_col = st.container()
            action_col = None

        with info_col:
            status_team = (
                current_team_name
                or "—"
            )

            status_pick = (
                current_pick_id
                or "—"
            )

            # Keep the HTML compact. Indented multiline HTML can be
            # interpreted by Streamlit Markdown as code blocks.
            urgency_color = (
                _nfhl_clock_urgency_color(
                    remaining_label
                )
            )

            dock_html = (
                '<div class="nfhl-fixed-dock-info">'
                '<span class="nfhl-fixed-dock-pill">'
                '<span class="nfhl-kpi-label">Pick</span>'
                '<span class="nfhl-kpi-value" style="color:' + urgency_color + ';">'
                + escape(status_pick)
                + '</span>'
                '</span>'
                '<span class="nfhl-fixed-dock-pill">'
                '<span class="nfhl-kpi-label">Clock</span>'
                '<span class="nfhl-kpi-value" style="color:' + urgency_color + ';">'
                + escape(status_team)
                + '</span>'
                '</span>'
                '<span class="nfhl-fixed-dock-pill">'
                '<span class="nfhl-kpi-label">Time</span>'
                '<span class="nfhl-kpi-value" style="color:' + urgency_color + ';">'
                + escape(remaining_label)
                + '</span>'
                '</span>'
                '</div>'
            )

            st.markdown(
                dock_html,
                unsafe_allow_html=True,
            )

        if action_col is not None:
            with action_col:

                drafted_keys = {
                    str(
                        row[
                            "yahoo_player_key"
                        ]
                    )
                    for row in board_rows
                    if row.get(
                        "yahoo_player_key"
                    )
                }

                available_players = [
                    player
                    for player in players
                    if (
                        str(
                            player.get(
                                "yahoo_player_key"
                            )
                            or ""
                        )
                        not in drafted_keys
                    )
                    and player.get(
                        "yahoo_player_key"
                    )
                ]

                available_players.sort(
                    key=_player_rank
                )

                player_lookup = {
                    str(
                        player[
                            "yahoo_player_key"
                        ]
                    ): player
                    for player
                    in available_players
                }

                player_keys = list(
                    player_lookup.keys()
                )

                unique_target = (
                    target_pick_id
                    or current_pick_id
                    or "none"
                )

                select_key = (
                    "nfhl_pick_player_"
                    + unique_target
                )

                query_input_key = (
                    "nfhl_pick_query_input_"
                    + unique_target
                )

                query_applied_key = (
                    "nfhl_pick_query_applied_"
                    + unique_target
                )

                st.session_state.setdefault(
                    query_applied_key,
                    "",
                )

                (
                    search_button_col,
                    search_box_col,
                    player_col,
                    submit_col,
                ) = st.columns(
                    [
                        0.46,
                        1.28,
                        1.35,
                        0.36,
                    ],
                    gap="small",
                    vertical_alignment=(
                        "center"
                    ),
                )

                with search_button_col:
                    search_clicked = (
                        st.button(
                            "Search",
                            key=(
                                "nfhl_pick_search_"
                                + unique_target
                            ),
                            use_container_width=True,
                        )
                    )

                with search_box_col:
                    query = st.text_input(
                        "Search player",
                        value=str(
                            st.session_state.get(
                                query_applied_key,
                                "",
                            )
                            or ""
                        ),
                        placeholder=(
                            "Type a player name "
                            "to search..."
                        ),
                        key=query_input_key,
                        label_visibility=(
                            "collapsed"
                        ),
                    )

                previous_query = str(
                    st.session_state.get(
                        query_applied_key,
                        "",
                    )
                    or ""
                )

                current_query = str(
                    query
                    or ""
                )

                if (
                    search_clicked
                    or current_query
                    != previous_query
                ):
                    st.session_state[
                        query_applied_key
                    ] = current_query

                    st.session_state.pop(
                        select_key,
                        None,
                    )

                filtered_keys = (
                    filter_player_keys_by_query(
                        player_keys,
                        current_query,
                        lambda key: (
                            _player_display(
                                player_lookup[
                                    key
                                ]
                            )
                        ),
                    )
                )

                with player_col:
                    chosen_player_key = (
                        st.selectbox(
                            "Select player to draft",
                            options=filtered_keys,
                            format_func=(
                                lambda key: (
                                    _player_display(
                                        player_lookup[
                                            key
                                        ]
                                    )
                                )
                            ),
                            index=None,
                            placeholder=(
                                "Draft a player…"
                            ),
                            help=(
                                "Search above. "
                                "Player search is "
                                "case-insensitive and "
                                "accent-insensitive."
                            ),
                            key=select_key,
                            label_visibility=(
                                "collapsed"
                            ),
                        )
                    )

                with submit_col:
                    submit_clicked = (
                        st.button(
                            "SUBMIT",
                            type="primary",
                            key=(
                                "nfhl_pick_submit_"
                                + unique_target
                            ),
                            use_container_width=True,
                            disabled=preview_mode,
                        )
                    )

                if is_makeup_pick:
                    st.caption(
                        "Makeup pick: "
                        f"{target_pick_id} — "
                        f"{target_pick.get('current_owner_team_name') or ''}"
                    )

                if submit_clicked:
                    if preview_mode:
                        st.error(
                            "PREVIEW MODE cannot submit draft picks."
                        )
                        return

                    if chosen_player_key is None:
                        st.warning(
                            "Pick a player first."
                        )
                        return

                    if (
                        chosen_player_key
                        in drafted_keys
                    ):
                        st.error(
                            "Player already drafted."
                        )
                        return

                    if not target_pick:
                        st.error(
                            "You are not authorized "
                            "to submit a pick."
                        )
                        return

                    target_team_key = str(
                        target_pick.get(
                            "current_owner_team_key"
                        )
                        or ""
                    ).strip()

                    if not target_team_key:
                        st.error(
                            "The target pick does "
                            "not have an owner."
                        )
                        return

                    if role == "commissioner":
                        actor = (
                            "commissioner_link"
                        )

                    else:
                        actor = (
                            "manager:"
                            + target_team_key
                        )

                    try:
                        result = (
                            submit_manual_draft_pick(
                                expected_pick_id=(
                                    target_pick_id
                                    or ""
                                ),
                                expected_team_key=(
                                    target_team_key
                                ),
                                yahoo_player_key=(
                                    chosen_player_key
                                ),
                                actor=actor,
                            )
                        )

                    except Exception as exc:
                        st.error(
                            "Pick was not submitted: "
                            f"{exc}"
                        )
                        return

                    result_status = str(
                        result.get(
                            "result_status"
                        )
                        or ""
                    ).upper()

                    if (
                        result_status
                        != "EXECUTED"
                    ):
                        st.error(
                            "The draft engine did "
                            "not execute the pick: "
                            f"{result_status or 'UNKNOWN'}"
                        )
                        return

                    player_name = str(
                        player_lookup[
                            chosen_player_key
                        ].get(
                            "full_name"
                        )
                        or chosen_player_key
                    )

                    if bool(
                        result.get(
                            "late_pick"
                        )
                    ):
                        st.success(
                            "Late pick recorded: "
                            f"{player_name}."
                        )

                    else:
                        st.success(
                            "Pick recorded: "
                            f"{player_name}."
                        )

                    st.cache_data.clear()
                    st.rerun()

    if status_only:
        return

    # ============================================================
    # AUTHORITATIVE GRAPHICAL BOARD
    # ============================================================

    if not preview_mode:
        render_postgres_board_html(
            board_rows,
            players=players,
        )

    # For a logged-in manager who cannot pick right now, keep the
    # status concise and below the board rather than rendering a
    # disabled picker.
    if (
        not preview_mode
        and role == "manager"
        and draft_status == "ACTIVE"
        and target_pick_id is None
    ):
        st.caption(
            "Draft controls will appear when your team is on the "
            "clock or if you have an unresolved expired pick."
        )
