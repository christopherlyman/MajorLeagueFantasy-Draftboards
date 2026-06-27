from __future__ import annotations
from draftboard.state.league_profile import get_active_qo_rounds
import os
import re
import sys
import subprocess
import time
import json
import uuid
from datetime import datetime, timezone

import psycopg
import streamlit as st

from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)

from draftboard.domain.clock import compute_clock_status, start_pick_clock
from draftboard.state.autosave import save_autosave
from draftboard.state.store import DraftState, set_current_pick




def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _get_dsn() -> str:
    return get_postgres_dsn()


def _get_draft_key() -> str:
    return get_draft_key()


def _get_league_key() -> str:
    return get_league_key()


def _get_season_year() -> int:
    return get_season_year()

def _generate_temp_password(length: int = 12) -> str:
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(int(length)))


def _load_resettable_manager_accounts(dsn: str, league_key: str, season_year: int) -> list[dict]:
    sql = """
        SELECT
            u.user_id,
            u.email_normalized,
            u.active,
            u.must_change_password,
            u.is_site_admin,
            r.franchise_id,
            r.role_code,
            fst.team_key,
            fst.team_name
        FROM public.auth_user u
        JOIN public.auth_user_league_role r
          ON r.user_id = u.user_id
         AND r.league_key = %s
         AND r.active = true
        LEFT JOIN public.franchise_season_team fst
          ON fst.franchise_id = r.franchise_id
         AND fst.league_key = r.league_key
         AND fst.season_year = %s
        WHERE u.active = true
        ORDER BY
            COALESCE(fst.team_name, ''),
            u.email_normalized;
    """
    out: list[dict] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(league_key), int(season_year)))
            for row in cur.fetchall():
                out.append(
                    {
                        "user_id": int(row[0]),
                        "email_normalized": str(row[1]),
                        "active": bool(row[2]),
                        "must_change_password": bool(row[3]),
                        "is_site_admin": bool(row[4]),
                        "franchise_id": int(row[5]) if row[5] is not None else None,
                        "role_code": str(row[6]) if row[6] is not None else None,
                        "team_key": str(row[7]) if row[7] is not None else "",
                        "team_name": str(row[8]) if row[8] is not None else "",
                    }
                )
    return out


def _admin_reset_local_user_password(*, dsn: str, user_id: int, temp_password: str) -> bool:
    import bcrypt

    pw = str(temp_password or "")
    if len(pw) < 10:
        raise ValueError("Temporary password must be at least 10 characters.")

    password_hash = bcrypt.hashpw(
        pw.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    sql = """
        UPDATE public.auth_user
        SET password_hash = %s,
            must_change_password = true
        WHERE user_id = %s
          AND active = true
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (password_hash, int(user_id)))
            updated = int(cur.rowcount or 0)
        conn.commit()

    return updated == 1

def _insert_trade(
    dsn: str,
    league_key: str,
    season_year: int,
    created_by: str | None = None,
    notes: str | None = None,
) -> str:
    """
    Inserts one trade row.
    trade.trade_id is UUID with NO default, so we generate it here.
    Returns trade_id (UUID string).
    """
    trade_id = str(uuid.uuid4())

    sql = """
      INSERT INTO public.trade
        (trade_id, league_key, season_year, created_at, created_by, notes)
      VALUES
        (%s, %s, %s, now(), %s, %s);
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    trade_id,
                    str(league_key),
                    int(season_year),
                    (str(created_by) if created_by else None),
                    (str(notes) if notes else None),
                ),
            )
        conn.commit()

    return trade_id

def _update_contract_team_key(
    dsn: str,
    league_key: str,
    season_year: int,
    yahoo_player_key: str,
    to_team_key: str,
    note: str = "",
) -> int:
    sql = """
      UPDATE public.contract
         SET team_key=%s,
             note=%s,
             updated_at=now()
       WHERE league_key=%s
         AND season_year=%s
         AND yahoo_player_key=%s
         AND years_remaining > 0;
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(to_team_key or ""),
                    str(note or ""),
                    str(league_key),
                    int(season_year),
                    str(yahoo_player_key),
                ),
            )
            n = cur.rowcount or 0
        conn.commit()
    return int(n)


def _void_contract_ssot(
    dsn: str,
    league_key: str,
    season_year: int,
    yahoo_player_key: str,
    note: str = "voided",
) -> int:
    sql = """
      UPDATE public.contract
         SET years_remaining=0,
             note=%s,
             updated_at=now()
       WHERE league_key=%s
         AND season_year=%s
         AND yahoo_player_key=%s
         AND years_remaining > 0;
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(note or ""),
                    str(league_key),
                    int(season_year),
                    str(yahoo_player_key),
                ),
            )
            n = cur.rowcount or 0
        conn.commit()
    return int(n)


def _upsert_contract_ssot(
    dsn: str,
    league_key: str,
    season_year: int,
    yahoo_player_key: str,
    years_remaining: int,
    team_key: str,
    note: str = "",
) -> int:
    sql = """
      INSERT INTO public.contract
        (league_key, season_year, team_key, yahoo_player_key, years_remaining, note, updated_at)
      VALUES
        (%s, %s, %s, %s, %s, %s, now())
      ON CONFLICT (league_key, season_year, yahoo_player_key)
      DO UPDATE SET
        team_key=EXCLUDED.team_key,
        years_remaining=EXCLUDED.years_remaining,
        note=EXCLUDED.note,
        updated_at=now();
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(league_key),
                    int(season_year),
                    str(team_key or ""),
                    str(yahoo_player_key),
                    int(years_remaining),
                    str(note or ""),
                ),
            )
            n = cur.rowcount or 0
        conn.commit()
    return int(n)

def _insert_trade_assets(
    dsn: str,
    trade_id: str,
    rows: list[dict],
) -> int:
    """
    Inserts trade_asset rows matching your schema.
    - trade_asset_id UUID has NO default -> generated here
    - snapshot_json is NOT NULL -> pass '{}' if missing
    """
    sql = """
      INSERT INTO public.trade_asset
        (trade_asset_id, trade_id, asset_type, asset_id, from_team_key, to_team_key, snapshot_json, created_at)
      VALUES
        (%s, %s, %s, %s, %s, %s, %s::jsonb, now());
    """

    n = 0
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for r in rows:
                trade_asset_id = str(uuid.uuid4())
                snapshot = r.get("snapshot") or {}
                cur.execute(
                    sql,
                    (
                        trade_asset_id,
                        str(trade_id),
                        str(r.get("asset_type") or ""),
                        str(r.get("asset_id") or ""),
                        str(r.get("from_team_key") or ""),
                        str(r.get("to_team_key") or ""),
                        json.dumps(snapshot),
                    ),
                )
                n += 1
        conn.commit()

    return int(n)

def _load_pt_map(dsn: str, league_key: str, season_year: int) -> dict[str, str]:
    sql = """
    SELECT yahoo_player_key, team_key
    FROM public.prospect_tag
    WHERE league_key=%s AND season_year=%s;
    """
    out: dict[str, str] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            for pk, tk in cur.fetchall():
                if pk and tk:
                    out[str(pk)] = str(tk)
    return out

def _load_contract_overrides(dsn: str, league_key: str, season_year: int) -> list[dict]:
    sql = """
      SELECT yahoo_player_key, years_remaining, yahoo_team_key, yahoo_team_name, note, updated_at
      FROM public.contract_override
      WHERE league_key=%s AND season_year=%s
      ORDER BY yahoo_team_name NULLS LAST, yahoo_player_key;
    """
    out: list[dict] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            for pk, yrs, tkey, tname, note, updated_at in cur.fetchall():
                out.append(
                    {
                        "yahoo_player_key": str(pk),
                        "years_remaining": int(yrs) if yrs is not None else 0,
                        "yahoo_team_key": str(tkey or ""),
                        "yahoo_team_name": str(tname or ""),
                        "note": str(note or ""),
                        "updated_at": str(updated_at or ""),
                    }
                )
    return out


def _upsert_contract_override(
    dsn: str,
    league_key: str,
    season_year: int,
    yahoo_player_key: str,
    years_remaining: int,
    yahoo_team_key: str,
    yahoo_team_name: str,
    note: str = "",
) -> None:
    sql = """
      INSERT INTO public.contract_override
        (league_key, season_year, yahoo_player_key, years_remaining, yahoo_team_key, yahoo_team_name, note, updated_at)
      VALUES
        (%s, %s, %s, %s, %s, %s, %s, now())
      ON CONFLICT (league_key, season_year, yahoo_player_key)
      DO UPDATE SET
        years_remaining=EXCLUDED.years_remaining,
        yahoo_team_key=EXCLUDED.yahoo_team_key,
        yahoo_team_name=EXCLUDED.yahoo_team_name,
        note=EXCLUDED.note,
        updated_at=now();
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    league_key,
                    season_year,
                    str(yahoo_player_key),
                    int(years_remaining),
                    str(yahoo_team_key or ""),
                    str(yahoo_team_name or ""),
                    str(note or ""),
                ),
            )
        conn.commit()


def _delete_contract_override(dsn: str, league_key: str, season_year: int, yahoo_player_key: str) -> int:
    sql = """
      DELETE FROM public.contract_override
      WHERE league_key=%s AND season_year=%s AND yahoo_player_key=%s;
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year, str(yahoo_player_key)))
            n = cur.rowcount or 0
        conn.commit()
    return int(n)



# NFFL_KEEPER_ROSTER_OVERRIDE_HELPERS_START

def _nffl_count_rows(cur, sql: str, params: tuple) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def _nffl_set_active_contract(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    team_key: str,
    yahoo_player_key: str,
    years_remaining: int,
    note: str = "",
) -> str:
    """
    NFFL canonical contract override.
    Writes nffl.contract, not legacy public.contract.
    """
    league_key = str(league_key)
    season_year = int(season_year)
    team_key = str(team_key or "").strip()
    yahoo_player_key = str(yahoo_player_key or "").strip()
    years_remaining = int(years_remaining)
    note = str(note or "").strip()

    if not team_key:
        raise ValueError("team_key is required.")
    if not yahoo_player_key:
        raise ValueError("yahoo_player_key is required.")
    if years_remaining < 1:
        raise ValueError("years_remaining must be >= 1.")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            n = _nffl_count_rows(
                cur,
                """
                SELECT count(*)
                FROM nffl.contract
                WHERE league_key=%s
                  AND season_year=%s
                  AND yahoo_player_key=%s
                """,
                (league_key, season_year, yahoo_player_key),
            )
            if n > 1:
                raise RuntimeError(
                    f"Refusing contract override: duplicate nffl.contract rows for {yahoo_player_key}."
                )

            if n == 0:
                cur.execute(
                    """
                    INSERT INTO nffl.contract (
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        contract_years_remaining,
                        contract_source,
                        source_snapshot_id,
                        status,
                        note,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        'commissioner_override',
                        NULL,
                        'active',
                        %s,
                        now(),
                        now()
                    )
                    """,
                    (
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        years_remaining,
                        note,
                    ),
                )
                action = "inserted"
            else:
                cur.execute(
                    """
                    UPDATE nffl.contract
                       SET team_key=%s,
                           contract_years_remaining=%s,
                           contract_source='commissioner_override',
                           status='active',
                           note=%s,
                           updated_at_utc=now()
                     WHERE league_key=%s
                       AND season_year=%s
                       AND yahoo_player_key=%s
                    """,
                    (
                        team_key,
                        years_remaining,
                        note,
                        league_key,
                        season_year,
                        yahoo_player_key,
                    ),
                )
                action = "updated"

        conn.commit()

    return action


def _nffl_void_contract(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    yahoo_player_key: str,
    note: str = "voided by commissioner",
) -> int:
    """
    Void one NFFL contract row by making it non-active.
    Available Players and board contract placeholders rely on status='active'.
    """
    league_key = str(league_key)
    season_year = int(season_year)
    yahoo_player_key = str(yahoo_player_key or "").strip()
    note = str(note or "").strip() or "voided by commissioner"

    if not yahoo_player_key:
        raise ValueError("yahoo_player_key is required.")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            n = _nffl_count_rows(
                cur,
                """
                SELECT count(*)
                FROM nffl.contract
                WHERE league_key=%s
                  AND season_year=%s
                  AND yahoo_player_key=%s
                """,
                (league_key, season_year, yahoo_player_key),
            )
            if n > 1:
                raise RuntimeError(
                    f"Refusing contract void: duplicate nffl.contract rows for {yahoo_player_key}."
                )
            if n == 0:
                return 0

            cur.execute(
                """
                UPDATE nffl.contract
                   SET contract_years_remaining=0,
                       status='voided',
                       note=%s,
                       updated_at_utc=now()
                 WHERE league_key=%s
                   AND season_year=%s
                   AND yahoo_player_key=%s
                """,
                (note, league_key, season_year, yahoo_player_key),
            )
            rows = int(cur.rowcount or 0)

        conn.commit()

    return rows


def _nffl_set_franchise_tag(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    team_key: str,
    yahoo_player_key: str,
    note: str = "",
) -> str:
    """
    Set one NFFL Franchise Tag for a team.

    Writes:
    - nffl.offseason_keeper_decision decision_type='FT'
    - nffl.franchise_tag_history tag_status='applied'

    The current visible FT placeholder source is offseason_keeper_decision.
    The history table preserves FT usage history.
    """
    import json

    league_key = str(league_key)
    season_year = int(season_year)
    team_key = str(team_key or "").strip()
    yahoo_player_key = str(yahoo_player_key or "").strip()
    note = str(note or "").strip()

    if not team_key:
        raise ValueError("team_key is required.")
    if not yahoo_player_key:
        raise ValueError("yahoo_player_key is required.")

    payload = {
        "decision_type": "FT",
        "team_key": team_key,
        "yahoo_player_key": yahoo_player_key,
        "source": "commissioner_override",
        "action": "set",
    }

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Enforce one current FT decision per team/year.
            cur.execute(
                """
                DELETE FROM nffl.offseason_keeper_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                  AND decision_type='FT'
                """,
                (league_key, season_year, team_key),
            )

            cur.execute(
                """
                INSERT INTO nffl.offseason_keeper_decision (
                    league_key,
                    season_year,
                    team_key,
                    yahoo_player_key,
                    decision_type,
                    decision_status,
                    revision_number,
                    decided_by,
                    decided_at_utc,
                    note,
                    created_at_utc,
                    updated_at_utc
                )
                VALUES (
                    %s, %s, %s, %s,
                    'FT',
                    'DRAFT',
                    1,
                    'commissioner',
                    now(),
                    %s,
                    now(),
                    now()
                )
                """,
                (league_key, season_year, team_key, yahoo_player_key, note),
            )

            # Keep only one applied FT history row per team/year as current applied history.
            cur.execute(
                """
                DELETE FROM nffl.franchise_tag_history
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                  AND tag_status='applied'
                """,
                (league_key, season_year, team_key),
            )

            cur.execute(
                """
                INSERT INTO nffl.franchise_tag_history (
                    league_key,
                    season_year,
                    team_key,
                    yahoo_player_key,
                    tag_status,
                    note,
                    created_at_utc,
                    updated_at_utc
                )
                VALUES (
                    %s, %s, %s, %s,
                    'applied',
                    %s,
                    now(),
                    now()
                )
                ON CONFLICT (league_key, season_year, team_key, yahoo_player_key)
                DO UPDATE SET
                    tag_status='applied',
                    note=EXCLUDED.note,
                    updated_at_utc=now()
                """,
                (league_key, season_year, team_key, yahoo_player_key, note),
            )

            cur.execute(
                """
                INSERT INTO nffl.offseason_keeper_decision_audit (
                    league_key,
                    season_year,
                    team_key,
                    action_type,
                    revision_number,
                    action_by,
                    action_at_utc,
                    decision_payload,
                    note
                )
                VALUES (
                    %s, %s, %s,
                    'SAVE_DRAFT',
                    1,
                    'commissioner',
                    now(),
                    %s::jsonb,
                    %s
                )
                """,
                (league_key, season_year, team_key, json.dumps(payload), note),
            )

        conn.commit()

    return "set"


def _nffl_clear_franchise_tag(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    team_key: str,
    note: str = "cleared by commissioner",
) -> int:
    """
    Clear current FT decision for one team/year and record cleared history.
    """
    import json

    league_key = str(league_key)
    season_year = int(season_year)
    team_key = str(team_key or "").strip()
    note = str(note or "").strip() or "cleared by commissioner"

    if not team_key:
        raise ValueError("team_key is required.")

    payload = {
        "decision_type": "FT",
        "team_key": team_key,
        "source": "commissioner_override",
        "action": "clear",
    }

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM nffl.offseason_keeper_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                  AND decision_type='FT'
                """,
                (league_key, season_year, team_key),
            )
            decision_rows = int(cur.rowcount or 0)

            cur.execute(
                """
                UPDATE nffl.franchise_tag_history
                   SET tag_status='void',
                       note=%s,
                       updated_at_utc=now()
                 WHERE league_key=%s
                   AND season_year=%s
                   AND team_key=%s
                   AND tag_status='applied'
                """,
                (note, league_key, season_year, team_key),
            )
            history_rows = int(cur.rowcount or 0)

            if decision_rows + history_rows > 0:
                cur.execute(
                    """
                    INSERT INTO nffl.offseason_keeper_decision_audit (
                        league_key,
                        season_year,
                        team_key,
                        action_type,
                        revision_number,
                        action_by,
                        action_at_utc,
                        decision_payload,
                        note
                    )
                    VALUES (
                        %s, %s, %s,
                        'SAVE_DRAFT',
                        1,
                        'commissioner',
                        now(),
                        %s::jsonb,
                        %s
                    )
                    """,
                    (league_key, season_year, team_key, json.dumps(payload), note),
                )

        conn.commit()

    return decision_rows + history_rows


# NFFL_KEEPER_ROSTER_OVERRIDE_HELPERS_END


# NFFL_PICK_OWNERSHIP_OVERRIDE_HELPERS_START

def _nffl_load_pick_ownership_rows(
    *,
    dsn: str,
    draft_key: str,
    league_key: str,
    season_year: int,
) -> list[dict]:
    """
    Load pick ownership directly from nffl.draft_pick.
    This is the DB source of truth for pick ownership.
    """
    sql = """
        SELECT
            dp.pick_id,
            dp.round_number,
            dp.slot_number,
            dp.round_label,
            dp.pick_type,
            dp.column_team_key,
            COALESCE(col_team.team_name, dp.column_team_key) AS column_team_name,
            dp.current_owner_team_key,
            COALESCE(owner_team.team_name, dp.current_owner_team_key) AS current_owner_team_name,
            dp.traded_flag,
            COALESCE(dp.ownership_note, '') AS ownership_note,
            CASE WHEN ds.pick_id IS NULL THEN false ELSE true END AS is_selected
        FROM nffl.draft_pick dp
        LEFT JOIN nffl.team col_team
          ON col_team.league_key=%s
         AND col_team.season_year=%s
         AND col_team.team_key=dp.column_team_key
        LEFT JOIN nffl.team owner_team
          ON owner_team.league_key=%s
         AND owner_team.season_year=%s
         AND owner_team.team_key=dp.current_owner_team_key
        LEFT JOIN nffl.draft_selection ds
          ON ds.draft_key=dp.draft_key
         AND ds.pick_id=dp.pick_id
        WHERE dp.draft_key=%s
        ORDER BY dp.round_number, dp.slot_number
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql,
                (
                    str(league_key),
                    int(season_year),
                    str(league_key),
                    int(season_year),
                    str(draft_key),
                ),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def _nffl_update_pick_owner(
    *,
    dsn: str,
    draft_key: str,
    league_key: str,
    season_year: int,
    pick_id: str,
    new_owner_team_key: str,
    note: str = "",
) -> dict:
    """
    Commissioner override for pick ownership.

    Writes only nffl.draft_pick:
    - current_owner_team_key
    - traded_flag
    - ownership_note
    - updated_at_utc

    Refuses to alter ownership after a pick has already been selected,
    because nffl.draft_selection.selecting_team_key would then become inconsistent.
    """
    draft_key = str(draft_key or "").strip()
    league_key = str(league_key or "").strip()
    season_year = int(season_year)
    pick_id = str(pick_id or "").strip()
    new_owner_team_key = str(new_owner_team_key or "").strip()
    note = str(note or "").strip()

    if not draft_key:
        raise ValueError("draft_key is required.")
    if not league_key:
        raise ValueError("league_key is required.")
    if not pick_id:
        raise ValueError("pick_id is required.")
    if not new_owner_team_key:
        raise ValueError("new_owner_team_key is required.")

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT count(*) AS n
                FROM nffl.team
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                """,
                (league_key, season_year, new_owner_team_key),
            )
            team_n = int((cur.fetchone() or {}).get("n") or 0)
            if team_n != 1:
                raise RuntimeError(f"New owner team_key is not valid for this NFFL season: {new_owner_team_key}")

            cur.execute(
                """
                SELECT
                    draft_key,
                    pick_id,
                    column_team_key,
                    current_owner_team_key,
                    traded_flag,
                    COALESCE(ownership_note, '') AS ownership_note
                FROM nffl.draft_pick
                WHERE draft_key=%s
                  AND pick_id=%s
                FOR UPDATE
                """,
                (draft_key, pick_id),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"Pick not found: {draft_key} / {pick_id}")

            cur.execute(
                """
                SELECT count(*) AS n
                FROM nffl.draft_selection
                WHERE draft_key=%s
                  AND pick_id=%s
                """,
                (draft_key, pick_id),
            )
            selected_n = int((cur.fetchone() or {}).get("n") or 0)
            if selected_n > 0:
                raise RuntimeError(
                    f"Refusing to change ownership for selected pick {pick_id}. "
                    "Delete/correct the draft selection first."
                )

            column_team_key = str(row["column_team_key"])
            old_owner_team_key = str(row["current_owner_team_key"])
            traded_flag = bool(new_owner_team_key != column_team_key)

            final_note = note
            if not final_note and traded_flag:
                final_note = f"Commissioner ownership override: {old_owner_team_key} -> {new_owner_team_key}"
            if not final_note and not traded_flag:
                final_note = ""

            cur.execute(
                """
                UPDATE nffl.draft_pick
                   SET current_owner_team_key=%s,
                       traded_flag=%s,
                       ownership_note=NULLIF(%s, ''),
                       updated_at_utc=now()
                 WHERE draft_key=%s
                   AND pick_id=%s
                """,
                (
                    new_owner_team_key,
                    traded_flag,
                    final_note,
                    draft_key,
                    pick_id,
                ),
            )

        conn.commit()

    return {
        "draft_key": draft_key,
        "pick_id": pick_id,
        "column_team_key": column_team_key,
        "old_owner_team_key": old_owner_team_key,
        "new_owner_team_key": new_owner_team_key,
        "traded_flag": traded_flag,
        "ownership_note": final_note,
    }


# NFFL_PICK_OWNERSHIP_OVERRIDE_HELPERS_END



def _refresh_contract_cache_into_session_state() -> None:
    """
    Refreshes:
      - st.session_state["contracted_keys"]
      - st.session_state["contract_rows"]
    from canonical DB truth.
    """
    from draftboard.data.db_players import load_contracted_player_keys, load_contracts_current

    try:
        dsn = get_postgres_dsn()
        league_key = get_league_key()
        season_year = get_season_year()
    except RuntimeError:
        return

    contracted_keys = load_contracted_player_keys(dsn)
    st.session_state["contracted_keys"] = set(contracted_keys or set())

    contract_rows = load_contracts_current(dsn, league_key, season_year)
    st.session_state["contract_rows"] = list(contract_rows or [])

def _upsert_pt_player(
    dsn: str,
    league_key: str,
    season_year: int,
    team_key: str,
    yahoo_player_key: str,
) -> None:
    sql = """
    INSERT INTO public.prospect_tag (league_key, season_year, team_key, yahoo_player_key, updated_at)
    VALUES (%s, %s, %s, %s, now())
    ON CONFLICT (league_key, season_year, yahoo_player_key)
    DO UPDATE SET team_key=EXCLUDED.team_key, updated_at=now();
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year, team_key, yahoo_player_key))
        conn.commit()

def _delete_pt_player(dsn: str, league_key: str, season_year: int, yahoo_player_key: str) -> None:
    sql = """
    DELETE FROM public.prospect_tag
    WHERE league_key=%s AND season_year=%s AND yahoo_player_key=%s;
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year, yahoo_player_key))
        conn.commit()

def _delete_pt_for_team(dsn: str, league_key: str, season_year: int, team_key: str) -> int:
    """
    Deletes the current PT assignment for a team (max 1 per team per season).
    Returns rows deleted (0 or 1).
    """
    sql = """
    DELETE FROM public.prospect_tag
    WHERE league_key=%s AND season_year=%s AND team_key=%s;
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year, team_key))
            n = cur.rowcount or 0
        conn.commit()
    return int(n)


def _upsert_team_predraft_qos(
    dsn: str,
    league_key: str,
    season_year: int,
    team_key: str,
    rows: list[tuple[str, int, str]],
    created_by: str | None = None,
) -> int:
    """
    rows: [(yahoo_player_key, qo_level, note), ...] qo_level=1..5
    Strategy: wipe existing for team, then insert provided rows.
    """
    if not dsn:
        return 0

    try:
        import psycopg
    except Exception:
        return 0

    sql_delete = """
        DELETE FROM public.qualifying_offer
        WHERE league_key=%s AND season_year=%s AND team_key=%s;
    """

    sql_insert = """
        INSERT INTO public.qualifying_offer
          (league_key, season_year, team_key, yahoo_player_key, qo_level, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, now());
    """

    ok = 0
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # wipe first
                cur.execute(sql_delete, (league_key, season_year, team_key))

                # insert rows (validated)
                for yahoo_player_key, qo_level, _note in rows:
                    pk = str(yahoo_player_key).strip() if yahoo_player_key is not None else ""
                    lvl = int(qo_level) if qo_level is not None else 0

                    if not pk:
                        continue
                    if lvl < 1 or lvl > 5:
                        continue

                    cur.execute(sql_insert, (league_key, season_year, team_key, pk, lvl))
                    ok += 1

            conn.commit()
        return ok
    except Exception:
        return 0


def _parse_qo_lines(raw: str, state: DraftState) -> tuple[list[tuple[str, int, str]], list[str]]:
    """
    Returns:
      rows: [(yahoo_player_key, qo_group, notes), ...]
      errors: [msg, ...]
    Accepts lines like:
      469.p.12345, QO2
      469.p.12345, 2
      Michael King, QO1
      QO1: Michael King SP
    Name matching is exact (case-insensitive) against state.players[*].name.
    If multiple matches -> reject.
    """
    rows: list[tuple[str, int, str]] = []
    errors: list[str] = []
    if not raw.strip():
        return rows, errors

    # build name index (case-insensitive)
    name_to_keys: dict[str, list[str]] = {}
    for pk, p in state.players.items():
        name_to_keys.setdefault(p.name.strip().lower(), []).append(pk)

    for ln, line in enumerate(raw.splitlines(), start=1):
        s = line.strip()
        if not s:
            continue

        # normalize separators
        s = s.replace("\t", ",")
        s = re.sub(r"\s+", " ", s)

        qo_group: int | None = None
        notes = ""

        # Try extract QO group from patterns like "QO1:" or "QO2"
        qo_rounds_count = get_active_qo_rounds()
        m = re.search(r"\bQO\s*(\d+)\b", s, flags=re.IGNORECASE)
        if m:
            qo_group = int(m.group(1))
            s = re.sub(r"\bQO\s*\d+\b", "", s, flags=re.IGNORECASE).strip()
            s = s.lstrip(":").strip()

        # If comma form, allow "thing, 2" or "thing, QO2"
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if qo_group is None and len(parts) >= 2:
            # group is in last token
            last = parts[-1]
            m2 = re.search(r"\b(\d+)\b", last)
            if m2:
                qo_group = int(m2.group(1))
                parts = parts[:-1]

        if qo_group is None:
            errors.append(f"Line {ln}: missing QO group (need QO1..QO{qo_rounds_count})")
            continue

        if qo_group < 1 or qo_group > qo_rounds_count:
            errors.append(f"Line {ln}: QO group must be between QO1 and QO{qo_rounds_count}")
            continue

        # remaining "thing" could be yahoo key or player name (+ optional trailing position)
        thing = " ".join(parts).strip() if parts else ""
        thing = re.sub(
            r"\b(SP|RP|P|C|1B|2B|3B|SS|OF|UTIL)\b$",
            "",
            thing,
            flags=re.IGNORECASE,
        ).strip()

        if not thing:
            errors.append(f"Line {ln}: missing player key or name")
            continue

        yahoo_key = None
        if re.match(r"^\d+\.[a-z]\.\d+$", thing):  # e.g., 469.p.12345
            yahoo_key = thing
        else:
            # name lookup (exact case-insensitive)
            keys = name_to_keys.get(thing.lower(), [])
            if len(keys) == 1:
                yahoo_key = keys[0]
            elif len(keys) == 0:
                errors.append(f"Line {ln}: name not found: '{thing}'")
                continue
            else:
                errors.append(f"Line {ln}: name ambiguous (multiple matches): '{thing}'")
                continue

        rows.append((yahoo_key, qo_group, notes))

    return rows, errors



def reset_draft_state(state: DraftState) -> int:
    """
    Reset draft selections in both canonical DB truth and local UI/autosave state.

    Canonical selected-pick truth is nffl.draft_selection.
    Local state/autosave is still cleared so the current Streamlit session cannot rehydrate stale picks.
    """
    dsn = _get_dsn()
    draft_key = _get_draft_key()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM nffl.draft_selection
                WHERE draft_key=%s
                """,
                (str(draft_key),),
            )
            deleted_rows = int(cur.rowcount or 0)
        conn.commit()

    for pick in state.picks.values():
        pick.selected_player_key = None
        pick.selected_ts_iso = None

    state.pick_log.clear()

    if state.pick_order:
        state.clock.current_pick_id = state.pick_order[0]

    # Reset clock metadata too
    state.clock.is_running = False
    state.clock.pick_started_ts_iso = None
    state.clock.pick_paused_ts_iso = None
    state.clock.elapsed_paused_seconds = 0

    save_autosave(state)
    return deleted_rows


def delete_pick(state: DraftState, pick_id: str, rewind_clock: bool) -> int:
    """
    Delete one selected pick in both canonical DB truth and local UI/autosave state.
    """
    pick_id = str(pick_id or "").strip()
    if not pick_id:
        return 0

    dsn = _get_dsn()
    draft_key = _get_draft_key()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM nffl.draft_selection
                WHERE draft_key=%s
                  AND pick_id=%s
                """,
                (str(draft_key), pick_id),
            )
            deleted_rows = int(cur.rowcount or 0)
        conn.commit()

    if pick_id in state.picks:
        pick = state.picks[pick_id]
        pick.selected_player_key = None
        pick.selected_ts_iso = None

    # Remove all matching local pick-log entries, not just the last one.
    state.pick_log[:] = [
        entry for entry in state.pick_log
        if getattr(entry, "pick_id", None) != pick_id
    ]

    if rewind_clock:
        state.clock.current_pick_id = pick_id

    save_autosave(state)
    return deleted_rows

def _pause_clock(state: DraftState) -> None:
    if state.clock.pick_started_ts_iso is None:
        return
    if state.clock.pick_paused_ts_iso is not None:
        return

    # Capture elapsed so pause truly freezes across refresh
    status = compute_clock_status(
        is_running=state.clock.is_running,
        seconds_per_pick=state.clock.seconds_per_pick,
        started_ts_iso=state.clock.pick_started_ts_iso,
        paused_ts_iso=None,
        elapsed_paused_seconds=state.clock.elapsed_paused_seconds,
    )
    state.clock.elapsed_paused_seconds = int(status.elapsed_seconds)

    state.clock.pick_paused_ts_iso = _utc_now_iso()
    state.clock.is_running = False
    save_autosave(state)


def _resume_clock(state: DraftState) -> None:
    if state.clock.pick_started_ts_iso is None:
        return
    if state.clock.pick_paused_ts_iso is None:
        return

    # Start a new running segment "now"; keep elapsed_paused_seconds accumulated.
    state.clock.pick_started_ts_iso = _utc_now_iso()
    state.clock.pick_paused_ts_iso = None
    state.clock.is_running = True
    save_autosave(state)


def _start_clock_if_needed(state: DraftState) -> None:
    # Start from fresh if never started
    if state.clock.pick_started_ts_iso is None:
        state.clock.elapsed_paused_seconds = 0
        state.clock.pick_started_ts_iso = start_pick_clock()

    # If it was paused, resume semantics
    state.clock.pick_paused_ts_iso = None
    state.clock.is_running = True
    save_autosave(state)




def _publish_nffl_qoft_to_predraft_qos(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    published_by: str,
) -> dict:
    """
    Publish Teams-tab QO decisions into the DraftBoard QO source.

    Source:
      nffl.offseason_keeper_decision

    Target:
      public.qualifying_offer

    This publishes QO placeholders/reservations only. It does not create real draft picks.
    """
    sql = """
    WITH ctx AS (
        SELECT
            %(league_key)s::text AS league_key,
            %(season_year)s::integer AS season_year
    ),
    deleted AS (
        DELETE FROM public.qualifying_offer q
        USING ctx
        WHERE q.league_key = ctx.league_key
          AND q.season_year = ctx.season_year
        RETURNING 1
    ),
    qo_src AS (
        SELECT
            d.league_key,
            d.season_year,
            d.team_key,
            d.yahoo_player_key,
            replace(d.decision_type, 'QO', '')::integer AS qo_level
        FROM nffl.offseason_keeper_decision d
        JOIN ctx
          ON ctx.league_key = d.league_key
         AND ctx.season_year = d.season_year
        WHERE d.decision_type IN ('QO1','QO2','QO3','QO4')
    ),
    inserted AS (
        INSERT INTO public.qualifying_offer (
            league_key,
            season_year,
            team_key,
            yahoo_player_key,
            qo_level,
            note,
            updated_at,
            team_key_yahoo
        )
        SELECT
            league_key,
            season_year,
            team_key,
            yahoo_player_key,
            qo_level,
            'Published from nffl.offseason_keeper_decision by Commissioner reveal action.',
            now(),
            team_key
        FROM qo_src
        ORDER BY team_key, qo_level
        RETURNING 1
    ),
    locked_decisions AS (
        UPDATE nffl.offseason_keeper_decision d
           SET decision_status = 'LOCKED',
               updated_at_utc = now(),
               note = concat_ws(' | ', nullif(d.note, ''), 'Locked by Commissioner reveal action.')
        FROM ctx
        WHERE d.league_key = ctx.league_key
          AND d.season_year = ctx.season_year
          AND d.decision_type IN ('QO1','QO2','QO3','QO4','FT')
        RETURNING 1
    ),
    reveal AS (
        INSERT INTO nffl.league_visibility_state (
            league_key,
            season_year,
            qoft_revealed,
            revealed_at_utc,
            revealed_by,
            created_at_utc,
            updated_at_utc
        )
        SELECT
            league_key,
            season_year,
            true,
            now(),
            %(published_by)s,
            now(),
            now()
        FROM ctx
        ON CONFLICT (league_key, season_year)
        DO UPDATE SET
            qoft_revealed = true,
            revealed_at_utc = now(),
            revealed_by = EXCLUDED.revealed_by,
            updated_at_utc = now()
        RETURNING 1
    )
    SELECT
        (SELECT count(*) FROM deleted) AS deleted_qo_rows,
        (SELECT count(*) FROM inserted) AS inserted_qo_rows,
        (SELECT count(*) FROM locked_decisions) AS locked_decision_rows,
        (SELECT count(*) FROM reveal) AS reveal_rows;
    """

    ft_sql = """
        SELECT count(*) AS ft_count
        FROM nffl.offseason_keeper_decision
        WHERE league_key=%s
          AND season_year=%s
          AND decision_type='FT';
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "league_key": str(league_key),
                    "season_year": int(season_year),
                    "published_by": str(published_by or "commissioner"),
                },
            )
            row = cur.fetchone()
            cur.execute(ft_sql, (str(league_key), int(season_year)))
            ft_count = int((cur.fetchone() or [0])[0] or 0)
        conn.commit()

    return {
        "deleted_qo_rows": int(row[0] or 0),
        "inserted_qo_rows": int(row[1] or 0),
        "locked_decision_rows": int(row[2] or 0),
        "reveal_rows": int(row[3] or 0),
        "ft_count": int(ft_count),
    }


def _reset_nffl_qoft_publish_for_testing(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
) -> dict:
    """
    Clears published predraft QOs and returns QO/FT to private.
    Does not delete saved Teams-tab QO/FT decisions.
    """
    sql = """
    WITH deleted AS (
        DELETE FROM public.qualifying_offer
        WHERE league_key=%s
          AND season_year=%s
        RETURNING 1
    ),
    unreveal AS (
        UPDATE nffl.league_visibility_state
           SET qoft_revealed=false,
               revealed_at_utc=NULL,
               revealed_by=NULL,
               updated_at_utc=now()
         WHERE league_key=%s
           AND season_year=%s
        RETURNING 1
    ),
    unlocked AS (
        UPDATE nffl.offseason_keeper_decision
           SET decision_status='DRAFT',
               updated_at_utc=now(),
               note = NULLIF(
                   BTRIM(
                       regexp_replace(
                           regexp_replace(
                               COALESCE(note, ''),
                               E'\\s*\\|\\s*Locked by Commissioner reveal action\\.$',
                               '',
                               'g'
                           ),
                           E'\\s*\\|\\s*$',
                           '',
                           'g'
                       )
                   ),
                   ''
               )
         WHERE league_key=%s
           AND season_year=%s
           AND decision_status='LOCKED'
        RETURNING 1
    )
    SELECT
        (SELECT count(*) FROM deleted) AS deleted_qo_rows,
        (SELECT count(*) FROM unreveal) AS unreveal_rows,
        (SELECT count(*) FROM unlocked) AS unlocked_decision_rows;
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(league_key),
                    int(season_year),
                    str(league_key),
                    int(season_year),
                    str(league_key),
                    int(season_year),
                ),
            )
            row = cur.fetchone()
        conn.commit()

    return {
        "deleted_qo_rows": int(row[0] or 0),
        "unreveal_rows": int(row[1] or 0),
        "unlocked_decision_rows": int(row[2] or 0),
    }



def _load_nffl_contract_readiness(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
) -> dict[str, object]:
    sql_counts = """
        WITH contract_status AS (
            SELECT
                status AS label,
                count(*)::integer AS value
            FROM nffl.contract
            WHERE league_key=%s
              AND season_year=%s
            GROUP BY status
        ),
        import_reconciliation AS (
            SELECT
                reconciliation_status AS label,
                count(*)::integer AS value
            FROM nffl.v_contract_import_roster_reconciliation
            WHERE league_key=%s
              AND season_year=%s
              AND import_status='ACTIVE_CONTRACT'
            GROUP BY reconciliation_status
        ),
        invalid_active AS (
            WITH active_contracts AS (
                SELECT
                    c.league_key,
                    c.season_year,
                    c.team_key,
                    c.yahoo_player_key
                FROM nffl.contract c
                WHERE c.league_key=%s
                  AND c.season_year=%s
                  AND c.status='active'
            ),
            rostered_same_team AS (
                SELECT DISTINCT
                    rsp.league_key,
                    rsp.season_year,
                    rsp.team_key,
                    rsp.yahoo_player_key
                FROM nffl.roster_snapshot_player rsp
                WHERE rsp.league_key=%s
                  AND rsp.season_year=%s
            )
            SELECT count(*)::integer AS value
            FROM active_contracts ac
            LEFT JOIN rostered_same_team r
              ON r.league_key=ac.league_key
             AND r.season_year=ac.season_year
             AND r.team_key=ac.team_key
             AND r.yahoo_player_key=ac.yahoo_player_key
            WHERE r.yahoo_player_key IS NULL
        )
        SELECT 'contract_status' AS section, label, value FROM contract_status
        UNION ALL
        SELECT 'import_reconciliation' AS section, label, value FROM import_reconciliation
        UNION ALL
        SELECT 'current_invalid_active_contracts' AS section, 'invalid_active_contracts' AS label, value FROM invalid_active
        ORDER BY section, label;
    """

    sql_blockers = """
        SELECT
            team_name,
            player_name,
            matched_full_name,
            yahoo_player_key,
            years_remaining_2026,
            reconciliation_status
        FROM nffl.v_contract_import_active_blockers
        WHERE league_key=%s
          AND season_year=%s
        ORDER BY team_name, player_name;
    """

    counts: dict[str, int] = {}
    blockers: list[dict[str, object]] = []

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql_counts,
                (
                    str(league_key),
                    int(season_year),
                    str(league_key),
                    int(season_year),
                    str(league_key),
                    int(season_year),
                    str(league_key),
                    int(season_year),
                ),
            )
            for section, label, value in cur.fetchall():
                counts[f"{section}:{label}"] = int(value or 0)

            cur.execute(sql_blockers, (str(league_key), int(season_year)))
            for row in cur.fetchall():
                blockers.append(
                    {
                        "team_name": str(row[0] or ""),
                        "player_name": str(row[1] or ""),
                        "matched_full_name": str(row[2] or ""),
                        "yahoo_player_key": str(row[3] or ""),
                        "years_remaining_2026": int(row[4] or 0),
                        "reconciliation_status": str(row[5] or ""),
                    }
                )

    return {"counts": counts, "blockers": blockers}


def _render_nffl_contract_readiness_panel() -> None:
    dsn = _get_dsn()
    league_key = _get_league_key()
    season_year = _get_season_year()

    st.markdown("#### NFFL Contract / Roster Readiness")

    try:
        readiness = _load_nffl_contract_readiness(
            dsn=dsn,
            league_key=league_key,
            season_year=season_year,
        )
    except Exception as exc:
        st.error(f"Could not load NFFL contract readiness: {exc}")
        return

    counts = dict(readiness.get("counts") or {})
    blockers = list(readiness.get("blockers") or [])

    active_contracts = int(counts.get("contract_status:active", 0))
    void_contracts = int(counts.get("contract_status:void", 0))
    invalid_active = int(counts.get("current_invalid_active_contracts:invalid_active_contracts", 0))
    eligible_import = int(counts.get("import_reconciliation:ACTIVE_ELIGIBLE_SAME_TEAM", 0))
    import_blockers = sum(
        value
        for key, value in counts.items()
        if key.startswith("import_reconciliation:")
        and not key.endswith(":ACTIVE_ELIGIBLE_SAME_TEAM")
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active Contracts", active_contracts)
    c2.metric("Voided Contracts", void_contracts)
    c3.metric("Invalid Active", invalid_active)
    c4.metric("Import Eligible", eligible_import)
    c5.metric("Import Blockers", import_blockers)

    if invalid_active > 0:
        st.error(
            "Blocking issue: active contracts include players not found on the same team "
            "in the end-of-prior-season roster snapshot."
        )
    else:
        st.success("Current active contracts pass same-team end-of-prior-season roster reconciliation.")

    if blockers:
        with st.expander("Import reconciliation blockers", expanded=False):
            st.caption(
                "These are contract-sheet staging rows that should not become active contracts unless reviewed. "
                "They are not a current DraftBoard blocker if the current Invalid Active count is zero."
            )
            st.dataframe(
                blockers,
                hide_index=True,
                use_container_width=True,
            )
    else:
        st.info("No active-contract import blockers found.")


def _render_nffl_qoft_publish_controls(state: DraftState, auth_ctx: dict | None = None) -> None:
    dsn = _get_dsn()
    league_key = _get_league_key()
    season_year = _get_season_year()

    st.markdown("##### Publish / Reveal Status")
    st.caption(
        "Publishes Teams-tab QO selections into the DraftBoard QO source, reveals QO/FT choices, "
        "and locks the current QO/FT decision rows."
    )

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        count(*) FILTER (WHERE decision_type IN ('QO1','QO2','QO3','QO4')) AS qo_rows,
                        count(*) FILTER (WHERE decision_type = 'FT') AS ft_rows,
                        count(*) FILTER (WHERE decision_status='LOCKED') AS locked_rows
                    FROM nffl.offseason_keeper_decision
                    WHERE league_key=%s
                      AND season_year=%s;
                    """,
                    (str(league_key), int(season_year)),
                )
                counts = cur.fetchone() or (0, 0, 0)

                cur.execute(
                    """
                    SELECT count(*)
                    FROM public.qualifying_offer
                    WHERE league_key=%s
                      AND season_year=%s;
                    """,
                    (str(league_key), int(season_year)),
                )
                published_qos = int((cur.fetchone() or [0])[0] or 0)

                cur.execute(
                    """
                    SELECT COALESCE(qoft_revealed,false), revealed_at_utc, revealed_by
                    FROM nffl.league_visibility_state
                    WHERE league_key=%s
                      AND season_year=%s;
                    """,
                    (str(league_key), int(season_year)),
                )
                visibility = cur.fetchone()
    except Exception as exc:
        st.error(f"Could not load QO/FT publish status: {exc}")
        return

    qo_rows = int(counts[0] or 0)
    ft_rows = int(counts[1] or 0)
    locked_rows = int(counts[2] or 0)
    revealed = bool(visibility and visibility[0])
    revealed_at = visibility[1] if visibility else None
    revealed_by = visibility[2] if visibility else ""

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saved QOs", qo_rows)
    c2.metric("Saved FT", ft_rows)
    c3.metric("Published QOs", published_qos)
    c4.metric("Locked Decisions", locked_rows)

    if revealed:
        st.success(f"QO/FT is currently revealed. Revealed by {revealed_by or 'unknown'} at {revealed_at}.")
    else:
        st.warning("QO/FT is currently private and not yet published to the DraftBoard QO source.")

    publish_blockers: list[str] = []

    try:
        readiness = _load_nffl_contract_readiness(
            dsn=dsn,
            league_key=league_key,
            season_year=season_year,
        )
        readiness_counts = dict(readiness.get("counts") or {})
        invalid_active_contracts = int(
            readiness_counts.get("current_invalid_active_contracts:invalid_active_contracts", 0)
        )
        if invalid_active_contracts > 0:
            publish_blockers.append(
                f"{invalid_active_contracts} active contract(s) failed same-team end-roster reconciliation"
            )
    except Exception as exc:
        publish_blockers.append(f"could not verify contract readiness: {exc}")

    if publish_blockers:
        st.error("Publish / Reveal is blocked: " + "; ".join(publish_blockers) + ".")

    with st.form("nffl_publish_reveal_qoft_form", clear_on_submit=False):
        confirm = st.checkbox(
            "Confirm publish/reveal QO-FT",
            value=False,
            key="nffl_confirm_publish_reveal_qoft",
        )
        publish_clicked = st.form_submit_button(
            "Publish / Reveal QO-FT",
            type="primary",
            disabled=bool(publish_blockers),
        )

    if publish_clicked:
        if not confirm:
            st.warning("Confirm publish/reveal QO-FT first.")
            return

        actor = "commissioner"
        if isinstance(auth_ctx, dict):
            actor = str(auth_ctx.get("acting_as") or auth_ctx.get("display_name") or "commissioner")

        try:
            result = _publish_nffl_qoft_to_predraft_qos(
                dsn=dsn,
                league_key=league_key,
                season_year=season_year,
                published_by=actor,
            )
            st.success(f"Published/revealed QO-FT: {result}")
            try:
                save_autosave(state)
            except Exception:
                pass
            st.rerun()
        except Exception as exc:
            st.error(f"Publish/reveal failed: {exc}")

    with st.expander("Reset QO/FT reveal to private", expanded=False):
        st.caption(
            "This clears published QO rows for this league/year and returns QO/FT to private. "
            "It does not delete saved Teams-tab QO/FT decisions."
        )
        with st.form("nffl_reset_qoft_publish_form", clear_on_submit=False):
            reset_confirm = st.checkbox(
                "Confirm reset QO/FT reveal",
                value=False,
                key="nffl_confirm_reset_qoft_publish",
            )
            reset_clicked = st.form_submit_button("Reset QO/FT Reveal to Private")

        if reset_clicked:
            if not reset_confirm:
                st.warning("Confirm reset QO/FT reveal first.")
                return

            try:
                result = _reset_nffl_qoft_publish_for_testing(
                    dsn=dsn,
                    league_key=league_key,
                    season_year=season_year,
                )
                st.success(f"Reset QO/FT reveal state: {result}")
                st.rerun()
            except Exception as exc:
                st.error(f"QO/FT reveal reset failed: {exc}")


def render_commissioner_actions(state: DraftState, auth_ctx: dict[str, object] | None = None) -> None:
    st.subheader("Commissioner Tools")

    _render_nffl_contract_readiness_panel()

    st.divider()

    st.markdown("#### NFFL QO/FT Publish + Reveal")
    _render_nffl_qoft_publish_controls(state, auth_ctx=auth_ctx)

    st.divider()

    auth_ctx = dict(auth_ctx or {})
    is_site_admin = bool(auth_ctx.get("is_site_admin", False))
    league_key = _get_league_key()
    is_milf = str(league_key) == "469.l.60688"

    # Feature-gate league-specific commissioner tools.
    # NFFL has prospect_tags=false, so Prospect Tag controls must not render there.
    try:
        from draftboard.state.league_profile import get_active_league_profile
        _profile = get_active_league_profile()
        _features = dict((_profile or {}).get("features") or {})
    except Exception:
        _features = {}
    prospect_tags_enabled = bool(_features.get("prospect_tags", False))

    # NFFL commissioner page cleanup: hide stale/risky legacy tools without deleting shared code.
    show_disabled_season_team_assignments = False
    show_trade_builder = False
    show_contract_overrides = False

    with st.expander("Admin Password Reset Tool", expanded=False):
        if not is_site_admin:
            st.info("Site admin access required.")
        else:
            try:
                dsn = _get_dsn()
                league_key = _get_league_key()
                season_year = _get_season_year()
                accounts = _load_resettable_manager_accounts(dsn, league_key, season_year)
            except Exception as e:
                st.error(f"Failed to load manager accounts: {e}")
                st.stop()

            reset_candidates = [a for a in accounts if not bool(a.get("is_site_admin", False))]

            if not reset_candidates:
                st.warning("No resettable manager accounts found.")
            else:
                option_map: dict[str, dict] = {}
                option_labels: list[str] = []

                for a in reset_candidates:
                    label = (
                        f"{a.get('team_name') or '(No Team)'} — "
                        f"{a.get('email_normalized') or ''} "
                        f"[FID {a.get('franchise_id')}]"
                    )
                    option_labels.append(label)
                    option_map[label] = a

                selected_label = st.selectbox(
                    "Manager account to reset",
                    options=option_labels,
                    key="admin_reset_manager_select",
                )

                selected = option_map[selected_label]
                temp_pw = _generate_temp_password(12)

                st.caption(
                    "Reset sets a new temporary password and forces the manager "
                    "to change it on next login."
                )

                if st.button("Generate temporary password and reset", key="admin_reset_password_btn", type="primary"):
                    try:
                        ok = _admin_reset_local_user_password(
                            dsn=dsn,
                            user_id=int(selected["user_id"]),
                            temp_password=temp_pw,
                        )
                    except Exception as e:
                        st.error(f"Password reset failed: {e}")
                        ok = False

                    if not ok:
                        st.error("Password reset did not affect exactly one active user.")
                    else:
                        st.success("Password reset successful.")
                        st.code(temp_pw, language=None)
                        st.warning("Copy this temporary password now. It is only shown after reset.")
    # -----------------------
    # Season Team Assignments (Commissioner)
    # -----------------------
    if show_disabled_season_team_assignments:
        with st.expander("Season Team Assignments (Commissioner)", expanded=False):
            st.warning(
                "Temporarily disabled while multi-league scoping is repaired."
            )
            st.info(
                "This tool currently assumes one league per season and can hide the rest of Commissioner Tools."
            )

    # -----------------------
    # Set Draft Order
    # -----------------------
    with st.expander("Set Draft Order", expanded=False):
        st.subheader("Set Draft Order")

        TEAM_UNASSIGNED = ""
        manager_count = len(getattr(state, "teams", {}) or {})
        if manager_count <= 0:
            manager_count = len(getattr(state, "draft_order_team_keys_by_slot", []) or []) or 16

        def _init_slot_map() -> dict[int, str]:
            """
            Preferred source: state.draft_order_team_keys_by_slot (slot 1..N)
            Fallback: derive from Round 1 picks original_team_key by slot.
            """
            slot_map: dict[int, str] = {}

            # 1) Prefer persisted slot-order list if present
            order = getattr(state, "draft_order_team_keys_by_slot", None)
            if isinstance(order, list) and len(order) == manager_count:
                for s in range(1, manager_count + 1):
                    tk = str(order[s - 1] or "")
                    slot_map[s] = tk if tk else TEAM_UNASSIGNED
            else:
                # 2) Fallback: Round 1 picks -> slot -> ORIGINAL team key (column owner baseline)
                # IMPORTANT: column order must NOT change when a pick is traded.
                r1 = [p for p in state.picks.values() if int(getattr(p, "round_number", 0) or 0) == 1]
                for p in r1:
                    try:
                        slot = int(getattr(p, "slot", 0) or 0)
                    except Exception:
                        continue
                    if 1 <= slot <= manager_count:
                        orig = str(getattr(p, "original_team_key", "") or "").strip()
                        ow = str(getattr(p, "owner_team_key", "") or "").strip()
                        tk = orig or ow  # defensive fallback only
                        slot_map[slot] = tk if tk else TEAM_UNASSIGNED

            # Ensure all active slots exist
            for s in range(1, manager_count + 1):
                slot_map.setdefault(s, TEAM_UNASSIGNED)

            return slot_map

        # Keep interactive mapping in session_state for snappy dropdown UX.
        # Reset stale maps if switching between leagues with different manager counts.
        _existing_slot_keys = sorted(
            int(k) for k in dict(st.session_state.get("draft_order_slot_to_team", {}) or {}).keys()
            if str(k).isdigit()
        )
        if (
            "draft_order_slot_to_team" not in st.session_state
            or _existing_slot_keys != list(range(1, manager_count + 1))
        ):
            st.session_state["draft_order_slot_to_team"] = _init_slot_map()

        slot_to_team: dict[int, str] = dict(st.session_state["draft_order_slot_to_team"])

        # Helpers
        team_keys = sorted([t.team_key for t in state.teams.values()])

        def team_name(k: str) -> str:
            return state.teams[k].name if k in state.teams else k

        def _team_to_slot(slot_map: dict[int, str]) -> dict[str, int]:
            out: dict[str, int] = {}
            for s, tk in slot_map.items():
                if tk and tk != TEAM_UNASSIGNED:
                    out[str(tk)] = int(s)
            return out

        team_to_slot = _team_to_slot(slot_to_team)

        st.write(
            "Assign each team a pick number for this league. "
            "This controls BOARD COLUMN ORDER (slot order), not pick ownership."
        )

        selected_team = st.selectbox(
            "Team",
            options=team_keys,
            key="draft_order_team_select",
            format_func=team_name,
        )

        current_slot_for_team = team_to_slot.get(str(selected_team), 0)

        pick_options = [TEAM_UNASSIGNED] + list(range(1, manager_count + 1))
        pick_index = 0
        if current_slot_for_team in range(1, manager_count + 1):
            pick_index = pick_options.index(current_slot_for_team)

        chosen_pick = st.selectbox(
            "Pick #",
            options=pick_options,
            index=pick_index,
            key="draft_order_pick_select",
            format_func=lambda x: "" if x == TEAM_UNASSIGNED else f"Pick #{x}",
            help=f"Choose Pick #1..#{manager_count} for the selected team. Blank means unassigned.",
        )

        if st.button("Apply Assignment", type="primary", key="draft_order_apply_btn"):
            # Remove this team from any existing slot
            for s in range(1, manager_count + 1):
                if slot_to_team.get(s, TEAM_UNASSIGNED) == selected_team:
                    slot_to_team[s] = TEAM_UNASSIGNED

            # If assigning to a pick slot, set that slot to this team
            if chosen_pick != TEAM_UNASSIGNED:
                s_new = int(chosen_pick)
                slot_to_team[s_new] = selected_team

            st.session_state["draft_order_slot_to_team"] = dict(slot_to_team)
            st.success("Assignment applied (not saved yet).")

        c1, c2, c3 = st.columns([1, 1, 2])

        with c1:
            if st.button("Reset from saved slot order", key="draft_order_reset_btn"):
                st.session_state["draft_order_slot_to_team"] = _init_slot_map()
                st.rerun()

        with c2:
            if st.button("Save Draft Order", key="draft_order_save_btn"):
                slot_to_team = dict(st.session_state["draft_order_slot_to_team"])

                # Persist as list[str] where index 0 = slot 1 ... index 15 = slot 16
                order: list[str] = []
                for s in range(1, manager_count + 1):
                    tk = slot_to_team.get(s, TEAM_UNASSIGNED)
                    order.append(str(tk) if tk else "")

                # Save to state (canonical slot order)
                state.draft_order_team_keys_by_slot = order

                # Rebase picks grid slot baselines so "TRADE" reflects real trades only.
                # Deterministic rule:
                # - We always rebase original_team_key to the new slot baseline (column identity).
                # - We only move owner_team_key when the pick was NOT previously traded:
                #     owner_team_key == original_team_key (pre-rebase).
                picks_obj = (getattr(state, "picks", {}) or {})
                new_slot_to_team = dict(slot_to_team)

                # Apply rebasing for every pick (policy: reset STANDARD owners to new baseline)
                for ps in picks_obj.values():

                    # -------- dict path --------
                    if isinstance(ps, dict):
                        try:
                            slot = int(ps.get("slot", 0) or 0)
                            rnd = int(ps.get("round_number", 0) or 0)
                        except Exception:
                            continue
                        if slot < 1 or slot > manager_count:
                            continue

                        new_tk = str(new_slot_to_team.get(slot, "") or "").strip()
                        if not new_tk:
                            continue

                        prev_orig = str(ps.get("original_team_key", "") or "").strip()
                        prev_owner = str(ps.get("owner_team_key", "") or "").strip()

                        # Always rebase baseline to slot identity
                        ps["original_team_key"] = new_tk

                        # Preserve real traded picks.
                        # If the pick was not traded before rebasing, move owner to the new baseline.
                        if prev_owner == prev_orig:
                            ps["owner_team_key"] = new_tk

                        continue

                    # -------- PickSlot object path --------
                    try:
                        slot = int(getattr(ps, "slot", 0) or 0)
                        rnd = int(getattr(ps, "round_number", 0) or 0)
                    except Exception:
                        continue
                    if slot < 1 or slot > manager_count:
                        continue

                    new_tk = str(new_slot_to_team.get(slot, "") or "").strip()
                    if not new_tk:
                        continue

                    prev_orig = str(getattr(ps, "original_team_key", "") or "").strip()
                    prev_owner = str(getattr(ps, "owner_team_key", "") or "").strip()

                    # Always rebase baseline to slot identity
                    setattr(ps, "original_team_key", new_tk)

                    # Preserve real traded picks.
                    # If the pick was not traded before rebasing, move owner to the new baseline.
                    if prev_owner == prev_orig:
                        setattr(ps, "owner_team_key", new_tk)

                save_autosave(state)
                st.success("Draft order saved (slot order).")
                st.rerun()

        with c3:
            # Display current mapping as a table
            slot_to_team = dict(st.session_state["draft_order_slot_to_team"])
            table_rows = []
            for s in range(1, manager_count + 1):
                tk = slot_to_team.get(s, TEAM_UNASSIGNED)
                table_rows.append(
                    {
                        "Pick #": s,
                        "Team": "" if not tk else team_name(tk),
                    }
                )
            st.table(table_rows)

    # -----------------------
    # Refresh Yahoo Player Universe
    # -----------------------
    with st.expander("Refresh Yahoo Player Universe", expanded=False):
        st.caption(
            "Refreshes player meta (rank, % rostered, prior-year stats) from Yahoo into Postgres, "
            "then reloads players into DraftBoard. Does not change contracts/QOs/picks."
        )

        # Show last refresh results (persisted across reruns)
        last = st.session_state.get("yahoo_refresh_last", None)
        if isinstance(last, dict):
            st.markdown("**Last refresh result**")
            st.write(f"Finished at (UTC): {last.get('finished_utc', '')}")
            st.write(f"Exit code: {last.get('exit_code', '')}")
            st.write(f"Duration (sec): {last.get('duration_sec', '')}")
            st.write(f"Players reloaded: {last.get('players_before', '')} → {last.get('players_after', '')}")
            st.write(f"Meta rows updated (last 10 min): {last.get('meta_updated_last_10m', '')}")

            log_text = (last.get("stdout", "") or "") + ("\n" if last.get("stdout") and last.get("stderr") else "") + (last.get("stderr", "") or "")
            if log_text.strip():
                lines = [ln for ln in log_text.splitlines()]

                # Lightweight “conflict” signals from log text (deterministic string matches)
                skipped = [ln for ln in lines if "skipping bad player_key" in ln.lower()]
                conflicts = [ln for ln in lines if "conflict" in ln.lower()]

                st.write(f"Skipped keys: **{len(skipped)}**")
                st.write(f"Conflicts detected (log-based): **{len(conflicts)}**")

                # Show only the last 30 lines to keep UI clean
                tail = "\n".join(lines[-30:])
                st.text_area("Refresh log (last 30 lines)", tail, height=220)

                # Optional: allow full log viewing (Streamlit disallows nested expanders)
                if st.checkbox("Show full refresh log", value=False, key="yahoo_refresh_show_full_log"):
                    st.text_area("Full refresh log", log_text, height=400)
            else:
                st.info("Last refresh produced no script output (stdout/stderr).")

            st.divider()

            # If a refresh is in progress (same session), show a visible indicator
        if st.session_state.get("yahoo_refresh_running") is True:
            st.success("Loading... (refresh in progress)")

        if st.button("Run Refresh Now", key="btn_refresh_yahoo_universe"):
            st.session_state["yahoo_refresh_running"] = True

            try:
                with st.spinner("Refreshing Yahoo player universe..."):
                    dsn = _get_dsn()
                    before_n = len(getattr(state, "players", {}) or {})

                    # Run the existing loader script (updates DB tables/views used by v_available_players_current)
                    t0 = time.time()
                    cmd = [sys.executable, "/app/scripts/yahoo/yahoo_bulk_load.py"]

                    env = os.environ.copy()
                    env.setdefault("YAHOO_LEAGUE_KEY", _get_league_key())
                    env.setdefault("YAHOO_SEASON_YEAR", str(_get_season_year()))

                    # Derive Yahoo game key from league_key prefix, e.g. "469.l.41640" -> "469"
                    try:
                        game_key = _get_league_key().split(".")[0]
                        if game_key.isdigit():
                            env.setdefault("YAHOO_GAME_KEY", game_key)
                    except Exception:
                        pass

                    try:
                        proc = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            check=False,
                            env=env,
                        )
                    except Exception as e:
                        st.error(f"Refresh failed (could not run yahoo_bulk_load.py): {e}")
                        st.stop()

                    # Always keep stdout/stderr for the "receipt"
                    combined = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")

                    if proc.returncode != 0:
                        # Persist failure receipt too
                        st.session_state["yahoo_refresh_last"] = {
                            "finished_utc": _utc_now_iso(),
                            "exit_code": int(proc.returncode),
                            "duration_sec": round(float(time.time() - t0), 2),
                            "players_before": int(before_n),
                            "players_after": "",
                            "meta_updated_last_10m": "",
                            "stdout": proc.stdout or "",
                            "stderr": proc.stderr or "",
                        }
                        st.error(f"Refresh failed (yahoo_bulk_load.py exit code {proc.returncode}).")
                        st.stop()

                    # Quick DB sanity check (best-effort)
                    meta_updated_last_10m = ""
                    try:
                        league_key = _get_league_key()
                        sql_check = """
                            SELECT
                              count(*) FILTER (WHERE updated_at >= now() - interval '10 minutes') AS updated_last_10m
                            FROM public.yahoo_player_meta
                            WHERE source_game_key = %s;
                        """
                        with psycopg.connect(dsn) as conn:
                            with conn.cursor() as cur:
                                cur.execute(sql_check, (str(league_key).split(".")[0],))
                                (meta_updated_last_10m,) = cur.fetchone()
                    except Exception:
                        meta_updated_last_10m = ""

                    # Reload player universe from DB into in-memory state (preserves picks/contracts/QOs/PT)
                    from draftboard.data.db_players import load_available_players
                    state.players = load_available_players(dsn)
                    after_n = len(state.players or {})

                    # Keep contract cache in sync (PT eligibility uses contracted_keys_2026)
                    _refresh_contract_cache_into_session_state()

                    # Persist receipt so results survive st.rerun()
                    st.session_state["yahoo_refresh_last"] = {
                        "finished_utc": _utc_now_iso(),
                        "exit_code": int(proc.returncode),
                        "duration_sec": round(float(time.time() - t0), 2),
                        "players_before": int(before_n),
                        "players_after": int(after_n),
                        "meta_updated_last_10m": int(meta_updated_last_10m) if meta_updated_last_10m not in (None, "") else "",
                        "stdout": proc.stdout or "",
                        "stderr": proc.stderr or "",
                    }

                    save_autosave(state)

                # Show a short success toast before rerun (the receipt persists anyway)
                st.success(f"Player universe refreshed and reloaded. Players: {before_n} → {after_n}")

            finally:
                st.session_state["yahoo_refresh_running"] = False

            st.rerun()

    # -----------------------
    # Trade Builder (UI ONLY — SAFE)
    # -----------------------
    if show_trade_builder:
        with st.expander("Trade (Builder)", expanded=False):

            st.caption(
                "Trade Builder writes trade receipts to DB. "
                "On Submit: inserts trade + trade_asset rows. "
                "For contracted players, it also updates canonical contract SSOT in public.contract. "
                "Pick ownership is not yet rewired here."
            )

            # --------------------------------------------------
            # Session State Container
            # --------------------------------------------------
            if "trade_builder_v1" not in st.session_state:
                st.session_state["trade_builder_v1"] = {
                    "left":  {"team_key": "", "players": [], "picks": []},
                    "right": {"team_key": "", "players": [], "picks": []},
                    "finalize": False,
                }

            tb = st.session_state["trade_builder_v1"]

            # --------------------------------------------------
            # Helpers
            # --------------------------------------------------
            def team_name(k: str) -> str:
                t = state.teams.get(k)
                return t.name if t else k

            team_keys = sorted([t.team_key for t in state.teams.values()])

            # ---------- Player options ----------
            player_keys_sorted = sorted(
                state.players.keys(),
                key=lambda pk: (
                    getattr(state.players[pk], "rank_value", None) is None,
                    getattr(state.players[pk], "rank_value", None) if getattr(state.players[pk], "rank_value", None) is not None else 999999,
                    state.players[pk].name or "",
                    pk,
                ),
            )

            player_label_by_key = {
                pk: f"{state.players[pk].name} ({pk})"
                for pk in player_keys_sorted
            }

            player_options = [""] + list(player_label_by_key.values())
            player_key_by_label = {v: k for k, v in player_label_by_key.items()}

            # ---------- Pick options ----------
            def _pick_label(ps):
                r = int(getattr(ps, "round_number", 0) or 0)
                s = int(getattr(ps, "slot", 0) or 0)

                # UI label: show pick_id + (R#.slot) + current owner only
                owner = str(getattr(ps, "owner_team_key", "") or "").strip()
                owner_nm = team_name(owner) if owner else ""

                return f"{ps.pick_id} (R{r}.{s})" + (f" | owner={owner_nm}" if owner_nm else "")

            pick_ids_sorted = sorted(
                state.picks.keys(),
                key=lambda pid: (
                    state.picks[pid].round_number,
                    state.picks[pid].slot,
                    pid,
                ),
            )

            pick_label_by_id = {
                pid: _pick_label(state.picks[pid])
                for pid in pick_ids_sorted
            }

            pick_options = [""] + list(pick_label_by_id.values())
            pick_id_by_label = {v: k for k, v in pick_label_by_id.items()}

            MAX_ITEMS = 10

            # --------------------------------------------------
            # Trade Pane Renderer
            # --------------------------------------------------
            def _pane(which: str):

                pane = tb[which]
                title = "Team A (gets)" if which == "left" else "Team B (gets)"

                st.markdown(f"### {title}")

                pane["team_key"] = st.selectbox(
                    "Select team",
                    options=[""] + team_keys,
                    index=([""] + team_keys).index(pane["team_key"])
                    if pane["team_key"] in ([""] + team_keys)
                    else 0,
                    key=f"trade_team_{which}",
                    format_func=lambda k: "" if k == "" else team_name(k),
                )

                # ---------------- PLAYER ADD ----------------
                st.markdown("**Add player to get**")
                st.caption("Add contract to player:")

                c1, c2, c3 = st.columns([3, 1, 1])

                with c1:
                    sel_player = st.selectbox(
                        "Player",
                        options=player_options,
                        key=f"trade_player_sel_{which}",
                        label_visibility="collapsed",
                    )

                with c2:
                    contract_years = st.number_input(
                        "Contract years (0–5)",
                        min_value=0,
                        max_value=5,
                        value=0,
                        step=1,
                        key=f"trade_player_contract_{which}",
                    )

                with c3:
                    if st.button("Add", key=f"trade_player_add_{which}", use_container_width=True):
                        pk = player_key_by_label.get(sel_player, "")
                        existing_keys = {str(x.get("player_key")) for x in pane["players"] if isinstance(x, dict)}
                        if pk and pk not in existing_keys:
                            if len(pane["players"]) < MAX_ITEMS:
                                pane["players"].append({"player_key": pk, "contract_years": int(contract_years)})

                # Display players
                for i, rec in enumerate(list(pane["players"])):
                    pk = str(rec.get("player_key") or "")
                    yrs = int(rec.get("contract_years") or 0)

                    nm = state.players.get(pk).name if pk in state.players else pk
                    a, b = st.columns([6, 1])
                    with a:
                        st.write(f"• {nm} ({pk}) — contract {yrs}y")
                    with b:
                        if st.button("✕", key=f"trade_rm_player_{which}_{i}"):
                            pane["players"].pop(i)
                            st.rerun()

                # ---------------- PICK ADD ----------------
                st.markdown("**Add pick to get**")

                c3, c4 = st.columns([3, 1])

                with c3:
                    # Picks to get should come from the OTHER team (the team you're trading with)
                    other_team_key = tb["right"]["team_key"] if which == "left" else tb["left"]["team_key"]

                    if not other_team_key:
                        st.info("First select a team to trade with.")
                        sel_pick = ""  # no selection possible yet
                        other_pick_id_by_label = {}
                    else:
                        other_pick_ids = []
                        for pid in pick_ids_sorted:
                            ps = state.picks.get(pid)
                            if not ps:
                                continue
                            ow = str(getattr(ps, "owner_team_key", "") or "").strip()
                            if ow == str(other_team_key).strip():
                                other_pick_ids.append(pid)

                        other_pick_labels = [""] + [pick_label_by_id[pid] for pid in other_pick_ids]
                        other_pick_id_by_label = {pick_label_by_id[pid]: pid for pid in other_pick_ids}

                        sel_pick = st.selectbox(
                            "Pick (from other team)",
                            options=other_pick_labels,
                            key=f"trade_pick_sel_{which}",
                            label_visibility="collapsed",
                        )

                with c4:
                    if st.button("Add", key=f"trade_pick_add_{which}", use_container_width=True):
                        pid = other_pick_id_by_label.get(sel_pick, "")
                        if pid and pid not in pane["picks"]:
                            if len(pane["picks"]) < MAX_ITEMS:
                                pane["picks"].append(pid)

                # Display picks
                for i, pid in enumerate(list(pane["picks"])):
                    lbl = pick_label_by_id.get(pid, pid)
                    a, b = st.columns([6, 1])
                    with a:
                        st.write(f"• {lbl}")
                    with b:
                        if st.button("✕", key=f"trade_rm_pick_{which}_{i}"):
                            pane["picks"].remove(pid)
                            st.rerun()
                        
                # (Contracts are attached to players at add-time via the contract_years input above.)

            # --------------------------------------------------
            # Layout
            # --------------------------------------------------
            left_col, right_col = st.columns(2)

            with left_col:
                _pane("left")

            with right_col:
                _pane("right")

            # --------------------------------------------------
            # Preview (UI only)
            # --------------------------------------------------
            st.divider()
            st.markdown("### Trade Preview")

            left_ct = len(tb["left"]["players"]) + len(tb["left"]["picks"])
            right_ct = len(tb["right"]["players"]) + len(tb["right"]["picks"])

            st.write(
                f"Team A items: {left_ct} "
                f"(players={len(tb['left']['players'])}, picks={len(tb['left']['picks'])})"
            )

            st.write(
                f"Team B items: {right_ct} "
                f"(players={len(tb['right']['players'])}, picks={len(tb['right']['picks'])})"
            )

            st.caption("Preview of the trade to be written to DB on Submit.")

            st.divider()

            # -----------------------
            # Finalize → Submit
            # -----------------------
            tb["finalize"] = st.checkbox(
                "Finalize Trade (locks selections and enables Submit)",
                value=bool(tb.get("finalize", False)),
                key="trade_finalize_checkbox",
            )

            teams_ok = bool(tb["left"]["team_key"]) and bool(tb["right"]["team_key"])
            teams_distinct = (tb["left"]["team_key"] != tb["right"]["team_key"]) if teams_ok else False
            can_submit = bool(tb["finalize"]) and teams_ok and teams_distinct

            submit = st.button(
                "Submit Trade (writes DB rows)",
                type="primary",
                disabled=not can_submit,
                key="trade_submit_btn",
            )

            if submit:
                try:
                    dsn = _get_dsn()
                    league_key = _get_league_key()
                    season_year = _get_season_year()

                    team_a = str(tb["left"]["team_key"])
                    team_b = str(tb["right"]["team_key"])

                    asset_rows: list[dict] = []

                    # Team A gets: FROM Team B -> TO Team A
                    for rec in tb["left"]["players"]:
                        pk = str(rec.get("player_key") or "").strip()
                        yrs = int(rec.get("contract_years") or 0)
                        if pk:
                            asset_rows.append(
                                {
                                    "asset_type": "PLAYER",
                                    "asset_id": pk,
                                    "from_team_key": team_b,
                                    "to_team_key": team_a,
                                    "snapshot": {"contract_years": yrs},
                                }
                            )

                    for pid in tb["left"]["picks"]:
                        pid = str(pid or "").strip()
                        if pid:
                            asset_rows.append(
                                {
                                    "asset_type": "PICK",
                                    "asset_id": pid,
                                    "from_team_key": team_b,
                                    "to_team_key": team_a,
                                    "snapshot": {},
                                }
                            )

                    # Team B gets: FROM Team A -> TO Team B
                    for rec in tb["right"]["players"]:
                        pk = str(rec.get("player_key") or "").strip()
                        yrs = int(rec.get("contract_years") or 0)
                        if pk:
                            asset_rows.append(
                                {
                                    "asset_type": "PLAYER",
                                    "asset_id": pk,
                                    "from_team_key": team_a,
                                    "to_team_key": team_b,
                                    "snapshot": {"contract_years": yrs},
                                }
                            )

                    for pid in tb["right"]["picks"]:
                        pid = str(pid or "").strip()
                        if pid:
                            asset_rows.append(
                                {
                                    "asset_type": "PICK",
                                    "asset_id": pid,
                                    "from_team_key": team_a,
                                    "to_team_key": team_b,
                                    "snapshot": {},
                                }
                            )

                    trade_id = _insert_trade(
                        dsn=dsn,
                        league_key=league_key,
                        season_year=season_year,
                        created_by="commissioner",
                        notes="",
                    )
                    n_assets = _insert_trade_assets(dsn=dsn, trade_id=trade_id, rows=asset_rows)

                    # Push ownership changes into existing canonical SSOT paths.
                    # - PLAYER with contract_years > 0 -> public.contract
                    # - PICK -> DraftBoard persisted state (owner_team_key only; columns remain fixed)
                    n_contract_updates = 0
                    n_pick_updates = 0
                    matched_pick_ids = []
                    missing_pick_ids = []

                    for row in asset_rows:
                        asset_type = str(row.get("asset_type") or "").strip().upper()
                        asset_id = str(row.get("asset_id") or "").strip()
                        to_team_key = str(row.get("to_team_key") or "").strip()

                        if not asset_id or not to_team_key:
                            continue

                        if asset_type == "PLAYER":
                            snapshot = row.get("snapshot") or {}
                            years_remaining = int(snapshot.get("contract_years") or 0)

                            if years_remaining > 0:
                                n_contract_updates += _update_contract_team_key(
                                    dsn=dsn,
                                    league_key=league_key,
                                    season_year=season_year,
                                    yahoo_player_key=asset_id,
                                    to_team_key=to_team_key,
                                    note=f"trade:{trade_id}",
                                )
                            continue

                        if asset_type == "PICK":
                            ps = (getattr(state, "picks", {}) or {}).get(asset_id)
                            if not ps:
                                missing_pick_ids.append(asset_id)
                                continue

                            matched_pick_ids.append(asset_id)

                            if isinstance(ps, dict):
                                ps["owner_team_key"] = to_team_key
                                n_pick_updates += 1
                            else:
                                setattr(ps, "owner_team_key", to_team_key)
                                n_pick_updates += 1

                    _refresh_contract_cache_into_session_state()
                    save_autosave(state)

                    st.success(
                        f"Trade saved to DB. trade_id={trade_id} assets={n_assets} "
                        f"contract_updates={n_contract_updates} pick_updates={n_pick_updates} "
                        f"matched_picks={matched_pick_ids} missing_picks={missing_pick_ids}"
                    )

                    # Reset builder after successful write
                    st.session_state["trade_builder_v1"] = {
                        "left":  {"team_key": "", "players": [], "picks": []},
                        "right": {"team_key": "", "players": [], "picks": []},
                        "finalize": False,
                    }
                    st.rerun()

                except Exception as e:
                    st.error(f"Submit failed: {e}")

    # -----------------------
    if not is_milf:
        # Qualifying Offers
        # -----------------------
        with st.expander("Qualifying Offers", expanded=False):
            # (Your existing QO block unchanged)
            st.subheader("Qualifying Offers (Predraft)")

            try:
                dsn = _get_dsn()
            except Exception as e:
                st.error(str(e))
                dsn = ""

            league_key = _get_league_key()
            season_year = _get_season_year()

            def _load_team_qos(dsn: str, league_key: str, season_year: int, team_key: str) -> dict[int, str]:
                out: dict[int, str] = {}
                if not dsn:
                    return out
                try:
                    with psycopg.connect(dsn) as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                SELECT qo_level, yahoo_player_key
                                FROM public.qualifying_offer
                                WHERE league_key=%s AND season_year=%s AND team_key=%s
                                ORDER BY qo_level;
                                """,
                                (league_key, season_year, team_key),
                            )
                            for lvl, pkey in cur.fetchall():
                                if lvl is not None and pkey:
                                    out[int(lvl)] = str(pkey)
                except Exception as e:
                    st.warning(f"Could not load existing QOs for team: {e}")
                return out

            if dsn:
                st.caption(f"League: {league_key} • Season: {season_year}")

                team_keys = sorted([t.team_key for t in state.teams.values()])
                team_key = st.selectbox(
                    "Team",
                    options=team_keys,
                    key="qo_team_select",
                    format_func=lambda k: state.teams[k].name if k in state.teams else k,
                )

                contracted_keys = (
                    getattr(state, "contracted_player_keys_2026", None)
                    or getattr(state, "contracted_keys_2026", None)
                    or getattr(state, "contracted_keys", None)
                    or set()
                )

                candidate_keys = [pk for pk in state.players.keys() if pk not in contracted_keys]
                candidate_keys.sort(
                    key=lambda pk: (
                        getattr(state.players[pk], "rank_value", None) is None,
                        getattr(state.players[pk], "rank_value", None) if getattr(state.players[pk], "rank_value", None) is not None else 999999,
                        state.players[pk].name or "",
                        pk,
                    )
                )

                def _pos_label(pos) -> str:
                    try:
                        v = pos.value
                    except Exception:
                        v = str(pos)
                    if v == "1B":
                        return "1B"
                    if v == "2B":
                        return "2B"
                    if v == "3B":
                        return "3B"
                    return v

                def _player_label(pk: str) -> str:
                    p = state.players.get(pk)
                    if not p:
                        return ""
                    tm = getattr(p, "mlb_team", "") or ""
                    pos = "/".join([_pos_label(x) for x in getattr(p, "positions", [])]) if getattr(p, "positions", None) else ""
                    if tm and pos:
                        return f"{p.name} — {tm} — {pos}"
                    if tm:
                        return f"{p.name} — {tm}"
                    if pos:
                        return f"{p.name} — {pos}"
                    return p.name

                existing = _load_team_qos(dsn, league_key, season_year, team_key)

                st.write(f"Select QO1–QO{get_active_qo_rounds()} using searchable dropdowns (type to search).")

                selected: dict[int, str] = {}
                for lvl in range(1, get_active_qo_rounds() + 1):
                    default_pk = existing.get(lvl)
                    opts = [""] + candidate_keys
                    idx = 0
                    if default_pk and default_pk in candidate_keys:
                        idx = opts.index(default_pk)

                    pk = st.selectbox(
                        f"QO{lvl}",
                        options=opts,
                        index=idx,
                        key=f"qo_{team_key}_{lvl}_player",
                        format_func=lambda x: "" if x == "" else _player_label(x),
                    )
                    if pk:
                        selected[lvl] = pk

                c1, c2, c3 = st.columns([1, 1, 2])

                with c1:
                    if st.button("Save Predraft QOs for Team", type="primary", key=f"qos_apply_btn_{team_key}"):
                        errs: list[str] = []

                        missing = [lvl for lvl in range(1, get_active_qo_rounds() + 1) if lvl not in selected]
                        if missing:
                            errs.append("Missing: " + ", ".join([f"QO{x}" for x in missing]))

                        seen = set()
                        dups = []
                        for _lvl, _pk in selected.items():
                            if _pk in seen:
                                dups.append(_pk)
                            seen.add(_pk)
                        if dups:
                            errs.append("Duplicate player selected across levels (must be unique).")

                        if errs:
                            for e in errs:
                                st.error(e)
                        else:
                            to_save = [(selected[lvl], lvl, "") for lvl in range(1, get_active_qo_rounds() + 1)]
                            ok = _upsert_team_predraft_qos(
                                dsn, league_key, season_year, team_key, to_save, created_by="commissioner"
                            )
                            st.success(f"Saved {ok} QO rows for {state.teams[team_key].name}.")
                            st.rerun()

                with c2:
                    if st.button("Reload from DB", key=f"qos_reload_btn_{team_key}"):
                        st.rerun()

                with c3:
                    if st.button("Show saved QOs for Team", key=f"qos_show_btn_{team_key}"):
                        with psycopg.connect(dsn) as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    SELECT qo_level, yahoo_player_key
                                    FROM public.qualifying_offer
                                    WHERE league_key=%s AND season_year=%s AND team_key=%s
                                    ORDER BY qo_level;
                                    """,
                                    (league_key, season_year, team_key),
                                )
                                rows = cur.fetchall()
                        st.write(rows if rows else "No QOs saved for this team yet.")

    # -----------------------
    if prospect_tags_enabled:
        # Prospect Tags
        # -----------------------
        with st.expander("Prospect Tags", expanded=False):
            # (Your existing PT block unchanged)
            st.subheader("Prospect Tags (PT)")

            try:
                dsn = _get_dsn()
            except Exception as e:
                st.error(str(e))
                dsn = ""

            league_key = _get_league_key()
            season_year = _get_season_year()

            if not hasattr(state, "pt_player_team_map") or state.pt_player_team_map is None:
                state.pt_player_team_map = {}

            try:
                if dsn:
                    state.pt_player_team_map = _load_pt_map(dsn, league_key, season_year)
            except Exception as e:
                st.error(f"PT load failed: {e}")

            team_keys = sorted([t.team_key for t in state.teams.values()])
            pt_team_key = st.selectbox(
                "Team (PT)",
                options=team_keys,
                format_func=lambda k: state.teams[k].name if k in state.teams else k,
                key="pt_team_select",
            )

            current_pt_player_key = ""
            for _pk, _tk in state.pt_player_team_map.items():
                if str(_tk) == str(pt_team_key):
                    current_pt_player_key = str(_pk)
                    break

            if current_pt_player_key:
                st.caption(f"Current PT: {current_pt_player_key}")

            contracted_keys = st.session_state.get("contracted_keys", set()) or set()

            def _pt_eligible(p) -> bool:
                # Deterministic PT eligibility:
                # - Exclude contracted/PT players (contracted_keys is the canonical cache)
                # - Exclude QO-eligible players
                # Everything else is commissioner judgment (do not block).
                if p.player_key in contracted_keys:
                    return False
                if getattr(p, "is_qo_eligible", False):
                    return False
                return True

            pt_candidates = [pk for pk, p in state.players.items() if _pt_eligible(p)]
            pt_candidates.sort(
                key=lambda pk: (
                    getattr(state.players[pk], "rank_value", None) is None,
                    getattr(state.players[pk], "rank_value", None) if getattr(state.players[pk], "rank_value", None) is not None else 999999,
                    state.players[pk].name or "",
                    pk,
                )
            )

            def _pt_label(pk: str) -> str:
                if pk == "":
                    return ""
                p = state.players.get(pk)
                if not p:
                    return pk
                name = p.name
                team = getattr(p, "mlb_team", "")
                pos = "/".join([x.value for x in getattr(p, "positions", [])]) if getattr(p, "positions", None) else ""
                bits = [name]
                if team:
                    bits.append(team)
                if pos:
                    bits.append(pos)
                return " — ".join(bits)

            pt_player_key = st.selectbox(
                "Select PT player",
                options=[""] + pt_candidates,
                format_func=_pt_label,
                key="pt_player_select",
            )

            # Non-blocking confirmation preview (commissioner judgment)
            if pt_player_key:
                p = state.players.get(pt_player_key)
                if p:
                    st.table([{
                        "Player": p.name,
                        "Team": getattr(p, "mlb_team", ""),
                        "Pos": "/".join([x.value for x in getattr(p, "positions", [])]) if getattr(p, "positions", None) else "",
                        "Rank": getattr(p, "rank_value", None),
                        "AB": getattr(p, "h_ab", None),
                        "IP": getattr(p, "ip", None),
                        "QO?": getattr(p, "is_qo_eligible", False),
                        "%Owned": getattr(p, "percent_owned", None),
                    }])

            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                if st.button("Add / Update PT", type="primary", key="pt_add_btn", disabled=(pt_player_key == "")):
                    if dsn and current_pt_player_key:
                        _delete_pt_player(dsn, league_key, season_year, current_pt_player_key)
                    if dsn:
                        _upsert_pt_player(dsn, league_key, season_year, pt_team_key, pt_player_key)
                        state.pt_player_team_map = _load_pt_map(dsn, league_key, season_year)
                    st.success("PT saved.")
                    save_autosave(state)
                    st.rerun()

            with c2:
                if st.button("Remove PT", key="pt_remove_btn", disabled=(current_pt_player_key == "")):
                    if dsn and current_pt_player_key:
                        _delete_pt_player(dsn, league_key, season_year, current_pt_player_key)
                        state.pt_player_team_map = _load_pt_map(dsn, league_key, season_year)
                    st.success("PT removed.")
                    save_autosave(state)
                    st.rerun()

            with c3:
                team_pts = [pk for pk, tk in state.pt_player_team_map.items() if tk == pt_team_key]
                team_pts = [pk for pk in team_pts if pk in state.players]
                team_pts.sort(key=lambda pk: state.players[pk].name)
                st.caption(f"PT players on {state.teams[pt_team_key].name}: {len(team_pts)}")
                if team_pts:
                    st.table([{"Player": _pt_label(pk)} for pk in team_pts])

    # -----------------------
    if not is_milf:
        # Contract Overrides
        # -----------------------
        if show_contract_overrides:
            with st.expander("Contracts (Overrides)", expanded=False):
                st.subheader("Contracts (Overrides)")
                st.caption("These overrides patch contract truth without rebuilding transaction analysis. Years=0 means NOT under contract.")

                try:
                    dsn = _get_dsn()
                except Exception as e:
                    st.error(str(e))
                    dsn = ""

                league_key = _get_league_key()
                season_year = _get_season_year()

                if not dsn:
                    st.stop()

                team_keys = sorted([t.team_key for t in state.teams.values()])

                def _team_name(k: str) -> str:
                    return state.teams[k].name if k in state.teams else k

                # ---------------------------
                # Build "who owns this contract/PT" labels for UI + void inference
                # ---------------------------
                contracted_keys = set(st.session_state.get("contracted_keys", set()) or set())
                contract_rows = list(st.session_state.get("contract_rows", []) or [])
                pt_map = dict(getattr(state, "pt_player_team_map", {}) or {})

                # player_key -> display owner label (DraftBoard team name for PT; yahoo_team_name for contracts)
                pkey_to_owner_label: dict[str, str] = {}

                # PT first (wins)
                for pk, tkey in pt_map.items():
                    pk = str(pk)
                    tkey = str(tkey)
                    nm = _team_name(tkey)
                    pkey_to_owner_label[pk] = f"{nm} (PT)"

                # Contracts next (only if PT didn't already set it)
                for row in contract_rows:
                    pk = str(row.get("yahoo_player_key") or "")
                    if not pk:
                        continue
                    if pk in pkey_to_owner_label:
                        continue
                    yn = str(row.get("yahoo_team_name") or "").strip()
                    if yn:
                        pkey_to_owner_label[pk] = yn

                # Player picker (searchable)
                all_player_keys = sorted(
                    state.players.keys(),
                    key=lambda pk: (
                        getattr(state.players[pk], "rank_value", None) is None,
                        getattr(state.players[pk], "rank_value", None) if getattr(state.players[pk], "rank_value", None) is not None else 999999,
                        state.players[pk].name or "",
                        pk,
                    ),
                )

                def _player_label(pk: str) -> str:
                    p = state.players.get(pk)
                    if not p:
                        return pk
                    owner = pkey_to_owner_label.get(pk, "")
                    tm = getattr(p, "mlb_team", "") or ""
                    pos = "/".join([x.value for x in getattr(p, "positions", [])]) if getattr(p, "positions", None) else ""

                    bits = [p.name]
                    if owner:
                        bits.append(owner)
                    if tm:
                        bits.append(tm)
                    if pos:
                        bits.append(pos)
                    return " — ".join(bits)

                # Read mode FIRST (so the Player dropdown can depend on it)
                mode = st.radio(
                    "Contract action",
                    options=["Set contract", "Void contract (years=0)"],
                    horizontal=True,
                    key="contract_override_mode",
                )

                # Player options depend on mode:
                # - Set contract: all players
                # - Void: ONLY currently contracted (including PT, because contracted_keys already unions PT)
                if mode == "Void contract (years=0)":
                    player_options = sorted(
                        contracted_keys,
                        key=lambda pk: (
                            getattr(state.players.get(pk), "rank_value", None) is None,
                            getattr(state.players.get(pk), "rank_value", None) if getattr(state.players.get(pk), "rank_value", None) is not None else 999999,
                            getattr(state.players.get(pk), "name", "") or "",
                            pk,
                        ),
                    )
                else:
                    player_options = all_player_keys

                override_player_key = st.selectbox(
                    "Player",
                    options=player_options,
                    format_func=_player_label,
                    index=None,
                    placeholder="Start typing a player name…",
                    key="contract_override_player",
                )

                years = 0
                team_key = ""
                note = ""

                if mode == "Set contract":
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        years = st.number_input(
                            "Years remaining",
                            min_value=1,
                            max_value=10,
                            value=1,
                            step=1,
                            key="contract_override_years",
                        )
                    with c2:
                        team_key = st.selectbox(
                            "Assign contract to DraftBoard team",
                            options=team_keys,
                            format_func=_team_name,
                            key="contract_override_team",
                        )
                    note = st.text_input("Note (optional)", value="", key="contract_override_note")
                else:
                    years = 0
                    note = st.text_input("Note (optional)", value="voided", key="contract_override_note_void")

                    # In void mode, capture inferred owner for audit/debugging (helps validate what you're voiding)
                    # If PT: store DraftBoard team name. If contract row: store yahoo_team_name.
                    inferred_owner = ""
                    if override_player_key:
                        inferred_owner = pkey_to_owner_label.get(str(override_player_key), "")
                    # We don't force a DraftBoard team_key here because voiding semantics are years=0,
                    # but we DO store a useful yahoo_team_name for visibility.
                    team_key = ""  # keep blank; years=0 is the signal

                # Map DraftBoard team selection -> contract row expects yahoo_team_name
                yahoo_team_name = _team_name(team_key) if team_key else ""
                if mode == "Void contract (years=0)" and override_player_key:
                    # If we inferred an owner label, store it (even if it's "Team (PT)" it's still better than blank)
                    _inf = pkey_to_owner_label.get(str(override_player_key), "")
                    yahoo_team_name = _inf or yahoo_team_name

                # Store DraftBoard team_key here (stable join key).
                # NOTE: DB column name is yahoo_team_key, but we repurpose it as draft_team_key for now.
                yahoo_team_key = str(team_key or "")

                c1, c2, c3 = st.columns([1, 1, 2])

                with c1:
                    if st.button("Save Contract Action", type="primary", key="contract_override_save"):
                        if override_player_key:
                            if mode == "Void contract (years=0)":
                                n = _void_contract_ssot(
                                    dsn=dsn,
                                    league_key=league_key,
                                    season_year=season_year,
                                    yahoo_player_key=override_player_key,
                                    note=note or "voided",
                                )
                                _refresh_contract_cache_into_session_state()
                                st.success(f"Contract voided in SSOT. rows_updated={n}. Contract cache refreshed.")
                            else:
                                n = _upsert_contract_ssot(
                                    dsn=dsn,
                                    league_key=league_key,
                                    season_year=season_year,
                                    yahoo_player_key=override_player_key,
                                    years_remaining=int(years),
                                    team_key=yahoo_team_key,
                                    note=note,
                                )
                                _refresh_contract_cache_into_session_state()
                                st.success(f"Contract saved to SSOT. rows_written={n}. Contract cache refreshed.")
                            st.rerun()
                        else:
                            st.error("Select a player first.")

                with c2:
                    if st.button("Delete Override", key="contract_override_delete"):
                        n = _delete_contract_override(dsn, league_key, season_year, override_player_key)
                        _refresh_contract_cache_into_session_state()
                        st.success(f"Deleted {n} override row(s). Contract cache refreshed.")
                        st.rerun()

                with c3:
                    if st.button("Refresh contract cache only", key="contract_override_refresh_cache"):
                        _refresh_contract_cache_into_session_state()
                        st.success("Contract cache refreshed.")
                        st.rerun()

                st.divider()
                st.subheader("Existing overrides")

                try:
                    rows = _load_contract_overrides(dsn, league_key, season_year)
                except Exception as e:
                    rows = []
                    st.error(f"Failed to load overrides: {e}")

                if not rows:
                    st.caption("No overrides saved yet.")
                else:
                    table = []
                    for r in rows:
                        pk = r["yahoo_player_key"]
                        nm = state.players[pk].name if pk in state.players else ""
                        table.append(
                            {
                                "Player": nm,
                                "yahoo_player_key": pk,
                                "Years": r["years_remaining"],
                                "Team": r["yahoo_team_name"],
                                "Note": r["note"],
                                "Updated": r["updated_at"],
                            }
                        )
                    st.table(table)

        # -----------------------
        # Draft Tools (your existing block)
        # -----------------------

    # -----------------------
    # NFFL Keeper / Roster Overrides
    # -----------------------
    with st.expander("NFFL Keeper / Roster Overrides", expanded=False):
        st.caption(
            "NFFL-native commissioner overrides. Writes to nffl.contract, "
            "nffl.offseason_keeper_decision, and nffl.franchise_tag_history."
        )

        try:
            dsn = _get_dsn()
            league_key = _get_league_key()
            season_year = _get_season_year()
        except Exception as e:
            st.error(str(e))
            dsn = ""

        if not dsn:
            st.warning("No Postgres DSN available.")
        else:
            team_keys = sorted([t.team_key for t in state.teams.values()])

            def _nffl_team_label(k: str) -> str:
                return state.teams[k].name if k in state.teams else str(k)

            player_keys = sorted(
                list(state.players.keys()),
                key=lambda pk: (
                    getattr(state.players[pk], "rank_value", None) is None,
                    getattr(state.players[pk], "rank_value", None) if getattr(state.players[pk], "rank_value", None) is not None else 999999,
                    getattr(state.players[pk], "name", "") or "",
                    pk,
                ),
            )

            def _nffl_pos_label(p) -> str:
                vals = []
                for x in getattr(p, "positions", []) or []:
                    try:
                        vals.append(str(x.value))
                    except Exception:
                        vals.append(str(x))
                return "/".join([v for v in vals if v])

            def _nffl_player_label(pk: str) -> str:
                if not pk:
                    return ""
                p = state.players.get(pk)
                if not p:
                    return str(pk)
                bits = [str(getattr(p, "name", "") or pk)]
                nfl_team = (
                    getattr(p, "nfl_team", "")
                    or getattr(p, "nfl_team_abbr", "")
                    or getattr(p, "mlb_team", "")
                    or ""
                )
                pos = _nffl_pos_label(p)
                if nfl_team:
                    bits.append(str(nfl_team))
                if pos:
                    bits.append(pos)
                bits.append(str(pk))
                return " — ".join(bits)

            action = st.radio(
                "Override action",
                options=[
                    "Set / update active contract",
                    "Void contract",
                    "Set Franchise Tag",
                    "Clear Franchise Tag",
                ],
                horizontal=False,
                key="nffl_keeper_override_action",
            )

            st.caption(
                "Choose the override action above, make all branch changes below, then submit once."
            )

            if action == "Set / update active contract":
                with st.form("nffl_contract_override_form", clear_on_submit=False):
                    note = st.text_input(
                        "Commissioner note",
                        value="commissioner override",
                        key="nffl_contract_override_note_form",
                    )

                    c1, c2, c3 = st.columns([2, 2, 1])
                    with c1:
                        contract_player_key = st.selectbox(
                            "Player",
                            options=[""] + player_keys,
                            format_func=_nffl_player_label,
                            key="nffl_contract_override_player_form",
                        )
                    with c2:
                        contract_team_key = st.selectbox(
                            "Assign to team",
                            options=team_keys,
                            format_func=_nffl_team_label,
                            key="nffl_contract_override_team_form",
                        )
                    with c3:
                        contract_years = st.number_input(
                            "Years",
                            min_value=1,
                            max_value=4,
                            value=1,
                            step=1,
                            key="nffl_contract_override_years_form",
                        )

                    confirm = st.checkbox(
                        "Confirm contract override",
                        value=False,
                        key="nffl_contract_override_confirm_form",
                    )

                    submitted = st.form_submit_button(
                        "Save NFFL Contract Override",
                        type="primary",
                    )

                if submitted:
                    if not confirm:
                        st.warning("Confirm contract override before saving.")
                    elif not contract_player_key:
                        st.warning("Select a player before saving.")
                    else:
                        try:
                            result = _nffl_set_active_contract(
                                dsn=dsn,
                                league_key=league_key,
                                season_year=season_year,
                                team_key=contract_team_key,
                                yahoo_player_key=contract_player_key,
                                years_remaining=int(contract_years),
                                note=note,
                            )
                            _refresh_contract_cache_into_session_state()
                            st.success(f"NFFL contract {result}: {_nffl_player_label(contract_player_key)}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Contract override failed: {e}")

            elif action == "Void contract":
                with st.form("nffl_contract_void_form", clear_on_submit=False):
                    note = st.text_input(
                        "Commissioner note",
                        value="commissioner override",
                        key="nffl_contract_void_note_form",
                    )

                    contract_player_key = st.selectbox(
                        "Player to void",
                        options=[""] + player_keys,
                        format_func=_nffl_player_label,
                        key="nffl_contract_void_player_form",
                    )

                    confirm = st.checkbox(
                        "Confirm contract void",
                        value=False,
                        key="nffl_contract_void_confirm_form",
                    )

                    submitted = st.form_submit_button(
                        "Void NFFL Contract",
                        type="primary",
                    )

                if submitted:
                    if not confirm:
                        st.warning("Confirm contract void before saving.")
                    elif not contract_player_key:
                        st.warning("Select a player before saving.")
                    else:
                        try:
                            rows = _nffl_void_contract(
                                dsn=dsn,
                                league_key=league_key,
                                season_year=season_year,
                                yahoo_player_key=contract_player_key,
                                note=note or "voided by commissioner",
                            )
                            _refresh_contract_cache_into_session_state()
                            st.success(f"NFFL contract void rows_updated={rows}: {_nffl_player_label(contract_player_key)}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Contract void failed: {e}")

            elif action == "Set Franchise Tag":
                with st.form("nffl_ft_override_form", clear_on_submit=False):
                    note = st.text_input(
                        "Commissioner note",
                        value="commissioner override",
                        key="nffl_ft_override_note_form",
                    )

                    c1, c2 = st.columns([2, 2])
                    with c1:
                        ft_team_key = st.selectbox(
                            "Franchise Tag team",
                            options=team_keys,
                            format_func=_nffl_team_label,
                            key="nffl_ft_override_team_form",
                        )
                    with c2:
                        ft_player_key = st.selectbox(
                            "Franchise Tag player",
                            options=[""] + player_keys,
                            format_func=_nffl_player_label,
                            key="nffl_ft_override_player_form",
                        )

                    confirm = st.checkbox(
                        "Confirm Franchise Tag override",
                        value=False,
                        key="nffl_ft_override_confirm_form",
                    )

                    submitted = st.form_submit_button(
                        "Set Franchise Tag",
                        type="primary",
                    )

                if submitted:
                    if not confirm:
                        st.warning("Confirm Franchise Tag override before saving.")
                    elif not ft_player_key:
                        st.warning("Select a Franchise Tag player before saving.")
                    else:
                        try:
                            result = _nffl_set_franchise_tag(
                                dsn=dsn,
                                league_key=league_key,
                                season_year=season_year,
                                team_key=ft_team_key,
                                yahoo_player_key=ft_player_key,
                                note=note,
                            )
                            _refresh_contract_cache_into_session_state()
                            st.success(f"Franchise Tag {result}: {_nffl_team_label(ft_team_key)} — {_nffl_player_label(ft_player_key)}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Franchise Tag override failed: {e}")

            elif action == "Clear Franchise Tag":
                with st.form("nffl_ft_clear_form", clear_on_submit=False):
                    note = st.text_input(
                        "Commissioner note",
                        value="commissioner override",
                        key="nffl_ft_clear_note_form",
                    )

                    ft_team_key = st.selectbox(
                        "Team to clear FT for",
                        options=team_keys,
                        format_func=_nffl_team_label,
                        key="nffl_ft_clear_team_form",
                    )

                    confirm = st.checkbox(
                        "Confirm clear Franchise Tag",
                        value=False,
                        key="nffl_ft_clear_confirm_form",
                    )

                    submitted = st.form_submit_button(
                        "Clear Franchise Tag",
                        type="primary",
                    )

                if submitted:
                    if not confirm:
                        st.warning("Confirm clear Franchise Tag before saving.")
                    else:
                        try:
                            rows = _nffl_clear_franchise_tag(
                                dsn=dsn,
                                league_key=league_key,
                                season_year=season_year,
                                team_key=ft_team_key,
                                note=note or "cleared by commissioner",
                            )
                            _refresh_contract_cache_into_session_state()
                            st.success(f"Franchise Tag cleared for {_nffl_team_label(ft_team_key)}. rows_changed={rows}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Clear Franchise Tag failed: {e}")


    # -----------------------
    # NFFL Pick Ownership Overrides
    # -----------------------
    with st.expander("NFFL Pick Ownership Overrides", expanded=False):
        st.caption(
            "Commissioner pick ownership correction. Writes to nffl.draft_pick only. "
            "Selected picks are blocked here; correct selected picks through Draft Tools first."
        )

        try:
            dsn = _get_dsn()
            league_key = _get_league_key()
            season_year = _get_season_year()
            draft_key = _get_draft_key()
        except Exception as e:
            st.error(str(e))
            dsn = ""

        if not dsn:
            st.warning("No Postgres DSN available.")
        else:
            try:
                pick_rows = _nffl_load_pick_ownership_rows(
                    dsn=dsn,
                    draft_key=draft_key,
                    league_key=league_key,
                    season_year=season_year,
                )
            except Exception as e:
                pick_rows = []
                st.error(f"Failed to load pick ownership rows: {e}")

            if not pick_rows:
                st.caption("No picks found for this draft.")
            else:
                team_keys = sorted([t.team_key for t in state.teams.values()])

                def _nffl_team_label(k: str) -> str:
                    return state.teams[k].name if k in state.teams else str(k)

                def _pick_label(row: dict) -> str:
                    selected = " | SELECTED" if bool(row.get("is_selected")) else ""
                    traded = " | traded" if bool(row.get("traded_flag")) else ""
                    return (
                        f"{row.get('pick_id')} | "
                        f"column={row.get('column_team_name')} | "
                        f"owner={row.get('current_owner_team_name')}"
                        f"{traded}{selected}"
                    )

                pick_ids = [str(r["pick_id"]) for r in pick_rows]
                row_by_pick_id = {str(r["pick_id"]): r for r in pick_rows}

                with st.form("nffl_pick_owner_override_form", clear_on_submit=False):
                    c1, c2 = st.columns([3, 2])
                    with c1:
                        pick_id = st.selectbox(
                            "Pick",
                            options=pick_ids,
                            format_func=lambda pid: _pick_label(row_by_pick_id[pid]),
                            key="nffl_pick_owner_override_pick",
                        )

                    selected_row = row_by_pick_id[pick_id]
                    current_owner = str(selected_row.get("current_owner_team_key") or "")
                    default_owner_idx = team_keys.index(current_owner) if current_owner in team_keys else 0

                    with c2:
                        new_owner_team_key = st.selectbox(
                            "New owner",
                            options=team_keys,
                            index=default_owner_idx,
                            format_func=_nffl_team_label,
                            key="nffl_pick_owner_override_new_owner",
                        )

                    note = st.text_input(
                        "Ownership note",
                        value="commissioner pick ownership override",
                        key="nffl_pick_owner_override_note",
                    )

                    is_selected = bool(selected_row.get("is_selected"))
                    if is_selected:
                        st.warning(
                            "This pick already has a draft selection. Ownership override is blocked here. "
                            "Use Draft Tools to correct the selection first."
                        )

                    confirm = st.checkbox(
                        "Confirm pick ownership override",
                        value=False,
                        key="nffl_pick_owner_override_confirm",
                    )

                    save_owner_clicked = st.form_submit_button(
                        "Save Pick Ownership Override",
                        type="primary",
                        disabled=bool(is_selected),
                    )

                if save_owner_clicked:
                    if not confirm:
                        st.warning("Confirm pick ownership override first.")
                        return
                    if not pick_id or not new_owner_team_key:
                        st.warning("Select a pick and new owner first.")
                        return

                    try:
                        result = _nffl_update_pick_owner(
                            dsn=dsn,
                            draft_key=draft_key,
                            league_key=league_key,
                            season_year=season_year,
                            pick_id=pick_id,
                            new_owner_team_key=new_owner_team_key,
                            note=note,
                        )
                        st.success(
                            "Pick ownership updated: "
                            f"{result['pick_id']} | "
                            f"{_nffl_team_label(result['old_owner_team_key'])} -> "
                            f"{_nffl_team_label(result['new_owner_team_key'])}"
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Pick ownership update failed: {e}")

        
    with st.expander("Draft Tools", expanded=False):
        # (keep your current Draft Tools section exactly as-is)
        st.subheader("Set Current Pick")
        current_pick_id = state.clock.current_pick_id
        current_idx = state.pick_order.index(current_pick_id) if current_pick_id in state.pick_order else 0
        new_pick = st.selectbox(
            "Commissioner: set current pick",
            options=state.pick_order,
            index=current_idx,
            key="current_pick_select_commissioner_tools",
        )
        if new_pick != state.clock.current_pick_id:
            set_current_pick(new_pick)
            save_autosave(state)
            st.success(f"Current pick set to {new_pick}.")
            st.rerun()
        st.divider()

        st.subheader("Draft Clock")

        status = compute_clock_status(
            is_running=state.clock.is_running,
            seconds_per_pick=state.clock.seconds_per_pick,
            started_ts_iso=state.clock.pick_started_ts_iso,
            paused_ts_iso=state.clock.pick_paused_ts_iso,
            elapsed_paused_seconds=int(getattr(state.clock, "elapsed_paused_seconds", 0) or 0),
        )

        remaining_hr = status.remaining_seconds // 3600
        remaining_min = (status.remaining_seconds % 3600) // 60
        st.write(f"**Clock:** {'RUNNING' if status.is_running else 'STOPPED'}")
        st.write(f"**Remaining:** {remaining_hr:02d}:{remaining_min:02d} (hh:mm)")

        can_start = (state.clock.pick_started_ts_iso is None) or (
            not status.is_running and state.clock.pick_paused_ts_iso is None
        )
        can_pause = status.is_running
        can_resume = (state.clock.pick_paused_ts_iso is not None)

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Start Draft", type="primary", key="clock_start", disabled=not can_start):
                _start_clock_if_needed(state)
                st.success("Clock started.")
        with c2:
            if st.button("Pause Clock", type="secondary", key="clock_pause", disabled=not can_pause):
                _pause_clock(state)
                st.success("Clock paused.")
        with c3:
            if st.button("Resume Clock", type="secondary", key="clock_resume", disabled=not can_resume):
                _resume_clock(state)
                st.success("Clock resumed.")

        st.write("")

        preset = st.radio(
            "Pick duration",
            options=["24 hours", "12 hours", "Custom"],
            index=0
            if state.clock.seconds_per_pick == 24 * 3600
            else (1 if state.clock.seconds_per_pick == 12 * 3600 else 2),
            horizontal=True,
            key="clock_duration_preset",
        )
        if preset == "Custom":
            hours = st.number_input(
                "Custom hours per pick",
                min_value=1,
                max_value=72,
                value=max(1, int(state.clock.seconds_per_pick // 3600)),
                step=1,
                key="clock_custom_hours",
            )
            new_seconds = int(hours) * 3600
        elif preset == "12 hours":
            new_seconds = 12 * 3600
        else:
            new_seconds = 24 * 3600

        if int(new_seconds) != int(state.clock.seconds_per_pick):
            state.clock.seconds_per_pick = int(new_seconds)
            save_autosave(state)
            st.info("Pick duration updated.")

        weekends = st.toggle(
            "Count weekends (Sat/Sun) toward the clock",
            value=bool(state.clock.weekends_count),
            key="clock_weekends_count",
        )
        if bool(weekends) != bool(state.clock.weekends_count):
            state.clock.weekends_count = bool(weekends)
            save_autosave(state)
            st.info("Weekend rule updated.")

        st.divider()

        st.subheader("Fix a Mistake")

        picked = [
            (pid, int(p.round_number), int(p.slot))
            for pid, p in state.picks.items()
            if (p.selected_player_key is not None and p.selected_ts_iso is not None)
        ]
        picked.sort(key=lambda t: (t[1], t[2]))
        picked_ids = [pid for pid, _rnd, _slot in picked]
        if not picked_ids:
            st.caption("No picks to delete yet.")
        else:
            with st.form("delete_pick_form", clear_on_submit=False):
                pick_id = st.selectbox(
                    "Pick to delete",
                    options=picked_ids,
                    help="Clears the selected player from a pick slot.",
                    key="delete_pick_select",
                )

                delete_mode = st.radio(
                    "Delete behavior",
                    options=[
                        "Delete pick + rewind clock to this pick",
                        "Delete pick (do not change clock)",
                    ],
                    index=0,
                    key="delete_pick_mode",
                )

                confirm = st.checkbox(
                    "I understand this will clear the pick.",
                    key="delete_pick_confirm",
                )

                delete_clicked = st.form_submit_button("DELETE PICK", type="primary")

            if delete_clicked:
                if not confirm:
                    st.warning("Confirm that you understand this will clear the pick first.")
                    return

                rewind = delete_mode.startswith("Delete pick + rewind")
                db_rows_deleted = delete_pick(state, pick_id, rewind_clock=rewind)
                if rewind:
                    st.success(f"Deleted {pick_id}. DB rows deleted={db_rows_deleted}. Clock rewound to {pick_id}.")
                else:
                    st.success(f"Deleted {pick_id}. DB rows deleted={db_rows_deleted}. Clock unchanged.")
                st.rerun()

# -----------------------
# Danger Zone (your existing block)
# -----------------------
    with st.expander("Danger Zone", expanded=False):
        with st.form("reset_draft_form", clear_on_submit=False):
            reset_confirm = st.checkbox(
                "I understand this will wipe ALL picks and reset the draft.",
                key="reset_draft_confirm",
            )
            reset_clicked = st.form_submit_button("RESET DRAFT (wipe all picks)", type="secondary")

        if reset_clicked:
            if not reset_confirm:
                st.warning("Confirm that you understand this will wipe all picks first.")
                return

            db_rows_deleted = reset_draft_state(state)
            st.success(f"Draft reset. DB rows deleted={db_rows_deleted}. All picks cleared and clock reset to first pick.")
            st.rerun()
