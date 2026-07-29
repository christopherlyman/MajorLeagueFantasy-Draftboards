from __future__ import annotations

from collections.abc import (
    Callable,
    Mapping,
)
from typing import Any

import streamlit as st


ELIGIBLE_PICK_KINDS = frozenset(
    {
        "QO",
        "POACH",
        "FA",
    }
)



def _pick_kind(row: dict[str, Any]) -> str:
    pick_kind = str(
        row.get("pick_kind") or ""
    ).strip().upper()

    if pick_kind:
        return pick_kind

    display_label = str(
        row.get("display_label") or ""
    ).strip()

    if ":" not in display_label:
        return ""

    return display_label.rsplit(
        ":",
        1,
    )[-1].strip().upper()


def _eligible_players(
    display_team_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}

    for row in display_team_rows:
        if (
            str(
                row.get("roster_source") or ""
            ).upper()
            != "DRAFTED"
        ):
            continue

        pick_kind = _pick_kind(row)

        if pick_kind not in ELIGIBLE_PICK_KINDS:
            continue

        player_key = str(
            row.get("yahoo_player_key") or ""
        ).strip()

        if not player_key:
            continue

        normalized = dict(row)
        normalized["pick_kind"] = pick_kind

        players.setdefault(
            player_key,
            normalized,
        )

    return list(players.values())


def _position_label(row: dict[str, Any]) -> str:
    value = (
        row.get("position")
        or row.get("eligible_positions")
        or ""
    )

    if isinstance(value, (list, tuple)):
        return "/".join(
            str(item)
            for item in value
            if str(item).strip()
        )

    return (
        str(value)
        .replace("[", "")
        .replace("]", "")
        .replace('"', "")
        .replace("'", "")
        .replace(",", "/")
        .strip()
    )


def _player_label(row: dict[str, Any]) -> str:
    player_name = str(
        row.get("player_name")
        or row.get("yahoo_player_key")
        or "Unknown Player"
    )

    details = [
        str(row.get("nfl_team_abbr") or "FA"),
        _position_label(row),
        str(
            row.get("display_label")
            or row.get("pick_kind")
            or ""
        ),
    ]

    details = [
        detail
        for detail in details
        if detail
    ]

    return (
        f"{player_name} - "
        + " | ".join(details)
    )


def _available_player_keys(
    *,
    all_player_keys: list[str],
    selections_by_year: dict[int, str],
    current_year: int,
) -> list[str]:
    selected_elsewhere = {
        str(player_key)
        for years, player_key
        in selections_by_year.items()
        if (
            years != current_year
            and str(player_key)
        )
    }

    current_value = str(
        selections_by_year.get(
            current_year
        )
        or ""
    )

    return [
        player_key
        for player_key in all_player_keys
        if (
            player_key not in selected_elsewhere
            or player_key == current_value
        )
    ]


def render_post_draft_contract_tool(
    *,
    team_key: str,
    team_name: str,
    display_team_rows: list[dict[str, Any]],
    has_locked_ft: bool,
    acting_as: str,
    persisted_plan: Mapping[
        int,
        str,
    ] | None = None,
    persisted_revision: int = 0,
    save_plan: Callable[
        [Mapping[int, str]],
        int,
    ] | None = None,
) -> None:
    """
    Show one team's new contract choices.

    Saving is available after QO/FT reveal.
    """
    st.markdown("#### Save New Contracts")

    if save_plan is None:
        st.caption(
            "Commissioner preview only. Saving "
            "becomes available after QO/FT "
            "selections are revealed."
        )
    else:
        st.caption(
            "Choose the new contracts for this "
            "team. Saved choices remain editable "
            "until the commissioner finalizes "
            "and reveals them."
        )

    actual_players = _eligible_players(
        display_team_rows
    )

    eligible_players = actual_players

    if actual_players:
        st.success(
            f"{len(actual_players)} eligible drafted "
            "player(s) found."
        )
    else:
        st.info(
            "No eligible drafted players currently "
            f"exist for {team_name}. The dropdowns "
            "will populate as this team makes "
            "eligible draft selections."
        )

    if has_locked_ft:
        st.caption(
            "Locked Franchise Tag detected: "
            "the 2-year contract slot is unavailable."
        )
    else:
        st.caption(
            "No locked Franchise Tag detected: "
            "the 2-year contract slot is available."
        )

    contract_years = (
        (4, 3)
        if has_locked_ft
        else (4, 3, 2)
    )

    player_by_key = {
        str(row["yahoo_player_key"]): row
        for row in eligible_players
    }

    all_player_keys = list(
        player_by_key.keys()
    )

    saved_plan_key = (
        f"contract_tool_plan_{team_key}"
    )

    revision_key = (
        f"contract_tool_revision_{team_key}"
    )

    widget_keys = {
        years: (
            f"contract_tool_select_"
            f"{team_key}_{years}"
        )
        for years in contract_years
    }

    database_plan = {
        int(years): str(player_key)
        for years, player_key
        in (persisted_plan or {}).items()
        if (
            int(years) in contract_years
            and str(player_key)
        )
    }

    database_revision = int(
        persisted_revision
    )

    session_revision = int(
        st.session_state.get(
            revision_key,
            -1,
        )
    )

    if (
        save_plan is not None
        and session_revision
        != database_revision
    ):
        st.session_state[
            saved_plan_key
        ] = database_plan

        st.session_state[
            revision_key
        ] = database_revision

        for years, widget_key in (
            widget_keys.items()
        ):
            st.session_state[
                widget_key
            ] = database_plan.get(
                years,
                "",
            )

    saved_plan = dict(
        st.session_state.get(
            saved_plan_key,
            database_plan,
        )
    )

    current_widget_values: dict[
        int,
        str,
    ] = {}

    for years in contract_years:
        widget_key = widget_keys[years]

        current_value = str(
            st.session_state.get(
                widget_key,
                saved_plan.get(years) or "",
            )
            or ""
        )

        if current_value not in player_by_key:
            current_value = ""

            if widget_key in st.session_state:
                st.session_state[
                    widget_key
                ] = ""

        current_widget_values[
            years
        ] = current_value

    selected_plan: dict[int, str] = {}

    columns = st.columns(
        len(contract_years)
    )

    for column, years in zip(
        columns,
        contract_years,
    ):
        with column:
            st.markdown(
                f"**{years}-Year Contract**"
            )

            widget_key = widget_keys[
                years
            ]

            available_player_keys = (
                _available_player_keys(
                    all_player_keys=(
                        all_player_keys
                    ),
                    selections_by_year=(
                        current_widget_values
                    ),
                    current_year=years,
                )
            )

            slot_option_keys = [
                "",
                *available_player_keys,
            ]

            current_value = (
                current_widget_values[
                    years
                ]
            )

            selected_plan[years] = st.selectbox(
                "Eligible drafted player",
                options=slot_option_keys,
                index=slot_option_keys.index(
                    current_value
                ),
                format_func=lambda player_key: (
                    "- Select player -"
                    if not player_key
                    else _player_label(
                        player_by_key[player_key]
                    )
                ),
                key=widget_key,
                label_visibility="collapsed",
                disabled=not bool(
                    player_by_key
                ),
            )

    selected_players = [
        player_key
        for player_key
        in selected_plan.values()
        if player_key
    ]

    has_duplicate = (
        len(selected_players)
        != len(set(selected_players))
    )

    if has_duplicate:
        st.error(
            "A player can only receive one "
            "new contract."
        )

    if st.button(
        "Save New Contracts",
        type="primary",
        disabled=(
            has_duplicate
            or save_plan is None
        ),
        key=f"contract_tool_save_{team_key}",
    ):
        normalized_plan = {
            years: player_key
            for years, player_key
            in selected_plan.items()
            if player_key
        }

        if save_plan is None:
            st.error(
                "Saving is not available yet."
            )
        else:
            try:
                revision = int(
                    save_plan(normalized_plan)
                )
            except Exception as exc:
                st.error(
                    "New contracts were not saved: "
                    f"{exc}"
                )
            else:
                st.session_state[
                    saved_plan_key
                ] = normalized_plan

                st.session_state[
                    revision_key
                ] = revision

                saved_plan = normalized_plan

                st.success(
                    "New contracts saved as "
                    f"revision {revision}."
                )

    if saved_plan:
        st.markdown(
            "##### Current Saved Plan"
        )

        for years in contract_years:
            player_key = str(
                saved_plan.get(years) or ""
            )

            if not player_key:
                continue

            player = player_by_key.get(
                player_key
            )

            label = (
                _player_label(player)
                if player
                else player_key
            )

            st.write(
                f"**{years} years:** {label}"
            )

        revision = int(
            st.session_state.get(
                revision_key,
                0,
            )
        )

        st.caption(
            f"Saved revision {revision} | "
            f"Acting as {acting_as}"
        )
    else:
        st.caption(
            "No new contracts have been selected."
        )
