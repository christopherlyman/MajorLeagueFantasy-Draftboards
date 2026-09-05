from __future__ import annotations

import streamlit as st

from draftboard.data.picks_grid import build_picks_grid
from draftboard.data.draft_projection import load_draft_state_projection
from draftboard.domain.models import PickSlot, Team
from draftboard.domain.rules import QO_ROUNDS, ROUNDS_TOTAL
from draftboard.domain.rules import DEFAULT_QO_ALLOWS_FREE_AGENTS
from draftboard.state.runtime import get_draft_key, get_league_key, get_postgres_dsn, get_season_year
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
        pt_player_team_map={},
        draft_order_team_keys_by_slot=_build_draft_order_from_profile(picks),
    )
    return initial

def ensure_initialized() -> None:
    """
    Initialize Streamlit state from PostgreSQL relational truth.

    Rich team/player presentation data is built first. Draft picks,
    ownership, selections, keeper placeholders, current QOs, pick log,
    draft order, and clock state are projected from the dedicated
    MLF relational model.
    """
    if has_state():
        return

    base = _build_mlf_initial_state()

    projected = load_draft_state_projection(
        dsn=get_postgres_dsn(),
        draft_key=get_draft_key(),
        base_state=base,
    )

    init_state(projected)
