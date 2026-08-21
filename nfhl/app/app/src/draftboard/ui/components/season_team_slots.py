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
    slots = get_season_team_slots()

    st.subheader(
        "League Roll Call"
    )

    st.caption(
        "The fourteen league slots below are seeded from the 2025 NFHL. "
        "They let us track returning and replacement managers while the "
        "2026 Yahoo league is still filling. These league slots are not "
        "draft positions."
    )

    linked = sum(
        1
        for row in slots
        if row.get(
            "current_team_key"
        )
    )

    returning = sum(
        1
        for row in slots
        if str(
            row.get(
                "assignment_status"
            )
            or ""
        ).upper()
        == "RETURNING"
    )

    replaced = sum(
        1
        for row in slots
        if str(
            row.get(
                "assignment_status"
            )
            or ""
        ).upper()
        == "REPLACED"
    )

    pending = sum(
        1
        for row in slots
        if str(
            row.get(
                "assignment_status"
            )
            or ""
        ).upper()
        == "PENDING"
    )

    c1, c2, c3, c4 = st.columns(
        4
    )

    c1.metric(
        "League Slots",
        len(slots),
    )

    c2.metric(
        "Yahoo Linked",
        f"{linked}/14",
    )

    c3.metric(
        "Returning",
        returning,
    )

    c4.metric(
        "Pending / Replacement",
        pending + replaced,
    )

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

        replacement_manager = str(
            row.get(
                "replacement_manager_name"
            )
            or ""
        ).strip()

        replacement_team = str(
            row.get(
                "replacement_team_name"
            )
            or ""
        ).strip()

        if status == "REPLACED":
            manager_2026 = (
                yahoo_manager
                or replacement_manager
                or "TBD"
            )

            team_2026 = (
                yahoo_team
                or replacement_team
                or "TBD"
            )

        elif status == "RETURNING":
            manager_2026 = (
                yahoo_manager
                or row.get(
                    "prior_manager_name"
                )
            )

            team_2026 = (
                yahoo_team
                or row.get(
                    "prior_team_name"
                )
            )

        else:
            manager_2026 = "Pending"
            team_2026 = "Pending"

        display_rows.append(
            {
                "Slot": row[
                    "league_slot_number"
                ],
                "2025 Manager": row[
                    "prior_manager_name"
                ],
                "2025 Team": row[
                    "prior_team_name"
                ],
                "Status": status,
                "2026 Manager": manager_2026,
                "2026 Team": team_2026,
                "Yahoo Linked": (
                    "Yes"
                    if row.get(
                        "current_team_key"
                    )
                    else "No"
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            display_rows
        ),
        hide_index=True,
        use_container_width=True,
    )

    role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).strip().lower()

    if role != "commissioner":
        return

    with st.expander(
        "Commissioner — League Roll Call / Team Assignments",
        expanded=False,
    ):
        st.caption(
            "Use this to mark returning managers, record replacements, "
            "and associate each league slot with the manager's real "
            "2026 Yahoo team. Changes are allowed only while the draft "
            "is in PREP."
        )

        if st.button(
            "Auto-Match Current Yahoo Teams",
            key="nfhl_slot_auto_match",
        ):
            try:
                result = (
                    auto_match_season_team_slots()
                )
            except Exception as exc:
                st.error(
                    f"Auto-match failed: {exc}"
                )
            else:
                st.success(
                    "Auto-match complete. "
                    f"New links: {result['matched']}."
                )
                st.cache_data.clear()
                st.rerun()

        slot_labels = {
            int(
                row[
                    "league_slot_number"
                ]
            ): (
                f"Slot {int(row['league_slot_number'])}: "
                f"{row['prior_manager_name']} — "
                f"{row['prior_team_name']}"
            )
            for row in slots
        }

        selected_slot = st.selectbox(
            "League Slot",
            options=list(
                slot_labels.keys()
            ),
            format_func=lambda value: (
                slot_labels[
                    int(value)
                ]
            ),
            key="nfhl_rollcall_slot",
        )

        slot = next(
            row
            for row in slots
            if int(
                row[
                    "league_slot_number"
                ]
            )
            == int(
                selected_slot
            )
        )

        st.write(
            f"**2025 manager:** "
            f"{slot['prior_manager_name']}"
        )

        st.write(
            f"**2025 team:** "
            f"{slot['prior_team_name']}"
        )

        statuses = [
            "PENDING",
            "RETURNING",
            "REPLACED",
        ]

        current_status = str(
            slot.get(
                "assignment_status"
            )
            or "PENDING"
        ).upper()

        status = st.selectbox(
            "2026 Status",
            options=statuses,
            index=(
                statuses.index(
                    current_status
                )
                if current_status
                in statuses
                else 0
            ),
            key=(
                "nfhl_rollcall_status_"
                f"{selected_slot}"
            ),
        )

        assigned_elsewhere = {
            str(
                row.get(
                    "current_team_key"
                )
            )
            for row in slots
            if row.get(
                "current_team_key"
            )
            and int(
                row[
                    "league_slot_number"
                ]
            )
            != int(
                selected_slot
            )
        }

        current_team_key = str(
            slot.get(
                "current_team_key"
            )
            or ""
        )

        available_teams = [
            team
            for team in teams
            if (
                str(
                    team.get(
                        "team_key"
                    )
                    or ""
                )
                not in assigned_elsewhere
            )
        ]

        team_by_key = {
            str(
                team.get(
                    "team_key"
                )
                or ""
            ): team
            for team in available_teams
        }

        team_options = [
            ""
        ] + sorted(
            team_by_key.keys(),
            key=lambda key: str(
                team_by_key[
                    key
                ].get(
                    "team_name"
                )
                or key
            ).casefold(),
        )

        if (
            current_team_key
            and current_team_key
            not in team_options
        ):
            team_options.append(
                current_team_key
            )

        team_index = (
            team_options.index(
                current_team_key
            )
            if current_team_key
            in team_options
            else 0
        )

        selected_team_key = st.selectbox(
            "2026 Yahoo Team",
            options=team_options,
            index=team_index,
            format_func=lambda key: (
                "— Not linked yet —"
                if not key
                else (
                    f"{team_by_key.get(key, {}).get('team_name') or key}"
                    " — "
                    f"{team_by_key.get(key, {}).get('owner_name') or ''}"
                )
            ),
            key=(
                "nfhl_rollcall_team_"
                f"{selected_slot}"
            ),
        )

        replacement_manager = (
            st.text_input(
                "Replacement Manager",
                value=str(
                    slot.get(
                        "replacement_manager_name"
                    )
                    or ""
                ),
                disabled=(
                    status
                    != "REPLACED"
                ),
                key=(
                    "nfhl_rollcall_replacement_manager_"
                    f"{selected_slot}"
                ),
            )
        )

        replacement_team = (
            st.text_input(
                "Replacement Team",
                value=str(
                    slot.get(
                        "replacement_team_name"
                    )
                    or ""
                ),
                disabled=(
                    status
                    != "REPLACED"
                ),
                key=(
                    "nfhl_rollcall_replacement_team_"
                    f"{selected_slot}"
                ),
            )
        )

        commissioner_note = (
            st.text_input(
                "Commissioner Note",
                value=str(
                    slot.get(
                        "commissioner_note"
                    )
                    or ""
                ),
                key=(
                    "nfhl_rollcall_note_"
                    f"{selected_slot}"
                ),
            )
        )

        if st.button(
            "Save League Slot",
            type="primary",
            key=(
                "nfhl_rollcall_save_"
                f"{selected_slot}"
            ),
        ):
            try:
                save_season_team_slot_assignment(
                    league_slot_number=int(
                        selected_slot
                    ),
                    assignment_status=status,
                    current_team_key=(
                        selected_team_key
                        or None
                    ),
                    replacement_manager_name=(
                        replacement_manager
                        or None
                    ),
                    replacement_team_name=(
                        replacement_team
                        or None
                    ),
                    commissioner_note=(
                        commissioner_note
                        or None
                    ),
                )

            except Exception as exc:
                st.error(
                    f"League-slot update failed: {exc}"
                )

            else:
                st.success(
                    f"League slot {selected_slot} saved."
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
              top: 3.25rem;
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
