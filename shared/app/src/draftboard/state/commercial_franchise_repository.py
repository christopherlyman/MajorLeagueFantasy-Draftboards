from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from draftboard.state.commercial_league_profile_repository import (
    parse_commercial_league_profile_yaml,
)


class CommercialFranchiseRepositoryError(RuntimeError):
    """Raised when commercial franchise persistence cannot be completed safely."""


@dataclass(frozen=True)
class CommercialFranchiseInput:
    team_name: str
    owner_name: str | None = None


@dataclass(frozen=True)
class StoredCommercialFranchise:
    franchise_id: int
    franchise_name: str
    league_key: str
    season_year: int
    team_key: str
    team_name: str
    owner_name: str | None
    source: str


def _normalize_franchise_inputs(
    franchises: Sequence[CommercialFranchiseInput | Mapping[str, Any]],
) -> list[CommercialFranchiseInput]:
    if isinstance(franchises, (str, bytes)):
        raise CommercialFranchiseRepositoryError(
            "franchises must be a sequence of franchise records."
        )

    normalized: list[CommercialFranchiseInput] = []

    for slot, item in enumerate(franchises, start=1):
        if isinstance(item, CommercialFranchiseInput):
            team_name_raw = item.team_name
            owner_name_raw = item.owner_name
        elif isinstance(item, Mapping):
            team_name_raw = item.get("team_name")
            owner_name_raw = item.get("owner_name")
        else:
            raise CommercialFranchiseRepositoryError(
                f"Franchise slot {slot} must be a mapping or "
                "CommercialFranchiseInput."
            )

        team_name = str(team_name_raw or "").strip()

        if not team_name:
            raise CommercialFranchiseRepositoryError(
                f"Franchise slot {slot} team_name must be non-empty."
            )

        owner_name = str(owner_name_raw or "").strip() or None

        normalized.append(
            CommercialFranchiseInput(
                team_name=team_name,
                owner_name=owner_name,
            )
        )

    return normalized


def load_commercial_league_franchises(
    connection: Any,
    league_key: str,
    season_year: int,
) -> list[StoredCommercialFranchise]:
    """Load canonical franchises for one commercial league season."""

    key = str(league_key or "").strip()

    if not key:
        raise CommercialFranchiseRepositoryError(
            "league_key must be non-empty."
        )

    if (
        isinstance(season_year, bool)
        or not isinstance(season_year, int)
        or season_year <= 0
    ):
        raise CommercialFranchiseRepositoryError(
            "season_year must be a positive integer."
        )

    sql = """
        SELECT
            f.franchise_id,
            f.franchise_name,
            fst.league_key,
            fst.season_year,
            fst.team_key,
            fst.team_name,
            fst.owner_name,
            fst.source
        FROM public.franchise_season_team fst
        JOIN public.franchise f
          ON f.franchise_id = fst.franchise_id
        WHERE fst.league_key = %s
          AND fst.season_year = %s
        ORDER BY f.franchise_id
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, (key, season_year))
        rows = cursor.fetchall()

    return [
        StoredCommercialFranchise(
            franchise_id=int(row[0]),
            franchise_name=str(row[1]),
            league_key=str(row[2]),
            season_year=int(row[3]),
            team_key=str(row[4]),
            team_name=str(row[5] or ""),
            owner_name=(
                str(row[6]).strip()
                if row[6] is not None and str(row[6]).strip()
                else None
            ),
            source=str(row[7]),
        )
        for row in rows
    ]


def initialize_commercial_league_franchises(
    connection: Any,
    league_key: str,
    season_year: int,
    franchises: Sequence[
        CommercialFranchiseInput | Mapping[str, Any]
    ],
    *,
    source: str = "manual",
) -> list[StoredCommercialFranchise]:
    """
    Atomically initialize all franchises for one commercial league season.

    Invariants:
    - the target active Profile v2 must exist;
    - submitted count must equal profile manager_count;
    - the season must not already contain franchise mappings;
    - every team name must be nonblank;
    - each franchise receives a durable generated franchise_id;
    - season team keys use <league_key>.t.<slot>;
    - all inserts commit together or roll back together.
    """

    key = str(league_key or "").strip()

    if not key:
        raise CommercialFranchiseRepositoryError(
            "league_key must be non-empty."
        )

    if (
        isinstance(season_year, bool)
        or not isinstance(season_year, int)
        or season_year <= 0
    ):
        raise CommercialFranchiseRepositoryError(
            "season_year must be a positive integer."
        )

    provenance = str(source or "").strip()

    if not provenance:
        raise CommercialFranchiseRepositoryError(
            "source must be non-empty."
        )

    normalized = _normalize_franchise_inputs(franchises)

    profile_sql = """
        SELECT profile_yaml
        FROM public.league_profile
        WHERE league_key = %s
          AND season_year = %s
          AND is_active = true
        FOR UPDATE
    """

    existing_sql = """
        SELECT COUNT(*)
        FROM public.franchise_season_team
        WHERE league_key = %s
          AND season_year = %s
    """

    franchise_sql = """
        INSERT INTO public.franchise (
            franchise_name,
            created_at,
            updated_at
        )
        VALUES (%s, now(), now())
        RETURNING franchise_id
    """

    season_team_sql = """
        INSERT INTO public.franchise_season_team (
            franchise_id,
            season_year,
            league_key,
            team_key,
            team_id,
            team_name,
            owner_guid,
            owner_name,
            source,
            created_at,
            updated_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            NULL,
            %s,
            NULL,
            %s,
            %s,
            now(),
            now()
        )
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                profile_sql,
                (key, season_year),
            )
            profile_row = cursor.fetchone()

            if profile_row is None:
                raise CommercialFranchiseRepositoryError(
                    "No active commercial league profile found for "
                    f"league_key={key} season_year={season_year}."
                )

            profile = parse_commercial_league_profile_yaml(
                str(profile_row[0])
            )

            league = profile.get("league")

            if (
                not isinstance(league, Mapping)
                or "league_model" not in league
                or "manager_count" not in league
            ):
                raise CommercialFranchiseRepositoryError(
                    "Franchise initialization requires "
                    "commercial Profile v2."
                )

            if str(league.get("league_key") or "").strip() != key:
                raise CommercialFranchiseRepositoryError(
                    "Persisted profile league_key does not match "
                    "the requested league."
                )

            if int(league.get("season_year")) != season_year:
                raise CommercialFranchiseRepositoryError(
                    "Persisted profile season_year does not match "
                    "the requested season."
                )

            manager_count = league.get("manager_count")

            if (
                isinstance(manager_count, bool)
                or not isinstance(manager_count, int)
                or manager_count <= 0
            ):
                raise CommercialFranchiseRepositoryError(
                    "Profile manager_count must be a positive integer."
                )

            if len(normalized) != manager_count:
                raise CommercialFranchiseRepositoryError(
                    "Franchise count must equal profile manager_count: "
                    f"expected={manager_count} "
                    f"actual={len(normalized)}."
                )

            cursor.execute(
                existing_sql,
                (key, season_year),
            )
            existing_row = cursor.fetchone()

            if existing_row is None:
                raise CommercialFranchiseRepositoryError(
                    "Could not determine existing franchise count."
                )

            existing_count = int(existing_row[0])

            if existing_count != 0:
                raise CommercialFranchiseRepositoryError(
                    "Franchises are already initialized for "
                    f"league_key={key} season_year={season_year}."
                )

            created: list[StoredCommercialFranchise] = []

            for slot, item in enumerate(normalized, start=1):
                cursor.execute(
                    franchise_sql,
                    (item.team_name,),
                )
                franchise_row = cursor.fetchone()

                if franchise_row is None:
                    raise CommercialFranchiseRepositoryError(
                        f"Franchise slot {slot} did not return "
                        "a franchise_id."
                    )

                franchise_id = int(franchise_row[0])
                team_key = f"{key}.t.{slot}"

                cursor.execute(
                    season_team_sql,
                    (
                        franchise_id,
                        season_year,
                        key,
                        team_key,
                        item.team_name,
                        item.owner_name,
                        provenance,
                    ),
                )

                created.append(
                    StoredCommercialFranchise(
                        franchise_id=franchise_id,
                        franchise_name=item.team_name,
                        league_key=key,
                        season_year=season_year,
                        team_key=team_key,
                        team_name=item.team_name,
                        owner_name=item.owner_name,
                        source=provenance,
                    )
                )

        connection.commit()
        return created

    except Exception:
        connection.rollback()
        raise
