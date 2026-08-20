from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import dict_row
import streamlit as st

from draftboard.domain.nffl_post_draft_contract_rules import (
    eligible_drafted_player_keys,
    validate_contract_selections,
)
from draftboard.ui.components.nffl_post_draft_contract_tool import (
    render_post_draft_contract_tool,
)


BASE_COLUMNS = ["Player", "Team", "Bye", "% Ros", "Position", "Contract", "QO Eligible", "FT Eligible", "Fan Pts"]

POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF", "Other"]
DECISION_TYPES = ["QO1", "QO2", "QO3", "QO4", "FT"]

STAT_SPECS = {
    "QB": [
        ("Pass Yds", [], "4"),
        ("Pass TD", [], "5"),
        ("Int", [], "6"),
        ("Sack", [], "7"),
        ("Pick Six", [], "58"),
        ("40 Yd Cmp", [], "59"),
        ("Pass 1st Downs", [], "79"),
        ("Rush Att", [], "8"),
        ("Rush Yds", [], "9"),
        ("Rush TD", [], "10"),
        ("40 Yd Rush", [], "61"),
        ("Rush 1st Downs", [], "81"),
        ("Tgt", [], "78"),
        ("2PT", [], "16"),
        ("Fum", [], "17"),
        ("Lost", [], "18"),
    ],
    "RB": [
        ("Rush Att", [], "8"),
        ("Rush Yds", [], "9"),
        ("Rush TD", [], "10"),
        ("40 Yd Rush", [], "61"),
        ("Rush 1st Downs", [], "81"),
        ("Tgt", [], "78"),
        ("Rec", [], "11"),
        ("Rec Yds", [], "12"),
        ("Rec TD", [], "13"),
        ("40 Yd Rec", [], "63"),
        ("Rec 1st Downs", [], "80"),
        ("Ret TD", [], "15"),
        ("2PT", [], "16"),
        ("Fum", [], "17"),
        ("Lost", [], "18"),
    ],
    "WR": [
        ("Tgt", [], "78"),
        ("Rec", [], "11"),
        ("Rec Yds", [], "12"),
        ("Rec TD", [], "13"),
        ("40 Yd Rec", [], "63"),
        ("Rec 1st Downs", [], "80"),
        ("Rush Att", [], "8"),
        ("Rush Yds", [], "9"),
        ("Rush TD", [], "10"),
        ("40 Yd Rush", [], "61"),
        ("Rush 1st Downs", [], "81"),
        ("Ret TD", [], "15"),
        ("2PT", [], "16"),
        ("Fum", [], "17"),
        ("Lost", [], "18"),
    ],
    "TE": [
        ("Tgt", [], "78"),
        ("Rec", [], "11"),
        ("Rec Yds", [], "12"),
        ("Rec TD", [], "13"),
        ("40 Yd Rec", [], "63"),
        ("Rec 1st Downs", [], "80"),
        ("Rush Att", [], "8"),
        ("Rush Yds", [], "9"),
        ("Rush TD", [], "10"),
        ("40 Yd Rush", [], "61"),
        ("Rush 1st Downs", [], "81"),
        ("Ret TD", [], "15"),
        ("2PT", [], "16"),
        ("Fum", [], "17"),
        ("Lost", [], "18"),
    ],
    "K": [
        ("PAT Made", [], "29"),
        ("FG Made", [], "85"),
        ("FG Yds", [], "84"),
        ("FG Miss", [], "86"),
        ("PAT Miss", [], "30"),
    ],
    "DEF": [
        ("Pts vs.", [], "31"),
        ("Sack", [], "32"),
        ("Safe", [], "36"),
        ("Int", [], "33"),
        ("Fum Rec", [], "34"),
        ("TD", [], "35"),
        ("Blk Kick", [], "37"),
        ("4 Dwn Stops", [], "67"),
        ("Ret TD", [], "49"),
        ("Def Yds Allow", [], "69"),
    ],
    "Other": [
        ("GP", [], "0"),
    ],
}


def _fetch_rows(
    dsn: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, str],
    dict[str, dict[str, str]],
    dict[str, dict[str, Any]],
]:
    workbench_sql = """
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
        )
        SELECT
            w.league_key,
            w.season_year,
            w.team_key,
            w.team_name,
            w.owner_name,
            w.yahoo_player_key,
            w.player_name,
            w.nfl_team_abbr,
            w.eligible_positions,
            w.percent_rostered,
            w.bye_week,
            w.contract_years_remaining,
            w.is_active_contract,
            w.was_franchise_tagged_prior_season,
            w.can_select_qo,
            w.can_select_ft,
            w.manager_action_status,
            coalesce(s.stats_json, '{}'::jsonb) as stats_json,
            fp.fan_points_2025
        FROM nffl.v_team_offseason_qo_ft_workbench w
        LEFT JOIN snap ON true
        LEFT JOIN nffl.roster_snapshot_player rsp
          ON rsp.snapshot_id = snap.snapshot_id
         AND rsp.league_key = w.league_key
         AND rsp.season_year = w.season_year
         AND rsp.team_key = w.team_key
         AND rsp.yahoo_player_key = w.yahoo_player_key
        LEFT JOIN nffl.roster_snapshot_player_stats s
          ON s.snapshot_id = rsp.snapshot_id
         AND s.team_key = rsp.team_key
         AND s.yahoo_player_key = rsp.yahoo_player_key
        LEFT JOIN nffl.v_roster_snapshot_player_fantasy_points fp
          ON fp.snapshot_id = rsp.snapshot_id
         AND fp.team_key = rsp.team_key
         AND fp.yahoo_player_key = rsp.yahoo_player_key
        ORDER BY
            w.team_name,
            w.player_name;
    """

    math_sql = """
        WITH ctx AS (
            SELECT
                current_league_key AS league_key,
                current_season_year AS season_year
            FROM nffl.v_active_season_context
            LIMIT 1
        ),
        rules AS (
            SELECT
                r.league_key,
                r.season_year,
                r.roster_size,
                r.draft_rounds_total,
                r.qo_rounds,
                r.first_standard_round,
                r.max_ft_per_team
            FROM nffl.league_roster_rule r
            JOIN ctx
              ON ctx.league_key = r.league_key
             AND ctx.season_year = r.season_year
        ),
        team_base AS (
            SELECT
                t.league_key,
                t.season_year,
                t.team_key,
                t.team_name,
                t.owner_name
            FROM nffl.team t
            JOIN ctx
              ON ctx.league_key = t.league_key
             AND ctx.season_year = t.season_year
        ),
        visible_counts AS (
            SELECT
                w.league_key,
                w.season_year,
                w.team_key,
                count(*) AS visible_eligible_players,
                count(*) FILTER (WHERE w.is_active_contract) AS active_contracts
            FROM nffl.v_team_offseason_qo_ft_workbench w
            JOIN ctx
              ON ctx.league_key = w.league_key
             AND ctx.season_year = w.season_year
            GROUP BY
                w.league_key,
                w.season_year,
                w.team_key
        ),
        decision_counts AS (
            SELECT
                d.league_key,
                d.season_year,
                d.team_key,
                count(*) FILTER (WHERE d.decision_type IN ('QO1', 'QO2', 'QO3', 'QO4')) AS selected_qos,
                count(*) FILTER (WHERE d.decision_type = 'FT') AS selected_ft
            FROM nffl.offseason_keeper_decision d
            JOIN ctx
              ON ctx.league_key = d.league_key
             AND ctx.season_year = d.season_year
            WHERE d.decision_type IN ('QO1', 'QO2', 'QO3', 'QO4', 'FT')
            GROUP BY
                d.league_key,
                d.season_year,
                d.team_key
        ),
        math AS (
            SELECT
                tb.team_key,
                tb.team_name,
                tb.owner_name,
                r.roster_size,
                r.draft_rounds_total,
                r.qo_rounds,
                r.first_standard_round,
                COALESCE(vc.active_contracts, 0) AS active_contracts,
                COALESCE(dc.selected_qos, 0) AS selected_qos,
                COALESCE(dc.selected_ft, 0) AS selected_ft,
                (
                    COALESCE(vc.active_contracts, 0)
                    + COALESCE(dc.selected_qos, 0)
                    + COALESCE(dc.selected_ft, 0)
                ) AS controlled_roster_slots,
                (
                    r.roster_size
                    - COALESCE(vc.active_contracts, 0)
                    - COALESCE(dc.selected_qos, 0)
                    - COALESCE(dc.selected_ft, 0)
                ) AS open_draft_slots_after_keeper_decisions,
                (
                    r.roster_size
                    - COALESCE(vc.active_contracts, 0)
                    - r.qo_rounds
                    - COALESCE(dc.selected_ft, 0)
                ) AS standard_open_slots_if_all_qos_used,
                (
                    COALESCE(vc.active_contracts, 0)
                    + COALESCE(dc.selected_qos, 0)
                    + COALESCE(dc.selected_ft, 0)
                ) <= r.roster_size
                AND COALESCE(dc.selected_qos, 0) <= r.qo_rounds
                AND COALESCE(dc.selected_ft, 0) <= r.max_ft_per_team AS roster_math_valid,
                COALESCE(vc.visible_eligible_players, 0) AS visible_eligible_players,
                tb.league_key,
                tb.season_year
            FROM team_base tb
            CROSS JOIN rules r
            LEFT JOIN visible_counts vc
              ON vc.league_key = tb.league_key
             AND vc.season_year = tb.season_year
             AND vc.team_key = tb.team_key
            LEFT JOIN decision_counts dc
              ON dc.league_key = tb.league_key
             AND dc.season_year = tb.season_year
             AND dc.team_key = tb.team_key
        )
        SELECT
            team_key,
            team_name,
            owner_name,
            roster_size,
            draft_rounds_total,
            qo_rounds,
            first_standard_round,
            active_contracts,
            selected_qos,
            selected_ft,
            controlled_roster_slots,
            open_draft_slots_after_keeper_decisions,
            standard_open_slots_if_all_qos_used,
            roster_math_valid,
            visible_eligible_players,
            league_key,
            season_year
        FROM math
        ORDER BY team_name;
    """

    stat_meta_sql = """
        SELECT
            sc.stat_id,
            COALESCE(NULLIF(sc.display_name, ''), NULLIF(sc.name, ''), sc.stat_id) AS label
        FROM nffl.yahoo_stat_category sc
        JOIN nffl.v_active_season_context ctx
          ON sc.game_key = split_part(ctx.prior_league_key, '.l.', 1)
        ORDER BY COALESCE(sc.sort_order, 9999), sc.stat_id;
    """

    decisions_sql = """
        SELECT
            league_key,
            season_year,
            team_key,
            yahoo_player_key,
            decision_type,
            decision_status,
            revision_number
        FROM nffl.offseason_keeper_decision
        WHERE league_key = (SELECT current_league_key FROM nffl.v_active_season_context LIMIT 1)
          AND season_year = (SELECT current_season_year FROM nffl.v_active_season_context LIMIT 1)
          AND decision_type IN ('QO1', 'QO2', 'QO3', 'QO4', 'FT')
        ORDER BY team_key, decision_type;
    """

    submissions_sql = """
        SELECT
            league_key,
            season_year,
            team_key,
            submission_status,
            revision_number,
            reset_count,
            submitted_at_utc,
            locked_at_utc
        FROM nffl.offseason_team_submission
        WHERE league_key = (SELECT current_league_key FROM nffl.v_active_season_context LIMIT 1)
          AND season_year = (SELECT current_season_year FROM nffl.v_active_season_context LIMIT 1);
    """

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(workbench_sql)
            workbench = list(cur.fetchall())

            cur.execute(math_sql)
            math_rows = list(cur.fetchall())

            cur.execute(stat_meta_sql)
            stat_meta = {str(r["stat_id"]): str(r["label"] or "") for r in cur.fetchall()}

            cur.execute(decisions_sql)
            decision_rows = list(cur.fetchall())

            cur.execute(submissions_sql)
            submission_rows = list(cur.fetchall())

    decisions_by_team: dict[str, dict[str, str]] = defaultdict(dict)
    locked_ft_team_keys: set[str] = set()

    for r in decision_rows:
        team_key = str(r["team_key"])
        decision_type = str(r["decision_type"])
        decision_status = str(
            r["decision_status"] or ""
        ).upper()

        decisions_by_team[
            team_key
        ][decision_type] = str(
            r["yahoo_player_key"]
        )

        if (
            decision_type == "FT"
            and decision_status == "LOCKED"
        ):
            locked_ft_team_keys.add(team_key)

    submissions_by_team: dict[str, dict[str, Any]] = {}
    for r in submission_rows:
        submissions_by_team[str(r["team_key"])] = dict(r)

    return (
        workbench,
        math_rows,
        stat_meta,
        decisions_by_team,
        submissions_by_team,
        locked_ft_team_keys,
    )


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value)


def _position_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    text = str(value or "").replace("[", "").replace("]", "").replace('"', "")
    return [p.strip() for p in text.split(",") if p.strip()]


def _positions(value: Any) -> str:
    return ", ".join(_position_list(value))


def _position_label(value: Any) -> str:
    positions = _position_list(value)
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        if pos in positions:
            return pos
    for pos in positions:
        if pos != "W/R/T":
            return pos
    return ""


def _primary_position(row: dict[str, Any]) -> str:
    positions = _position_list(row.get("eligible_positions"))

    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        if pos in positions:
            return pos

    return "Other"


def _points_label(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()
    if not text or text in ("-", "None", "null"):
        return ""

    try:
        return f"{float(text):,.2f}"
    except Exception:
        return text


def _fetch_live_roster_display_rows(dsn: str) -> list[dict[str, Any]]:
    """
    Post-reveal Teams tab display source.

    Keep the football workbench UI, but display only live roster membership:
    active contracts + locked FT + real draft selections.
    """
    sql = """
        WITH ctx AS (
            SELECT
                current_league_key AS league_key,
                current_season_year AS season_year,
                prior_league_key,
                prior_season_year,
                draft_key
            FROM nffl.v_active_season_context
            LIMIT 1
        ),
        snap AS (
            SELECT rs.snapshot_id
            FROM nffl.roster_snapshot rs
            JOIN ctx
              ON ctx.league_key = rs.league_key
             AND ctx.season_year = rs.season_year
            WHERE rs.snapshot_type='END_OF_PRIOR_SEASON_ROSTER'
            ORDER BY rs.updated_at_utc DESC
            LIMIT 1
        ),
        player_lookup AS (
            SELECT DISTINCT ON (w.yahoo_player_key)
                w.yahoo_player_key,
                w.player_name,
                w.nfl_team_abbr,
                w.eligible_positions,
                w.percent_rostered,
                w.bye_week,
                COALESCE(s.stats_json, '{}'::jsonb) AS stats_json,
                fp.fan_points_2025
            FROM nffl.v_team_offseason_qo_ft_workbench w
            JOIN ctx
              ON ctx.league_key = w.league_key
             AND ctx.season_year = w.season_year
            LEFT JOIN snap ON true
            LEFT JOIN nffl.roster_snapshot_player rsp
              ON rsp.snapshot_id = snap.snapshot_id
             AND rsp.league_key = w.league_key
             AND rsp.season_year = w.season_year
             AND rsp.team_key = w.team_key
             AND rsp.yahoo_player_key = w.yahoo_player_key
            LEFT JOIN nffl.roster_snapshot_player_stats s
              ON s.snapshot_id = rsp.snapshot_id
             AND s.team_key = rsp.team_key
             AND s.yahoo_player_key = rsp.yahoo_player_key
            LEFT JOIN nffl.v_roster_snapshot_player_fantasy_points fp
              ON fp.snapshot_id = rsp.snapshot_id
             AND fp.team_key = rsp.team_key
             AND fp.yahoo_player_key = rsp.yahoo_player_key
            ORDER BY w.yahoo_player_key, w.is_active_contract DESC, w.player_name
        ),
        yahoo_stats AS (
            SELECT
                s.league_key,
                (s.season_year + 1) AS season_year,
                s.yahoo_player_key,
                jsonb_object_agg(s.stat_id::text, s.value_raw ORDER BY s.stat_id) AS stats_json,
                ROUND(
                    SUM(COALESCE(s.value_num, 0) * COALESCE(m.modifier_value, 0))::numeric,
                    2
                ) AS fan_points_2025
            FROM public.yahoo_player_league_season_stat s
            JOIN ctx
              ON s.league_key = ctx.league_key
             AND s.season_year = ctx.season_year - 1
            LEFT JOIN nffl.yahoo_stat_modifier m
              ON m.league_key = ctx.prior_league_key
             AND m.season_year = ctx.prior_season_year
             AND m.stat_id = s.stat_id::text
            GROUP BY s.league_key, s.season_year, s.yahoo_player_key
        ),
        live_source AS (
            SELECT
                1 AS source_order,
                'CONTRACT' AS roster_source,
                c.league_key,
                c.season_year,
                c.team_key,
                c.yahoo_player_key,
                c.contract_years_remaining::text AS display_label,
                c.contract_years_remaining,
                true AS is_active_contract
            FROM nffl.contract c
            JOIN ctx
              ON ctx.league_key = c.league_key
             AND ctx.season_year = c.season_year
            WHERE c.status = 'active'

            UNION ALL

            SELECT
                2 AS source_order,
                'FT' AS roster_source,
                d.league_key,
                d.season_year,
                d.team_key,
                d.yahoo_player_key,
                'FT' AS display_label,
                NULL::integer AS contract_years_remaining,
                false AS is_active_contract
            FROM nffl.offseason_keeper_decision d
            JOIN ctx
              ON ctx.league_key = d.league_key
             AND ctx.season_year = d.season_year
            WHERE d.decision_type = 'FT'
              AND d.decision_status = 'LOCKED'

            UNION ALL

            SELECT
                3 AS source_order,
                'DRAFTED' AS roster_source,
                ctx.league_key,
                ctx.season_year,
                ds.selecting_team_key AS team_key,
                ds.yahoo_player_key,
                ds.pick_id || ':' || ds.pick_kind AS display_label,
                NULL::integer AS contract_years_remaining,
                false AS is_active_contract
            FROM nffl.draft_selection ds
            JOIN ctx
              ON ctx.draft_key = ds.draft_key
            WHERE NOT EXISTS (
                SELECT 1
                FROM nffl.contract c
                WHERE c.league_key = ctx.league_key
                  AND c.season_year = ctx.season_year
                  AND c.team_key =
                      ds.selecting_team_key
                  AND c.yahoo_player_key =
                      ds.yahoo_player_key
                  AND c.status = 'active'
            )
              AND NOT EXISTS (
                SELECT 1
                FROM nffl.offseason_keeper_decision ft
                WHERE ft.league_key = ctx.league_key
                  AND ft.season_year = ctx.season_year
                  AND ft.team_key =
                      ds.selecting_team_key
                  AND ft.yahoo_player_key =
                      ds.yahoo_player_key
                  AND ft.decision_type = 'FT'
                  AND ft.decision_status = 'LOCKED'
            )
        )
        SELECT
            ls.league_key,
            ls.season_year,
            ls.team_key,
            t.team_name,
            t.owner_name,
            ls.yahoo_player_key,
            COALESCE(pl.player_name, pu.full_name, ls.yahoo_player_key) AS player_name,
            COALESCE(pl.nfl_team_abbr, pu.nfl_team_abbr, '') AS nfl_team_abbr,
            COALESCE(pl.eligible_positions, pu.eligible_positions, '[]'::jsonb) AS eligible_positions,
            COALESCE(pl.percent_rostered, pu.percent_owned::text) AS percent_rostered,
            COALESCE(pl.bye_week::text, bye.bye_week) AS bye_week,
            ls.contract_years_remaining,
            ls.is_active_contract,
            false AS was_franchise_tagged_prior_season,
            false AS can_select_qo,
            false AS can_select_ft,
            ls.roster_source AS manager_action_status,
            COALESCE(pl.stats_json, ys.stats_json, '{}'::jsonb) AS stats_json,
            COALESCE(pl.fan_points_2025, ys.fan_points_2025) AS fan_points_2025,
            ls.roster_source,
            ls.display_label
        FROM live_source ls
        LEFT JOIN nffl.team t
          ON t.league_key = ls.league_key
         AND t.season_year = ls.season_year
         AND t.team_key = ls.team_key
        LEFT JOIN player_lookup pl
          ON pl.yahoo_player_key = ls.yahoo_player_key
        LEFT JOIN nffl.player_universe pu
          ON pu.league_key = ls.league_key
         AND pu.season_year = ls.season_year
         AND pu.yahoo_player_key = ls.yahoo_player_key
        LEFT JOIN LATERAL (
            SELECT elem->'bye_weeks'->>'week' AS bye_week
            FROM jsonb_array_elements(COALESCE(pu.raw_payload->0, '[]'::jsonb)) AS raw(elem)
            WHERE elem ? 'bye_weeks'
            LIMIT 1
        ) bye ON true
        LEFT JOIN yahoo_stats ys
          ON ys.league_key = ls.league_key
         AND ys.season_year = ls.season_year
         AND ys.yahoo_player_key = ls.yahoo_player_key
        ORDER BY
            t.team_name,
            ls.source_order,
            COALESCE(pl.fan_points_2025, ys.fan_points_2025, 0) DESC,
            COALESCE(pl.player_name, pu.full_name, ls.yahoo_player_key);
    """

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]


def _percent_label(value: Any) -> str:
    if value is None:
        return "0%"

    text = str(value).strip()
    if not text or text in ("-", "None", "null"):
        return "0%"

    if text.endswith("%"):
        return text

    try:
        return f"{float(text):.0f}%"
    except Exception:
        return text




def _load_season_end_contract_state(
    dsn: str,
) -> dict[str, Any]:
    """Return the current status of the prior-season contract rollover."""
    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_season_year,
                    current_league_key,
                    prior_season_year,
                    prior_league_key
                FROM nffl.v_active_season_context
                LIMIT 1
                """
            )
            ctx = cur.fetchone()

            if not ctx:
                raise RuntimeError(
                    "No active NFFL season context is configured."
                )

            current_year = int(ctx["current_season_year"])
            prior_year = int(ctx["prior_season_year"])
            current_league = str(ctx["current_league_key"])
            prior_league = str(ctx["prior_league_key"])

            snapshot_id = (
                f"nffl_{current_year}_from_"
                f"{prior_year}_end_roster"
            )

            cur.execute(
                """
                SELECT count(*)::integer AS contract_count
                FROM nffl.contract
                WHERE league_key = %s
                  AND season_year = %s
                  AND status = 'active'
                """,
                (
                    prior_league,
                    prior_year,
                ),
            )
            prior_contract_count = int(
                cur.fetchone()["contract_count"]
            )

            cur.execute(
                """
                SELECT
                    rs.snapshot_id,

                    (
                        SELECT count(*)::integer
                        FROM nffl.roster_snapshot_source_player sp
                        WHERE sp.snapshot_id = rs.snapshot_id
                    ) AS source_player_count,

                    (
                        SELECT count(*)::integer
                        FROM nffl.roster_snapshot_player rp
                        WHERE rp.snapshot_id = rs.snapshot_id
                    ) AS normalized_player_count,

                    (
                        SELECT count(*)::integer
                        FROM nffl.roster_snapshot_source_player sp
                        WHERE sp.snapshot_id = rs.snapshot_id
                          AND sp.mapping_status =
                              'NOT_IN_CURRENT_UNIVERSE'
                    ) AS unmapped_player_count,

                    rf.content_sha256,
                    rf.finalized_at_utc,
                    rf.finalized_by

                FROM nffl.roster_snapshot rs

                LEFT JOIN nffl.roster_snapshot_finalization rf
                  ON rf.snapshot_id = rs.snapshot_id

                WHERE rs.snapshot_id = %s
                """,
                (snapshot_id,),
            )

            snapshot = cur.fetchone()

            cur.execute(
                """
                SELECT count(*)::integer AS audit_count
                FROM nffl.v_contract_season_audit_current
                WHERE league_key = %s
                  AND season_year = %s
                  AND end_roster_snapshot_id = %s
                """,
                (
                    prior_league,
                    prior_year,
                    snapshot_id,
                ),
            )
            audit_count = int(
                cur.fetchone()["audit_count"]
            )

            cur.execute(
                """
                SELECT count(*)::integer AS contract_count
                FROM nffl.contract
                WHERE league_key = %s
                  AND season_year = %s
                  AND contract_source = 'season_rollover'
                  AND source_snapshot_id = %s
                """,
                (
                    current_league,
                    current_year,
                    snapshot_id,
                ),
            )
            rollover_contract_count = int(
                cur.fetchone()["contract_count"]
            )

    state: dict[str, Any] = {
        "current_season_year": current_year,
        "current_league_key": current_league,
        "prior_season_year": prior_year,
        "prior_league_key": prior_league,
        "snapshot_id": snapshot_id,
        "prior_contract_count": prior_contract_count,
        "snapshot_exists": snapshot is not None,
        "snapshot_finalized": (
            snapshot is not None
            and snapshot["finalized_at_utc"] is not None
        ),
        "audit_count": audit_count,
        "rollover_contract_count": rollover_contract_count,
    }

    if snapshot is not None:
        state.update(dict(snapshot))

    return state


def _capture_prior_season_roster_snapshot(
    dsn: str,
) -> str:
    """Run the existing Yahoo prior-season roster loader."""
    import os
    import subprocess
    import sys

    loader = (
        "/app/scripts/yahoo/"
        "yahoo_nffl_prior_roster_snapshot_load.py"
    )

    if not os.path.isfile(loader):
        raise RuntimeError(
            f"Yahoo roster loader was not found at {loader}."
        )

    env = dict(os.environ)
    env["POSTGRES_DSN"] = dsn

    result = subprocess.run(
        [
            sys.executable,
            loader,
        ],
        cwd="/app",
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    output = "\n".join(
        part.strip()
        for part in (
            result.stdout,
            result.stderr,
        )
        if part and part.strip()
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Yahoo roster capture failed.\n"
            + output[-4000:]
        )

    return output


def _finalize_roster_snapshot(
    dsn: str,
    snapshot_id: str,
    *,
    finalized_by: str,
) -> dict[str, Any]:
    """Lock a captured roster snapshot against later modification."""
    import hashlib

    actor = str(finalized_by or "").strip()

    if not actor:
        actor = "commissioner_ui"

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM nffl.roster_snapshot_finalization
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            existing = cur.fetchone()

            if existing:
                return dict(existing)

            cur.execute(
                """
                SELECT 1
                FROM nffl.roster_snapshot
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )

            if not cur.fetchone():
                raise RuntimeError(
                    f"Roster snapshot {snapshot_id} does not exist."
                )

            cur.execute(
                """
                SELECT
                    (
                        SELECT count(*)::integer
                        FROM nffl.roster_snapshot_source_player
                        WHERE snapshot_id = %s
                    ) AS source_player_count,

                    (
                        SELECT count(*)::integer
                        FROM nffl.roster_snapshot_player
                        WHERE snapshot_id = %s
                    ) AS normalized_player_count,

                    (
                        SELECT count(*)::integer
                        FROM nffl.roster_snapshot_source_player
                        WHERE snapshot_id = %s
                          AND mapping_status =
                              'NOT_IN_CURRENT_UNIVERSE'
                    ) AS unmapped_player_count
                """,
                (
                    snapshot_id,
                    snapshot_id,
                    snapshot_id,
                ),
            )
            counts = dict(cur.fetchone())

            if int(counts["source_player_count"]) <= 0:
                raise RuntimeError(
                    "Cannot lock a roster snapshot with "
                    "zero Yahoo roster rows."
                )

            cur.execute(
                """
                SELECT
                    (
                        to_jsonb(sp)
                        - 'updated_at_utc'
                    )::text AS evidence_json
                FROM nffl.roster_snapshot_source_player sp
                WHERE sp.snapshot_id = %s
                ORDER BY
                    sp.source_team_key,
                    sp.source_yahoo_player_key,
                    sp.roster_slot NULLS FIRST
                """,
                (snapshot_id,),
            )

            evidence = "\n".join(
                str(row["evidence_json"])
                for row in cur.fetchall()
            )

            content_sha256 = hashlib.sha256(
                evidence.encode("utf-8")
            ).hexdigest()

            cur.execute(
                """
                INSERT INTO nffl.roster_snapshot_finalization (
                    snapshot_id,
                    source_player_count,
                    normalized_player_count,
                    unmapped_player_count,
                    content_sha256,
                    finalized_at_utc,
                    finalized_by,
                    note
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    now(),
                    %s,
                    %s
                )
                RETURNING *
                """,
                (
                    snapshot_id,
                    int(counts["source_player_count"]),
                    int(counts["normalized_player_count"]),
                    int(counts["unmapped_player_count"]),
                    content_sha256,
                    actor,
                    (
                        "Locked from the commissioner "
                        "season-end contract workflow."
                    ),
                ),
            )

            finalized = dict(cur.fetchone())

    return finalized


def _preview_season_end_contract_changes(
    dsn: str,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Preview the prior-season contract results without writing them."""
    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM nffl.preview_contract_season_reconciliation(
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    state["prior_league_key"],
                    int(state["prior_season_year"]),
                    state["snapshot_id"],
                ),
            )

            return [
                dict(row)
                for row in cur.fetchall()
            ]


def _apply_season_end_contract_changes(
    dsn: str,
    state: dict[str, Any],
    *,
    audited_by: str,
) -> list[dict[str, Any]]:
    """Write the reviewed contract rollover into the next season."""
    actor = str(audited_by or "").strip()

    if not actor:
        actor = "commissioner_ui"

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM nffl.apply_contract_season_reconciliation(
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    state["prior_league_key"],
                    int(state["prior_season_year"]),
                    state["snapshot_id"],
                    actor,
                ),
            )

            return [
                dict(row)
                for row in cur.fetchall()
            ]


def _load_needs_review_contract_audits(
    dsn: str,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return current unresolved season-end contract audits."""
    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.*,
                    COALESCE(
                        pu.full_name,
                        a.yahoo_player_key_at_start
                    ) AS player_name,
                    COALESCE(
                        start_team.team_name,
                        a.team_key_at_start
                    ) AS start_team_name,
                    COALESCE(
                        observed_team.team_name,
                        a.observed_current_team_key
                    ) AS observed_team_name

                FROM nffl.v_contract_season_audit_current a

                LEFT JOIN nffl.player_universe pu
                  ON pu.league_key = a.league_key
                 AND pu.season_year = a.season_year
                 AND pu.yahoo_player_key =
                     a.yahoo_player_key_at_start

                LEFT JOIN nffl.team start_team
                  ON start_team.league_key = a.league_key
                 AND start_team.season_year = a.season_year
                 AND start_team.team_key =
                     a.team_key_at_start

                LEFT JOIN nffl.team observed_team
                  ON observed_team.league_key = %s
                 AND observed_team.season_year = %s
                 AND observed_team.team_key =
                     a.observed_current_team_key

                WHERE a.league_key = %s
                  AND a.season_year = %s
                  AND a.end_roster_snapshot_id = %s
                  AND a.rollover_action = 'NEEDS_REVIEW'

                ORDER BY
                    player_name,
                    a.yahoo_player_key_at_start
                """,
                (
                    state["current_league_key"],
                    int(state["current_season_year"]),
                    state["prior_league_key"],
                    int(state["prior_season_year"]),
                    state["snapshot_id"],
                ),
            )

            return [
                dict(row)
                for row in cur.fetchall()
            ]


def _resolve_contract_review(
    dsn: str,
    contract_season_audit_id: int,
    resolution: str,
    *,
    resolved_by: str,
) -> dict[str, Any]:
    """Resolve one NEEDS_REVIEW audit by creating the next revision."""
    actor = str(resolved_by or "").strip()

    if not actor:
        actor = "commissioner_ui"

    resolution = str(resolution or "").strip().upper()

    if resolution not in {
        "RECOGNIZE_MOVE",
        "DROP",
        "VOID",
    }:
        raise RuntimeError(
            f"Unsupported contract review resolution: {resolution}"
        )

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM nffl.contract_season_audit
                WHERE contract_season_audit_id = %s
                FOR UPDATE
                """,
                (int(contract_season_audit_id),),
            )
            current = cur.fetchone()

            if not current:
                raise RuntimeError(
                    "The contract review audit no longer exists."
                )

            current = dict(current)

            cur.execute(
                """
                SELECT 1
                FROM nffl.contract_season_audit
                WHERE supersedes_audit_id = %s
                LIMIT 1
                """,
                (
                    int(
                        current[
                            "contract_season_audit_id"
                        ]
                    ),
                ),
            )

            if cur.fetchone():
                raise RuntimeError(
                    "This contract review has already been resolved."
                )

            if str(current["rollover_action"]) != "NEEDS_REVIEW":
                raise RuntimeError(
                    "Only a current NEEDS_REVIEW audit can be resolved."
                )

            league_key = str(current["league_key"])
            season_year = int(current["season_year"])

            cur.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext(%s),
                    %s
                )
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            cur.execute(
                """
                SELECT
                    current_season_year,
                    current_league_key,
                    prior_season_year,
                    prior_league_key
                FROM nffl.v_active_season_context
                LIMIT 1
                """
            )
            ctx = cur.fetchone()

            if not ctx:
                raise RuntimeError(
                    "No active NFFL season context is configured."
                )

            ctx = dict(ctx)

            expected_next_year = season_year + 1

            if (
                int(ctx["current_season_year"])
                != expected_next_year
                or int(ctx["prior_season_year"])
                != season_year
                or str(ctx["prior_league_key"])
                != league_key
            ):
                raise RuntimeError(
                    "The active NFFL season context no longer "
                    "matches this contract review."
                )

            next_league_key: str | None = None
            next_season_year: int | None = None
            next_team_key: str | None = None
            next_yahoo_player_key: str | None = None
            next_contract_years: int | None = None
            create_contract = False

            contract_years_at_start = int(
                current["contract_years_at_start"]
            )

            if resolution == "RECOGNIZE_MOVE":
                if (
                    str(
                        current[
                            "roster_reconciliation_status"
                        ]
                    )
                    != "DIFFERENT_TEAM"
                ):
                    raise RuntimeError(
                        "Recognize Move is available only when "
                        "Yahoo shows the player on another NFFL team."
                    )

                observed_team_key = str(
                    current.get(
                        "observed_current_team_key"
                    )
                    or ""
                ).strip()

                observed_player_key = str(
                    current.get(
                        "observed_current_yahoo_player_key"
                    )
                    or ""
                ).strip()

                if (
                    not observed_team_key
                    or not observed_player_key
                ):
                    raise RuntimeError(
                        "Yahoo did not provide a trustworthy "
                        "next-season team/player mapping."
                    )

                cur.execute(
                    """
                    SELECT 1
                    FROM nffl.team
                    WHERE league_key = %s
                      AND season_year = %s
                      AND team_key = %s
                    """,
                    (
                        str(ctx["current_league_key"]),
                        expected_next_year,
                        observed_team_key,
                    ),
                )

                if not cur.fetchone():
                    raise RuntimeError(
                        "The observed destination team is not "
                        "part of the configured next NFFL season."
                    )

                cur.execute(
                    """
                    SELECT 1
                    FROM nffl.player_universe
                    WHERE league_key = %s
                      AND season_year = %s
                      AND yahoo_player_key = %s
                    """,
                    (
                        str(ctx["current_league_key"]),
                        expected_next_year,
                        observed_player_key,
                    ),
                )

                if not cur.fetchone():
                    raise RuntimeError(
                        "The observed player is not present in "
                        "the configured next-season player universe."
                    )

                if contract_years_at_start >= 2:
                    rollover_action = "CARRY_FORWARD"
                    next_league_key = str(
                        ctx["current_league_key"]
                    )
                    next_season_year = expected_next_year
                    next_team_key = observed_team_key
                    next_yahoo_player_key = observed_player_key
                    next_contract_years = (
                        contract_years_at_start - 1
                    )
                    create_contract = True

                    resolution_note = (
                        "Commissioner recognized the Yahoo "
                        "team change and carried the contract "
                        "to the observed NFFL team."
                    )
                else:
                    rollover_action = "EXPIRE"

                    resolution_note = (
                        "Commissioner recognized the Yahoo "
                        "team change; the contract was in its "
                        "final year and expired."
                    )

            elif resolution == "DROP":
                rollover_action = "DROP"
                resolution_note = (
                    "Commissioner resolved this review as "
                    "a dropped contract."
                )

            else:
                rollover_action = "VOID"
                resolution_note = (
                    "Commissioner voided this contract as "
                    "an administrative correction."
                )

            existing_reason = str(
                current.get("reconciliation_reason") or ""
            ).strip()

            reconciliation_reason = (
                f"{existing_reason} {resolution_note}"
                if existing_reason
                else resolution_note
            )

            if create_contract:
                stable_player_id = str(
                    current["stable_player_id"]
                )

                cur.execute(
                    """
                    SELECT *
                    FROM nffl.contract
                    WHERE league_key = %s
                      AND season_year = %s
                      AND split_part(
                            yahoo_player_key,
                            '.p.',
                            2
                          ) = %s
                    FOR UPDATE
                    """,
                    (
                        next_league_key,
                        next_season_year,
                        stable_player_id,
                    ),
                )

                existing_contract = cur.fetchone()

                if existing_contract:
                    existing_contract = dict(
                        existing_contract
                    )

                    exact_existing = (
                        str(
                            existing_contract[
                                "yahoo_player_key"
                            ]
                        )
                        == next_yahoo_player_key
                        and str(
                            existing_contract["team_key"]
                        )
                        == next_team_key
                        and int(
                            existing_contract[
                                "contract_years_remaining"
                            ]
                        )
                        == next_contract_years
                        and str(
                            existing_contract[
                                "contract_source"
                            ]
                        )
                        == "season_rollover"
                        and str(
                            existing_contract.get(
                                "source_snapshot_id"
                            )
                            or ""
                        )
                        == str(
                            current[
                                "end_roster_snapshot_id"
                            ]
                        )
                        and str(
                            existing_contract["status"]
                        )
                        == "active"
                    )

                    if not exact_existing:
                        raise RuntimeError(
                            "A conflicting next-season contract "
                            "already exists for this player."
                        )

                    create_contract = False

            next_revision = (
                int(current["audit_revision"]) + 1
            )

            cur.execute(
                """
                INSERT INTO nffl.contract_season_audit (
                    league_key,
                    season_year,
                    team_key_at_start,
                    yahoo_player_key_at_start,
                    stable_player_id,
                    contract_years_at_start,
                    contract_status_at_start,
                    contract_source_at_start,
                    contract_source_snapshot_id,
                    origin_contract_episode_id,
                    end_roster_snapshot_id,
                    roster_reconciliation_status,
                    observed_source_team_key,
                    observed_source_yahoo_player_key,
                    observed_current_team_key,
                    observed_current_yahoo_player_key,
                    rollover_action,
                    next_season_year,
                    next_league_key,
                    next_team_key,
                    next_yahoo_player_key,
                    next_contract_years,
                    reconciliation_reason,
                    audit_revision,
                    supersedes_audit_id,
                    audited_at_utc,
                    audited_by
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    now(), %s
                )
                RETURNING *
                """,
                (
                    current["league_key"],
                    current["season_year"],
                    current["team_key_at_start"],
                    current["yahoo_player_key_at_start"],
                    current["stable_player_id"],
                    current["contract_years_at_start"],
                    current["contract_status_at_start"],
                    current["contract_source_at_start"],
                    current["contract_source_snapshot_id"],
                    current["origin_contract_episode_id"],
                    current["end_roster_snapshot_id"],
                    current[
                        "roster_reconciliation_status"
                    ],
                    current[
                        "observed_source_team_key"
                    ],
                    current[
                        "observed_source_yahoo_player_key"
                    ],
                    current[
                        "observed_current_team_key"
                    ],
                    current[
                        "observed_current_yahoo_player_key"
                    ],
                    rollover_action,
                    next_season_year,
                    next_league_key,
                    next_team_key,
                    next_yahoo_player_key,
                    next_contract_years,
                    reconciliation_reason,
                    next_revision,
                    current[
                        "contract_season_audit_id"
                    ],
                    actor,
                ),
            )

            revised_audit = dict(cur.fetchone())

            contract_created = False

            if create_contract:
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
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'season_rollover',
                        %s,
                        'active',
                        %s,
                        now(),
                        now()
                    )
                    """,
                    (
                        next_league_key,
                        next_season_year,
                        next_team_key,
                        next_yahoo_player_key,
                        next_contract_years,
                        current[
                            "end_roster_snapshot_id"
                        ],
                        (
                            f"Carried forward from "
                            f"{season_year} contract after "
                            "commissioner resolution of "
                            "finalized end-of-season roster "
                            "reconciliation."
                        ),
                    ),
                )

                contract_created = True

    return {
        "contract_season_audit_id": revised_audit[
            "contract_season_audit_id"
        ],
        "audit_revision": revised_audit[
            "audit_revision"
        ],
        "rollover_action": revised_audit[
            "rollover_action"
        ],
        "contract_created": contract_created,
    }


def _render_season_end_contract_update(
    dsn: str,
    *,
    acting_as: str,
) -> None:
    """Commissioner workflow for creating next-season contracts."""
    from collections import Counter

    st.markdown("### Season-End Contract Update")

    try:
        state = _load_season_end_contract_state(dsn)
    except Exception as exc:
        st.error(
            "Could not load the season-end contract status: "
            f"{exc}"
        )
        return

    current_year = int(state["current_season_year"])
    prior_year = int(state["prior_season_year"])
    snapshot_id = str(state["snapshot_id"])
    prior_contract_count = int(
        state["prior_contract_count"]
    )
    audit_count = int(state["audit_count"])

    st.caption(
        f"Move {prior_year} contracts into {current_year}. "
        "The new Yahoo season must be configured first."
    )

    if prior_contract_count == 0:
        st.info(
            f"There are no active {prior_year} contracts "
            "to roll forward in the current season setup. "
            "For the historical 2025 → 2026 bootstrap, this is "
            "expected. This workflow becomes active after the "
            "next Yahoo season is configured."
        )
        return

    if audit_count not in {
        0,
        prior_contract_count,
    }:
        st.error(
            "The season-end audit is incomplete: "
            f"{audit_count} of {prior_contract_count} active "
            "contracts currently have audit results. "
            "Do not continue until this database state is reviewed."
        )
        return

    st.markdown("#### Step 1 — Capture the final Yahoo rosters")

    if not bool(state["snapshot_exists"]):
        st.warning(
            f"No final {prior_year} Yahoo roster has been captured yet."
        )

        if st.button(
            f"Capture {prior_year} Final Yahoo Rosters",
            key="nffl_capture_final_prior_rosters",
        ):
            try:
                _capture_prior_season_roster_snapshot(dsn)
            except Exception as exc:
                st.error(
                    "Yahoo roster capture failed: "
                    f"{exc}"
                )
            else:
                st.success(
                    f"Captured the final {prior_year} Yahoo rosters."
                )
                st.rerun()

        return

    source_count = int(
        state.get("source_player_count") or 0
    )
    normalized_count = int(
        state.get("normalized_player_count") or 0
    )
    unmapped_count = int(
        state.get("unmapped_player_count") or 0
    )

    st.success(
        f"Captured {source_count} Yahoo roster entries. "
        f"{normalized_count} matched DraftBoard players; "
        f"{unmapped_count} could not be mapped automatically."
    )

    st.markdown("#### Step 2 — Lock the captured roster")

    if not bool(state["snapshot_finalized"]):
        st.warning(
            "The roster has been captured but is not locked yet. "
            "Once locked, DraftBoard will preserve it as the "
            "permanent end-of-season evidence."
        )

        confirm_lock = st.checkbox(
            (
                f"I confirm the captured {prior_year} rosters "
                "are the final Yahoo rosters."
            ),
            key="nffl_finalize_roster_snapshot_confirmation",
        )

        if st.button(
            f"Lock {prior_year} Final Rosters",
            type="primary",
            disabled=not confirm_lock,
            key="nffl_finalize_roster_snapshot_button",
        ):
            try:
                _finalize_roster_snapshot(
                    dsn,
                    snapshot_id,
                    finalized_by=acting_as,
                )
            except Exception as exc:
                st.error(
                    "The roster was not locked: "
                    f"{exc}"
                )
            else:
                st.success(
                    f"The {prior_year} final roster is now locked."
                )
                st.rerun()

        return

    fingerprint = str(
        state.get("content_sha256") or ""
    )

    st.success(
        f"The {prior_year} final roster is locked. "
        f"Evidence fingerprint: {fingerprint[:12]}…"
    )

    st.markdown("#### Step 3 — Review the contract changes")

    try:
        preview_rows = _preview_season_end_contract_changes(
            dsn,
            state,
        )
    except Exception as exc:
        st.error(
            "Could not calculate the contract changes: "
            f"{exc}"
        )
        return

    action_counts = Counter(
        str(row.get("rollover_action") or "")
        for row in preview_rows
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Carry Forward",
        int(action_counts.get("CARRY_FORWARD", 0)),
    )
    c2.metric(
        "Expire",
        int(action_counts.get("EXPIRE", 0)),
    )
    c3.metric(
        "Drop",
        (
            int(action_counts.get("DROP", 0))
            + int(action_counts.get("VOID", 0))
        ),
    )
    c4.metric(
        "Needs Review",
        int(action_counts.get("NEEDS_REVIEW", 0)),
    )

    preview_review_rows = [
        row
        for row in preview_rows
        if str(row.get("rollover_action") or "")
        == "NEEDS_REVIEW"
    ]

    if audit_count == 0:
        st.markdown(
            f"#### Step 4 — Record the {prior_year} results"
        )

        if preview_review_rows:
            st.warning(
                f"{len(preview_review_rows)} contract(s) need "
                "commissioner review. Recording the season-end "
                "results will save all automatic carry, expire, "
                "and drop decisions and open those unusual cases "
                "for resolution. It will not guess how to resolve them."
            )
        else:
            st.caption(
                "No unusual contracts need review. "
                "This will record the season-end audit and create "
                f"the contracts that carry into {current_year}."
            )

        confirm_apply = st.checkbox(
            (
                f"I reviewed the {prior_year} results and confirm "
                "DraftBoard should record them."
            ),
            key="nffl_apply_season_rollover_confirmation",
        )

        if st.button(
            f"Record {prior_year} Season-End Results",
            type="primary",
            disabled=not confirm_apply,
            key="nffl_apply_season_rollover_button",
        ):
            try:
                _apply_season_end_contract_changes(
                    dsn,
                    state,
                    audited_by=acting_as,
                )
            except Exception as exc:
                st.error(
                    "The season-end results were not recorded: "
                    f"{exc}"
                )
            else:
                st.success(
                    f"The {prior_year} season-end results "
                    "were recorded."
                )
                st.rerun()

        return

    try:
        unresolved_rows = (
            _load_needs_review_contract_audits(
                dsn,
                state,
            )
        )
    except Exception as exc:
        st.error(
            "Could not load the contracts needing review: "
            f"{exc}"
        )
        return

    if unresolved_rows:
        st.markdown(
            "#### Step 5 — Resolve unusual contracts"
        )

        st.warning(
            f"{len(unresolved_rows)} contract(s) still need "
            "a commissioner decision. Next-season processing "
            "is not complete until each one is resolved."
        )

        for row in unresolved_rows:
            audit_id = int(
                row["contract_season_audit_id"]
            )
            player_name = str(
                row.get("player_name")
                or row["yahoo_player_key_at_start"]
            )
            years = int(
                row["contract_years_at_start"]
            )
            roster_status = str(
                row["roster_reconciliation_status"]
            )

            with st.expander(
                f"{player_name} — {years} year(s) remaining",
                expanded=True,
            ):
                st.write(
                    f"**Starting team:** "
                    f"{row.get('start_team_name') or row['team_key_at_start']}"
                )

                if roster_status == "DIFFERENT_TEAM":
                    st.write(
                        f"**Yahoo final roster:** "
                        f"{row.get('observed_team_name') or row.get('observed_current_team_key')}"
                    )
                    st.caption(
                        "Yahoo shows this contracted player on "
                        "another NFFL team."
                    )
                elif roster_status == "SOURCE_PLAYER_UNMAPPED":
                    st.caption(
                        "Yahoo returned this player in the final "
                        "roster, but DraftBoard could not map the "
                        "player into the new-season Yahoo player universe."
                    )
                else:
                    st.caption(
                        str(
                            row.get(
                                "reconciliation_reason"
                            )
                            or roster_status
                        )
                    )

                choices: dict[str, str] = {}

                if (
                    roster_status == "DIFFERENT_TEAM"
                    and row.get(
                        "observed_current_team_key"
                    )
                    and row.get(
                        "observed_current_yahoo_player_key"
                    )
                ):
                    if years >= 2:
                        choices[
                            "Recognize move / trade and carry contract"
                        ] = "RECOGNIZE_MOVE"
                    else:
                        choices[
                            "Recognize move / trade — contract expires"
                        ] = "RECOGNIZE_MOVE"

                choices["Drop contract"] = "DROP"
                choices["Void contract"] = "VOID"

                choice_label = st.selectbox(
                    "Commissioner resolution",
                    list(choices.keys()),
                    key=(
                        "nffl_contract_review_choice_"
                        f"{audit_id}"
                    ),
                )

                confirm_resolution = st.checkbox(
                    (
                        "I confirm this resolution for "
                        f"{player_name}."
                    ),
                    key=(
                        "nffl_contract_review_confirm_"
                        f"{audit_id}"
                    ),
                )

                if st.button(
                    "Save Resolution",
                    type="primary",
                    disabled=not confirm_resolution,
                    key=(
                        "nffl_contract_review_save_"
                        f"{audit_id}"
                    ),
                ):
                    try:
                        result = _resolve_contract_review(
                            dsn,
                            audit_id,
                            choices[choice_label],
                            resolved_by=acting_as,
                        )
                    except Exception as exc:
                        st.error(
                            "The contract review was not resolved: "
                            f"{exc}"
                        )
                    else:
                        action = str(
                            result["rollover_action"]
                        )

                        if action == "CARRY_FORWARD":
                            st.success(
                                "Resolution saved and the "
                                f"{current_year} contract was created."
                            )
                        elif action == "EXPIRE":
                            st.success(
                                "Resolution saved. The contract "
                                "expired after its final year."
                            )
                        elif action == "DROP":
                            st.success(
                                "Resolution saved as a dropped contract."
                            )
                        else:
                            st.success(
                                "Resolution saved as a voided contract."
                            )

                        st.rerun()

        return

    state = _load_season_end_contract_state(dsn)

    st.success(
        f"The {prior_year} → {current_year} contract update "
        "is complete. "
        f"{state['rollover_contract_count']} active contracts "
        f"carried into {current_year}."
    )

def _fetch_contract_history_rows(
    dsn: str,
) -> list[dict[str, Any]]:
    """Return continuous contract history across legacy and modern seasons."""
    sql = """
        WITH RECURSIVE
        ctx AS (
            SELECT
                current_season_year,
                current_league_key AS league_key
            FROM nffl.v_active_season_context
            LIMIT 1
        ),

        -- Yahoo changes league/team keys between seasons.
        -- Walk the saved team bridges backward so every historical
        -- franchise can be displayed under its current team key.
        team_lineage AS (
            SELECT
                b.current_season_year AS season_year,
                b.current_league_key AS league_key,
                b.current_team_key AS team_key,
                b.current_team_key AS display_team_key
            FROM nffl.team_season_bridge b
            JOIN ctx
              ON ctx.current_season_year =
                 b.current_season_year
             AND ctx.league_key =
                 b.current_league_key

            UNION

            SELECT
                b.source_season_year,
                b.source_league_key,
                b.source_team_key,
                tl.display_team_key
            FROM team_lineage tl
            JOIN nffl.team_season_bridge b
              ON b.current_season_year =
                 tl.season_year
             AND b.current_league_key =
                 tl.league_key
             AND b.current_team_key =
                 tl.team_key
        ),

        -- Spreadsheet history: 2021-2025.
        legacy_rows AS (
            SELECT
                ctx.league_key,
                tl.display_team_key AS team_key,
                e.source_row_number,
                e.source_owner_name,
                e.source_team_name,
                e.player_name,
                e.yahoo_player_key,
                e.source_note,
                s.season_year,
                s.contract_years,
                s.contract_status,
                s.acquisition_type,
                s.source_value,
                s.note
            FROM nffl.historical_contract_episode e
            JOIN team_lineage tl
              ON tl.league_key = e.league_key
             AND tl.team_key = e.team_key
            CROSS JOIN ctx
            JOIN nffl.historical_contract_season s
              ON s.league_key = e.league_key
             AND s.team_key = e.team_key
             AND s.source_row_number =
                 e.source_row_number
            WHERE s.season_year <= 2025
        ),

        -- The contracts that crossed the spreadsheet/DraftBoard
        -- boundary into the 2026 season.
        legacy_boundary_rows AS (
            SELECT
                ctx.league_key,
                tl.display_team_key AS team_key,
                e.source_row_number,
                e.source_owner_name,
                e.source_team_name,
                e.player_name,
                e.yahoo_player_key,
                e.source_note,
                c.season_year,
                CASE
                    WHEN c.status = 'active'
                        THEN c.contract_years_remaining
                    ELSE NULL
                END AS contract_years,
                CASE c.status
                    WHEN 'active' THEN 'CONTRACT'
                    WHEN 'void' THEN 'DROPPED'
                    WHEN 'expired' THEN 'EXPIRED'
                    WHEN 'needs_review' THEN 'NEEDS_REVIEW'
                    ELSE upper(c.status)
                END AS contract_status,
                'NONE'::text AS acquisition_type,
                CASE c.status
                    WHEN 'active'
                        THEN c.contract_years_remaining::text
                    WHEN 'void'
                        THEN 'Dropped'
                    WHEN 'expired'
                        THEN 'Expired'
                    WHEN 'needs_review'
                        THEN 'Review'
                    ELSE c.status
                END AS source_value,
                CASE c.status
                    WHEN 'void'
                        THEN 'Operational contract status: void.'
                    WHEN 'needs_review'
                        THEN 'Operational contract requires review.'
                    ELSE ''
                END AS note
            FROM nffl.historical_contract_episode e
            JOIN team_lineage tl
              ON tl.league_key = e.league_key
             AND tl.team_key = e.team_key
            CROSS JOIN ctx
            JOIN nffl.contract c
              ON c.league_key = e.league_key
             AND c.team_key = e.team_key
             AND c.yahoo_player_key =
                 e.yahoo_player_key
             AND c.season_year = 2026
            WHERE e.yahoo_player_key IS NOT NULL
        ),

        -- Future years for contracts that originally came from
        -- the spreadsheet.  The year-end audit tells us whether
        -- the contract carried, expired, dropped, or needs review.
        legacy_rollover_rows AS (
            SELECT
                ctx.league_key,
                tl.display_team_key AS team_key,
                e.source_row_number,
                e.source_owner_name,
                e.source_team_name,
                e.player_name,
                e.yahoo_player_key,
                e.source_note,

                COALESCE(
                    a.next_season_year,
                    a.season_year + 1
                ) AS season_year,

                CASE
                    WHEN a.rollover_action = 'CARRY_FORWARD'
                        THEN a.next_contract_years
                    ELSE NULL
                END AS contract_years,

                CASE a.rollover_action
                    WHEN 'CARRY_FORWARD' THEN 'CONTRACT'
                    WHEN 'EXPIRE' THEN 'EXPIRED'
                    WHEN 'DROP' THEN 'DROPPED'
                    WHEN 'VOID' THEN 'VOID'
                    WHEN 'NEEDS_REVIEW' THEN 'NEEDS_REVIEW'
                    ELSE a.rollover_action
                END AS contract_status,

                'NONE'::text AS acquisition_type,

                CASE a.rollover_action
                    WHEN 'CARRY_FORWARD'
                        THEN a.next_contract_years::text
                    WHEN 'EXPIRE'
                        THEN 'Expired'
                    WHEN 'DROP'
                        THEN 'Dropped'
                    WHEN 'VOID'
                        THEN 'Void'
                    WHEN 'NEEDS_REVIEW'
                        THEN 'Review'
                    ELSE a.rollover_action
                END AS source_value,

                COALESCE(
                    a.reconciliation_reason,
                    ''
                ) AS note

            FROM nffl.historical_contract_episode e

            JOIN team_lineage tl
              ON tl.league_key = e.league_key
             AND tl.team_key = e.team_key

            CROSS JOIN ctx

            JOIN nffl.v_contract_season_audit_current a
              ON a.origin_contract_episode_id IS NULL
             AND a.stable_player_id =
                 split_part(
                     e.yahoo_player_key,
                     '.',
                     3
                 )
             AND a.season_year >= 2026

            WHERE e.yahoo_player_key IS NOT NULL
              AND a.rollover_action IN (
                  'CARRY_FORWARD',
                  'EXPIRE',
                  'DROP',
                  'VOID',
                  'NEEDS_REVIEW'
              )
        ),

        -- New contracts awarded by DraftBoard beginning in 2026.
        modern_award_rows AS (
            SELECT
                ctx.league_key,
                tl.display_team_key AS team_key,

                -- Historical spreadsheet row numbers are positive.
                -- Modern episode IDs are negated only for the UI row key.
                -h.contract_episode_id
                    AS source_row_number,

                NULL::text AS source_owner_name,
                NULL::text AS source_team_name,

                COALESCE(
                    pu.full_name,
                    h.yahoo_player_key
                ) AS player_name,

                h.yahoo_player_key,

                concat(
                    'Contract awarded via ',
                    h.source_pick_kind,
                    ' ',
                    h.source_pick_id
                ) AS source_note,

                h.season_year,

                h.contract_years_awarded
                    AS contract_years,

                'CONTRACT'::text
                    AS contract_status,

                'NONE'::text
                    AS acquisition_type,

                h.contract_years_awarded::text
                    AS source_value,

                concat(
                    'New ',
                    h.contract_years_awarded,
                    '-year contract awarded via ',
                    h.source_pick_kind,
                    ' ',
                    h.source_pick_id,
                    '.'
                ) AS note

            FROM nffl.contract_history_episode h

            JOIN team_lineage tl
              ON tl.league_key = h.league_key
             AND tl.team_key = h.team_key

            CROSS JOIN ctx

            LEFT JOIN nffl.player_universe pu
              ON pu.league_key = h.league_key
             AND pu.season_year = h.season_year
             AND pu.yahoo_player_key =
                 h.yahoo_player_key

            WHERE h.season_year >= 2026
        ),

        -- Every later year stays attached to the same modern
        -- contract episode that created the original row.
        modern_rollover_rows AS (
            SELECT
                ctx.league_key,
                tl.display_team_key AS team_key,

                -h.contract_episode_id
                    AS source_row_number,

                NULL::text AS source_owner_name,
                NULL::text AS source_team_name,

                COALESCE(
                    pu.full_name,
                    h.yahoo_player_key
                ) AS player_name,

                h.yahoo_player_key,

                concat(
                    'Contract episode ',
                    h.contract_episode_id
                ) AS source_note,

                COALESCE(
                    a.next_season_year,
                    a.season_year + 1
                ) AS season_year,

                CASE
                    WHEN a.rollover_action = 'CARRY_FORWARD'
                        THEN a.next_contract_years
                    ELSE NULL
                END AS contract_years,

                CASE a.rollover_action
                    WHEN 'CARRY_FORWARD' THEN 'CONTRACT'
                    WHEN 'EXPIRE' THEN 'EXPIRED'
                    WHEN 'DROP' THEN 'DROPPED'
                    WHEN 'VOID' THEN 'VOID'
                    WHEN 'NEEDS_REVIEW' THEN 'NEEDS_REVIEW'
                    ELSE a.rollover_action
                END AS contract_status,

                'NONE'::text AS acquisition_type,

                CASE a.rollover_action
                    WHEN 'CARRY_FORWARD'
                        THEN a.next_contract_years::text
                    WHEN 'EXPIRE'
                        THEN 'Expired'
                    WHEN 'DROP'
                        THEN 'Dropped'
                    WHEN 'VOID'
                        THEN 'Void'
                    WHEN 'NEEDS_REVIEW'
                        THEN 'Review'
                    ELSE a.rollover_action
                END AS source_value,

                COALESCE(
                    a.reconciliation_reason,
                    ''
                ) AS note

            FROM nffl.contract_history_episode h

            JOIN team_lineage tl
              ON tl.league_key = h.league_key
             AND tl.team_key = h.team_key

            CROSS JOIN ctx

            JOIN nffl.v_contract_season_audit_current a
              ON a.origin_contract_episode_id =
                 h.contract_episode_id

            LEFT JOIN nffl.player_universe pu
              ON pu.league_key = h.league_key
             AND pu.season_year = h.season_year
             AND pu.yahoo_player_key =
                 h.yahoo_player_key

            WHERE a.rollover_action IN (
                'CARRY_FORWARD',
                'EXPIRE',
                'DROP',
                'VOID',
                'NEEDS_REVIEW'
            )
        ),

        all_rows AS (
            SELECT *
            FROM legacy_rows

            UNION ALL

            SELECT *
            FROM legacy_boundary_rows

            UNION ALL

            SELECT *
            FROM legacy_rollover_rows

            UNION ALL

            SELECT *
            FROM modern_award_rows

            UNION ALL

            SELECT *
            FROM modern_rollover_rows
        ),

        -- Franchise Tags remain authoritative in their own
        -- lifecycle table. Normalize them to the current franchise
        -- lineage only for Contract History display.
        ft_rows AS (
            SELECT
                ctx.league_key,
                f.league_key AS source_league_key,
                tl.display_team_key AS team_key,
                f.season_year,
                f.yahoo_player_key,
                f.note
            FROM nffl.franchise_tag_history f
            JOIN team_lineage tl
              ON tl.league_key = f.league_key
             AND tl.team_key = f.team_key
            CROSS JOIN ctx
            WHERE f.season_year >= 2026
              AND f.tag_status = 'applied'
        ),

        -- Commissioner corrections are sparse overlays.
        -- The original imported/award history remains unchanged.
        -- A real Franchise Tag wins the display for that season.
        effective_rows AS (
            SELECT
                ar.league_key,

                COALESCE(
                    o.team_key,
                    ar.team_key
                ) AS team_key,

                ar.source_row_number,
                ar.source_owner_name,
                ar.source_team_name,
                ar.player_name,
                ar.yahoo_player_key,
                ar.source_note,
                ar.season_year,

                CASE
                    WHEN ft.yahoo_player_key IS NOT NULL
                        THEN NULL
                    WHEN o.yahoo_player_key IS NOT NULL
                        THEN o.contract_years
                    ELSE ar.contract_years
                END AS contract_years,

                CASE
                    WHEN ft.yahoo_player_key IS NOT NULL
                        THEN 'FT'
                    ELSE COALESCE(
                        o.contract_status,
                        ar.contract_status
                    )
                END AS contract_status,

                CASE
                    WHEN ft.yahoo_player_key IS NOT NULL
                        THEN 'NONE'
                    ELSE COALESCE(
                        o.acquisition_type,
                        ar.acquisition_type
                    )
                END AS acquisition_type,

                CASE
                    WHEN ft.yahoo_player_key IS NOT NULL
                        THEN 'FT'
                    WHEN o.yahoo_player_key IS NULL
                        THEN ar.source_value
                    WHEN o.contract_status = 'CONTRACT'
                        THEN o.contract_years::text
                    WHEN o.contract_status = 'NO_CONTRACT'
                        THEN 'No Contract'
                    WHEN o.contract_status = 'DROPPED'
                        THEN 'Dropped'
                    WHEN o.contract_status = 'EXPIRED'
                        THEN 'Expired'
                    WHEN o.contract_status = 'NEEDS_REVIEW'
                        THEN 'Review'
                    ELSE o.contract_status
                END AS source_value,

                CASE
                    WHEN ft.yahoo_player_key IS NOT NULL
                        THEN COALESCE(
                            NULLIF(ft.note, ''),
                            'Franchise Tag'
                        )
                    WHEN o.yahoo_player_key IS NOT NULL
                        THEN COALESCE(
                            NULLIF(o.note, ''),
                            ar.note
                        )
                    ELSE ar.note
                END AS note

            FROM all_rows ar

            LEFT JOIN
                nffl.contract_history_season_override o
              ON o.league_key = ar.league_key
             AND o.season_year = ar.season_year
             AND o.yahoo_player_key =
                 ar.yahoo_player_key

            LEFT JOIN ft_rows ft
              ON ft.league_key = ar.league_key
             AND ft.team_key = COALESCE(
                    o.team_key,
                    ar.team_key
                 )
             AND ft.season_year = ar.season_year
             AND split_part(
                    ft.yahoo_player_key,
                    '.',
                    3
                 ) = split_part(
                    ar.yahoo_player_key,
                    '.',
                    3
                 )
        ),

        -- If the FT season has no normal contract row of its own,
        -- attach the FT cell to the player's most recent episode.
        -- If no episode exists at all, use a display-only stable
        -- negative key derived from Yahoo's numeric player id.
        missing_ft_rows AS (
            SELECT
                ft.league_key,
                ft.team_key,

                COALESCE(
                    prior.source_row_number::bigint,
                    (
                        -1000000000::bigint
                        - split_part(
                            ft.yahoo_player_key,
                            '.',
                            3
                          )::bigint
                    )
                ) AS source_row_number,

                prior.source_owner_name,
                prior.source_team_name,

                COALESCE(
                    prior.player_name,
                    pu.full_name,
                    ft.yahoo_player_key
                ) AS player_name,

                ft.yahoo_player_key,

                COALESCE(
                    prior.source_note,
                    'Franchise Tag'
                ) AS source_note,

                ft.season_year,

                NULL::integer AS contract_years,
                'FT'::text AS contract_status,
                'NONE'::text AS acquisition_type,
                'FT'::text AS source_value,

                COALESCE(
                    NULLIF(ft.note, ''),
                    'Franchise Tag'
                ) AS note

            FROM ft_rows ft

            LEFT JOIN LATERAL (
                SELECT
                    ar.source_row_number,
                    ar.source_owner_name,
                    ar.source_team_name,
                    ar.player_name,
                    ar.source_note
                FROM all_rows ar
                WHERE ar.team_key = ft.team_key
                  AND split_part(
                          ar.yahoo_player_key,
                          '.',
                          3
                      ) = split_part(
                          ft.yahoo_player_key,
                          '.',
                          3
                      )
                  AND ar.season_year < ft.season_year
                ORDER BY ar.season_year DESC
                LIMIT 1
            ) prior
              ON true

            LEFT JOIN nffl.player_universe pu
              ON pu.league_key = ft.source_league_key
             AND pu.season_year = ft.season_year
             AND pu.yahoo_player_key =
                 ft.yahoo_player_key

            WHERE NOT EXISTS (
                SELECT 1
                FROM all_rows ar
                WHERE ar.team_key = ft.team_key
                  AND ar.season_year = ft.season_year
                  AND split_part(
                          ar.yahoo_player_key,
                          '.',
                          3
                      ) = split_part(
                          ft.yahoo_player_key,
                          '.',
                          3
                      )
            )
        ),

        display_rows AS (
            SELECT *
            FROM effective_rows

            UNION ALL

            SELECT *
            FROM missing_ft_rows
        )

        SELECT
            dr.*,
            (
                dr.season_year =
                    ctx.current_season_year
                AND upper(
                    dr.contract_status
                ) = 'CONTRACT'
                AND EXISTS (
                    SELECT 1
                    FROM nffl.contract c
                    WHERE c.league_key =
                          ctx.league_key
                      AND c.season_year =
                          ctx.current_season_year
                      AND c.team_key =
                          dr.team_key
                      AND split_part(
                              c.yahoo_player_key,
                              '.',
                              3
                          ) = split_part(
                              dr.yahoo_player_key,
                              '.',
                              3
                          )
                      AND c.status = 'active'
                )
            ) AS is_active_contract

        FROM display_rows dr
        CROSS JOIN ctx

        ORDER BY
            dr.team_key,
            dr.source_row_number,
            dr.season_year DESC
    """

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)

            return [
                dict(row)
                for row in cur.fetchall()
            ]

def _render_contract_history_matrix(
    rows: list[dict[str, Any]],
    *,
    show_internal_notes: bool = True,
) -> None:
    if not rows:
        st.caption(
            "No historical contract data has "
            "been imported for this team."
        )
        return

    episodes: dict[
        int,
        dict[str, Any],
    ] = {}

    years: set[int] = set()

    for row in rows:
        row_number = int(
            row["source_row_number"]
        )

        episode = episodes.setdefault(
            row_number,
            {
                "player_name": str(
                    row["player_name"]
                ),
                "source_note": str(
                    row.get("source_note")
                    or ""
                ),
                "is_active_contract": bool(
                    row.get("is_active_contract")
                ),
                "cells": {},
            },
        )

        episode["is_active_contract"] = (
            bool(
                episode.get(
                    "is_active_contract"
                )
            )
            or bool(
                row.get(
                    "is_active_contract"
                )
            )
        )

        season_year = int(
            row["season_year"]
        )

        years.add(season_year)

        episode["cells"][season_year] = row

    ordered_years = sorted(
        years,
        reverse=True,
    )

    def escaped(value: Any) -> str:
        return html.escape(
            str(value or ""),
            quote=True,
        )

    def cell_details(
        cell: dict[str, Any],
    ) -> tuple[str, str, str]:
        status = str(
            cell["contract_status"]
        ).upper()

        acquisition = str(
            cell["acquisition_type"]
        ).upper()

        years_remaining = cell.get(
            "contract_years"
        )

        if status == "CONTRACT":
            display_value = str(
                int(years_remaining)
            )
            description = (
                f"{display_value}-year contract"
            )
        elif status == "FT":
            display_value = "FT"
            description = "Franchise Tag"
        elif status == "NO_CONTRACT":
            display_value = "NC"
            description = "No Contract"
        elif status == "EXPIRED":
            display_value = "Expired"
            description = "Contract expired"
        elif status == "DROPPED":
            display_value = "Dropped"
            description = "Player dropped"
        else:
            display_value = escaped(
                cell.get("source_value")
            )
            description = display_value

        if status == "DROPPED":
            background = (
                "rgba(239,68,68,0.34)"
            )
        elif acquisition == "TRADE":
            background = (
                "rgba(34,197,94,0.34)"
            )
            description += (
                "; acquired by trade"
            )
        elif acquisition == "WAIVER":
            background = (
                "rgba(249,115,22,0.38)"
            )
            description += (
                "; waiver-wire pickup"
            )
        elif status == "FT":
            background = (
                "rgba(59,130,246,0.36)"
            )
        elif status == "NO_CONTRACT":
            background = (
                "rgba(236,72,153,0.30)"
            )
        elif status == "EXPIRED":
            background = (
                "rgba(107,114,128,0.38)"
            )
        else:
            background = (
                "rgba(148,163,184,0.12)"
            )

        note = str(
            cell.get("note")
            or ""
        ).strip()

        if note and show_internal_notes:
            description += f"; {note}"

        return (
            display_value,
            background,
            description,
        )

    legend = """
    <div class="nffl-history-legend">
      <span><b>4/3/2/1</b> Years remaining</span>
      <span><b>Player names:</b> bright = active; gray = inactive</span>
      <span><i style="background:rgba(59,130,246,.36)"></i>Franchise Tag</span>
      <span><i style="background:rgba(236,72,153,.30)"></i>No Contract</span>
      <span><i style="background:rgba(34,197,94,.34)"></i>Trade</span>
      <span><i style="background:rgba(239,68,68,.34)"></i>Dropped</span>
      <span><i style="background:rgba(249,115,22,.38)"></i>Waiver Pickup</span>
      <span><i style="background:rgba(107,114,128,.38)"></i>Expired</span>
    </div>
    """

    header_cells = "".join(
        (
            "<th>"
            + escaped(year)
            + "</th>"
        )
        for year in ordered_years
    )

    body_rows: list[str] = []

    for row_number in sorted(episodes):
        episode = episodes[row_number]
        player_name = escaped(
            episode["player_name"]
        )

        source_note = escaped(
            episode["source_note"]
        )

        player_title = (
            f' title="{source_note}"'
            if source_note and show_internal_notes
            else ""
        )

        player_class = (
            "nffl-history-player-active"
            if episode.get(
                "is_active_contract"
            )
            else "nffl-history-player-inactive"
        )

        cells = []

        for year in ordered_years:
            cell = episode[
                "cells"
            ].get(year)

            if not cell:
                cells.append(
                    '<td class="nffl-history-empty"></td>'
                )
                continue

            (
                display_value,
                background,
                description,
            ) = cell_details(cell)

            cells.append(
                (
                    '<td style="background:'
                    + background
                    + ';" title="'
                    + escaped(
                        f"{year}: {description}"
                    )
                    + '">'
                    + escaped(display_value)
                    + "</td>"
                )
            )

        body_rows.append(
            (
                "<tr>"
                f'<td class="{player_class}"'
                f"{player_title}>"
                f"{player_name}</td>"
                + "".join(cells)
                + "</tr>"
            )
        )

    markup = f"""
    <style>
      .nffl-history-legend {{
        display:flex;
        flex-wrap:wrap;
        gap:.45rem 1rem;
        margin:.15rem 0 .7rem 0;
        font-size:.82rem;
      }}

      .nffl-history-legend span {{
        display:inline-flex;
        align-items:center;
        gap:.35rem;
      }}

      .nffl-history-legend i {{
        display:inline-block;
        width:.9rem;
        height:.9rem;
        border:1px solid rgba(148,163,184,.55);
        border-radius:.18rem;
      }}

      .nffl-history-scroll {{
        overflow-x:auto;
        max-width:100%;
        border:1px solid rgba(148,163,184,.35);
        border-radius:.4rem;
      }}

      .nffl-history-table {{
        border-collapse:separate;
        border-spacing:0;
        min-width:760px;
        width:max-content;
        color:var(--text-color);
        font-size:.9rem;
      }}

      .nffl-history-table th,
      .nffl-history-table td {{
        min-width:105px;
        padding:.48rem .65rem;
        text-align:center;
        border-right:1px solid rgba(148,163,184,.25);
        border-bottom:1px solid rgba(148,163,184,.25);
        white-space:nowrap;
      }}

      .nffl-history-table th {{
        position:sticky;
        top:0;
        z-index:2;
        background:var(--secondary-background-color);
        font-weight:700;
      }}

      .nffl-history-table th:first-child,
      .nffl-history-table td:first-child {{
        position:sticky;
        left:0;
        min-width:220px;
        max-width:220px;
        text-align:left;
        z-index:3;
        background:var(--secondary-background-color);
        font-weight:600;
      }}

      .nffl-history-table th:first-child {{
        z-index:4;
      }}

      .nffl-history-table td.nffl-history-player-active {{
        color:var(--text-color);
        font-weight:700;
      }}

      .nffl-history-table td.nffl-history-player-inactive {{
        color:rgba(148,163,184,.62);
        font-weight:500;
      }}

      .nffl-history-table tr:last-child td {{
        border-bottom:0;
      }}

      .nffl-history-table th:last-child,
      .nffl-history-table td:last-child {{
        border-right:0;
      }}

      .nffl-history-empty {{
        background:transparent;
      }}
    </style>

    {legend}

    <div class="nffl-history-scroll">
      <table class="nffl-history-table">
        <thead>
          <tr>
            <th>Players</th>
            {header_cells}
          </tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """

    st.markdown(
        markup,
        unsafe_allow_html=True,
    )




def _contract_label(row: dict[str, Any]) -> str:
    roster_source = str(row.get("roster_source") or "").upper()
    if roster_source == "FT":
        return "FT"
    if roster_source == "DRAFTED":
        return "Drafted"

    if not row.get("is_active_contract"):
        return ""
    years = row.get("contract_years_remaining")
    if years is None:
        return "Yes"
    return f"{int(years)} yr"


def _yes_no(value: Any) -> str:
    return "Yes" if bool(value) else "No"


def _stat_value(stats_json: Any, stat_id: str | None) -> str:
    if not stat_id:
        return ""

    stats = stats_json or {}
    if not isinstance(stats, dict):
        return ""

    value = stats.get(str(stat_id))
    if value is None or str(value).strip() in ("", "-"):
        return ""

    text = str(value).strip()

    try:
        if "." not in text:
            return f"{int(text):,}"
        return f"{float(text):,.1f}"
    except Exception:
        return text


def _stat_columns_for(position: str, stat_meta: dict[str, str]) -> list[tuple[str, str | None]]:
    specs = STAT_SPECS.get(position, STAT_SPECS["Other"])
    return [(label, fallback) for label, _patterns, fallback in specs if fallback]


def _sort_group(row: dict[str, Any]) -> tuple[float, str]:
    try:
        points = float(row.get("fan_points_2025") or 0)
    except Exception:
        points = 0.0

    return (-points, str(row.get("player_name") or ""))


def _team_position_df(rows: list[dict[str, Any]], position: str, stat_meta: dict[str, str]) -> pd.DataFrame:
    stat_cols = _stat_columns_for(position, stat_meta)
    ordered = sorted(rows, key=_sort_group)

    table_rows = []
    for r in ordered:
        row = {
            "Player": r.get("player_name") or "",
            "Team": r.get("nfl_team_abbr") or "",
            "Bye": r.get("bye_week") or "",
            "% Ros": _percent_label(r.get("percent_rostered")),
            "Position": _position_label(r.get("eligible_positions")),
            "Contract": _contract_label(r),
            "QO Eligible": _yes_no(r.get("can_select_qo")),
            "FT Eligible": _yes_no(r.get("can_select_ft")),
            "Fan Pts": _points_label(r.get("fan_points_2025")),
        }

        for label, stat_id in stat_cols:
            row[label] = _stat_value(r.get("stats_json"), stat_id)

        table_rows.append(row)

    return pd.DataFrame(table_rows)


def _render_html_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.caption("No players.")
        return

    html = df.to_html(index=False, escape=True, classes="nffl-team-table")
    st.markdown(html, unsafe_allow_html=True)


def _decision_label(row: dict[str, Any]) -> str:
    pos = _position_label(row.get("eligible_positions"))
    team = row.get("nfl_team_abbr") or ""
    return f"{row.get('player_name')} ({team}, {pos})"


def _choice_options(team_rows: list[dict[str, Any]], decision_type: str) -> tuple[list[str], dict[str, str]]:
    if decision_type == "FT":
        rows = [r for r in team_rows if r.get("can_select_ft")]
    else:
        rows = [r for r in team_rows if r.get("can_select_qo")]

    rows = sorted(rows, key=_sort_group)

    options = [""]
    labels = {"": "â€” No selection â€”"}

    for r in rows:
        key = str(r["yahoo_player_key"])
        options.append(key)
        labels[key] = _decision_label(r)

    return options, labels


def _option_index(options: list[str], value: str | None) -> int:
    if value and value in options:
        return options.index(value)
    return 0


def _save_team_decisions(
    dsn: str,
    team: dict[str, Any],
    selections: dict[str, str],
    decided_by: str = "commissioner_ui",
) -> None:
    selected_items = [(slot, player_key) for slot, player_key in selections.items() if player_key]
    player_keys = [player_key for _slot, player_key in selected_items]

    if len(player_keys) != len(set(player_keys)):
        raise ValueError("A player can only be selected once across QO1-QO4 and FT.")

    payload = [{"decision_type": slot, "yahoo_player_key": player_key} for slot, player_key in selected_items]

    league_key = str(team["league_key"])
    season_year = int(team["season_year"])
    team_key = str(team["team_key"])

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO nffl.offseason_team_submission (
                    league_key,
                    season_year,
                    team_key,
                    submission_status,
                    revision_number,
                    updated_at_utc
                )
                VALUES (%s, %s, %s, 'DRAFT', 0, now())
                ON CONFLICT (league_key, season_year, team_key)
                DO NOTHING
                """,
                (league_key, season_year, team_key),
            )

            cur.execute(
                """
                SELECT revision_number
                FROM nffl.offseason_team_submission
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                FOR UPDATE
                """,
                (league_key, season_year, team_key),
            )
            row = cur.fetchone()
            current_revision = int(row["revision_number"] if row else 0)
            new_revision = current_revision + 1

            cur.execute(
                """
                DELETE FROM nffl.offseason_keeper_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                  AND decision_type IN ('QO1', 'QO2', 'QO3', 'QO4', 'FT')
                """,
                (league_key, season_year, team_key),
            )

            for slot, player_key in selected_items:
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
                        updated_at_utc
                    )
                    VALUES (%s, %s, %s, %s, %s, 'DRAFT', %s, %s, now(), %s, now())
                    """,
                    (
                        league_key,
                        season_year,
                        team_key,
                        player_key,
                        slot,
                        new_revision,
                        decided_by,
                        "Saved from NFFL Teams tab.",
                    ),
                )

            cur.execute(
                """
                UPDATE nffl.offseason_team_submission
                SET
                    submission_status='DRAFT',
                    revision_number=%s,
                    submitted_at_utc=NULL,
                    submitted_by=NULL,
                    updated_at_utc=now()
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                """,
                (new_revision, league_key, season_year, team_key),
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
                VALUES (%s, %s, %s, 'SAVE_DRAFT', %s, %s, now(), %s::jsonb, %s)
                """,
                (
                    league_key,
                    season_year,
                    team_key,
                    new_revision,
                    decided_by,
                    json.dumps(payload),
                    "Saved draft QO/FT selections from Teams tab.",
                ),
            )

        conn.commit()



def _load_new_contract_plan(
    dsn: str,
    team: dict[str, Any],
) -> tuple[dict[int, str], int]:
    league_key = str(team["league_key"])
    season_year = int(team["season_year"])
    team_key = str(team["team_key"])

    with psycopg.connect(dsn) as conn:
        with conn.cursor(
            row_factory=dict_row
        ) as cur:
            cur.execute(
                """
                SELECT
                    s.submission_status,
                    s.revision_number,
                    d.contract_years,
                    d.yahoo_player_key
                FROM
                    nffl.post_draft_contract_submission
                        AS s
                LEFT JOIN
                    nffl.post_draft_contract_decision
                        AS d
                  ON d.league_key=s.league_key
                 AND d.season_year=s.season_year
                 AND d.draft_key=s.draft_key
                 AND d.team_key=s.team_key
                WHERE s.league_key=%s
                  AND s.season_year=%s
                  AND s.team_key=%s
                  AND s.draft_key=(
                      SELECT draft_key
                      FROM nffl.v_active_season_context
                      LIMIT 1
                  )
                ORDER BY
                    d.contract_years DESC
                """,
                (
                    league_key,
                    season_year,
                    team_key,
                ),
            )

            rows = [
                dict(row)
                for row in cur.fetchall()
            ]

    if not rows:
        return {}, 0

    statuses = {
        str(
            row["submission_status"]
        ).upper()
        for row in rows
    }

    revisions = {
        int(row["revision_number"])
        for row in rows
    }

    if (
        len(statuses) != 1
        or not statuses.issubset(
            {"DRAFT", "PUBLISHED"}
        )
    ):
        raise ValueError(
            "The saved contract submission "
            "has an invalid status."
        )

    if len(revisions) != 1:
        raise ValueError(
            "The saved contract submission "
            "has inconsistent revisions."
        )

    plan: dict[int, str] = {}

    for row in rows:
        years_value = row[
            "contract_years"
        ]
        player_value = row[
            "yahoo_player_key"
        ]

        if (
            years_value is None
            and player_value is None
        ):
            continue

        if (
            years_value is None
            or player_value is None
        ):
            raise ValueError(
                "A saved contract decision "
                "is incomplete."
            )

        years = int(years_value)
        player_key = str(player_value)

        if years not in {2, 3, 4}:
            raise ValueError(
                "A saved contract decision "
                "has an invalid term."
            )

        if years in plan:
            raise ValueError(
                "A contract term appears more "
                "than once in the saved plan."
            )

        if player_key in plan.values():
            raise ValueError(
                "A player appears more than "
                "once in the saved plan."
            )

        plan[years] = player_key

    return plan, revisions.pop()


def _save_new_contracts(
    dsn: str,
    team: dict[str, Any],
    selections: dict[int, str],
    decided_by: str = "commissioner_ui",
    authorized_team_key: str | None = None,
) -> int:
    league_key = str(team["league_key"])
    season_year = int(team["season_year"])
    team_key = str(team["team_key"])

    if (
        authorized_team_key is not None
        and team_key
        != str(authorized_team_key)
    ):
        raise PermissionError(
            "Managers can save new contracts "
            "only for their own team."
        )

    with psycopg.connect(dsn) as conn:
        with conn.cursor(
            row_factory=dict_row
        ) as cur:
            cur.execute(
                """
                SELECT
                    current_league_key
                        AS league_key,
                    current_season_year
                        AS season_year,
                    draft_key
                FROM nffl.v_active_season_context
                LIMIT 1
                """
            )

            context = cur.fetchone()

            if not context:
                raise ValueError(
                    "The active NFFL draft "
                    "could not be found."
                )

            if (
                str(context["league_key"])
                != league_key
                or int(context["season_year"])
                != season_year
            ):
                raise ValueError(
                    "The selected team does not "
                    "match the active NFFL season."
                )

            draft_key = str(
                context["draft_key"]
            )

            cur.execute(
                """
                SELECT
                    qoft_revealed,
                    post_draft_contracts_revealed
                FROM
                    nffl.league_visibility_state
                WHERE league_key=%s
                  AND season_year=%s
                FOR UPDATE
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            visibility = cur.fetchone()

            if not visibility:
                raise ValueError(
                    "League visibility settings "
                    "could not be found."
                )

            if not bool(
                visibility["qoft_revealed"]
            ):
                raise ValueError(
                    "New contracts cannot be saved "
                    "until QO/FT selections are "
                    "revealed."
                )

            if bool(
                visibility[
                    "post_draft_contracts_revealed"
                ]
            ):
                raise ValueError(
                    "New contracts have already "
                    "been finalized and revealed."
                )

            cur.execute(
                """
                INSERT INTO
                    nffl.post_draft_contract_submission (
                        league_key,
                        season_year,
                        draft_key,
                        team_key,
                        submission_status,
                        revision_number,
                        updated_at_utc
                    )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    'DRAFT',
                    0,
                    now()
                )
                ON CONFLICT (
                    league_key,
                    season_year,
                    team_key
                )
                DO NOTHING
                """,
                (
                    league_key,
                    season_year,
                    draft_key,
                    team_key,
                ),
            )

            cur.execute(
                """
                SELECT
                    draft_key,
                    submission_status,
                    revision_number
                FROM
                    nffl.post_draft_contract_submission
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                FOR UPDATE
                """,
                (
                    league_key,
                    season_year,
                    team_key,
                ),
            )

            submission = cur.fetchone()

            if not submission:
                raise ValueError(
                    "The team contract record "
                    "could not be created."
                )

            if (
                str(submission["draft_key"])
                != draft_key
            ):
                raise ValueError(
                    "The saved contract record "
                    "belongs to a different draft."
                )

            if (
                str(
                    submission[
                        "submission_status"
                    ]
                ).upper()
                == "PUBLISHED"
            ):
                raise ValueError(
                    "This team's contracts have "
                    "already been published."
                )

            cur.execute(
                """
                SELECT
                    pick_id,
                    yahoo_player_key,
                    UPPER(pick_kind)
                        AS pick_kind
                FROM nffl.draft_selection
                WHERE draft_key=%s
                  AND selecting_team_key=%s
                ORDER BY pick_id
                """,
                (
                    draft_key,
                    team_key,
                ),
            )

            draft_rows = [
                dict(row)
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT yahoo_player_key
                FROM nffl.contract
                WHERE league_key=%s
                  AND season_year=%s
                  AND status='active'
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            active_contract_keys = {
                str(row["yahoo_player_key"])
                for row in cur.fetchall()
                if row["yahoo_player_key"]
            }

            cur.execute(
                """
                SELECT
                    team_key,
                    yahoo_player_key
                FROM
                    nffl.offseason_keeper_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND decision_type='FT'
                  AND decision_status='LOCKED'
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            locked_ft_rows = [
                dict(row)
                for row in cur.fetchall()
            ]

            locked_ft_keys = {
                str(row["yahoo_player_key"])
                for row in locked_ft_rows
                if row["yahoo_player_key"]
            }

            has_locked_ft = any(
                str(row["team_key"])
                == team_key
                for row in locked_ft_rows
            )

            eligible_player_keys = (
                eligible_drafted_player_keys(
                    draft_rows,
                    active_contract_keys,
                    locked_ft_keys,
                )
            )

            normalized = (
                validate_contract_selections(
                    selections,
                    eligible_player_keys,
                    has_locked_ft,
                )
            )

            draft_row_by_player = {
                str(
                    row["yahoo_player_key"]
                ): row
                for row in draft_rows
                if row["yahoo_player_key"]
            }

            new_revision = (
                int(
                    submission[
                        "revision_number"
                    ]
                )
                + 1
            )

            payload = []

            for years in sorted(
                normalized,
                reverse=True,
            ):
                player_key = normalized[years]
                source_row = (
                    draft_row_by_player[
                        player_key
                    ]
                )

                payload.append(
                    {
                        "contract_years": years,
                        "yahoo_player_key": (
                            player_key
                        ),
                        "source_pick_id": str(
                            source_row["pick_id"]
                        ),
                        "source_pick_kind": str(
                            source_row[
                                "pick_kind"
                            ]
                        ).upper(),
                    }
                )

            cur.execute(
                """
                DELETE FROM
                    nffl.post_draft_contract_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                """,
                (
                    league_key,
                    season_year,
                    team_key,
                ),
            )

            for decision in payload:
                cur.execute(
                    """
                    INSERT INTO
                        nffl.post_draft_contract_decision (
                            league_key,
                            season_year,
                            draft_key,
                            team_key,
                            contract_years,
                            yahoo_player_key,
                            source_pick_id,
                            source_pick_kind,
                            revision_number,
                            decided_by,
                            decided_at_utc,
                            updated_at_utc
                        )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        now(),
                        now()
                    )
                    """,
                    (
                        league_key,
                        season_year,
                        draft_key,
                        team_key,
                        decision[
                            "contract_years"
                        ],
                        decision[
                            "yahoo_player_key"
                        ],
                        decision[
                            "source_pick_id"
                        ],
                        decision[
                            "source_pick_kind"
                        ],
                        new_revision,
                        decided_by,
                    ),
                )

            cur.execute(
                """
                UPDATE
                    nffl.post_draft_contract_submission
                SET
                    submission_status='DRAFT',
                    revision_number=%s,
                    published_at_utc=NULL,
                    published_by=NULL,
                    updated_at_utc=now()
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                """,
                (
                    new_revision,
                    league_key,
                    season_year,
                    team_key,
                ),
            )

            cur.execute(
                """
                INSERT INTO
                    nffl.post_draft_contract_audit (
                        league_key,
                        season_year,
                        draft_key,
                        team_key,
                        action_type,
                        revision_number,
                        action_by,
                        action_at_utc,
                        decision_payload,
                        note
                    )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    'SAVE_DRAFT',
                    %s,
                    %s,
                    now(),
                    %s::jsonb,
                    %s
                )
                """,
                (
                    league_key,
                    season_year,
                    draft_key,
                    team_key,
                    new_revision,
                    decided_by,
                    json.dumps(
                        payload,
                        sort_keys=True,
                    ),
                    (
                        "Saved new contracts from "
                        "the NFFL Teams tab."
                    ),
                ),
            )

        conn.commit()

    return new_revision


def _reset_team_decisions(
    dsn: str,
    team: dict[str, Any],
    action_by: str = "commissioner_ui",
) -> None:
    league_key = str(team["league_key"])
    season_year = int(team["season_year"])
    team_key = str(team["team_key"])

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO nffl.offseason_team_submission (
                    league_key,
                    season_year,
                    team_key,
                    submission_status,
                    revision_number,
                    reset_count,
                    updated_at_utc
                )
                VALUES (%s, %s, %s, 'DRAFT', 0, 0, now())
                ON CONFLICT (league_key, season_year, team_key)
                DO NOTHING
                """,
                (league_key, season_year, team_key),
            )

            cur.execute(
                """
                SELECT revision_number
                FROM nffl.offseason_team_submission
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                FOR UPDATE
                """,
                (league_key, season_year, team_key),
            )
            row = cur.fetchone()
            current_revision = int(row["revision_number"] if row else 0)
            new_revision = current_revision + 1

            cur.execute(
                """
                DELETE FROM nffl.offseason_keeper_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                  AND decision_type IN ('QO1', 'QO2', 'QO3', 'QO4', 'FT')
                """,
                (league_key, season_year, team_key),
            )

            cur.execute(
                """
                UPDATE nffl.offseason_team_submission
                SET
                    submission_status='DRAFT',
                    revision_number=%s,
                    reset_count=reset_count + 1,
                    reset_at_utc=now(),
                    reset_by=%s,
                    submitted_at_utc=NULL,
                    submitted_by=NULL,
                    updated_at_utc=now()
                WHERE league_key=%s
                  AND season_year=%s
                  AND team_key=%s
                """,
                (new_revision, action_by, league_key, season_year, team_key),
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
                VALUES (%s, %s, %s, 'RESET', %s, %s, now(), '[]'::jsonb, %s)
                """,
                (
                    league_key,
                    season_year,
                    team_key,
                    new_revision,
                    action_by,
                    "Reset draft QO/FT selections from Teams tab.",
                ),
            )

        conn.commit()



def _publish_new_contracts(
    dsn: str,
    published_by: str = "commissioner_ui",
) -> dict[str, int]:
    actor = (
        str(published_by or "").strip()
        or "commissioner_ui"
    )

    with psycopg.connect(dsn) as conn:
        with conn.cursor(
            row_factory=dict_row
        ) as cur:
            cur.execute(
                """
                SELECT
                    current_league_key
                        AS league_key,
                    current_season_year
                        AS season_year,
                    draft_key
                FROM nffl.v_active_season_context
                LIMIT 1
                """
            )

            context = cur.fetchone()

            if not context:
                raise ValueError(
                    "The active NFFL draft "
                    "could not be found."
                )

            league_key = str(
                context["league_key"]
            )
            season_year = int(
                context["season_year"]
            )
            draft_key = str(
                context["draft_key"]
            )

            cur.execute(
                """
                SELECT
                    qoft_revealed,
                    post_draft_contracts_revealed
                FROM
                    nffl.league_visibility_state
                WHERE league_key=%s
                  AND season_year=%s
                FOR UPDATE
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            visibility = cur.fetchone()

            if not visibility:
                raise ValueError(
                    "League visibility settings "
                    "could not be found."
                )

            if not bool(
                visibility["qoft_revealed"]
            ):
                raise ValueError(
                    "QO/FT selections must be "
                    "revealed before new contracts "
                    "can be finalized."
                )

            if bool(
                visibility[
                    "post_draft_contracts_revealed"
                ]
            ):
                raise ValueError(
                    "New contracts have already "
                    "been finalized and revealed."
                )

            cur.execute(
                """
                SELECT
                    count(*) AS draft_pick_count
                FROM nffl.draft_pick
                WHERE draft_key=%s
                """,
                (draft_key,),
            )

            draft_pick_count = int(
                cur.fetchone()[
                    "draft_pick_count"
                ]
            )

            cur.execute(
                """
                SELECT
                    count(*) AS selection_count
                FROM nffl.draft_selection
                WHERE draft_key=%s
                """,
                (draft_key,),
            )

            selection_count = int(
                cur.fetchone()[
                    "selection_count"
                ]
            )

            if draft_pick_count <= 0:
                raise ValueError(
                    "The active draft has no picks."
                )

            if selection_count != draft_pick_count:
                raise ValueError(
                    "The draft must be complete "
                    "before new contracts can be "
                    "finalized. "
                    f"{selection_count} of "
                    f"{draft_pick_count} picks "
                    "have selections."
                )

            cur.execute(
                """
                SELECT DISTINCT
                    column_team_key AS team_key
                FROM nffl.draft_pick
                WHERE draft_key=%s
                ORDER BY column_team_key
                """,
                (draft_key,),
            )

            active_team_keys = [
                str(row["team_key"])
                for row in cur.fetchall()
                if row["team_key"]
            ]

            if not active_team_keys:
                raise ValueError(
                    "No teams were found in the "
                    "active draft."
                )

            active_team_key_set = set(
                active_team_keys
            )

            cur.execute(
                """
                SELECT
                    team_key,
                    draft_key,
                    submission_status,
                    revision_number
                FROM
                    nffl.post_draft_contract_submission
                WHERE league_key=%s
                  AND season_year=%s
                ORDER BY team_key
                FOR UPDATE
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            submission_rows = [
                dict(row)
                for row in cur.fetchall()
            ]

            submission_by_team = {
                str(row["team_key"]): row
                for row in submission_rows
            }

            submission_team_keys = set(
                submission_by_team
            )

            missing_teams = sorted(
                active_team_key_set
                - submission_team_keys
            )

            extra_teams = sorted(
                submission_team_keys
                - active_team_key_set
            )

            if missing_teams:
                raise ValueError(
                    "Every team must save its new "
                    "contract plan before publication. "
                    "Missing teams: "
                    + ", ".join(missing_teams)
                )

            if extra_teams:
                raise ValueError(
                    "Contract submissions exist for "
                    "teams outside the active draft: "
                    + ", ".join(extra_teams)
                )

            for team_key in active_team_keys:
                submission = (
                    submission_by_team[team_key]
                )

                if (
                    str(
                        submission["draft_key"]
                    )
                    != draft_key
                ):
                    raise ValueError(
                        "A saved contract submission "
                        "belongs to a different draft: "
                        f"{team_key}."
                    )

                if (
                    str(
                        submission[
                            "submission_status"
                        ]
                    ).upper()
                    != "DRAFT"
                ):
                    raise ValueError(
                        "Every submission must still "
                        "be in DRAFT status before "
                        "publication."
                    )

                if int(
                    submission[
                        "revision_number"
                    ]
                ) <= 0:
                    raise ValueError(
                        "Every team must explicitly "
                        "save its plan, including an "
                        "empty plan. Unsaved team: "
                        f"{team_key}."
                    )

            cur.execute(
                """
                SELECT
                    team_key,
                    contract_years,
                    yahoo_player_key,
                    source_pick_id,
                    UPPER(source_pick_kind)
                        AS source_pick_kind,
                    revision_number
                FROM
                    nffl.post_draft_contract_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND draft_key=%s
                ORDER BY
                    team_key,
                    contract_years DESC
                """,
                (
                    league_key,
                    season_year,
                    draft_key,
                ),
            )

            decision_rows = [
                dict(row)
                for row in cur.fetchall()
            ]

            decisions_by_team: dict[
                str,
                list[dict[str, Any]],
            ] = {
                team_key: []
                for team_key in active_team_keys
            }

            for row in decision_rows:
                team_key = str(
                    row["team_key"]
                )

                if (
                    team_key
                    not in active_team_key_set
                ):
                    raise ValueError(
                        "A contract decision belongs "
                        "to a team outside the active "
                        f"draft: {team_key}."
                    )

                decisions_by_team[
                    team_key
                ].append(row)

            cur.execute(
                """
                SELECT
                    selecting_team_key
                        AS team_key,
                    pick_id,
                    yahoo_player_key,
                    UPPER(pick_kind)
                        AS pick_kind
                FROM nffl.draft_selection
                WHERE draft_key=%s
                ORDER BY
                    selecting_team_key,
                    pick_id
                FOR SHARE
                """,
                (draft_key,),
            )

            draft_rows = [
                dict(row)
                for row in cur.fetchall()
            ]

            draft_row_by_team_player = {
                (
                    str(row["team_key"]),
                    str(
                        row["yahoo_player_key"]
                    ),
                ): row
                for row in draft_rows
                if (
                    row["team_key"]
                    and row["yahoo_player_key"]
                )
            }

            cur.execute(
                """
                SELECT yahoo_player_key
                FROM nffl.contract
                WHERE league_key=%s
                  AND season_year=%s
                  AND status='active'
                FOR UPDATE
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            active_contract_keys = {
                str(row["yahoo_player_key"])
                for row in cur.fetchall()
                if row["yahoo_player_key"]
            }

            cur.execute(
                """
                SELECT
                    team_key,
                    yahoo_player_key
                FROM
                    nffl.offseason_keeper_decision
                WHERE league_key=%s
                  AND season_year=%s
                  AND decision_type='FT'
                  AND decision_status='LOCKED'
                ORDER BY team_key
                FOR SHARE
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            locked_ft_rows = [
                dict(row)
                for row in cur.fetchall()
            ]

            locked_ft_team_keys = {
                str(row["team_key"])
                for row in locked_ft_rows
                if row["team_key"]
            }

            locked_ft_player_keys = {
                str(row["yahoo_player_key"])
                for row in locked_ft_rows
                if row["yahoo_player_key"]
            }

            selected_player_keys: set[
                str
            ] = set()

            payload_by_team: dict[
                str,
                list[dict[str, Any]],
            ] = {}

            for team_key in active_team_keys:
                submission = (
                    submission_by_team[
                        team_key
                    ]
                )

                revision_number = int(
                    submission[
                        "revision_number"
                    ]
                )

                allowed_years = (
                    {3, 4}
                    if team_key
                    in locked_ft_team_keys
                    else {2, 3, 4}
                )

                seen_years: set[int] = set()
                seen_team_players: set[
                    str
                ] = set()
                payload: list[
                    dict[str, Any]
                ] = []

                for decision in (
                    decisions_by_team[
                        team_key
                    ]
                ):
                    years = int(
                        decision[
                            "contract_years"
                        ]
                    )

                    player_key = str(
                        decision[
                            "yahoo_player_key"
                        ]
                    )

                    if (
                        int(
                            decision[
                                "revision_number"
                            ]
                        )
                        != revision_number
                    ):
                        raise ValueError(
                            "A contract decision does "
                            "not match its team's saved "
                            f"revision: {team_key}."
                        )

                    if years not in allowed_years:
                        raise ValueError(
                            f"{team_key} is not "
                            f"eligible for a {years}-year "
                            "contract slot."
                        )

                    if years in seen_years:
                        raise ValueError(
                            "A team has more than one "
                            f"{years}-year decision: "
                            f"{team_key}."
                        )

                    if (
                        player_key
                        in seen_team_players
                    ):
                        raise ValueError(
                            "A player appears in more "
                            "than one contract slot for "
                            f"{team_key}."
                        )

                    if (
                        player_key
                        in selected_player_keys
                    ):
                        raise ValueError(
                            "A player appears in more "
                            "than one team's new "
                            f"contracts: {player_key}."
                        )

                    if (
                        player_key
                        in active_contract_keys
                    ):
                        raise ValueError(
                            "A selected player already "
                            "has an active contract: "
                            f"{player_key}."
                        )

                    if (
                        player_key
                        in locked_ft_player_keys
                    ):
                        raise ValueError(
                            "A selected player is "
                            "already a locked franchise "
                            f"tag: {player_key}."
                        )

                    source_row = (
                        draft_row_by_team_player.get(
                            (
                                team_key,
                                player_key,
                            )
                        )
                    )

                    if not source_row:
                        raise ValueError(
                            "A selected player was not "
                            "actually drafted by the "
                            f"team: {player_key}."
                        )

                    actual_pick_kind = str(
                        source_row["pick_kind"]
                    ).upper()

                    if actual_pick_kind not in {
                        "QO",
                        "POACH",
                        "FA",
                    }:
                        raise ValueError(
                            "Only QO, POACH, and FA "
                            "draft selections may "
                            "receive new contracts."
                        )

                    if (
                        str(
                            decision[
                                "source_pick_id"
                            ]
                        )
                        != str(
                            source_row["pick_id"]
                        )
                    ):
                        raise ValueError(
                            "A decision's source pick "
                            "does not match the actual "
                            f"draft pick: {player_key}."
                        )

                    if (
                        str(
                            decision[
                                "source_pick_kind"
                            ]
                        ).upper()
                        != actual_pick_kind
                    ):
                        raise ValueError(
                            "A decision's source kind "
                            "does not match the actual "
                            f"draft result: {player_key}."
                        )

                    seen_years.add(years)
                    seen_team_players.add(
                        player_key
                    )
                    selected_player_keys.add(
                        player_key
                    )

                    payload.append(
                        {
                            "contract_years": years,
                            "yahoo_player_key": (
                                player_key
                            ),
                            "source_pick_id": str(
                                source_row["pick_id"]
                            ),
                            "source_pick_kind": (
                                actual_pick_kind
                            ),
                        }
                    )

                payload_by_team[
                    team_key
                ] = payload

            published_contract_count = 0

            for team_key in active_team_keys:
                submission = (
                    submission_by_team[
                        team_key
                    ]
                )

                revision_number = int(
                    submission[
                        "revision_number"
                    ]
                )

                payload = payload_by_team[
                    team_key
                ]

                for decision in payload:
                    player_key = str(
                        decision[
                            "yahoo_player_key"
                        ]
                    )

                    years = int(
                        decision[
                            "contract_years"
                        ]
                    )

                    note = (
                        "Published from the NFFL "
                        "post-draft contract "
                        f"submission, revision "
                        f"{revision_number}."
                    )

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
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            'post_draft_contract_submission',
                            -- A newly awarded contract is not derived
                            -- from roster-snapshot evidence. Draft
                            -- provenance is recorded in
                            -- contract_history_episode below.
                            NULL,
                            'active',
                            %s,
                            now(),
                            now()
                        )
                        ON CONFLICT (
                            league_key,
                            season_year,
                            yahoo_player_key
                        )
                        DO UPDATE
                        SET
                            team_key=EXCLUDED.team_key,
                            contract_years_remaining=
                                EXCLUDED.contract_years_remaining,
                            contract_source=
                                EXCLUDED.contract_source,
                            source_snapshot_id=
                                EXCLUDED.source_snapshot_id,
                            status='active',
                            note=EXCLUDED.note,
                            updated_at_utc=now()
                        WHERE
                            nffl.contract.status
                            <> 'active'
                        RETURNING yahoo_player_key
                        """,
                        (
                            league_key,
                            season_year,
                            team_key,
                            player_key,
                            years,
                            note,
                        ),
                    )

                    if not cur.fetchone():
                        raise ValueError(
                            "A new contract could not "
                            "be written because the "
                            "player already has an "
                            "active contract: "
                            f"{player_key}."
                        )

                    cur.execute(
                        """
                        INSERT INTO
                            nffl.contract_history_episode (
                                league_key,
                                season_year,
                                draft_key,
                                team_key,
                                yahoo_player_key,
                                contract_years_awarded,
                                source_pick_id,
                                source_pick_kind,
                                source_revision_number,
                                published_at_utc,
                                published_by
                            )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            now(),
                            %s
                        )
                        """,
                        (
                            league_key,
                            season_year,
                            draft_key,
                            team_key,
                            player_key,
                            years,
                            decision[
                                "source_pick_id"
                            ],
                            decision[
                                "source_pick_kind"
                            ],
                            revision_number,
                            actor,
                        ),
                    )

                    published_contract_count += 1

                cur.execute(
                    """
                    UPDATE
                        nffl.post_draft_contract_submission
                    SET
                        submission_status='PUBLISHED',
                        published_at_utc=now(),
                        published_by=%s,
                        note=%s,
                        updated_at_utc=now()
                    WHERE league_key=%s
                      AND season_year=%s
                      AND draft_key=%s
                      AND team_key=%s
                      AND submission_status='DRAFT'
                      AND revision_number=%s
                    """,
                    (
                        actor,
                        (
                            "Finalized and revealed "
                            "with all league "
                            "submissions."
                        ),
                        league_key,
                        season_year,
                        draft_key,
                        team_key,
                        revision_number,
                    ),
                )

                if cur.rowcount != 1:
                    raise ValueError(
                        "A team submission changed "
                        "during publication: "
                        f"{team_key}."
                    )

                cur.execute(
                    """
                    INSERT INTO
                        nffl.post_draft_contract_audit (
                            league_key,
                            season_year,
                            draft_key,
                            team_key,
                            action_type,
                            revision_number,
                            action_by,
                            action_at_utc,
                            decision_payload,
                            note
                        )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        'PUBLISH',
                        %s,
                        %s,
                        now(),
                        %s::jsonb,
                        %s
                    )
                    """,
                    (
                        league_key,
                        season_year,
                        draft_key,
                        team_key,
                        revision_number,
                        actor,
                        json.dumps(payload),
                        (
                            "Finalized and revealed "
                            "all new post-draft "
                            "contracts."
                        ),
                    ),
                )

            cur.execute(
                """
                UPDATE nffl.league_visibility_state
                SET
                    post_draft_contracts_revealed=true,
                    post_draft_contracts_revealed_at_utc=
                        now(),
                    post_draft_contracts_revealed_by=%s
                WHERE league_key=%s
                  AND season_year=%s
                  AND post_draft_contracts_revealed=false
                """,
                (
                    actor,
                    league_key,
                    season_year,
                ),
            )

            if cur.rowcount != 1:
                raise ValueError(
                    "The contract visibility state "
                    "changed during publication."
                )

        conn.commit()

    return {
        "team_count": len(
            active_team_keys
        ),
        "contract_count": (
            published_contract_count
        ),
    }


def _post_draft_contracts_revealed(
    dsn: str,
) -> bool:
    sql = """
        SELECT COALESCE(
            v.post_draft_contracts_revealed,
            false
        ) AS contracts_revealed
        FROM nffl.v_active_season_context ctx
        LEFT JOIN nffl.league_visibility_state v
          ON v.league_key =
             ctx.current_league_key
         AND v.season_year =
             ctx.current_season_year
        LIMIT 1;
    """

    try:
        with psycopg.connect(
            dsn,
            row_factory=dict_row,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()

                return bool(
                    row
                    and row[
                        "contracts_revealed"
                    ]
                )
    except Exception:
        return False


def _qoft_revealed(dsn: str) -> bool:
    sql = """
        SELECT COALESCE(v.qoft_revealed, false) AS qoft_revealed
        FROM nffl.v_active_season_context ctx
        LEFT JOIN nffl.league_visibility_state v
          ON v.league_key = ctx.current_league_key
         AND v.season_year = ctx.current_season_year
        LIMIT 1;
    """
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return bool(row and row["qoft_revealed"])
    except Exception:
        return False


def _render_public_decision_summary(
    team_rows: list[dict[str, Any]],
    existing: dict[str, str],
) -> None:
    if not existing:
        st.caption("No QO/FT selections are available yet.")
        return

    label_by_key = {
        str(row.get("yahoo_player_key") or ""): _decision_label(row)
        for row in team_rows
    }

    summary_rows = []
    for slot in ["QO1", "QO2", "QO3", "QO4", "FT"]:
        player_key = str(existing.get(slot) or "").strip()
        if player_key:
            summary_rows.append({
                "Slot": slot,
                "Player": label_by_key.get(player_key, player_key),
            })

    if not summary_rows:
        st.caption("No QO/FT selections are available yet.")
        return

    st.dataframe(summary_rows, hide_index=True, use_container_width=True)


def _render_decision_controls(
    dsn: str,
    team: dict[str, Any],
    team_rows: list[dict[str, Any]],
    existing: dict[str, str],
    submission: dict[str, Any] | None,
    acting_as: str,
) -> None:
    status = (submission or {}).get("submission_status", "DRAFT")
    revision = (submission or {}).get("revision_number", 0)
    reset_count = (submission or {}).get("reset_count", 0)

    st.markdown("#### QO / FT Draft Selections")
    st.caption(
        f"Status: {status} | Revision: {revision} | Resets: {reset_count}. "
        f"Acting as: {acting_as}. This browser identity comes from Team Gateway."
    )

    if status == "LOCKED":
        st.warning("This team is locked. Editing is disabled.")
        return

    form_key = f"nffl_decision_form_{_safe_key(str(team['team_key']))}"

    with st.form(form_key, clear_on_submit=False):
        selections: dict[str, str] = {}

        for slot in DECISION_TYPES:
            options, labels = _choice_options(team_rows, slot)
            if not options:
                st.caption(f"No eligible players for {slot}.")
                selections[slot] = ""
                continue

            selections[slot] = st.selectbox(
                slot,
                options=options,
                index=_option_index(options, existing.get(slot)),
                format_func=lambda value, labels=labels: labels.get(value, value),
                key=f"nffl_{_safe_key(str(team['team_key']))}_{slot}_form",
            )

        chosen = [v for v in selections.values() if v]
        duplicate = len(chosen) != len(set(chosen))

        if duplicate:
            st.error("A player can only be selected once across QO1-QO4 and FT.")

        save_col, reset_col = st.columns([1, 1])

        with save_col:
            save_submitted = st.form_submit_button(
                "Save Selections",
                disabled=duplicate,
                use_container_width=True,
            )

        with reset_col:
            reset_submitted = st.form_submit_button(
                "Reset Selections",
                use_container_width=True,
            )

    if save_submitted:
        try:
            _save_team_decisions(dsn, team, selections, decided_by=acting_as)
            st.success("Draft selections saved.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save selections: {exc}")

    if reset_submitted:
        try:
            _reset_team_decisions(dsn, team, action_by=acting_as)
            st.success("Draft selections reset.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not reset selections: {exc}")


def render_nffl_team_workbench(dsn: str, gateway_context: dict[str, Any] | None = None) -> None:
    st.subheader("Teams")

    try:
        (
            workbench,
            math_rows,
            stat_meta,
            decisions_by_team,
            submissions_by_team,
            locked_ft_team_keys,
        ) = _fetch_rows(dsn)
    except Exception as exc:
        st.error(f"Could not load NFFL team workbench from Postgres: {exc}")
        return

    if not math_rows:
        st.warning("No NFFL roster math rows found.")
        return

    st.markdown(
        """
        <style>
          table.nffl-team-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.86rem;
            margin: 0.45rem 0 1.35rem 0;
            border: 1px solid #94A3B8;
            border-radius: 8px;
            overflow: hidden;
            background: #FFFFFF;
            color: #0F172A;
          }

          table.nffl-team-table th {
            text-align: left;
            background: #0F172A;
            color: #FFFFFF;
            font-weight: 850;
            border-bottom: 2px solid #334155;
            padding: 0.50rem 0.60rem;
            white-space: nowrap;
          }

          table.nffl-team-table td {
            color: #0F172A;
            background: #FFFFFF;
            border-bottom: 1px solid #CBD5E1;
            padding: 0.42rem 0.60rem;
            vertical-align: top;
          }

          table.nffl-team-table tr:nth-child(even) td {
            background: #E9EEF5;
            color: #0F172A;
          }

          table.nffl-team-table tr:nth-child(odd) td {
            background: #FFFFFF;
            color: #0F172A;
          }

          table.nffl-team-table tr:hover td {
            background: #DDE7F3;
            color: #0F172A;
          }

          table.nffl-team-table td:first-child,
          table.nffl-team-table th:first-child {
            font-weight: 750;
            min-width: 11rem;
          }

          table.nffl-team-table td:nth-child(3),
          table.nffl-team-table td:nth-child(4),
          table.nffl-team-table td:nth-child(5),
          table.nffl-team-table td:nth-child(6),
          table.nffl-team-table td:nth-child(7),
          table.nffl-team-table td:nth-child(8),
          table.nffl-team-table td:nth-child(9) {
            white-space: nowrap;
          }

          table.nffl-team-table th:not(:first-child),
          table.nffl-team-table td:not(:first-child) {
            text-align: center;
          }

          table.nffl-team-table th:first-child,
          table.nffl-team-table th:nth-child(2),
          table.nffl-team-table td:first-child,
          table.nffl-team-table td:nth-child(2) {
            text-align: left;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    rows_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in workbench:
        rows_by_team[str(row["team_key"])].append(row)

    gateway_context = gateway_context or {}
    gateway_role = str(gateway_context.get("role") or "public")
    gateway_team_key = str(gateway_context.get("team_key") or "")
    gateway_team_name = str(gateway_context.get("team_name") or "")
    acting_as_base = str(gateway_context.get("acting_as") or gateway_role)
    qoft_revealed = _qoft_revealed(dsn)
    contracts_revealed = (
        _post_draft_contracts_revealed(dsn)
    )

    display_rows_by_team = rows_by_team
    if qoft_revealed:
        try:
            live_display_rows = _fetch_live_roster_display_rows(dsn)
            live_display_rows_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in live_display_rows:
                live_display_rows_by_team[str(row["team_key"])].append(row)
            display_rows_by_team = live_display_rows_by_team
            st.caption("Live roster view: active contracts + locked FT + real draft selections.")
        except Exception as exc:
            st.warning(f"Could not load live roster display rows; falling back to offseason pool: {exc}")

    contract_history_rows_by_team: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    try:
        for historical_row in (
            _fetch_contract_history_rows(
                dsn
            )
        ):
            contract_history_rows_by_team[
                str(historical_row["team_key"])
            ].append(historical_row)
    except Exception as exc:
        st.warning(
            "Could not load historical "
            f"contracts: {exc}"
        )

    if gateway_role == "commissioner":
        visible_math_rows = math_rows
        st.caption("Team Gateway: Commissioner")
    elif gateway_role == "manager" and gateway_team_key:
        visible_math_rows = math_rows
        st.caption(
            f"Team Gateway: {gateway_team_name}. "
            "All rosters/contracts are visible; QO/FT selections remain private until Start Draft."
        )
    else:
        st.info("Choose your team in the Team Gateway.")
        return

    if gateway_role == "commissioner":
        _render_season_end_contract_update(
            dsn,
            acting_as=(
                acting_as_base
                or "commissioner_ui"
            ),
        )

    if (
        gateway_role == "commissioner"
        and qoft_revealed
        and not contracts_revealed
    ):
        st.warning(
            "Finalize & Reveal New Contracts is "
            "irreversible. Every team must first "
            "save its plan, including teams making "
            "no new contracts."
        )

        confirm_contract_publication = st.checkbox(
            "I confirm that the draft is complete "
            "and all team contract plans are ready.",
            key=(
                "nffl_finalize_new_contracts_"
                "confirmation"
            ),
        )

        if st.button(
            "Finalize & Reveal New Contracts",
            type="primary",
            disabled=(
                not confirm_contract_publication
            ),
            key=(
                "nffl_finalize_new_contracts_"
                "button"
            ),
        ):
            try:
                publication_result = (
                    _publish_new_contracts(
                        dsn,
                        published_by=(
                            acting_as_base
                            or "commissioner_ui"
                        ),
                    )
                )
            except Exception as exc:
                st.error(
                    "New contracts were not "
                    f"published: {exc}"
                )
            else:
                st.success(
                    "Finalized and revealed "
                    f"{publication_result['contract_count']} "
                    "new contracts across "
                    f"{publication_result['team_count']} "
                    "teams."
                )
                st.rerun()

    elif (
        gateway_role == "commissioner"
        and contracts_revealed
    ):
        st.success(
            "New contracts are finalized "
            "and revealed."
        )

    def _load_active_draft_team_order() -> list[str]:
        try:
            import psycopg

            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH ctx AS (
                            SELECT draft_key
                            FROM nffl.v_active_season_context
                            LIMIT 1
                        ),
                        first_round AS (
                            SELECT MIN(dp.round_number) AS round_number
                            FROM nffl.draft_pick dp
                            JOIN ctx
                              ON ctx.draft_key = dp.draft_key
                        )
                        SELECT dp.column_team_key
                        FROM nffl.draft_pick dp
                        JOIN ctx
                          ON ctx.draft_key = dp.draft_key
                        JOIN first_round fr
                          ON fr.round_number = dp.round_number
                        ORDER BY dp.slot_number
                        """
                    )
                    return [str(r[0] or "").strip() for r in cur.fetchall() if str(r[0] or "").strip()]
        except Exception:
            return []

    canonical_order = _load_active_draft_team_order()
    if canonical_order:
        order_index = {team_key: idx for idx, team_key in enumerate(canonical_order)}
        visible_math_rows = sorted(
            visible_math_rows,
            key=lambda row: (
                order_index.get(str(row.get("team_key") or ""), 10_000),
                str(row.get("team_name") or ""),
            ),
        )

    if not visible_math_rows:
        st.warning("No teams are available for this Team Gateway selection.")
        return

    default_team_key = ""
    if gateway_role == "manager" and gateway_team_key:
        default_team_key = gateway_team_key
    elif gateway_role == "commissioner":
        default_team_key = "470.l.84346.t.1"  # Buccaneer Blitzkrieg

    if default_team_key:
        visible_math_rows = sorted(
            visible_math_rows,
            key=lambda row: 0 if str(row.get("team_key") or "") == default_team_key else 1,
        )

    tabs = st.tabs([str(r["team_name"]) for r in visible_math_rows])

    for tab, math in zip(tabs, visible_math_rows):
        team_key = str(math["team_key"])
        team_rows = rows_by_team.get(team_key, [])
        display_team_rows = display_rows_by_team.get(team_key, [])
        has_locked_ft = team_key in locked_ft_team_keys
        is_own_team = gateway_role == "manager" and team_key == gateway_team_key
        can_manage_qoft = (
            not contracts_revealed
            and not qoft_revealed
            and (
                gateway_role == "commissioner"
                or is_own_team
            )
        )
        can_see_qoft = (
            not contracts_revealed
            and (
                gateway_role == "commissioner"
                or is_own_team
                or qoft_revealed
            )
        )
        acting_as = acting_as_base if gateway_role == "manager" else f"commissioner:{math['team_name']}"

        with tab:
            st.markdown(f"### {math['team_name']}")
            st.caption(f"Manager: {math['owner_name']}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Post-Draft Cap", int(math["roster_size"]))
            if qoft_revealed:
                c2.metric("Rostered Players", len(display_team_rows))
            else:
                c2.metric("Eligible Offseason Players", len(team_rows))
            c3.metric("Contracts", int(math["active_contracts"]))

            public_open_slots = int(math["roster_size"]) - int(math["active_contracts"])
            if can_see_qoft:
                c4.metric("Open Slots After QO/FT", int(math["open_draft_slots_after_keeper_decisions"]))
            else:
                c4.metric("Public Open Slots Before QO/FT", public_open_slots)

            st.caption(
                "The offseason eligible pool may exceed 16 because IL/extra roster-held players can exist. "
                "The roster must resolve to 16 after keeper decisions and the draft."
            )

            if (
                gateway_role == "commissioner"
                and not qoft_revealed
            ):
                with st.expander(
                    "Save New Contracts - "
                    "Commissioner Design Review",
                    expanded=True,
                ):
                    render_post_draft_contract_tool(
                        team_key=team_key,
                        team_name=str(math["team_name"]),
                        display_team_rows=display_team_rows,
                        has_locked_ft=has_locked_ft,
                        acting_as=acting_as,
                    )

            if (
                not contracts_revealed
                and (
                    gateway_role == "commissioner"
                    or is_own_team
                )
                and qoft_revealed
            ):
                loaded_contract_plan, loaded_contract_revision = (
                    _load_new_contract_plan(
                        dsn,
                        math,
                    )
                )

                render_post_draft_contract_tool(
                    team_key=team_key,
                    team_name=str(math["team_name"]),
                    display_team_rows=display_team_rows,
                    has_locked_ft=has_locked_ft,
                    acting_as=acting_as,
                    persisted_plan=loaded_contract_plan,
                    persisted_revision=loaded_contract_revision,
                    save_plan=lambda selections, team=math: _save_new_contracts(
                        dsn,
                        team,
                        dict(selections),
                        decided_by=acting_as,
                        authorized_team_key=(
                            gateway_team_key
                            if gateway_role == "manager"
                            else None
                        ),
                    ),
                )
            elif can_manage_qoft:
                _render_decision_controls(
                    dsn,
                    math,
                    team_rows,
                    decisions_by_team.get(team_key, {}),
                    submissions_by_team.get(team_key),
                    acting_as,
                )
            elif can_see_qoft:
                st.markdown("#### QO / FT Selections")
                _render_public_decision_summary(
                    team_rows,
                    decisions_by_team.get(team_key, {}),
                )
            elif not contracts_revealed:
                st.caption("QO/FT selections are hidden until the commissioner starts the draft.")

            rows_by_pos: dict[str, list[dict[str, Any]]] = {pos: [] for pos in POSITION_ORDER}
            for row in display_team_rows:
                pos = _primary_position(row)
                rows_by_pos.setdefault(pos, []).append(row)

            for pos in POSITION_ORDER:
                rows = rows_by_pos.get(pos, [])
                if not rows:
                    continue

                st.markdown(f"#### {pos}")
                df = _team_position_df(rows, pos, stat_meta)
                _render_html_table(df)

            st.markdown("#### Contract History")

            _render_contract_history_matrix(
                contract_history_rows_by_team.get(
                    team_key,
                    [],
                ),
                show_internal_notes=(
                    gateway_role == "commissioner"
                ),
            )
