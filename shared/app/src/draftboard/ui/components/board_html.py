from __future__ import annotations

from typing import Dict, List

import streamlit as st

from draftboard.domain.models import PickSlot, Player, Team, Position


def _is_qo_round(pick: PickSlot) -> bool:
    rt = str(getattr(pick, "round_type", "") or "")
    return rt == "QO" or rt.endswith("QO")


def _pick_label(pick: PickSlot) -> str:
    if _is_qo_round(pick):
        return f"QO{int(pick.round_number)}.{int(pick.slot)}"
    return f"R{int(pick.round_number):02d}.{int(pick.slot)}"


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split()
    if len(parts) <= 1:
        return full.strip(), ""
    first = parts[0]
    last = " ".join(parts[1:])
    return first, last


def _pos_to_enum(pos) -> Position:
    """
    primary_position may be a Position enum or a raw string.
    Normalize to Position for consistent coloring + labels.
    """
    if isinstance(pos, Position):
        return pos
    s = str(pos).strip()
    # Handle common "3B" etc. Enum names use _3B/_2B/_1B in our codebase.
    mapping = {
        "1B": Position._1B,
        "2B": Position._2B,
        "3B": Position._3B,
    }
    if s in mapping:
        return mapping[s]
    # Everything else should align with Position values (C, SS, OF, UTIL, SP, RP)
    try:
        return Position(s)
    except Exception:
        return Position.UTIL


def _pos_color(pos: Position) -> str:
    """
    Custom palette requested by user.
    """
    return {
        # Pitchers
        Position.RP: "#cbabd1",
        Position.SP: "#9656a2",
        # Bats
        Position.C: "#6f1926",
        Position.OF: "#de324c",
        Position._3B: "#f4895f",
        Position._1B: "#f8e16f",
        Position._2B: "#95cf92",
        Position.SS: "#369acc",
        # UTIL fallback
        Position.UTIL: "#FCA5A5",  # UTIL = light red (grey reserved for QO placeholders)
    }.get(pos, "#9CA3AF")


def render_board_html(
    picks: Dict[str, PickSlot],
    teams: Dict[str, Team],
    players: Dict[str, Player],
    qo_placeholders: dict | None = None,
    min_col_px: int = 72,
    cell_h_px: int = 96,
    draft_order_team_keys_by_slot: List[str] | None = None,
    pick_kind_by_pick_id: dict[str, str] | None = None,
    pt_player_keys: set[str] | None = None,
) -> None:
    # Column order (Pick #1..16) comes from draft_order_team_keys_by_slot.
    # It may contain "" for unassigned slots — that is VALID and should still produce a column.
    default_team_keys: List[str] = list(teams.keys())

    if isinstance(draft_order_team_keys_by_slot, list) and len(draft_order_team_keys_by_slot) == 16:
        team_keys = [str(x or "") for x in draft_order_team_keys_by_slot]  # keep blanks
    else:
        # Fallback: best-effort 16 columns from whatever teams dict order is
        team_keys = default_team_keys[:16]

    # Headers: blank if slot unassigned or unknown team key
    col_headers: List[str] = []
    for tk in team_keys:
        if tk and tk in teams:
            col_headers.append(teams[tk].name)
        else:
            col_headers.append("")

    def _slot_original_team_key(ps: PickSlot) -> str:
        """
        Original owner for the board is the TEAM IN THIS SLOT (column owner),
        derived from draft_order_team_keys_by_slot / team_keys list.
        """
        try:
            slot = int(getattr(ps, "slot", 0) or 0)
        except Exception:
            return ""
        if slot < 1 or slot > len(team_keys):
            return ""
        return str(team_keys[slot - 1] or "").strip()

    def _is_traded_pick(ps: PickSlot) -> bool:
        """
        Traded = current owner differs from slot-original owner.
        This matches your UI requirement: only show team label if the pick moved columns.
        """
        try:
            owner = str(getattr(ps, "owner_team_key", "") or "").strip()
            orig = _slot_original_team_key(ps)
            if not owner or not orig:
                return False
            return owner != orig
        except Exception:
            return False

    def _owner_label(ps: PickSlot) -> str:
        """
        Bottom label: show current owner team name (or team key if unknown).
        Only used for traded picks.
        """
        tk = str(getattr(ps, "owner_team_key", "") or "")
        if tk and tk in teams:
            return str(teams[tk].name)
        return tk

    ordered = sorted(picks.values(), key=lambda p: (p.round_number, p.slot))
    
    by_round: Dict[int, List[PickSlot]] = {}
    for p in ordered:
        by_round.setdefault(p.round_number, []).append(p)

    rounds = list(range(1, 26))
    grid_rows: List[List[PickSlot]] = [sorted(by_round.get(r, []), key=lambda p: p.slot) for r in rounds]

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
            grid-template-columns: repeat({len(col_headers)}, minmax({min_col_px}px, 1fr));
            gap: 4px;
            position: sticky;
            top: 3.25rem;
            z-index: 20;
            background: linear-gradient(180deg, #F8FAFC, #EEF2F7);
            padding: 8px 0 10px 0;
            border-bottom: 2px solid #CBD5E1;
          }}

          .db-hcell {{
            background: #FFFFFF;
            color: #0F172A !important;
            border: 2px solid #CBD5E1;
            border-radius: 12px;
            padding: 8px 8px;
            font-weight: 900;

            font-size: clamp(0.95rem, 1.2vw, 1.12rem);
            line-height: clamp(1.05rem, 1.4vw, 1.22rem);

            height: 74px;
            text-align: center;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
          }}

          .db-grid {{
            display: grid;
            grid-template-columns: repeat({len(col_headers)}, minmax({min_col_px}px, 1fr));
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
          }}

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

          .db-badge {{
            position: absolute;
            bottom: 6px;
            left: 8px;
            font-size: clamp(0.70rem, 1.0vw, 0.85rem);
            font-weight: 950;
            opacity: 0.95;
            white-space: nowrap;
          }}

          /* ✅ NEW — traded pick ownership label */
            .db-owner {{
              position: absolute;
              bottom: 6px;
              left: 8px;
              font-size: clamp(0.62rem, 0.95vw, 0.76rem);
              font-weight: 800;
              opacity: 0.92;
              white-space: nowrap;
            }}

            /* PT indicator (Prospect Tagged) — bottom-left, above badges/owner to avoid overlap */
            /* PT indicator (Prospect Tagged) */
            .db-pt {{
              position: absolute;
              left: 8px;
              font-size: clamp(0.70rem, 1.0vw, 0.85rem);
              font-weight: 950;
              opacity: 0.95;
              white-space: nowrap;
            }}

            /* Default PT position: align with badge baseline (bottom-left) */
            .db-pt-low {{
              bottom: 6px;
            }}

            /* If a badge (QO/POACH) is also present, bump PT up slightly */
            .db-pt-high {{
              bottom: 22px;
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

          .db-empty {{
            background: #F8FAFC;
            color: #0F172A !important;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    html = '<div class="db-wrap"><div class="db-header">'
    for h in col_headers:
        html += f'<div class="db-hcell" title="{h}">{h}</div>'
    html += '</div><div class="db-grid">'
    for r in grid_rows:
        for pick in r:
            label = _pick_label(pick)

            # -----------------------------
            # PICK HAS PLAYER
            # -----------------------------
            if pick.selected_player_key:
                pl = players.get(pick.selected_player_key)
                if not pl:
                    # Defensive: treat as empty cell if player not loaded
                    is_traded = _is_traded_pick(pick)
                    owner_html = f'<div class="db-owner">{_owner_label(pick)}</div>' if is_traded else ""
                    tl_html = '<div class="db-tl">TRADE</div>' if is_traded else ""
                    html += (
                        f'<div class="db-cell db-empty">'
                        f'{tl_html}'
                        f'<div class="db-tr">{label}</div>'
                        f'{owner_html}'
                        f'</div>'
                    )
                    continue
                first, last = _split_name(pl.name)

                pos_enum = _pos_to_enum(pl.primary_position)
                pos_label = pos_enum.value
                tl = f"{pos_label} - {pl.mlb_team}"

                is_qo_placeholder = (_is_qo_round(pick) and pick.selected_ts_iso is None)
                bg = "#9CA3AF" if is_qo_placeholder else _pos_color(pos_enum)

                if is_qo_placeholder:
                    text_color = "#0F172A"
                else:
                    text_color = "#FFFFFF" if pos_enum in (Position.C, Position.SP) else "#0F172A"

                # bottom badge (neutral text)
                kind = ""
                if pick_kind_by_pick_id:
                    kind = str(pick_kind_by_pick_id.get(pick.pick_id, "") or "")

                badge = ""
                if kind == "POACH":
                    badge = "POACH"
                elif kind == "QO":
                    badge = "QO"

                badge_html = f'<div class="db-badge">{badge}</div>' if badge else ""

                # PT marker: driven purely by player identity
                pt_html = ""
                try:
                    if pt_player_keys and str(pick.selected_player_key) in pt_player_keys:
                        # Align with badge baseline unless a QO/POACH badge is present
                        pt_class = "db-pt db-pt-high" if badge else "db-pt db-pt-low"
                        pt_html = f'<div class="{pt_class}">PT</div>'
                except Exception:
                    pt_html = ""

                owner_html = ""
                if _is_traded_pick(pick):
                    owner_html = f'<div class="db-owner">{_owner_label(pick)}</div>'

                html += (
                    f'<div class="db-cell" style="background:{bg}; color:{text_color};">'
                    f'<div class="db-tl">{tl}</div>'
                    f'<div class="db-tr">{label}</div>'
                    f'<div class="db-center">'
                    f'  <div class="db-first">{first}</div>'
                    f'  <div class="db-last">{last}</div>'
                    f"</div>"
                    f"{pt_html}"
                    f"{badge_html}"
                    f"{owner_html}"
                    f"</div>"
                )

            # -----------------------------
            # EMPTY SLOT / PLACEHOLDER
            # -----------------------------
            else:
                # QO placeholder (predraft): grey until the pick is actually made
                placeholder_key = None
                if qo_placeholders and _is_qo_round(pick):
                    rec = qo_placeholders.get(pick.owner_team_key) or {}
                    lvls = rec.get("levels") if isinstance(rec, dict) else None
                    if isinstance(lvls, dict):
                        placeholder_key = lvls.get(pick.round_number)

                if placeholder_key and placeholder_key in players:
                    pl = players[placeholder_key]
                    first, last = _split_name(pl.name)

                    pos_enum = _pos_to_enum(pl.primary_position)
                    pos_label = pos_enum.value
                    tl = f"{pos_label} - {pl.mlb_team}"

                    bg = "#CBD5E1"  # grey placeholder
                    text_color = "#0F172A"

                    is_traded = _is_traded_pick(pick)
                    owner_html = f'<div class="db-owner">{_owner_label(pick)}</div>' if is_traded else ""
                    tl_display = "TRADE" if is_traded else tl

                    html += (
                        f'<div class="db-cell" style="background:{bg}; color:{text_color};">'
                        f'<div class="db-tl">{tl_display}</div>'
                        f'<div class="db-tr">{label}</div>'
                        f'<div class="db-center">'
                        f'  <div class="db-first">{first}</div>'
                        f'  <div class="db-last">{last}</div>'
                        f"</div>"
                        f"{owner_html}"
                        f"</div>"
                    )
                else:
                    is_traded = _is_traded_pick(pick)
                    tl = "TRADE" if is_traded else ""

                    owner_html = f'<div class="db-owner">{_owner_label(pick)}</div>' if is_traded else ""

                    tl_html = '<div class="db-tl">TRADE</div>' if is_traded else ""

                    html += (
                        f'<div class="db-cell db-empty">'
                        f'{tl_html}'
                        f'<div class="db-tr">{label}</div>'
                        f'{owner_html}'
                        f'</div>'
                    )

    html += "</div></div>"
    st.markdown(html, unsafe_allow_html=True)
