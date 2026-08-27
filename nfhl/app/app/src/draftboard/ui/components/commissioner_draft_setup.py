from __future__ import annotations

import pandas as pd
import streamlit as st

from draftboard.data.db import (
    finalize_lottery,
    get_draft_initialization_readiness,
    get_lottery_state,
    initialize_draft_from_lottery,
    initialize_lottery,
    reveal_next_lottery_slot,
    void_lottery,
)

from draftboard.data.season_team_slots import (
    get_season_team_slots,
)


def _commissioner(
    gateway_context: dict[str, object],
) -> bool:
    return (
        str(
            gateway_context.get("role")
            or "public"
        )
        .strip()
        .lower()
        == "commissioner"
    )


def _refresh(
    *,
    force_open_key: str | None = None,
) -> None:
    if force_open_key:
        st.session_state[
            force_open_key
        ] = True

    st.cache_data.clear()
    st.rerun()


def render_draft_setup_panel(
    *,
    gateway_context: dict[str, object],
    current_teams: int,
    target_teams: int,
) -> None:
    if not _commissioner(
        gateway_context
    ):
        return

    force_open_draft_setup = bool(
        st.session_state.pop(
            "nfhl_force_open_draft_setup",
            False,
        )
    )

    keep_draft_setup_open = bool(
        force_open_draft_setup
        or st.session_state.get(
            "nfhl_confirm_finalize_build",
            False,
        )
        or st.session_state.get(
            "nfhl_draft_setup_notice",
            None,
        )
    )

    with st.expander(
        "Draft Setup",
        expanded=keep_draft_setup_open,
    ):
        notice = (
            st.session_state.pop(
                "nfhl_draft_setup_notice",
                None,
            )
        )

        if notice:
            st.success(
                str(notice)
            )

        try:
            readiness = (
                get_draft_initialization_readiness()
            )

        except Exception as exc:
            st.error(
                "Unable to load Draft Setup: "
                f"{exc}"
            )
            return

        slots = (
            get_season_team_slots()
        )

        mapped_slots = sum(
            1
            for row in slots
            if row.get(
                "current_team_key"
            )
        )

        expected_picks = int(
            readiness.get(
                "expected_draft_pick_count"
            )
            or 0
        )

        board_count = int(
            readiness.get(
                "draft_pick_count"
            )
            or 0
        )

        rounds = int(
            readiness.get(
                "rounds_total"
            )
            or 0
        )

        order_mode = str(
            readiness.get(
                "draft_order_mode"
            )
            or "Not Set"
        )

        c1, c2, c3, c4 = st.columns(
            4
        )

        c1.metric(
            "Teams",
            f"{current_teams}/{target_teams}",
        )

        c2.metric(
            "Rounds",
            rounds,
        )

        c3.metric(
            "Order",
            order_mode.title(),
        )

        c4.metric(
            "Draft Board",
            f"{board_count}/{expected_picks}",
        )

        st.markdown(
            "#### Draft Lottery"
        )

        membership_ready = (
            current_teams
            == target_teams
        )

        rollcall_ready = (
            mapped_slots
            == target_teams
        )

        lottery_ready = (
            membership_ready
            and rollcall_ready
        )

        c1, c2, c3 = st.columns(
            3
        )

        c1.metric(
            "Yahoo Membership",
            f"{current_teams}/{target_teams}",
        )

        c2.metric(
            "Roll Call",
            f"{mapped_slots}/{target_teams}",
        )

        equal_odds = (
            100.0 / target_teams
            if target_teams
            else 0.0
        )

        c3.metric(
            "Chance Per Team",
            f"{equal_odds:.2f}%",
        )

        if not lottery_ready:
            blockers = []

            if not membership_ready:
                blockers.append(
                    "Yahoo membership "
                    f"{current_teams}/{target_teams}"
                )

            if not rollcall_ready:
                blockers.append(
                    "roll call "
                    f"{mapped_slots}/{target_teams}"
                )

            st.info(
                "Draft Lottery is locked until "
                "league formation is complete. "
                + " | ".join(blockers)
            )

        lottery = (
            get_lottery_state()
        )

        # --------------------------------------------------------
        # No lottery yet
        # --------------------------------------------------------

        if lottery is None:
            st.write(
                "**Lottery status:** Not started"
            )

            if st.button(
                "Initialize Draft Lottery",
                disabled=not lottery_ready,
                type="primary",
                use_container_width=True,
                key="nfhl_commissioner_initialize_lottery",
            ):
                try:
                    initialize_lottery(
                        expected_team_count=target_teams,
                        actor="commissioner_link",
                    )

                except Exception as exc:
                    st.error(
                        "Could not initialize lottery: "
                        f"{exc}"
                    )

                else:
                    _refresh(force_open_key="nfhl_force_open_draft_setup")

            st.divider()

            _render_board_state(
                readiness=readiness,
            )

            return

        # --------------------------------------------------------
        # Existing lottery
        # --------------------------------------------------------

        run = lottery[
            "run"
        ]

        picks = lottery[
            "picks"
        ]

        status = str(
            run.get(
                "status"
            )
            or ""
        ).upper()

        configured_team_count = int(
            run.get(
                "configured_team_count"
            )
            or target_teams
        )

        revealed_count = int(
            lottery.get(
                "revealed_count"
            )
            or 0
        )

        hidden_slots = sorted(
            (
                int(pick.get("slot_number") or 0)
                for pick in picks
                if pick.get("revealed_at_utc") is None
            ),
            reverse=True,
        )

        if (
            not hidden_slots
            and status != "FINALIZED"
        ):
            st.info(
                "All lottery positions have been revealed. "
                "The order is ready for final confirmation."
            )

            confirm = st.checkbox(
                "I confirm the fully revealed lottery order "
                "and want to build the production Draft Board "
                "from it.",
                key="nfhl_confirm_finalize_build",
            )

            if st.button(
                "Finalize Lottery & Build Draft Board",
                type="primary",
                use_container_width=True,
                disabled=not confirm,
                key="nfhl_finalize_build",
            ):
                try:
                    finalize_lottery(
                        actor="commissioner_link",
                    )

                except Exception as exc:
                    st.error(
                        "Lottery finalization failed: "
                        f"{exc}"
                    )
                    return

                try:
                    result = (
                        initialize_draft_from_lottery(
                            actor="commissioner_link",
                        )
                    )

                except Exception as exc:
                    st.error(
                        "The lottery was finalized successfully, "
                        "but Draft Board construction failed. "
                        "The finalized lottery is preserved and "
                        "the board can be retried below. "
                        f"Error: {exc}"
                    )
                    return

                if (
                    str(
                        result.get(
                            "result_status"
                        )
                        or ""
                    ).upper()
                    != "INITIALIZED"
                ):
                    st.error(
                        "The lottery was finalized, but "
                        "Draft Board initialization returned "
                        "an unexpected result."
                    )
                    return

                st.session_state[
                    "nfhl_draft_setup_notice"
                ] = (
                    "Draft Lottery finalized and production "
                    "Draft Board built successfully: "
                    f"{result['draft_pick_count']} picks."
                )

                _refresh(force_open_key="nfhl_force_open_draft_setup")

        elif status == "FINALIZED":
            st.success(
                "Draft Lottery finalized. "
                "The persisted order is locked."
            )
        st.divider()

        # Re-fetch because lottery may have been finalized
        # during a previous render.
        try:
            readiness = (
                get_draft_initialization_readiness()
            )

        except Exception as exc:
            st.error(
                "Unable to reload Draft Board readiness: "
                f"{exc}"
            )
            return

        _render_board_state(
            readiness=readiness,
        )


def _render_board_state(
    *,
    readiness: dict,
) -> None:
    st.markdown(
        "#### Draft Board"
    )

    expected_picks = int(
        readiness.get(
            "expected_draft_pick_count"
        )
        or 0
    )

    board_count = int(
        readiness.get(
            "draft_pick_count"
        )
        or 0
    )

    if (
        expected_picks > 0
        and board_count
        == expected_picks
    ):
        st.success(
            "Production Draft Board is initialized: "
            f"{board_count}/{expected_picks} picks. "
            "The draft clock remains stopped."
        )

        st.info(
            "Next action: visually verify the final "
            "Draft Board and team order. Start the draft "
            "only from Draft Operations."
        )

        return

    if bool(
        readiness.get(
            "ready"
        )
    ):
        st.warning(
            "All Draft Board prerequisites are satisfied, "
            "but the board has not yet been built."
        )

        confirm = st.checkbox(
            "Build the production Draft Board from "
            "the finalized lottery.",
            key="nfhl_retry_build_confirm",
        )

        if st.button(
            "Build NFHL Draft Board",
            type="primary",
            use_container_width=True,
            disabled=not confirm,
            key="nfhl_retry_build_board",
        ):
            try:
                result = (
                    initialize_draft_from_lottery(
                        actor="commissioner_link",
                    )
                )

            except Exception as exc:
                st.error(
                    "Draft Board construction failed: "
                    f"{exc}"
                )
                return

            if (
                str(
                    result.get(
                        "result_status"
                    )
                    or ""
                ).upper()
                != "INITIALIZED"
            ):
                st.error(
                    "Draft Board initializer returned "
                    "an unexpected result."
                )
                return

            st.session_state[
                "nfhl_draft_setup_notice"
            ] = (
                "Production Draft Board built: "
                f"{result['draft_pick_count']} picks."
            )

            _refresh(force_open_key="nfhl_force_open_draft_setup")

        return

    st.info(
        "Draft Board construction is locked."
    )

    for blocker in (
        readiness.get(
            "blockers"
        )
        or []
    ):
        st.markdown(
            f"- {blocker}"
        )


def render_danger_zone(
    *,
    gateway_context: dict[str, object],
) -> None:
    if not _commissioner(
        gateway_context
    ):
        return

    with st.expander(
        "Danger Zone",
        expanded=bool(
            st.session_state.pop(
                "nfhl_force_open_danger_zone",
                False,
            )
        ),
    ):
        st.warning(
            "Destructive commissioner actions live here. "
            "Normal offseason and draft administration "
            "should not require this section."
        )

        st.markdown(
            "#### Void / Reset Draft Lottery"
        )

        lottery = (
            get_lottery_state()
        )

        if lottery is None:
            st.info(
                "There is no active Draft Lottery to void."
            )
            return

        status = str(
            lottery[
                "run"
            ].get(
                "status"
            )
            or ""
        ).upper()

        st.write(
            "**Current lottery status:** "
            f"{status}"
        )

        try:
            readiness = (
                get_draft_initialization_readiness()
            )

            board_count = int(
                readiness.get(
                    "draft_pick_count"
                )
                or 0
            )

        except Exception as exc:
            st.error(
                "Unable to determine Draft Board state: "
                f"{exc}"
            )
            return

        if board_count:
            st.error(
                "A production Draft Board already exists. "
                "Lottery reset is blocked after board "
                "initialization."
            )

        st.caption(
            "Voiding preserves this lottery in history as VOID "
            "and allows a new lottery to be created, subject to "
            "database safety rules."
        )

        reason = st.text_input(
            "Reason for voiding lottery",
            key="nfhl_danger_void_reason",
        )

        confirm = st.checkbox(
            "I understand this invalidates the current "
            "Draft Lottery.",
            key="nfhl_danger_void_confirm",
        )

        ready = bool(
            str(
                reason
            ).strip()
            and confirm
            and not board_count
        )

        if st.button(
            "Void Current Draft Lottery",
            disabled=not ready,
            key="nfhl_danger_void_lottery",
        ):
            try:
                void_lottery(
                    actor="commissioner_link",
                    reason=str(
                        reason
                    ).strip(),
                )

            except Exception as exc:
                st.error(
                    "Could not void Draft Lottery: "
                    f"{exc}"
                )

            else:
                _refresh(force_open_key="nfhl_force_open_danger_zone")
