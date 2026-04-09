from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pandas as pd
import streamlit as st

from draftboard.state.store import DraftState


def parse_opening_day_date_from_env() -> date | None:
    raw = str(os.environ.get("DRAFTBOARD_OPENING_DAY_DATE", "") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


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


def is_draft_complete(state: DraftState) -> bool:
    total_pick_slots = len(list(state.pick_order or []))
    if total_pick_slots == 0:
        return False
    return len(_real_pick_rows(state)) == total_pick_slots


def render_draft_complete_banner(state: DraftState, *, league_name: str, season_year: int) -> None:
    if not is_draft_complete(state):
        return

    st.success(
        f"Congratulations! The {season_year} {league_name} Draft has concluded. "
        f"Best of luck to everyone this season."
    )


def _fmt_seconds_hhmmss(total_seconds: float) -> str:
    total_seconds = max(0, int(round(float(total_seconds))))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def render_draft_statistics_tab(state: DraftState) -> None:
    st.subheader("Draft Statistics")

    opening_day_date = parse_opening_day_date_from_env()
    real_picks = _real_pick_rows(state)
    if not real_picks:
        st.info("No real picks have been made yet.")
        return

    total_pick_slots = len(list(state.pick_order or []))
    first_pick_date = real_picks[0]["selected_ts"].date()
    last_pick_date = real_picks[-1]["selected_ts"].date()

    chart_end_date = last_pick_date
    if opening_day_date is not None and opening_day_date > chart_end_date:
        chart_end_date = opening_day_date

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

    if opening_day_date is not None and opening_day_date >= first_pick_date:
        total_days_inclusive = (opening_day_date - first_pick_date).days + 1
        required_values: list[float] = []
        for d in chart_dates:
            day_num = (d.date() - first_pick_date).days + 1
            if d.date() <= opening_day_date:
                required_values.append(
                    min(float(total_pick_slots), float(total_pick_slots) * float(day_num) / float(total_days_inclusive))
                )
            else:
                required_values.append(float(total_pick_slots))
        chart_df["Required Pace to Opening Day"] = required_values
        st.caption(f"Opening Day target: {opening_day_date.isoformat()}")
    else:
        st.info(
            "Required pace line is hidden because DRAFTBOARD_OPENING_DAY_DATE is missing/invalid or earlier than the first recorded pick date."
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
