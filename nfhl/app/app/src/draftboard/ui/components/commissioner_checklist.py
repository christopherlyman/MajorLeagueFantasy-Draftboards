from __future__ import annotations

from typing import Any

import streamlit as st

from draftboard.data.db import (
    ensure_team_gateway_links,
    get_dashboard_summary,
    get_draft_initialization_readiness,
    get_live_draft_board,
    get_lottery_state,
    get_team_gateway_links,
    get_teams,
)

from draftboard.data.season_team_slots import (
    auto_match_season_team_slots,
    get_season_team_slots,
)


_COMPLETE_STATUSES = {
    "COMPLETE",
    "COMPLETED",
    "FINISHED",
    "FINAL",
}


def _upper(value: object) -> str:
    return str(
        value
        or ""
    ).strip().upper()


def _int(value: object) -> int:
    try:
        return int(
            value
            or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0


def get_commissioner_checklist_state(
    *,
    run_housekeeping: bool = True,
) -> dict[str, Any]:
    """
    Return current Commissioner workflow/readiness state.

    Safe automatic housekeeping:
    - resolve unique returning-manager matches while PREP;
    - ensure manager gateway links exist.

    This function does NOT:
    - refresh Yahoo;
    - run/finalize a lottery;
    - initialize the Draft Board;
    - start/pause/resume the draft;
    - delete or rewind picks.
    """

    housekeeping = {
        "auto_matched": 0,
        "links_created": 0,
        "errors": [],
    }

    summary = get_dashboard_summary()

    draft_status = _upper(
        summary.get(
            "draft_status"
        )
    )

    # ------------------------------------------------------------
    # Deterministic housekeeping.
    # ------------------------------------------------------------

    if (
        run_housekeeping
        and draft_status == "PREP"
    ):
        try:
            result = (
                auto_match_season_team_slots()
            )

            housekeeping[
                "auto_matched"
            ] = _int(
                result.get(
                    "matched"
                )
            )

        except Exception as exc:
            housekeeping[
                "errors"
            ].append(
                "Returning-manager automation: "
                f"{exc}"
            )

    if run_housekeeping:
        try:
            housekeeping[
                "links_created"
            ] = _int(
                ensure_team_gateway_links()
            )

        except Exception as exc:
            housekeeping[
                "errors"
            ].append(
                "Manager-link provisioning: "
                f"{exc}"
            )

    # ------------------------------------------------------------
    # Reload state after housekeeping.
    # ------------------------------------------------------------

    summary = get_dashboard_summary()
    teams = get_teams()
    slots = get_season_team_slots()
    links = get_team_gateway_links()
    board = get_live_draft_board()

    try:
        readiness = (
            get_draft_initialization_readiness()
        )

    except Exception as exc:
        readiness = {
            "ready": False,
            "blockers": [
                f"Readiness check failed: {exc}"
            ],
        }

    try:
        lottery = get_lottery_state()

    except Exception:
        lottery = None

    draft_status = _upper(
        summary.get(
            "draft_status"
        )
    )

    player_count = _int(
        summary.get(
            "player_count"
        )
    )

    team_count = len(
        teams
    )

    target_teams = _int(
        readiness.get(
            "manager_count"
        )
    )

    if target_teams <= 0:
        target_teams = (
            len(slots)
            or 14
        )

    expected_picks = _int(
        readiness.get(
            "expected_draft_pick_count"
        )
    )

    rounds_total = _int(
        readiness.get(
            "rounds_total"
        )
    )

    order_mode = str(
        readiness.get(
            "draft_order_mode"
        )
        or ""
    ).strip().lower()

    finalized_lottery_count = _int(
        readiness.get(
            "finalized_lottery_count"
        )
    )

    # ------------------------------------------------------------
    # Roll call.
    # ------------------------------------------------------------

    mapped_slots = [
        row
        for row in slots
        if row.get(
            "current_team_key"
        )
    ]

    pending_slots = [
        row
        for row in slots
        if _upper(
            row.get(
                "assignment_status"
            )
        ) == "PENDING"
    ]

    returning_slots = [
        row
        for row in slots
        if _upper(
            row.get(
                "assignment_status"
            )
        ) == "RETURNING"
    ]

    replaced_slots = [
        row
        for row in slots
        if _upper(
            row.get(
                "assignment_status"
            )
        ) == "REPLACED"
    ]

    assigned_team_keys = {
        str(
            row.get(
                "current_team_key"
            )
            or ""
        )
        for row in mapped_slots
        if row.get(
            "current_team_key"
        )
    }

    current_team_keys = {
        str(
            team.get(
                "team_key"
            )
            or ""
        )
        for team in teams
        if team.get(
            "team_key"
        )
    }

    unmatched_current_teams = [
        team
        for team in teams
        if str(
            team.get(
                "team_key"
            )
            or ""
        )
        not in assigned_team_keys
    ]

    waiting_prior_managers = [
        str(
            row.get(
                "prior_manager_name"
            )
            or "Unknown"
        )
        for row in pending_slots
    ]

    # ------------------------------------------------------------
    # Manager access.
    # ------------------------------------------------------------

    active_link_keys = {
        str(
            row.get(
                "team_key"
            )
            or ""
        )
        for row in links
        if (
            row.get(
                "team_key"
            )
            and bool(
                row.get(
                    "is_active"
                )
            )
        )
    }

    active_current_links = (
        current_team_keys
        & active_link_keys
    )

    claimed_current_links = sum(
        1
        for row in links
        if (
            str(
                row.get(
                    "team_key"
                )
                or ""
            )
            in current_team_keys
            and bool(
                row.get(
                    "is_active"
                )
            )
            and _int(
                row.get(
                    "claim_count"
                )
            ) > 0
        )
    )

    # ------------------------------------------------------------
    # Derived workflow state.
    # ------------------------------------------------------------

    membership_complete = (
        team_count
        == target_teams
    )

    current_identities_resolved = (
        team_count > 0
        and not unmatched_current_teams
    )

    rollcall_complete = (
        len(mapped_slots)
        == target_teams
        and not unmatched_current_teams
    )

    manager_access_ready = (
        team_count > 0
        and len(
            active_current_links
        )
        == team_count
    )

    player_universe_available = (
        player_count > 0
    )

    draft_format_configured = (
        target_teams > 0
        and rounds_total > 0
        and expected_picks > 0
        and order_mode == "snake"
    )

    lottery_finalized = (
        finalized_lottery_count
        == 1
    )

    board_rows = len(
        board
    )

    board_initialized = (
        expected_picks > 0
        and board_rows
        == expected_picks
    )

    checks = [
        membership_complete,
        current_identities_resolved,
        rollcall_complete,
        manager_access_ready,
        player_universe_available,
        draft_format_configured,
        lottery_finalized,
        board_initialized,
    ]

    completed_checks = sum(
        1
        for value in checks
        if value
    )

    total_checks = len(
        checks
    )

    # ------------------------------------------------------------
    # Determine the single most useful next action.
    # ------------------------------------------------------------

    if not membership_complete:
        next_action = {
            "title":
                "Complete League Membership",
            "kind":
                "waiting",
            "section":
                "League Setup & Roll Call",
            "detail": (
                f"{team_count} of {target_teams} Yahoo "
                "teams have joined."
            ),
        }

        if (
            waiting_prior_managers
            and not unmatched_current_teams
        ):
            next_action[
                "detail"
            ] += (
                " Waiting on: "
                + ", ".join(
                    waiting_prior_managers
                )
                + ". No manual roll-call "
                  "mapping is required right now."
            )

    elif unmatched_current_teams:
        names = [
            str(
                row.get(
                    "owner_name"
                )
                or row.get(
                    "team_name"
                )
                or "Unknown"
            )
            for row
            in unmatched_current_teams
        ]

        next_action = {
            "title":
                "Resolve Roll Call Exceptions",
            "kind":
                "action",
            "section":
                "League Setup & Roll Call",
            "detail": (
                "Yahoo contains manager identities "
                "that cannot be matched automatically: "
                + ", ".join(names)
                + "."
            ),
        }

    elif not rollcall_complete:
        next_action = {
            "title":
                "Resolve Remaining League Slots",
            "kind":
                "action",
            "section":
                "League Setup & Roll Call",
            "detail": (
                f"{len(mapped_slots)} of "
                f"{target_teams} prior-season slots "
                "are mapped."
            ),
        }

    elif not manager_access_ready:
        next_action = {
            "title":
                "Repair Manager Access",
            "kind":
                "action",
            "section":
                "Manager Access",
            "detail": (
                f"{len(active_current_links)} of "
                f"{team_count} current Yahoo teams "
                "have active manager links."
            ),
        }

    elif not player_universe_available:
        next_action = {
            "title":
                "Refresh Player Data",
            "kind":
                "action",
            "section":
                "League Setup & Roll Call",
            "detail":
                "No current player universe is loaded.",
        }

    elif not draft_format_configured:
        next_action = {
            "title":
                "Verify Draft Configuration",
            "kind":
                "action",
            "section":
                "Draft Setup",
            "detail": (
                "Draft format is not fully configured "
                "as a snake draft."
            ),
        }

    elif not lottery_finalized:
        next_action = {
            "title":
                "Run the Draft Lottery",
            "kind":
                "action",
            "section":
                "Draft Setup",
            "detail": (
                "League formation is complete. "
                "The draft lottery is the next "
                "required step."
            ),
        }

    elif not board_initialized:
        next_action = {
            "title":
                "Initialize the Draft Board",
            "kind":
                "action",
            "section":
                "Draft Setup",
            "detail": (
                "The lottery is finalized, but the "
                f"{expected_picks}-pick Draft Board "
                "has not been initialized."
            ),
        }

    else:
        next_action = {
            "title":
                "Verify the Draft Board",
            "kind":
                "action",
            "section":
                "Draft Setup",
            "detail": (
                "Automated prerequisites are complete. "
                "Visually verify the final team order "
                "and board before starting the draft."
            ),
        }

    draft_complete = (
        draft_status
        in _COMPLETE_STATUSES
    )

    if draft_status == "ACTIVE":
        draft_phase = "ACTIVE"

    elif draft_complete:
        draft_phase = "Complete"

    else:
        draft_phase = "Not started"

    post_phase = (
        "Ready for closeout"
        if draft_complete
        else "Waiting"
    )

    return {
        "draft_status":
            draft_status,
        "team_count":
            team_count,
        "target_teams":
            target_teams,
        "player_count":
            player_count,
        "mapped_count":
            len(mapped_slots),
        "returning_count":
            len(returning_slots),
        "replaced_count":
            len(replaced_slots),
        "pending_count":
            len(pending_slots),
        "waiting_prior_managers":
            waiting_prior_managers,
        "unmatched_current_teams":
            unmatched_current_teams,
        "active_link_count":
            len(active_current_links),
        "claimed_link_count":
            claimed_current_links,
        "membership_complete":
            membership_complete,
        "current_identities_resolved":
            current_identities_resolved,
        "rollcall_complete":
            rollcall_complete,
        "manager_access_ready":
            manager_access_ready,
        "player_universe_available":
            player_universe_available,
        "draft_format_configured":
            draft_format_configured,
        "rounds_total":
            rounds_total,
        "order_mode":
            order_mode,
        "expected_picks":
            expected_picks,
        "lottery_started":
            lottery is not None,
        "lottery_finalized":
            lottery_finalized,
        "board_rows":
            board_rows,
        "board_initialized":
            board_initialized,
        "readiness_blockers":
            list(
                readiness.get(
                    "blockers"
                )
                or []
            ),
        "completed_checks":
            completed_checks,
        "total_checks":
            total_checks,
        "draft_phase":
            draft_phase,
        "post_phase":
            post_phase,
        "draft_complete":
            draft_complete,
        "next_action":
            next_action,
        "housekeeping":
            housekeeping,
    }


def _render_check(
    label: str,
    *,
    state: str,
    detail: str,
) -> None:
    icons = {
        "complete": "✓",
        "waiting": "○",
        "action": "⚠",
        "locked": "🔒",
    }

    icon = icons.get(
        state,
        "○",
    )

    st.markdown(
        f"{icon} **{label}** — {detail}"
    )


def render_commissioner_checklist(
    *,
    gateway_context: dict[str, object],
) -> None:
    role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).strip().lower()

    if role != "commissioner":
        return

    state = (
        get_commissioner_checklist_state(
            run_housekeeping=True,
        )
    )

    st.markdown(
        "### Commissioner Readiness"
    )

    c1, c2, c3 = st.columns(
        3
    )

    c1.metric(
        "OFFSEASON",
        (
            f"{state['completed_checks']}/"
            f"{state['total_checks']} complete"
        ),
    )

    c2.metric(
        "DRAFT",
        state[
            "draft_phase"
        ],
    )

    c3.metric(
        "POST-DRAFT",
        state[
            "post_phase"
        ],
    )

    progress = (
        state[
            "completed_checks"
        ]
        / max(
            1,
            state[
                "total_checks"
            ],
        )
    )

    st.progress(
        progress
    )

    housekeeping = state[
        "housekeeping"
    ]

    if housekeeping[
        "auto_matched"
    ]:
        st.success(
            "Automatically resolved "
            f"{housekeeping['auto_matched']} "
            "returning manager"
            + (
                "s."
                if housekeeping[
                    "auto_matched"
                ] != 1
                else "."
            )
        )

    if housekeeping[
        "links_created"
    ]:
        st.success(
            "Automatically created "
            f"{housekeeping['links_created']} "
            "missing manager-access link"
            + (
                "s."
                if housekeeping[
                    "links_created"
                ] != 1
                else "."
            )
        )

    for error in housekeeping[
        "errors"
    ]:
        st.warning(
            error
        )

    next_action = state[
        "next_action"
    ]

    message = (
        f"**Next action: "
        f"{next_action['title']}**  \n"
        f"{next_action['detail']}  \n"
        f"Section: "
        f"{next_action['section']}"
    )

    if (
        next_action[
            "kind"
        ]
        == "waiting"
    ):
        st.info(
            message
        )

    else:
        st.warning(
            message
        )

    # ------------------------------------------------------------
    # OFFSEASON
    # ------------------------------------------------------------

    with st.expander(
        (
            "Offseason Checklist — "
            f"{state['completed_checks']}/"
            f"{state['total_checks']} complete"
        ),
        expanded=False,
    ):
        _render_check(
            "League membership",
            state=(
                "complete"
                if state[
                    "membership_complete"
                ]
                else "waiting"
            ),
            detail=(
                f"{state['team_count']}/"
                f"{state['target_teams']} "
                "Yahoo teams joined"
            ),
        )

        _render_check(
            "Current manager identities",
            state=(
                "complete"
                if state[
                    "current_identities_resolved"
                ]
                else "action"
            ),
            detail=(
                f"{state['team_count']} current "
                "Yahoo teams; "
                f"{len(state['unmatched_current_teams'])} "
                "manual exceptions"
            ),
        )

        if state[
            "rollcall_complete"
        ]:
            rollcall_state = (
                "complete"
            )

        elif state[
            "unmatched_current_teams"
        ]:
            rollcall_state = (
                "action"
            )

        else:
            rollcall_state = (
                "waiting"
            )

        _render_check(
            "Roll call & team mapping",
            state=rollcall_state,
            detail=(
                f"{state['mapped_count']}/"
                f"{state['target_teams']} "
                "league slots mapped"
            ),
        )

        _render_check(
            "Manager access",
            state=(
                "complete"
                if state[
                    "manager_access_ready"
                ]
                else "action"
            ),
            detail=(
                f"{state['active_link_count']}/"
                f"{state['team_count']} current teams "
                "have active links; "
                f"{state['claimed_link_count']} "
                "claimed so far"
            ),
        )

        _render_check(
            "Player universe",
            state=(
                "complete"
                if state[
                    "player_universe_available"
                ]
                else "action"
            ),
            detail=(
                f"{state['player_count']:,} "
                "players loaded"
            ),
        )

        _render_check(
            "Draft format",
            state=(
                "complete"
                if state[
                    "draft_format_configured"
                ]
                else "action"
            ),
            detail=(
                f"{state['target_teams']} teams · "
                f"{state['rounds_total']} rounds · "
                f"{state['order_mode'] or 'unconfigured'} · "
                f"{state['expected_picks']} picks"
            ),
        )

        if state[
            "lottery_finalized"
        ]:
            lottery_state = (
                "complete"
            )

        elif (
            state[
                "membership_complete"
            ]
            and state[
                "rollcall_complete"
            ]
        ):
            lottery_state = (
                "action"
            )

        else:
            lottery_state = (
                "locked"
            )

        _render_check(
            "Draft lottery",
            state=lottery_state,
            detail=(
                "Finalized"
                if state[
                    "lottery_finalized"
                ]
                else (
                    "Started but not finalized"
                    if state[
                        "lottery_started"
                    ]
                    else "Not started"
                )
            ),
        )

        if state[
            "board_initialized"
        ]:
            board_state = (
                "complete"
            )

        elif state[
            "lottery_finalized"
        ]:
            board_state = (
                "action"
            )

        else:
            board_state = (
                "locked"
            )

        _render_check(
            "Draft Board initialized",
            state=board_state,
            detail=(
                f"{state['board_rows']}/"
                f"{state['expected_picks']} "
                "draft positions"
            ),
        )

        if state[
            "waiting_prior_managers"
        ]:
            st.caption(
                "Waiting on prior managers: "
                + ", ".join(
                    state[
                        "waiting_prior_managers"
                    ]
                )
            )

        if state[
            "readiness_blockers"
        ]:
            st.caption(
                "Draft initializer currently reports: "
                + " | ".join(
                    str(value)
                    for value
                    in state[
                        "readiness_blockers"
                    ]
                )
            )

    # ------------------------------------------------------------
    # DURING DRAFT
    # ------------------------------------------------------------

    with st.expander(
        (
            "Draft Checklist — "
            f"{state['draft_phase']}"
        ),
        expanded=False,
    ):
        _render_check(
            "Draft Board ready",
            state=(
                "complete"
                if state[
                    "board_initialized"
                ]
                else "locked"
            ),
            detail=(
                f"{state['board_rows']}/"
                f"{state['expected_picks']} "
                "draft positions"
            ),
        )

        started = (
            state[
                "draft_status"
            ]
            != "PREP"
        )

        _render_check(
            "Draft started",
            state=(
                "complete"
                if started
                else "locked"
            ),
            detail=(
                state[
                    "draft_status"
                ]
                or "UNKNOWN"
            ),
        )

        _render_check(
            "Draft completed",
            state=(
                "complete"
                if state[
                    "draft_complete"
                ]
                else (
                    "waiting"
                    if started
                    else "locked"
                )
            ),
            detail=(
                "Complete"
                if state[
                    "draft_complete"
                ]
                else "Waiting for final selection"
            ),
        )

        st.caption(
            "Clock, pause/resume, and current-pick "
            "adjustments remain under Draft Operations."
        )

    # ------------------------------------------------------------
    # POST-DRAFT
    # ------------------------------------------------------------

    with st.expander(
        (
            "Post-Draft Checklist — "
            f"{state['post_phase']}"
        ),
        expanded=False,
    ):
        _render_check(
            "Draft completion",
            state=(
                "complete"
                if state[
                    "draft_complete"
                ]
                else "locked"
            ),
            detail=(
                state[
                    "draft_status"
                ]
                or "UNKNOWN"
            ),
        )

        _render_check(
            "Post-draft closeout",
            state=(
                "action"
                if state[
                    "draft_complete"
                ]
                else "locked"
            ),
            detail=(
                "Validate final draft results "
                "and complete league handoff"
                if state[
                    "draft_complete"
                ]
                else "Available after draft completion"
            ),
        )
