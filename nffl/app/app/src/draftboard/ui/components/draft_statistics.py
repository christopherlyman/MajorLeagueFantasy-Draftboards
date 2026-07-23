from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import psycopg
import streamlit as st

from draftboard.state.store import DraftState
from draftboard.state.league_schedule import (
    DRAFT_COMPLETION_DEADLINE_EVENT_CODE,
    DRAFT_START_EVENT_CODE,
    load_draft_schedule,
)
from draftboard.state.runtime import (
    get_league_key,
    get_season_year,
)

NFL_OPENING_DAY_FALLBACK = date(2026, 9, 9)
DRAFT_START_FALLBACK = date(2026, 8, 1)


def parse_opening_day_date_from_env() -> date | None:
    raw = str(os.environ.get("DRAFTBOARD_OPENING_DAY_DATE", "") or "").strip()
    if not raw:
        return NFL_OPENING_DAY_FALLBACK
    try:
        return date.fromisoformat(raw)
    except Exception:
        return NFL_OPENING_DAY_FALLBACK


def parse_draft_start_date_from_env() -> date:
    raw = str(
        os.environ.get("DRAFTBOARD_DRAFT_START_DATE", "") or ""
    ).strip()

    if not raw:
        return DRAFT_START_FALLBACK

    try:
        return date.fromisoformat(raw)
    except ValueError:
        return DRAFT_START_FALLBACK


def _load_statistics_schedule_dates(
    dsn: str,
) -> tuple[date | None, date | None, str | None]:
    """
    Load the canonical start and completion dates for Draft Statistics.

    Missing events return None so existing environment fallbacks remain
    available. Database failures are returned as a displayable warning.
    """
    try:
        schedule = load_draft_schedule(
            dsn,
            get_league_key(),
            get_season_year(),
        )
    except Exception as exc:
        return None, None, str(exc)

    return (
        schedule.active_event_date(
            DRAFT_START_EVENT_CODE
        ),
        schedule.active_event_date(
            DRAFT_COMPLETION_DEADLINE_EVENT_CODE
        ),
        None,
    )


def _real_pick_rows(state: DraftState) -> list[dict[str, Any]]:
    order = list(state.pick_order or [])
    order_index = {pid: i for i, pid in enumerate(order)}

    rows: list[dict[str, Any]] = []
    for pick_id in order:
        ps = state.picks.get(pick_id)
        if ps is None:
            continue

        selected_ts_iso = getattr(ps, "selected_ts_iso", None)
        selected_player_key = getattr(ps, "selected_player_key", None)
        if not selected_ts_iso or not selected_player_key:
            continue

        try:
            selected_ts = datetime.fromisoformat(str(selected_ts_iso))
        except Exception:
            continue

        rows.append(
            {
                "pick_id": str(pick_id),
                "owner_team_key": str(getattr(ps, "owner_team_key", "") or ""),
                "player_key": str(selected_player_key),
                "selected_ts_iso": str(selected_ts_iso),
                "selected_ts": selected_ts,
            }
        )

    rows.sort(key=lambda r: (r["selected_ts"], order_index.get(r["pick_id"], 999999)))
    return rows



def _load_canonical_draft_counts(
    dsn: str,
    draft_key: str,
) -> dict[str, int]:
    """
    Load draft counts from canonical PostgreSQL board truth.

    Contract and FT placeholders occupy board slots without requiring a
    live manager selection. QO placeholders remain live selections.
    """
    sql = """
        SELECT
            COUNT(*) AS board_slots,
            COUNT(*) FILTER (
                WHERE placeholder_source = 'CONTRACT'
            ) AS contract_slots,
            COUNT(*) FILTER (
                WHERE placeholder_source = 'FT'
            ) AS ft_slots,
            COUNT(*) FILTER (
                WHERE selected_at_utc IS NOT NULL
            ) AS completed_live_picks,
            COUNT(*) FILTER (
                WHERE selected_at_utc IS NULL
                  AND COALESCE(placeholder_source, '')
                      NOT IN ('CONTRACT', 'FT')
            ) AS live_picks_remaining
        FROM nffl.v_draft_board_current
        WHERE draft_key = %s
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(draft_key),))
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            f"Canonical draft counts were not returned for {draft_key!r}."
        )

    counts = {
        "board_slots": int(row[0] or 0),
        "contract_slots": int(row[1] or 0),
        "ft_slots": int(row[2] or 0),
        "completed_live_picks": int(row[3] or 0),
        "live_picks_remaining": int(row[4] or 0),
    }

    accounted = (
        counts["contract_slots"]
        + counts["ft_slots"]
        + counts["completed_live_picks"]
        + counts["live_picks_remaining"]
    )

    if accounted != counts["board_slots"]:
        raise RuntimeError(
            "Canonical draft-count integrity failed: "
            f"board_slots={counts['board_slots']}, accounted={accounted}."
        )

    return counts


def is_draft_complete(state: DraftState) -> bool:
    """
    Return True only for the terminal post-draft clock state.

    After the final live selection, advancement finds no next open
    pick, leaves current_pick_id on that real selection, and fully
    stops and clears the clock. Every active or rewound draft state
    points at an unselected current pick instead.
    """
    current_pick_id = str(
        getattr(state.clock, "current_pick_id", "") or ""
    )
    current_pick = state.picks.get(current_pick_id)

    if current_pick is None:
        return False

    return bool(
        getattr(current_pick, "selected_player_key", None)
        and getattr(current_pick, "selected_ts_iso", None)
        and not bool(getattr(state.clock, "is_running", False))
        and getattr(state.clock, "pick_started_ts_iso", None) is None
        and getattr(state.clock, "pick_paused_ts_iso", None) is None
        and int(
            getattr(
                state.clock,
                "elapsed_paused_seconds",
                0,
            )
            or 0
        ) == 0
    )


def render_draft_complete_banner(state: DraftState, *, league_name: str, season_year: int) -> None:
    if not is_draft_complete(state):
        return

    st.markdown("## \U0001F3C6 CONGRATULATIONS! \U0001F3C6")
    st.markdown(
        f"### THE {season_year} {league_name} DRAFT IS COMPLETE"
    )
    st.success(
        "The rosters are set and the chase for the championship "
        "begins. Good luck this season\u2014may your starters stay "
        "healthy and your waiver claims clear! \U0001F3C8"
    )


def _fmt_seconds_hhmmss(total_seconds: float) -> str:
    total_seconds = max(0, int(round(float(total_seconds))))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"



def _fmt_hours_minutes(total_seconds: float) -> str:
    total_seconds = max(0, int(round(float(total_seconds))))
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60

    if hours <= 0:
        return f"{minutes}m"
    if minutes <= 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def _fmt_projected_completion_date(projected_dt: datetime | None) -> str:
    if projected_dt is None:
        return "—"
    return projected_dt.date().isoformat()


def _required_picks_per_day_to_target(
    *,
    total_picks_remaining: int,
    target_completion_date: date | None,
    today_date: date,
) -> tuple[float | None, bool]:
    if target_completion_date is None:
        return None, False

    if total_picks_remaining <= 0:
        return 0.0, False

    days_available = (target_completion_date - today_date).days + 1
    if days_available <= 0:
        return None, True

    return float(total_picks_remaining) / float(days_available), False


def _fmt_required_picks_per_day(value: float | None, *, overdue: bool) -> str:
    if overdue:
        return "Overdue"
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _render_draft_kpis(
    *,
    total_pick_slots: int,
    total_picks_remaining: int,
    avg_picks_per_day: float | None,
    required_picks_per_day: float | None,
    required_picks_overdue: bool,
    target_completion_date: date | None,
    projected_completion_date: datetime | None,
    avg_seconds_per_pick: float | None,
) -> None:
    kpi_cols = st.columns(6)
    kpi_cols[0].metric("Live Picks", f"{int(total_pick_slots):,}")
    kpi_cols[1].metric(
        "Live Picks Remaining",
        f"{int(total_picks_remaining):,}",
    )
    kpi_cols[2].metric(
        "Avg Picks / Day",
        "—" if avg_picks_per_day is None else f"{float(avg_picks_per_day):.2f}",
    )
    kpi_cols[3].metric(
        "Req Picks / Day",
        _fmt_required_picks_per_day(required_picks_per_day, overdue=required_picks_overdue),
    )
    kpi_cols[4].metric("Projected Complete", _fmt_projected_completion_date(projected_completion_date))
    kpi_cols[5].metric(
        "Avg Time / Pick",
        "—" if avg_seconds_per_pick is None else _fmt_hours_minutes(float(avg_seconds_per_pick)),
    )

    target_label = "—" if target_completion_date is None else target_completion_date.isoformat()
    st.caption(
        f"Target finish date: {target_label}. "
        "Required picks/day uses calendar days remaining through the target finish date. "
        "Average time per pick uses elapsed wall-clock time between completed picks."
    )


def render_draft_statistics_tab(
    state: DraftState,
    *,
    dsn: str,
    draft_key: str,
) -> None:
    st.subheader("Draft Statistics")

    opening_day_fallback_date = (
        parse_opening_day_date_from_env()
    )
    draft_start_fallback_date = (
        parse_draft_start_date_from_env()
    )

    (
        saved_draft_start_date,
        saved_completion_target_date,
        schedule_load_error,
    ) = _load_statistics_schedule_dates(dsn)

    draft_start_date = (
        saved_draft_start_date
        or draft_start_fallback_date
    )

    completion_target_date = (
        saved_completion_target_date
    )

    if (
        completion_target_date is None
        and opening_day_fallback_date is not None
    ):
        completion_target_date = (
            opening_day_fallback_date
            - timedelta(days=1)
        )

    pace_as_of_date = max(
        date.today(),
        draft_start_date,
    )

    if schedule_load_error:
        st.warning(
            "Draft schedule could not be loaded from PostgreSQL. "
            "Draft Statistics is using fallback dates. "
            f"Details: {schedule_load_error}"
        )
    elif (
        saved_draft_start_date is None
        or saved_completion_target_date is None
    ):
        st.caption(
            "Draft schedule is incomplete. Draft Statistics is "
            "using fallback dates until the Commissioner saves "
            "all four schedule events."
        )

    real_picks = _real_pick_rows(state)

    canonical = _load_canonical_draft_counts(dsn, draft_key)

    completed_picks = canonical["completed_live_picks"]
    total_picks_remaining = canonical["live_picks_remaining"]
    total_pick_slots = completed_picks + total_picks_remaining

    st.caption(
        f"Board slots: {canonical['board_slots']:,} | "
        f"Contract reserved: {canonical['contract_slots']:,} | "
        f"FT reserved: {canonical['ft_slots']:,} | "
        f"Live selections: {total_pick_slots:,}"
    )

    if completed_picks != len(real_picks):
        st.warning(
            "Draft Statistics state is temporarily out of sync with "
            "canonical PostgreSQL pick history. Refresh the page."
        )

    required_picks_per_day_to_target, required_picks_overdue = _required_picks_per_day_to_target(
        total_picks_remaining=total_picks_remaining,
        target_completion_date=completion_target_date,
        today_date=pace_as_of_date,
    )

    if not real_picks:
        _render_draft_kpis(
            total_pick_slots=total_pick_slots,
            total_picks_remaining=total_picks_remaining,
            avg_picks_per_day=None,
            required_picks_per_day=required_picks_per_day_to_target,
            required_picks_overdue=required_picks_overdue,
            target_completion_date=completion_target_date,
            projected_completion_date=None,
            avg_seconds_per_pick=None,
        )
        st.info("No real picks have been made yet.")
        return

    first_pick_ts = real_picks[0]["selected_ts"]
    latest_pick_ts = real_picks[-1]["selected_ts"]
    first_pick_date = first_pick_ts.date()
    last_pick_date = latest_pick_ts.date()

    now_for_projection = datetime.now(tz=latest_pick_ts.tzinfo) if latest_pick_ts.tzinfo else datetime.now()
    elapsed_days_for_kpi = max(1, (now_for_projection.date() - first_pick_date).days + 1)
    avg_picks_per_day_kpi = float(completed_picks) / float(elapsed_days_for_kpi)

    elapsed_seconds_first_to_latest = max(0.0, (latest_pick_ts - first_pick_ts).total_seconds())
    avg_seconds_per_pick_kpi = elapsed_seconds_first_to_latest / float(max(completed_picks - 1, 1))

    projected_completion_date = None
    if total_picks_remaining == 0:
        projected_completion_date = latest_pick_ts
    elif avg_picks_per_day_kpi > 0:
        projected_completion_date = now_for_projection + timedelta(
            days=float(total_picks_remaining) / float(avg_picks_per_day_kpi)
        )

    _render_draft_kpis(
        total_pick_slots=total_pick_slots,
        total_picks_remaining=total_picks_remaining,
        avg_picks_per_day=avg_picks_per_day_kpi,
        required_picks_per_day=required_picks_per_day_to_target,
        required_picks_overdue=required_picks_overdue,
        target_completion_date=completion_target_date,
        projected_completion_date=projected_completion_date,
        avg_seconds_per_pick=avg_seconds_per_pick_kpi,
    )

    chart_end_date = last_pick_date
    if completion_target_date is not None and completion_target_date > chart_end_date:
        chart_end_date = completion_target_date

    chart_dates = pd.date_range(first_pick_date, chart_end_date, freq="D")
    daily_pick_counts: dict[date, int] = {}
    for row in real_picks:
        d = row["selected_ts"].date()
        daily_pick_counts[d] = daily_pick_counts.get(d, 0) + 1

    actual_daily = pd.Series(
        [daily_pick_counts.get(d.date(), 0) for d in chart_dates],
        index=chart_dates,
        dtype="float64",
    )
    actual_cumulative = actual_daily.cumsum()

    chart_df = pd.DataFrame(index=chart_dates)
    chart_df["Actual Cumulative Picks"] = actual_cumulative

    if completion_target_date is not None and completion_target_date >= first_pick_date:
        total_days_inclusive = (completion_target_date - first_pick_date).days + 1
        required_values: list[float] = []
        for d in chart_dates:
            day_num = (d.date() - first_pick_date).days + 1
            if d.date() <= completion_target_date:
                required_values.append(
                    min(float(total_pick_slots), float(total_pick_slots) * float(day_num) / float(total_days_inclusive))
                )
            else:
                required_values.append(float(total_pick_slots))
        chart_df["Required Pace to Completion Deadline"] = required_values
        st.caption(f"Completion target: {completion_target_date.isoformat()}")
    else:
        st.info(
            "Required pace line is hidden because the draft completion deadline is missing/invalid or earlier than the first recorded pick date."
        )

    elapsed_days_inclusive = max(1, (last_pick_date - first_pick_date).days + 1)
    avg_picks_per_day = float(len(real_picks)) / float(elapsed_days_inclusive)

    projected_values: list[float] = []
    for d in chart_dates:
        day_num = (d.date() - first_pick_date).days + 1
        projected_values.append(
            min(float(total_pick_slots), float(avg_picks_per_day) * float(max(day_num, 0)))
        )
    chart_df["Actual Pace (Avg Trend)"] = projected_values

    st.line_chart(chart_df, use_container_width=True)

    st.caption(
        f"Draft window: {first_pick_date.isoformat()} → {last_pick_date.isoformat()} | "
        f"Real picks: {len(real_picks)} / {total_pick_slots}"
    )
    st.caption(
        "Actual Pace (Avg Trend) is a projected cumulative line using the draft's observed average picks per day so far."
    )

    team_stats: dict[str, dict[str, float]] = {}
    for team_key in state.teams.keys():
        team_stats[str(team_key)] = {
            "picks_made": 0.0,
            "cumulative_seconds": 0.0,
            "rank_sum": 0.0,
            "rank_count": 0.0,
        }

    prev_ts: datetime | None = None
    for row in real_picks:
        team_key = str(row["owner_team_key"])
        if team_key not in team_stats:
            team_stats[team_key] = {
                "picks_made": 0.0,
                "cumulative_seconds": 0.0,
                "rank_sum": 0.0,
                "rank_count": 0.0,
            }

        elapsed_seconds = 0.0 if prev_ts is None else max(
            0.0, (row["selected_ts"] - prev_ts).total_seconds()
        )

        team_stats[team_key]["picks_made"] += 1.0
        team_stats[team_key]["cumulative_seconds"] += elapsed_seconds

        player = state.players.get(row["player_key"])
        rank_value = getattr(player, "rank_value", None) if player is not None else None
        if isinstance(rank_value, (int, float)):
            team_stats[team_key]["rank_sum"] += float(rank_value)
            team_stats[team_key]["rank_count"] += 1.0

        prev_ts = row["selected_ts"]

    rows: list[dict[str, Any]] = []
    for team_key in sorted(state.teams.keys(), key=lambda k: state.teams[k].name):
        rec = team_stats.get(str(team_key), {})
        picks_made = int(rec.get("picks_made", 0.0) or 0.0)
        cumulative_seconds = float(rec.get("cumulative_seconds", 0.0) or 0.0)
        avg_seconds = cumulative_seconds / picks_made if picks_made else 0.0

        rank_count = float(rec.get("rank_count", 0.0) or 0.0)
        avg_rank = (float(rec.get("rank_sum", 0.0) or 0.0) / rank_count) if rank_count else None

        rows.append(
            {
                "Team": state.teams[team_key].name,
                "Picks Made": picks_made,
                "Average Wall-Clock / Pick": _fmt_seconds_hhmmss(avg_seconds),
                "Cumulative Wall-Clock": _fmt_seconds_hhmmss(cumulative_seconds),
                "Average Current Rank": None if avg_rank is None else round(avg_rank, 2),
            }
        )

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "Wall-clock timing is measured as elapsed time between consecutive real picks. "
        "The first recorded pick is treated as 00:00:00 elapsed because no pre-draft start timestamp is stored."
    )
