from __future__ import annotations

import streamlit as st


def render_draft_lottery_tab(state) -> None:
    st.subheader("Draft Lottery")

    if state.commissioner_mode:
        st.info("Commissioner controls will appear here.")

    saved_order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
    team_count = len(getattr(state, "teams", {}) or {}) or len(saved_order)

    slot_map = dict(st.session_state.get("draft_order_slot_to_team", {}) or {})

    # Ignore stale session maps from a different league size.
    slot_keys = sorted(
        int(k) for k in slot_map.keys()
        if str(k).isdigit()
    )
    if slot_keys != list(range(1, team_count + 1)):
        slot_map = {}

    if not slot_map and saved_order and len(saved_order) == team_count:
        slot_map = {
            slot: str(saved_order[slot - 1] or "").strip()
            for slot in range(1, team_count + 1)
        }

    if slot_map and team_count > 0:
        rows = []
        for slot in range(1, team_count + 1):
            tk = str(slot_map.get(slot, "") or "").strip()
            team_name = state.teams[tk].name if tk in state.teams else ""
            rows.append((slot, team_name))

        html = """
        <style>
        .lottery-table {
            border-collapse: collapse;
            width: 100%;
        }
        .lottery-table th, .lottery-table td {
            border-bottom: 1px solid rgba(255,255,255,0.1);
            padding: 6px 8px;
        }
        .lottery-table th:first-child,
        .lottery-table td:first-child {
            text-align: center;
            width: 80px;
        }
        </style>
        <table class="lottery-table">
        <thead>
        <tr>
            <th>Pick #</th>
            <th>Team</th>
        </tr>
        </thead>
        <tbody>
        """

        for slot, team_name in rows:
            html += f"<tr><td>{slot}</td><td>{team_name}</td></tr>"

        html += "</tbody></table>"
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.caption("No draft order is currently available to display.")
