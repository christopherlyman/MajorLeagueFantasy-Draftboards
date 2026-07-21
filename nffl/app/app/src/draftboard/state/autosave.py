from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from draftboard.domain.models import PickLogEntry, PickSlot, Player, Team
from draftboard.state.store import DraftClock, DraftState
from draftboard.domain.rules import DEFAULT_QO_ALLOWS_FREE_AGENTS

# Always anchor autosave relative to the DraftBoard folder (not process CWD).
# autosave.py is: app/src/draftboard/state/autosave.py
_DRAFTBOARD_DIR = Path(__file__).resolve().parents[3]
AUTOSAVE_PATH = Path(os.environ.get("DRAFTBOARD_AUTOSAVE_PATH", str(_DRAFTBOARD_DIR / "draft_state_autosave.json"))).expanduser()

# Draft identity for DB-backed state comes from canonical runtime config.
ENV_DSN_PRIMARY = "POSTGRES_DSN"
ENV_DSN_FALLBACK = "MLF_POSTGRES_DSN"


def _dict_get(d: dict[str, Any], key: str, default: Any) -> Any:
    v = d.get(key, default)
    return default if v is None else v


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _get_draft_key() -> str:
    from draftboard.state.runtime import get_draft_key
    return get_draft_key()


def _try_db_fetch(draft_key: str) -> dict[str, Any] | None:
    dsn = os.environ.get(ENV_DSN_PRIMARY)
    if not dsn or not str(dsn).strip():
        dsn = os.environ.get(ENV_DSN_FALLBACK)
    if not dsn or not str(dsn).strip():
        return None
    dsn = str(dsn).strip()

    try:
        import psycopg  # lazy import so file load works even without DB deps
    except Exception:
        return None

    sql = """
    SELECT state_json
    FROM public.draftboard_state
    WHERE draft_key = %(draft_key)s
    """
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"draft_key": draft_key})
                row = cur.fetchone()
        if not row:
            return None
        state_json = row[0]  # psycopg adapts jsonb to Python object
        return state_json if isinstance(state_json, dict) else None
    except Exception:
        # DB is optional at runtime; never take down the app.
        return None



def get_canonical_state_sha256() -> str | None:
    """
    Return the SHA-256 marker for the canonical persisted DraftState.

    This performs a metadata-only query. It does not load or deserialize
    state_json and does not modify application or database state.
    """
    dsn = os.environ.get(ENV_DSN_PRIMARY)
    if not dsn or not str(dsn).strip():
        dsn = os.environ.get(ENV_DSN_FALLBACK)
    if not dsn or not str(dsn).strip():
        return None
    dsn = str(dsn).strip()

    try:
        import psycopg
    except Exception:
        return None

    sql = """
    SELECT state_sha256
    FROM public.draftboard_state
    WHERE draft_key = %(draft_key)s
    """

    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"draft_key": _get_draft_key()})
                row = cur.fetchone()

        if not row or row[0] is None:
            return None

        return str(row[0]).strip() or None
    except Exception:
        # Canonical persistence is optional at runtime; synchronization
        # checks must never take down the DraftBoard.
        return None



def _try_db_upsert(*, draft_key: str, schema_version: str, raw_bytes: bytes, state_obj: dict[str, Any]) -> None:
    dsn = os.environ.get(ENV_DSN_PRIMARY)
    if not dsn or not str(dsn).strip():
        dsn = os.environ.get(ENV_DSN_FALLBACK)
    if not dsn or not str(dsn).strip():
        return
    dsn = str(dsn).strip()

    try:
        import psycopg  # lazy import
    except Exception:
        return

    state_sha = _sha256_hex(raw_bytes)

    upsert_sql = """
    INSERT INTO public.draftboard_state (draft_key, schema_version, state_json, state_sha256, updated_at_utc)
    VALUES (%(draft_key)s, %(schema_version)s, %(state_json)s::jsonb, %(state_sha256)s, now())
    ON CONFLICT (draft_key) DO UPDATE SET
      schema_version = EXCLUDED.schema_version,
      state_json     = EXCLUDED.state_json,
      state_sha256   = EXCLUDED.state_sha256,
      updated_at_utc = now()
    """
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    upsert_sql,
                    {
                        "draft_key": draft_key,
                        "schema_version": schema_version,
                        "state_json": json.dumps(state_obj, separators=(",", ":"), ensure_ascii=False),
                        "state_sha256": state_sha,
                    },
                )
            conn.commit()
    except Exception:
        # DB is optional; ignore failures to keep UI stable.
        return


def _raw_to_state(raw: dict[str, Any]) -> DraftState | None:
    required_top_keys = {"teams", "players", "picks", "pick_order", "clock"}
    if not isinstance(raw, dict) or not required_top_keys.issubset(set(raw.keys())):
        return None

    try:
        teams = {k: Team(**v) for k, v in raw["teams"].items()}
        players = {k: Player(**v) for k, v in raw["players"].items()}
        picks = {k: PickSlot(**v) for k, v in raw["picks"].items()}
        pick_order = list(raw["pick_order"])
        pick_log = [PickLogEntry(**e) for e in raw.get("pick_log", [])]

        clock_raw = raw.get("clock", {}) or {}
        clock = DraftClock(
            current_pick_id=clock_raw["current_pick_id"],
            auto_advance=_dict_get(clock_raw, "auto_advance", True),
            is_running=_dict_get(clock_raw, "is_running", False),
            pick_started_ts_iso=_dict_get(clock_raw, "pick_started_ts_iso", None),
            pick_paused_ts_iso=_dict_get(clock_raw, "pick_paused_ts_iso", None),
            elapsed_paused_seconds=int(_dict_get(clock_raw, "elapsed_paused_seconds", 0) or 0),
            seconds_per_pick=int(_dict_get(clock_raw, "seconds_per_pick", 24 * 60 * 60)),
            weekends_count=bool(_dict_get(clock_raw, "weekends_count", False)),
            timezone=str(_dict_get(clock_raw, "timezone", "America/New_York")),
        )

        return DraftState(
            schema_version=str(raw.get("schema_version", "1.0")),
            rules_qo_allows_free_agents=bool(raw.get("rules_qo_allows_free_agents", DEFAULT_QO_ALLOWS_FREE_AGENTS)),
            commissioner_mode=bool(raw.get("commissioner_mode", False)),
            active_team_key=str(raw.get("active_team_key", next(iter(teams.keys())))),
            view_mode=str(raw.get("view_mode", "SLOT")),
            clock=clock,
            teams=teams,
            players=players,
            picks=picks,
            pick_order=pick_order,
            pick_log=pick_log,
            # ✅ persist/restore canonical column order (slot 1..16)
            draft_order_team_keys_by_slot=list(raw.get("draft_order_team_keys_by_slot", []) or []),
            # ✅ persist/restore PT ownership map (player_key -> team_key)
            pt_player_team_map=dict(raw.get("pt_player_team_map", {}) or {}),
        )
    except Exception:
        return None


def save_autosave(state: DraftState) -> None:
    """
    Persist state to:
      1) app/draft_state_autosave.json
      2) public.draftboard_state (if POSTGRES_DSN or MLF_POSTGRES_DSN is set)
    """
    data = asdict(state)

    # Stable JSON on disk (and for hashing)
    raw_text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    raw_bytes = raw_text.encode("utf-8")

    AUTOSAVE_PATH.write_text(raw_text, encoding="utf-8")

    draft_key = _get_draft_key()
    schema_version = str(data.get("schema_version", "1.0"))
    try:
        _try_db_upsert(draft_key=draft_key, schema_version=schema_version, raw_bytes=raw_bytes, state_obj=data)
        # TEMP DEBUG
        import streamlit as st
        st.session_state["debug_last_db_upsert_ok"] = True
        st.session_state["debug_last_db_upsert_draft_key"] = draft_key
    except Exception as e:
        # TEMP DEBUG
        import streamlit as st
        st.session_state["debug_last_db_upsert_ok"] = False
        st.session_state["debug_last_db_upsert_draft_key"] = draft_key
        st.session_state["debug_last_db_upsert_error"] = repr(e)
        # re-raise so we SEE it during commissioner save
        raise


def try_load_autosave() -> DraftState | None:
    """
    Load priority:
      1) Postgres row for DRAFTBOARD_DRAFT_KEY (if configured + present)
      2) app/draft_state_autosave.json (legacy)
    """
    draft_key = _get_draft_key()

    raw = _try_db_fetch(draft_key)
    if raw is not None:
        st = _raw_to_state(raw)
        if st is not None:
            return st

    if not AUTOSAVE_PATH.exists():
        return None

    try:
        raw2 = json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

    return _raw_to_state(raw2)
