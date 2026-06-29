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


def _fetch_board_rows(dsn: str, draft_key: str) -> list[dict[str, Any]]:
    sql = """
    SELECT
        draft_key,
        pick_id,
        round_number,
        slot_number,
        round_label,
        pick_type,
        column_team_key,
        column_team_name,
        current_owner_team_key,
        current_owner_team_name,
        traded_flag,
        ownership_note,
        yahoo_player_key,
        selected_player_name,
        pick_kind,
        selected_at_utc,
        selected_primary_position,
        placeholder_source,
        contract_years_remaining
    FROM nffl.v_draft_board_current
    WHERE draft_key = %s
    ORDER BY round_number, slot_number
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (draft_key,))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def render_postgres_board_html(
    dsn: str,
    draft_key: str,
    min_col_px: int = 72,
    cell_h_px: int = 96,
) -> None:
    rows = _fetch_board_rows(dsn, draft_key)

    if not rows:
        st.warning(f"No draft board rows found in nffl.v_draft_board_current for draft_key={draft_key}.")
        return

    first_round = min(int(r["round_number"]) for r in rows)
    headers = [
        str(r["column_team_name"] or "")
        for r in sorted(
            [r for r in rows if int(r["round_number"]) == first_round],
            key=lambda x: int(x["slot_number"]),
        )
    ]

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
          .db-wrap {{ color: #111 !important; }}

          .db-header {{
            display: grid;
            grid-template-columns: repeat({len(headers)}, minmax({min_col_px}px, 1fr));
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
            font-size: clamp(0.95rem, 1.2vw, 1.12rem);
            line-height: clamp(1.05rem, 1.4vw, 1.22rem);
            height: 74px;
            text-align: center;
            text-transform: uppercase;
            letter-spacing: -0.03em;
            text-shadow: 0 0 8px rgba(255, 121, 0, 0.24);
            box-shadow: inset 0 -4px 0 #D50A0A, 0 2px 8px rgba(0,0,0,0.24);
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
          }}

          .db-grid {{
            display: grid;
            grid-template-columns: repeat({len(headers)}, minmax({min_col_px}px, 1fr));
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

          .db-pos-qb {{ background: #FEF3C7; }}
          .db-pos-rb {{ background: #DCFCE7; }}
          .db-pos-wr {{ background: #DBEAFE; }}
          .db-pos-te {{ background: #F3E8FF; }}
          .db-pos-k  {{ background: #E0F2FE; }}
          .db-pos-def {{ background: #FFE4E6; }}
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
            font-size: clamp(0.78rem, 1.05vw, 0.96rem);
            font-weight: 800;
            line-height: 1.05em;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }}

          .db-last {{
            font-size: clamp(0.98rem, 1.35vw, 1.20rem);
            font-weight: 950;
            line-height: 1.10em;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }}

          /* NFFL_HIGH_CONTRAST_BOARD_PALETTE_START */

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

          /* Position palette: high contrast, readable, and distinct */
          .db-pos-qb {{
            background: #B91C1C !important;
            color: #FFFFFF !important;
            border-color: #450A0A !important;
          }}

          .db-pos-rb {{
            background: #166534 !important;
            color: #FFFFFF !important;
            border-color: #052E16 !important;
          }}

          .db-pos-wr {{
            background: #1D4ED8 !important;
            color: #FFFFFF !important;
            border-color: #172554 !important;
          }}

          .db-pos-te {{
            background: #6D28D9 !important;
            color: #FFFFFF !important;
            border-color: #2E1065 !important;
          }}

          .db-pos-k {{
            background: #B45309 !important;
            color: #FFFFFF !important;
            border-color: #451A03 !important;
          }}

          .db-pos-def {{
            background: #374151 !important;
            color: #FFFFFF !important;
            border-color: #030712 !important;
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

          /* NFFL_HIGH_CONTRAST_BOARD_PALETTE_END */


          /* NFFL_FLAT_LABEL_OVERRIDE_START */

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

          /* NFFL_FLAT_LABEL_OVERRIDE_END */

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

                raw_pos = str(row.get("selected_primary_position") or "").upper().strip()
                if raw_pos in {"D/ST", "DST", "DEFENSE", "DEF"}:
                    pos_key = "def"
                elif raw_pos in {"QB", "RB", "WR", "TE", "K"}:
                    pos_key = raw_pos.lower()
                else:
                    pos_key = "unknown"

                if pos_key == "def":
                    pos_label = "DEF"
                elif pos_key in {"qb", "rb", "wr", "te", "k"}:
                    pos_label = pos_key.upper()
                else:
                    pos_label = ""

                if pos_label and traded:
                    cell_tl_html = f'<div class="db-tl">TRADE · {escape(pos_label)}</div>'
                elif pos_label:
                    cell_tl_html = f'<div class="db-tl">{escape(pos_label)}</div>'
                else:
                    cell_tl_html = tl_html

                is_qo_placeholder = pick_kind == "QO_PLACEHOLDER"
                is_ft_placeholder = pick_kind == "FT_PLACEHOLDER"
                is_contract_placeholder = pick_kind == "CONTRACT_PLACEHOLDER"

                if is_qo_placeholder:
                    display_badge = "QO"
                    cell_class = "db-cell db-cell-qo-placeholder"
                elif is_ft_placeholder:
                    display_badge = "FT"
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
