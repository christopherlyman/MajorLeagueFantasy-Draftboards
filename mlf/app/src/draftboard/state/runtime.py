from __future__ import annotations

import os


ENV_DRAFT_KEY = "DRAFTBOARD_DRAFT_KEY"
ENV_LEAGUE_KEY_PRIMARY = "LEAGUE_KEY"
ENV_LEAGUE_KEY_FALLBACK = "MLF_LEAGUE_KEY"
ENV_SEASON_YEAR_PRIMARY = "SEASON_YEAR"
ENV_SEASON_YEAR_FALLBACK = "MLF_SEASON_YEAR"
ENV_POSTGRES_DSN_PRIMARY = "POSTGRES_DSN"
ENV_POSTGRES_DSN_FALLBACK = "MLF_POSTGRES_DSN"


def get_draft_key() -> str:
    v = str(os.environ.get(ENV_DRAFT_KEY, "") or "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {ENV_DRAFT_KEY}")
    return v


def get_league_key() -> str:
    v = str(os.environ.get(ENV_LEAGUE_KEY_PRIMARY, "") or "").strip()
    if v:
        return v

    v = str(os.environ.get(ENV_LEAGUE_KEY_FALLBACK, "") or "").strip()
    if v:
        return v

    raise RuntimeError(
        f"Missing required env var: {ENV_LEAGUE_KEY_PRIMARY} or {ENV_LEAGUE_KEY_FALLBACK}"
    )


def get_season_year() -> int:
    raw = str(os.environ.get(ENV_SEASON_YEAR_PRIMARY, "") or "").strip()
    if not raw:
        raw = str(os.environ.get(ENV_SEASON_YEAR_FALLBACK, "") or "").strip()
    if not raw:
        raise RuntimeError(
            f"Missing required env var: {ENV_SEASON_YEAR_PRIMARY} or {ENV_SEASON_YEAR_FALLBACK}"
        )
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(
            f"Invalid {ENV_SEASON_YEAR_PRIMARY}/{ENV_SEASON_YEAR_FALLBACK}: {raw}"
        ) from e


def postgres_dsn() -> str:
    v = str(os.environ.get(ENV_POSTGRES_DSN_PRIMARY, "") or "").strip()
    if v:
        return v

    v = str(os.environ.get(ENV_POSTGRES_DSN_FALLBACK, "") or "").strip()
    if v:
        return v

    raise RuntimeError(
        f"Missing required env var: {ENV_POSTGRES_DSN_PRIMARY} or {ENV_POSTGRES_DSN_FALLBACK}"
    )


def get_postgres_dsn() -> str:
    return postgres_dsn()


def get_game_key_from_league_key(league_key: str | None = None) -> str:
    lk = str(league_key or get_league_key()).strip()
    parts = lk.split(".")
    if not parts or not parts[0]:
        raise RuntimeError(f"Could not derive game key from league key: {lk}")
    return parts[0]