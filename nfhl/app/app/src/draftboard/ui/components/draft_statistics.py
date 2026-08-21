from __future__ import annotations

import os

from datetime import (
    date,
    datetime,
    timedelta,
)

from typing import Any

import pandas as pd
import streamlit as st
from zoneinfo import ZoneInfo

from draftboard.data.db import (
    get_live_draft_board,
    get_live_draft_state,
)

from draftboard.data.season_team_slots import (
    get_season_team_slots,
)


NFHL_OPENING_DAY_FALLBACK = date(
    2026,
    9,
    29,
)


def _opening_day() -> date:
    raw = str(
        os.environ.get(
            "DRAFTBOARD_OPENING_DAY_DATE"
        )
        or ""
    ).strip()

    if raw:
        try:
            return date.fromisoformat(
                raw
            )
        except Exception:
            pass

    return (
        NFHL_OPENING_DAY_FALLBACK
    )


def _target_completion_date() -> date:
    return (
        _opening_day()
        - timedelta(
            days=1
        )
    )


def _as_datetime(
    value: object,
) -> datetime | None:
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value

    try:
        return datetime.fromisoformat(
            str(
                value
            )
        )
    except Exception:
        return None


def _fmt_hours_minutes(
    seconds: float | None,
) -> str:
    if seconds is None:
        return "—"

    total = max(
        0,
        int(
            round(
                float(
                    seconds
                )
            )
        ),
    )

    hours, rem = divmod(
        total,
        3600,
    )

    minutes = (
        rem // 60
    )

    if hours <= 0:
        return (
            f"{minutes}m"
        )

    if minutes <= 0:
        return (
            f"{hours}h"
        )

    return (
        f"{hours}h {minutes}m"
    )


def _fmt_hhmmss(
    seconds: float,
) -> str:
    total = max(
        0,
        int(
            round(
                float(
                    seconds
                )
            )
        ),
    )

    hours, rem = divmod(
        total,
        3600,
    )

    minutes, secs = divmod(
        rem,
        60,
    )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d}"
    )


def _player_rank_lookup(
    players: list[dict],
) -> dict[str, float]:
    result: dict[
        str,
        float,
    ] = {}

    for player in players:
        key = str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ).strip()

        rank = player.get(
            "rank_value"
        )

        if (
            not key
            or rank is None
        ):
            continue

        try:
            result[key] = (
                float(
                    rank
                )
            )
        except Exception:
            continue

    return result


def _preview_team_names() -> list[str]:
    try:
        slots = (
            get_season_team_slots()
        )
    except Exception:
        return []

    names: list[str] = []

    for slot in slots:
        name = str(
            slot.get(
                "current_team_name"
            )
            or slot.get(
                "replacement_team_name"
            )
            or slot.get(
                "prior_team_name"
            )
            or ""
        ).strip()

        if name:
            names.append(
                name
            )

    return names


def _render_kpis(
    *,
    total_pick_slots: int,
    total_picks_remaining: int,
    avg_picks_per_day: float | None,
    required_picks_per_day: float | None,
    projected_completion_date: datetime | None,
    avg_seconds_per_pick: float | None,
) -> None:
    cols = st.columns(
        6
    )

    cols[0].metric(
        "Live Picks",
        f"{total_pick_slots:,}",
    )

    cols[1].metric(
        "Live Picks Remaining",
        f"{total_picks_remaining:,}",
    )

    cols[2].metric(
        "Avg Picks / Day",
        (
            "—"
            if avg_picks_per_day
            is None
            else f"{avg_picks_per_day:.2f}"
        ),
    )

    cols[3].metric(
        "Req Picks / Day",
        (
            "—"
            if required_picks_per_day
            is None
            else f"{required_picks_per_day:.2f}"
        ),
    )

    cols[4].metric(
        "Projected Complete",
        (
            "—"
            if projected_completion_date
            is None
            else projected_completion_date
            .date()
            .isoformat()
        ),
    )

    cols[5].metric(
        "Avg Time / Pick",
        _fmt_hours_minutes(
            avg_seconds_per_pick
        ),
    )



# NFHL_PREP_PACE_GRAPH_START
def _render_preview_pace_chart(
    *,
    total_pick_slots: int,
    target_date: date,
) -> None:
    """
    Render the same cumulative pace-chart shape used during the live
    draft, using deterministic in-memory PREP data only.

    Nothing here is persisted or treated as an actual draft result.
    """
    preview_days = 10

    start_date = (
        target_date
        - timedelta(
            days=preview_days - 1
        )
    )

    chart_dates = pd.date_range(
        start_date,
        target_date,
        freq="D",
    )

    # Deterministic representative curve solely for visual approval.
    progress_fractions = [
        0.00,
        0.08,
        0.19,
        0.28,
        0.41,
        0.52,
        0.64,
        0.75,
        0.89,
        1.00,
    ]

    actual_values = [
        round(
            float(total_pick_slots)
            * fraction,
            2,
        )
        for fraction
        in progress_fractions
    ]

    required_values = [
        round(
            float(total_pick_slots)
            * float(day_number)
            / float(preview_days),
            2,
        )
        for day_number
        in range(
            1,
            preview_days + 1,
        )
    ]

    observed_rate = (
        float(actual_values[-2])
        / float(
            preview_days - 1
        )
    )

    trend_values = [
        min(
            float(total_pick_slots),
            round(
                observed_rate
                * float(day_number),
                2,
            ),
        )
        for day_number
        in range(
            1,
            preview_days + 1,
        )
    ]

    chart_df = pd.DataFrame(
        {
            "Actual Cumulative Picks":
                actual_values,

            "Required Pace to Completion Deadline":
                required_values,

            "Actual Pace (Avg Trend)":
                trend_values,
        },
        index=chart_dates,
    )

    st.caption(
        "PRE-DRAFT GRAPH PREVIEW — The values below are "
        "representative only. The live chart will use actual "
        "NFHL selections and the saved draft schedule."
    )

    st.line_chart(
        chart_df,
        use_container_width=True,
    )

    st.caption(
        "Live behavior: Actual Cumulative Picks tracks completed "
        "selections; Required Pace tracks the completion deadline; "
        "Actual Pace (Avg Trend) projects the observed drafting rate."
    )


# NFHL_PREP_PACE_GRAPH_END



def render_draft_statistics(
    *,
    players: list[dict],
) -> None:
    st.subheader(
        "Draft Statistics"
    )

    target_date = (
        _target_completion_date()
    )

    try:
        live_state = (
            get_live_draft_state()
        )

        board_rows = (
            get_live_draft_board()
        )

    except Exception as exc:
        st.error(
            "Could not load Draft Statistics: "
            f"{exc}"
        )
        return

    draft_status = str(
        live_state.get(
            "status"
        )
        or ""
    ).upper()

    if board_rows:
        total_pick_slots = (
            len(
                board_rows
            )
        )

    else:
        total_pick_slots = (
            14 * 18
        )

    completed_rows: list[
        dict[str, Any]
    ] = []

    for row in board_rows:
        if (
            not row.get(
                "yahoo_player_key"
            )
            or not row.get(
                "selected_at_utc"
            )
        ):
            continue

        selected_ts = (
            _as_datetime(
                row.get(
                    "selected_at_utc"
                )
            )
        )

        if selected_ts is None:
            continue

        record = dict(
            row
        )

        record[
            "_selected_ts"
        ] = selected_ts

        completed_rows.append(
            record
        )

    completed_rows.sort(
        key=lambda row: (
            row[
                "_selected_ts"
            ],
            int(
                row.get(
                    "round_number"
                )
                or 0
            ),
            int(
                row.get(
                    "slot_number"
                )
                or 0
            ),
        )
    )

    completed_picks = (
        len(
            completed_rows
        )
    )

    total_remaining = max(
        0,
        total_pick_slots
        - completed_picks,
    )

    avg_picks_per_day = None
    avg_seconds_per_pick = None
    projected_completion = None
    required_picks_per_day = None

    if completed_rows:
        first_ts = (
            completed_rows[0][
                "_selected_ts"
            ]
        )

        latest_ts = (
            completed_rows[-1][
                "_selected_ts"
            ]
        )

        latest_tz = (
            latest_ts.tzinfo
        )

        now = (
            datetime.now(
                tz=latest_tz
            )
            if latest_tz
            else datetime.now()
        )

        elapsed_days = max(
            1,
            (
                now.date()
                - first_ts.date()
            ).days
            + 1,
        )

        avg_picks_per_day = (
            float(
                completed_picks
            )
            / float(
                elapsed_days
            )
        )

        elapsed_seconds = max(
            0.0,
            (
                latest_ts
                - first_ts
            ).total_seconds(),
        )

        if completed_picks > 1:
            avg_seconds_per_pick = (
                elapsed_seconds
                / float(
                    completed_picks
                    - 1
                )
            )

        else:
            avg_seconds_per_pick = (
                0.0
            )

        if (
            total_remaining <= 0
        ):
            projected_completion = (
                latest_ts
            )

        elif (
            avg_picks_per_day
            and avg_picks_per_day > 0
        ):
            projected_completion = (
                now
                + timedelta(
                    days=(
                        float(
                            total_remaining
                        )
                        / avg_picks_per_day
                    )
                )
            )

        days_to_target = (
            target_date
            - now.date()
        ).days + 1

        if days_to_target > 0:
            required_picks_per_day = (
                float(
                    total_remaining
                )
                / float(
                    days_to_target
                )
            )

    _render_kpis(
        total_pick_slots=(
            total_pick_slots
        ),
        total_picks_remaining=(
            total_remaining
        ),
        avg_picks_per_day=(
            avg_picks_per_day
        ),
        required_picks_per_day=(
            required_picks_per_day
        ),
        projected_completion_date=(
            projected_completion
        ),
        avg_seconds_per_pick=(
            avg_seconds_per_pick
        ),
    )

    st.caption(
        "Target finish date: "
        f"{target_date.isoformat()}. "
        "Average time per pick uses elapsed wall-clock time "
        "between completed selections."
    )

    # ============================================================
    # PREP / NO PICKS YET
    # ============================================================

    if not completed_rows:
        st.info(
            "No real picks have been made yet."
        )

        if draft_status == "PREP":
            st.caption(
                "PRE-DRAFT PREVIEW — The statistic layout is live, "
                "but pace and timing KPI values intentionally remain "
                "blank until actual selections exist."
            )

            _render_preview_pace_chart(
                total_pick_slots=(
                    total_pick_slots
                ),
                target_date=(
                    target_date
                ),
            )

        team_names = (
            _preview_team_names()
        )

        if team_names:
            preview_rows = [
                {
                    "Team": name,
                    "Picks Made": 0,
                    "Average Wall-Clock / Pick": "—",
                    "Cumulative Wall-Clock": "00:00:00",
                    "Average Current Rank": None,
                }
                for name
                in team_names
            ]

            st.dataframe(
                pd.DataFrame(
                    preview_rows
                ),
                hide_index=True,
                use_container_width=True,
            )

        return

    # ============================================================
    # PACE CHART
    # ============================================================

    first_ts = (
        completed_rows[0][
            "_selected_ts"
        ]
    )

    latest_ts = (
        completed_rows[-1][
            "_selected_ts"
        ]
    )

    first_date = (
        first_ts.date()
    )

    latest_date = (
        latest_ts.date()
    )

    chart_end_date = max(
        latest_date,
        target_date,
    )

    chart_dates = (
        pd.date_range(
            first_date,
            chart_end_date,
            freq="D",
        )
    )

    daily_counts: dict[
        date,
        int,
    ] = {}

    for row in completed_rows:
        day = (
            row[
                "_selected_ts"
            ].date()
        )

        daily_counts[
            day
        ] = (
            daily_counts.get(
                day,
                0,
            )
            + 1
        )

    actual_daily = pd.Series(
        [
            daily_counts.get(
                dt.date(),
                0,
            )
            for dt
            in chart_dates
        ],
        index=chart_dates,
        dtype="float64",
    )

    chart_df = pd.DataFrame(
        index=chart_dates
    )

    chart_df[
        "Actual Cumulative Picks"
    ] = (
        actual_daily.cumsum()
    )

    if (
        target_date
        >= first_date
    ):
        total_days = (
            target_date
            - first_date
        ).days + 1

        required_values: list[
            float
        ] = []

        for dt in chart_dates:
            day_number = (
                dt.date()
                - first_date
            ).days + 1

            if (
                dt.date()
                <= target_date
            ):
                required_values.append(
                    min(
                        float(
                            total_pick_slots
                        ),
                        float(
                            total_pick_slots
                        )
                        * float(
                            day_number
                        )
                        / float(
                            total_days
                        ),
                    )
                )

            else:
                required_values.append(
                    float(
                        total_pick_slots
                    )
                )

        chart_df[
            "Required Pace to Completion Deadline"
        ] = required_values

    elapsed_days = max(
        1,
        (
            latest_date
            - first_date
        ).days + 1,
    )

    observed_rate = (
        float(
            completed_picks
        )
        / float(
            elapsed_days
        )
    )

    projected_values: list[
        float
    ] = []

    for dt in chart_dates:
        day_number = (
            dt.date()
            - first_date
        ).days + 1

        projected_values.append(
            min(
                float(
                    total_pick_slots
                ),
                observed_rate
                * float(
                    max(
                        day_number,
                        0,
                    )
                ),
            )
        )

    chart_df[
        "Actual Pace (Avg Trend)"
    ] = projected_values

    st.line_chart(
        chart_df,
        use_container_width=True,
    )

    st.caption(
        "Draft window: "
        f"{first_date.isoformat()} → "
        f"{latest_date.isoformat()} | "
        f"Real picks: {completed_picks} / "
        f"{total_pick_slots}"
    )

    # ============================================================
    # TEAM STATISTICS
    # ============================================================

    rank_lookup = (
        _player_rank_lookup(
            players
        )
    )

    team_stats: dict[
        str,
        dict[str, Any],
    ] = {}

    previous_ts: (
        datetime | None
    ) = None

    for row in completed_rows:
        team_name = str(
            row.get(
                "current_owner_team_name"
            )
            or "Unknown"
        )

        rec = team_stats.setdefault(
            team_name,
            {
                "picks_made": 0,
                "cumulative_seconds": 0.0,
                "rank_sum": 0.0,
                "rank_count": 0,
            },
        )

        elapsed = (
            0.0
            if previous_ts is None
            else max(
                0.0,
                (
                    row[
                        "_selected_ts"
                    ]
                    - previous_ts
                ).total_seconds(),
            )
        )

        rec[
            "picks_made"
        ] += 1

        rec[
            "cumulative_seconds"
        ] += elapsed

        player_key = str(
            row.get(
                "yahoo_player_key"
            )
            or ""
        )

        rank = (
            rank_lookup.get(
                player_key
            )
        )

        if rank is not None:
            rec[
                "rank_sum"
            ] += rank

            rec[
                "rank_count"
            ] += 1

        previous_ts = (
            row[
                "_selected_ts"
            ]
        )

    all_team_names = {
        str(
            row.get(
                "column_team_name"
            )
            or ""
        )
        for row in board_rows
        if row.get(
            "column_team_name"
        )
    }

    table_rows: list[
        dict[str, Any]
    ] = []

    for team_name in sorted(
        all_team_names
    ):
        rec = team_stats.get(
            team_name,
            {
                "picks_made": 0,
                "cumulative_seconds": 0.0,
                "rank_sum": 0.0,
                "rank_count": 0,
            },
        )

        picks_made = int(
            rec[
                "picks_made"
            ]
        )

        cumulative = float(
            rec[
                "cumulative_seconds"
            ]
        )

        avg_seconds = (
            cumulative
            / float(
                picks_made
            )
            if picks_made
            else 0.0
        )

        rank_count = int(
            rec[
                "rank_count"
            ]
        )

        avg_rank = (
            float(
                rec[
                    "rank_sum"
                ]
            )
            / float(
                rank_count
            )
            if rank_count
            else None
        )

        table_rows.append(
            {
                "Team": team_name,
                "Picks Made": picks_made,
                "Average Wall-Clock / Pick": (
                    _fmt_hhmmss(
                        avg_seconds
                    )
                    if picks_made
                    else "—"
                ),
                "Cumulative Wall-Clock": (
                    _fmt_hhmmss(
                        cumulative
                    )
                ),
                "Average Current Rank": (
                    None
                    if avg_rank is None
                    else round(
                        avg_rank,
                        2,
                    )
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            table_rows
        ),
        hide_index=True,
        use_container_width=True,
    )

    st.caption(
        "Wall-clock timing is measured as elapsed time between "
        "consecutive real picks. The first recorded selection is "
        "treated as zero elapsed time."
    )
