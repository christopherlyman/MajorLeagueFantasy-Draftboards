from __future__ import annotations

import os
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

import streamlit as st
import bcrypt
import psycopg
import extra_streamlit_components as stx

from draftboard.data.picks_grid import build_picks_grid
from draftboard.domain.clock import compute_clock_status
from draftboard.domain.models import Position, PickLogEntry, PickSlot, Team
from draftboard.state.autosave import try_load_autosave, save_autosave
from draftboard.state.runtime import get_league_key, get_postgres_dsn, get_season_year
from draftboard.state.store import DraftClock, DraftState, has_state, init_state, get_state, set_current_pick
from draftboard.state.init_restore import (
    _team_to_slot_from_order,
    _is_legacy_team_keyspace,
    _build_canonical_teams_from_yahoo_rows,
    _build_legacy_to_canonical_team_key_map,
    _canon_team_key_from_mixed_key,
    ensure_initialized,
)
from draftboard.ui.components.board_html import render_board_html
from draftboard.ui.components.commissioner_tools import render_commissioner_actions
from draftboard.ui.components.draft_lottery import render_draft_lottery_tab
from draftboard.ui.components.draft_statistics import (
    render_draft_complete_banner,
    render_draft_statistics_tab,
)
from draftboard.ui.components.player_search import (
    filter_player_keys_by_query,
    player_search_matches,
)

APP_VERSION = "v1"
APP_FILE_PATH = __file__


def _last_modified_iso(path: str) -> str:
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except Exception:
        return ""


def _pos_label(p) -> str:
    try:
        return p.value
    except Exception:
        return str(p)


def _clock_hhmm(state: DraftState) -> str:
    status = compute_clock_status(
        is_running=state.clock.is_running,
        seconds_per_pick=state.clock.seconds_per_pick,
        started_ts_iso=state.clock.pick_started_ts_iso,
        paused_ts_iso=state.clock.pick_paused_ts_iso,
        elapsed_paused_seconds=int(getattr(state.clock, "elapsed_paused_seconds", 0) or 0),
    )
    hh = status.remaining_seconds // 3600
    mm = (status.remaining_seconds % 3600) // 60
    return f"{hh:02d}:{mm:02d}"



def _load_predraft_qo_rows(dsn: str, league_key: str, season_year: int) -> list[tuple[str, int, str, object]]:
    '''
    Raw predraft QO rows from public.qualifying_offer.

    Returns tuples:
      (team_key, qo_level, yahoo_player_key, updated_at)
    '''
    if not dsn:
        return []

    try:
        import psycopg
    except Exception:
        return []

    out: list[tuple[str, int, str, object]] = []
    sql = '''
      SELECT team_key, qo_level, yahoo_player_key, updated_at
      FROM public.qualifying_offer
      WHERE league_key=%s AND season_year=%s
      ORDER BY team_key, qo_level;
    '''
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            for team_key, lvl, pkey, updated_at in cur.fetchall():
                if not team_key or lvl is None or not pkey:
                    continue
                out.append((str(team_key), int(lvl), str(pkey), updated_at))
    return out


def _load_predraft_qos(dsn: str, league_key: str, season_year: int) -> dict[str, dict[int, str]]:
    '''
    Returns:
      { team_key: {1: yahoo_player_key, ..., 5: yahoo_player_key} }
    From public.qualifying_offer (predraft selections).
    '''
    out: dict[str, dict[int, str]] = {}
    for team_key, lvl, pkey, _updated_at in _load_predraft_qo_rows(dsn, league_key, season_year):
        out.setdefault(str(team_key), {})[int(lvl)] = str(pkey)
    return out

def _load_predraft_qo_player_maps_canonical(
    dsn: str,
    league_key: str,
    season_year: int,
    *,
    order: list[str] | None,
    legacy_to_canon: dict[str, str] | None,
) -> tuple[set[str], dict[str, int], dict[str, str]]:
    '''
    Returns:
      - predraft_qo_keys
      - predraft_qo_level_by_player
      - predraft_qo_team_by_player

    Team keys are normalized to canonical Yahoo team keys.
    '''
    predraft_qo_keys: set[str] = set()
    predraft_qo_level_by_player: dict[str, int] = {}
    predraft_qo_team_by_player: dict[str, str] = {}

    for team_key, lvl, pkey, _updated_at in _load_predraft_qo_rows(dsn, league_key, season_year):
        pk = str(pkey)
        predraft_qo_keys.add(pk)
        predraft_qo_level_by_player[pk] = int(lvl)
        predraft_qo_team_by_player[pk] = _canon_team_key_from_mixed_key(
            str(team_key),
            order=list(order or []),
            legacy_to_canon=dict(legacy_to_canon or {}),
        )

    return predraft_qo_keys, predraft_qo_level_by_player, predraft_qo_team_by_player


def _compute_current_qos_from_log(predraft: dict[str, dict[int, str]], pick_log: list) -> dict[str, dict[int, str]]:
    """
    Starting from predraft QOs, remove any player that has already been selected
    in the real pick log. Returns current effective QOs by team/level.
    """
    selected = set()
    for ev in (pick_log or []):
        pk = getattr(ev, "player_key", None)
        if pk:
            selected.add(str(pk))

    out: dict[str, dict[int, str]] = {}
    for team_key, lvls in (predraft or {}).items():
        for lvl, pkey in (lvls or {}).items():
            spk = str(pkey or "").strip()
            if not spk or spk in selected:
                continue
            out.setdefault(str(team_key), {})[int(lvl)] = spk
    return out


def _load_predraft_qo_player_maps_raw(dsn: str, league_key: str, season_year: int) -> tuple[set[str], dict[str, int], dict[str, str]]:
    '''
    Returns:
      - predraft_qo_keys
      - predraft_qo_level_by_player
      - predraft_qo_team_by_player

    Raw team keys as stored in public.qualifying_offer.
    '''
    predraft_qo_keys: set[str] = set()
    predraft_qo_level_by_player: dict[str, int] = {}
    predraft_qo_team_by_player: dict[str, str] = {}

    for team_key, lvl, pkey, _updated_at in _load_predraft_qo_rows(dsn, league_key, season_year):
        pk = str(pkey)
        predraft_qo_keys.add(pk)
        predraft_qo_level_by_player[pk] = int(lvl)
        predraft_qo_team_by_player[pk] = str(team_key)

    return predraft_qo_keys, predraft_qo_level_by_player, predraft_qo_team_by_player


def _sync_qo_placeholders(state: DraftState, predraft: dict[str, dict[int, str]], current: dict[str, dict[int, str]]) -> None:
    """
    Fill grey placeholders for QO rounds based on *current* QOs.

    Canonical rule:
    - QO-round slot ownership follows the current pick owner (pick.owner_team_key).
    - Column identity remains fixed by draft_order_team_keys_by_slot.
    - We do not re-derive QO-round ownership from slot order here because QO-round
      draft slots are tradable assets.
    """

    for pick in state.picks.values():
        if int(getattr(pick, "round_number", 0) or 0) > 5:
            continue

        # if it's a real pick, never touch it
        if getattr(pick, "selected_ts_iso", None) is not None:
            continue

        team_key = str(getattr(pick, "owner_team_key", "") or "").strip()
        if not team_key or team_key not in (state.teams or {}):
            continue

        lvl = int(getattr(pick, "round_number", 0) or 0)

        pk = current.get(team_key, {}).get(lvl)
        pick.selected_player_key = pk if pk else None
        pick.selected_ts_iso = None


def _apply_pick(state: DraftState, pick_id: str, player_key: str, pick_kind: str = "FA") -> None:
    pick = state.picks[pick_id]
    ts = datetime.utcnow().isoformat()

    pick.selected_player_key = player_key
    pick.selected_ts_iso = ts

    player = state.players[player_key]
    state.pick_log.append(
        PickLogEntry(
            event_id=str(uuid4()),
            pick_id=pick.pick_id,
            owner_team_key=pick.owner_team_key,
            player_key=player.player_key,
            player_name=player.name,
            primary_position=player.primary_position if isinstance(player.primary_position, Position) else Position(str(player.primary_position)),
            pick_kind=pick_kind,
            ts_iso=ts,
        )
    )

    idx = state.pick_order.index(pick.pick_id)
    if idx + 1 < len(state.pick_order):
        next_pick_id = state.pick_order[idx + 1]
        state.clock.current_pick_id = next_pick_id

        if bool(getattr(state.clock, "auto_advance", True)):
            from draftboard.domain.clock import start_pick_clock
            state.clock.pick_started_ts_iso = start_pick_clock()
            state.clock.pick_paused_ts_iso = None
            state.clock.elapsed_paused_seconds = 0
            state.clock.is_running = True
    else:
        state.clock.is_running = False
        state.clock.pick_started_ts_iso = None
        state.clock.pick_paused_ts_iso = None
        state.clock.elapsed_paused_seconds = 0

    save_autosave(state)


def render_pick_controls(state: DraftState) -> None:
    drafted = {
        ps.selected_player_key
        for ps in state.picks.values()
        if ps.selected_player_key and ps.selected_ts_iso is not None
    }

    import os

    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()

    order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
    legacy_to_canon = dict(st.session_state.get("legacy_to_canonical_team_key_map", {}) or {})

    predraft_qo_keys, predraft_qo_level_by_player, predraft_qo_team_by_player = _load_predraft_qo_player_maps_canonical(
        dsn,
        league_key,
        season_year,
        order=order,
        legacy_to_canon=legacy_to_canon,
    )

    current_pick_id = state.clock.current_pick_id
    current_pick = state.picks[current_pick_id]
    on_clock_team = state.teams.get(current_pick.owner_team_key)
    hhmm = _clock_hhmm(state)

    st.markdown(
        """
        <style>
        .kpi-line { margin: 0.45rem 0; }
        .kpi-label { font-size: 2.0rem; opacity: 0.70; font-weight: 850; }
        .kpi-value { font-size: 2.0rem; color: #0ea5e9; font-weight: 950; line-height: 1.1; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1], vertical_alignment="top")

    with left:
        team_name = on_clock_team.name if on_clock_team else "(unknown)"
        st.markdown(
            f"""
            <div class="kpi-line"><span class="kpi-label">Current Pick:</span> <span class="kpi-value">{current_pick_id}</span></div>
            <div class="kpi-line"><span class="kpi-label">On the clock:</span> <span class="kpi-value">{team_name}</span></div>
            <div class="kpi-line"><span class="kpi-label">Pick Clock:</span> <span class="kpi-value">{hhmm}</span></div>
            """,
            unsafe_allow_html=True,
        )


    with right:
        # Only treat it as "used" if it was a real pick (ts exists).
        # Placeholders for QOs/contracts have selected_ts_iso=None and must remain pickable.
        if current_pick.selected_player_key is not None and current_pick.selected_ts_iso is not None:
            st.info("This pick is already used.")
            return

        import os

        keeper_filter_enabled = str(
            os.environ.get("DRAFTBOARD_KEEPER_FILTER_ENABLED", "1")
        ).strip().lower() in ("1", "true", "yes", "y", "on")

        contracted_keys = (
            st.session_state.get("contracted_keys", set()) or set()
        ) if keeper_filter_enabled else set()

        available_players = [
            p for p in state.players.values()
            if (p.player_key not in drafted and p.player_key not in contracted_keys)
        ]
        available_players.sort(
            key=lambda p: (
                getattr(p, "rank_value", None) is None,
                getattr(p, "rank_value", None) if getattr(p, "rank_value", None) is not None else 999999,
                p.name or "",
            )
        )
        def fmt_player(pk: str) -> str:
            p = state.players[pk]
            pos = "/".join([_pos_label(x) for x in getattr(p, "positions", [])])
            tm = getattr(p, "mlb_team", "") or ""
            if tm and pos:
                return f"{p.name} — {tm} — {pos}"
            if tm:
                return f"{p.name} — {tm}"
            if pos:
                return f"{p.name} — {pos}"
            return p.name

        draft_player_query_main = st.text_input(
            "Search player",
            value="",
            placeholder="Type a player name...",
            key=f"draft_player_query_main_{state.clock.current_pick_id}",
        )

        player_keys = [p.player_key for p in available_players]
        player_options = filter_player_keys_by_query(player_keys, draft_player_query_main, fmt_player)

        select_key = f"selected_player_key_main_{state.clock.current_pick_id}"
        chosen_player_key = st.selectbox(
            "Select player to draft",
            options=player_options,
            format_func=fmt_player,
            index=None,
            placeholder="Choose a player…",
            help="Search above. Player search is case-insensitive and accent-insensitive.",
            key=select_key,
        )

        btn_label = "MAKE PICK" if state.commissioner_mode else "SUBMIT PICK"
        if st.button(btn_label, type="primary", key="submit_pick_main"):
            if not _user_can_submit_pick(state):
                st.error("You are not authorized to submit this pick.")
                return

            if chosen_player_key is None:
                st.warning("Pick a player first.")
                return

            pick = state.picks[state.clock.current_pick_id]
            if pick.selected_player_key is not None and pick.selected_ts_iso is not None:
                st.error("That pick is already used.")
                return
            if chosen_player_key in drafted:
                st.error("Player already drafted.")
                return


            # ---- QO / POACH / RELEASE LOGIC (pick-driven) ----
            pick_kind = "FA"

            # Identify current on-clock round/team
            cur_pick = state.picks[state.clock.current_pick_id]
            cur_round = int(cur_pick.round_number)
            cur_team_key = str(cur_pick.owner_team_key)

            # If chosen player is in predraft QO list, we may need to treat as QO, POACH, or FA (released)
            if chosen_player_key in predraft_qo_level_by_player:
                submitter_lvl = int(predraft_qo_level_by_player[chosen_player_key])
                submitter_team_key = str(predraft_qo_team_by_player.get(chosen_player_key, ""))

                # Find the submitter's original QO slot pick (TEAM + level)
                submitter_slot = None
                for ps in state.picks.values():
                    if int(ps.round_number) == submitter_lvl and str(ps.owner_team_key) == submitter_team_key:
                        submitter_slot = ps
                        break

                # If we can't find the slot, be defensive: treat as reserved (no guessing)
                if submitter_slot is None:
                    submitter_nm = state.teams.get(submitter_team_key).name if submitter_team_key in state.teams else submitter_team_key
                    st.error(f"Cannot resolve submitter slot for this QO player. Reserved for {submitter_nm} at QO{submitter_lvl}.")
                    return

                # PICK-DRIVEN RELEASE:
                # If the submitter slot has been used as a real pick (ts exists) and they did NOT take this player,
                # then the player is released immediately to the FA pool.
                submitter_slot_used = (submitter_slot.selected_ts_iso is not None)
                submitter_took_player = (submitter_slot_used and submitter_slot.selected_player_key == chosen_player_key)
                released_to_fa = (submitter_slot_used and not submitter_took_player)
                
                # If it's the submitter team on its exact slot: it's a QO pick
                if (cur_team_key == submitter_team_key) and (cur_round == submitter_lvl):
                    pick_kind = "QO"

                # If released, anyone can take as FA (even within same QO round)
                elif released_to_fa:
                    pick_kind = "FA"

                # Otherwise still reserved; only poach-eligible if submitter_lvl > current round (QO rounds 1..5)
                else:
                    # Only meaningful during QO rounds
                    if 1 <= cur_round <= 5 and submitter_lvl > cur_round:
                        pick_kind = "POACH"
                    else:
                        submitter_nm = state.teams.get(submitter_team_key).name if submitter_team_key in state.teams else submitter_team_key
                        st.error(f"Not poach-eligible yet. Reserved for {submitter_nm} at QO{submitter_lvl}.")
                        return
            # ---- END QO / POACH / RELEASE LOGIC ----

            _apply_pick(state, state.clock.current_pick_id, chosen_player_key, pick_kind=pick_kind)
            st.success(
                f"Picked {state.players[chosen_player_key].name} at {state.clock.current_pick_id} [{pick_kind}]"
            )

            # Clear selection for THIS pick + rerun so UI updates immediately
            if select_key in st.session_state:
                del st.session_state[select_key]
            st.rerun()

def render_mobile_pick(state: DraftState) -> None:
    drafted = {ps.selected_player_key for ps in state.picks.values()
    if ps.selected_player_key and ps.selected_ts_iso is not None}

    import os
    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()
    predraft_qo_keys, predraft_qo_level_by_player, predraft_qo_team_by_player = _load_predraft_qo_player_maps_raw(
        dsn, league_key, season_year
    )

    current_pick_id = state.clock.current_pick_id
    current_pick = state.picks[current_pick_id]
    on_clock_team = state.teams.get(current_pick.owner_team_key)
    hhmm = _clock_hhmm(state)

    st.markdown(
        """
        <style>
          .mobile-card {
            border: 1px solid rgba(0,0,0,0.14);
            border-radius: 16px;
            padding: 14px 14px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            background: #FFFFFF;
          }
          .mobile-kicker { font-size: 0.85rem; opacity: 0.80; font-weight: 800; }
          .mobile-pick { font-size: 1.85rem; font-weight: 950; line-height: 1.10; margin-top: 4px; }
          .mobile-team { font-size: 1.25rem; font-weight: 900; margin-top: 6px; }
          .mobile-clock { font-size: 1.05rem; font-weight: 850; margin-top: 6px; opacity: 0.9; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="mobile-card">', unsafe_allow_html=True)
    st.markdown('<div class="mobile-kicker">Current Pick</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="mobile-pick">{current_pick_id}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="mobile-team">On the clock: {on_clock_team.name if on_clock_team else "(unknown)"}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="mobile-clock">Pick Clock: {hhmm}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.write("")

    if current_pick.selected_player_key is not None and current_pick.selected_ts_iso is not None:
        pl = state.players[current_pick.selected_player_key]
        st.success(f"This pick is already used: {pl.name} — {pl.mlb_team}")
        return

    pos_options = [p.value for p in Position]
    pos_filter = st.multiselect("Position filter", options=pos_options, default=[])

    def eligible(p) -> bool:
        if p.player_key in drafted:
            return False
        if not pos_filter:
            return True
        return any(_pos_label(pp) in pos_filter for pp in p.positions)

    available_players = [p for p in state.players.values() if eligible(p)]
    available_players.sort(
        key=lambda p: (
            getattr(p, "rank_value", None) is None,
            getattr(p, "rank_value", None) if getattr(p, "rank_value", None) is not None else 999999,
            p.name or "",
        )
    )
    def fmt(pk: str) -> str:
        p = state.players[pk]
        return f"{p.name} — {p.mlb_team} — {'/'.join([_pos_label(x) for x in p.positions])}"

    mobile_player_query = st.text_input(
        "Search player",
        value="",
        placeholder="Type a player name...",
        key="mobile_player_query",
    )

    player_keys = [p.player_key for p in available_players]
    player_options = filter_player_keys_by_query(player_keys, mobile_player_query, fmt)

    chosen_player_key = st.selectbox(
        "Select player to draft",
        options=player_options,
        format_func=fmt,
        index=None,
        placeholder="Choose a player…",
        help="Search above. Player search is case-insensitive and accent-insensitive.",
        key="mobile_selected_player_key",
    )

    st.write("")

    if st.button("SUBMIT PICK", type="primary", use_container_width=True, key="mobile_submit_pick"):
        if not _user_can_submit_pick(state):
            st.error("You are not authorized to submit this pick.")
            return

        if chosen_player_key is None:
            st.warning("Pick a player first.")
            return
        if chosen_player_key in drafted:
            st.error("Player already drafted.")
            return
        _apply_pick(state, current_pick_id, chosen_player_key, pick_kind="FA")
        st.success(f"Picked {state.players[chosen_player_key].name} at {state.clock.current_pick_id} [FA]")

def _build_players_df(
    players: list["Player"],
    *,
    status_by_player_key: dict[str, str],
    team_name_by_player_key: dict[str, str],
    draft_pick_label_by_player_key: dict[str, str],
) -> "pd.DataFrame":
    """
    Available Players table builder.

    Adds 3 display columns:
      - Contract/PT/QO
      - Team Name
      - Draft Pick   (display label; sorting uses a separate key in render_available_players)
    """
    import pandas as pd

    rows = []
    for p in players:
        all_pos_list = [_pos_label(x) for x in getattr(p, "positions", [])]

        # Display cleanup:
        # - Batters: UTIL is implied unless UTIL-only
        # - Pitchers: P is implied unless P-only
        pos_set = set(all_pos_list)

        is_pitcher = bool(pos_set & {"SP", "RP", "P"}) and not bool(pos_set & {"C","1B","2B","3B","SS","OF","UTIL"} - {"UTIL"})
        # If your data never mixes hitter/pitcher, the above is fine; otherwise it's defensive.

        if not is_pitcher:
            # Drop UTIL if they have any other non-UTIL position
            if "UTIL" in pos_set and any(x != "UTIL" for x in pos_set):
                all_pos_list = [x for x in all_pos_list if x != "UTIL"]
        else:
            # Drop P if they have other pitcher positions
            if "P" in pos_set and any(x in pos_set for x in ("SP", "RP")):
                all_pos_list = [x for x in all_pos_list if x != "P"]

        pk = str(getattr(p, "player_key", "") or "")
        rows.append(
            {
                "Player Name": getattr(p, "name", ""),
                "Team": getattr(p, "mlb_team", ""),
                "Position": "/".join(all_pos_list),

                "Contract/PT/QO": status_by_player_key.get(pk, ""),
                # Numeric years for sorting/display; blank for non-contracts
                "Contract Years": (
                    int(status_by_player_key.get(pk, "0").split("-")[0])
                    if status_by_player_key.get(pk, "").endswith("-year") or status_by_player_key.get(pk, "").endswith("-years")
                    else None
                ),
                "Team Name": team_name_by_player_key.get(pk, ""),
                "Draft Pick": draft_pick_label_by_player_key.get(pk, ""),

                "Current Rank": getattr(p, "rank_value", None),
                "% Ros": getattr(p, "percent_owned", None),

                "H/AB": getattr(p, "h_ab", None),
                "R": getattr(p, "r", None),
                "HR": getattr(p, "hr", None),
                "RBI": getattr(p, "rbi", None),
                "SB": getattr(p, "sb", None),
                "BB": getattr(p, "bb", None),
                "K (H)": getattr(p, "k_hit", None),
                "AVG": getattr(p, "avg", None),

                "IP": getattr(p, "ip", None),
                "W": getattr(p, "w", None),
                "K (P)": getattr(p, "k_pit", None),
                "TB": getattr(p, "tb", None),
                "ERA": getattr(p, "era", None),
                "WHIP": getattr(p, "whip", None),
                "QS": getattr(p, "qs", None),
                "SV+H": getattr(p, "sv_h", None),
            }
        )

    df = pd.DataFrame(rows)

    # Coerce numeric columns for correct numeric sorting + display
    numeric_cols = [
        "Current Rank", "% Ros",
        "R", "HR", "RBI", "SB", "BB", "K (H)",
        "AVG", "IP", "W", "K (P)", "TB", "ERA", "WHIP", "QS", "SV+H",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


def _format_players_df_for_display(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Apply the same display formatting rules as Available Players,
    without changing sorting semantics (caller sorts first).
    """
    import pandas as pd

    df_disp = df.copy()

    # Rank / % Ros: show as ints without .0
    for c in ["Current Rank", "Rank", "% Ros"]:
        if c in df_disp.columns:
            s = pd.to_numeric(df_disp[c], errors="coerce")
            s = s.round(0)
            s = s.astype("Int64")  # keeps <NA>
            # Convert to string first, then blank out <NA> (can't assign "" into Int64)
            df_disp[c] = s.astype("string").fillna("")

    # Counting stats: show ints
    for c in ["R","HR","RBI","SB","BB","K (H)","W","K (P)","TB","QS","SV+H"]:
        if c in df_disp.columns:
            s = df_disp[c]
            s = s.round(0).astype("Int64")
            df_disp[c] = s.where(~s.isna(), pd.NA).astype("string").fillna("")

    # Rate stats (pitching): force DISPLAY STRINGS (prevents 195.100000 rendering)
    # ERA/WHIP => 2 decimals, IP => 1 decimal. Missing => "" (blank)
    for c in ["ERA", "WHIP"]:
        if c in df_disp.columns:
            s = pd.to_numeric(df_disp[c], errors="coerce")
            df_disp[c] = s.apply(lambda x: "" if pd.isna(x) else f"{float(x):.2f}")

    # AVG: display as .300 (no leading zero). Missing => ""
    if "AVG" in df_disp.columns:
        def _fmt_avg(x):
            if pd.isna(x):
                return ""
            s = f"{float(x):.3f}"
            return s[1:] if s.startswith("0") else s
        df_disp["AVG"] = pd.to_numeric(df_disp["AVG"], errors="coerce").apply(_fmt_avg)

    # IP: 1 decimal (string). Missing => ""
    if "IP" in df_disp.columns:
        s = pd.to_numeric(df_disp["IP"], errors="coerce")
        df_disp["IP"] = s.apply(lambda x: "" if pd.isna(x) else f"{float(x):.1f}")

    # Replace pandas <NA> and literal "None"/"nan" strings with blanks (display-only)
    df_disp = df_disp.replace({pd.NA: ""})
    obj_cols = [c for c in df_disp.columns if df_disp[c].dtype == "object"]
    if obj_cols:
        df_disp[obj_cols] = df_disp[obj_cols].replace({"None": "", "nan": "", "NaN": "", None: ""})

    return df_disp


def render_available_players(state: DraftState) -> None:
    import pandas as pd
    import numpy as np
    from draftboard.state.league_profile import get_active_league_profile

    drafted = {ps.selected_player_key for ps in state.picks.values()
    if ps.selected_player_key and ps.selected_ts_iso is not None}

    profile = get_active_league_profile()
    features = dict(profile.get("features") or {})
    qo_enabled = bool(features.get("qualifying_offers", False))
    contracts_enabled = bool(features.get("contracts", False))
    pt_enabled = bool(features.get("prospect_tags", False))

    import os, psycopg
    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()

    st.subheader("Available Players")
    search = st.text_input("Search", value="", placeholder="Type a player name...")
    pos_options = [p.value for p in Position]
    pos_filter = st.multiselect("Filter by position", options=pos_options, default=[])

    toggle_labels = ["Show all players"]
    if qo_enabled:
        toggle_labels.extend(["Show only QOs", "Show only Poach-eligible"])
    if pt_enabled:
        toggle_labels.append("Show only PT")
    if contracts_enabled:
        toggle_labels.append("Show only Contracts")

    cols = st.columns(len(toggle_labels))
    i = 0

    with cols[i]:
        show_all_players = st.toggle("Show all players", value=False)
    i += 1

    show_qo = False
    show_poach = False
    show_pt = False
    show_contracts = False

    if qo_enabled:
        with cols[i]:
            show_qo = st.toggle("Show only QOs", value=False)
        i += 1
        with cols[i]:
            show_poach = st.toggle("Show only Poach-eligible", value=False)
        i += 1

    if pt_enabled:
        with cols[i]:
            show_pt = st.toggle("Show only PT", value=False)
        i += 1

    if contracts_enabled:
        with cols[i]:
            show_contracts = st.toggle("Show only Contracts", value=False)

    predraft_qo_keys: set[str] = set()
    predraft_qo_level_by_key: dict[str, int] = {}
    predraft_by_team = _load_predraft_qos_by_team(dsn, league_key, season_year) if (dsn and qo_enabled) else {}

    for _tk, rec in (predraft_by_team or {}).items():
        for lvl, pk in (rec.get("levels") or {}).items():
            if pk:
                predraft_qo_keys.add(str(pk))
                predraft_qo_level_by_key[str(pk)] = int(lvl)

    # Determine current round from the on-clock pick (used for "poach-eligible" filter)
    _cp = state.picks.get(state.clock.current_pick_id)
    current_round = int(_cp.round_number) if _cp else 0

    # --- Sort controls (single source of truth = session_state) ---
    sort_cols = ["Draft Pick", "Team Name", "Current Rank", "% Ros",
        "R", "HR", "RBI", "SB", "BB", "K (H)",
        "AVG", "IP", "W", "K (P)", "TB", "ERA", "WHIP", "QS", "SV+H",
        "Player Name", "Team", "Position"]
    if qo_enabled or pt_enabled or contracts_enabled:
        sort_cols.insert(1, "Contract/PT/QO")
    if contracts_enabled:
        sort_cols.insert(2, "Contract Years")

    # Set defaults BEFORE widget creation (and only once)
    default_sort = "Current Rank"
    st.session_state.setdefault("avail_sort_col", default_sort)
    st.session_state.setdefault("avail_sort_desc", False)

    # Widget gets its value from session_state via the key
    sort_col = st.selectbox("Sort by", options=sort_cols, key="avail_sort_col")

    sort_desc = st.toggle("Descending", key="avail_sort_desc")

    if st.button("Reset sort", key="avail_sort_reset"):
        st.session_state.pop("avail_sort_col", None)
        st.session_state.pop("avail_sort_desc", None)
        st.rerun()

    def matches_name(name: str) -> bool:
        return player_search_matches(search, name)

    def matches_positions(all_positions: list[str]) -> bool:
        if not pos_filter:
            return True
        return any(pp in pos_filter for pp in all_positions)

    contracted_keys = set() if not (contracts_enabled or pt_enabled or qo_enabled) else (st.session_state.get("contracted_keys", set()) or set())

    # Build available players list:
    # - default: exclude drafted + exclude contracted/PT
    # - BUT if "Show only QOs" is on, include drafted QOs too
    available_players = []
    for p in state.players.values():
        pk = str(p.player_key)

        # drafted filter:
        # - default: hide drafted
        # - show_all_players: include drafted
        # - show_qo: can also include drafted QOs if you ever want that view
        if pk in drafted and not show_all_players:
            if not (show_qo and pk in predraft_qo_keys):
                continue

        # contracted/PT filter (unless show_all_players is enabled)
        if (not show_all_players) and (pk in contracted_keys):
            # if show_qo is ON and player is a predraft QO, let it through (QO view wants all QOs)
            if not (show_qo and pk in predraft_qo_keys):
                continue

        # PT toggle
        if show_pt:
            pt_map = getattr(state, "pt_player_team_map", None) or {}
            if pk not in pt_map:
                continue

        # Contracts toggle (contracts only; PT has its own toggle)
        if show_contracts:
            # contract_years_2026 is loaded later in your function; use session_state cached version if present
            # We'll compute it later; for now we defer filter until after contract_years_2026 loads by using a placeholder.
            pass

        if not matches_name(p.name):
            continue

        all_pos_list = [_pos_label(x) for x in p.positions]
        if not matches_positions(all_pos_list):
            continue

        # Show only QOs = restrict to predraft QO list (public.qualifying_offer)
        if show_qo and (p.player_key not in predraft_qo_keys):
            continue

        # Show only Poach-eligible = predraft QOs whose level is strictly greater than the current round (1..5)
        if show_poach:
            cur_round = int(current_round or 0)
            # Only meaningful during QO rounds
            if 1 <= cur_round <= 5:
                lvl = predraft_qo_level_by_key.get(p.player_key)
                # eligible means: player's predraft QO level is strictly greater than current round
                if lvl is None or int(lvl) <= cur_round:
                    continue
        available_players.append(p)

    # ---- build deterministic mappings for the 3 new columns ----

    # 1) Contracts (years remaining) + PT
    # Canonical source (already loaded during ensure_initialized): session_state["contract_years_map"]
    # This avoids duplicate DB reads and prevents drift from unused legacy helper paths.
    contract_years_map = dict(st.session_state.get("contract_years_map", {}) or {}) if contracts_enabled else {}
    pt_map = (getattr(state, "pt_player_team_map", None) or {}) if pt_enabled else {}

    # Apply "Show only Contracts" filter now that we have contract years loaded.
    # Contract-only means: present in the canonical contract-years map and not PT.
    if show_contracts:
        filtered = []
        for p in available_players:
            pk = str(p.player_key)
            if pk in pt_map:
                continue
            if pk not in (contract_years_map or {}):
                continue
            filtered.append(p)
        available_players = filtered

    def _contract_label(yrs: int) -> str:
        if yrs == 1:
            return "1-year"
        return f"{yrs}-years"

    status_by_player_key: dict[str, str] = {}

    # PT overrides
    for pk in pt_map.keys():
        status_by_player_key[str(pk)] = "PT"

    # Contract years (only set if not already PT)
    for pk, yrs in (contract_years_map or {}).items():
        pk = str(pk)
        if pk in status_by_player_key:
            continue
        if yrs is None:
            continue
        status_by_player_key[pk] = _contract_label(int(yrs))

    # 2) Predraft QOs (source of truth: public.qualifying_offer via _load_predraft_qos_by_team)
    #    Also gives us QO team ownership for Team Name even if not placed on board.
    predraft_by_team = _load_predraft_qos_by_team(dsn, league_key, season_year) if (dsn and qo_enabled) else {}
    predraft_qo_level_by_key: dict[str, int] = {}
    predraft_qo_team_by_key: dict[str, str] = {}

    for tkey, rec in (predraft_by_team or {}).items():
        levels = (rec or {}).get("levels", {}) or {}
        for lvl, pk in levels.items():
            if not pk:
                continue
            pk = str(pk)
            predraft_qo_level_by_key[pk] = int(lvl)
            predraft_qo_team_by_key[pk] = str(tkey)
            # Only set status if not already PT/Contract
            if pk not in status_by_player_key:
                status_by_player_key[pk] = f"QO{int(lvl)}"

    # 3) Draft Pick + Team Name (ownership rules)
    # - Draft Pick: ONLY real picks (selected_ts_iso is not None)
    # - Team Name: real picks + keeper placeholders in rounds 6..25 (Contract/PT only)
    # - QO placeholders (rounds 1..5 with selected_ts_iso is None) do NOT count as ownership
    # - Undrafted predraft QOs should NOT show Team Name

    pick_order_index_by_pick_id = {pid: i for i, pid in enumerate(state.pick_order or [])}

    pt_map = getattr(state, "pt_player_team_map", None) or {}
    keeper_keys = set((contract_years_map or {}).keys()) | set((pt_map or {}).keys())

    team_name_by_player_key: dict[str, str] = {}
    draft_pick_label_by_player_key: dict[str, str] = {}
    draft_pick_sort_by_player_key: dict[str, int] = {}

    # A) Real picks -> Draft Pick + Team Name
    player_key_to_best_pick_id: dict[str, str] = {}
    player_key_to_best_order_idx: dict[str, int] = {}

    for ps in state.picks.values():
        pk = getattr(ps, "selected_player_key", None)
        if not pk:
            continue
        if getattr(ps, "selected_ts_iso", None) is None:
            continue  # ignore placeholders for Draft Pick

        pk = str(pk)
        pid = str(getattr(ps, "pick_id", "") or "")
        if not pid:
            continue
        idx = pick_order_index_by_pick_id.get(pid)
        if idx is None:
            continue

        prev = player_key_to_best_order_idx.get(pk)
        if prev is None or idx < prev:
            player_key_to_best_order_idx[pk] = idx
            player_key_to_best_pick_id[pk] = pid

    for pk, pid in player_key_to_best_pick_id.items():
        ps = state.picks.get(pid)
        if ps is None:
            continue

        r = int(getattr(ps, "round_number", 0) or 0)
        s = int(getattr(ps, "slot", 0) or 0)

        # label for real picks only
        rt = str(getattr(ps, "round_type", "") or "")
        is_qo_round = (rt == "QO" or rt.endswith("QO"))
        if is_qo_round:
            draft_pick_label_by_player_key[pk] = f"QO{r}.{s}"
        else:
            draft_pick_label_by_player_key[pk] = f"R{r:02d}.{s}"

        draft_pick_sort_by_player_key[pk] = int(player_key_to_best_order_idx.get(pk, 10**9))

        owner_team_key = str(getattr(ps, "owner_team_key", "") or "")
        tm = state.teams.get(owner_team_key)
        if tm:
            team_name_by_player_key[pk] = str(getattr(tm, "name", "") or "")

    # B) Keeper placeholders in rounds 6..25 -> Team Name ONLY (no Draft Pick)
    for ps in state.picks.values():
        pk = getattr(ps, "selected_player_key", None)
        if not pk:
            continue
        if getattr(ps, "selected_ts_iso", None) is not None:
            continue  # already a real pick
        r = int(getattr(ps, "round_number", 0) or 0)
        rt = str(getattr(ps, "round_type", "") or "")
        is_qo_round = (rt == "QO" or rt.endswith("QO"))
        if is_qo_round:
            continue  # never treat QO placeholders as ownership
        pk = str(pk)
        if pk not in keeper_keys:
            continue

        owner_team_key = str(getattr(ps, "owner_team_key", "") or "")
        tm = state.teams.get(owner_team_key)
        if tm:
            team_name_by_player_key.setdefault(pk, str(getattr(tm, "name", "") or ""))

    # Build df (now includes the 3 new columns, and NO Tags)
    df = _build_players_df(
        available_players,
        status_by_player_key=status_by_player_key,
        team_name_by_player_key=team_name_by_player_key,
        draft_pick_label_by_player_key=draft_pick_label_by_player_key,
    )

    # Add hidden sort key for Draft Pick sorting via the existing sort controls
    df["_draft_pick_sort"] = 10**9  # default large (works even if df has 0 rows/cols)
    if "Draft Pick" in df.columns:
        # map by player key: we need player_key column, so compute via name mapping is unsafe.
        # Instead, derive from current available_players list in the same order as df rows.
        # Deterministic: rebuild a parallel list of player_keys.
        player_keys_in_df = [str(p.player_key) for p in available_players]
        df["_draft_pick_sort"] = [
            int(draft_pick_sort_by_player_key.get(pk, 10**9)) for pk in player_keys_in_df
        ]

        # Hidden numeric sort for Contract Years (contracts only; PT/QO/blank = 0)
        df["_contract_years_sort"] = [
            int((contract_years_map or {}).get(pk, 0) or 0) for pk in player_keys_in_df
        ]

    # Sort with blanks always last
    if sort_col == "Draft Pick":
        df = df.sort_values("_draft_pick_sort", ascending=(not sort_desc), na_position="last", kind="mergesort")
    elif sort_col == "Contract Years":
        df = df.sort_values("_contract_years_sort", ascending=(not sort_desc), na_position="last", kind="mergesort")
    elif sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=(not sort_desc), na_position="last", kind="mergesort")

    # Display formatting (without breaking sorting, since sorting already happened)
    df_for_display = df.drop(columns=["_draft_pick_sort", "_contract_years_sort"], errors="ignore")

    # Hide feature-specific columns when the active league does not use them.
    cols_to_drop = []
    if not (qo_enabled or pt_enabled or contracts_enabled):
        cols_to_drop.append("Contract/PT/QO")
    if not contracts_enabled:
        cols_to_drop.append("Contract Years")

    if cols_to_drop:
        df_for_display = df_for_display.drop(columns=cols_to_drop, errors="ignore")

    df_disp = _format_players_df_for_display(df_for_display)

    st.caption(f"Available: {len(df_disp)}")

    # Display-only cleanup: blank out literal "None" strings in object columns (don’t touch Int64, floats, etc.)
    obj_cols = [c for c in df_disp.columns if str(df_disp[c].dtype) in ("object", "string")]
    if obj_cols:
        df_disp[obj_cols] = df_disp[obj_cols].replace(
            {None: "", "None": "", "nan": "", "NaN": "", "<NA>": "", pd.NA: ""}
        )

    st.dataframe(df_disp, use_container_width=True, height=720, hide_index=True)


def render_teams(state: DraftState, contract_years_2026: dict[str, int]) -> None:
    import pandas as pd

    st.subheader("Teams")

    order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
    teams = [state.teams[tk] for tk in order if tk in state.teams]
    if not teams:
        st.caption("No teams loaded.")
        return

    team_tabs = st.tabs([t.name for t in teams])

    # --- helpers ---
    HITTER_POS = {"C", "1B", "2B", "3B", "SS", "OF", "UTIL"}
    PITCHER_POS = {"SP", "RP", "P"}

    hitter_slots = ["C", "1B", "2B", "3B", "SS", "OF", "OF", "OF", "UTIL"]
    pitcher_slots = ["SP", "SP", "SP", "RP", "RP", "RP", "P", "P", "P"]
    BENCH_CAP = 7  # 25 total - 18 starters

    def _pos_str_list(p: Player) -> list[str]:
        return [_pos_label(x) for x in p.positions]
    def _display_positions(p: Player) -> str:
        pos = _pos_str_list(p)
        # Batters: drop UTIL unless UTIL-only
        if not _is_pitcher(p):
            if "UTIL" in pos and len(pos) > 1:
                pos = [x for x in pos if x != "UTIL"]
            return "/".join(pos)
        # Pitchers: drop P unless P-only
        else:
            if "P" in pos and len(pos) > 1:
                pos = [x for x in pos if x != "P"]
            return "/".join(pos)

    def _is_pitcher(p: Player) -> bool:
        pos = set(_pos_str_list(p))
        # Treat as pitcher only if it has pitcher positions and no hitter positions
        return bool(pos & PITCHER_POS) and not bool(pos & (HITTER_POS - {"UTIL"}))

    def _eligible_for_slot(p: Player, slot: str, is_hitter: bool) -> bool:
        pos = set(_pos_str_list(p))
        if is_hitter:
            if slot == "UTIL":
                return not _is_pitcher(p)
            return slot in pos
        else:
            if slot == "P":
                return _is_pitcher(p)
            return slot in pos

    def _sort_key(p: Player):
        # Lower rank_value is better; blanks last
        rv = getattr(p, "rank_value", None)
        return (rv is None, rv if rv is not None else 10**9, p.name)

    def _contract_display(p: Player) -> str:
         # PT overrides everything
        if getattr(state, "pt_player_team_map", None) and p.player_key in state.pt_player_team_map:
            return "PT"
        yrs = contract_years_2026.get(p.player_key)
        return str(yrs) if yrs is not None else ""

    def _hitter_row(slot_label: str, p: Player | None) -> dict:
        if p is None:
            return {
                "POS": slot_label,
                "Batters": "",
                "Team": "",
                "Position": "",
                "Contract": "",
                "Rank": "",
                "% Ros": "",
                "H/AB": "",
                "R": "",
                "HR": "",
                "RBI": "",
                "SB": "",
                "BB": "",
                "K (H)": "",
                "AVG": "",
            }

        return {
            "POS": slot_label,
            "Batters": p.name,
            "Team": getattr(p, "mlb_team", ""),
            "Position": _display_positions(p),
            "Contract": _contract_display(p),
            "Rank": getattr(p, "rank_value", None),
            "% Ros": getattr(p, "percent_owned", None),
            "H/AB": getattr(p, "h_ab", None),
            "R": getattr(p, "r", None),
            "HR": getattr(p, "hr", None),
            "RBI": getattr(p, "rbi", None),
            "SB": getattr(p, "sb", None),
            "BB": getattr(p, "bb", None),
            "K (H)": getattr(p, "k_hit", None),
            "AVG": getattr(p, "avg", None),
        }

    def _pitcher_row(slot_label: str, p: Player | None) -> dict:
        if p is None:
            return {
                "POS": slot_label,
                "Pitchers": "",
                "Team": "",
                "Position": "",
                "Contract": "",
                "Rank": "",
                "% Ros": "",
                "IP": "",
                "W": "",
                "K (P)": "",
                "TB": "",
                "ERA": "",
                "WHIP": "",
                "QS": "",
                "SV+H": "",
            }

        return {
            "POS": slot_label,
            "Pitchers": p.name,
            "Team": getattr(p, "mlb_team", ""),
            "Position": _display_positions(p),
            "Contract": _contract_display(p),
            "Rank": getattr(p, "rank_value", None),
            "% Ros": getattr(p, "percent_owned", None),
            "IP": getattr(p, "ip", None),
            "W": getattr(p, "w", None),
            "K (P)": getattr(p, "k_pit", None),
            "TB": getattr(p, "tb", None),
            "ERA": getattr(p, "era", None),
            "WHIP": getattr(p, "whip", None),
            "QS": getattr(p, "qs", None),
            "SV+H": getattr(p, "sv_h", None),
        }

    # Pre-index picks by owner (round/slot order)
    picks_by_owner: dict[str, list[PickSlot]] = {}
    for pick in state.picks.values():
        picks_by_owner.setdefault(pick.owner_team_key, []).append(pick)
    for k in picks_by_owner:
        picks_by_owner[k].sort(key=lambda p: (p.round_number, p.slot))

    for tab, team in zip(team_tabs, teams):
        with tab:
            team_picks = picks_by_owner.get(team.team_key, [])

            pt_map = getattr(state, "pt_player_team_map", None) or {}
            keeper_keys = set(contract_years_2026.keys()) | set(pt_map.keys())

            def _include_on_teams_tab(ps: PickSlot) -> bool:
                if not ps.selected_player_key:
                    return False
                # Real drafted pick (including QO rounds once drafted)
                if ps.selected_ts_iso is not None:
                    return True
                # Keeper-prefill only (standard rounds only; exclude QO placeholders)
                if ps.round_number > 5 and ps.selected_player_key in keeper_keys:
                    return True
                return False

            team_keys_in_order = [p.selected_player_key for p in team_picks if _include_on_teams_tab(p)]
            roster_players = [state.players[k] for k in team_keys_in_order if k in state.players]

            st.caption(f"Rostered: {len(roster_players)} / 25  •  Bench capacity: {BENCH_CAP}")

            if not roster_players:
                st.caption("No players assigned to this team yet.")
                continue
            # --- display-only lineup overrides (session state) ---
            # Keyed by team_key only (no draft_key currently visible in ui/app.py)
            if "lineup_overrides" not in st.session_state:
                st.session_state["lineup_overrides"] = {}

            team_override = st.session_state["lineup_overrides"].setdefault(team.team_key, {})
            def _label_to_player(sel_label: str) -> Player | None:
                if not sel_label:
                    return None
                pk = label_to_key.get(sel_label)
                if not pk:
                    return None
                return state.players.get(pk)

            def _consume(pool: list[Player], p: Player) -> None:
                # remove player from pool if present (avoid duplicates)
                for i, cand in enumerate(pool):
                    if cand.player_key == p.player_key:
                        pool.pop(i)
                        return

            # Build eligible choices per slot (include "" for open)
            def _choice_label(p: Player) -> str:
                return f"{p.name} ({_display_positions(p)})"

            roster_choices = {p.player_key: _choice_label(p) for p in roster_players}
            label_to_key = {v: k for k, v in roster_choices.items()}
            all_labels = [""] + sorted(roster_choices.values())

            # Compute eligible label lists for hitter/pitcher slots
            def _eligible_labels_for(slot: str, is_hitter: bool) -> list[str]:
                labels = [""]
                for p in roster_players:
                    if _eligible_for_slot(p, slot, is_hitter):
                        labels.append(roster_choices[p.player_key])
                return sorted(set(labels))

            with st.expander("Lineup (display-only): swap players into slots", expanded=False):
                st.caption("These changes only affect the Teams view in this browser session (no DB writes).")

                colA, colB = st.columns(2)
                
                def _apply_override(slot_key: str, sel_label: str) -> None:
                    # "move" semantics: a player label can exist in only one override slot per team
                    if sel_label:
                        for k, v in list(team_override.items()):
                            if k != slot_key and v == sel_label:
                                team_override[k] = ""
                    team_override[slot_key] = sel_label

                with colA:
                    st.markdown("**Hitters slots**")
                    cols = st.columns(3)
                    for i, slot in enumerate(hitter_slots):
                        with cols[i % 3]:
                            keyname = f"ovr_{team.team_key}_H_{slot}_{i}"
                            opts = _eligible_labels_for(slot, True)
                            current = team_override.get(f"H:{slot}:{i}", "")
                            sel = st.selectbox(slot, options=opts, index=opts.index(current) if current in opts else 0, key=keyname)
                            _apply_override(f"H:{slot}:{i}", sel)

                    st.markdown("**Hitter Bench (BN)**")
                    for i in range(BENCH_CAP):
                        slot = f"BN{i+1}"
                        keyname = f"ovr_{team.team_key}_H_BN_{i}"
                        opts = [""] + sorted([roster_choices[p.player_key] for p in roster_players if not _is_pitcher(p)])
                        current = team_override.get(f"H:BN:{i}", "")
                        sel = st.selectbox(slot, options=opts, index=opts.index(current) if current in opts else 0, key=keyname)
                        _apply_override(f"H:BN:{i}", sel)

                with colB:
                    st.markdown("**Pitchers slots**")
                    cols = st.columns(3)
                    for i, slot in enumerate(pitcher_slots):
                        with cols[i % 3]:
                            keyname = f"ovr_{team.team_key}_P_{slot}_{i}"
                            opts = _eligible_labels_for(slot, False)
                            current = team_override.get(f"P:{slot}:{i}", "")
                            sel = st.selectbox(slot, options=opts, index=opts.index(current) if current in opts else 0, key=keyname)
                            _apply_override(f"P:{slot}:{i}", sel)

                    st.markdown("**Pitcher Bench (BN)**")
                    for i in range(BENCH_CAP):
                        slot = f"BN{i+1}"
                        keyname = f"ovr_{team.team_key}_P_BN_{i}"
                        opts = [""] + sorted([roster_choices[p.player_key] for p in roster_players if _is_pitcher(p)])
                        current = team_override.get(f"P:BN:{i}", "")
                        sel = st.selectbox(slot, options=opts, index=opts.index(current) if current in opts else 0, key=keyname)
                        _apply_override(f"P:BN:{i}", sel)

                if st.button("Clear lineup overrides for this team", key=f"clear_ovr_{team.team_key}"):
                    st.session_state["lineup_overrides"][team.team_key] = {}
                    st.rerun()
                
            # Split players
            hitters = [p for p in roster_players if not _is_pitcher(p)]
            pitchers = [p for p in roster_players if _is_pitcher(p)]

            # Deterministic fill: best rank first
            hitters_pool = sorted(hitters, key=_sort_key)
            pitchers_pool = sorted(pitchers, key=_sort_key)

            # Assign starters
            hitter_assigned: list[Player] = []
            pitcher_assigned: list[Player] = []

            def _take_best(pool: list[Player], slot: str, is_hitter: bool) -> Player | None:
                for i, cand in enumerate(pool):
                    if _eligible_for_slot(cand, slot, is_hitter):
                        return pool.pop(i)
                return None

            # Collect ALL valid explicit overrides first, then consume from pools before any auto-fill.
            hitter_override_by_slot: dict[tuple[str, int], Player] = {}
            pitcher_override_by_slot: dict[tuple[str, int], Player] = {}
            hitter_bench_override_by_idx: dict[int, Player] = {}
            pitcher_bench_override_by_idx: dict[int, Player] = {}

            # Hitter starter overrides
            for i, slot in enumerate(hitter_slots):
                sel = team_override.get(f"H:{slot}:{i}", "")
                p = _label_to_player(sel)
                if p is not None and _eligible_for_slot(p, slot, True):
                    hitter_override_by_slot[(slot, i)] = p

            # Pitcher starter overrides
            for i, slot in enumerate(pitcher_slots):
                sel = team_override.get(f"P:{slot}:{i}", "")
                p = _label_to_player(sel)
                if p is not None and _eligible_for_slot(p, slot, False):
                    pitcher_override_by_slot[(slot, i)] = p

            # Hitter bench overrides
            for i in range(BENCH_CAP):
                sel = team_override.get(f"H:BN:{i}", "")
                p = _label_to_player(sel)
                if p is not None and not _is_pitcher(p):
                    hitter_bench_override_by_idx[i] = p

            # Pitcher bench overrides
            for i in range(BENCH_CAP):
                sel = team_override.get(f"P:BN:{i}", "")
                p = _label_to_player(sel)
                if p is not None and _is_pitcher(p):
                    pitcher_bench_override_by_idx[i] = p

            # Consume every explicit override before any auto-fill so duplicates cannot occur.
            consumed_hitter_keys: set[str] = set()
            for p in list(hitter_override_by_slot.values()) + list(hitter_bench_override_by_idx.values()):
                pk = str(p.player_key)
                if pk in consumed_hitter_keys:
                    continue
                _consume(hitters_pool, p)
                consumed_hitter_keys.add(pk)

            consumed_pitcher_keys: set[str] = set()
            for p in list(pitcher_override_by_slot.values()) + list(pitcher_bench_override_by_idx.values()):
                pk = str(p.player_key)
                if pk in consumed_pitcher_keys:
                    continue
                _consume(pitchers_pool, p)
                consumed_pitcher_keys.add(pk)

            # Starter rows
            hitter_rows = []
            for i, slot in enumerate(hitter_slots):
                p = hitter_override_by_slot.get((slot, i))
                if p is None:
                    p = _take_best(hitters_pool, slot, True)
                if p:
                    hitter_assigned.append(p)
                hitter_rows.append(_hitter_row(slot, p))

            pitcher_rows = []
            for i, slot in enumerate(pitcher_slots):
                p = pitcher_override_by_slot.get((slot, i))
                if p is None:
                    p = _take_best(pitchers_pool, slot, False)
                if p:
                    pitcher_assigned.append(p)
                pitcher_rows.append(_pitcher_row(slot, p))

            # Remaining players -> bench (cap total = 7)
            hitter_bench = hitters_pool[:]    # remaining hitters
            pitcher_bench = pitchers_pool[:]  # remaining pitchers

            bench_used = 0

            # 1) Hitter BN overrides
            for i in range(BENCH_CAP):
                if bench_used >= BENCH_CAP:
                    break
                p = hitter_bench_override_by_idx.get(i)
                if p is None:
                    continue
                hitter_rows.append(_hitter_row("BN", p))
                bench_used += 1

            # 2) Pitcher BN overrides
            for i in range(BENCH_CAP):
                if bench_used >= BENCH_CAP:
                    break
                p = pitcher_bench_override_by_idx.get(i)
                if p is None:
                    continue
                pitcher_rows.append(_pitcher_row("BN", p))
                bench_used += 1

            # 3) Auto-fill remaining bench: hitters then pitchers
            while bench_used < BENCH_CAP and hitter_bench:
                p = hitter_bench.pop(0)
                hitter_rows.append(_hitter_row("BN", p))
                bench_used += 1

            while bench_used < BENCH_CAP and pitcher_bench:
                p = pitcher_bench.pop(0)
                pitcher_rows.append(_pitcher_row("BN", p))
                bench_used += 1

            # 4) If still short, show open BN rows under hitters (keeps stable layout)
            while bench_used < BENCH_CAP:
                hitter_rows.append(_hitter_row("BN", None))
                bench_used += 1
                
            # --- render ---
            st.markdown(
                """
                <style>
                  .teams-table-wrap {
                    width: 100%;
                    overflow-x: auto;
                  }
                  table.teams-table {
                    width: 100%;
                    border-collapse: collapse;
                    table-layout: auto;
                  }
                  table.teams-table th,
                  table.teams-table td {
                    white-space: nowrap;
                  }
                </style>
                """,
                unsafe_allow_html=True,
            )

            st.markdown("### Hitters")
            hitter_df = pd.DataFrame(hitter_rows)
            hitter_df.insert(0, "#", range(1, len(hitter_df) + 1))

            # Coerce numeric columns
            for c in ["Rank", "% Ros", "R", "HR", "RBI", "SB", "BB", "K (H)"]:
                if c in hitter_df.columns:
                    hitter_df[c] = pd.to_numeric(hitter_df[c], errors="coerce")

            if "AVG" in hitter_df.columns:
                hitter_df["AVG"] = pd.to_numeric(hitter_df["AVG"], errors="coerce")

            # Display formatting: integers for rank/ownership/counting stats
            for c in ["Rank", "% Ros", "R", "HR", "RBI", "SB", "BB", "K (H)"]:
                if c in hitter_df.columns:
                    hitter_df[c] = hitter_df[c].round(0).astype("Int64").astype("string")

            # AVG: .300 (no leading zero)
            if "AVG" in hitter_df.columns:
                def _fmt_avg(x):
                    if pd.isna(x):
                        return ""
                    s = f"{float(x):.3f}"
                    return s[1:] if s.startswith("0") else s
                hitter_df["AVG"] = hitter_df["AVG"].apply(_fmt_avg)

            # Remove <NA> noise
            hitter_disp = hitter_df.fillna("").replace({pd.NA: ""})
            st.markdown(
                f'<div class="teams-table-wrap">{hitter_disp.to_html(index=False, escape=False, classes="teams-table")}</div>',
                unsafe_allow_html=True,
            )

            st.markdown("### Pitchers")
            pitcher_df = pd.DataFrame(pitcher_rows)
            pitcher_df.insert(0, "#", range(len(hitter_rows) + 1, len(hitter_rows) + len(pitcher_df) + 1))

            # Coerce numeric columns
            for c in ["Rank", "% Ros", "W", "K (P)", "TB", "QS", "SV+H"]:
                if c in pitcher_df.columns:
                    pitcher_df[c] = pd.to_numeric(pitcher_df[c], errors="coerce")

            for c in ["IP", "ERA", "WHIP"]:
                if c in pitcher_df.columns:
                    pitcher_df[c] = pd.to_numeric(pitcher_df[c], errors="coerce")

            # Display formatting: integers
            for c in ["Rank", "% Ros", "W", "K (P)", "TB", "QS", "SV+H"]:
                if c in pitcher_df.columns:
                    pitcher_df[c] = pitcher_df[c].round(0).astype("Int64").astype("string")

            # IP: 1 decimal (e.g., 110.2)
            if "IP" in pitcher_df.columns:
                pitcher_df["IP"] = pitcher_df["IP"].apply(lambda x: "" if pd.isna(x) else f"{float(x):.1f}")

            # ERA/WHIP: 2 decimals (e.g., 3.09 / 0.97)
            for c in ["ERA", "WHIP"]:
                if c in pitcher_df.columns:
                    pitcher_df[c] = pitcher_df[c].apply(lambda x: "" if pd.isna(x) else f"{float(x):.2f}")

            # Remove <NA> noise
            pitcher_disp = pitcher_df.fillna("").replace({pd.NA: ""})
            st.markdown(
                f'<div class="teams-table-wrap">{pitcher_disp.to_html(index=False, escape=False, classes="teams-table")}</div>',
                unsafe_allow_html=True,
            )



def _load_predraft_qos_by_team(dsn: str, league_key: str, season_year: int) -> dict:
    # Returns:
    #   { TEAM_XX: { "levels": {1: pk, .., 5: pk}, "updated_at": "YYYY-MM-DD HH:MM:SS" } }
    # Source of truth: public.qualifying_offer (predraft selections)
    out: dict = {}
    for team_key, lvl, pkey, updated_at in _load_predraft_qo_rows(dsn, league_key, season_year):
        rec = out.setdefault(str(team_key), {"levels": {}, "updated_at": None})
        rec["levels"][int(lvl)] = str(pkey)

        if updated_at is not None:
            ts = str(updated_at).split("+")[0]
            if rec["updated_at"] is None or ts > rec["updated_at"]:
                rec["updated_at"] = ts
    return out

def _fmt_qo_cell(state, yahoo_player_key: str) -> str:
    if not yahoo_player_key:
        return ""
    p = state.players.get(yahoo_player_key)
    if not p:
        return ""
    tm = getattr(p, "mlb_team", "") or ""
    try:
        pos = p.primary_position.value
    except Exception:
        pos = ""
    if tm and pos:
        return f"{p.name} ({tm}) {pos}"
    if tm:
        return f"{p.name} ({tm})"
    return f"{p.name}"


def render_qos_tab(state) -> None:
    import os
    import pandas as pd
    import streamlit as st

    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()

    owner_name_by_team_key: dict[str, str] = {}
    if dsn:
        from draftboard.data.db_players import load_yahoo_team_map
        yahoo_team_rows_for_owner = load_yahoo_team_map(dsn, league_key, season_year)
        owner_name_by_team_key = {
            str(r.get("team_key") or "").strip(): str(r.get("owner_name") or "").strip()
            for r in (yahoo_team_rows_for_owner or [])
            if str(r.get("team_key") or "").strip()
        }

    def _fmt_updated(ts: str) -> str:
        s = str(ts or "").strip()
        if not s:
            return ""
        s = s.split("+")[0]
        s = s.split(".")[0]
        return s

    predraft_by_team = _load_predraft_qos_by_team(dsn, league_key, season_year) if dsn else {}

    # Convert predraft_by_team -> {team_key: {lvl: pk}}
    predraft_levels: dict[str, dict[int, str]] = {}
    predraft_updated_at: dict[str, str] = {}
    order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
    legacy_to_canon = dict(st.session_state.get("legacy_to_canonical_team_key_map", {}) or {})

    for tk, rec in (predraft_by_team or {}).items():
        levels = (rec or {}).get("levels", {}) or {}

        canon_tk = _canon_team_key_from_mixed_key(str(tk), order=order, legacy_to_canon=legacy_to_canon)
        if not canon_tk:
            continue

        predraft_levels[str(canon_tk)] = {int(lvl): str(pk) for lvl, pk in levels.items() if pk}
        predraft_updated_at[str(canon_tk)] = str((rec or {}).get("updated_at") or "")

    # Current QO state = predraft + replayed POACH events.
    # IMPORTANT: Do NOT use board pick slots to populate this table.
    current_levels = _compute_current_qos_from_log(predraft_levels, state.pick_log)

    def _updated_ts_for_team(team_key: str) -> str:
        latest = ""
        for e in state.pick_log:
            if e.pick_kind != "POACH":
                continue
            if e.ts_iso:
                latest = max(latest, e.ts_iso)
        return latest or predraft_updated_at.get(team_key, "")

    # Canonical display order = draft slot order (SSOT-driven)
    order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
    teams_in_order = [state.teams[tk] for tk in order if tk in state.teams]

    current_rows = []
    predraft_rows = []

    for t in teams_in_order:
        tkey = t.team_key
        team_name = t.name
        pass

        cur_lvls = current_levels.get(tkey, {}) or {}
        pre_lvls = predraft_levels.get(tkey, {}) or {}

        current_rows.append(
            {
                "Owner": owner_name_by_team_key.get(tkey, ""),
                "Team": team_name,
                "QO1": _fmt_qo_cell(state, cur_lvls.get(1, "")),
                "QO2": _fmt_qo_cell(state, cur_lvls.get(2, "")),
                "QO3": _fmt_qo_cell(state, cur_lvls.get(3, "")),
                "QO4": _fmt_qo_cell(state, cur_lvls.get(4, "")),
                "QO5": _fmt_qo_cell(state, cur_lvls.get(5, "")),
                "Updated": _fmt_updated(_updated_ts_for_team(tkey)),
            }
        )

        predraft_rows.append(
            {
                "Owner": owner_name_by_team_key.get(tkey, ""),
                "Team": team_name,
                "QO1": _fmt_qo_cell(state, pre_lvls.get(1, "")),
                "QO2": _fmt_qo_cell(state, pre_lvls.get(2, "")),
                "QO3": _fmt_qo_cell(state, pre_lvls.get(3, "")),
                "QO4": _fmt_qo_cell(state, pre_lvls.get(4, "")),
                "QO5": _fmt_qo_cell(state, pre_lvls.get(5, "")),
                "Updated": _fmt_updated(predraft_updated_at.get(tkey, "")),
            }
        )

    current_df = pd.DataFrame(current_rows)
    current_df.insert(0, "#", range(1, len(current_df) + 1))

    predraft_df = pd.DataFrame(predraft_rows)
    predraft_df.insert(0, "#", range(1, len(predraft_df) + 1))

    st.subheader("Current QOs")
    st.caption("Derived from Predraft QOs + replayed POACH events (not from draft picks).")
    st.markdown(current_df.to_html(index=False, escape=False), unsafe_allow_html=True)

    st.subheader("Predraft selections")
    st.markdown(predraft_df.to_html(index=False, escape=False), unsafe_allow_html=True)

def render_pick_log(state: DraftState) -> None:
    st.subheader("Pick Log")
    if not state.pick_log:
        st.caption("No picks yet.")
        return
    for e in reversed(state.pick_log):
        st.write(
            f"{e.ts_iso} — {e.pick_id} — {e.owner_team_key} — "
            f"{e.player_name} ({_pos_label(e.primary_position)}) [{e.pick_kind}]"
        )


def render_pick_tracker(state: DraftState, owner_name_by_team_key: dict[str, str]) -> None:
    st.subheader("Pick Tracker")

    if not state.pick_log:
        st.caption("No picks yet.")
        return

    rows = []
    for idx, entry in enumerate(state.pick_log, start=1):
        pick = state.picks.get(entry.pick_id)
        team = state.teams.get(entry.owner_team_key)

        if pick and pick.round_number <= 5:
            round_label = f"QO{pick.round_number}"
        else:
            round_label = str(pick.round_number) if pick else ""

        rows.append(
            {
                "Owner": owner_name_by_team_key.get(entry.owner_team_key, ""),
                "Team Name": team.name if team else "",
                "Round": round_label,
                "Pick #": pick.slot if pick else "",
                "Overall #": idx,
                "QO": "Y" if entry.pick_kind == "QO" else "",
                "Poach": "Y" if entry.pick_kind == "POACH" else "",
                "Player Name": entry.player_name,
                "Pos": _pos_label(entry.primary_position),
                "Team": state.players[entry.player_key].mlb_team,
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)

def _clear_local_auth_session() -> None:
    for k in [
        "auth_is_authenticated",
        "auth_user_id",
        "auth_email",
        "auth_is_site_admin",
        "auth_league_role",
        "auth_franchise_id",
        "auth_team_key",
        "auth_team_name",
        "auth_must_change_password",
    ]:
        st.session_state.pop(k, None)


def _get_auth_context() -> dict[str, object]:
    return {
        "is_authenticated": bool(st.session_state.get("auth_is_authenticated", False)),
        "user_id": st.session_state.get("auth_user_id"),
        "email": st.session_state.get("auth_email"),
        "is_site_admin": bool(st.session_state.get("auth_is_site_admin", False)),
        "league_role": st.session_state.get("auth_league_role"),
        "franchise_id": st.session_state.get("auth_franchise_id"),
        "team_key": st.session_state.get("auth_team_key"),
        "team_name": st.session_state.get("auth_team_name"),
        "must_change_password": bool(st.session_state.get("auth_must_change_password", False)),
    }

def _auth_cookie_secret() -> str:
    return str(
    os.environ.get("AUTH_COOKIE_SECRET")
    or os.environ.get("MLF_AUTH_COOKIE_SECRET", "")
    or ""
)


def _auth_session_ttl_days() -> int:
    return 30


def _new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _read_auth_cookie_session_token() -> str | None:
    secret = _auth_cookie_secret()
    if not secret:
        return None

    try:
        raw = st.context.cookies.get("mlf_auth")
    except Exception:
        return None

    token = str(raw or "").strip()
    if not token:
        return None

    return token


def _create_auth_session(*, user_id: int) -> str | None:
    dsn = get_postgres_dsn()
    if not dsn:
        return None

    session_token = _new_session_token()
    expires_at_utc = datetime.utcnow() + timedelta(days=_auth_session_ttl_days())

    sql = """
        INSERT INTO public.auth_session
          (session_token, user_id, created_at_utc, expires_at_utc, revoked_at_utc)
        VALUES
          (%s, %s, now(), %s, NULL)
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (session_token, int(user_id), expires_at_utc))
            conn.commit()
        return session_token
    except Exception:
        return None


def _create_auth_handoff_code(*, session_token: str, ttl_seconds: int = 60) -> str | None:
    dsn = get_postgres_dsn()
    if not dsn:
        return None

    token = str(session_token or "").strip()
    if not token:
        return None

    handoff_code = secrets.token_urlsafe(24)

    sql = """
        insert into public.auth_handoff_code
          (handoff_code, session_token, created_at_utc, expires_at_utc, consumed_at_utc)
        values
          (%s, %s, now(), now() + (%s || ' seconds')::interval, null)
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (handoff_code, token, int(ttl_seconds)))
            conn.commit()
        return handoff_code
    except Exception:
        return None


def _auth_bridge_set_url(*, handoff_code: str, next_url: str = "/") -> str:
    code = str(handoff_code or "").strip()
    nxt = str(next_url or "/").strip() or "/"
    return f"/auth/set?code={code}&next={nxt}"


def _auth_bridge_clear_url(*, next_url: str = "/") -> str:
    nxt = str(next_url or "/").strip() or "/"
    return f"/auth/clear?next={nxt}"


def _get_auth_session_user_id(*, session_token: str) -> int | None:
    dsn = get_postgres_dsn()
    if not dsn:
        return None

    token = str(session_token or "").strip()
    if not token:
        return None

    sql = """
        SELECT user_id
        FROM public.auth_session
        WHERE session_token = %s
          AND revoked_at_utc IS NULL
          AND expires_at_utc > now()
        LIMIT 1
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (token,))
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _revoke_auth_session(*, session_token: str) -> None:
    dsn = get_postgres_dsn()
    if not dsn:
        return

    token = str(session_token or "").strip()
    if not token:
        return

    sql = """
        UPDATE public.auth_session
        SET revoked_at_utc = now()
        WHERE session_token = %s
          AND revoked_at_utc IS NULL
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (token,))
            conn.commit()
    except Exception:
        return


def _delete_expired_auth_sessions() -> None:
    dsn = get_postgres_dsn()
    if not dsn:
        return

    sql = """
        DELETE FROM public.auth_session
        WHERE expires_at_utc <= now()
           OR revoked_at_utc IS NOT NULL
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
    except Exception:
        return

def _get_client_ip_for_login_attempt() -> str | None:
    """
    Best-effort placeholder for light protection.
    Streamlit does not expose client IP cleanly in this app today.
    """
    return None


def _record_login_attempt(*, email_attempted: str, ip_address: str | None, success: bool) -> None:
    dsn = get_postgres_dsn()
    if not dsn:
        return

    sql = """
        INSERT INTO public.auth_login_attempt
          (email_attempted, ip_address, success, attempt_ts)
        VALUES
          (%s, %s, %s, now())
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(email_attempted or "").strip().lower(),
                        (str(ip_address).strip() if ip_address else None),
                        bool(success),
                    ),
                )
            conn.commit()
    except Exception:
        # Light protection only; never break login flow because audit insert failed.
        return


def _count_recent_failed_login_attempts(*, email_attempted: str, ip_address: str | None, window_minutes: int = 10) -> int:
    dsn = get_postgres_dsn()
    if not dsn:
        return 0

    email_norm = str(email_attempted or "").strip().lower()
    ip_norm = (str(ip_address).strip() if ip_address else None)

    if ip_norm:
        sql = """
            SELECT count(*)
            FROM public.auth_login_attempt
            WHERE success = false
              AND attempt_ts >= (now() - make_interval(mins => %s))
              AND (
                    email_attempted = %s
                    OR ip_address = %s
              )
        """
        params = (int(window_minutes), email_norm, ip_norm)
    else:
        sql = """
            SELECT count(*)
            FROM public.auth_login_attempt
            WHERE success = false
              AND attempt_ts >= (now() - make_interval(mins => %s))
              AND email_attempted = %s
        """
        params = (int(window_minutes), email_norm)

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _is_login_rate_limited(*, email_attempted: str, ip_address: str | None, max_failures: int = 5, window_minutes: int = 10) -> bool:
    failures = _count_recent_failed_login_attempts(
        email_attempted=email_attempted,
        ip_address=ip_address,
        window_minutes=window_minutes,
    )
    return failures >= int(max_failures)

def _load_local_auth_principal_by_user_id(*, user_id: int, league_key: str, season_year: int) -> dict | None:
    dsn = get_postgres_dsn()
    if not dsn:
        st.error("Missing env var MLF_POSTGRES_DSN inside DraftBoard container.")
        return None

    sql = """
        SELECT
            u.user_id,
            u.email_normalized,
            u.password_hash,
            u.active AS user_active,
            u.must_change_password,
            u.is_site_admin,
            r.role_code,
            r.active AS role_active,
            r.franchise_id,
            fst.team_key,
            fst.team_name
        FROM public.auth_user u
        LEFT JOIN public.auth_user_league_role r
          ON r.user_id = u.user_id
         AND r.league_key = %s
         AND r.active = true
        LEFT JOIN public.franchise_season_team fst
          ON fst.franchise_id = r.franchise_id
         AND fst.league_key = r.league_key
         AND fst.season_year = %s
        WHERE u.user_id = %s
        LIMIT 1
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (league_key, season_year, int(user_id)))
                row = cur.fetchone()
                if not row:
                    return None

                return {
                    "user_id": int(row[0]),
                    "email_normalized": str(row[1]),
                    "password_hash": str(row[2]),
                    "user_active": bool(row[3]),
                    "must_change_password": bool(row[4]),
                    "is_site_admin": bool(row[5]),
                    "role_code": str(row[6]) if row[6] is not None else None,
                    "role_active": bool(row[7]) if row[7] is not None else False,
                    "franchise_id": int(row[8]) if row[8] is not None else None,
                    "team_key": str(row[9]) if row[9] is not None else None,
                    "team_name": str(row[10]) if row[10] is not None else None,
                }
    except Exception as e:
        st.error(f"Failed to load local auth principal by user_id: {e}")
        return None

def _set_local_user_password(*, user_id: int, new_password: str) -> bool:
    dsn = get_postgres_dsn()
    if not dsn:
        st.error("Missing env var MLF_POSTGRES_DSN inside DraftBoard container.")
        return False

    pw = str(new_password or "")
    if len(pw) < 10:
        st.error("New password must be at least 10 characters.")
        return False

    try:
        password_hash = bcrypt.hashpw(
            pw.encode("utf-8"),
            bcrypt.gensalt(rounds=12),
        ).decode("utf-8")
    except Exception as e:
        st.error(f"Failed to hash password: {e}")
        return False

    sql = """
        UPDATE public.auth_user
        SET password_hash = %s,
            must_change_password = false
        WHERE user_id = %s
          AND active = true
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (password_hash, int(user_id)))
                updated = int(cur.rowcount or 0)
            conn.commit()
    except Exception as e:
        st.error(f"Failed to update password: {e}")
        return False

    if updated != 1:
        st.error("Password update did not affect exactly one active user.")
        return False

    return True

def _render_must_change_password_gate() -> bool:
    auth = _get_auth_context()

    if not auth["is_authenticated"]:
        return False

    if not auth.get("must_change_password", False):
        return False

    st.warning("You must change your temporary password before using the DraftBoard.")

    with st.form("force_password_change_form"):
        new_pw = st.text_input("New password", type="password", key="force_pw_1")
        new_pw_2 = st.text_input("Re-enter new password", type="password", key="force_pw_2")
        submitted = st.form_submit_button("Set new password", type="primary")

        if submitted:
            if not new_pw or not new_pw_2:
                st.error("Enter the new password in both fields.")
                return True

            if new_pw != new_pw_2:
                st.error("The passwords do not match.")
                return True

            if len(new_pw) < 10:
                st.error("New password must be at least 10 characters.")
                return True

            if _set_local_user_password(user_id=int(auth["user_id"]), new_password=new_pw):
                st.session_state["auth_must_change_password"] = False
                st.success("Password changed successfully.")
                st.rerun()

    return True

def _user_can_submit_pick(state: DraftState) -> bool:
    """
    Determines if the current authenticated user may submit
    a pick for the currently on-clock team.
    """

    auth = _get_auth_context()

    if not auth["is_authenticated"]:
        return False

    # Site admin always allowed
    if auth["is_site_admin"]:
        return True

    current_pick_id = state.clock.current_pick_id
    pick = state.picks.get(current_pick_id)

    if not pick:
        return False

    owner_team_key = str(pick.owner_team_key)

    return auth.get("team_key") == owner_team_key

def _load_local_auth_principal(*, email_normalized: str, league_key: str, season_year: int) -> dict | None:
    dsn = get_postgres_dsn()
    if not dsn:
        st.error("Missing env var MLF_POSTGRES_DSN inside DraftBoard container.")
        return None

    sql = """
        SELECT
            u.user_id,
            u.email_normalized,
            u.password_hash,
            u.active AS user_active,
            u.must_change_password,
            u.is_site_admin,
            r.role_code,
            r.active AS role_active,
            r.franchise_id,
            fst.team_key,
            fst.team_name
        FROM public.auth_user u
        LEFT JOIN public.auth_user_league_role r
          ON r.user_id = u.user_id
         AND r.league_key = %s
         AND r.active = true
        LEFT JOIN public.franchise_season_team fst
          ON fst.franchise_id = r.franchise_id
         AND fst.league_key = r.league_key
         AND fst.season_year = %s
        WHERE u.email_normalized = %s
        LIMIT 1
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (league_key, season_year, email_normalized))
                row = cur.fetchone()
                if not row:
                    return None

                return {
                    "user_id": int(row[0]),
                    "email_normalized": str(row[1]),
                    "password_hash": str(row[2]),
                    "user_active": bool(row[3]),
                    "must_change_password": bool(row[4]),
                    "is_site_admin": bool(row[5]),
                    "role_code": str(row[6]) if row[6] is not None else None,
                    "role_active": bool(row[7]) if row[7] is not None else False,
                    "franchise_id": int(row[8]) if row[8] is not None else None,
                    "team_key": str(row[9]) if row[9] is not None else None,
                    "team_name": str(row[10]) if row[10] is not None else None,
                }
    except Exception as e:
        st.error(f"Failed to load local auth principal: {e}")
        return None


def _render_local_auth_block() -> None:
    league_key = get_league_key()
    season_year = get_season_year()

    auth_ctx = _get_auth_context()

    with st.expander("Login", expanded=not auth_ctx["is_authenticated"]):
        if auth_ctx["is_authenticated"]:
            role_txt = "site admin" if auth_ctx["is_site_admin"] else (auth_ctx["league_role"] or "viewer")
            team_txt = auth_ctx["team_name"] or auth_ctx["team_key"] or "no team mapping"
            st.success(f"Signed in as {auth_ctx['email']} ({role_txt})")
            st.caption(f"Current league mapping: {team_txt}")

            st.divider()
            st.markdown("**Profile**")
            st.caption("Change your password.")

            with st.form("profile_change_password_form"):
                current_pw = st.text_input("Current password", type="password", key="profile_current_pw")
                new_pw = st.text_input("New password", type="password", key="profile_new_pw")
                new_pw_2 = st.text_input("Re-enter new password", type="password", key="profile_new_pw_2")
                submitted = st.form_submit_button("Change password", type="primary")

                if submitted:
                    if not current_pw or not new_pw or not new_pw_2:
                        st.error("Enter the current password and the new password in both fields.")
                    elif new_pw != new_pw_2:
                        st.error("The new passwords do not match.")
                    elif len(new_pw) < 10:
                        st.error("New password must be at least 10 characters.")
                    else:
                        principal = _load_local_auth_principal_by_user_id(
                            user_id=int(auth_ctx["user_id"]),
                            league_key=league_key,
                            season_year=season_year,
                        )

                        if principal is None or not principal["user_active"]:
                            st.error("Unable to load the current user.")
                        else:
                            stored_hash = str(principal["password_hash"] or "")
                            try:
                                ok = bcrypt.checkpw(current_pw.encode("utf-8"), stored_hash.encode("utf-8"))
                            except Exception:
                                ok = False

                            if not ok:
                                st.error("Current password is incorrect.")
                            elif _set_local_user_password(
                                user_id=int(auth_ctx["user_id"]),
                                new_password=new_pw,
                            ):
                                st.session_state["auth_must_change_password"] = False
                                st.success("Password changed successfully.")
                                st.rerun()

            if st.button("Log out", key="local_auth_logout_btn"):
                session_token = _read_auth_cookie_session_token()
                if session_token:
                    _revoke_auth_session(session_token=session_token)
                _clear_local_auth_session()
                st.markdown(
                    f'<meta http-equiv="refresh" content="0; url={_auth_bridge_clear_url(next_url="/")}">',
                    unsafe_allow_html=True,
                )
                st.stop()
            return

        email = st.text_input("Email", key="local_auth_email")
        pw = st.text_input("Password", type="password", key="local_auth_password")

        if st.button("Log in", type="primary", key="local_auth_login_btn"):
            email_normalized = str(email or "").strip().lower()
            if not email_normalized or not pw:
                st.error("Enter email and password.")
                return

            client_ip = _get_client_ip_for_login_attempt()

            if _is_login_rate_limited(
                email_attempted=email_normalized,
                ip_address=client_ip,
                max_failures=5,
                window_minutes=10,
            ):
                st.error("Too many failed login attempts. Please wait 10 minutes and try again.")
                return

            principal = _load_local_auth_principal(
                email_normalized=email_normalized,
                league_key=league_key,
                season_year=season_year,
            )

            if principal is None:
                _record_login_attempt(
                    email_attempted=email_normalized,
                    ip_address=client_ip,
                    success=False,
                )
                st.error("Invalid email or password.")
                return

            if not principal["user_active"]:
                _record_login_attempt(
                    email_attempted=email_normalized,
                    ip_address=client_ip,
                    success=False,
                )
                st.error("This user is inactive.")
                return

            stored_hash = str(principal["password_hash"] or "")
            try:
                ok = bcrypt.checkpw(pw.encode("utf-8"), stored_hash.encode("utf-8"))
            except Exception:
                ok = False

            if not ok:
                _record_login_attempt(
                    email_attempted=email_normalized,
                    ip_address=client_ip,
                    success=False,
                )
                st.error("Invalid email or password.")
                return

            _record_login_attempt(
                email_attempted=email_normalized,
                ip_address=client_ip,
                success=True,
            )

            st.session_state["auth_is_authenticated"] = True
            st.session_state["auth_user_id"] = principal["user_id"]
            st.session_state["auth_email"] = principal["email_normalized"]
            st.session_state["auth_is_site_admin"] = bool(principal["is_site_admin"])
            st.session_state["auth_must_change_password"] = bool(principal["must_change_password"])
            st.session_state["auth_league_role"] = principal["role_code"]
            st.session_state["auth_franchise_id"] = principal["franchise_id"]
            st.session_state["auth_team_key"] = principal["team_key"]
            st.session_state["auth_team_name"] = principal["team_name"]

            session_token = _create_auth_session(user_id=int(principal["user_id"]))
            if not session_token:
                st.error("Unable to create auth session.")
                return

            st.session_state["_debug_login_session_token_prefix"] = str(session_token)[:12]

            handoff_code = _create_auth_handoff_code(session_token=session_token, ttl_seconds=60)
            if not handoff_code:
                st.error("Unable to create auth handoff.")
                return

            st.success("Logged in.")
            st.markdown(
                f'<meta http-equiv="refresh" content="0; url={_auth_bridge_set_url(handoff_code=handoff_code, next_url="/")}">',
                unsafe_allow_html=True,
            )
            st.stop()

def _commissioner_auth(state: DraftState) -> bool:
    """
    Commissioner tools are ONLY accessible at:
      /?commissioner=1

    Access requires a logged-in user whose league role is commissioner,
    or a site admin.
    """
    if str(st.query_params.get("commissioner", "0")) != "1":
        return False

    auth_ctx = _get_auth_context()

    if not bool(auth_ctx.get("is_authenticated", False)):
        st.info("Log in to access commissioner tools.")
        return False

    if bool(auth_ctx.get("is_site_admin", False)):
        return True

    if str(auth_ctx.get("league_role") or "").strip().lower() == "commissioner":
        return True

    st.info("Commissioner access required.")
    return False

def _debug_enabled() -> bool:
    """
    Debug is ONLY allowed when:
      - commissioner URL is used (/?commissioner=1)
      - commissioner is unlocked (session_state flag set by _commissioner_auth)
      - debug toggle is explicitly enabled
    """
    if str(st.query_params.get("commissioner", "0")) != "1":
        return False
    if not bool(st.session_state.get("commissioner_authed", False)):
        return False
    return bool(st.session_state.get("show_debug", False))


def render_app() -> None:
    # Non-commissioner URL must not expose any sidebar UI (no pane, no expand control).
    # Commissioner URL continues to use the sidebar normally.
    is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"

    if not is_commissioner_url:
        st.markdown(
            """
            <style>
              /* Hide the entire sidebar */
              [data-testid="stSidebar"] { display: none !important; }

              /* Hide the expand/collapse control that appears when sidebar is collapsed */
              [data-testid="collapsedControl"] { display: none !important; }

              /* Extra safety for variant testids across versions/builds */
              [data-testid="stSidebarCollapsedControl"] { display: none !important; }
            </style>
            """,
            unsafe_allow_html=True,
        )
    ensure_initialized()
    state = get_state()

    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()

    if not bool(st.session_state.get("auth_is_authenticated", False)):
        _delete_expired_auth_sessions()
        cookie_session_token = _read_auth_cookie_session_token()
        cookie_user_id = _get_auth_session_user_id(session_token=cookie_session_token) if cookie_session_token else None
        if cookie_user_id is not None:
            principal = _load_local_auth_principal_by_user_id(
                user_id=int(cookie_user_id),
                league_key=league_key,
                season_year=season_year,
            )
            if principal is not None and principal["user_active"]:
                st.session_state["auth_is_authenticated"] = True
                st.session_state["auth_user_id"] = principal["user_id"]
                st.session_state["auth_email"] = principal["email_normalized"]
                st.session_state["auth_is_site_admin"] = bool(principal["is_site_admin"])
                st.session_state["auth_must_change_password"] = bool(principal["must_change_password"])
                st.session_state["auth_league_role"] = principal["role_code"]
                st.session_state["auth_franchise_id"] = principal["franchise_id"]
                st.session_state["auth_team_key"] = principal["team_key"]
                st.session_state["auth_team_name"] = principal["team_name"]

    predraft_qos_raw = _load_predraft_qos(dsn, league_key, season_year)

    # Normalize predraft QO team_keyspace (TEAM_XX -> canonical Yahoo key) if needed
    order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
    legacy_to_canon = dict(st.session_state.get("legacy_to_canonical_team_key_map", {}) or {})

    predraft_qos: dict[str, dict[int, str]] = {}
    for tk, lvls in (predraft_qos_raw or {}).items():
        ntk = _canon_team_key_from_mixed_key(str(tk), order=order, legacy_to_canon=legacy_to_canon)
        if not ntk:
            continue
        predraft_qos.setdefault(ntk, {})
        for lvl, pk in (lvls or {}).items():
            if pk:
                predraft_qos[ntk][int(lvl)] = str(pk)

    current_qos = _compute_current_qos_from_log(predraft_qos, state.pick_log)
    _sync_qo_placeholders(state, predraft_qos, current_qos)
    qo_ph = 0
    for ps in state.picks.values():
        if ps.round_number <= 5 and ps.selected_ts_iso is None and ps.selected_player_key:
            qo_ph += 1
        # DEBUG sidebar is commissioner-only AND requires password unlock.
    is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
    debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

    if debug_enabled:
        st.sidebar.write("qo_placeholders_rounds1_5_render:", qo_ph)

    owner_name_by_team_key: dict[str, str] = {}
    if dsn:
        from draftboard.data.db_players import load_yahoo_team_map

        yahoo_team_rows_for_owner = load_yahoo_team_map(dsn, league_key, season_year)
        owner_name_by_team_key = {
            str(r.get("team_key") or "").strip(): str(r.get("owner_name") or "").strip()
            for r in (yahoo_team_rows_for_owner or [])
            if str(r.get("team_key") or "").strip()
        }

    st.markdown(
        """
        <style>
          .block-container {
            max-width: 100% !important;
            padding-left: 0.25rem !important;
            padding-right: 0.25rem !important;
          }

            /* Tabs: force single-row + horizontal scroll + visible scrollbar */
            div[data-baseweb="tab-list"] {
              display: flex !important;
              flex-wrap: nowrap !important;
              overflow-x: scroll !important;     /* use scroll (not auto) so scrollbar appears */
              overflow-y: hidden !important;
              white-space: nowrap !important;
              border-bottom: 1px solid rgba(255,255,255,0.12);
              padding-bottom: 2px !important;
              scrollbar-gutter: stable both-edges;
            }

            /* Prevent the internal container from spacing tabs out */
            div[data-baseweb="tab-list"] > div:not([data-baseweb="tab-highlight"]) {
              display: flex !important;
              flex-wrap: nowrap !important;
              gap: 0px !important;
            }

            /* Make tab buttons compact and avoid "fill the row" behavior */
            div[data-baseweb="tab-list"] button[role="tab"] {
              flex: 0 0 auto !important;
              min-width: max-content !important;
              border-radius: 10px 10px 0 0;
              margin: 0 !important;
              padding: 4px 8px !important;
              border: 1px solid rgba(255,255,255,0.12);
              border-bottom: 2px solid transparent !important;
              font-size: 0.90rem !important;
              line-height: 1.05 !important;
            }

            div[data-baseweb="tab-list"] button[role="tab"][aria-selected="true"] {
              margin: 0 !important;
              border-bottom: 2px solid rgb(255, 75, 75) !important;
            }

            div[data-baseweb="tab-highlight"] {
              display: none !important;
            }

            /* Make the scrollbar actually visible (Chrome/Safari) */
            div[data-baseweb="tab-list"]::-webkit-scrollbar {
              height: 10px;
            }
            div[data-baseweb="tab-list"]::-webkit-scrollbar-thumb {
              border-radius: 8px;
              background: rgba(255,255,255,0.25);
            }
            div[data-baseweb="tab-list"]::-webkit-scrollbar-track {
              background: rgba(255,255,255,0.07);
            }

            /* Firefox scrollbar */
            div[data-baseweb="tab-list"] {
              scrollbar-width: thin;
              scrollbar-color: rgba(255,255,255,0.25) rgba(255,255,255,0.07);
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    state.commissioner_mode = bool(_commissioner_auth(state))

    from draftboard.state.league_profile import get_active_league_profile
    _active_profile = get_active_league_profile()
    _league_name = str((_active_profile.get("league") or {}).get("name") or "Draft Board").strip()
    st.title(f"{_league_name} Draft Board")

    auth_col, _ = st.columns([1, 3], vertical_alignment="top")
    with auth_col:
        _render_local_auth_block()

    if _render_must_change_password_gate():
        return

    mobile_mode = st.toggle("Mobile Pick Mode", value=False)

    # View mode toggle removed (not used; confusing during draft)
    # state.view_mode remains in state for backward compatibility.

    from datetime import datetime
    from zoneinfo import ZoneInfo

    if mobile_mode:
        render_mobile_pick(state)
    else:
        # Top header row
        header_left, header_right = st.columns([1,1], vertical_alignment="center")

        with header_left:
            pass  # keeps spacing aligned with layout

        with header_right:
            now = datetime.now(ZoneInfo("UTC"))

            clocks = {
                "EST": now.astimezone(ZoneInfo("America/New_York")),
                "PST": now.astimezone(ZoneInfo("America/Los_Angeles")),
                "JST": now.astimezone(ZoneInfo("Asia/Tokyo")),
                "Spain": now.astimezone(ZoneInfo("Europe/Madrid")),
                "London": now.astimezone(ZoneInfo("Europe/London")),
            }

            clock_cols = st.columns(len(clocks), gap="small")

            for col, (label, t) in zip(clock_cols, clocks.items()):
                with col:
                    st.markdown(
                        f"""
                        <div style="
                            border:1px solid rgba(255,255,255,0.14);
                            border-radius:12px;
                            padding:8px 10px;
                            background:rgba(255,255,255,0.03);
                            text-align:center;
                            min-height:64px;
                        ">
                            <div style="
                                font-size:0.68rem;
                                font-weight:800;
                                letter-spacing:0.04em;
                                opacity:0.72;
                                margin-bottom:4px;
                                text-transform:uppercase;
                            ">
                                {label}
                            </div>
                            <div style="
                                font-size:1.05rem;
                                font-weight:900;
                                line-height:1.15;
                            ">
                                {t.strftime('%-I:%M %p')}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

        render_pick_controls(state)

    render_draft_complete_banner(state, league_name=_league_name, season_year=season_year)

    from draftboard.state.league_profile import get_active_qualifying_offers_enabled

    if get_active_qualifying_offers_enabled():
        tab_board, tab_players, tab_teams, tab_qos, tab_lottery, tab_tracker, tab_stats = st.tabs(
            ["Draft Board", "Available Players", "Teams", "QOs", "Draft Lottery", "Pick Tracker", "Draft Statistics"]
        )
    else:
        tab_board, tab_players, tab_teams, tab_lottery, tab_tracker, tab_stats = st.tabs(
            ["Draft Board", "Available Players", "Teams", "Draft Lottery", "Pick Tracker", "Draft Statistics"]
        )
        tab_qos = None

    with tab_board:
        st.subheader("Draft Board")

        # IMPORTANT: Do NOT pass predraft placeholders to the board.
        # We already sync the *current* QO placeholders into state.picks via _sync_qo_placeholders().
        pick_kind_by_pick_id = {e.pick_id: e.pick_kind for e in (state.pick_log or []) if getattr(e, "pick_id", None)}

        # --- DEBUG: board header + keeper placeholder sanity ---
        order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])
        unknown = [k for k in order if k and k not in state.teams]
        # DEBUG sidebar is commissioner-only AND requires password unlock.
        is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
        debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

        if debug_enabled:
            st.sidebar.markdown("### DEBUG: Board render inputs")
            st.sidebar.write("teams_count:", len(state.teams))
            st.sidebar.write("picks_count:", len(state.picks))
            st.sidebar.write("order_len:", len(order))
            st.sidebar.write("order_sample:", order[:8])
            st.sidebar.write("TEAMS_TAB order_len:", len(order))
            st.sidebar.write("TEAMS_TAB order_sample:", order[:8])
            st.sidebar.write("TEAMS_TAB missing_in_teams:", [k for k in order if k and k not in state.teams][:8])
            st.sidebar.write("order_unknown_count:", len(unknown))
            st.sidebar.write("order_unknown_sample:", unknown[:8])
            st.sidebar.write("pt_map_size:", len(getattr(state, "pt_player_team_map", {}) or {}))
            st.sidebar.write(
                "pt_map_team_keys_sample:",
                list((getattr(state, "pt_player_team_map", {}) or {}).values())[:5],
            )

        # Count keeper placeholders at render-time (rounds 6..25, ts None, has player)
        keeper_ph = 0
        for ps in state.picks.values():
            if ps.round_number <= 5:
                continue
            if ps.selected_ts_iso is not None:
                continue
            if ps.selected_player_key:
                keeper_ph += 1
        # DEBUG sidebar is commissioner-only AND requires password unlock.
        is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
        debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

        if debug_enabled:
            st.sidebar.write("keeper_placeholders_rounds6_25_render:", keeper_ph)

        # Auto-heal order if missing/invalid (deterministic from Round 6 owners)
        if len(order) != 16 or unknown:
            healed = []
            for slot in range(1, 17):
                pid = f"R06-{slot:02d}"
                ps = state.picks.get(pid)
                healed.append(str(getattr(ps, "owner_team_key", "") or "") if ps else "")
            state.draft_order_team_keys_by_slot = healed
            order = healed
            # DEBUG sidebar is commissioner-only AND requires password unlock.
            is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
            debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

            if debug_enabled:
                st.sidebar.write("ORDER_HEALED:", True)
                st.sidebar.write("order_healed_sample:", order[:8])
        else:
            # DEBUG sidebar is commissioner-only AND requires password unlock.
            is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
            debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

            if debug_enabled:
                st.sidebar.write("ORDER_HEALED:", False)

        render_board_html(
            state.picks,
            state.teams,
            state.players,
            qo_placeholders=None,
            draft_order_team_keys_by_slot=order,
            pick_kind_by_pick_id=pick_kind_by_pick_id,
            pt_player_keys=set((getattr(state, "pt_player_team_map", {}) or {}).keys()),
        )

        with st.expander("Pick Log (details)", expanded=False):
            render_pick_log(state)

    with tab_players:
        render_available_players(state)

    # --- Contracts/PT source of truth for UI ---
    # Canonical contracts come from session_state["contract_rows"] (loaded in ensure_initialized()).
    contract_rows = st.session_state.get("contract_rows", []) or []
    pt_map = getattr(state, "pt_player_team_map", None) or {}

    contract_years_map: dict[str, int] = {}
    for row in contract_rows:
        pk = str(row.get("yahoo_player_key") or "").strip()
        yrs = row.get("years_remaining")
        if pk and yrs is not None:
            contract_years_map[pk] = int(yrs)

    # PTs should also be treated as keepers (they already display as "PT" in the Teams tab)
    for pk in pt_map.keys():
        contract_years_map.setdefault(str(pk), contract_years_map.get(str(pk)))

    with tab_teams:
        render_teams(state, contract_years_map)

    if tab_qos is not None:
        with tab_qos:
            render_qos_tab(state)

    with tab_lottery:
        render_draft_lottery_tab(state)

    with tab_tracker:
        render_pick_tracker(state, owner_name_by_team_key)

    with tab_stats:
        render_draft_statistics_tab(state)

    st.divider()

    if state.commissioner_mode:
        if st.button("Lock Commissioner Tools", key="commissioner_lock"):
            st.session_state["commissioner_authed"] = False
            st.rerun()

        auth_ctx = _get_auth_context()
        render_commissioner_actions(state, auth_ctx=auth_ctx)

    from pathlib import Path
    from draftboard.state.autosave import AUTOSAVE_PATH

    autosave_p = Path(str(AUTOSAVE_PATH))
    autosave_info = ""
    try:
        if autosave_p.exists():
            autosave_info = f" • Autosave: {autosave_p.stat().st_size} bytes @ {datetime.fromtimestamp(autosave_p.stat().st_mtime).isoformat(timespec='seconds')}"
        else:
            autosave_info = " • Autosave: (missing)"
    except Exception:
        autosave_info = " • Autosave: (unreadable)"

    st.caption(f"Version: {APP_VERSION} • Last modified: {_last_modified_iso(APP_FILE_PATH)}{autosave_info}")
