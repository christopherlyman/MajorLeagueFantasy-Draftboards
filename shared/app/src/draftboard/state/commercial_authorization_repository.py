from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CommercialAuthorizationRepositoryError(RuntimeError):
    """Raised when commercial authorization persistence invariants fail."""


@dataclass(frozen=True)
class StoredCommercialLeagueRole:
    user_id: int
    league_key: str
    season_year: int
    role_code: str
    active: bool
    created_at_utc: Any
    updated_at_utc: Any


_ROLE_COLUMNS = """
    user_id,
    league_key,
    season_year,
    role_code,
    active,
    created_at_utc,
    updated_at_utc
"""


def _positive_int(value: object, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CommercialAuthorizationRepositoryError(
            f"{field_name} must be a positive integer."
        ) from exc

    if parsed <= 0:
        raise CommercialAuthorizationRepositoryError(
            f"{field_name} must be a positive integer."
        )

    return parsed


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()

    if not text:
        raise CommercialAuthorizationRepositoryError(
            f"{field_name} must be non-empty."
        )

    return text


def _row_to_role(row: tuple | list) -> StoredCommercialLeagueRole:
    return StoredCommercialLeagueRole(
        user_id=int(row[0]),
        league_key=str(row[1]),
        season_year=int(row[2]),
        role_code=str(row[3]),
        active=bool(row[4]),
        created_at_utc=row[5],
        updated_at_utc=row[6],
    )


def grant_commercial_commissioner(
    connection,
    *,
    user_id: int,
    league_key: str,
    season_year: int,
) -> StoredCommercialLeagueRole:
    """
    Grant or reactivate commissioner access for one commercial league season.

    The caller must supply a server-resolved user_id. Browser-supplied
    ownership identity must not be treated as authoritative.
    """
    uid = _positive_int(user_id, "user_id")
    key = _required_text(league_key, "league_key")
    year = _positive_int(season_year, "season_year")

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM public.auth_user
                WHERE user_id = %s
                  AND active = true
                """,
                (uid,),
            )

            if cursor.fetchone() is None:
                raise CommercialAuthorizationRepositoryError(
                    f"Active auth_user user_id={uid} was not found."
                )

            cursor.execute(
                """
                SELECT is_active
                FROM public.league_profile
                WHERE league_key = %s
                  AND season_year = %s
                FOR UPDATE
                """,
                (key, year),
            )

            profile_row = cursor.fetchone()

            if profile_row is None or not bool(profile_row[0]):
                raise CommercialAuthorizationRepositoryError(
                    "Active commercial league profile was not found."
                )

            cursor.execute(
                f"""
                INSERT INTO public.commercial_league_user_role (
                    user_id,
                    league_key,
                    season_year,
                    role_code,
                    active
                )
                VALUES (%s, %s, %s, 'commissioner', true)
                ON CONFLICT (
                    user_id,
                    league_key,
                    season_year,
                    role_code
                )
                DO UPDATE SET
                    active = true,
                    updated_at_utc = now()
                RETURNING {_ROLE_COLUMNS}
                """,
                (uid, key, year),
            )

            row = cursor.fetchone()

            if row is None:
                raise CommercialAuthorizationRepositoryError(
                    "Commissioner role grant returned no row."
                )

        connection.commit()
        return _row_to_role(row)

    except Exception as exc:
        connection.rollback()

        if isinstance(exc, CommercialAuthorizationRepositoryError):
            raise

        raise CommercialAuthorizationRepositoryError(
            f"Failed to grant commercial commissioner role: {exc}"
        ) from exc


def load_commercial_commissioner_role(
    connection,
    *,
    user_id: int,
    league_key: str,
    season_year: int,
) -> StoredCommercialLeagueRole | None:
    uid = _positive_int(user_id, "user_id")
    key = _required_text(league_key, "league_key")
    year = _positive_int(season_year, "season_year")

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {_ROLE_COLUMNS}
            FROM public.commercial_league_user_role
            WHERE user_id = %s
              AND league_key = %s
              AND season_year = %s
              AND role_code = 'commissioner'
            """,
            (uid, key, year),
        )

        row = cursor.fetchone()

    return _row_to_role(row) if row else None


def can_administer_commercial_league(
    connection,
    *,
    user_id: int,
    league_key: str,
    season_year: int,
) -> bool:
    """
    Return True only when the user, role, and commercial profile are all active.
    """
    uid = _positive_int(user_id, "user_id")
    key = _required_text(league_key, "league_key")
    year = _positive_int(season_year, "season_year")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM public.commercial_league_user_role r
            JOIN public.auth_user u
              ON u.user_id = r.user_id
            JOIN public.league_profile p
              ON p.league_key = r.league_key
             AND p.season_year = r.season_year
            WHERE r.user_id = %s
              AND r.league_key = %s
              AND r.season_year = %s
              AND r.role_code = 'commissioner'
              AND r.active = true
              AND u.active = true
              AND p.is_active = true
            LIMIT 1
            """,
            (uid, key, year),
        )

        return cursor.fetchone() is not None
