from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

import psycopg
import streamlit as st


def _split_name(full: str) -> tuple[str, str]:
    parts = str(full or "").strip().split()
    if len(parts) <= 1:
        return str(full or "").strip(), ""
    return parts[0], " ".join(parts[1:])


def _cell_label(row: dict[str, Any]) -> str:
    return f"{row['round_label']}.{int(row['slot_number'])}"


def _baseball_position(raw: Any) -> tuple[str, str]:
    """Return CSS position class and compact display label."""
    value = str(raw or "").upper().strip()

    # Be tolerant of future Yahoo variants such as SP/RP or LF/CF/RF.
    token = value.replace("/", ",").split(",", 1)[0].strip()

    if token in {"P", "SP", "RP"}:
        return "p", token
    if token in {"OF", "LF", "CF", "RF"}:
        return "of", token
    if token == "C":
        return "c", "C"
    if token == "1B":
        return "1b", "1B"
    if token == "2B":
        return "2b", "2B"
    if token == "3B":
        return "3b", "3B"
    if token == "SS":
        return "ss", "SS"
    if token in {"UTIL", "DH", "MI", "CI"}:
        return "util", token

    return "unknown", ""


def _fetch_board_rows(dsn: str, draft_key: str) -> list[dict[str, Any]]:
    """Load the complete MLF board from canonical relational state."""
    sql = """
    SELECT
        dp.draft_key,
        dp.pick_id,
        dp.round_number,
        dp.slot_number,
        dp.round_label,
        dp.pick_type,

        dp.column_team_key,
        COALESCE(column_team.team_name, dp.column_team_key)
            AS column_team_name,

        dp.current_owner_team_key,
        COALESCE(owner_team.team_name, dp.current_owner_team_key)
            AS current_owner_team_name,

        dp.traded_flag,
        dp.ownership_note,

        COALESCE(ds.yahoo_player_key, dka.yahoo_player_key)
            AS yahoo_player_key,

        pu.player_name
            AS selected_player_name,

        CASE
            WHEN ds.yahoo_player_key IS NOT NULL
                THEN COALESCE(ds.pick_kind, 'STANDARD')
            WHEN UPPER(COALESCE(dka.keeper_kind, '')) = 'CONTRACT'
                THEN 'CONTRACT_PLACEHOLDER'
            WHEN UPPER(COALESCE(dka.keeper_kind, '')) = 'PT'
                THEN 'PT_PLACEHOLDER'
            WHEN dka.keeper_kind IS NOT NULL
                THEN UPPER(dka.keeper_kind)
            ELSE NULL
        END AS pick_kind,

        ds.selected_at_utc,

        pu.primary_position
            AS selected_primary_position,

        dka.keeper_kind
            AS placeholder_source,

        CASE
            WHEN UPPER(COALESCE(dka.keeper_kind, '')) = 'CONTRACT'
                THEN contract_effective.years_remaining
            ELSE NULL
        END AS contract_years_remaining

    FROM mlf.draft_pick AS dp

    JOIN mlf.draft AS d
      ON d.draft_key = dp.draft_key

    LEFT JOIN mlf.draft_selection AS ds
      ON ds.draft_key = dp.draft_key
     AND ds.pick_id = dp.pick_id

    LEFT JOIN mlf.draft_keeper_assignment AS dka
      ON dka.draft_key = dp.draft_key
     AND dka.pick_id = dp.pick_id

    LEFT JOIN mlf.team AS column_team
      ON column_team.league_key = d.league_key
     AND column_team.season_year = d.season_year
     AND column_team.team_key = dp.column_team_key

    LEFT JOIN mlf.team AS owner_team
      ON owner_team.league_key = d.league_key
     AND owner_team.season_year = d.season_year
     AND owner_team.team_key = dp.current_owner_team_key

    LEFT JOIN mlf.player_universe AS pu
      ON pu.league_key = d.league_key
     AND pu.season_year = d.season_year
     AND pu.yahoo_player_key =
         COALESCE(ds.yahoo_player_key, dka.yahoo_player_key)

    LEFT JOIN mlf.v_contract_effective AS contract_effective
      ON contract_effective.league_key = d.league_key
     AND contract_effective.season_year = d.season_year
     AND contract_effective.yahoo_player_key =
         COALESCE(ds.yahoo_player_key, dka.yahoo_player_key)

    WHERE dp.draft_key = %s

    ORDER BY
        dp.round_number,
        dp.slot_number
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (draft_key,))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def render_postgres_board_html(
    dsn: str,
    draft_key: str,
    min_col_px: int = 132,
    cell_h_px: int = 96,
) -> None:
    rows = _fetch_board_rows(dsn, draft_key)

    if not rows:
        st.warning(f"No draft board rows found in canonical MLF relational state for draft_key={draft_key}.")
        return

    first_round = min(int(r["round_number"]) for r in rows)
    headers = [
        str(r["column_team_name"] or "")
        for r in sorted(
            [r for r in rows if int(r["round_number"]) == first_round],
            key=lambda x: int(x["slot_number"]),
        )
    ]

    board_min_px = (
        len(headers) * min_col_px
        + max(0, len(headers) - 1) * 4
    )

    by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_round[int(r["round_number"])].append(r)

    grid_rows = [
        sorted(by_round[rnd], key=lambda x: int(x["slot_number"]))
        for rnd in sorted(by_round)
    ]

    st.markdown(
        """
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <style>
          .db-wrap {{
            color: #111 !important;
            width: 100%;
            max-width: 100%;
            overflow-x: auto;
            overflow-y: visible;
            padding-bottom: 8px;
          }}

          .db-header {{
            display: grid;
            grid-template-columns: repeat({len(headers)}, minmax({min_col_px}px, 1fr));
            min-width: {board_min_px}px;
            gap: 4px;
            position: sticky;
            top: 3.25rem;
            z-index: 20;
            background: linear-gradient(135deg, #0A0A08 0%, #34302B 62%, #5c1717 100%);
            padding: 10px 0 12px 0;
            border-bottom: 3px solid #D50A0A;
          }}

          .db-hcell {{
            background: #34302B;
            color: #F2F0EA !important;
            border: 2px solid #D50A0A;
            border-radius: 10px;
            padding: 8px 10px;
            box-sizing: border-box;
            font-weight: 950;
            font-size: clamp(0.82rem, 0.95vw, 1.00rem);
            line-height: clamp(1.05rem, 1.4vw, 1.22rem);
            height: 84px;
            text-align: center;
            text-transform: uppercase;
            letter-spacing: -0.03em;
            text-shadow: 0 0 8px rgba(255, 121, 0, 0.24);
            box-shadow: inset 0 -4px 0 #D50A0A, 0 2px 8px rgba(0,0,0,0.24);
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
          }}

          .db-grid {{
            display: grid;
            grid-template-columns: repeat({len(headers)}, minmax({min_col_px}px, 1fr));
            min-width: {board_min_px}px;
            gap: 4px;
            align-items: stretch;
            padding: 8px 0 12px 0;
          }}

          .db-cell {{
            border: 1.5px solid rgba(0,0,0,0.18);
            border-radius: 14px;
            height: {cell_h_px}px;
            padding: 8px 8px;
            position: relative;
            overflow: hidden;
            box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            background: #F8FAFC;
            color: #0F172A !important;
          }}

          .db-cell-selected {{
            background: #E0F2FE;
          }}

          .db-cell-qo-placeholder {{
            background: #F1F5F9;
            border-style: dashed;
            border-color: #94A3B8;
          }}

          .db-pos-p {{ background: #DBEAFE; }}
          .db-pos-of {{ background: #DCFCE7; }}
          .db-pos-c {{ background: #FEF3C7; }}
          .db-pos-1b {{ background: #FEE2E2; }}
          .db-pos-2b {{ background: #CCFBF1; }}
          .db-pos-3b {{ background: #FFEDD5; }}
          .db-pos-ss {{ background: #F3E8FF; }}
          .db-pos-util {{ background: #E5E7EB; }}
          .db-pos-unknown {{ background: #E5E7EB; }}

          .db-tl {{
            position: absolute;
            top: 6px;
            left: 8px;
            font-size: clamp(0.64rem, 1.0vw, 0.78rem);
            opacity: 0.92;
            font-weight: 900;
            white-space: nowrap;
          }}

          .db-tr {{
            position: absolute;
            top: 6px;
            right: 8px;
            font-size: clamp(0.64rem, 1.0vw, 0.78rem);
            opacity: 0.92;
            font-weight: 900;
            white-space: nowrap;
          }}

          .db-owner {{
            position: absolute;
            bottom: 6px;
            left: 8px;
            font-size: clamp(0.62rem, 0.95vw, 0.76rem);
            font-weight: 800;
            opacity: 0.92;
            white-space: nowrap;
          }}

          .db-badge {{
            position: absolute;
            bottom: 6px;
            left: 8px;
            font-size: clamp(0.70rem, 1.0vw, 0.85rem);
            font-weight: 950;
            opacity: 0.95;
            white-space: nowrap;
          }}

          .db-center {{
            height: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding-top: 12px;
          }}

          .db-first {{
            font-size: clamp(0.74rem, 0.90vw, 0.90rem);
            font-weight: 800;
            line-height: 1.05em;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }}

          .db-last {{
            font-size: clamp(0.90rem, 1.10vw, 1.10rem);
            font-weight: 950;
            line-height: 1.10em;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }}

          /* MLF_HIGH_CONTRAST_BOARD_PALETTE_START */

          .db-cell-qo-placeholder {{
            background: #64748B !important;
            border: 2px solid #1E293B !important;
            color: #F8FAFC !important;
            box-shadow: inset 0 0 0 2px rgba(255,255,255,0.10), 0 2px 4px rgba(0,0,0,0.22) !important;
          }}

          .db-cell-qo-placeholder .db-first,
          .db-cell-qo-placeholder .db-last,
          .db-cell-qo-placeholder .db-tr,
          .db-cell-qo-placeholder .db-tl,
          .db-cell-qo-placeholder .db-owner,
          .db-cell-qo-placeholder .db-badge {{
            color: #F8FAFC !important;
            text-shadow: 0 1px 2px rgba(0,0,0,0.58) !important;
          }}

          /* Baseball position palette: high contrast and distinct */
          .db-pos-p {{
            background: #1D4ED8 !important;
            color: #FFFFFF !important;
            border-color: #172554 !important;
          }}

          .db-pos-of {{
            background: #166534 !important;
            color: #FFFFFF !important;
            border-color: #052E16 !important;
          }}

          .db-pos-c {{
            background: #B45309 !important;
            color: #FFFFFF !important;
            border-color: #451A03 !important;
          }}

          .db-pos-1b {{
            background: #B91C1C !important;
            color: #FFFFFF !important;
            border-color: #450A0A !important;
          }}

          .db-pos-2b {{
            background: #0F766E !important;
            color: #FFFFFF !important;
            border-color: #042F2E !important;
          }}

          .db-pos-3b {{
            background: #C2410C !important;
            color: #FFFFFF !important;
            border-color: #431407 !important;
          }}

          .db-pos-ss {{
            background: #6D28D9 !important;
            color: #FFFFFF !important;
            border-color: #2E1065 !important;
          }}

          .db-pos-util {{
            background: #475569 !important;
            color: #FFFFFF !important;
            border-color: #0F172A !important;
          }}

          .db-pos-unknown {{
            background: #1F2937 !important;
            color: #FFFFFF !important;
            border-color: #030712 !important;
          }}

          .db-cell-selected .db-first,
          .db-cell-selected .db-last,
          .db-cell-selected .db-tr,
          .db-cell-selected .db-tl,
          .db-cell-selected .db-owner,
          .db-cell-selected .db-badge {{
            color: #FFFFFF !important;
            text-shadow: 0 1px 2px rgba(0,0,0,0.60) !important;
          }}

          .db-cell-selected .db-badge,
          .db-cell-qo-placeholder .db-badge {{
            background: rgba(0,0,0,0.34) !important;
            border-radius: 8px !important;
            padding: 2px 7px !important;
            letter-spacing: 0.03em !important;
          }}

          .db-cell-selected .db-tl,
          .db-cell-qo-placeholder .db-tl {{
            background: rgba(0,0,0,0.30) !important;
            border-radius: 8px !important;
            padding: 2px 6px !important;
            letter-spacing: 0.04em !important;
          }}

          .db-cell-selected,
          .db-cell-qo-placeholder {{
            border-width: 2px !important;
          }}

          /* MLF_HIGH_CONTRAST_BOARD_PALETTE_END */


          /* MLF_FLAT_LABEL_OVERRIDE_START */

          .db-wrap .db-cell .db-tl,
          .db-wrap .db-cell .db-badge,
          .db-wrap .db-cell-selected .db-tl,
          .db-wrap .db-cell-selected .db-badge,
          .db-wrap .db-cell-qo-placeholder .db-tl,
          .db-wrap .db-cell-qo-placeholder .db-badge {{
            background: none !important;
            background-color: transparent !important;
            border: 0 !important;
            outline: 0 !important;
            box-shadow: none !important;
            border-radius: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            filter: none !important;
          }}

          .db-wrap .db-cell .db-tl,
          .db-wrap .db-cell-selected .db-tl,
          .db-wrap .db-cell-qo-placeholder .db-tl {{
            left: 8px !important;
            top: 6px !important;
          }}

          .db-wrap .db-cell .db-badge,
          .db-wrap .db-cell-selected .db-badge,
          .db-wrap .db-cell-qo-placeholder .db-badge {{
            left: 8px !important;
            bottom: 6px !important;
          }}

          /* MLF_FLAT_LABEL_OVERRIDE_END */

        </style>
        """,
        unsafe_allow_html=True,
    )

    html = '<div class="db-wrap"><div class="db-header">'
    for h in headers:
        hh = escape(h)
        html += f'<div class="db-hcell" title="{hh}">{hh}</div>'
    html += '</div><div class="db-grid">'

    for round_rows in grid_rows:
        for row in round_rows:
            label = escape(_cell_label(row))
            traded = bool(row.get("traded_flag"))
            selected_name = str(row.get("selected_player_name") or "").strip()
            pick_kind = str(row.get("pick_kind") or "").strip()
            current_owner = escape(str(row.get("current_owner_team_name") or row.get("current_owner_team_key") or ""))
            ownership_note = escape(str(row.get("ownership_note") or ""))

            tl_html = '<div class="db-tl">TRADE</div>' if traded else ""
            owner_label = ownership_note or current_owner
            owner_html = f'<div class="db-owner">{owner_label}</div>' if traded else ""

            if selected_name:
                first, last = _split_name(selected_name)

                pos_key, pos_label = _baseball_position(
                    row.get("selected_primary_position")
                )

                if pos_label and traded:
                    cell_tl_html = f'<div class="db-tl">TRADE · {escape(pos_label)}</div>'
                elif pos_label:
                    cell_tl_html = f'<div class="db-tl">{escape(pos_label)}</div>'
                else:
                    cell_tl_html = tl_html

                is_qo_placeholder = pick_kind == "QO_PLACEHOLDER"
                is_ft_placeholder = pick_kind == "FT_PLACEHOLDER"
                is_pt_placeholder = pick_kind == "PT_PLACEHOLDER"
                is_contract_placeholder = pick_kind == "CONTRACT_PLACEHOLDER"

                if is_qo_placeholder:
                    display_badge = "QO"
                    cell_class = f"db-cell db-cell-selected db-pos-{pos_key}"
                elif is_ft_placeholder:
                    display_badge = "FT"
                    cell_class = f"db-cell db-cell-selected db-pos-{pos_key}"
                elif is_pt_placeholder:
                    display_badge = "PT"
                    cell_class = f"db-cell db-cell-selected db-pos-{pos_key}"
                elif is_contract_placeholder:
                    yrs = row.get("contract_years_remaining")
                    display_badge = f"C{int(yrs)}" if yrs is not None else "C"
                    cell_class = f"db-cell db-cell-selected db-pos-{pos_key}"
                else:
                    display_badge = pick_kind
                    cell_class = f"db-cell db-cell-selected db-pos-{pos_key}"

                badge = escape(display_badge) if display_badge else ""
                badge_html = f'<div class="db-badge">{badge}</div>' if badge else ""

                html += (
                    f'<div class="{cell_class}">'
                    f'{cell_tl_html}'
                    f'<div class="db-tr">{label}</div>'
                    '<div class="db-center">'
                    f'<div class="db-first">{escape(first)}</div>'
                    f'<div class="db-last">{escape(last)}</div>'
                    '</div>'
                    f'{badge_html}'
                    f'{owner_html}'
                    '</div>'
                )
            else:
                html += (
                    '<div class="db-cell">'
                    f'{tl_html}'
                    f'<div class="db-tr">{label}</div>'
                    f'{owner_html}'
                    '</div>'
                )

    html += "</div></div>"
    st.markdown(html, unsafe_allow_html=True)
