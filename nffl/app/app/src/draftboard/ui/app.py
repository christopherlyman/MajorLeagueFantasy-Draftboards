from __future__ import annotations
import os
import re
import base64
import hmac
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

import streamlit as st
import bcrypt
import psycopg
from psycopg.rows import dict_row
import extra_streamlit_components as stx

from draftboard.data.picks_grid import build_picks_grid
from draftboard.domain.clock import compute_clock_status
from draftboard.domain.models import Position, PickLogEntry, PickSlot, Team
from draftboard.state.autosave import try_load_autosave, save_autosave
from draftboard.state.runtime import get_league_key, get_postgres_dsn, get_season_year
from draftboard.state.store import DraftClock, DraftState, has_state, init_state, get_state, set_current_pick
from draftboard.state.league_profile import get_active_first_standard_round, get_active_manager_count, get_active_qo_rounds
from draftboard.state.init_restore import (
    _team_to_slot_from_order,
    _is_legacy_team_keyspace,
    _build_canonical_teams_from_yahoo_rows,
    _build_legacy_to_canonical_team_key_map,
    _canon_team_key_from_mixed_key,
    ensure_initialized,
)
from draftboard.ui.components.board_html import render_board_html
from draftboard.ui.components.postgres_board_html import render_postgres_board_html
from draftboard.ui.components.nffl_team_workbench import render_nffl_team_workbench
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


def _shift_qos_up_after_gap(level_map: dict[int, str], gap_level: int, *, max_level: int | None = None) -> dict[int, str]:
    """
    Fill a vacated QO level by moving lower-ranked QOs upward.

    Example with four QO rounds:
      QO2 is poached.
      QO3 -> QO2
      QO4 -> QO3
      QO4 becomes empty.
    """
    max_qo = int(max_level or get_active_qo_rounds())
    gap = int(gap_level)

    new = dict(level_map or {})
    for lvl in range(gap, max_qo):
        nxt = new.get(lvl + 1)
        if nxt:
            new[lvl] = nxt
        else:
            new.pop(lvl, None)

    new.pop(max_qo, None)
    return {int(k): str(v) for k, v in new.items() if v}


def _compute_current_qos_from_log(predraft: dict[str, dict[int, str]], pick_log: list) -> dict[str, dict[int, str]]:
    """
    Replay NFFL/MLF-style QO state from original published QOs plus actual QO-round picks.

    Rules:
    - Original QOs are placeholders/reservations, not real picks.
    - If a team takes its own QO at that exact QO slot, that QO is consumed.
    - If a team drafts a FA instead of using its QO slot, its own QO for that slot is released.
    - If a team poaches another team's lower QO:
        * poacher's same-round QO is released;
        * poached player is consumed as a real pick;
        * victim team's lower remaining QOs move up to fill the gap.
    - QO1 cannot be poached because poach requires victim level > current round.
    """
    max_qo = int(get_active_qo_rounds())

    current: dict[str, dict[int, str]] = {
        str(tk): {int(lvl): str(pk) for lvl, pk in (lvls or {}).items() if pk}
        for tk, lvls in (predraft or {}).items()
    }

    def _find_player(pk: str) -> tuple[str, int] | None:
        wanted = str(pk or "").strip()
        if not wanted:
            return None
        for team_key, lvls in current.items():
            for lvl, pkey in list(lvls.items()):
                if str(pkey) == wanted:
                    return team_key, int(lvl)
        return None

    def _release_team_level(team_key: str, lvl: int) -> None:
        tk = str(team_key or "").strip()
        if not tk:
            return
        current.setdefault(tk, {})
        current[tk].pop(int(lvl), None)
        current[tk] = {int(k): str(v) for k, v in current[tk].items() if v}

    def _qoround_from_pick_id(pick_id: str) -> int | None:
        m = re.match(r"^QO(\d+)-", str(pick_id or "").strip(), flags=re.IGNORECASE)
        if not m:
            return None
        lvl = int(m.group(1))
        return lvl if 1 <= lvl <= max_qo else None

    events = sorted(
        list(pick_log or []),
        key=lambda e: str(getattr(e, "ts_iso", "") or ""),
    )

    for e in events:
        round_level = _qoround_from_pick_id(getattr(e, "pick_id", ""))
        if round_level is None:
            continue

        owner_team_key = str(getattr(e, "owner_team_key", "") or "").strip()
        selected_player_key = str(getattr(e, "player_key", "") or "").strip()
        pick_kind = str(getattr(e, "pick_kind", "") or "").strip().upper()

        if not owner_team_key or not selected_player_key:
            continue

        if pick_kind == "QO":
            # Own same-level QO retention consumes that QO.
            # Own lower-QO promotion also consumes the selected lower QO, releases
            # the skipped same-round QO, and promotes later own QOs after the gap.
            found = _find_player(selected_player_key)
            if found and str(found[0]) == owner_team_key:
                _selected_team_key, selected_level = found
                team_lvls = dict(current.get(owner_team_key, {}) or {})
                team_lvls.pop(int(round_level), None)
                team_lvls.pop(int(selected_level), None)
                if int(selected_level) != int(round_level):
                    team_lvls = _shift_qos_up_after_gap(
                        team_lvls,
                        int(selected_level),
                        max_level=max_qo,
                    )
                current[owner_team_key] = {
                    int(lvl): str(pk)
                    for lvl, pk in sorted(team_lvls.items())
                    if pk
                }
            else:
                _release_team_level(owner_team_key, round_level)
            continue

        if pick_kind == "FA":
            _release_team_level(owner_team_key, round_level)
            continue

        if pick_kind == "POACH":
            _release_team_level(owner_team_key, round_level)

            found = _find_player(selected_player_key)
            if not found:
                continue

            victim_team_key, victim_level = found
            victim_lvls = dict(current.get(victim_team_key, {}) or {})
            victim_lvls.pop(int(victim_level), None)
            current[victim_team_key] = _shift_qos_up_after_gap(
                victim_lvls,
                int(victim_level),
                max_level=max_qo,
            )
            continue

        _release_team_level(owner_team_key, round_level)

    return {
        str(tk): {int(lvl): str(pk) for lvl, pk in sorted((lvls or {}).items()) if pk}
        for tk, lvls in sorted(current.items())
        if lvls
    }


def _compute_qo_display_from_log(predraft: dict[str, dict[int, str]], pick_log: list) -> dict[str, dict[int, str]]:
    """
    Replay QO display outcomes for the QOs tab.

    This is intentionally separate from _compute_current_qos_from_log:
    - current_qos is for future rights / poach eligibility, so retained QOs are consumed.
    - qo_display is for visible outcomes, so retained QOs remain shown in their QO slot.
    """
    max_qo = int(get_active_qo_rounds())

    current: dict[str, dict[int, str]] = {
        str(tk): {int(lvl): str(pk) for lvl, pk in (lvls or {}).items() if pk}
        for tk, lvls in (predraft or {}).items()
    }

    def _find_player(pk: str) -> tuple[str, int] | None:
        wanted = str(pk or "").strip()
        if not wanted:
            return None
        for team_key, lvls in current.items():
            for lvl, pkey in list(lvls.items()):
                if str(pkey) == wanted:
                    return team_key, int(lvl)
        return None

    def _release_team_level(team_key: str, lvl: int) -> None:
        tk = str(team_key or "").strip()
        if not tk:
            return
        current.setdefault(tk, {})
        current[tk].pop(int(lvl), None)
        current[tk] = {int(k): str(v) for k, v in current[tk].items() if v}

    def _qoround_from_pick_id(pick_id: str) -> int | None:
        m = re.match(r"^QO(\d+)-", str(pick_id or "").strip(), flags=re.IGNORECASE)
        if not m:
            return None
        lvl = int(m.group(1))
        return lvl if 1 <= lvl <= max_qo else None

    events = sorted(
        list(pick_log or []),
        key=lambda e: str(getattr(e, "ts_iso", "") or ""),
    )

    for e in events:
        round_level = _qoround_from_pick_id(getattr(e, "pick_id", ""))
        if round_level is None:
            continue

        owner_team_key = str(getattr(e, "owner_team_key", "") or "").strip()
        selected_player_key = str(getattr(e, "player_key", "") or "").strip()
        pick_kind = str(getattr(e, "pick_kind", "") or "").strip().upper()

        if not owner_team_key or not selected_player_key:
            continue

        if pick_kind == "QO":
            # Retained/promoted own QO should remain visible as the team's QO
            # outcome in the QOs tab current table.
            found = _find_player(selected_player_key)
            selected_level = int(found[1]) if found and str(found[0]) == owner_team_key else int(round_level)
            team_lvls = dict(current.get(owner_team_key, {}) or {})
            team_lvls.pop(int(round_level), None)
            if selected_level != int(round_level):
                team_lvls.pop(selected_level, None)
                team_lvls = _shift_qos_up_after_gap(
                    team_lvls,
                    selected_level,
                    max_level=max_qo,
                )
            team_lvls[int(round_level)] = selected_player_key
            current[owner_team_key] = {
                int(lvl): str(pk)
                for lvl, pk in sorted(team_lvls.items())
                if pk
            }
            continue

        if pick_kind == "FA":
            # Team declined/released its own QO at this level.
            _release_team_level(owner_team_key, round_level)
            continue

        if pick_kind == "POACH":
            # Poacher declined/released its own same-level QO.
            _release_team_level(owner_team_key, round_level)

            # Victim loses the poached player; lower remaining QOs promote.
            found = _find_player(selected_player_key)
            if not found:
                continue

            victim_team_key, victim_level = found
            victim_lvls = dict(current.get(victim_team_key, {}) or {})
            victim_lvls.pop(int(victim_level), None)
            current[victim_team_key] = _shift_qos_up_after_gap(
                victim_lvls,
                int(victim_level),
                max_level=max_qo,
            )
            continue

        _release_team_level(owner_team_key, round_level)

    return {
        str(tk): {int(lvl): str(pk) for lvl, pk in sorted((lvls or {}).items()) if pk}
        for tk, lvls in sorted(current.items())
        if lvls
    }


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


# NFFL_DRAFT_SELECTION_DB_WRITE_HELPER_START

def _record_draft_selection_to_db(
    *,
    pick_id: str,
    selecting_team_key: str,
    yahoo_player_key: str,
    pick_kind: str,
    selected_at_utc: str,
) -> None:
    """
    Canonical write path for real draft picks.

    Board rendering reads nffl.v_draft_board_current, which depends on
    nffl.draft_selection. Therefore Make Pick must write here before
    local Streamlit/autosave state is trusted.
    """
    import os
    import psycopg

    dsn = get_postgres_dsn()
    if not dsn:
        raise RuntimeError("Cannot record draft selection: POSTGRES_DSN / MLF_POSTGRES_DSN is not configured.")

    draft_key = (
        os.environ.get("DRAFTBOARD_DRAFT_KEY")
        or "nffl_2026_preseason"
    )

    pk = str(pick_id or "").strip()
    tk = str(selecting_team_key or "").strip()
    ypk = str(yahoo_player_key or "").strip()
    kind = str(pick_kind or "FA").strip().upper()
    ts = str(selected_at_utc or "").strip()

    if not pk or not tk or not ypk:
        raise RuntimeError(
            f"Cannot record draft selection: missing pick/team/player "
            f"(pick_id={pk!r}, selecting_team_key={tk!r}, yahoo_player_key={ypk!r})."
        )

    if kind not in {"FA", "QO", "POACH"}:
        kind = "FA"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Avoid relying on an unknown unique constraint shape.
            # UI guards already prevent overwriting a used pick.
            cur.execute(
                """
                DELETE FROM nffl.draft_selection
                WHERE draft_key = %s
                  AND pick_id = %s
                """,
                (draft_key, pk),
            )
            cur.execute(
                """
                INSERT INTO nffl.draft_selection (
                    draft_key,
                    pick_id,
                    selecting_team_key,
                    yahoo_player_key,
                    pick_kind,
                    selected_at_utc,
                    selected_by,
                    note
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s::timestamp, NULL, NULL
                )
                """,
                (draft_key, pk, tk, ypk, kind, ts),
            )
        conn.commit()

# NFFL_DRAFT_SELECTION_DB_WRITE_HELPER_END


def _apply_pick(state: DraftState, pick_id: str, player_key: str, pick_kind: str = "FA") -> None:
    pick = state.picks[pick_id]
    ts = datetime.utcnow().isoformat()

    player = state.players[player_key]

    # NFFL_DRAFT_SELECTION_DB_WRITE_CALL_START
    # Write canonical DB truth before mutating local/autosave state.
    _record_draft_selection_to_db(
        pick_id=pick.pick_id,
        selecting_team_key=pick.owner_team_key,
        yahoo_player_key=player.player_key,
        pick_kind=pick_kind,
        selected_at_utc=ts,
    )
    # NFFL_DRAFT_SELECTION_DB_WRITE_CALL_END

    pick.selected_player_key = player_key
    pick.selected_ts_iso = ts
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



def _load_pick_dropdown_protected_keeper_keys(
    dsn: str | None,
    league_key: str,
    season_year: int,
) -> set[str]:
    """
    Pick dropdown legality filter.

    Include free agents and current QOs in the dropdown.
    Exclude active contracts and FT players.
    Already-drafted players are filtered separately from DraftState.
    """
    if not dsn:
        raise RuntimeError("Postgres DSN is not configured")

    import psycopg

    sql = """
        SELECT c.yahoo_player_key
        FROM nffl.contract c
        WHERE c.league_key = %s
          AND c.season_year = %s
          AND c.status = 'active'

        UNION

        SELECT d.yahoo_player_key
        FROM nffl.offseason_keeper_decision d
        WHERE d.league_key = %s
          AND d.season_year = %s
          AND d.decision_type = 'FT'
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year, league_key, season_year))
            return {str(row[0]) for row in cur.fetchall() if row and row[0]}


def render_pick_controls(state: DraftState) -> None:
    # NFFL_PICK_CONTROLS_PREDRAFT_QOS_SCOPE_FIX_START
    import os
    # render_pick_controls is called independently from render_app's main QO load.
    # Keep the QO replay engine deterministic by loading predraft QOs in local scope.
    dsn_for_qo_replay = get_postgres_dsn()
    league_key_for_qo_replay = (
        os.environ.get("LEAGUE_KEY")
        or os.environ.get("MLF_LEAGUE_KEY")
        or "470.l.84346"
    )
    try:
        season_year_for_qo_replay = int(
            os.environ.get("SEASON_YEAR")
            or os.environ.get("MLF_SEASON_YEAR")
            or 2026
        )
    except Exception:
        season_year_for_qo_replay = 2026

    predraft_qos = (
        _load_predraft_qos(
            dsn_for_qo_replay,
            league_key_for_qo_replay,
            season_year_for_qo_replay,
        )
        if dsn_for_qo_replay
        else {}
    )
    # NFFL_PICK_CONTROLS_PREDRAFT_QOS_SCOPE_FIX_END

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
        protected_keeper_keys = {str(pk) for pk in (contracted_keys or set())}
        try:
            protected_keeper_keys.update(
                _load_pick_dropdown_protected_keeper_keys(
                    dsn,
                    league_key,
                    season_year,
                )
            )
        except Exception as exc:
            st.error(f"Could not load contract/FT protected player list: {exc}")
            return


        available_players = [
            p for p in state.players.values()
            if (p.player_key not in drafted and p.player_key not in protected_keeper_keys)
        ]

        # Sort the desktop pick dropdown by DB-backed NFFL Actual Rank.
        # state.players.rank_value is not hydrated for NFFL, but nffl.player_universe has rank_value.
        dropdown_stats_by_player_key = _fetch_available_players_nffl_stats(
            dsn,
            league_key,
            season_year,
            [str(p.player_key) for p in available_players],
        )
        dropdown_rank_by_player_key = {
            str(pk): row.get("rank_value")
            for pk, row in (dropdown_stats_by_player_key or {}).items()
            if row.get("rank_value") is not None
        }

        def _dropdown_rank_value(p):
            rv = dropdown_rank_by_player_key.get(str(p.player_key))
            if rv is None:
                rv = getattr(p, "rank_value", None)
            try:
                return float(rv) if rv is not None else None
            except Exception:
                return None

        available_players.sort(
            key=lambda p: (
                _dropdown_rank_value(p) is None,
                _dropdown_rank_value(p) if _dropdown_rank_value(p) is not None else 999999,
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

        player_keys = [p.player_key for p in available_players]
        select_key = f"selected_player_key_main_{state.clock.current_pick_id}"
        search_input_key = f"draft_player_query_input_main_{state.clock.current_pick_id}"
        search_applied_key = f"draft_player_query_applied_main_{state.clock.current_pick_id}"
        search_form_key = f"draft_player_search_form_main_{state.clock.current_pick_id}"
        submit_form_key = f"draft_player_submit_form_main_{state.clock.current_pick_id}"

        st.session_state.setdefault(search_applied_key, "")

        with st.form(search_form_key, clear_on_submit=False):
            draft_player_query_main = st.text_input(
                "Search player",
                value=str(st.session_state.get(search_applied_key, "") or ""),
                placeholder="Type a player name...",
                key=search_input_key,
            )
            search_submitted = st.form_submit_button("Search")

        if search_submitted:
            st.session_state[search_applied_key] = str(draft_player_query_main or "")
            st.session_state.pop(select_key, None)
            st.rerun()

        draft_player_query_applied = str(st.session_state.get(search_applied_key, "") or "")
        player_options = filter_player_keys_by_query(player_keys, draft_player_query_applied, fmt_player)

        if draft_player_query_applied:
            st.caption(f"Search: {draft_player_query_applied} · {len(player_options)} matching players")
        else:
            st.caption(f"Showing {len(player_options)} draftable players")

        btn_label = "MAKE PICK" if state.commissioner_mode else "SUBMIT PICK"

        with st.form(submit_form_key, clear_on_submit=False):
            chosen_player_key = st.selectbox(
                "Select player to draft",
                options=player_options,
                format_func=fmt_player,
                index=None,
                placeholder="Choose a player…",
                help="Search above. Player search is case-insensitive and accent-insensitive.",
                key=select_key,
            )
            submit_pick_clicked = st.form_submit_button(btn_label, type="primary")

        if submit_pick_clicked:
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
            if chosen_player_key in protected_keeper_keys:
                st.error("Contract/FT players are not draftable.")
                return

            # ---- QO / POACH / RELEASE LOGIC (pick-driven, replay-aware) ----
            pick_kind = "FA"

            cur_pick = state.picks[state.clock.current_pick_id]
            cur_round = int(cur_pick.round_number)
            cur_team_key = str(cur_pick.owner_team_key)

            current_qos_for_pick = _compute_current_qos_from_log(predraft_qos, state.pick_log)

            current_qo_level_by_player: dict[str, int] = {}
            current_qo_team_by_player: dict[str, str] = {}
            for _tk, _lvls in (current_qos_for_pick or {}).items():
                for _lvl, _pk in (_lvls or {}).items():
                    if _pk:
                        current_qo_level_by_player[str(_pk)] = int(_lvl)
                        current_qo_team_by_player[str(_pk)] = str(_tk)

            if 1 <= cur_round <= get_active_qo_rounds():
                if chosen_player_key in current_qo_level_by_player:
                    holder_lvl = int(current_qo_level_by_player[chosen_player_key])
                    holder_team_key = str(current_qo_team_by_player.get(chosen_player_key, ""))

                    if cur_team_key == holder_team_key and holder_lvl >= cur_round:
                        # Own same-level QO retention and own lower-QO promotion are both legal.
                        pick_kind = "QO"
                    elif holder_team_key != cur_team_key and holder_lvl > cur_round:
                        pick_kind = "POACH"
                    else:
                        holder_nm = state.teams.get(holder_team_key).name if holder_team_key in state.teams else holder_team_key
                        st.error(f"Not poach-eligible. Reserved for {holder_nm} at QO{holder_lvl}.")
                        return
                else:
                    pick_kind = "FA"
            else:
                pick_kind = "FA"
            # ---- END QO / POACH / RELEASE LOGIC ----

            _apply_pick(state, state.clock.current_pick_id, chosen_player_key, pick_kind=pick_kind)
            st.success(
                f"Picked {state.players[chosen_player_key].name} at {state.clock.current_pick_id} [{pick_kind}]"
            )

            # Clear selection/search for THIS pick + rerun so UI updates immediately
            st.session_state.pop(select_key, None)
            st.session_state.pop(search_input_key, None)
            st.session_state.pop(search_applied_key, None)
            st.rerun()
def render_mobile_pick(state: DraftState) -> None:
    drafted = {ps.selected_player_key for ps in state.picks.values()
    if ps.selected_player_key and ps.selected_ts_iso is not None}

    import os
    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()
    try:
        protected_keeper_keys = _load_pick_dropdown_protected_keeper_keys(
            dsn,
            league_key,
            season_year,
        )
    except Exception as exc:
        st.error(f"Could not load contract/FT protected player list: {exc}")
        return

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
        if p.player_key in protected_keeper_keys:
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
        if chosen_player_key in protected_keeper_keys:
            st.error("Contract/FT players are not draftable.")
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

                "Actual Rank": getattr(p, "rank_value", None),
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



# NFFL_AVAILABLE_PLAYERS_STATS_HELPERS_START

def _nffl_available_stat_value(stats_json, stat_id: str):
    stats = stats_json or {}
    if not isinstance(stats, dict):
        return None

    value = stats.get(str(stat_id))
    if value is None or str(value).strip() in ("", "-"):
        return None

    text = str(value).strip()
    try:
        if "." in text:
            return float(text)
        return int(text)
    except Exception:
        return text


def _fetch_available_players_nffl_stats(
    dsn: str,
    league_key: str,
    season_year: int,
    player_keys: list[str],
) -> dict[str, dict]:
    if not dsn or not player_keys:
        return {}

    import psycopg
    from psycopg.rows import dict_row

    keys = [str(k) for k in player_keys if k]
    if not keys:
        return {}

    sql = """
        WITH snap AS (
            SELECT rs.snapshot_id
            FROM nffl.roster_snapshot rs
            JOIN nffl.v_active_season_context ctx
              ON ctx.current_league_key = rs.league_key
             AND ctx.current_season_year = rs.season_year
             AND ctx.prior_season_year = rs.source_season_year
            WHERE rs.snapshot_type='END_OF_PRIOR_SEASON_ROSTER'
            ORDER BY rs.updated_at_utc DESC
            LIMIT 1
        ),
        keys AS (
            SELECT unnest(%s::text[]) AS yahoo_player_key
        )
        SELECT DISTINCT ON (k.yahoo_player_key)
            k.yahoo_player_key,
            pu.percent_owned,
            pu.rank_value,
            coalesce(s.stats_json, '{}'::jsonb) AS stats_json,
            fp.fan_points_2025
        FROM keys k
        LEFT JOIN nffl.player_universe pu
          ON pu.league_key = %s
         AND pu.season_year = %s
         AND pu.yahoo_player_key = k.yahoo_player_key
        LEFT JOIN snap ON true
        LEFT JOIN nffl.roster_snapshot_player_stats s
          ON s.snapshot_id = snap.snapshot_id
         AND s.league_key = %s
         AND s.season_year = %s
         AND s.yahoo_player_key = k.yahoo_player_key
        LEFT JOIN nffl.v_roster_snapshot_player_fantasy_points fp
          ON fp.snapshot_id = s.snapshot_id
         AND fp.team_key = s.team_key
         AND fp.yahoo_player_key = s.yahoo_player_key
        ORDER BY k.yahoo_player_key, fp.fan_points_2025 DESC NULLS LAST;
    """

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (keys, league_key, int(season_year), league_key, int(season_year)))
            rows = list(cur.fetchall())

    return {str(r["yahoo_player_key"]): dict(r) for r in rows}


def _apply_nffl_available_stats_columns(df, players, stats_by_player_key: dict[str, dict]):
    import pandas as pd

    df2 = df.copy()

    # Drop old baseball display columns from the generic inherited table.
    old_cols = [
        "H/AB", "R", "HR", "RBI", "SB", "BB", "K (H)", "AVG",
        "IP", "W", "K (P)", "TB", "ERA", "WHIP", "QS", "SV+H",
    ]
    df2 = df2.drop(columns=[c for c in old_cols if c in df2.columns], errors="ignore")

    player_keys = [str(getattr(p, "player_key", "") or "") for p in players]

    # Yahoo stat IDs used by the same roster_snapshot_player_stats source as Teams.
    # Keep this one-table view focused on headline cross-position football stats.
    stat_specs = [
        ("GP", "0"),
        ("Pass Yds", "4"),
        ("Pass TD", "5"),
        ("Pass INT", "6"),
        ("Rush Att", "8"),
        ("Rush Yds", "9"),
        ("Rush TD", "10"),
        ("Rec", "11"),
        ("Rec Yds", "12"),
        ("Rec TD", "13"),
        ("Targets", "78"),
        ("Fum Lost", "17"),
    ]

    new_cols = {
        "Actual Rank": [
            (stats_by_player_key.get(pk, {}) or {}).get("rank_value")
            for pk in player_keys
        ],
        "% Ros": [
            (stats_by_player_key.get(pk, {}) or {}).get("percent_owned")
            for pk in player_keys
        ],
        "Fan Pts": [
            (stats_by_player_key.get(pk, {}) or {}).get("fan_points_2025")
            for pk in player_keys
        ]
    }

    for label, stat_id in stat_specs:
        new_cols[label] = [
            _nffl_available_stat_value(
                (stats_by_player_key.get(pk, {}) or {}).get("stats_json"),
                stat_id,
            )
            for pk in player_keys
        ]

    # Remove any prior version of these columns before inserting.
    df2 = df2.drop(columns=[c for c in new_cols if c in df2.columns], errors="ignore")

    insert_after = "% Ros" if "% Ros" in df2.columns else "Current Rank"
    if insert_after in df2.columns:
        insert_at = list(df2.columns).index(insert_after) + 1
    else:
        insert_at = len(df2.columns)

    left = df2.iloc[:, :insert_at]
    middle = pd.DataFrame(new_cols)
    right = df2.iloc[:, insert_at:]

    return pd.concat([left.reset_index(drop=True), middle.reset_index(drop=True), right.reset_index(drop=True)], axis=1)

# NFFL_AVAILABLE_PLAYERS_STATS_HELPERS_END


# NFFL_AVAILABLE_PLAYERS_KEEPER_STATUS_HELPERS_START

def _fetch_available_players_keeper_status(
    dsn: str,
    league_key: str,
    season_year: int,
    player_keys: list[str],
) -> dict[str, dict]:
    if not dsn or not player_keys:
        return {}

    import psycopg
    from psycopg.rows import dict_row

    keys = [str(k) for k in player_keys if k]
    if not keys:
        return {}

    sql = """
        WITH keys AS (
            SELECT unnest(%s::text[]) AS yahoo_player_key
        ),
        contract_rows AS (
            SELECT
                c.yahoo_player_key,
                'CONTRACT'::text AS status_type,
                c.contract_years_remaining,
                CASE
                    WHEN c.contract_years_remaining = 1 THEN '1-year'
                    ELSE c.contract_years_remaining::text || '-years'
                END AS status_label,
                c.team_key,
                t.team_name,
                20 AS priority
            FROM nffl.contract c
            LEFT JOIN nffl.team t
              ON t.league_key = c.league_key
             AND t.season_year = c.season_year
             AND t.team_key = c.team_key
            WHERE c.league_key = %s
              AND c.season_year = %s
              AND c.status = 'active'
              AND c.yahoo_player_key = ANY(%s::text[])
        ),
        ft_rows AS (
            SELECT
                d.yahoo_player_key,
                'FT'::text AS status_type,
                NULL::integer AS contract_years_remaining,
                'FT'::text AS status_label,
                d.team_key,
                t.team_name,
                10 AS priority
            FROM nffl.offseason_keeper_decision d
            LEFT JOIN nffl.team t
              ON t.league_key = d.league_key
             AND t.season_year = d.season_year
             AND t.team_key = d.team_key
            WHERE d.league_key = %s
              AND d.season_year = %s
              AND d.decision_type = 'FT'
              AND d.yahoo_player_key = ANY(%s::text[])
        ),
        qo_rows AS (
            SELECT
                q.yahoo_player_key,
                'QO'::text AS status_type,
                NULL::integer AS contract_years_remaining,
                'QO' || q.qo_level::text AS status_label,
                q.team_key,
                t.team_name,
                30 AS priority
            FROM public.qualifying_offer q
            LEFT JOIN nffl.team t
              ON t.league_key = q.league_key
             AND t.season_year = q.season_year
             AND t.team_key = q.team_key
            WHERE q.league_key = %s
              AND q.season_year = %s
              AND q.yahoo_player_key = ANY(%s::text[])
        ),
        all_rows AS (
            SELECT * FROM ft_rows
            UNION ALL
            SELECT * FROM contract_rows
            UNION ALL
            SELECT * FROM qo_rows
        )
        SELECT DISTINCT ON (k.yahoo_player_key)
            k.yahoo_player_key,
            a.status_type,
            a.contract_years_remaining,
            a.status_label,
            a.team_key,
            a.team_name
        FROM keys k
        LEFT JOIN all_rows a
          ON a.yahoo_player_key = k.yahoo_player_key
        ORDER BY k.yahoo_player_key, a.priority NULLS LAST;
    """

    params = (
        keys,
        league_key, int(season_year), keys,
        league_key, int(season_year), keys,
        league_key, int(season_year), keys,
    )

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())

    out = {}
    for r in rows:
        pk = str(r["yahoo_player_key"])
        if not r.get("status_type"):
            continue
        out[pk] = dict(r)
    return out

# NFFL_AVAILABLE_PLAYERS_KEEPER_STATUS_HELPERS_END


# NFFL_AVAILABLE_PLAYERS_DRAFT_SELECTION_STATUS_HELPERS_START

def _fetch_available_players_draft_selection_status(
    dsn: str,
    draft_key: str,
    league_key: str,
    season_year: int,
    player_keys: list[str],
) -> dict[str, dict]:
    """
    Read canonical real draft selections for Available Players display/filtering.
    This is separate from QO/contract/FT placeholders.
    """
    if not dsn or not player_keys:
        return {}

    import psycopg
    from psycopg.rows import dict_row

    keys = [str(k) for k in player_keys if k]
    if not keys:
        return {}

    sql = """
        WITH keys AS (
            SELECT unnest(%s::text[]) AS yahoo_player_key
        )
        SELECT
            ds.yahoo_player_key,
            ds.pick_id,
            ds.pick_kind,
            ds.selecting_team_key,
            t.team_name,
            ds.selected_at_utc
        FROM keys k
        JOIN nffl.draft_selection ds
          ON ds.yahoo_player_key = k.yahoo_player_key
         AND ds.draft_key = %s
        LEFT JOIN nffl.team t
          ON t.league_key = %s
         AND t.season_year = %s
         AND t.team_key = ds.selecting_team_key
    """

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (keys, draft_key, league_key, int(season_year)))
            rows = list(cur.fetchall())

    return {str(r["yahoo_player_key"]): dict(r) for r in rows}

# NFFL_AVAILABLE_PLAYERS_DRAFT_SELECTION_STATUS_HELPERS_END





def _format_players_df_for_display(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Apply the same display formatting rules as Available Players,
    without changing sorting semantics (caller sorts first).
    """
    import pandas as pd

    df_disp = df.copy()

    # Rank / % Ros: show as ints without .0
    for c in ["Actual Rank", "Current Rank", "Rank", "% Ros"]:
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

    # NFFL football stats display formatting.
    for c in [
        "GP", "Pass Yds", "Pass TD", "Pass INT", "Rush Att", "Rush Yds",
        "Rush TD", "Rec", "Rec Yds", "Rec TD", "Targets", "Fum Lost",
    ]:
        if c in df_disp.columns:
            vals = pd.to_numeric(df_disp[c], errors="coerce").round(0).astype("Int64")
            df_disp[c] = vals.astype("string").fillna("")

    if "Fan Pts" in df_disp.columns:
        # Keep numeric so Streamlit manual column sorting works correctly.
        df_disp["Fan Pts"] = pd.to_numeric(df_disp["Fan Pts"], errors="coerce").round(1)

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

    # NFFL_AVAILABLE_PLAYERS_DB_DRAFT_STATUS_START
    import os as _nffl_available_os

    all_available_tab_player_keys = [str(p.player_key) for p in state.players.values()]
    draft_key_for_available_players = _nffl_available_os.environ.get(
        "DRAFTBOARD_DRAFT_KEY",
        "nffl_2026_preseason",
    )
    db_draft_status_by_player_key = _fetch_available_players_draft_selection_status(
        get_postgres_dsn(),
        draft_key_for_available_players,
        get_league_key(),
        get_season_year(),
        all_available_tab_player_keys,
    )
    drafted |= set(db_draft_status_by_player_key.keys())
    # NFFL_AVAILABLE_PLAYERS_DB_DRAFT_STATUS_END

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
    pos_options = [p.value for p in Position]

    toggle_labels = ["Show all players"]
    if qo_enabled:
        toggle_labels.extend(["Show only QOs", "Show only Poach-eligible"])
    if pt_enabled:
        toggle_labels.append("Show only PT")
    if contracts_enabled:
        toggle_labels.append("Show only Contracts")

    # --- Sort controls (single source of truth = session_state) ---
    sort_cols = ["Draft Pick", "Team Name", "Actual Rank", "% Ros",
        "Fan Pts", "GP", "Pass Yds", "Pass TD", "Pass INT",
        "Rush Att", "Rush Yds", "Rush TD", "Rec", "Rec Yds", "Rec TD",
        "Targets", "Fum Lost",
        "Player Name", "Team", "Position"]
    if qo_enabled or pt_enabled or contracts_enabled:
        sort_cols.insert(1, "Contract/PT/QO")
    if contracts_enabled:
        sort_cols.insert(2, "Contract Years")

    default_sort = "Actual Rank"

    # Defaults must exist before widget creation.
    st.session_state.setdefault("available_players_search", "")
    st.session_state.setdefault("available_players_pos_filter", [])
    st.session_state.setdefault("available_players_show_all", False)
    st.session_state.setdefault("available_players_show_qo", False)
    st.session_state.setdefault("available_players_show_poach", False)
    st.session_state.setdefault("available_players_show_pt", False)
    st.session_state.setdefault("available_players_show_contracts", False)

    if st.session_state.get("avail_sort_col") not in sort_cols:
        st.session_state["avail_sort_col"] = default_sort
        st.session_state["avail_sort_desc"] = False
    st.session_state.setdefault("avail_sort_col", default_sort)
    st.session_state.setdefault("avail_sort_desc", False)

    search = st.text_input(
        "Search",
        placeholder="Type a player name...",
        key="available_players_search",
    )
    pos_filter = st.multiselect(
        "Position filter",
        options=["QB", "RB", "WR", "TE", "K", "DEF"],
        default=[],
        key="available_players_pos_filter",
        help="Leave blank to show all positions. Select one or more positions to filter the table.",
    )

    cols = st.columns(len(toggle_labels))
    i = 0

    with cols[i]:
        show_all_players = st.toggle(
            "Show all players",
            key="available_players_show_all",
        )
    i += 1

    show_qo = False
    show_poach = False
    show_pt = False
    show_contracts = False

    if qo_enabled:
        with cols[i]:
            show_qo = st.toggle(
                "Show only QOs",
                key="available_players_show_qo",
            )
        i += 1
        with cols[i]:
            show_poach = st.toggle(
                "Show only Poach-eligible",
                key="available_players_show_poach",
            )
        i += 1

    if pt_enabled:
        with cols[i]:
            show_pt = st.toggle(
                "Show only PT",
                key="available_players_show_pt",
            )
        i += 1

    if contracts_enabled:
        with cols[i]:
            show_contracts = st.toggle(
                "Show only Contracts",
                key="available_players_show_contracts",
            )

    sort_col = st.selectbox("Sort by", options=sort_cols, key="avail_sort_col")
    sort_desc = st.toggle("Descending", key="avail_sort_desc")

    reset_filters_clicked = st.button("Reset Filters", key="available_players_reset_filters")

    if reset_filters_clicked:
        for _key in [
            "available_players_search",
            "available_players_pos_filter",
            "available_players_pos_filter_choice",
            "available_players_show_all",
            "available_players_show_qo",
            "available_players_show_poach",
            "available_players_show_pt",
            "available_players_show_contracts",
            "avail_sort_col",
            "avail_sort_desc",
        ]:
            st.session_state.pop(_key, None)
        st.rerun()

    predraft_qo_keys: set[str] = set()
    predraft_qo_level_by_key: dict[str, int] = {}
    predraft_by_team = _load_predraft_qos_by_team(dsn, league_key, season_year) if (dsn and qo_enabled) else {}

    for _tk, rec in (predraft_by_team or {}).items():
        for lvl, pk in (rec.get("levels") or {}).items():
            if pk:
                predraft_qo_keys.add(str(pk))
                predraft_qo_level_by_key[str(pk)] = int(lvl)

    # Live QO overlay for Available Players.
    # public.qualifying_offer remains the original submission source; this overlay
    # replays live draft decisions so declined/poached-away QOs become free agents.
    predraft_qos_for_live_available = _load_predraft_qos(dsn, league_key, season_year) if (dsn and qo_enabled) else {}
    current_qos_for_available = _compute_current_qos_from_log(
        predraft_qos_for_live_available,
        state.pick_log,
    ) if qo_enabled else {}

    current_qo_keys: set[str] = set()
    current_qo_level_by_key: dict[str, int] = {}
    for _tk, _levels in (current_qos_for_available or {}).items():
        for lvl, pk in (_levels or {}).items():
            if pk:
                pk = str(pk)
                current_qo_keys.add(pk)
                current_qo_level_by_key[pk] = int(lvl)
                # Team ownership is intentionally not used here; Available Players
                # should show live QO status, not original/released team ownership.

    # Determine current round from the on-clock pick (used for "poach-eligible" filter)
    _cp = state.picks.get(state.clock.current_pick_id)
    current_round = int(_cp.round_number) if _cp else 0

    def matches_name(name: str) -> bool:
        return player_search_matches(search, name)

    def matches_positions(all_positions: list[str]) -> bool:
        if not pos_filter:
            return True
        return any(pp in pos_filter for pp in all_positions)

    canonical_keeper_status_by_player_key = _fetch_available_players_keeper_status(
        dsn,
        league_key,
        season_year,
        [str(p.player_key) for p in state.players.values()],
    ) if (dsn and (contracts_enabled or qo_enabled)) else {}

    canonical_reserved_keys = {
        str(pk) for pk, row in canonical_keeper_status_by_player_key.items()
        if row.get("status_type") in {"CONTRACT", "FT"}
        or (row.get("status_type") == "QO" and str(pk) in current_qo_keys)
    }

    canonical_contract_keys = {
        str(pk) for pk, row in canonical_keeper_status_by_player_key.items()
        if row.get("status_type") == "CONTRACT"
    }

    legacy_reserved_keys = set(st.session_state.get("contracted_keys", set()) or set())
    released_original_qo_keys = set(predraft_qo_keys) - set(current_qo_keys)

    # Legacy session-state reserved keys can include original QOs. Once live QO
    # replay releases an original QO, Available Players must treat that player
    # as a free agent again.
    legacy_reserved_keys.difference_update(released_original_qo_keys)

    contracted_keys = set() if not (contracts_enabled or pt_enabled or qo_enabled) else (
        legacy_reserved_keys | canonical_reserved_keys
    )

    # Build available players list:
    # - default: exclude drafted + exclude contracted/PT
    # - BUT if "Show only QOs" is on, include drafted QOs too
    available_players = []
    for p in state.players.values():
        pk = str(p.player_key)

        # Toggle semantics:
        # - Default view hides drafted/reserved players.
        # - Show only QOs / Poach-eligible / Contracts operate from the full relevant source pool.
        # - Therefore those toggles must bypass the default reserved-player exclusion.
        bypass_drafted_filter = bool(show_all_players or show_qo or show_poach or show_contracts)
        bypass_reserved_filter = bool(show_all_players or show_qo or show_poach or show_contracts)

        if pk in drafted and not bypass_drafted_filter:
            continue

        if (pk in contracted_keys) and not bypass_reserved_filter:
            continue

        # PT toggle
        if show_pt:
            pt_map = getattr(state, "pt_player_team_map", None) or {}
            if pk not in pt_map:
                continue

        # Contracts toggle: canonical contracts only, regardless of Show all players.
        if show_contracts:
            if pk not in canonical_contract_keys:
                continue

        if not matches_name(p.name):
            continue

        all_pos_list = [_pos_label(x) for x in p.positions]
        if not matches_positions(all_pos_list):
            continue

        # Show only QOs = restrict to CURRENT live QOs, not released original QOs.
        if show_qo and (pk not in current_qo_keys):
            continue

        # Show only Poach-eligible = current live QOs whose level is strictly greater than the current round.
        if show_poach:
            cur_round = int(current_round or 0)
            # Only meaningful during QO rounds
            if 1 <= cur_round <= get_active_qo_rounds():
                lvl = current_qo_level_by_key.get(pk)
                # eligible means: player's current QO level is strictly greater than current round
                if lvl is None or int(lvl) <= cur_round:
                    continue
            else:
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
            if pk not in canonical_contract_keys:
                continue
            filtered.append(p)
        available_players = filtered

    def _contract_label(yrs: int) -> str:
        if yrs == 1:
            return "1-year"
        return f"{yrs}-years"

    status_by_player_key: dict[str, str] = {}

    # Canonical DB statuses first: contracts, FT, and CURRENT live QOs only.
    # Released original QOs must not keep their QO label.
    # PT can still override below if ever enabled.
    for pk, row in (canonical_keeper_status_by_player_key or {}).items():
        pk = str(pk)
        status_type = str(row.get("status_type") or "")
        if status_type == "QO" and pk not in current_qo_keys:
            continue
        label = str(row.get("status_label") or "")
        if label:
            status_by_player_key[pk] = label

    # DB draft selections override keeper/QO/FT display status when visible in show-all views.
    for pk, row in (db_draft_status_by_player_key or {}).items():
        kind = str(row.get("pick_kind") or "").upper()
        status_by_player_key[str(pk)] = f"Drafted {kind}" if kind else "Drafted"

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

    # 2) Current live QOs only.
    # Original QO submissions remain available for original/audit tables,
    # but Available Players should classify only current QO rights as QOs.
    for pk, lvl in (current_qo_level_by_key or {}).items():
        pk = str(pk)
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

    team_name_by_player_key: dict[str, str] = {
        str(pk): str(row.get("team_name") or "")
        for pk, row in (canonical_keeper_status_by_player_key or {}).items()
        if row.get("team_name")
        and (
            row.get("status_type") in {"CONTRACT", "FT"}
            or (row.get("status_type") == "QO" and str(pk) in current_qo_keys)
        )
    }
    draft_pick_label_by_player_key: dict[str, str] = {}
    draft_pick_sort_by_player_key: dict[str, int] = {}

    # DB draft selections override Team Name and Draft Pick labels when visible in show-all views.
    for pk, row in (db_draft_status_by_player_key or {}).items():
        pk = str(pk)
        if row.get("team_name"):
            team_name_by_player_key[pk] = str(row.get("team_name") or "")
        if row.get("pick_id"):
            draft_pick_label_by_player_key[pk] = str(row.get("pick_id") or "")
            draft_pick_sort_by_player_key[pk] = -1

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

    nffl_available_stats_by_player_key = _fetch_available_players_nffl_stats(
        dsn,
        league_key,
        season_year,
        [str(p.player_key) for p in available_players],
    )
    df = _apply_nffl_available_stats_columns(
        df,
        available_players,
        nffl_available_stats_by_player_key,
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


    # NFFL_AVAILABLE_PLAYERS_TABLE_RENDER_START
    # Final display stage. The prior code builds/filter/sorts data; this block renders it.
    if df is None or df.empty:
        st.caption("No players match the current Available Players filters.")
        return

    df = df.copy()

    sort_col_effective = sort_col if sort_col in df.columns else "Actual Rank"
    sort_desc_effective = bool(sort_desc)

    sort_by = sort_col_effective
    if sort_col_effective == "Draft Pick":
        sort_by = "_draft_pick_sort"
    elif sort_col_effective == "Contract Years":
        sort_by = "_contract_years_sort"

    if sort_by in df.columns:
        # Keep sort deterministic and stable.
        if sort_by.startswith("_") or sort_col_effective in {
            "Actual Rank", "% Ros", "Fan Pts", "GP",
            "Pass Yds", "Pass TD", "Pass INT",
            "Rush Att", "Rush Yds", "Rush TD",
            "Rec", "Rec Yds", "Rec TD", "Targets", "Fum Lost",
            "Contract Years",
        }:
            df[sort_by] = pd.to_numeric(df[sort_by], errors="coerce")

        secondary_cols = []
        if "Player Name" in df.columns and sort_by != "Player Name":
            secondary_cols.append("Player Name")

        sort_cols_effective = [sort_by] + secondary_cols
        ascending_flags = [not sort_desc_effective] + [True for _ in secondary_cols]

        df = df.sort_values(
            by=sort_cols_effective,
            ascending=ascending_flags,
            na_position="last",
            kind="mergesort",
        )

    df_disp = df.drop(
        columns=["_draft_pick_sort", "_contract_years_sort"],
        errors="ignore",
    ).copy()

    # Keep numeric columns numeric so manual Streamlit column sorting works.
    numeric_cols = [
        "Actual Rank", "% Ros", "Fan Pts", "GP",
        "Pass Yds", "Pass TD", "Pass INT",
        "Rush Att", "Rush Yds", "Rush TD",
        "Rec", "Rec Yds", "Rec TD", "Targets", "Fum Lost",
        "Contract Years",
    ]
    for c in numeric_cols:
        if c in df_disp.columns:
            df_disp[c] = pd.to_numeric(df_disp[c], errors="coerce")

    if "Fan Pts" in df_disp.columns:
        df_disp["Fan Pts"] = pd.to_numeric(df_disp["Fan Pts"], errors="coerce").round(1)

    column_config = {}
    if "Actual Rank" in df_disp.columns:
        column_config["Actual Rank"] = st.column_config.NumberColumn("Actual Rank", format="%d")
    if "% Ros" in df_disp.columns:
        column_config["% Ros"] = st.column_config.NumberColumn("% Ros", format="%d")
    if "Fan Pts" in df_disp.columns:
        column_config["Fan Pts"] = st.column_config.NumberColumn("Fan Pts", format="%.1f")

    st.caption(f"Showing {len(df_disp)} players")
    table_height = min(2600, max(1200, int(len(df_disp) * 34) + 90))

    st.dataframe(
        df_disp,
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        height=table_height,
    )
    # NFFL_AVAILABLE_PLAYERS_TABLE_RENDER_END


def render_teams(state: DraftState, contract_years_2026: dict[str, int]) -> None:
    import pandas as pd

    st.subheader("Teams")

    # Teams tab order should follow the canonical draft-slot order from Postgres.
    # The in-memory/autosave order can be stale across browser sessions after lottery apply.
    def _load_teams_tab_order_from_db() -> list[str]:
        try:
            import os
            import psycopg

            dsn = get_postgres_dsn()
            draft_key = os.environ.get("DRAFTBOARD_DRAFT_KEY", "nffl_2026_preseason")
            if not dsn or not draft_key:
                return []

            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH first_round AS (
                            SELECT MIN(round_number) AS round_number
                            FROM nffl.draft_pick
                            WHERE draft_key = %s
                        )
                        SELECT dp.column_team_key
                        FROM nffl.draft_pick dp
                        JOIN first_round fr
                          ON fr.round_number = dp.round_number
                        WHERE dp.draft_key = %s
                        ORDER BY dp.slot_number
                        """,
                        (draft_key, draft_key),
                    )
                    rows = [str(r[0] or "").strip() for r in cur.fetchall()]

            return [tk for tk in rows if tk]
        except Exception:
            return []

    db_order = _load_teams_tab_order_from_db()
    state_order = list(getattr(state, "draft_order_team_keys_by_slot", []) or [])

    if db_order and all(tk in state.teams for tk in db_order):
        order = db_order
        state.draft_order_team_keys_by_slot = db_order
    else:
        order = state_order

    # Keep all loaded teams visible even if an order source is incomplete.
    ordered_seen = set()
    teams = []
    for tk in order:
        if tk in state.teams and tk not in ordered_seen:
            teams.append(state.teams[tk])
            ordered_seen.add(tk)
    for tk, team in state.teams.items():
        if tk not in ordered_seen:
            teams.append(team)
            ordered_seen.add(tk)
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

    # Current QO display outcomes = predraft + replayed live QO/FA/POACH outcomes.
    # IMPORTANT: this is display-only; future poach eligibility still uses _compute_current_qos_from_log.
    current_levels = _compute_qo_display_from_log(predraft_levels, state.pick_log)

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

        current_row = {
            "Owner": owner_name_by_team_key.get(tkey, ""),
            "Team": team_name,
        }
        for _lvl in range(1, get_active_qo_rounds() + 1):
            current_row[f"QO{_lvl}"] = _fmt_qo_cell(state, cur_lvls.get(_lvl, ""))
        current_row["Updated"] = _fmt_updated(_updated_ts_for_team(tkey))
        current_rows.append(current_row)

        predraft_row = {
            "Owner": owner_name_by_team_key.get(tkey, ""),
            "Team": team_name,
        }
        for _lvl in range(1, get_active_qo_rounds() + 1):
            predraft_row[f"QO{_lvl}"] = _fmt_qo_cell(state, pre_lvls.get(_lvl, ""))
        predraft_row["Updated"] = _fmt_updated(predraft_updated_at.get(tkey, ""))
        predraft_rows.append(predraft_row)

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

        if pick and pick.round_number <= get_active_qo_rounds():
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

    import pandas as pd
    st.markdown(pd.DataFrame(rows).to_html(index=False, escape=False), unsafe_allow_html=True)


NFFL_GATEWAY_COOKIE_NAME = "nffl_team_gateway"


def _nffl_gateway_secret() -> str:
    return (
        os.environ.get("NFFL_GATEWAY_COOKIE_SECRET")
        or os.environ.get("MLF_AUTH_COOKIE_SECRET")
        or "nffl-dev-gateway-secret-change-me"
    )


def _nffl_gateway_sign(payload_b64: str) -> str:
    return hmac.new(
        _nffl_gateway_secret().encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _nffl_gateway_pack(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = _nffl_gateway_sign(payload_b64)
    return f"{payload_b64}.{sig}"


def _nffl_gateway_unpack(token: str | None) -> dict | None:
    if not token or "." not in str(token):
        return None

    payload_b64, sig = str(token).split(".", 1)
    expected = _nffl_gateway_sign(payload_b64)
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        padded = payload_b64 + ("=" * (-len(payload_b64) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None

    role = str(payload.get("role") or "")
    if role not in {"commissioner", "manager"}:
        return None

    return payload


def _nffl_gateway_auth_context(payload: dict | None) -> dict[str, object]:
    if not payload:
        return {
            "is_authenticated": False,
            "user_id": None,
            "email": None,
            "is_site_admin": False,
            "league_role": None,
            "franchise_id": None,
            "team_key": None,
            "team_name": None,
            "must_change_password": False,
            "role": "public",
            "acting_as": "public",
        }

    role = str(payload.get("role") or "")
    team_key = payload.get("team_key")
    team_name = payload.get("team_name")

    return {
        "is_authenticated": True,
        "user_id": None,
        "email": str(payload.get("display_name") or team_name or role),
        "is_site_admin": role == "commissioner",
        "league_role": "commissioner" if role == "commissioner" else "manager",
        "franchise_id": None,
        "team_key": team_key,
        "team_name": team_name,
        "must_change_password": False,
        "role": role,
        "acting_as": str(payload.get("acting_as") or (f"{role}:{team_name or 'all'}")),
    }


def _nffl_gateway_team_options(dsn: str) -> list[dict[str, object]]:
    sql = """
        SELECT
            t.team_key,
            t.team_name,
            t.owner_name
        FROM nffl.team t
        JOIN nffl.v_active_season_context ctx
          ON ctx.current_league_key=t.league_key
         AND ctx.current_season_year=t.season_year
        ORDER BY t.team_name;
    """
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())






def _render_nffl_gateway_audit_tab(dsn: str) -> None:
    st.subheader("Team Gateway Audit")
    st.caption("Recent browser identity selections and switches. This is commissioner-only.")

    clear_col, _ = st.columns([1, 2])
    with clear_col:
        confirm_clear = st.checkbox(
            "Confirm clear audit test data",
            value=False,
            key="gateway_audit_confirm_clear",
        )
        if st.button(
            "Clear Gateway Audit Test Data",
            disabled=not confirm_clear,
            key="gateway_audit_clear_button",
        ):
            try:
                with psycopg.connect(dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM nffl.team_gateway_audit;")
                    conn.commit()
                st.success("Gateway audit test data cleared.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not clear Gateway Audit data: {exc}")

    sql = """
        SELECT
            created_at_utc,
            action_type,
            selected_role,
            selected_team_name,
            previous_role,
            previous_team_name,
            action_note
        FROM nffl.team_gateway_audit
        ORDER BY audit_id DESC
        LIMIT 200;
    """

    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = list(cur.fetchall())
    except Exception as exc:
        st.error(f"Could not load Team Gateway audit: {exc}")
        return

    if not rows:
        st.info("No Team Gateway activity has been logged yet.")
        return

    import pandas as pd

    df = pd.DataFrame(rows)

    df["created_at_utc"] = (
        pd.to_datetime(df["created_at_utc"], utc=True)
        .dt.tz_convert("America/New_York")
        .dt.strftime("%Y-%m-%d %-I:%M:%S %p %Z")
    )

    df = df.rename(
        columns={
            "created_at_utc": "Time Eastern",
            "action_type": "Action",
            "selected_role": "Selected Role",
            "selected_team_name": "Selected Team",
            "previous_role": "Previous Role",
            "previous_team_name": "Previous Team",
            "action_note": "Note",
        }
    )

    st.dataframe(df, hide_index=True, use_container_width=True)


def _nffl_gateway_audit(
    dsn: str,
    *,
    selected_role: str,
    selected_team_key: str | None,
    selected_team_name: str | None,
    previous_role: str | None,
    previous_team_key: str | None,
    previous_team_name: str | None,
    action_type: str,
    action_note: str | None = None,
) -> None:
    sql = """
        INSERT INTO nffl.team_gateway_audit (
            league_key,
            season_year,
            selected_role,
            selected_team_key,
            selected_team_name,
            previous_role,
            previous_team_key,
            previous_team_name,
            action_type,
            action_note,
            query_string
        )
        SELECT
            ctx.current_league_key,
            ctx.current_season_year,
            %(selected_role)s,
            %(selected_team_key)s,
            %(selected_team_name)s,
            %(previous_role)s,
            %(previous_team_key)s,
            %(previous_team_name)s,
            %(action_type)s,
            %(action_note)s,
            %(query_string)s
        FROM nffl.v_active_season_context ctx;
    """
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    {
                        "selected_role": selected_role,
                        "selected_team_key": selected_team_key,
                        "selected_team_name": selected_team_name,
                        "previous_role": previous_role,
                        "previous_team_key": previous_team_key,
                        "previous_team_name": previous_team_name,
                        "action_type": action_type,
                        "action_note": action_note,
                        "query_string": str(dict(st.query_params)),
                    },
                )
            conn.commit()
    except Exception as exc:
        st.warning(f"Team Gateway audit logging failed: {exc}")




def _nffl_gateway_claim_team_link(dsn: str, link_token: str) -> dict[str, object] | None:
    sql_select = """
        SELECT
            l.league_key,
            l.season_year,
            l.team_key,
            t.team_name,
            t.owner_name
        FROM nffl.team_gateway_link l
        JOIN nffl.team t
          ON t.league_key=l.league_key
         AND t.season_year=l.season_year
         AND t.team_key=l.team_key
        JOIN nffl.v_active_season_context ctx
          ON ctx.current_league_key=l.league_key
         AND ctx.current_season_year=l.season_year
        WHERE l.link_token = %(link_token)s
          AND l.is_active = true
        LIMIT 1;
    """

    sql_update = """
        UPDATE nffl.team_gateway_link
           SET claim_count = claim_count + 1,
               last_claimed_at_utc = now(),
               updated_at_utc = now()
         WHERE link_token = %(link_token)s
           AND is_active = true;
    """

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_select, {"link_token": link_token})
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(sql_update, {"link_token": link_token})
        conn.commit()

    return dict(row)


def _render_nffl_manager_links_tab(dsn: str) -> None:
    st.subheader("Manager Team Links")
    st.caption(
        "Give each manager only their own link. Opening the link remembers that browser as the correct team."
    )

    base_url = os.environ.get("NFFL_PUBLIC_URL", "https://nffl.majorleaguefantasy.app").rstrip("/")

    sql = """
        SELECT
            t.team_name,
            t.owner_name,
            l.is_active,
            l.claim_count,
            l.last_claimed_at_utc,
            l.link_token
        FROM nffl.team_gateway_link l
        JOIN nffl.team t
          ON t.league_key=l.league_key
         AND t.season_year=l.season_year
         AND t.team_key=l.team_key
        JOIN nffl.v_active_season_context ctx
          ON ctx.current_league_key=l.league_key
         AND ctx.current_season_year=l.season_year
        ORDER BY t.team_name;
    """

    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = list(cur.fetchall())
    except Exception as exc:
        st.error(f"Could not load manager links: {exc}")
        return

    if not rows:
        st.info("No manager links have been generated yet.")
        return

    import pandas as pd

    out = []
    for row in rows:
        claimed = row.get("last_claimed_at_utc")
        if claimed:
            claimed_text = (
                pd.to_datetime(claimed, utc=True)
                .tz_convert("America/New_York")
                .strftime("%Y-%m-%d %-I:%M:%S %p %Z")
            )
        else:
            claimed_text = ""

        out.append(
            {
                "Team": row["team_name"],
                "Manager": row["owner_name"],
                "Active": row["is_active"],
                "Claims": row["claim_count"],
                "Last Claimed Eastern": claimed_text,
                "Manager Link": f"{base_url}/?team={row['link_token']}",
            }
        )

    import html as html_lib
    import streamlit.components.v1 as components

    def _esc(value: object) -> str:
        return html_lib.escape("" if value is None else str(value), quote=True)

    html_rows = []
    for idx, link_row in enumerate(out, start=1):
        manager_url = str(link_row.get("Manager Link") or "")
        html_rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{_esc(link_row.get('Team'))}</td>"
            f"<td>{_esc(link_row.get('Manager'))}</td>"
            f"<td>{_esc(link_row.get('Active'))}</td>"
            f"<td>{_esc(link_row.get('Claims'))}</td>"
            f"<td>{_esc(link_row.get('Last Claimed Eastern'))}</td>"
            "<td>"
            f"<button class='copy-btn' type='button' data-copy='{_esc(manager_url)}' onclick='copyManagerLink(this)'>Copy</button>"
            "<span class='copy-status' aria-live='polite'></span>"
            "</td>"
            f"<td><code class='manager-link'>{_esc(manager_url)}</code></td>"
            "</tr>"
        )

    html_doc = f"""
    <style>
      .manager-links-wrap {{
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        width: 100%;
        color: #f8fafc;
      }}
      table.manager-links-table {{
        border-collapse: collapse;
        width: 100%;
        table-layout: fixed;
        font-size: 14px;
        background: #0f172a;
        color: #f8fafc;
      }}
      .manager-links-table th,
      .manager-links-table td {{
        border: 1px solid rgba(226, 232, 240, 0.24);
        padding: 8px 10px;
        vertical-align: top;
        text-align: left;
        color: #f8fafc;
      }}
      .manager-links-table th {{
        background: #1e293b;
        font-weight: 700;
        color: #f8fafc;
      }}
      .manager-links-table th:nth-child(1),
      .manager-links-table td:nth-child(1) {{
        width: 42px;
        text-align: right;
      }}
      .manager-links-table th:nth-child(4),
      .manager-links-table td:nth-child(4),
      .manager-links-table th:nth-child(5),
      .manager-links-table td:nth-child(5) {{
        width: 70px;
      }}
      .manager-links-table th:nth-child(7),
      .manager-links-table td:nth-child(7) {{
        width: 120px;
      }}
      .copy-btn {{
        cursor: pointer;
        border: 1px solid rgba(226, 232, 240, 0.45);
        border-radius: 6px;
        padding: 5px 10px;
        background: #f8fafc;
        color: #0f172a;
        font-weight: 700;
      }}
      .copy-btn:hover {{
        background: #e2e8f0;
      }}
      .copy-status {{
        display: block;
        margin-top: 4px;
        font-size: 12px;
        color: #86efac;
        min-height: 16px;
        font-weight: 700;
      }}
      .manager-link {{
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
        color: #bae6fd;
        background: rgba(15, 23, 42, 0.75);
      }}
    </style>

    <div class="manager-links-wrap">
      <table class="manager-links-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Team</th>
            <th>Manager</th>
            <th>Active</th>
            <th>Claims</th>
            <th>Last Claimed Eastern</th>
            <th>Copy</th>
            <th>Manager Link</th>
          </tr>
        </thead>
        <tbody>
          {''.join(html_rows)}
        </tbody>
      </table>
    </div>

    <script>
      function copyManagerLink(button) {{
        const text = button.getAttribute("data-copy") || "";
        const status = button.parentElement.querySelector(".copy-status");

        function markDone(message) {{
          const originalText = button.getAttribute("data-original-text") || button.textContent || "Copy";
          button.setAttribute("data-original-text", originalText);
          button.textContent = message;
          if (status) {{
            status.textContent = message;
          }}
          window.setTimeout(() => {{
            button.textContent = originalText;
            if (status) {{
              status.textContent = "";
            }}
          }}, 1800);
        }}

        function fallbackCopy() {{
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.setAttribute("readonly", "");
          ta.style.position = "fixed";
          ta.style.left = "-9999px";
          ta.style.top = "0";
          document.body.appendChild(ta);
          ta.focus();
          ta.select();

          try {{
            const ok = document.execCommand("copy");
            markDone(ok ? "Copied" : "Copy failed");
          }} catch (err) {{
            markDone("Copy failed");
          }} finally {{
            document.body.removeChild(ta);
          }}
        }}

        if (navigator.clipboard && window.isSecureContext) {{
          navigator.clipboard.writeText(text)
            .then(() => markDone("Copied"))
            .catch(() => fallbackCopy());
        }} else {{
          fallbackCopy();
        }}
      }}
    </script>
    """

    manager_links_height = 115 + (len(out) * 64)
    components.html(html_doc, height=manager_links_height, scrolling=False)


def _render_nffl_team_gateway(dsn: str) -> dict[str, object]:
    commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"

    cookie_manager = stx.CookieManager(key="nffl_team_gateway_cookie_manager")
    cookies = cookie_manager.get_all() or {}

    if commissioner_url:
        payload = {
            "role": "commissioner",
            "team_key": None,
            "team_name": "Commissioner",
            "display_name": "Commissioner",
            "acting_as": "commissioner",
            "created_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
        }
        ctx = _nffl_gateway_auth_context(payload)
        st.session_state["nffl_gateway_context"] = ctx

        with st.expander("Team Gateway: Commissioner", expanded=False):
            st.caption("Commissioner URL is active. Manager cookies are ignored on this page.")
            st.warning("Commissioner View can see and edit all teams.")
            st.code("https://nffl.majorleaguefantasy.app/?commissioner=1")

        return ctx

    link_token = str(
        st.query_params.get("team")
        or st.query_params.get("team_token")
        or ""
    ).strip()

    previous_payload = _nffl_gateway_unpack(cookies.get(NFFL_GATEWAY_COOKIE_NAME))

    if link_token:
        linked_team = _nffl_gateway_claim_team_link(dsn, link_token)

        if not linked_team:
            st.error("This team link is invalid or inactive. Ask the Commissioner for a fresh link.")
            return _nffl_gateway_auth_context(None)

        payload = {
            "role": "manager",
            "team_key": str(linked_team["team_key"]),
            "team_name": str(linked_team["team_name"]),
            "display_name": str(linked_team["owner_name"] or linked_team["team_name"]),
            "acting_as": f"manager:{linked_team['team_name']}",
            "created_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
        }

        _nffl_gateway_audit(
            dsn,
            selected_role="manager",
            selected_team_key=str(payload.get("team_key") or "") or None,
            selected_team_name=str(payload.get("team_name") or "") or None,
            previous_role=str(previous_payload.get("role") or "") if previous_payload else None,
            previous_team_key=str(previous_payload.get("team_key") or "") if previous_payload else None,
            previous_team_name=str(previous_payload.get("team_name") or "") if previous_payload else None,
            action_type="CLAIM_TEAM_LINK",
            action_note="Browser gateway identity set from manager team link.",
        )

        token = _nffl_gateway_pack(payload)
        cookie_manager.set(
            NFFL_GATEWAY_COOKIE_NAME,
            token,
            expires_at=datetime.utcnow() + timedelta(days=180),
        )

        ctx = _nffl_gateway_auth_context(payload)
        st.session_state["nffl_gateway_context"] = ctx

        # Remove the token from the visible URL after claiming.
        try:
            st.query_params.clear()
        except Exception:
            pass

        st.success(f"Remembered this browser as {ctx.get('team_name')}.")
        return ctx

    cookie_payload = _nffl_gateway_unpack(cookies.get(NFFL_GATEWAY_COOKIE_NAME))

    # Commissioner identity is never honored on the normal public URL.
    if cookie_payload and cookie_payload.get("role") == "commissioner":
        cookie_manager.delete(NFFL_GATEWAY_COOKIE_NAME)
        cookie_payload = None

    if cookie_payload:
        ctx = _nffl_gateway_auth_context(cookie_payload)
        st.session_state["nffl_gateway_context"] = ctx

        label = str(ctx.get("team_name") or "Manager")
        with st.expander(f"Team Gateway: {label}", expanded=False):
            st.caption("This browser is remembered for draft/QO/FT actions.")
            st.warning("Team Gateway changes are logged and reviewable by the Commissioner.")
            if st.button("Clear This Browser", key="nffl_gateway_clear_main"):
                _nffl_gateway_audit(
                    dsn,
                    selected_role="public",
                    selected_team_key=None,
                    selected_team_name=None,
                    previous_role=str(ctx.get("role") or ""),
                    previous_team_key=str(ctx.get("team_key") or "") or None,
                    previous_team_name=str(ctx.get("team_name") or "") or None,
                    action_type="CLEAR_BROWSER",
                    action_note="Browser gateway identity cleared from the app. Re-entry requires a team link.",
                )
                cookie_manager.delete(NFFL_GATEWAY_COOKIE_NAME)
                st.session_state.pop("nffl_gateway_context", None)
                st.rerun()

        return ctx

    st.markdown("### Team Gateway")
    st.info("Open the unique team link sent by the Commissioner to enter the Draft Board.")
    return _nffl_gateway_auth_context(None)


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
    NFFL Team Gateway authorization.

    This is intentionally low-friction. It is not password security.
    It makes sure draft submissions from this browser are attributed to the
    remembered team, and only that team can submit when on the clock.
    """
    gateway = st.session_state.get("nffl_gateway_context") or {}

    if gateway.get("role") == "commissioner":
        return True

    team_key = str(gateway.get("team_key") or "")
    if not team_key:
        return False

    current_pick_id = getattr(state.clock, "current_pick_id", None)
    if not current_pick_id or current_pick_id not in state.picks:
        return False

    pick = state.picks[current_pick_id]
    return str(getattr(pick, "owner_team_key", "") or "") == team_key


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
        if ps.round_number <= get_active_qo_rounds() and ps.selected_ts_iso is None and ps.selected_player_key:
            qo_ph += 1
        # DEBUG sidebar is commissioner-only AND requires password unlock.
    is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
    debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

    if debug_enabled:
        st.sidebar.write(f"qo_placeholders_rounds1_{get_active_qo_rounds()}_render:", qo_ph)

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

            div[data-testid="stElementContainer"]:has(.draftboard-hero-shell),
            div[data-testid="stMarkdownContainer"]:has(.draftboard-hero-shell) {
              width: 100% !important;
              max-width: none !important;
            }

            .draftboard-hero-shell {
              width: 100%;
              max-width: none;
              margin: 0 0 16px 0;
              padding: 0;
              box-sizing: border-box;
            }

            .draftboard-hero {
              display: flex;
              align-items: center;
              justify-content: flex-start;
              gap: 26px;
              width: 100%;
              min-height: 126px;
              background: linear-gradient(135deg, #0A0A08 0%, #34302B 54%, #5c1717 100%);
              border: 3px solid #D50A0A;
              border-radius: 18px;
              padding: 24px 32px;
              margin: 0;
              box-sizing: border-box;
              box-shadow: 0 0 20px rgba(213, 10, 10, 0.26);
            }

            .draftboard-logo-fallback {
              width: 92px;
              height: 92px;
              border-radius: 14px;
              border: 3px solid #D50A0A;
              display: flex;
              align-items: center;
              justify-content: center;
              font-weight: 950;
              font-size: 1.72rem;
              color: #FF7900;
              background: #34302B;
              letter-spacing: 0.03em;
              flex: 0 0 auto;
            }

            .draftboard-title-wrap {
              line-height: 1.0;
              text-align: left;
            }

            .draftboard-kicker {
              color: #FF7900;
              font-weight: 850;
              font-size: 1.00rem;
              letter-spacing: 0.20em;
              text-transform: uppercase;
              margin-bottom: 9px;
            }

            .draftboard-title {
              color: #f7f7f7;
              font-weight: 950;
              font-size: clamp(2.8rem, 7vw, 5.6rem);
              letter-spacing: -0.05em;
              text-transform: uppercase;
            }

            .draftboard-title-year {
              color: #FF7900;
            }

            @media (max-width: 640px) {
              .draftboard-hero {
                gap: 14px;
                min-height: 102px;
                padding: 18px 16px;
              }

              .draftboard-logo-fallback {
                width: 62px;
                height: 62px;
                font-size: 1.10rem;
              }

              .draftboard-kicker {
                font-size: 0.80rem;
                letter-spacing: 0.12em;
                margin-bottom: 6px;
              }

              .draftboard-title {
                font-size: clamp(1.9rem, 9vw, 2.8rem);
              }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    from draftboard.state.league_profile import get_active_league_profile
    _active_profile = get_active_league_profile()
    _league_name = str((_active_profile.get("league") or {}).get("name") or "Draft Board").strip()
    st.markdown(
        f"""
        <div class="draftboard-hero-shell">
          <div class="draftboard-hero">
            <div class="draftboard-logo-fallback">NFFL</div>
            <div class="draftboard-title-wrap">
              <div class="draftboard-kicker">Official Draft Board</div>
              <div class="draftboard-title">Draft Board <span class="draftboard-title-year">{season_year}</span></div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    gateway_ctx = _render_nffl_team_gateway(get_postgres_dsn())
    st.session_state["nffl_gateway_context"] = gateway_ctx

    if not gateway_ctx.get("is_authenticated"):
        return

    state.commissioner_mode = str(gateway_ctx.get("role") or "") == "commissioner"

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

    tab_manager_links = None
    tab_gateway_audit = None

    if get_active_qualifying_offers_enabled():
        tab_names = ["Draft Board", "Available Players", "Teams", "QOs", "Draft Lottery", "Pick Tracker", "Draft Statistics"]
        if state.commissioner_mode:
            tab_names.extend(["Manager Links", "Gateway Audit"])
        tabs = st.tabs(tab_names)
        tab_board, tab_players, tab_teams, tab_qos, tab_lottery, tab_tracker, tab_stats = tabs[:7]
        if state.commissioner_mode:
            tab_manager_links = tabs[7]
            tab_gateway_audit = tabs[8]
    else:
        tab_names = ["Draft Board", "Available Players", "Teams", "Draft Lottery", "Pick Tracker", "Draft Statistics"]
        if state.commissioner_mode:
            tab_names.extend(["Manager Links", "Gateway Audit"])
        tabs = st.tabs(tab_names)
        tab_board, tab_players, tab_teams, tab_lottery, tab_tracker, tab_stats = tabs[:6]
        tab_qos = None
        if state.commissioner_mode:
            tab_manager_links = tabs[6]
            tab_gateway_audit = tabs[7]

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
            if ps.round_number <= get_active_qo_rounds():
                continue
            if ps.selected_ts_iso is not None:
                continue
            if ps.selected_player_key:
                keeper_ph += 1
        # DEBUG sidebar is commissioner-only AND requires password unlock.
        is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
        debug_enabled = bool(is_commissioner_url and st.session_state.get("commissioner_authed"))

        if debug_enabled:
            st.sidebar.write(f"keeper_placeholders_rounds{get_active_qo_rounds()+1}_25_render:", keeper_ph)

        # Auto-heal order if missing/invalid (deterministic from first standard round owners)
        expected_slots = get_active_manager_count()
        first_standard_round = get_active_first_standard_round()
        if len(order) != expected_slots or unknown:
            healed = []
            for slot in range(1, expected_slots + 1):
                pid = f"R{first_standard_round:02d}-{slot:02d}"
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

        render_postgres_board_html(
            dsn=get_postgres_dsn(),
            draft_key=os.environ.get("DRAFTBOARD_DRAFT_KEY", "nffl_2026_preseason"),
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
        render_nffl_team_workbench(get_postgres_dsn(), gateway_context=st.session_state.get("nffl_gateway_context"))

    if tab_qos is not None:
        with tab_qos:
            render_qos_tab(state)

    with tab_lottery:
        render_draft_lottery_tab(state)

    with tab_tracker:
        render_pick_tracker(state, owner_name_by_team_key)

    with tab_stats:
        render_draft_statistics_tab(state)

    if tab_manager_links is not None:
        with tab_manager_links:
            _render_nffl_manager_links_tab(get_postgres_dsn())

    if tab_gateway_audit is not None:
        with tab_gateway_audit:
            _render_nffl_gateway_audit_tab(get_postgres_dsn())

    st.divider()

    if state.commissioner_mode:
        auth_ctx = st.session_state.get("nffl_gateway_context") or _nffl_gateway_auth_context(None)
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
