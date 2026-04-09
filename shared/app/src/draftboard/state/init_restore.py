from __future__ import annotations

import streamlit as st

from draftboard.data.picks_grid import build_picks_grid
from draftboard.domain.models import PickSlot, Team
from draftboard.domain.rules import QO_ROUNDS, ROUNDS_TOTAL
from draftboard.domain.rules import DEFAULT_QO_ALLOWS_FREE_AGENTS
from draftboard.state.autosave import try_load_autosave, save_autosave
from draftboard.state.runtime import get_league_key, get_postgres_dsn, get_season_year
from draftboard.state.league_profile import get_active_draft_order_mode, get_active_first_standard_round, get_active_qualifying_offers_enabled, get_active_rounds_total
from draftboard.state.store import DraftClock, DraftState, has_state, init_state


def _team_to_slot_from_order(order: list[str] | None) -> dict[str, int]:
    """
    order: list length 16 where index 0 => slot 1 holds team_key
    returns: {team_key: slot_num}
    """
    out: dict[str, int] = {}
    if isinstance(order, list) and len(order) == 16:
        for i, tk in enumerate(order, start=1):
            if tk:
                out[str(tk)] = int(i)
    return out


def _is_legacy_team_keyspace(team_keys: list[str]) -> bool:
    # Legacy keys looked like TEAM_01..TEAM_16
    return any(str(k).startswith("TEAM_") for k in (team_keys or []))


def _build_canonical_teams_from_yahoo_rows(yahoo_team_rows: list[dict]) -> dict[str, Team]:
    teams: dict[str, Team] = {}
    for i, r in enumerate(yahoo_team_rows or [], start=1):
        tk = str(r.get("team_key") or "").strip()
        nm = str(r.get("team_name") or "").strip()
        if not tk:
            continue
        abbr = "".join([p[0].upper() for p in nm.replace("'", "").split() if p][:3]) or f"T{i}"
        teams[tk] = Team(team_key=tk, name=nm or tk, abbr=abbr, color=None)
    return teams


def _build_legacy_to_canonical_team_key_map(*, legacy_teams: dict[str, Team], canonical_teams: dict[str, Team]) -> dict[str, str]:
    """
    Deterministic mapping strategy:
      1) Exact team name match (case-insensitive)
      2) Exact abbr match (case-insensitive)
    If any legacy teams remain unmapped, we STOP deterministically (no guessing).
    """
    name_to_canon = {(t.name or "").strip().lower(): k for k, t in canonical_teams.items()}
    abbr_to_canon = {(t.abbr or "").strip().lower(): k for k, t in canonical_teams.items()}

    mapping: dict[str, str] = {}
    for lk, lt in legacy_teams.items():
        nm = (lt.name or "").strip().lower()
        ab = (lt.abbr or "").strip().lower()

        ck = name_to_canon.get(nm) if nm else None
        if not ck and ab:
            ck = abbr_to_canon.get(ab)
        if ck:
            mapping[str(lk)] = str(ck)

    # Partial mapping only (no guessing).
    # If some legacy teams can't be mapped deterministically, we leave them unmapped
    # so callers can fall back to TEAM_XX -> slot order mapping if needed.
    return mapping


def _canon_team_key_from_mixed_key(
    tk: str,
    *,
    order: list[str] | None,
    legacy_to_canon: dict[str, str] | None,
) -> str:
    tk = str(tk or "").strip()
    if not tk:
        return ""
    legacy_to_canon = dict(legacy_to_canon or {})

    # Preferred: explicit legacy->canonical map (TEAM_04 -> yahoo key)
    mapped = legacy_to_canon.get(tk)
    if mapped:
        return str(mapped)

    # Fallback: TEAM_XX -> slot -> order[slot-1]
    if tk.startswith("TEAM_"):
        try:
            n = int(tk.replace("TEAM_", "").strip())
        except Exception:
            return tk
        if isinstance(order, list) and len(order) == 16 and 1 <= n <= 16:
            v = str(order[n - 1] or "").strip()
            return v if v else tk

    return tk


def _build_draft_order_from_first_standard_round(
    picks: dict[str, PickSlot],
    *,
    first_standard_round: int,
) -> list[str]:
    """
    Returns a list length 16 where index 0 => slot 1 holds team_key.

    Canonical draft slot order must be derived from the first standard round
    for the active league profile.
    """
    out = [""] * 16
    for p in picks.values():
        if int(getattr(p, "round_number", 0) or 0) != int(first_standard_round):
            continue
        try:
            slot = int(getattr(p, "slot", 0) or 0)
        except Exception:
            continue
        if 1 <= slot <= 16:
            out[slot - 1] = str(getattr(p, "original_team_key", "") or "")
    return out


def _apply_mlf_contract_pt_prefill(
    *,
    picks: dict[str, PickSlot],
    teams: dict[str, Team],
    players: dict,
    draft_order_team_keys_by_slot: list[str],
    contract_rows: list[dict] | None,
    pt_map: dict[str, str] | None,
    contract_rows_are_canonical_team_keys: bool,
) -> None:
    """
    MLF-only prefill behavior:
      - clear placeholder selections in standard rounds (6..25)
      - place contracts first in deepest rounds
      - place PT immediately above contracts
    Behavior is intentionally preserved from prior logic; this only removes
    restore-vs-fresh-boot drift by centralizing the shared algorithm.
    """
    team_to_slot = _team_to_slot_from_order(draft_order_team_keys_by_slot)

    def _normalize_team_key_via_order(tk: str) -> str:
        tk = str(tk or "").strip()
        if not tk:
            return ""
        if tk in teams:
            return tk
        if tk.startswith("TEAM_"):
            try:
                n = int(tk.replace("TEAM_", "").strip())
            except Exception:
                return tk
            order = list(draft_order_team_keys_by_slot or [])
            if 1 <= n <= 16 and isinstance(order, list) and len(order) == 16:
                mapped = str(order[n - 1] or "")
                return mapped if mapped else tk
        return tk

    # Clear placeholder selections in standard rounds before rebuilding.
    for ps in (picks or {}).values():
        if int(getattr(ps, "round_number", 0) or 0) <= QO_ROUNDS:
            continue
        if getattr(ps, "selected_ts_iso", None) is not None:
            continue
        if getattr(ps, "selected_player_key", None) is None:
            continue
        ps.selected_player_key = None
        ps.selected_ts_iso = None

    contracts_by_team: dict[str, list[str]] = {}
    pts_by_team: dict[str, list[str]] = {}

    for row in (contract_rows or []):
        pkey = str(row.get("yahoo_player_key") or "").strip()
        if not pkey or pkey not in players:
            continue

        if pkey in set((pt_map or {}).keys()):
            continue

        raw_team_key = str(row.get("team_key") or "").strip()
        if contract_rows_are_canonical_team_keys:
            tkey = raw_team_key if raw_team_key in teams else ""
        else:
            tkey = _normalize_team_key_via_order(raw_team_key)

        if not tkey or tkey not in teams:
            continue

        contracts_by_team.setdefault(tkey, []).append(pkey)

    for pkey, tkey in (pt_map or {}).items():
        pkey = str(pkey or "").strip()
        if not pkey or pkey not in players:
            continue
        tkey = _normalize_team_key_via_order(str(tkey))
        if not tkey or tkey not in teams:
            continue
        pts_by_team.setdefault(tkey, []).append(pkey)

    def _rank_sort_key(pkey: str):
        rv = getattr(players[pkey], "rank_value", None)
        return (1, 999999) if rv is None else (0, rv)

    already_selected = {ps.selected_player_key for ps in picks.values() if ps.selected_player_key}

    start_round = QO_ROUNDS + 1
    end_round = ROUNDS_TOTAL

    all_team_keys = sorted(set(list(contracts_by_team.keys()) + list(pts_by_team.keys())))

    for team_key in all_team_keys:
        slot_num = team_to_slot.get(team_key)
        if not slot_num:
            continue

        contract_keys = sorted(set(contracts_by_team.get(team_key, []) or []), key=_rank_sort_key)
        pt_keys = sorted(set(pts_by_team.get(team_key, []) or []), key=_rank_sort_key)

        ordered_keys = contract_keys + pt_keys

        rnd = end_round
        for pkey in ordered_keys:
            if pkey in already_selected:
                continue

            while rnd >= start_round:
                pick_id = f"R{rnd:02d}-{slot_num:02d}"
                ps = picks.get(pick_id)
                if ps is None:
                    rnd -= 1
                    continue
                if ps.selected_player_key is not None:
                    rnd -= 1
                    continue

                ps.selected_player_key = pkey
                ps.selected_ts_iso = None
                already_selected.add(pkey)
                rnd -= 1
                break




def _build_draft_order_from_profile(picks: dict[str, PickSlot]) -> list[str]:
    order_mode = str(get_active_draft_order_mode()).strip().lower()

    if order_mode in {"straight", "snake"}:
        return _build_draft_order_from_first_standard_round(
            picks,
            first_standard_round=get_active_first_standard_round(),
        )

    raise ValueError(f"Unsupported draft.order_mode: {order_mode!r}")


def _load_mlf_keeper_runtime_bundle(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
) -> tuple[dict, dict[str, str], set[str], list[dict], dict[str, int]]:
    """
    MLF-only runtime data bundle:
      - full player universe
      - PT map
      - contracted keys
      - contract rows
      - contract years map

    Session-state side effects are intentionally preserved for current MLF behavior.
    """
    from draftboard.data.db_players import (
        load_active_available_players,
        load_contracted_player_keys,
        load_contracts_current,
        load_contract_years_map,
        load_pt_players,
    )

    pt_map = load_pt_players(dsn, league_key, season_year)
    st.session_state["pt_player_team_map"] = dict(pt_map)

    players = load_active_available_players(dsn)

    contracted_keys = load_contracted_player_keys(dsn)
    contracted_keys = set(contracted_keys or set()) | set(pt_map.keys())
    st.session_state["contracted_keys"] = contracted_keys

    contract_rows = load_contracts_current(dsn, league_key, season_year)
    st.session_state["contract_rows"] = contract_rows

    contract_years_map = load_contract_years_map(dsn, league_key, season_year)
    st.session_state["contract_years_map"] = dict(contract_years_map)

    return players, dict(pt_map), set(contracted_keys), list(contract_rows or []), dict(contract_years_map)


def _restore_mlf_state_from_autosave(restored: DraftState) -> DraftState:
    # Refresh player universe + contracts on restore (stats/contracts may have changed)
    from draftboard.data.db_players import load_franchise_season_team_order, load_yahoo_team_map

    try:
        dsn = get_postgres_dsn()
        league_key = get_league_key()
        season_year = get_season_year()
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    players, pt_map, contracted_keys, contract_rows, contract_years_map = _load_mlf_keeper_runtime_bundle(
        dsn=dsn,
        league_key=league_key,
        season_year=season_year,
    )
    restored.pt_player_team_map = dict(pt_map)

    yahoo_team_rows = load_yahoo_team_map(dsn, league_key, season_year)

    # Capture legacy team objects + any prior saved order BEFORE overwriting restored.teams
    legacy_teams = dict(getattr(restored, "teams", {}) or {})
    prior_order = list(getattr(restored, "draft_order_team_keys_by_slot", []) or [])

    # Rebuild canonical teams on restore (autosave teams may be stale keyspace)
    yahoo_team_rows = sorted(list(yahoo_team_rows or []), key=lambda r: str(r.get("team_key") or ""))

    canonical_teams = _build_canonical_teams_from_yahoo_rows(yahoo_team_rows)
    restored.teams = canonical_teams

    # SSOT draft order (franchise_id order) must drive slot mapping + TEAM_XX normalization
    # Franchise/team roster order is NOT the same thing as draft order.
    # Draft order SSOT = state.draft_order_team_keys_by_slot (persisted in autosave/draftboard_state).
    # We may load franchise order for other UI uses, but we never overwrite draft order here.
    st.session_state["franchise_team_keys_by_franchise_id_order"] = list(
        load_franchise_season_team_order(dsn, league_key, season_year) or []
    )

    # Build deterministic legacy TEAM_XX -> canonical Yahoo team_key map (by name/abbr)
    legacy_to_canon: dict[str, str] = {}
    canon_to_legacy: dict[str, str] = {}

    try:
        if legacy_teams and _is_legacy_team_keyspace(list(legacy_teams.keys())):
            legacy_to_canon = _build_legacy_to_canonical_team_key_map(
                legacy_teams=legacy_teams,
                canonical_teams=restored.teams,
            )
            canon_to_legacy = {v: k for k, v in legacy_to_canon.items()}
    except Exception:
        # No guessing: if mapping cannot be built, leave empty and rely on other logic.
        legacy_to_canon = {}
        canon_to_legacy = {}

    # Attach mapping to state (so render_app can normalize QO team_keyspace)
    st.session_state["legacy_to_canonical_team_key_map"] = dict(legacy_to_canon)
    st.session_state["canonical_to_legacy_team_key_map"] = dict(canon_to_legacy)
    st.session_state["legacy_map_size"] = len(legacy_to_canon or {})
    st.session_state["legacy_map_team_04"] = str((legacy_to_canon or {}).get("TEAM_04") or "")

    # Ensure active team key is valid after rebuilding teams
    if str(getattr(restored, "active_team_key", "") or "") not in restored.teams:
        restored.active_team_key = next(iter(restored.teams.keys()), "")

    # ✅ HEAL LEGACY PICK OWNERSHIP (TEAM_XX) ON RESTORE
    # If any pick.owner_team_key is not a key in restored.teams, the board headers will go blank
    # and contract/PT prefill will not map to slots.
    # Right fix: rebuild picks from canonical teams (Yahoo keys) and replay saved selections.
    bad_owner_keys = set()
    for ps in (getattr(restored, "picks", {}) or {}).values():
        otk = str(getattr(ps, "owner_team_key", "") or "").strip()
        if otk and otk not in restored.teams:
            bad_owner_keys.add(otk)

    if bad_owner_keys:
        # Deterministic rebuild from canonical teams (no mock_data)
        new_picks, new_pick_order = build_picks_grid(
            restored.teams,
            order_mode=get_active_draft_order_mode(),
            first_standard_round=get_active_first_standard_round(),
            qualifying_offers=get_active_qualifying_offers_enabled(),
            rounds_total=get_active_rounds_total(),
        )

        # Replay saved selections by pick_id (preserve real picks + keeper placeholders)
        old_picks = dict(getattr(restored, "picks", {}) or {})
        for pid, old_ps in old_picks.items():
            if pid not in new_picks:
                continue
            new_ps = new_picks[pid]
            new_ps.selected_player_key = getattr(old_ps, "selected_player_key", None)
            new_ps.selected_ts_iso = getattr(old_ps, "selected_ts_iso", None)

        restored.picks = new_picks
        restored.pick_order = list(new_pick_order)

        # Preserve prior saved draft order if present; normalize legacy->canonical deterministically.
        legacy_to_canon = dict(st.session_state.get("legacy_to_canonical_team_key_map", {}) or {})
        if isinstance(prior_order, list) and len(prior_order) == 16:
            norm = []
            for tk in prior_order:
                tks = str(tk or "").strip()
                norm.append(str(legacy_to_canon.get(tks, tks)))
            # Keep only if it points at canonical teams
            if all((not x) or (x in restored.teams) for x in norm):
                restored.draft_order_team_keys_by_slot = norm
            else:
                restored.draft_order_team_keys_by_slot = _build_draft_order_from_profile(restored.picks)
        else:
            restored.draft_order_team_keys_by_slot = _build_draft_order_from_profile(restored.picks)

        # Ensure clock points at a valid pick id after rebuild
        cur = str(getattr(restored.clock, "current_pick_id", "") or "")
        if cur not in restored.picks:
            restored.clock.current_pick_id = restored.pick_order[0] if restored.pick_order else ""

        # Persist healed state
        save_autosave(restored)

    # Keep ALL players in state (including contracted). We filter contracted out of UI/pick dropdown.
    restored.players = players

    # Ensure slot-based draft order exists on restore BEFORE contract prefill
    order = getattr(restored, "draft_order_team_keys_by_slot", None)
    if not isinstance(order, list) or len(order) != 16:
        restored.draft_order_team_keys_by_slot = _build_draft_order_from_profile(restored.picks)

    # Normalize PT map values using deterministic legacy->canonical mapping (do NOT depend on slot order)
    legacy_to_canon = dict(st.session_state.get("legacy_to_canonical_team_key_map", {}) or {})
    pt_map_norm: dict[str, str] = {}
    for pkey, tkey in (pt_map or {}).items():
        pk = str(pkey or "").strip()
        tk = str(tkey or "").strip()
        if not pk:
            continue
        # If DB still stores TEAM_XX, map it; otherwise keep as-is
        order = list(getattr(restored, "draft_order_team_keys_by_slot", []) or [])
        pt_map_norm[pk] = _canon_team_key_from_mixed_key(tk, order=order, legacy_to_canon=legacy_to_canon)
    restored.pt_player_team_map = dict(pt_map_norm)
    pt_map = dict(pt_map_norm)

    _apply_mlf_contract_pt_prefill(
        picks=restored.picks,
        teams=restored.teams,
        players=players,
        draft_order_team_keys_by_slot=list(getattr(restored, "draft_order_team_keys_by_slot", []) or []),
        contract_rows=contract_rows,
        pt_map=pt_map,
        contract_rows_are_canonical_team_keys=False,
    )

    # One-time canonical rewrite: eliminate TEAM_XX autosave permanently
    # (We only do this after teams/picks/order are canonical and contract prefill is done.)
    try:
        save_autosave(restored)
    except Exception:
        pass

    return restored


def _build_mlf_initial_state() -> DraftState:
    from draftboard.data.db_players import load_yahoo_team_map

    try:
        dsn = get_postgres_dsn()
        league_key = get_league_key()
        season_year = get_season_year()
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    # Canonical teams (Yahoo team keys) — deterministic placeholder order: ORDER BY team_key
    yahoo_team_rows = load_yahoo_team_map(dsn, league_key, season_year)
    yahoo_team_rows = sorted(list(yahoo_team_rows or []), key=lambda r: str(r.get("team_key") or ""))

    teams: dict[str, Team] = {}
    for i, r in enumerate(yahoo_team_rows, start=1):
        tk = str(r.get("team_key") or "").strip()
        nm = str(r.get("team_name") or "").strip()
        if not tk:
            continue
        abbr = "".join([p[0].upper() for p in nm.replace("'", "").split() if p][:3]) or f"T{i}"
        teams[tk] = Team(team_key=tk, name=nm or tk, abbr=abbr, color=None)

    players, pt_map, contracted_keys, contract_rows, contract_years_map = _load_mlf_keeper_runtime_bundle(
        dsn=dsn,
        league_key=league_key,
        season_year=season_year,
    )

    picks, pick_order = build_picks_grid(
        teams,
        order_mode=get_active_draft_order_mode(),
        first_standard_round=get_active_first_standard_round(),
        qualifying_offers=get_active_qualifying_offers_enabled(),
        rounds_total=get_active_rounds_total(),
    )

    _apply_mlf_contract_pt_prefill(
        picks=picks,
        teams=teams,
        players=players,
        draft_order_team_keys_by_slot=_build_draft_order_from_profile(picks),
        contract_rows=contract_rows,
        pt_map=pt_map,
        contract_rows_are_canonical_team_keys=True,
    )

    initial = DraftState(
        schema_version="1.0",
        rules_qo_allows_free_agents=DEFAULT_QO_ALLOWS_FREE_AGENTS,
        commissioner_mode=False,
        active_team_key=next(iter(teams.keys())),
        view_mode="SLOT",
        clock=DraftClock(current_pick_id=pick_order[0], auto_advance=True),
        teams=teams,
        players=players,
        picks=picks,
        pick_order=pick_order,
        pick_log=[],
        pt_player_team_map=dict(pt_map),
        draft_order_team_keys_by_slot=_build_draft_order_from_profile(picks),
    )
    return initial

def ensure_initialized() -> None:
    if has_state():
        return

    restored = try_load_autosave()
    if restored is not None:
        restored = _restore_mlf_state_from_autosave(restored)
        init_state(restored)
        return

    # ---- fresh boot (no autosave) ----
    initial = _build_mlf_initial_state()
    init_state(initial)
