from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from draftboard.data.db_players import (
    load_available_players,
)
from draftboard.state.runtime import (
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


_REFRESH_LOCK = threading.Lock()

_YAHOO_LOADER = Path(
    "/app/scripts/yahoo/yahoo_bulk_load.py"
)


class YahooRefreshBusy(RuntimeError):
    pass


class YahooRefreshFailure(RuntimeError):
    pass


def _available_player_count(
    dsn: str,
) -> int:
    players = load_available_players(dsn)

    return len(players or {})


def _recent_meta_count(
    *,
    dsn: str,
    game_key: str,
) -> int:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM public.yahoo_player_meta
                WHERE source_game_key = %s
                  AND updated_at >= (
                      now() - interval '10 minutes'
                  )
                """,
                (game_key,),
            )
            row = cur.fetchone()

    return (
        int(row[0])
        if row and row[0] is not None
        else 0
    )


def refresh_yahoo_player_universe(
) -> dict[str, object]:
    if not _REFRESH_LOCK.acquire(
        blocking=False
    ):
        raise YahooRefreshBusy(
            "Yahoo player-universe refresh "
            "is already running."
        )

    try:
        dsn = str(
            get_postgres_dsn() or ""
        ).strip()

        if not dsn:
            raise YahooRefreshFailure(
                "PostgreSQL DSN is unavailable."
            )

        if not _YAHOO_LOADER.is_file():
            raise YahooRefreshFailure(
                "Yahoo loader is unavailable."
            )

        league_key = str(
            get_league_key() or ""
        ).strip()

        season_year = int(
            get_season_year()
        )

        game_key = (
            league_key.split(".", 1)[0]
            if league_key
            else ""
        )

        if not league_key or not game_key:
            raise YahooRefreshFailure(
                "Yahoo league context is unavailable."
            )

        stats_season = season_year - 1

        players_before = (
            _available_player_count(dsn)
        )

        env = os.environ.copy()

        env["YAHOO_LEAGUE_KEY"] = league_key
        env["YAHOO_GAME_KEY"] = game_key

        # yahoo_bulk_load.py uses this variable for
        # prior-year statistical context.
        env["YAHOO_STATS_SEASON"] = str(
            stats_season
        )

        # Retain the legacy variable for any helper
        # code that still reads it.
        env["YAHOO_SEASON_YEAR"] = str(
            season_year
        )

        env["YAHOO_WRITE_RAW"] = "0"

        started = time.monotonic()

        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(_YAHOO_LOADER),
                ],
                cwd="/app",
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=1800,
            )
        except subprocess.TimeoutExpired as exc:
            raise YahooRefreshFailure(
                "Yahoo refresh timed out."
            ) from exc
        except Exception as exc:
            raise YahooRefreshFailure(
                "Yahoo refresh could not start."
            ) from exc

        duration_sec = round(
            time.monotonic() - started,
            2,
        )

        if completed.returncode != 0:
            raise YahooRefreshFailure(
                "Yahoo loader returned a "
                "non-zero exit status."
            )

        players_after = (
            _available_player_count(dsn)
        )

        meta_updated_last_10m = (
            _recent_meta_count(
                dsn=dsn,
                game_key=game_key,
            )
        )

        return {
            "finished_at_utc": (
                datetime.now(timezone.utc)
                .isoformat()
            ),
            "duration_sec": duration_sec,
            "players_before": players_before,
            "players_after": players_after,
            "meta_updated_last_10m":
                meta_updated_last_10m,
            "stats_season": stats_season,
        }
    finally:
        _REFRESH_LOCK.release()
