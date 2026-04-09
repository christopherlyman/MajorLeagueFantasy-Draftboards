from __future__ import annotations

import streamlit as st


def render_draft_lottery_tab(state) -> None:
    st.subheader("Draft Lottery")

    if state.commissioner_mode:
        st.info("Commissioner controls will appear here.")

    slot_map = dict(st.session_state.get("draft_order_slot_to_team", {}) or {})

    if not slot_map:
        saved_order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
        if len(saved_order) == 16:
            slot_map = {slot: str(saved_order[slot - 1] or "").strip() for slot in range(1, 17)}

    if slot_map:
        rows = []
        for slot in range(1, 17):
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