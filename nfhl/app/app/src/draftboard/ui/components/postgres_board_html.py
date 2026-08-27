from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

import streamlit as st


def _split_name(
    full_name: str,
) -> tuple[str, str]:
    parts = str(
        full_name
        or ""
    ).strip().split()

    if len(parts) <= 1:
        return (
            str(
                full_name
                or ""
            ).strip(),
            "",
        )

    return (
        parts[0],
        " ".join(
            parts[1:]
        ),
    )


def _cell_label(
    row: dict[str, Any],
    *,
    total_slots: int,
) -> str:
    round_number = int(
        row.get(
            "round_number"
        )
        or 0
    )

    slot_number = int(
        row.get(
            "slot_number"
        )
        or 0
    )

    round_label = str(
        row.get(
            "round_label"
        )
        or ""
    ).strip()

    if not round_label:
        round_label = (
            f"R{round_number:02d}"
        )

    # Team columns remain fixed across the board. For a snake draft,
    # the chronological pick number within an even round runs in the
    # opposite direction across those fixed columns.
    display_pick_number = (
        slot_number
        if round_number % 2 == 1
        else (
            total_slots
            + 1
            - slot_number
        )
    )

    return (
        f"{round_label}."
        f"{display_pick_number}"
    )


def _position_key(
    raw_position: object,
) -> tuple[str, str]:
    raw = str(
        raw_position
        or ""
    ).strip().upper()

    mapping = {
        "C": ("c", "C"),
        "LW": ("lw", "LW"),
        "RW": ("rw", "RW"),
        "W": ("w", "W"),
        "F": ("f", "F"),
        "D": ("d", "D"),
        "G": ("g", "G"),
        "UTIL": ("util", "UTIL"),
    }

    return mapping.get(
        raw,
        ("unknown", raw),
    )


def _player_card_position_label(
    player: dict[str, Any],
    *,
    fallback_position: str,
) -> str:
    """
    Show Yahoo hockey-position eligibility on the draft card.

    Generic roster slots such as F and Util are intentionally
    excluded from the compact card label.
    """
    allowed = {
        "C",
        "LW",
        "RW",
        "W",
        "D",
        "G",
    }

    raw_positions = (
        player.get("eligible_positions")
        or []
    )

    if not isinstance(
        raw_positions,
        (list, tuple),
    ):
        raw_positions = [
            raw_positions
        ]

    positions: list[str] = []

    for value in raw_positions:
        position = str(
            value or ""
        ).strip().upper()

        if (
            position in allowed
            and position not in positions
        ):
            positions.append(
                position
            )

    if not positions:
        fallback = str(
            fallback_position or ""
        ).strip().upper()

        if fallback:
            positions.append(
                fallback
            )

    return "/".join(
        positions
    )


def render_postgres_board_html(
    rows: list[dict[str, Any]],
    *,
    players: list[dict[str, Any]] | None = None,
    min_col_px: int = 72,
    cell_h_px: int = 96,
) -> None:
    """
    Render the authoritative NFHL live Draft Board.

    Visual contract follows active NFFL:
      - columns = fantasy team names
      - no separate round/index column
      - each cell carries its own pick label
      - sticky team header
      - same board geometry
    """
    if not rows:
        return

    player_lookup = {
        str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ).strip(): player
        for player in (
            players or []
        )
        if str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ).strip()
    }

    first_round = min(
        int(
            row["round_number"]
        )
        for row in rows
    )

    headers = [
        str(
            row.get(
                "column_team_name"
            )
            or ""
        )
        for row in sorted(
            [
                row
                for row in rows
                if int(
                    row["round_number"]
                )
                == first_round
            ],
            key=lambda row: int(
                row["slot_number"]
            ),
        )
    ]

    by_round: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in rows:
        by_round[
            int(
                row["round_number"]
            )
        ].append(
            row
        )

    grid_rows = [
        sorted(
            by_round[round_number],
            key=lambda row: int(
                row["slot_number"]
            ),
        )
        for round_number
        in sorted(
            by_round
        )
    ]

    st.markdown(
        f"""
        <style>
          .nfhl-live-db-wrap {{
              color: #111 !important;
          }}

          .nfhl-live-db-header {{
              display: grid;
              grid-template-columns:
                  repeat(
                      {len(headers)},
                      minmax({min_col_px}px, 1fr)
                  );

              gap: 4px;

              position: sticky;
              top: 6.40rem;
              z-index: 20;

              background:
                  linear-gradient(
                      135deg,
                      #001B3F 0%,
                      #002868 58%,
                      #0055A5 100%
                  );

              padding: 10px 0 12px 0;

              border-bottom:
                  3px solid #FFFFFF;
          }}

          .nfhl-live-db-hcell {{
              background: #002868;
              color: #FFFFFF !important;

              border:
                  2px solid #4B92DB;

              border-radius: 10px;

              padding: 8px 10px;
              box-sizing: border-box;

              font-weight: 950;

              font-size:
                  clamp(
                      0.95rem,
                      1.2vw,
                      1.12rem
                  );

              line-height:
                  clamp(
                      1.05rem,
                      1.4vw,
                      1.22rem
                  );

              height: 74px;

              text-align: center;
              text-transform: uppercase;

              letter-spacing: -0.03em;

              box-shadow:
                  inset 0 -4px 0 #4B92DB,
                  0 2px 8px rgba(0,0,0,0.24);

              display: -webkit-box;

              -webkit-line-clamp: 2;
              -webkit-box-orient: vertical;

              overflow: hidden;
          }}

          .nfhl-live-db-grid {{
              display: grid;

              grid-template-columns:
                  repeat(
                      {len(headers)},
                      minmax({min_col_px}px, 1fr)
                  );

              gap: 4px;
              align-items: stretch;

              padding:
                  8px 0 12px 0;
          }}

          .nfhl-live-db-cell {{
              border:
                  1.5px solid rgba(0,0,0,0.18);

              border-radius: 14px;

              height: {cell_h_px}px;

              padding: 8px;

              position: relative;
              overflow: hidden;

              box-shadow:
                  0 1px 2px rgba(0,0,0,0.06);

              background: #F8FAFC;

              color:
                  #0F172A !important;
          }}

          .nfhl-live-db-selected {{
              color:
                  #FFFFFF !important;
          }}

          .nfhl-live-db-pos-c {{
              background: #B91C1C;
          }}

          .nfhl-live-db-pos-lw {{
              background: #166534;
          }}

          .nfhl-live-db-pos-rw {{
              background: #B45309;
          }}

          .nfhl-live-db-pos-w {{
              background: #047857;
          }}

          .nfhl-live-db-pos-f {{
              background: #0F766E;
          }}

          .nfhl-live-db-pos-d {{
              background: #1D4ED8;
          }}

          .nfhl-live-db-pos-g {{
              background: #6D28D9;
          }}

          .nfhl-live-db-pos-util {{
              background: #475569;
          }}

          .nfhl-live-db-pos-unknown {{
              background: #374151;
          }}

          .nfhl-live-db-tl {{
              position: absolute;

              top: 6px;
              left: 8px;

              font-size:
                  clamp(
                      0.64rem,
                      1.0vw,
                      0.78rem
                  );

              opacity: 0.92;
              font-weight: 900;

              white-space: nowrap;
          }}

          .nfhl-live-db-tr {{
              position: absolute;

              top: 6px;
              right: 8px;

              font-size:
                  clamp(
                      0.64rem,
                      1.0vw,
                      0.78rem
                  );

              opacity: 0.92;
              font-weight: 900;

              white-space: nowrap;
          }}

          .nfhl-live-db-owner {{
              position: absolute;

              bottom: 6px;
              left: 8px;

              font-size:
                  clamp(
                      0.62rem,
                      0.95vw,
                      0.76rem
                  );

              font-weight: 800;
              opacity: 0.92;

              white-space: nowrap;
          }}

          .nfhl-live-db-center {{
              height: 100%;

              display: flex;
              flex-direction: column;

              align-items: center;
              justify-content: center;

              text-align: center;

              padding-top: 12px;
          }}

          .nfhl-live-db-first {{
              font-size:
                  clamp(
                      0.78rem,
                      1.05vw,
                      0.96rem
                  );

              font-weight: 800;
              line-height: 1.05em;

              max-width: 100%;
              overflow: hidden;

              text-overflow: ellipsis;
              white-space: nowrap;
          }}

          .nfhl-live-db-last {{
              font-size:
                  clamp(
                      0.98rem,
                      1.35vw,
                      1.20rem
                  );

              font-weight: 950;
              line-height: 1.10em;

              max-width: 100%;
              overflow: hidden;

              text-overflow: ellipsis;
              white-space: nowrap;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    html = (
        '<div class="nfhl-live-db-wrap">'
        '<div class="nfhl-live-db-header">'
    )

    for header in headers:
        safe_header = escape(
            header
        )

        html += (
            '<div '
            'class="nfhl-live-db-hcell" '
            f'title="{safe_header}">'
            f'{safe_header}'
            '</div>'
        )

    html += (
        '</div>'
        '<div class="nfhl-live-db-grid">'
    )

    for round_rows in grid_rows:
        for row in round_rows:
            label = escape(
                _cell_label(
                    row,
                    total_slots=len(
                        headers
                    ),
                )
            )

            selected_name = str(
                row.get(
                    "selected_player_name"
                )
                or ""
            ).strip()

            traded = bool(
                row.get(
                    "traded_flag"
                )
            )

            current_owner = escape(
                str(
                    row.get(
                        "current_owner_team_name"
                    )
                    or row.get(
                        "current_owner_team_key"
                    )
                    or ""
                )
            )

            ownership_note = escape(
                str(
                    row.get(
                        "ownership_note"
                    )
                    or ""
                )
            )

            owner_label = (
                ownership_note
                or current_owner
            )

            owner_html = (
                '<div '
                'class="nfhl-live-db-owner">'
                f'{owner_label}'
                '</div>'
                if traded
                else ""
            )

            if selected_name:
                first_name, last_name = (
                    _split_name(
                        selected_name
                    )
                )

                pos_key, primary_pos_label = (
                    _position_key(
                        row.get(
                            "selected_primary_position"
                        )
                    )
                )

                selected_player = (
                    player_lookup.get(
                        str(
                            row.get(
                                "yahoo_player_key"
                            )
                            or ""
                        ).strip(),
                        {},
                    )
                )

                position_label = (
                    _player_card_position_label(
                        selected_player,
                        fallback_position=(
                            primary_pos_label
                        ),
                    )
                )

                nhl_team_label = str(
                    selected_player.get(
                        "nhl_team_abbr"
                    )
                    or ""
                ).strip().upper()

                card_meta = " - ".join(
                    part
                    for part in (
                        position_label,
                        nhl_team_label,
                    )
                    if part
                )

                tl_label = ""

                if traded and card_meta:
                    tl_label = (
                        f"TRADE · "
                        f"{card_meta}"
                    )

                elif traded:
                    tl_label = "TRADE"

                elif card_meta:
                    tl_label = card_meta

                tl_html = (
                    '<div '
                    'class="nfhl-live-db-tl">'
                    f'{escape(tl_label)}'
                    '</div>'
                    if tl_label
                    else ""
                )

                html += (
                    '<div '
                    'class="nfhl-live-db-cell '
                    'nfhl-live-db-selected '
                    f'nfhl-live-db-pos-{pos_key}">'
                    f'{tl_html}'
                    '<div '
                    'class="nfhl-live-db-tr">'
                    f'{label}'
                    '</div>'
                    '<div '
                    'class="nfhl-live-db-center">'
                    '<div '
                    'class="nfhl-live-db-first">'
                    f'{escape(first_name)}'
                    '</div>'
                    '<div '
                    'class="nfhl-live-db-last">'
                    f'{escape(last_name)}'
                    '</div>'
                    '</div>'
                    f'{owner_html}'
                    '</div>'
                )

            else:
                tl_html = (
                    '<div '
                    'class="nfhl-live-db-tl">'
                    'TRADE'
                    '</div>'
                    if traded
                    else ""
                )

                html += (
                    '<div '
                    'class="nfhl-live-db-cell">'
                    f'{tl_html}'
                    '<div '
                    'class="nfhl-live-db-tr">'
                    f'{label}'
                    '</div>'
                    f'{owner_html}'
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
