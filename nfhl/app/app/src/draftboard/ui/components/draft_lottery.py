from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

import streamlit as st

from draftboard.data.db import reveal_next_lottery_slot
from draftboard.state.runtime import get_season_year


def _pick_card_html(
    *,
    pick_number: int,
    team_name: str,
    owner_name: str,
    revealed: bool,
    is_commissioner: bool = False,
    can_reveal: bool = False,
    side: str = "",
) -> str:
    side_class = (
        "nfhl-lottery-tile-left"
        if side == "left"
        else "nfhl-lottery-tile-right"
        if side == "right"
        else ""
    )

    tile_class = (
        f"nfhl-lottery-tile {side_class} "
        "nfhl-lottery-tile-locked"
    )

    team_display = "LOCKED"

    detail_html = (
        '<div class="nfhl-lottery-owner-name">'
        "Reveal pending"
        "</div>"
    )

    if revealed:
        tile_class = (
            f"nfhl-lottery-tile {side_class} "
            "nfhl-lottery-tile-revealed"
        )

        team_display = html.escape(
            str(team_name or "")
        )

        owner = html.escape(
            str(owner_name or "")
        )

        detail_html = (
            '<div class="nfhl-lottery-owner-name">'
            f"{owner}"
            "</div>"
        )

    elif is_commissioner and can_reveal:
        detail_html = (
            '<div class="nfhl-lottery-owner-name">'
            "Next pick ready"
            "</div>"
            '<div class="nfhl-lottery-button-pocket">'
            "</div>"
        )

    elif is_commissioner:
        detail_html = (
            '<div class="nfhl-lottery-owner-name">'
            "Awaiting prior reveal"
            "</div>"
            '<div class="nfhl-lottery-reveal-disabled">'
            f"Reveal Pick #{int(pick_number)}"
            "</div>"
        )

    return (
        f'<div class="{tile_class}">'
        '<div class="nfhl-lottery-pick-num">'
        f"<span>{int(pick_number)}</span>"
        "</div>"
        '<div class="nfhl-lottery-team-panel">'
        '<div class="nfhl-lottery-team-name">'
        f"{team_display}"
        "</div>"
        f"{detail_html}"
        "</div>"
        "</div>"
    )


def _render_lottery_css() -> None:
    st.markdown(
        """
<style>
.nfhl-lottery-hero {
    display: flex;
    align-items: center;
    gap: 18px;
    background: linear-gradient(
        135deg,
        #07101f 0%,
        #002868 58%,
        #0d4f98 100%
    );
    border: 2px solid #4B92DB;
    border-radius: 18px;
    padding: 16px 20px;
    margin: 8px auto 18px auto;
    max-width: 860px;
    box-shadow: 0 0 18px rgba(75,146,219,0.28);
}

.nfhl-lottery-logo-fallback {
    width: 64px;
    height: 64px;
    border-radius: 12px;
    border: 2px solid #4B92DB;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 900;
    font-size: 1.22rem;
    color: #9fd1ff;
    background: #07101f;
    letter-spacing: 0.03em;
}

.nfhl-lottery-title-wrap {
    line-height: 1.0;
}

.nfhl-lottery-kicker {
    color: #9fd1ff;
    font-weight: 800;
    font-size: 0.88rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    margin-bottom: 6px;
}

.nfhl-lottery-title {
    color: #f7fbff;
    font-weight: 950;
    font-size: clamp(1.85rem, 4.5vw, 3.15rem);
    letter-spacing: -0.04em;
    text-transform: uppercase;
}

.nfhl-lottery-title-year {
    color: #9fd1ff;
}

.nfhl-lottery-tile {
    width: 100%;
    max-width: 480px;
    height: 114px;
    border: 2px solid #4B92DB;
    background: #f3f6f9;
    border-radius: 8px;
    margin-bottom: 11px;
    display: grid;
    grid-template-columns: 76px minmax(0, 1fr);
    align-items: stretch;
    box-shadow: 0 2px 8px rgba(0,0,0,0.24);
    overflow: hidden;
}

.nfhl-lottery-tile-left {
    margin-left: auto;
    margin-right: 0;
}

.nfhl-lottery-tile-right {
    margin-left: 0;
    margin-right: auto;
}

.nfhl-lottery-pick-num {
    background: #07101f;
    color: #9fd1ff;
    display: flex;
    align-items: center;
    justify-content: center;
    border-right: 2px solid #4B92DB;
}

.nfhl-lottery-pick-num span {
    font-size: 2.65rem;
    font-style: italic;
    font-weight: 950;
    line-height: 1;
    text-shadow: 0 0 8px rgba(159,209,255,0.30);
}

.nfhl-lottery-team-panel {
    padding: 11px 15px 10px 15px;
    min-width: 0;
}

.nfhl-lottery-team-name {
    color: #101820;
    font-size: clamp(1.18rem, 2.05vw, 1.72rem);
    font-weight: 950;
    line-height: 1.05;
    text-transform: uppercase;
    letter-spacing: -0.03em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.nfhl-lottery-owner-name {
    color: rgba(0,0,0,0.62);
    font-size: 0.95rem;
    margin-top: 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.nfhl-lottery-button-pocket {
    height: 36px;
    margin-top: 6px;
}

div[class*="st-key-lottery_reveal_pick_"] {
    position: relative;
    z-index: 20;
    margin-top: -56px;
    margin-bottom: 24px;
}

div[class*="st-key-lottery_reveal_pick_"] button {
    background: #0057a8 !important;
    color: #ffffff !important;
    border: 1px solid rgba(255,255,255,0.25) !important;
    border-radius: 999px !important;
    padding: 5px 12px !important;
    min-height: 30px !important;
    font-size: 0.82rem !important;
    font-weight: 900 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
    white-space: nowrap !important;
}

div[class*="st-key-lottery_reveal_pick_"]
button:hover:not(:disabled) {
    background: #4B92DB !important;
    color: #07101f !important;
}

.nfhl-lottery-reveal-disabled {
    display: inline-block;
    margin-top: 7px;
    padding: 5px 11px;
    border-radius: 999px;
    color: rgba(255,255,255,0.46);
    background: rgba(255,255,255,0.08);
    font-size: 0.82rem;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border: 1px solid rgba(255,255,255,0.10);
}

.nfhl-lottery-tile-locked {
    border-color: rgba(75,146,219,0.62);
    background: #07101f;
}

.nfhl-lottery-tile-locked
.nfhl-lottery-team-panel {
    background: linear-gradient(
        90deg,
        #07101f 0%,
        #002868 100%
    );
}

.nfhl-lottery-tile-locked
.nfhl-lottery-team-name {
    color: rgba(255,255,255,0.58);
    letter-spacing: 0.08em;
}

.nfhl-lottery-tile-locked
.nfhl-lottery-owner-name {
    color: rgba(255,255,255,0.58);
}

.nfhl-lottery-tile-revealed {
    border-color: #F4C430;
    background: #111111;
    box-shadow:
        0 0 14px rgba(244,196,48,0.30),
        0 2px 8px rgba(0,0,0,0.26);
}

.nfhl-lottery-tile-revealed
.nfhl-lottery-pick-num {
    background: #0B0B0B;
    color: #FFD84D;
    border-right: 2px solid #F4C430;
}

.nfhl-lottery-tile-revealed
.nfhl-lottery-team-panel {
    background: linear-gradient(
        90deg,
        #FFF8D6 0%,
        #F2D35B 100%
    );
}

.nfhl-lottery-tile-revealed
.nfhl-lottery-team-name {
    color: #111111;
}

.nfhl-lottery-tile-revealed
.nfhl-lottery-owner-name {
    color: #343434;
    font-weight: 700;
}

.nfhl-lottery-control-spacer {
    height: 38px;
    margin-bottom: 6px;
}

.nfhl-lottery-tech {
    font-family: monospace;
    font-size: 0.92rem;
    overflow-wrap: anywhere;
    opacity: 0.78;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def render_nfhl_lottery_board(
    *,
    lottery: dict[str, Any],
    is_commissioner: bool,
    key_prefix: str,
    actor: str = "commissioner_link",
    refresh_callback: Callable[[], None] | None = None,
    show_technical_details: bool = True,
) -> dict[str, Any]:
    """
    Shared NFFL-style NFHL lottery board.

    NFHL rules remain authoritative:
      * configured equal-odds teams
      * full order persisted before first reveal
      * reveal order configured count -> 1
      * PostgreSQL remains authoritative
    """

    _render_lottery_css()

    run = dict(
        lottery.get("run")
        or {}
    )

    picks = [
        dict(row)
        for row in (
            lottery.get("picks")
            or []
        )
    ]

    status = str(
        run.get("status")
        or ""
    ).upper()

    run_id = str(
        run.get("lottery_run_id")
        or ""
    )

    configured_team_count = int(
        run.get("configured_team_count")
        or 0
    )

    if configured_team_count <= 0:
        st.error(
            "NFHL lottery board has an invalid "
            "configured team count: "
            f"{configured_team_count}."
        )

        return {
            "status": status,
            "hidden_slots": [],
            "revealed_count": 0,
            "next_reveal_pick": None,
            "all_revealed": False,
        }

    pick_by_number = {
        int(row["slot_number"]): row
        for row in picks
    }

    expected_pick_numbers = set(
        range(
            1,
            configured_team_count + 1,
        )
    )

    if set(pick_by_number) != expected_pick_numbers:
        st.error(
            "NFHL lottery board requires persisted "
            "slots 1 through "
            f"{configured_team_count}."
        )

        return {
            "status": status,
            "hidden_slots": [],
            "revealed_count": 0,
            "next_reveal_pick": None,
            "all_revealed": False,
        }

    revealed_by_pick = {
        pick_number: (
            row.get("revealed_at_utc")
            is not None
        )
        for pick_number, row
        in pick_by_number.items()
    }

    hidden_slots = sorted(
        (
            pick_number
            for pick_number, revealed
            in revealed_by_pick.items()
            if not revealed
        ),
        reverse=True,
    )

    next_reveal_pick = (
        hidden_slots[0]
        if hidden_slots
        else None
    )

    revealed_count = (
        configured_team_count
        - len(hidden_slots)
    )

    all_revealed = not hidden_slots

    season_year = int(
        get_season_year()
    )

    st.markdown(
        (
            '<div class="nfhl-lottery-hero">'
            '<div class="nfhl-lottery-logo-fallback">'
            "NFHL"
            "</div>"
            '<div class="nfhl-lottery-title-wrap">'
            '<div class="nfhl-lottery-kicker">'
            "Official Draft Order Reveal"
            "</div>"
            '<div class="nfhl-lottery-title">'
            "Draft Lottery "
            '<span class="nfhl-lottery-title-year">'
            f"{season_year}"
            "</span>"
            "</div>"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    if st.button(
        "Refresh Lottery Results",
        key=f"{key_prefix}_refresh_results_{run_id}",
        help=(
            "Reload the latest revealed picks "
            "without leaving the Draft Lottery."
        ),
        use_container_width=False,
    ):
        if refresh_callback is not None:
            refresh_callback()
        else:
            st.cache_data.clear()
            st.rerun()

    left_count = (
        configured_team_count + 1
    ) // 2

    left_numbers = list(
        range(
            1,
            left_count + 1,
        )
    )

    right_numbers = list(
        range(
            left_count + 1,
            configured_team_count + 1,
        )
    )

    _, left_col, _, right_col, _ = (
        st.columns(
            [
                0.9,
                1.35,
                0.04,
                1.35,
                0.9,
            ],
            gap="small",
        )
    )

    def render_pick_slot(
        pick_number: int,
        side: str,
    ) -> None:
        row = pick_by_number[
            pick_number
        ]

        revealed = bool(
            revealed_by_pick[
                pick_number
            ]
        )

        team_name = (
            str(
                row.get("team_name")
                or ""
            )
            if revealed
            else ""
        )

        owner_name = (
            str(
                row.get("owner_name")
                or ""
            )
            if revealed
            else ""
        )

        can_reveal = bool(
            is_commissioner
            and pick_number == next_reveal_pick
            and status != "FINALIZED"
        )

        st.markdown(
            _pick_card_html(
                pick_number=pick_number,
                team_name=team_name,
                owner_name=owner_name,
                revealed=revealed,
                is_commissioner=is_commissioner,
                can_reveal=can_reveal,
                side=side,
            ),
            unsafe_allow_html=True,
        )

        if (
            is_commissioner
            and not revealed
        ):
            disabled = not can_reveal

            _, reveal_button_col = (
                st.columns(
                    [0.64, 0.36],
                    gap="small",
                )
            )

            with reveal_button_col:
                clicked = st.button(
                    f"Reveal Pick #{pick_number}",
                    key=(
                        "lottery_reveal_pick_"
                        f"{pick_number}"
                    ),
                    disabled=disabled,
                    use_container_width=True,
                )

            if clicked:
                try:
                    reveal_next_lottery_slot(
                        actor=actor,
                    )

                except Exception as exc:
                    st.error(
                        "Reveal failed for "
                        f"Pick #{pick_number}: {exc}"
                    )

                else:
                    st.success(
                        f"Pick #{pick_number} revealed."
                    )

                    if refresh_callback is not None:
                        refresh_callback()
                    else:
                        st.cache_data.clear()
                        st.rerun()

        elif (
            is_commissioner
            and revealed
        ):
            st.markdown(
                '<div class="nfhl-lottery-control-spacer"></div>',
                unsafe_allow_html=True,
            )

    with left_col:
        for pick_number in left_numbers:
            render_pick_slot(
                pick_number,
                "left",
            )

    with right_col:
        for pick_number in right_numbers:
            render_pick_slot(
                pick_number,
                "right",
            )

    if show_technical_details:
        with st.expander(
            "Lottery technical details",
            expanded=False,
        ):
            st.caption(
                f"Status: {status}"
            )

            st.caption(
                "Revealed: "
                f"{revealed_count}/{configured_team_count}"
            )

            st.caption(
                f"Reveal path: #{configured_team_count} → ... → #1."
            )

            st.caption(
                f"All {configured_team_count} teams began with equal odds. "
                "The complete lottery order was persisted "
                "before the first reveal; revealing a pick "
                "does not rerandomize the lottery."
            )

            if run_id:
                st.markdown(
                    (
                        '<div class="nfhl-lottery-tech">'
                        "Lottery run: "
                        f"{html.escape(run_id)}"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )

    return {
        "status": status,
        "hidden_slots": hidden_slots,
        "revealed_count": revealed_count,
        "next_reveal_pick": next_reveal_pick,
        "all_revealed": all_revealed,
    }
