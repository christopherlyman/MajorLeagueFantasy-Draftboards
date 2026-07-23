from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row


DEFAULT_SCHEDULE_TIMEZONE = "America/New_York"

DRAFT_LOTTERY_EVENT_CODE = "DRAFT_LOTTERY"
QO_FT_DEADLINE_EVENT_CODE = "QO_FT_SUBMISSION_DEADLINE"
DRAFT_START_EVENT_CODE = "DRAFT_START"
DRAFT_COMPLETION_DEADLINE_EVENT_CODE = (
    "DRAFT_COMPLETION_DEADLINE"
)

SCHEDULE_EVENT_CODES = (
    DRAFT_LOTTERY_EVENT_CODE,
    QO_FT_DEADLINE_EVENT_CODE,
    DRAFT_START_EVENT_CODE,
    DRAFT_COMPLETION_DEADLINE_EVENT_CODE,
)

SCHEDULE_EVENT_LABELS = {
    DRAFT_LOTTERY_EVENT_CODE: "Draft Lottery",
    QO_FT_DEADLINE_EVENT_CODE: "QO/FT Submission Deadline",
    DRAFT_START_EVENT_CODE: "Draft Start",
    DRAFT_COMPLETION_DEADLINE_EVENT_CODE: (
        "Draft Completion Deadline"
    ),
}


@dataclass(frozen=True, slots=True)
class LeagueScheduleEvent:
    event_code: str
    event_label: str
    event_date: date
    event_time_local: time | None
    timezone_name: str
    is_active: bool
    note: str | None


@dataclass(frozen=True, slots=True)
class DraftSchedule:
    league_key: str
    season_year: int
    events: Mapping[str, LeagueScheduleEvent]

    def active_event(
        self,
        event_code: str,
    ) -> LeagueScheduleEvent | None:
        event = self.events.get(str(event_code))
        if event is None or not event.is_active:
            return None
        return event

    def active_event_date(
        self,
        event_code: str,
    ) -> date | None:
        event = self.active_event(event_code)
        return None if event is None else event.event_date


def _load_draft_schedule_with_connection(
    conn: psycopg.Connection,
    league_key: str,
    season_year: int,
) -> DraftSchedule:
    """
    Load canonical schedule events using an existing connection.

    The caller owns the transaction and decides whether to commit or
    roll back.
    """
    sql = """
        SELECT
            event_code,
            event_label,
            event_date,
            event_time_local,
            timezone AS timezone_name,
            is_active,
            note
        FROM nffl.league_event
        WHERE league_key = %s
          AND season_year = %s
          AND event_code = ANY(%s)
        ORDER BY event_code
    """

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql,
            (
                str(league_key),
                int(season_year),
                list(SCHEDULE_EVENT_CODES),
            ),
        )
        rows = list(cur.fetchall())

    events: dict[str, LeagueScheduleEvent] = {}

    for row in rows:
        event_code = str(row["event_code"])

        if event_code in events:
            raise RuntimeError(
                "Duplicate canonical schedule event returned for "
                f"{league_key!r}, {season_year}, "
                f"{event_code!r}."
            )

        timezone_name = str(
            row["timezone_name"]
            or DEFAULT_SCHEDULE_TIMEZONE
        )

        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(
                "Invalid timezone stored for schedule event "
                f"{event_code!r}: {timezone_name!r}."
            ) from exc

        events[event_code] = LeagueScheduleEvent(
            event_code=event_code,
            event_label=str(row["event_label"]),
            event_date=row["event_date"],
            event_time_local=row["event_time_local"],
            timezone_name=timezone_name,
            is_active=bool(row["is_active"]),
            note=(
                None
                if row["note"] is None
                else str(row["note"])
            ),
        )

    return DraftSchedule(
        league_key=str(league_key),
        season_year=int(season_year),
        events=events,
    )


def load_draft_schedule(
    dsn: str,
    league_key: str,
    season_year: int,
) -> DraftSchedule:
    """
    Load the four canonical draft-schedule events for one league season.

    Inactive rows remain available in the returned event mapping, but
    active_event() and active_event_date() ignore them.
    """
    with psycopg.connect(dsn) as conn:
        return _load_draft_schedule_with_connection(
            conn,
            league_key,
            season_year,
        )


def validate_schedule_events(
    events: Mapping[str, LeagueScheduleEvent],
    *,
    require_all: bool = True,
) -> tuple[str, ...]:
    """
    Validate required events, timezone consistency, and chronology.

    Events on different dates do not require times for ordering.
    Events on the same date require both local times.
    """
    active_events = {
        code: event
        for code, event in events.items()
        if code in SCHEDULE_EVENT_CODES and event.is_active
    }

    errors: list[str] = []

    if require_all:
        for event_code in SCHEDULE_EVENT_CODES:
            if event_code not in active_events:
                errors.append(
                    "Missing active schedule event: "
                    f"{SCHEDULE_EVENT_LABELS[event_code]}."
                )

    timezone_names = {
        event.timezone_name
        for event in active_events.values()
    }

    for timezone_name in sorted(timezone_names):
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            errors.append(
                f"Invalid schedule timezone: {timezone_name!r}."
            )

    if len(timezone_names) > 1:
        errors.append(
            "All active schedule events must use the same timezone."
        )

    sequence = (
        (
            DRAFT_LOTTERY_EVENT_CODE,
            QO_FT_DEADLINE_EVENT_CODE,
        ),
        (
            QO_FT_DEADLINE_EVENT_CODE,
            DRAFT_START_EVENT_CODE,
        ),
        (
            DRAFT_START_EVENT_CODE,
            DRAFT_COMPLETION_DEADLINE_EVENT_CODE,
        ),
    )

    for earlier_code, later_code in sequence:
        earlier = active_events.get(earlier_code)
        later = active_events.get(later_code)

        if earlier is None or later is None:
            continue

        earlier_label = SCHEDULE_EVENT_LABELS[earlier_code]
        later_label = SCHEDULE_EVENT_LABELS[later_code]

        if earlier.event_date > later.event_date:
            errors.append(
                f"{earlier_label} must occur before {later_label}."
            )
            continue

        if earlier.event_date < later.event_date:
            continue

        if (
            earlier.event_time_local is None
            or later.event_time_local is None
        ):
            errors.append(
                f"{earlier_label} and {later_label} occur on the "
                "same date, so both require local times."
            )
            continue

        if earlier.event_time_local >= later.event_time_local:
            errors.append(
                f"{earlier_label} must occur before {later_label}."
            )

    return tuple(errors)


def _save_draft_schedule_with_connection(
    conn: psycopg.Connection,
    league_key: str,
    season_year: int,
    events: Mapping[str, LeagueScheduleEvent],
) -> DraftSchedule:
    """
    Validate and upsert a complete schedule in the caller's transaction.

    This helper does not commit. The caller owns commit or rollback.
    """
    input_errors: list[str] = []

    for event_code in SCHEDULE_EVENT_CODES:
        event = events.get(event_code)

        if event is None:
            continue

        if event.event_code != event_code:
            input_errors.append(
                "Schedule mapping key does not match event code: "
                f"key={event_code!r}, "
                f"event_code={event.event_code!r}."
            )

        if (
            event.event_time_local is not None
            and event.event_time_local.tzinfo is not None
        ):
            input_errors.append(
                f"{SCHEDULE_EVENT_LABELS[event_code]} local time "
                "must not contain timezone information."
            )

    validation_errors = (
        *input_errors,
        *validate_schedule_events(
            events,
            require_all=True,
        ),
    )

    if validation_errors:
        details = "\n".join(
            f"- {error}"
            for error in validation_errors
        )
        raise ValueError(
            "Draft schedule validation failed:\n"
            f"{details}"
        )

    sql = """
        INSERT INTO nffl.league_event (
            league_key,
            season_year,
            event_code,
            event_label,
            event_date,
            event_time_local,
            timezone,
            is_active,
            note
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            TRUE,
            %s
        )
        ON CONFLICT (
            league_key,
            season_year,
            event_code
        )
        DO UPDATE SET
            event_label = EXCLUDED.event_label,
            event_date = EXCLUDED.event_date,
            event_time_local = EXCLUDED.event_time_local,
            timezone = EXCLUDED.timezone,
            is_active = TRUE,
            note = EXCLUDED.note,
            updated_at_utc = now()
    """

    with conn.cursor() as cur:
        for event_code in SCHEDULE_EVENT_CODES:
            event = events[event_code]

            cur.execute(
                sql,
                (
                    str(league_key),
                    int(season_year),
                    event_code,
                    SCHEDULE_EVENT_LABELS[event_code],
                    event.event_date,
                    event.event_time_local,
                    event.timezone_name,
                    event.note,
                ),
            )

        cur.execute(
            """
            SELECT COUNT(*)
            FROM nffl.league_event
            WHERE league_key = %s
              AND season_year = %s
              AND event_code = ANY(%s)
              AND is_active = TRUE
            """,
            (
                str(league_key),
                int(season_year),
                list(SCHEDULE_EVENT_CODES),
            ),
        )

        row = cur.fetchone()
        active_count = int(
            0 if row is None else row[0] or 0
        )

        if active_count != len(SCHEDULE_EVENT_CODES):
            raise RuntimeError(
                "Schedule upsert integrity failed: "
                f"expected {len(SCHEDULE_EVENT_CODES)} "
                f"active events, found {active_count}."
            )

    saved_schedule = _load_draft_schedule_with_connection(
        conn,
        league_key,
        season_year,
    )

    saved_errors = validate_schedule_events(
        saved_schedule.events,
        require_all=True,
    )

    if saved_errors:
        details = "\n".join(
            f"- {error}"
            for error in saved_errors
        )
        raise RuntimeError(
            "Saved schedule failed pre-commit validation:\n"
            f"{details}"
        )

    return saved_schedule


def save_draft_schedule(
    dsn: str,
    league_key: str,
    season_year: int,
    events: Mapping[str, LeagueScheduleEvent],
) -> DraftSchedule:
    """
    Validate and transactionally upsert the complete draft schedule.

    The transaction commits only after the saved rows pass canonical
    reload and chronology validation. Any exception rolls back all four
    event changes.
    """
    with psycopg.connect(dsn) as conn:
        return _save_draft_schedule_with_connection(
            conn,
            league_key,
            season_year,
            events,
        )

