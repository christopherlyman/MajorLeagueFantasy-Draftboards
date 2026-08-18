from __future__ import annotations

import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()

    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")

    return value


def get_draft_key() -> str:
    return _required("DRAFTBOARD_DRAFT_KEY")


def get_league_key() -> str:
    return _required("LEAGUE_KEY")


def get_season_year() -> int:
    raw = _required("SEASON_YEAR")

    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"SEASON_YEAR must be an integer; received {raw!r}"
        ) from exc


def get_postgres_dsn() -> str:
    return _required("POSTGRES_DSN")
