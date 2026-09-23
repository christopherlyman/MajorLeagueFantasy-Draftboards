from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from draftboard.state.commercial_league_profile import (
    validate_commercial_league_profile,
)


class CommercialLeagueProfileRepositoryError(RuntimeError):
    """Raised when commercial profile persistence cannot be completed safely."""


class CommercialLeagueProfileVersionConflict(
    CommercialLeagueProfileRepositoryError
):
    """Raised when the caller's expected profile version is stale."""


@dataclass(frozen=True)
class CommercialLeagueProfileSaveResult:
    league_key: str
    season_year: int
    profile_version: int
    created: bool
    changed: bool


@dataclass(frozen=True)
class StoredCommercialLeagueProfile:
    league_key: str
    season_year: int
    profile_version: int
    profile: dict[str, Any]
    is_active: bool
    updated_by: str | None
    notes: str | None


def _plain_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    return value


def serialize_commercial_league_profile(
    profile: Mapping[str, Any],
) -> str:
    """Validate and deterministically serialize a commercial profile to YAML."""

    validate_commercial_league_profile(profile)
    plain = _plain_data(profile)

    return yaml.safe_dump(
        plain,
        sort_keys=False,
        allow_unicode=True,
    )


def parse_commercial_league_profile_yaml(
    yaml_text: str,
) -> dict[str, Any]:
    """Parse persisted YAML and revalidate the commercial profile boundary."""

    try:
        raw = yaml.safe_load(yaml_text)
    except Exception as exc:
        raise CommercialLeagueProfileRepositoryError(
            f"Commercial league profile YAML parse failed: {exc}"
        ) from exc

    if not isinstance(raw, Mapping):
        raise CommercialLeagueProfileRepositoryError(
            "Persisted commercial league profile must be a top-level mapping."
        )

    profile = dict(raw)
    validate_commercial_league_profile(profile)
    return profile


def load_commercial_league_profile(
    connection: Any,
    league_key: str,
    season_year: int,
) -> StoredCommercialLeagueProfile:
    """Load and validate one season-scoped commercial league profile."""

    sql = """
        SELECT
            profile_version,
            profile_yaml,
            is_active,
            updated_by,
            notes
        FROM public.league_profile
        WHERE league_key = %s
          AND season_year = %s
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, (league_key, season_year))
        row = cursor.fetchone()

    if not row:
        raise CommercialLeagueProfileRepositoryError(
            "No league_profile found for "
            f"league_key={league_key} season_year={season_year}."
        )

    profile_version, profile_yaml, is_active, updated_by, notes = row
    profile = parse_commercial_league_profile_yaml(str(profile_yaml))

    return StoredCommercialLeagueProfile(
        league_key=league_key,
        season_year=int(season_year),
        profile_version=int(profile_version),
        profile=profile,
        is_active=bool(is_active),
        updated_by=updated_by,
        notes=notes,
    )


def save_commercial_league_profile(
    connection: Any,
    profile: Mapping[str, Any],
    *,
    changed_by: str,
    notes: str | None = None,
    expected_profile_version: int | None = None,
    manage_transaction: bool = True,
) -> CommercialLeagueProfileSaveResult:
    """
    Atomically create or update one commercial league profile.

    Update behavior:
    - lock the current season-scoped row;
    - reject stale expected versions;
    - archive the complete prior YAML snapshot;
    - increment profile_version exactly once;
    - write the new active profile;
    - commit only after all writes succeed.

    Identical profile content is a no-op and does not create history.
    """

    new_yaml = serialize_commercial_league_profile(profile)

    league = profile["league"]
    league_key = str(league["league_key"]).strip()
    season_year = int(league["season_year"])

    actor = str(changed_by or "").strip()
    if not actor:
        raise CommercialLeagueProfileRepositoryError(
            "changed_by must be non-empty."
        )

    if expected_profile_version is not None:
        if (
            isinstance(expected_profile_version, bool)
            or not isinstance(expected_profile_version, int)
            or expected_profile_version < 0
        ):
            raise CommercialLeagueProfileRepositoryError(
                "expected_profile_version must be an integer >= 0."
            )

    select_sql = """
        SELECT profile_version, profile_yaml
        FROM public.league_profile
        WHERE league_key = %s
          AND season_year = %s
        FOR UPDATE
    """

    insert_sql = """
        INSERT INTO public.league_profile (
            league_key,
            season_year,
            profile_version,
            profile_yaml,
            is_active,
            updated_at_utc,
            updated_by,
            notes
        )
        VALUES (%s, %s, 1, %s, true, now(), %s, %s)
    """

    history_sql = """
        INSERT INTO public.league_profile_history (
            league_key,
            season_year,
            profile_version,
            profile_yaml,
            changed_at_utc,
            changed_by,
            notes
        )
        VALUES (%s, %s, %s, %s, now(), %s, %s)
    """

    update_sql = """
        UPDATE public.league_profile
        SET
            profile_version = %s,
            profile_yaml = %s,
            is_active = true,
            updated_at_utc = now(),
            updated_by = %s,
            notes = %s
        WHERE league_key = %s
          AND season_year = %s
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(select_sql, (league_key, season_year))
            current = cursor.fetchone()

            current_version = int(current[0]) if current else 0

            if (
                expected_profile_version is not None
                and expected_profile_version != current_version
            ):
                raise CommercialLeagueProfileVersionConflict(
                    "Commercial league profile version conflict: "
                    f"expected={expected_profile_version} "
                    f"actual={current_version}."
                )

            if current is None:
                cursor.execute(
                    insert_sql,
                    (
                        league_key,
                        season_year,
                        new_yaml,
                        actor,
                        notes,
                    ),
                )
                result = CommercialLeagueProfileSaveResult(
                    league_key=league_key,
                    season_year=season_year,
                    profile_version=1,
                    created=True,
                    changed=True,
                )
            else:
                old_version = int(current[0])
                old_yaml = str(current[1])
                old_profile = parse_commercial_league_profile_yaml(old_yaml)

                if _plain_data(old_profile) == _plain_data(profile):
                    result = CommercialLeagueProfileSaveResult(
                        league_key=league_key,
                        season_year=season_year,
                        profile_version=old_version,
                        created=False,
                        changed=False,
                    )
                else:
                    cursor.execute(
                        history_sql,
                        (
                            league_key,
                            season_year,
                            old_version,
                            old_yaml,
                            actor,
                            notes,
                        ),
                    )

                    new_version = old_version + 1

                    cursor.execute(
                        update_sql,
                        (
                            new_version,
                            new_yaml,
                            actor,
                            notes,
                            league_key,
                            season_year,
                        ),
                    )

                    result = CommercialLeagueProfileSaveResult(
                        league_key=league_key,
                        season_year=season_year,
                        profile_version=new_version,
                        created=False,
                        changed=True,
                    )

        if manage_transaction:
            connection.commit()

        return result

    except Exception:
        if manage_transaction:
            connection.rollback()

        raise
