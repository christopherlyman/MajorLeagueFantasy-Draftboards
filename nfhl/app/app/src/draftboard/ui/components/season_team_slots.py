from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from draftboard.data.season_team_slots import (
    auto_match_season_team_slots,
    get_preview_board_meta,
    get_season_team_slots,
    save_season_team_slot_assignment,
)


def _display_team_name(
    slot: dict,
) -> str:
    current = str(
        slot.get(
            "current_team_name"
        )
        or ""
    ).strip()

    replacement = str(
        slot.get(
            "replacement_team_name"
        )
        or ""
    ).strip()

    prior = str(
        slot.get(
            "prior_team_name"
        )
        or ""
    ).strip()

    if current:
        return current

    if replacement:
        return replacement

    return prior or "TBD"


def _display_manager_name(
    slot: dict,
) -> str:
    current = str(
        slot.get(
            "current_owner_name"
        )
        or ""
    ).strip()

    replacement = str(
        slot.get(
            "replacement_manager_name"
        )
        or ""
    ).strip()

    prior = str(
        slot.get(
            "prior_manager_name"
        )
        or ""
    ).strip()

    if current:
        return current

    if replacement:
        return replacement

    return prior or "TBD"


def render_season_team_slots(
    *,
    gateway_context: dict[str, object],
    teams: list[dict],
) -> None:
    role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).strip().lower()

    if role != "commissioner":
        return

    slots = get_season_team_slots()

    display_rows = []

    for row in slots:
        status = str(
            row.get(
                "assignment_status"
            )
            or "PENDING"
        ).upper()

        yahoo_team = str(
            row.get(
                "current_team_name"
            )
            or ""
        ).strip()

        yahoo_manager = str(
            row.get(
                "current_owner_name"
            )
            or ""
        ).strip()

        if status == "RETURNING":
            current_manager = (
                yahoo_manager
                or str(
                    row.get(
                        "prior_manager_name"
                    )
                    or ""
                )
            )

            current_team = (
                yahoo_team
                or str(
                    row.get(
                        "prior_team_name"
                    )
                    or ""
                )
            )

        elif status == "REPLACED":
            current_manager = (
                yahoo_manager
                or str(
                    row.get(
                        "replacement_manager_name"
                    )
                    or "TBD"
                )
            )

            current_team = (
                yahoo_team
                or str(
                    row.get(
                        "replacement_team_name"
                    )
                    or "TBD"
                )
            )

        else:
            current_manager = (
                "Waiting to join Yahoo"
            )

            current_team = "—"

        display_rows.append(
            {
                "Slot":
                    row[
                        "league_slot_number"
                    ],
                "Prior Manager":
                    row[
                        "prior_manager_name"
                    ],
                "Prior Team":
                    row[
                        "prior_team_name"
                    ],
                "Current Manager":
                    current_manager,
                "Current Yahoo Team":
                    current_team,
                "Status":
                    status,
            }
        )

    st.markdown(
        "#### Roll Call & Team Mapping"
    )

    # Full 14-row table: no inner vertical scrolling.
    st.dataframe(
        pd.DataFrame(
            display_rows
        ),
        hide_index=True,
        use_container_width=True,
        height=600,
    )

    pending_slots = [
        row
        for row in slots
        if (
            str(
                row.get(
                    "assignment_status"
                )
                or ""
            ).upper()
            == "PENDING"
            and not row.get(
                "current_team_key"
            )
        )
    ]

    assigned_team_keys = {
        str(
            row.get(
                "current_team_key"
            )
            or ""
        )
        for row in slots
        if row.get(
            "current_team_key"
        )
    }

    unassigned_teams = [
        team
        for team in teams
        if (
            str(
                team.get(
                    "team_key"
                )
                or ""
            )
            not in assigned_team_keys
        )
    ]

    if (
        not pending_slots
        and not unassigned_teams
    ):
        st.success(
            "Roll call is fully resolved. "
            "No commissioner action is required."
        )
        return

    if not unassigned_teams:
        waiting_names = ", ".join(
            str(
                row.get(
                    "prior_manager_name"
                )
                or "Unknown"
            )
            for row in pending_slots
        )

        st.info(
            f"{len(pending_slots)} prior manager"
            f"{'s have' if len(pending_slots) != 1 else ' has'} "
            "not joined the current Yahoo league yet: "
            f"{waiting_names}. "
            "No manual mapping is required unless "
            "one is being replaced."
        )

        return

    if not pending_slots:
        st.error(
            "Yahoo contains an unassigned current team, "
            "but there are no PENDING prior-season slots."
        )
        return

    st.warning(
        f"{len(unassigned_teams)} current Yahoo manager"
        f"{'s require' if len(unassigned_teams) != 1 else ' requires'} "
        "a roll-call decision."
    )

    team_by_key = {
        str(
            team.get(
                "team_key"
            )
            or ""
        ): team
        for team in unassigned_teams
    }

    team_keys = sorted(
        team_by_key.keys(),
        key=lambda key: (
            str(
                team_by_key[key].get(
                    "owner_name"
                )
                or ""
            ).casefold(),
            str(
                team_by_key[key].get(
                    "team_name"
                )
                or ""
            ).casefold(),
        ),
    )

    slot_by_number = {
        int(
            row[
                "league_slot_number"
            ]
        ): row
        for row in pending_slots
    }

    slot_numbers = sorted(
        slot_by_number.keys()
    )

    # Form prevents each selector change from rerunning the app.
    with st.form(
        "nfhl_rollcall_exception_form",
        clear_on_submit=False,
    ):
        selected_team_key = st.selectbox(
            "Unmatched current Yahoo manager",
            options=team_keys,
            format_func=lambda key: (
                f"{team_by_key[key].get('owner_name') or 'Unknown'}"
                " — "
                f"{team_by_key[key].get('team_name') or key}"
            ),
        )

        selected_slot = st.selectbox(
            "Prior-season manager being replaced",
            options=slot_numbers,
            format_func=lambda number: (
                f"{slot_by_number[number]['prior_manager_name']}"
                " — "
                f"{slot_by_number[number]['prior_team_name']}"
            ),
        )

        identity_type = st.radio(
            "Classification",
            options=[
                "Replacement manager",
                (
                    "Returning manager whose Yahoo "
                    "display name changed"
                ),
            ],
        )

        commissioner_note = st.text_input(
            "Commissioner note",
        )

        submitted = (
            st.form_submit_button(
                "Resolve Roll Call Exception",
                type="primary",
                use_container_width=True,
            )
        )

    if not submitted:
        return

    selected_team = (
        team_by_key[
            selected_team_key
        ]
    )

    status = (
        "RETURNING"
        if identity_type.startswith(
            "Returning manager"
        )
        else "REPLACED"
    )

    try:
        save_season_team_slot_assignment(
            league_slot_number=int(
                selected_slot
            ),
            assignment_status=status,
            current_team_key=(
                selected_team_key
            ),
            replacement_manager_name=None,
            replacement_team_name=None,
            commissioner_note=(
                commissioner_note
                or None
            ),
        )

    except Exception as exc:
        st.error(
            "Roll-call exception could not "
            f"be saved: {exc}"
        )
        return

    # Main view is already Commissioner and is maintained
    # by the keyed segmented-control widget.
    #
    # Streamlit 1.41 expanders are not stateful, so force
    # League Setup open for the immediate post-save rerun.
    st.session_state[
        "nfhl_force_open_league_setup"
    ] = True

    st.session_state[
        "nfhl_yahoo_team_refresh_notice"
    ] = (
        "Roll call updated: "
        f"{selected_team.get('owner_name') or 'manager'} "
        "was assigned to "
        f"{slot_by_number[int(selected_slot)]['prior_manager_name']}'s "
        "prior-season slot."
    )

    st.cache_data.clear()
    st.rerun()


def render_preview_draft_board() -> None:
    """
    PREP-only full-board visual preview.

    Visual contract intentionally follows the proven NFFL DraftBoard:
      - one column per fantasy team
      - team name only in each column header
      - no round-number/index column
      - every draft cell contains its own Rxx.slot label
      - fixed board geometry matching the NFFL renderer

    The administrative league-slot sequence is used only until the
    official lottery creates the real draft order.
    """
    try:
        meta = get_preview_board_meta()
        slots = get_season_team_slots()

    except Exception as exc:
        st.warning(
            f"Draft preview is unavailable: {exc}"
        )
        return

    draft_pick_rows = int(
        meta.get("draft_pick_rows")
        or 0
    )

    # Once the production board exists, this PREP preview gets out
    # of the way. The canonical live board renderer will own that
    # phase.
    if draft_pick_rows > 0:
        return

    rounds_total = int(
        meta.get("rounds_total")
        or 18
    )

    if len(slots) != 14:
        st.warning(
            "Draft preview requires exactly fourteen league slots."
        )
        return

    st.caption(
        "PRE-DRAFT PREVIEW — Team columns are shown so the complete "
        "board can be reviewed while roll call is still underway. "
        "The official column order will be set by the finalized "
        "Draft Lottery."
    )

    # NFFL visual geometry:
    #   header height 74px
    #   cell height 96px
    #   4px grid gaps
    #   sticky team-name header
    #
    # NFHL changes only the league palette.
    st.markdown(
        f"""
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >

        <style>
          .nfhl-db-wrap {{
              color: #111 !important;
              width: 100%;
          }}

          .nfhl-db-header {{
              display: grid;
              grid-template-columns:
                  repeat(14, minmax(72px, 1fr));
              gap: 4px;

              position: sticky;
              top: 6.40rem;
              z-index: 20;

              background:
                  linear-gradient(
                      135deg,
                      #001B3F 0%,
                      #002868 58%,
                      #0055A5 100%
                  );

              padding: 10px 0 12px 0;
              border-bottom: 3px solid #FFFFFF;
          }}

          .nfhl-db-hcell {{
              background: #002868;
              color: #FFFFFF !important;

              border: 2px solid #4B92DB;
              border-radius: 10px;

              padding: 8px 10px;
              box-sizing: border-box;

              font-weight: 950;
              font-size:
                  clamp(0.95rem, 1.2vw, 1.12rem);
              line-height:
                  clamp(1.05rem, 1.4vw, 1.22rem);

              height: 74px;

              text-align: center;
              text-transform: uppercase;
              letter-spacing: -0.03em;

              text-shadow:
                  0 0 8px rgba(255,255,255,0.14);

              box-shadow:
                  inset 0 -4px 0 #4B92DB,
                  0 2px 8px rgba(0,0,0,0.24);

              display: -webkit-box;
              -webkit-line-clamp: 2;
              -webkit-box-orient: vertical;
              overflow: hidden;
          }}

          .nfhl-db-grid {{
              display: grid;
              grid-template-columns:
                  repeat(14, minmax(72px, 1fr));

              gap: 4px;
              align-items: stretch;
              padding: 8px 0 12px 0;
          }}

          .nfhl-db-cell {{
              border:
                  1.5px solid rgba(0,0,0,0.18);

              border-radius: 14px;
              height: 96px;
              padding: 8px 8px;

              position: relative;
              overflow: hidden;

              box-shadow:
                  0 1px 2px rgba(0,0,0,0.06);

              background: #F8FAFC;
              color: #0F172A !important;
          }}

          .nfhl-db-tr {{
              position: absolute;
              top: 6px;
              right: 8px;

              font-size:
                  clamp(0.64rem, 1.0vw, 0.78rem);

              opacity: 0.92;
              font-weight: 900;
              white-space: nowrap;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    html = (
        '<div class="nfhl-db-wrap">'
        '<div class="nfhl-db-header">'
    )

    # Headers contain TEAM NAME ONLY.
    for slot in slots:
        team_name = escape(
            _display_team_name(slot)
        )

        html += (
            '<div '
            'class="nfhl-db-hcell" '
            f'title="{team_name}">'
            f'{team_name}'
            '</div>'
        )

    html += (
        '</div>'
        '<div class="nfhl-db-grid">'
    )

    # No round/index gutter. Every cell labels itself just like NFFL.
    for round_number in range(
        1,
        rounds_total + 1,
    ):
        for slot_number in range(
            1,
            15,
        ):
            label = (
                f"R{round_number:02d}."
                f"{slot_number}"
            )

            html += (
                '<div class="nfhl-db-cell">'
                '<div class="nfhl-db-tr">'
                f'{escape(label)}'
                '</div>'
                '</div>'
            )

    html += (
        '</div>'
        '</div>'
    )

    st.markdown(
        html,
        unsafe_allow_html=True,
    )

    st.caption(
        f"14 teams × {rounds_total} rounds = "
        f"{14 * rounds_total} draft cells."
    )
