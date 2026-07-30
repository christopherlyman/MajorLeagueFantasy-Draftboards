from __future__ import annotations

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



def _fetch_contract_history_rows(
    dsn: str,
) -> list[dict[str, Any]]:
    sql = """
        WITH ctx AS (
            SELECT
                current_league_key AS league_key
            FROM nffl.v_active_season_context
            LIMIT 1
        )
        SELECT
            h.contract_episode_id,
            h.league_key,
            h.season_year,
            h.draft_key,
            h.team_key,
            h.yahoo_player_key,
            COALESCE(
                pu.full_name,
                h.yahoo_player_key
            ) AS player_name,
            h.contract_years_awarded,
            h.source_pick_id,
            h.source_pick_kind,
            h.published_at_utc
        FROM nffl.contract_history_episode h
        JOIN ctx
          ON ctx.league_key = h.league_key
        LEFT JOIN nffl.player_universe pu
          ON pu.league_key = h.league_key
         AND pu.season_year = h.season_year
         AND pu.yahoo_player_key =
             h.yahoo_player_key
        ORDER BY
            h.team_key,
            h.published_at_utc DESC,
            h.contract_episode_id DESC
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


def _contract_history_df(
    rows: list[dict[str, Any]],
) -> pd.DataFrame:
    display_rows = []

    for row in rows:
        published_at = row.get(
            "published_at_utc"
        )

        published_label = (
            published_at.strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            if hasattr(published_at, "strftime")
            else str(published_at or "")
        )

        pick_id = str(
            row.get("source_pick_id") or ""
        )

        pick_kind = str(
            row.get("source_pick_kind") or ""
        ).upper()

        display_rows.append(
            {
                "Season": int(
                    row["season_year"]
                ),
                "Player": str(
                    row.get("player_name")
                    or row["yahoo_player_key"]
                ),
                "Contract": (
                    f"{int(row['contract_years_awarded'])} yr"
                ),
                "Draft Pick": (
                    f"{pick_id} ({pick_kind})"
                ),
                "Published": published_label,
            }
        )

    return pd.DataFrame(
        display_rows,
        columns=[
            "Season",
            "Player",
            "Contract",
            "Draft Pick",
            "Published",
        ],
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
    labels = {"": "— No selection —"}

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
                            %s,
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
                            draft_key,
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

    contract_history_by_team: dict[
        str,
        list[dict[str, Any]],
    ] = defaultdict(list)

    if contracts_revealed:
        try:
            for history_row in (
                _fetch_contract_history_rows(dsn)
            ):
                contract_history_by_team[
                    str(history_row["team_key"])
                ].append(history_row)
        except Exception as exc:
            st.warning(
                "Could not load contract history: "
                f"{exc}"
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

            if contracts_revealed:
                st.markdown("#### Contract History")

                history_rows = (
                    contract_history_by_team.get(
                        team_key,
                        [],
                    )
                )

                if history_rows:
                    _render_html_table(
                        _contract_history_df(
                            history_rows
                        )
                    )
                else:
                    st.caption(
                        "No published new-contract "
                        "episodes are available for "
                        "this team."
                    )
