from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List

from draftboard.domain.models import PickLogEntry, PickSlot, Player, Position, RoundType, Team
from draftboard.state.store import DraftClock, DraftState
from draftboard.domain.rules import DEFAULT_QO_ALLOWS_FREE_AGENTS
from draftboard.domain.rules import QO_ROUNDS, ROUNDS_TOTAL


def _team_from_dict(d: Dict[str, Any]) -> Team:
    return Team(**d)


def _player_from_dict(d: Dict[str, Any]) -> Player:
    # positions stored as strings -> Position enum
    d2 = dict(d)
    d2["positions"] = [Position(p) for p in d2["positions"]]
    return Player(**d2)


def _pick_from_dict(d: Dict[str, Any]) -> PickSlot:
    d2 = dict(d)
    d2["round_type"] = RoundType(d2["round_type"])
    return PickSlot(**d2)


def _log_from_dict(d: Dict[str, Any]) -> PickLogEntry:
    d2 = dict(d)
    d2["primary_position"] = Position(d2["primary_position"])
    return PickLogEntry(**d2)


def export_state_to_json_text(state: DraftState) -> str:
    payload: Dict[str, Any] = {
        "schema_version": state.schema_version,
        # NEW: declare canonical team keyspace
        "team_keyspace": "YAHOO",

        "league": {
            "teams_count": len(state.teams),
            "rounds_total": ROUNDS_TOTAL,
            "qo_rounds": QO_ROUNDS,
        },
        "rules": {
            "qo_allows_free_agents": state.rules_qo_allows_free_agents,
        },
        "ui": {
            "commissioner_mode": state.commissioner_mode,
            "active_team_key": state.active_team_key,
            "view_mode": state.view_mode,
        },
        "clock": {
            "current_pick_id": state.clock.current_pick_id,
            "auto_advance": state.clock.auto_advance,

            # OPTIONAL but recommended (round-trip all DraftClock fields)
            "is_running": state.clock.is_running,
            "pick_started_ts_iso": state.clock.pick_started_ts_iso,
            "pick_paused_ts_iso": state.clock.pick_paused_ts_iso,
            "elapsed_paused_seconds": state.clock.elapsed_paused_seconds,
            "seconds_per_pick": state.clock.seconds_per_pick,
            "weekends_count": state.clock.weekends_count,
            "timezone": state.clock.timezone,
        },

        # NEW: state extensions used by UI/board
        "state_ext": {
            "pt_player_team_map": dict(getattr(state, "pt_player_team_map", {}) or {}),
            "draft_order_team_keys_by_slot": list(getattr(state, "draft_order_team_keys_by_slot", []) or []),
        },

        "data": {
            "teams": {k: asdict(v) for k, v in state.teams.items()},
            "players": {k: _player_to_dict(v) for k, v in state.players.items()},
            "picks": {k: _pick_to_dict(v) for k, v in state.picks.items()},
            "pick_order": list(state.pick_order),
            "pick_log": [_log_to_dict(x) for x in state.pick_log],
        },
    }
    return json.dumps(payload, indent=2)


def import_state_from_json_text(text: str) -> DraftState:
    payload = json.loads(text)

    schema_version = payload.get("schema_version", "1.0")
    rules_qo_allows_free_agents = bool(payload.get("rules", {}).get("qo_allows_free_agents", DEFAULT_QO_ALLOWS_FREE_AGENTS))

    ui = payload.get("ui", {}) or {}
    clock = payload.get("clock", {}) or {}
    ext = payload.get("state_ext", {}) or {}

    teams = {k: _team_from_dict(v) for k, v in payload["data"]["teams"].items()}
    players = {k: _player_from_dict(v) for k, v in payload["data"]["players"].items()}
    picks = {k: _pick_from_dict(v) for k, v in payload["data"]["picks"].items()}
    pick_order = list(payload["data"]["pick_order"])
    pick_log = [_log_from_dict(x) for x in payload["data"]["pick_log"]]

    # NEW: restore extensions (back-compat defaults)
    pt_player_team_map = dict(ext.get("pt_player_team_map", {}) or {})
    draft_order_team_keys_by_slot = list(ext.get("draft_order_team_keys_by_slot", []) or [])

    return DraftState(
        schema_version=schema_version,
        rules_qo_allows_free_agents=rules_qo_allows_free_agents,
        commissioner_mode=bool(ui.get("commissioner_mode", False)),
        active_team_key=str(ui.get("active_team_key", next(iter(teams.keys())))),
        view_mode=str(ui.get("view_mode", "SLOT")),
        clock=DraftClock(
            current_pick_id=str(clock.get("current_pick_id", pick_order[0] if pick_order else "")),
            auto_advance=bool(clock.get("auto_advance", True)),

            # OPTIONAL but recommended (back-compat defaults come from DraftClock dataclass)
            is_running=bool(clock.get("is_running", False)),
            pick_started_ts_iso=clock.get("pick_started_ts_iso"),
            pick_paused_ts_iso=clock.get("pick_paused_ts_iso"),
            elapsed_paused_seconds=int(clock.get("elapsed_paused_seconds", 0) or 0),
            seconds_per_pick=int(clock.get("seconds_per_pick", 24 * 60 * 60) or (24 * 60 * 60)),
            weekends_count=bool(clock.get("weekends_count", False)),
            timezone=str(clock.get("timezone", "America/New_York")),
        ),
        teams=teams,
        players=players,
        picks=picks,
        pick_order=pick_order,
        pick_log=pick_log,

        # NEW: attach extensions
        pt_player_team_map=pt_player_team_map,
        draft_order_team_keys_by_slot=draft_order_team_keys_by_slot,
    )


def _player_to_dict(p: Player) -> Dict[str, Any]:
    d = asdict(p)

    # positions may be Position enums OR raw strings depending on how Player objects were loaded (DB vs JSON).
    pos_out: list[str] = []
    for pos in (getattr(p, "positions", None) or []):
        try:
            v = pos.value  # Position enum
        except Exception:
            v = str(pos)   # raw string
        pos_out.append(str(v))

    d["positions"] = pos_out
    return d


def _pick_to_dict(p: PickSlot) -> Dict[str, Any]:
    d = asdict(p)

    rt = getattr(p, "round_type", None)
    try:
        d["round_type"] = rt.value  # enum
    except Exception:
        d["round_type"] = str(rt)   # raw string

    return d


def _log_to_dict(e: PickLogEntry) -> Dict[str, Any]:
    d = asdict(e)

    pp = getattr(e, "primary_position", None)
    try:
        d["primary_position"] = pp.value   # enum
    except Exception:
        d["primary_position"] = str(pp)    # raw string

    return d
