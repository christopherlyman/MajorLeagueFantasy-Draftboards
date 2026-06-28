from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import random
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
import requests
import streamlit as st

from draftboard.state.autosave import save_autosave
from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


LOTTERY_STATUS_ACTIVE = "ACTIVE"
LOTTERY_STATUS_COMPLETE = "COMPLETE"
LOTTERY_STATUS_APPLIED = "APPLIED"
LOTTERY_STATUS_VOID = "VOID"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _team_name(state: Any, team_key: str) -> str:
    team = (getattr(state, "teams", {}) or {}).get(str(team_key))
    return str(getattr(team, "name", "") or team_key)


def _ordered_team_keys(state: Any) -> list[str]:
    teams = getattr(state, "teams", {}) or {}
    order = [str(tk) for tk in (getattr(state, "draft_order_team_keys_by_slot", []) or []) if str(tk) in teams]
    remaining = sorted(str(tk) for tk in teams.keys() if str(tk) not in set(order))
    return order + remaining


def _actor_label() -> str:
    for key in ("auth_team_name", "auth_email", "local_auth_email"):
        val = str(st.session_state.get(key) or "").strip()
        if val:
            return val
    return "commissioner"


def _walk_json(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json(v)


def _scalarize_json(obj: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                nested = _scalarize_json(v)
                for nk, nv in nested.items():
                    out.setdefault(nk, nv)
            else:
                out.setdefault(str(k), "" if v is None else str(v))
    elif isinstance(obj, list):
        for item in obj:
            nested = _scalarize_json(item)
            for nk, nv in nested.items():
                out.setdefault(nk, nv)
    return out


def _parse_int_safe(raw: Any) -> int | None:
    try:
        val = str(raw or "").strip()
        if not val:
            return None
        return int(val)
    except Exception:
        return None


def _find_yahoo_auth_module_path() -> str:
    candidates = [
        "/app/scripts/yahoo/auth.py",
        "/app/app/scripts/yahoo/auth.py",
        "/league_runtime/app/scripts/yahoo/auth.py",
        "/workspace/app/scripts/yahoo/auth.py",
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            return str(p)
    raise RuntimeError("Could not find Yahoo auth.py inside the app container.")


def _get_yahoo_access_token() -> str:
    auth_path = _find_yahoo_auth_module_path()
    spec = importlib.util.spec_from_file_location("nffl_lottery_yahoo_auth", auth_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Yahoo auth module from {auth_path}.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(mod.get_access_token())


def _load_active_context(dsn: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    current_season_year,
                    current_league_key,
                    prior_season_year,
                    prior_league_key,
                    draft_key
                FROM nffl.v_active_season_context
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("No active season context found in nffl.v_active_season_context.")
    return dict(row)


def _fetch_yahoo_json(token: str, url: str) -> dict[str, Any]:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=45,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Yahoo API returned HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _extract_standings_team_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for node in _walk_json(payload):
        if not isinstance(node, dict) or "team" not in node:
            continue

        flat = _scalarize_json(node["team"])
        team_key = str(flat.get("team_key") or "").strip()
        if not team_key or ".t." not in team_key or team_key in seen:
            continue

        rank = _parse_int_safe(flat.get("rank"))
        playoff_seed = _parse_int_safe(flat.get("playoff_seed"))
        clinched = str(flat.get("clinched_playoffs") or "").strip() == "1"

        # For the standings endpoint, all usable team rows should have rank.
        if rank is None:
            continue

        seen.add(team_key)
        rows.append(
            {
                "source_team_key": team_key,
                "source_team_id": str(flat.get("team_id") or "").strip(),
                "source_team_name": str(flat.get("name") or "").strip(),
                "rank": rank,
                "playoff_seed": playoff_seed,
                "clinched_playoffs": clinched,
                "wins": str(flat.get("wins") or "").strip(),
                "losses": str(flat.get("losses") or "").strip(),
                "ties": str(flat.get("ties") or "").strip(),
                "percentage": str(flat.get("percentage") or "").strip(),
                "points_for": str(flat.get("points_for") or "").strip(),
            }
        )

    rows.sort(key=lambda r: int(r["rank"]))
    return rows


def _load_team_bridge_map(
    *,
    dsn: str,
    current_league_key: str,
    current_season_year: int,
    prior_league_key: str,
    prior_season_year: int,
) -> dict[str, dict[str, Any]]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    b.source_team_key,
                    b.current_team_key,
                    t.team_name AS current_team_name,
                    t.owner_name AS current_owner_name
                FROM nffl.team_season_bridge b
                JOIN nffl.team t
                  ON t.league_key = b.current_league_key
                 AND t.season_year = b.current_season_year
                 AND t.team_key = b.current_team_key
                WHERE b.league_code = 'NFFL'
                  AND b.source_league_key = %s
                  AND b.source_season_year = %s
                  AND b.current_league_key = %s
                  AND b.current_season_year = %s
                """,
                (
                    prior_league_key,
                    int(prior_season_year),
                    current_league_key,
                    int(current_season_year),
                ),
            )
            rows = list(cur.fetchall() or [])

    return {str(r["source_team_key"]): dict(r) for r in rows}


def _load_yahoo_auto_lottery_defaults(
    *,
    dsn: str,
    current_league_key: str,
    current_season_year: int,
    current_team_keys: list[str],
) -> dict[str, Any]:
    ctx = _load_active_context(dsn)
    prior_league_key = str(ctx.get("prior_league_key") or "").strip()
    prior_season_year = int(ctx.get("prior_season_year") or 0)

    if not prior_league_key or not prior_season_year:
        raise RuntimeError("Active context is missing prior_league_key/prior_season_year.")

    token = _get_yahoo_access_token()
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{prior_league_key}/standings?format=json"
    payload = _fetch_yahoo_json(token, url)
    standings_rows = _extract_standings_team_rows(payload)

    if len(standings_rows) != 12:
        raise RuntimeError(f"Expected 12 prior-season standings rows, found {len(standings_rows)}.")

    bridge = _load_team_bridge_map(
        dsn=dsn,
        current_league_key=current_league_key,
        current_season_year=current_season_year,
        prior_league_key=prior_league_key,
        prior_season_year=prior_season_year,
    )
    if len(bridge) != 12:
        raise RuntimeError(f"Expected 12 team bridge rows, found {len(bridge)}.")

    champion_prior = standings_rows[0]
    playoff_prior = [r for r in standings_rows if bool(r["clinched_playoffs"])]
    if len(playoff_prior) != 6:
        # Conservative fallback if Yahoo ever omits clinched_playoffs.
        playoff_prior = standings_rows[:6]

    consolation_prior = [r for r in standings_rows if r["source_team_key"] not in {p["source_team_key"] for p in playoff_prior}]

    champion_current = bridge.get(champion_prior["source_team_key"])
    if not champion_current:
        raise RuntimeError(f"Missing bridge for champion {champion_prior['source_team_key']}.")

    champion_team_key = str(champion_current["current_team_key"])
    playoff_team_keys: list[str] = []
    consolation_team_keys: list[str] = []
    proof_rows: list[dict[str, Any]] = []

    for row in standings_rows:
        b = bridge.get(row["source_team_key"])
        if not b:
            raise RuntimeError(f"Missing bridge for prior team {row['source_team_key']}.")

        current_team_key = str(b["current_team_key"])
        if row["source_team_key"] == champion_prior["source_team_key"]:
            pool = "CHAMPION"
        elif row["source_team_key"] in {p["source_team_key"] for p in playoff_prior}:
            pool = "PLAYOFF"
            playoff_team_keys.append(current_team_key)
        else:
            pool = "CONSOLATION"
            consolation_team_keys.append(current_team_key)

        proof_rows.append(
            {
                "Pool": pool,
                "2025 Rank": int(row["rank"]),
                "2025 Team": row["source_team_name"],
                "2025 Key": row["source_team_key"],
                "2026 Team": str(b.get("current_team_name") or current_team_key),
                "2026 Owner": str(b.get("current_owner_name") or ""),
                "2026 Key": current_team_key,
            }
        )

    if champion_team_key not in current_team_keys:
        raise RuntimeError(f"Champion current team key is not loaded in app state: {champion_team_key}")
    if len(playoff_team_keys) != 5:
        raise RuntimeError(f"Expected 5 non-champion playoff teams, found {len(playoff_team_keys)}.")
    if len(consolation_team_keys) != 6:
        raise RuntimeError(f"Expected 6 consolation teams, found {len(consolation_team_keys)}.")
    if len(set([champion_team_key] + playoff_team_keys + consolation_team_keys)) != 12:
        raise RuntimeError("Auto-detected lottery pools did not resolve to 12 unique current teams.")

    return {
        "source": "Yahoo prior-season standings",
        "prior_league_key": prior_league_key,
        "prior_season_year": prior_season_year,
        "champion_team_key": champion_team_key,
        "playoff_team_keys": playoff_team_keys,
        "consolation_team_keys": consolation_team_keys,
        "proof_rows": proof_rows,
        "loaded_at_utc": _utc_now_iso(),
    }


def _load_lottery(dsn: str, league_key: str, season_year: int, draft_key: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT *
                FROM nffl.draft_order_lottery_run
                WHERE league_key = %s
                  AND season_year = %s
                  AND draft_key = %s
                  AND status <> 'VOID'
                ORDER BY created_at_utc DESC
                LIMIT 1
                """,
                (league_key, int(season_year), draft_key),
            )
            run = cur.fetchone()
            if not run:
                return None, []

            cur.execute(
                """
                SELECT
                    p.*,
                    t.team_name,
                    t.owner_name
                FROM nffl.draft_order_lottery_pick p
                LEFT JOIN nffl.team t
                  ON t.league_key = %s
                 AND t.season_year = %s
                 AND t.team_key = p.team_key
                WHERE p.run_id = %s
                ORDER BY p.pick_number DESC
                """,
                (league_key, int(season_year), run["run_id"]),
            )
            picks = list(cur.fetchall() or [])
            return dict(run), [dict(p) for p in picks]


def _create_lottery_run(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    draft_key: str,
    champion_team_key: str,
    playoff_team_keys: list[str],
    consolation_team_keys: list[str],
    all_team_keys: list[str],
    created_by: str,
) -> str:
    champion_team_key = str(champion_team_key or "").strip()
    playoff_team_keys = [str(tk).strip() for tk in playoff_team_keys if str(tk).strip()]
    consolation_team_keys = [str(tk).strip() for tk in consolation_team_keys if str(tk).strip()]
    all_team_keys = [str(tk).strip() for tk in all_team_keys if str(tk).strip()]

    if not champion_team_key:
        raise ValueError("Champion team is required.")
    if len(playoff_team_keys) != 5:
        raise ValueError("Exactly 5 non-champion playoff teams are required.")
    if len(consolation_team_keys) != 6:
        raise ValueError("Exactly 6 consolation teams are required.")

    full_pool = [champion_team_key] + playoff_team_keys + consolation_team_keys
    if len(set(full_pool)) != 12:
        raise ValueError("Champion, playoff, and consolation pools must contain 12 unique teams.")
    if set(full_pool) != set(all_team_keys):
        missing = sorted(set(all_team_keys) - set(full_pool))
        extra = sorted(set(full_pool) - set(all_team_keys))
        raise ValueError(f"Lottery pools must match all league teams. Missing={missing}; extra={extra}")

    seed = secrets.token_hex(32)
    rng = random.Random(seed)

    playoff_order = list(playoff_team_keys)
    consolation_order = list(consolation_team_keys)
    rng.shuffle(playoff_order)
    rng.shuffle(consolation_order)

    assignments: list[dict[str, Any]] = [
        {
            "pick_number": 12,
            "team_key": champion_team_key,
            "pool_type": "CHAMPION",
            "reveal_order": 1,
            "is_revealed": True,
        }
    ]

    for pick_number, team_key in zip(range(11, 6, -1), playoff_order):
        assignments.append(
            {
                "pick_number": pick_number,
                "team_key": team_key,
                "pool_type": "PLAYOFF",
                "reveal_order": 13 - pick_number,
                "is_revealed": False,
            }
        )

    for pick_number, team_key in zip(range(6, 0, -1), consolation_order):
        assignments.append(
            {
                "pick_number": pick_number,
                "team_key": team_key,
                "pool_type": "CONSOLATION",
                "reveal_order": 13 - pick_number,
                "is_revealed": False,
            }
        )

    audit_payload = {
        "league_key": league_key,
        "season_year": int(season_year),
        "draft_key": draft_key,
        "seed": seed,
        "assignments": sorted(
            [
                {
                    "pick_number": int(a["pick_number"]),
                    "team_key": str(a["team_key"]),
                    "pool_type": str(a["pool_type"]),
                    "reveal_order": int(a["reveal_order"]),
                }
                for a in assignments
            ],
            key=lambda r: int(r["pick_number"]),
        ),
    }
    audit_hash = hashlib.sha256(json.dumps(audit_payload, sort_keys=True).encode("utf-8")).hexdigest()
    run_id = f"nffl_{int(season_year)}_lottery_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT count(*) AS n
                FROM nffl.draft_order_lottery_run
                WHERE league_key = %s
                  AND season_year = %s
                  AND draft_key = %s
                  AND status <> 'VOID'
                """,
                (league_key, int(season_year), draft_key),
            )
            existing_n = int((cur.fetchone() or {}).get("n") or 0)
            if existing_n > 0:
                raise RuntimeError("A non-void lottery run already exists. Void it before creating a new one.")

            cur.execute(
                """
                INSERT INTO nffl.draft_order_lottery_run (
                    run_id,
                    league_key,
                    season_year,
                    draft_key,
                    status,
                    champion_team_key,
                    created_by,
                    audit_hash,
                    note
                )
                VALUES (%s, %s, %s, %s, 'ACTIVE', %s, %s, %s, %s)
                """,
                (
                    run_id,
                    league_key,
                    int(season_year),
                    draft_key,
                    champion_team_key,
                    created_by,
                    audit_hash,
                    "Seed stored only in server-side audit payload at creation time; assignments stored before reveal.",
                ),
            )

            for a in assignments:
                cur.execute(
                    """
                    INSERT INTO nffl.draft_order_lottery_pick (
                        run_id,
                        pick_number,
                        team_key,
                        pool_type,
                        reveal_order,
                        is_revealed,
                        revealed_at_utc,
                        revealed_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END, CASE WHEN %s THEN %s ELSE NULL END)
                    """,
                    (
                        run_id,
                        int(a["pick_number"]),
                        str(a["team_key"]),
                        str(a["pool_type"]),
                        int(a["reveal_order"]),
                        bool(a["is_revealed"]),
                        bool(a["is_revealed"]),
                        bool(a["is_revealed"]),
                        created_by,
                    ),
                )

        conn.commit()

    return run_id


def _reveal_pick(*, dsn: str, run_id: str, pick_number: int, actor: str) -> None:
    pick_number = int(pick_number)
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT status
                FROM nffl.draft_order_lottery_run
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            run = cur.fetchone()
            if not run:
                raise RuntimeError(f"Lottery run not found: {run_id}")
            if str(run["status"]) not in ("ACTIVE", "COMPLETE"):
                raise RuntimeError(f"Lottery run cannot be revealed in status {run['status']}.")

            cur.execute(
                """
                SELECT pick_number, is_revealed
                FROM nffl.draft_order_lottery_pick
                WHERE run_id = %s
                ORDER BY pick_number DESC
                FOR UPDATE
                """,
                (run_id,),
            )
            picks = list(cur.fetchall() or [])
            by_pick = {int(p["pick_number"]): bool(p["is_revealed"]) for p in picks}
            if pick_number not in by_pick:
                raise RuntimeError(f"Pick #{pick_number} is not part of this lottery.")
            if by_pick[pick_number]:
                return

            higher_unrevealed = [p for p, revealed in by_pick.items() if p > pick_number and not revealed]
            if higher_unrevealed:
                raise RuntimeError(f"Reveal Pick #{max(higher_unrevealed)} before Pick #{pick_number}.")

            cur.execute(
                """
                UPDATE nffl.draft_order_lottery_pick
                   SET is_revealed = true,
                       revealed_at_utc = now(),
                       revealed_by = %s,
                       updated_at_utc = now()
                 WHERE run_id = %s
                   AND pick_number = %s
                """,
                (actor, run_id, pick_number),
            )

            cur.execute(
                """
                SELECT count(*) AS remaining
                FROM nffl.draft_order_lottery_pick
                WHERE run_id = %s
                  AND is_revealed = false
                """,
                (run_id,),
            )
            remaining = int((cur.fetchone() or {}).get("remaining") or 0)
            if remaining == 0:
                cur.execute(
                    """
                    UPDATE nffl.draft_order_lottery_run
                       SET status = 'COMPLETE',
                           completed_at_utc = COALESCE(completed_at_utc, now()),
                           updated_at_utc = now()
                     WHERE run_id = %s
                    """,
                    (run_id,),
                )

        conn.commit()


def _void_lottery_run(*, dsn: str, run_id: str, actor: str) -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE nffl.draft_order_lottery_run
                   SET status = 'VOID',
                       updated_at_utc = now(),
                       note = concat_ws(' | ', nullif(note, ''), %s)
                 WHERE run_id = %s
                   AND status <> 'APPLIED'
                """,
                (f"Voided by {actor} at {_utc_now_iso()}", run_id),
            )
        conn.commit()


def _apply_lottery_order_to_draft(
    *,
    dsn: str,
    run_id: str,
    draft_key: str,
    actor: str,
) -> dict[int, str]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT status
                FROM nffl.draft_order_lottery_run
                WHERE run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            run = cur.fetchone()
            if not run:
                raise RuntimeError(f"Lottery run not found: {run_id}")
            if str(run["status"]) == "APPLIED":
                raise RuntimeError("Lottery order is already applied.")
            if str(run["status"]) not in ("COMPLETE", "ACTIVE"):
                raise RuntimeError(f"Lottery order cannot be applied from status {run['status']}.")

            cur.execute(
                """
                SELECT count(*) AS n
                FROM nffl.draft_order_lottery_pick
                WHERE run_id = %s
                  AND is_revealed = false
                """,
                (run_id,),
            )
            unrevealed_n = int((cur.fetchone() or {}).get("n") or 0)
            if unrevealed_n > 0:
                raise RuntimeError("All lottery picks must be revealed before applying draft order.")

            cur.execute(
                """
                SELECT count(*) AS n
                FROM nffl.draft_selection
                WHERE draft_key = %s
                """,
                (draft_key,),
            )
            selected_n = int((cur.fetchone() or {}).get("n") or 0)
            if selected_n > 0:
                raise RuntimeError("Refusing to apply draft order after draft selections exist.")

            cur.execute(
                """
                SELECT count(*) AS n
                FROM nffl.draft_pick
                WHERE draft_key = %s
                  AND traded_flag = true
                """,
                (draft_key,),
            )
            traded_n = int((cur.fetchone() or {}).get("n") or 0)
            if traded_n > 0:
                raise RuntimeError("Refusing to apply lottery order while traded pick overrides exist.")

            cur.execute(
                """
                SELECT pick_number, team_key
                FROM nffl.draft_order_lottery_pick
                WHERE run_id = %s
                ORDER BY pick_number
                """,
                (run_id,),
            )
            rows = list(cur.fetchall() or [])
            slot_to_team = {int(r["pick_number"]): str(r["team_key"]) for r in rows}
            if set(slot_to_team.keys()) != set(range(1, 13)):
                raise RuntimeError(f"Lottery order must contain picks 1-12. Found={sorted(slot_to_team.keys())}")

            for slot_number, team_key in slot_to_team.items():
                cur.execute(
                    """
                    UPDATE nffl.draft_pick
                       SET column_team_key = %s,
                           current_owner_team_key = %s,
                           traded_flag = false,
                           ownership_note = NULL,
                           updated_at_utc = now()
                     WHERE draft_key = %s
                       AND slot_number = %s
                    """,
                    (team_key, team_key, draft_key, int(slot_number)),
                )

            cur.execute(
                """
                UPDATE nffl.draft_order_lottery_run
                   SET status = 'APPLIED',
                       applied_at_utc = now(),
                       applied_by = %s,
                       updated_at_utc = now()
                 WHERE run_id = %s
                """,
                (actor, run_id),
            )

        conn.commit()

    return slot_to_team


def _sync_state_order_from_lottery(state: Any, slot_to_team: dict[int, str]) -> None:
    order = [str(slot_to_team[i]) for i in range(1, 13)]
    state.draft_order_team_keys_by_slot = order

    for pick in (getattr(state, "picks", {}) or {}).values():
        try:
            slot = int(getattr(pick, "slot", getattr(pick, "slot_number", 0)) or 0)
        except Exception:
            continue
        team_key = slot_to_team.get(slot)
        if not team_key:
            continue
        if hasattr(pick, "original_team_key"):
            setattr(pick, "original_team_key", team_key)
        if hasattr(pick, "owner_team_key"):
            setattr(pick, "owner_team_key", team_key)
        if hasattr(pick, "column_team_key"):
            setattr(pick, "column_team_key", team_key)
        if hasattr(pick, "current_owner_team_key"):
            setattr(pick, "current_owner_team_key", team_key)

    save_autosave(state)


def _pick_card_html(
    *,
    pick_number: int,
    team_name: str,
    owner_name: str,
    pool_type: str,
    revealed: bool,
    side: str = "",
    run_id: str = "",
    is_commissioner: bool = False,
    can_reveal: bool = False,
) -> str:
    pool_type = str(pool_type or "").upper()
    side_class = "lottery-tile-left" if side == "left" else "lottery-tile-right" if side == "right" else ""

    tile_class = f"lottery-tile {side_class} lottery-tile-locked"
    detail_html = '<div class="lottery-owner-name">Reveal pending</div>'
    team_display = "LOCKED"

    if revealed:
        tile_class = f"lottery-tile {side_class} lottery-tile-revealed"
        team_display = html.escape(team_name or "")
        owner = html.escape(owner_name or "")

        if pool_type == "CHAMPION" and pick_number == 12:
            tile_class = f"lottery-tile {side_class} lottery-tile-champion"
            detail_html = f'<div class="lottery-owner-name">{owner}</div><div class="lottery-banner">2025 Champion</div>'
        elif pool_type == "PLAYOFF":
            detail_html = f'<div class="lottery-owner-name">{owner}</div><div class="lottery-banner lottery-banner-muted">Playoff Pool</div>'
        elif pool_type == "CONSOLATION":
            detail_html = f'<div class="lottery-owner-name">{owner}</div><div class="lottery-banner lottery-banner-muted">Consolation Pool</div>'
        else:
            detail_html = f'<div class="lottery-owner-name">{owner}</div>'
    elif is_commissioner and can_reveal:
        detail_html = (
            '<div class="lottery-owner-name">Next pick ready</div>'
            '<div class="lottery-button-pocket"></div>'
        )
    elif is_commissioner:
        detail_html = (
            '<div class="lottery-owner-name">Awaiting prior reveal</div>'
            f'<div class="lottery-reveal-disabled">Reveal Pick #{int(pick_number)}</div>'
        )

    return (
        f'<div class="{tile_class}">'
        f'<div class="lottery-pick-num"><span>{pick_number}</span></div>'
        f'<div class="lottery-team-panel">'
        f'<div class="lottery-team-name">{team_display}</div>'
        f'{detail_html}'
        f'</div>'
        f'</div>'
    )


def _render_lottery_css() -> None:
    st.markdown(
        """
<style>
.lottery-hero {
    display: flex;
    align-items: center;
    gap: 18px;
    background: linear-gradient(135deg, #0A0A08 0%, #34302B 58%, #5c1717 100%);
    border: 2px solid #D50A0A;
    border-radius: 18px;
    padding: 16px 20px;
    margin: 8px auto 18px auto;
    max-width: 860px;
    box-shadow: 0 0 18px rgba(213, 10, 10, 0.22);
}
.lottery-logo-fallback {
    width: 64px;
    height: 64px;
    border-radius: 12px;
    border: 2px solid #D50A0A;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 900;
    font-size: 1.30rem;
    color: #FF7900;
    background: #34302B;
    letter-spacing: 0.03em;
}
.lottery-title-wrap {
    line-height: 1.0;
}
.lottery-kicker {
    color: #FF7900;
    font-weight: 800;
    font-size: 0.88rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.lottery-title {
    color: #f7f7f7;
    font-weight: 950;
    font-size: clamp(1.85rem, 4.5vw, 3.15rem);
    letter-spacing: -0.04em;
    text-transform: uppercase;
}
.lottery-title-year {
    color: #FF7900;
}
.lottery-tile {
    width: 100%;
    max-width: 480px;
    height: 114px;
    border: 2px solid #D50A0A;
    background: #f2f2ec;
    border-radius: 8px;
    margin-bottom: 11px;
    display: grid;
    grid-template-columns: 76px minmax(0, 1fr);
    align-items: stretch;
    box-shadow: 0 2px 8px rgba(0,0,0,0.24);
    overflow: hidden;
}
.lottery-tile-left {
    margin-left: auto;
    margin-right: 0;
}
.lottery-tile-right {
    margin-left: 0;
    margin-right: auto;
}
.lottery-pick-num {
    background: #34302B;
    color: #FF7900;
    display: flex;
    align-items: center;
    justify-content: center;
    border-right: 2px solid #D50A0A;
}
.lottery-pick-num span {
    font-size: 2.65rem;
    font-style: italic;
    font-weight: 950;
    line-height: 1;
    text-shadow: 0 0 8px rgba(255, 121, 0, 0.28);
}
.lottery-team-panel {
    padding: 11px 15px 10px 15px;
    min-width: 0;
}
.lottery-team-name {
    color: #111111;
    font-size: clamp(1.25rem, 2.05vw, 1.80rem);
    font-weight: 950;
    line-height: 1.05;
    text-transform: uppercase;
    letter-spacing: -0.03em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.lottery-owner-name {
    color: rgba(0,0,0,0.62);
    font-size: 0.95rem;
    margin-top: 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.lottery-banner {
    color: #050505;
    background: #FF7900;
    border-radius: 999px;
    display: inline-block;
    width: fit-content;
    padding: 3px 10px;
    font-size: 0.78rem;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-top: 5px;
}
.lottery-banner-muted {
    color: rgba(0,0,0,0.72);
    background: rgba(213,10,10,0.13);
    border: 1px solid rgba(0,0,0,0.10);
}
.lottery-button-pocket {
    height: 36px;
    margin-top: 6px;
}
div[class*="st-key-lottery_reveal_pick_"] {
    position: relative;
    z-index: 20;
    margin-top: -56px;
    margin-bottom: 24px;
}
div[class*="st-key-lottery_reveal_pick_"] button {
    background: #D50A0A !important;
    color: #F2F0EA !important;
    border: 1px solid rgba(255,255,255,0.20) !important;
    border-radius: 999px !important;
    padding: 5px 12px !important;
    min-height: 30px !important;
    font-size: 0.82rem !important;
    font-weight: 900 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
    white-space: nowrap !important;
}
div[class*="st-key-lottery_reveal_pick_"] button:hover {
    background: #FF7900 !important;
    color: #0A0A08 !important;
}
.lottery-reveal-disabled {
    display: inline-block;
    margin-top: 7px;
    padding: 5px 11px;
    border-radius: 999px;
    color: rgba(255,255,255,0.48);
    background: rgba(255,255,255,0.08);
    font-size: 0.82rem;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border: 1px solid rgba(255,255,255,0.10);
}
.lottery-tile-locked {
    border-color: rgba(213,10,10,0.58);
    background: #34302B;
}
.lottery-tile-locked .lottery-team-panel {
    background: linear-gradient(90deg, #0A0A08 0%, #34302B 100%);
}
.lottery-tile-locked .lottery-team-name {
    color: rgba(255,255,255,0.55);
    letter-spacing: 0.08em;
}
.lottery-tile-locked .lottery-owner-name {
    color: rgba(255,255,255,0.54);
}
.lottery-tile-champion {
    border-color: #FF7900;
    box-shadow: 0 0 14px rgba(255,121,0,0.26);
}
.lottery-tile-champion .lottery-team-panel {
    background: linear-gradient(90deg, #fff0df 0%, #F2F0EA 100%);
}
.lottery-control-spacer {
    height: 38px;
    margin-bottom: 6px;
}
.lottery-hash {
    font-family: monospace;
    font-size: 0.95rem;
    overflow-wrap: anywhere;
    opacity: 0.75;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _render_setup_form(state: Any, dsn: str, league_key: str, season_year: int, draft_key: str) -> None:
    st.markdown("### Initialize Draft Order Lottery")
    st.caption(
        "Yahoo auto-detect is used as the default setup. Commissioner override remains available before initialization."
    )

    team_keys = _ordered_team_keys(state)
    if len(team_keys) != 12:
        st.warning(f"Expected 12 teams, found {len(team_keys)}.")
        return

    cache_key = f"lottery_auto_defaults::{league_key}::{season_year}::{draft_key}"

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Refresh Yahoo Auto-Detect", key="lottery_refresh_yahoo_defaults"):
            st.session_state.pop(cache_key, None)
            st.rerun()
    with c2:
        st.caption("Source: prior-season Yahoo standings + NFFL team bridge.")

    auto: dict[str, Any] | None = st.session_state.get(cache_key)
    if auto is None:
        try:
            with st.spinner("Loading Yahoo prior-season lottery defaults..."):
                auto = _load_yahoo_auto_lottery_defaults(
                    dsn=dsn,
                    current_league_key=league_key,
                    current_season_year=season_year,
                    current_team_keys=team_keys,
                )
            st.session_state[cache_key] = auto
        except Exception as exc:
            # Do not cache errors. A transient Yahoo/auth/code issue should not stick
            # in the user's Streamlit session after a fix or refresh.
            auto = {"error": str(exc)}

    champion_default = team_keys[0]
    playoff_default: list[str] = []
    consolation_default: list[str] = []

    if auto and not auto.get("error"):
        st.success(
            f"Yahoo auto-detected pools from {auto['prior_season_year']} league {auto['prior_league_key']}."
        )
        proof_rows = list(auto.get("proof_rows") or [])
        champion_name = _team_name(state, str(auto.get("champion_team_key") or ""))
        st.caption(f"Auto-detected champion: {champion_name}")

        with st.expander("Review Yahoo source details / prior-season standings", expanded=False):
            st.dataframe(proof_rows, hide_index=True, use_container_width=True)

        champion_default = str(auto.get("champion_team_key") or champion_default)
        playoff_default = [str(tk) for tk in (auto.get("playoff_team_keys") or []) if str(tk) in team_keys]
        consolation_default = [str(tk) for tk in (auto.get("consolation_team_keys") or []) if str(tk) in team_keys]
    else:
        st.warning(
            "Yahoo auto-detect is unavailable. Use the manual override controls below."
        )
        if auto and auto.get("error"):
            st.caption(str(auto.get("error")))

    if champion_default not in team_keys:
        champion_default = team_keys[0]

    with st.form("draft_order_lottery_setup_form", clear_on_submit=False):
        champion_team_key = st.selectbox(
            "League Champion / Pick #12",
            options=team_keys,
            index=team_keys.index(champion_default),
            format_func=lambda tk: _team_name(state, tk),
            key="lottery_champion_team_key",
            help="Default comes from Yahoo final standings rank #1 when available.",
        )

        remaining = [tk for tk in team_keys if tk != champion_team_key]
        playoff_default_effective = [tk for tk in playoff_default if tk in remaining]
        consolation_default_effective = [tk for tk in consolation_default if tk in remaining and tk not in playoff_default_effective]

        playoff_team_keys = st.multiselect(
            "Remaining playoff teams, randomized into Picks #11-#7",
            options=remaining,
            default=playoff_default_effective,
            format_func=lambda tk: _team_name(state, tk),
            key="lottery_playoff_team_keys",
            help="Default comes from Yahoo clinched_playoffs=1, excluding the champion.",
        )

        consolation_options = [tk for tk in remaining if tk not in set(playoff_team_keys)]
        consolation_default_effective = [tk for tk in consolation_default_effective if tk in consolation_options]

        # If Yahoo defaults are valid, this should already contain 6 teams. If the commissioner
        # edits playoff picks, leave consolation editable rather than trying to silently reassign.
        consolation_team_keys = st.multiselect(
            "Consolation teams, randomized into Picks #6-#1",
            options=consolation_options,
            default=consolation_default_effective,
            format_func=lambda tk: _team_name(state, tk),
            key="lottery_consolation_team_keys",
            help="Default is every non-playoff team after Yahoo standings are mapped into the 2026 team keys.",
        )

        st.caption(
            f"Selected: champion=1, playoff={len(playoff_team_keys)}/5, "
            f"consolation={len(consolation_team_keys)}/6."
        )

        confirm = st.checkbox(
            "Confirm these pools are correct and initialize the lottery.",
            key="lottery_initialize_confirm",
        )
        submitted = st.form_submit_button("Initialize Lottery", type="primary")

    if not submitted:
        return

    if not confirm:
        st.warning("Confirm the lottery pools before initializing.")
        return

    try:
        run_id = _create_lottery_run(
            dsn=dsn,
            league_key=league_key,
            season_year=season_year,
            draft_key=draft_key,
            champion_team_key=champion_team_key,
            playoff_team_keys=playoff_team_keys,
            consolation_team_keys=consolation_team_keys,
            all_team_keys=team_keys,
            created_by=_actor_label(),
        )
        st.success(f"Lottery initialized: {run_id}")
        st.rerun()
    except Exception as exc:
        st.error(f"Lottery initialization failed: {exc}")


def _render_lottery_board(state: Any, dsn: str, run: dict[str, Any], picks: list[dict[str, Any]], draft_key: str) -> None:
    _render_lottery_css()

    is_commissioner = bool(getattr(state, "commissioner_mode", False))
    run_id = str(run["run_id"])
    status = str(run["status"])
    audit_hash = str(run.get("audit_hash") or "")

    st.markdown(
        '<div class="lottery-hero">'
        '<div class="lottery-logo-fallback">NFFL</div>'
        '<div class="lottery-title-wrap">'
        '<div class="lottery-kicker">Official Draft Order Reveal</div>'
        '<div class="lottery-title">Draft Lottery <span class="lottery-title-year">2026</span></div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    if st.button(
        "Refresh Lottery Results",
        key=f"lottery_refresh_results_{run_id}",
        help="Reload the latest revealed picks without leaving the Draft Lottery tab.",
        use_container_width=False,
    ):
        st.rerun()

    pick_by_number = {int(p["pick_number"]): p for p in picks}
    revealed_by_pick = {int(p["pick_number"]): bool(p["is_revealed"]) for p in picks}
    next_reveal_pick = None
    for pick_number in range(11, 0, -1):
        if not revealed_by_pick.get(pick_number, False):
            next_reveal_pick = pick_number
            break

    left_numbers = [1, 2, 3, 4, 5, 6]
    right_numbers = [7, 8, 9, 10, 11, 12]

    _, left_col, _, right_col, _ = st.columns([0.9, 1.35, 0.04, 1.35, 0.9], gap="small")

    def render_pick_slot(pick_number: int, side: str) -> None:
        p = pick_by_number.get(pick_number)
        if not p:
            st.warning(f"Pick #{pick_number} is missing from lottery run {run_id}.")
            return

        revealed = bool(p.get("is_revealed"))
        team_name = str(p.get("team_name") or _team_name(state, str(p.get("team_key") or "")))
        owner_name = str(p.get("owner_name") or "")
        pool_type = str(p.get("pool_type") or "")

        st.markdown(
            _pick_card_html(
                pick_number=pick_number,
                team_name=team_name,
                owner_name=owner_name,
                pool_type=pool_type,
                revealed=revealed,
                side=side,
                run_id=run_id,
                is_commissioner=is_commissioner,
                can_reveal=(pick_number == next_reveal_pick and status != LOTTERY_STATUS_APPLIED),
            ),
            unsafe_allow_html=True,
        )

        if is_commissioner:
            if not revealed:
                disabled = pick_number != next_reveal_pick or status == LOTTERY_STATUS_APPLIED
                _, reveal_button_col = st.columns([0.64, 0.36], gap="small")
                with reveal_button_col:
                    if st.button(
                        f"Reveal Pick #{pick_number}",
                        key=f"lottery_reveal_pick_{pick_number}",
                        disabled=disabled,
                        use_container_width=True,
                    ):
                        try:
                            _reveal_pick(
                                dsn=dsn,
                                run_id=run_id,
                                pick_number=pick_number,
                                actor=_actor_label(),
                            )
                            st.success(f"Pick #{pick_number} revealed.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Reveal failed: {exc}")
            else:
                st.markdown('<div class="lottery-control-spacer"></div>', unsafe_allow_html=True)

    with left_col:
        for n in left_numbers:
            render_pick_slot(n, "left")

    with right_col:
        for n in right_numbers:
            render_pick_slot(n, "right")

    with st.expander("Lottery technical details", expanded=False):
        st.caption(f"Status: {status}")
        if audit_hash:
            st.markdown(f'<div class="lottery-hash">Audit hash: {html.escape(audit_hash)}</div>', unsafe_allow_html=True)
        st.caption("Reveal path: #12 is already shown → #11 up to #7 → #6 up to #1.")

    all_revealed = bool(picks) and all(bool(p.get("is_revealed")) for p in picks)

    if is_commissioner:
        st.divider()
        st.markdown("### Commissioner Actions")

        if all_revealed and status != LOTTERY_STATUS_APPLIED:
            if st.button("Apply Draft Order", type="primary", key=f"lottery_apply_{run_id}", use_container_width=True):
                try:
                    slot_to_team = _apply_lottery_order_to_draft(
                        dsn=dsn,
                        run_id=run_id,
                        draft_key=draft_key,
                        actor=_actor_label(),
                    )
                    _sync_state_order_from_lottery(state, slot_to_team)
                    st.success("Draft order applied to the draft board.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Apply draft order failed: {exc}")

        if status != LOTTERY_STATUS_APPLIED:
            with st.form(f"lottery_void_form_{run_id}", clear_on_submit=False):
                confirm_void = st.checkbox(
                    "Void this lottery run so a new one can be initialized.",
                    key=f"lottery_void_confirm_{run_id}",
                )
                void_clicked = st.form_submit_button("Void Lottery Run")
            if void_clicked:
                if not confirm_void:
                    st.warning("Confirm before voiding this lottery run.")
                else:
                    try:
                        _void_lottery_run(dsn=dsn, run_id=run_id, actor=_actor_label())
                        st.success("Lottery run voided.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Void failed: {exc}")


def render_draft_lottery_tab(state) -> None:
    st.subheader("Draft Lottery")

    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()
    draft_key = get_draft_key()

    if not dsn:
        st.warning("Postgres DSN is not available; lottery cannot load.")
        return

    try:
        run, picks = _load_lottery(dsn, league_key, season_year, draft_key)
    except Exception as exc:
        st.error(f"Could not load draft lottery state: {exc}")
        return

    if run:
        _render_lottery_board(state, dsn, run, picks, draft_key)
        return

    st.info("No active draft order lottery has been initialized yet.")
    if bool(getattr(state, "commissioner_mode", False)):
        _render_setup_form(state, dsn, league_key, season_year, draft_key)
    else:
        st.caption("The commissioner has not started the draft order lottery.")
