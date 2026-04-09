from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ClockStatus:
    is_running: bool
    seconds_per_pick: int
    elapsed_seconds: int
    remaining_seconds: int


def _parse_iso_utc(ts_iso: str) -> datetime:
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_clock_status(
    *,
    is_running: bool,
    seconds_per_pick: int,
    started_ts_iso: str | None,
    paused_ts_iso: str | None,
    elapsed_paused_seconds: int = 0,
    now_utc: datetime | None = None,
) -> ClockStatus:
    """
    Correct pause behavior:
    - elapsed_paused_seconds stores total elapsed time accumulated when paused.
    - While paused, elapsed stays constant (elapsed_paused_seconds).
    - While running, elapsed = elapsed_paused_seconds + (now - started).
    Note: started_ts_iso is the moment the CURRENT running segment began.
    """
    if seconds_per_pick <= 0:
        seconds_per_pick = 24 * 60 * 60

    if started_ts_iso is None:
        return ClockStatus(
            is_running=False,
            seconds_per_pick=seconds_per_pick,
            elapsed_seconds=0,
            remaining_seconds=seconds_per_pick,
        )

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    start = _parse_iso_utc(started_ts_iso)

    # If paused, freeze elapsed at accumulated value
    if paused_ts_iso is not None:
        elapsed = max(0, int(elapsed_paused_seconds))
        remaining = max(0, seconds_per_pick - elapsed)
        return ClockStatus(
            is_running=False,
            seconds_per_pick=seconds_per_pick,
            elapsed_seconds=elapsed,
            remaining_seconds=remaining,
        )

    # Running: accumulated + current segment
    seg_elapsed = int(max(0.0, (now_utc - start).total_seconds()))
    elapsed = max(0, int(elapsed_paused_seconds) + seg_elapsed)
    remaining = max(0, seconds_per_pick - elapsed)

    return ClockStatus(
        is_running=bool(is_running),
        seconds_per_pick=seconds_per_pick,
        elapsed_seconds=elapsed,
        remaining_seconds=remaining,
    )


def start_pick_clock(now_utc: datetime | None = None) -> str:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return now_utc.replace(tzinfo=None).isoformat(timespec="seconds")
