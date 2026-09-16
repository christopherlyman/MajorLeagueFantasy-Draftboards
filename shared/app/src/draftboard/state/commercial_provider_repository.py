from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


class CommercialProviderRepositoryError(RuntimeError):
    """Raised when commercial provider persistence invariants fail."""


_ALLOWED_STATUSES = {
    "pending",
    "active",
    "error",
    "disconnected",
}


@dataclass(frozen=True)
class StoredProviderConnection:
    provider_connection_id: int
    user_id: int
    provider_code: str
    external_account_id: str | None
    external_account_name: str | None
    status: str
    created_at_utc: Any
    updated_at_utc: Any
    last_verified_at_utc: Any
    disconnected_at_utc: Any


@dataclass(frozen=True)
class StoredProviderLeagueBinding:
    provider_league_binding_id: int
    provider_connection_id: int
    provider_code: str
    league_key: str
    season_year: int
    provider_league_id: str
    provider_game_id: str | None
    provider_league_name: str | None
    created_at_utc: Any
    updated_at_utc: Any
    last_synced_at_utc: Any


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CommercialProviderRepositoryError(
            f"{field_name} must be non-empty."
        )
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _positive_int(value: object, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CommercialProviderRepositoryError(
            f"{field_name} must be a positive integer."
        ) from exc

    if parsed <= 0:
        raise CommercialProviderRepositoryError(
            f"{field_name} must be a positive integer."
        )

    return parsed


def _provider_code(value: object) -> str:
    code = _required_text(value, "provider_code").lower()

    if code != code.strip():
        raise CommercialProviderRepositoryError(
            "provider_code must be normalized."
        )

    return code


def _status(value: object) -> str:
    normalized = _required_text(value, "status").lower()

    if normalized not in _ALLOWED_STATUSES:
        raise CommercialProviderRepositoryError(
            f"Unsupported provider connection status: {normalized!r}."
        )

    return normalized


def _row_to_connection(row: tuple | list) -> StoredProviderConnection:
    return StoredProviderConnection(
        provider_connection_id=int(row[0]),
        user_id=int(row[1]),
        provider_code=str(row[2]),
        external_account_id=_optional_text(row[3]),
        external_account_name=_optional_text(row[4]),
        status=str(row[5]),
        created_at_utc=row[6],
        updated_at_utc=row[7],
        last_verified_at_utc=row[8],
        disconnected_at_utc=row[9],
    )


def _row_to_binding(row: tuple | list) -> StoredProviderLeagueBinding:
    return StoredProviderLeagueBinding(
        provider_league_binding_id=int(row[0]),
        provider_connection_id=int(row[1]),
        provider_code=str(row[2]),
        league_key=str(row[3]),
        season_year=int(row[4]),
        provider_league_id=str(row[5]),
        provider_game_id=_optional_text(row[6]),
        provider_league_name=_optional_text(row[7]),
        created_at_utc=row[8],
        updated_at_utc=row[9],
        last_synced_at_utc=row[10],
    )


_CONNECTION_COLUMNS = """
    provider_connection_id,
    user_id,
    provider_code,
    external_account_id,
    external_account_name,
    status,
    created_at_utc,
    updated_at_utc,
    last_verified_at_utc,
    disconnected_at_utc
"""


_BINDING_COLUMNS = """
    provider_league_binding_id,
    provider_connection_id,
    provider_code,
    league_key,
    season_year,
    provider_league_id,
    provider_game_id,
    provider_league_name,
    created_at_utc,
    updated_at_utc,
    last_synced_at_utc
"""


def load_provider_connection(
    connection,
    *,
    user_id: int,
    provider_connection_id: int,
) -> StoredProviderConnection | None:
    uid = _positive_int(user_id, "user_id")
    connection_id = _positive_int(
        provider_connection_id,
        "provider_connection_id",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {_CONNECTION_COLUMNS}
            FROM public.provider_connection
            WHERE provider_connection_id = %s
              AND user_id = %s
            """,
            (connection_id, uid),
        )
        row = cursor.fetchone()

    return _row_to_connection(row) if row else None


def create_provider_connection(
    connection,
    *,
    user_id: int,
    provider_code: str,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    status: str = "pending",
) -> StoredProviderConnection:
    uid = _positive_int(user_id, "user_id")
    provider = _provider_code(provider_code)
    account_id = _optional_text(external_account_id)
    account_name = _optional_text(external_account_name)
    normalized_status = _status(status)

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
                raise CommercialProviderRepositoryError(
                    f"Active auth_user user_id={uid} was not found."
                )

            cursor.execute(
                f"""
                INSERT INTO public.provider_connection (
                    user_id,
                    provider_code,
                    external_account_id,
                    external_account_name,
                    status
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_CONNECTION_COLUMNS}
                """,
                (
                    uid,
                    provider,
                    account_id,
                    account_name,
                    normalized_status,
                ),
            )

            row = cursor.fetchone()

            if row is None:
                raise CommercialProviderRepositoryError(
                    "Provider connection insert returned no row."
                )

        connection.commit()
        return _row_to_connection(row)

    except Exception as exc:
        connection.rollback()

        if isinstance(exc, CommercialProviderRepositoryError):
            raise

        raise CommercialProviderRepositoryError(
            f"Failed to create provider connection: {exc}"
        ) from exc


def update_provider_connection(
    connection,
    *,
    user_id: int,
    provider_connection_id: int,
    status: str,
    external_account_id: str | None = None,
    external_account_name: str | None = None,
    mark_verified: bool = False,
) -> StoredProviderConnection:
    uid = _positive_int(user_id, "user_id")
    connection_id = _positive_int(
        provider_connection_id,
        "provider_connection_id",
    )
    normalized_status = _status(status)
    account_id = _optional_text(external_account_id)
    account_name = _optional_text(external_account_name)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE public.provider_connection
                SET
                    status = %s,
                    external_account_id =
                        COALESCE(%s, external_account_id),
                    external_account_name =
                        COALESCE(%s, external_account_name),
                    updated_at_utc = now(),
                    last_verified_at_utc =
                        CASE
                            WHEN %s THEN now()
                            ELSE last_verified_at_utc
                        END,
                    disconnected_at_utc =
                        CASE
                            WHEN %s = 'disconnected'
                                THEN COALESCE(disconnected_at_utc, now())
                            ELSE NULL
                        END
                WHERE provider_connection_id = %s
                  AND user_id = %s
                RETURNING {_CONNECTION_COLUMNS}
                """,
                (
                    normalized_status,
                    account_id,
                    account_name,
                    bool(mark_verified),
                    normalized_status,
                    connection_id,
                    uid,
                ),
            )

            row = cursor.fetchone()

            if row is None:
                raise CommercialProviderRepositoryError(
                    "Provider connection was not found for the owning user."
                )

        connection.commit()
        return _row_to_connection(row)

    except Exception as exc:
        connection.rollback()

        if isinstance(exc, CommercialProviderRepositoryError):
            raise

        raise CommercialProviderRepositoryError(
            f"Failed to update provider connection: {exc}"
        ) from exc


def load_provider_league_binding(
    connection,
    *,
    user_id: int,
    league_key: str,
    season_year: int,
    provider_code: str,
) -> StoredProviderLeagueBinding | None:
    uid = _positive_int(user_id, "user_id")
    key = _required_text(league_key, "league_key")
    year = _positive_int(season_year, "season_year")
    provider = _provider_code(provider_code)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                b.provider_league_binding_id,
                b.provider_connection_id,
                b.provider_code,
                b.league_key,
                b.season_year,
                b.provider_league_id,
                b.provider_game_id,
                b.provider_league_name,
                b.created_at_utc,
                b.updated_at_utc,
                b.last_synced_at_utc
            FROM public.provider_league_binding b
            JOIN public.provider_connection c
              ON c.provider_connection_id =
                    b.provider_connection_id
             AND c.provider_code = b.provider_code
            WHERE c.user_id = %s
              AND b.league_key = %s
              AND b.season_year = %s
              AND b.provider_code = %s
            """,
            (uid, key, year, provider),
        )
        row = cursor.fetchone()

    return _row_to_binding(row) if row else None


def bind_provider_league(
    connection,
    *,
    user_id: int,
    provider_connection_id: int,
    league_key: str,
    season_year: int,
    provider_league_id: str,
    provider_game_id: str | None = None,
    provider_league_name: str | None = None,
) -> StoredProviderLeagueBinding:
    uid = _positive_int(user_id, "user_id")
    connection_id = _positive_int(
        provider_connection_id,
        "provider_connection_id",
    )
    key = _required_text(league_key, "league_key")
    year = _positive_int(season_year, "season_year")
    external_league_id = _required_text(
        provider_league_id,
        "provider_league_id",
    )
    external_game_id = _optional_text(provider_game_id)
    external_league_name = _optional_text(provider_league_name)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_CONNECTION_COLUMNS}
                FROM public.provider_connection
                WHERE provider_connection_id = %s
                  AND user_id = %s
                FOR UPDATE
                """,
                (connection_id, uid),
            )

            connection_row = cursor.fetchone()

            if connection_row is None:
                raise CommercialProviderRepositoryError(
                    "Provider connection was not found for the owning user."
                )

            stored_connection = _row_to_connection(connection_row)

            if stored_connection.status != "active":
                raise CommercialProviderRepositoryError(
                    "Provider connection must be active before "
                    "binding a league."
                )

            cursor.execute(
                """
                SELECT profile_yaml, is_active
                FROM public.league_profile
                WHERE league_key = %s
                  AND season_year = %s
                FOR UPDATE
                """,
                (key, year),
            )

            profile_row = cursor.fetchone()

            if profile_row is None or not bool(profile_row[1]):
                raise CommercialProviderRepositoryError(
                    "Active commercial league profile was not found."
                )

            try:
                profile = yaml.safe_load(profile_row[0]) or {}
            except Exception as exc:
                raise CommercialProviderRepositoryError(
                    "Persisted commercial league profile YAML is invalid."
                ) from exc

            league = profile.get("league")

            if not isinstance(league, dict):
                raise CommercialProviderRepositoryError(
                    "Persisted commercial league profile is missing "
                    "league metadata."
                )

            profile_key = str(
                league.get("league_key") or ""
            ).strip()

            profile_year_raw = league.get("season_year")

            try:
                profile_year = int(profile_year_raw)
            except (TypeError, ValueError) as exc:
                raise CommercialProviderRepositoryError(
                    "Persisted commercial league season_year is invalid."
                ) from exc

            profile_provider = str(
                league.get("platform") or ""
            ).strip().lower()

            if profile_key != key or profile_year != year:
                raise CommercialProviderRepositoryError(
                    "Persisted commercial league identity does not "
                    "match the requested league."
                )

            if profile_provider != stored_connection.provider_code:
                raise CommercialProviderRepositoryError(
                    "Commercial league platform does not match "
                    "the provider connection."
                )

            cursor.execute(
                f"""
                SELECT {_BINDING_COLUMNS}
                FROM public.provider_league_binding
                WHERE league_key = %s
                  AND season_year = %s
                  AND provider_code = %s
                FOR UPDATE
                """,
                (
                    key,
                    year,
                    stored_connection.provider_code,
                ),
            )

            existing_row = cursor.fetchone()

            if existing_row is not None:
                existing = _row_to_binding(existing_row)

                if (
                    existing.provider_connection_id == connection_id
                    and existing.provider_league_id
                    == external_league_id
                ):
                    connection.commit()
                    return existing

                raise CommercialProviderRepositoryError(
                    "Commercial league already has a different "
                    "provider binding."
                )

            cursor.execute(
                f"""
                INSERT INTO public.provider_league_binding (
                    provider_connection_id,
                    provider_code,
                    league_key,
                    season_year,
                    provider_league_id,
                    provider_game_id,
                    provider_league_name
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_BINDING_COLUMNS}
                """,
                (
                    connection_id,
                    stored_connection.provider_code,
                    key,
                    year,
                    external_league_id,
                    external_game_id,
                    external_league_name,
                ),
            )

            row = cursor.fetchone()

            if row is None:
                raise CommercialProviderRepositoryError(
                    "Provider league binding insert returned no row."
                )

        connection.commit()
        return _row_to_binding(row)

    except Exception as exc:
        connection.rollback()

        if isinstance(exc, CommercialProviderRepositoryError):
            raise

        raise CommercialProviderRepositoryError(
            f"Failed to bind provider league: {exc}"
        ) from exc
