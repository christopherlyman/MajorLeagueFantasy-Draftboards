from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Any, Mapping, Sequence

import json

from draftboard.domain.nffl_new_season import (
    NfflNewSeasonSpec,
    NfflResolvedFranchiseAssignment,
)


@dataclass(frozen=True)
class NfflNewSeasonStageResult:
    season_year: int
    league_key: str
    teams_created: int
    franchise_mappings_created: int
    bridges_created: int
    gateway_links_created: int
    season_context_created: int
    activated: bool


def _gateway_token() -> str:
    # 36 random bytes -> 48 URL-safe characters, matching current NFFL links.
    return secrets.token_urlsafe(36)


def _profile_yaml(spec: NfflNewSeasonSpec) -> str:
    """
    Serialize the profile using JSON, which is valid YAML syntax.

    Using the standard library keeps the domain service independent of
    PyYAML while remaining compatible with YAML readers.
    """
    return json.dumps(
        spec.profile,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def stage_nffl_new_season(
    conn: Any,
    *,
    spec: NfflNewSeasonSpec,
    target_teams: Sequence[Mapping[str, Any]],
    assignments: Sequence[NfflResolvedFranchiseAssignment],
    actor: str = "commissioner",
) -> NfflNewSeasonStageResult:
    """
    Atomically stage the next NFFL season without activating it.

    The caller owns the connection. Any exception aborts the surrounding
    transaction. This function deliberately does not commit.

    Preconditions:
    - the currently active NFFL context still matches spec's prior season;
    - the target Yahoo team universe is complete;
    - target season rows do not already exist;
    - assignments are complete and one-to-one.

    Writes:
    - public.league_profile
    - nffl.team
    - public.franchise_season_team
    - nffl.team_season_bridge
    - nffl.team_gateway_link
    - nffl.season_context with is_active = false

    It never changes the currently active season.
    """
    actor = str(actor or "").strip() or "commissioner"

    teams = list(target_teams)
    resolved = list(assignments)

    if len(teams) != spec.manager_count:
        raise ValueError(
            "target_teams count must equal spec.manager_count."
        )

    if len(resolved) != spec.manager_count:
        raise ValueError(
            "assignments count must equal spec.manager_count."
        )

    team_by_key: dict[str, Mapping[str, Any]] = {}

    for team in teams:
        team_key = str(team.get("team_key") or "").strip()
        team_name = str(team.get("team_name") or "").strip()

        if not team_key:
            raise ValueError("Every target team must have a team_key.")

        if not team_name:
            raise ValueError(
                f"Target team {team_key} must have a team_name."
            )

        if team_key in team_by_key:
            raise ValueError(
                f"Duplicate target team_key: {team_key}"
            )

        team_by_key[team_key] = team

    assignment_by_team: dict[
        str,
        NfflResolvedFranchiseAssignment,
    ] = {}

    franchise_ids: set[int] = set()

    for assignment in resolved:
        team_key = str(assignment.target_team_key).strip()

        if team_key not in team_by_key:
            raise ValueError(
                f"Assignment references unknown target team: {team_key}"
            )

        if team_key in assignment_by_team:
            raise ValueError(
                f"Duplicate assignment for target team: {team_key}"
            )

        franchise_id = int(assignment.franchise_id)

        if franchise_id in franchise_ids:
            raise ValueError(
                f"Duplicate franchise assignment: {franchise_id}"
            )

        franchise_ids.add(franchise_id)
        assignment_by_team[team_key] = assignment

    if set(assignment_by_team) != set(team_by_key):
        raise ValueError(
            "Every target team must have exactly one assignment."
        )

    with conn.cursor() as cur:
        # Serialize competing rollover attempts.
        cur.execute(
            """
            LOCK TABLE nffl.season_context
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
        cur.execute(
            """
            LOCK TABLE public.franchise_season_team
            IN SHARE ROW EXCLUSIVE MODE
            """
        )

        # Active season must not have changed since preview.
        cur.execute(
            """
            SELECT
                current_season_year,
                current_league_key,
                draft_key
            FROM nffl.season_context
            WHERE league_code='NFFL'
              AND is_active=true
            """
        )

        active_rows = cur.fetchall()

        if len(active_rows) != 1:
            raise RuntimeError(
                "Expected exactly one active NFFL season context."
            )

        active_year, active_league_key, _active_draft_key = active_rows[0]

        if (
            int(active_year) != spec.prior_season_year
            or str(active_league_key) != spec.prior_league_key
        ):
            raise RuntimeError(
                "Active NFFL context changed after preview."
            )

        # Yahoo source must still contain the exact target universe.
        cur.execute(
            """
            SELECT
                team_key,
                team_id,
                team_name,
                owner_name,
                owner_guid
            FROM public.yahoo_team_map
            WHERE league_key=%s
              AND season_year=%s
            ORDER BY team_key
            """,
            (
                spec.current_league_key,
                spec.current_season_year,
            ),
        )

        yahoo_rows = cur.fetchall()

        if len(yahoo_rows) != spec.manager_count:
            raise RuntimeError(
                "Target Yahoo team count changed after preview."
            )

        yahoo_by_key = {
            str(row[0]): row
            for row in yahoo_rows
        }

        if set(yahoo_by_key) != set(team_by_key):
            raise RuntimeError(
                "Target Yahoo team keys changed after preview."
            )

        # Refuse to overwrite or partially merge an existing staged season.
        checks = (
            (
                "public.league_profile",
                """
                SELECT count(*)
                FROM public.league_profile
                WHERE league_key=%s
                  AND season_year=%s
                """,
            ),
            (
                "nffl.team",
                """
                SELECT count(*)
                FROM nffl.team
                WHERE league_key=%s
                  AND season_year=%s
                """,
            ),
            (
                "public.franchise_season_team",
                """
                SELECT count(*)
                FROM public.franchise_season_team
                WHERE league_key=%s
                  AND season_year=%s
                """,
            ),
            (
                "nffl.team_season_bridge",
                """
                SELECT count(*)
                FROM nffl.team_season_bridge
                WHERE league_code='NFFL'
                  AND current_league_key=%s
                  AND current_season_year=%s
                """,
            ),
            (
                "nffl.team_gateway_link",
                """
                SELECT count(*)
                FROM nffl.team_gateway_link
                WHERE league_key=%s
                  AND season_year=%s
                """,
            ),
        )

        for label, query in checks:
            cur.execute(
                query,
                (
                    spec.current_league_key,
                    spec.current_season_year,
                ),
            )
            count = int(cur.fetchone()[0] or 0)

            if count != 0:
                raise RuntimeError(
                    f"Target season already has rows in {label}: {count}"
                )

        cur.execute(
            """
            SELECT count(*)
            FROM nffl.season_context
            WHERE league_code='NFFL'
              AND current_season_year=%s
            """,
            (spec.current_season_year,),
        )

        if int(cur.fetchone()[0] or 0) != 0:
            raise RuntimeError(
                "Target season_context already exists."
            )

        # Prior franchise identities must still be complete.
        cur.execute(
            """
            SELECT franchise_id, team_key
            FROM public.franchise_season_team
            WHERE league_key=%s
              AND season_year=%s
            ORDER BY team_key
            """,
            (
                spec.prior_league_key,
                spec.prior_season_year,
            ),
        )

        prior_rows = cur.fetchall()
        prior_franchise_ids = {
            int(row[0])
            for row in prior_rows
        }

        if len(prior_rows) != spec.manager_count:
            raise RuntimeError(
                "Prior franchise mapping count changed after preview."
            )

        if franchise_ids != prior_franchise_ids:
            raise RuntimeError(
                "Resolved franchise set no longer matches prior season."
            )

        # 1. League profile.
        cur.execute(
            """
            INSERT INTO public.league_profile (
                league_key,
                season_year,
                profile_version,
                profile_yaml,
                is_active,
                updated_by,
                notes
            )
            VALUES (%s, %s, 1, %s, true, %s, %s)
            """,
            (
                spec.current_league_key,
                spec.current_season_year,
                _profile_yaml(spec),
                actor,
                (
                    f"Staged NFFL {spec.current_season_year} profile "
                    f"from {spec.prior_season_year}."
                ),
            ),
        )

        # 2-5. Team, franchise mapping, bridge, gateway.
        for team_key in sorted(team_by_key):
            assignment = assignment_by_team[team_key]
            yahoo = yahoo_by_key[team_key]

            _team_key, team_id, team_name, owner_name, owner_guid = yahoo

            cur.execute(
                """
                INSERT INTO nffl.team (
                    league_key,
                    season_year,
                    team_key,
                    team_id,
                    team_name,
                    owner_name,
                    owner_guid
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    spec.current_league_key,
                    spec.current_season_year,
                    team_key,
                    team_id,
                    team_name,
                    owner_name,
                    owner_guid,
                ),
            )

            source = (
                "nffl_new_season_auto_owner_guid"
                if assignment.resolution_method == "AUTO_OWNER_GUID"
                else "nffl_new_season_commissioner_manual"
            )

            cur.execute(
                """
                INSERT INTO public.franchise_season_team (
                    franchise_id,
                    season_year,
                    league_key,
                    team_key,
                    team_id,
                    team_name,
                    owner_guid,
                    owner_name,
                    source
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    assignment.franchise_id,
                    spec.current_season_year,
                    spec.current_league_key,
                    team_key,
                    team_id,
                    team_name,
                    owner_guid,
                    owner_name,
                    source,
                ),
            )

            cur.execute(
                """
                SELECT team_key
                FROM public.franchise_season_team
                WHERE franchise_id=%s
                  AND season_year=%s
                  AND league_key=%s
                """,
                (
                    assignment.franchise_id,
                    spec.prior_season_year,
                    spec.prior_league_key,
                ),
            )

            prior_team = cur.fetchone()

            if not prior_team:
                raise RuntimeError(
                    f"Prior team not found for franchise "
                    f"{assignment.franchise_id}."
                )

            prior_team_key = str(prior_team[0])

            cur.execute(
                """
                INSERT INTO nffl.team_season_bridge (
                    league_code,
                    current_season_year,
                    current_league_key,
                    current_team_key,
                    source_season_year,
                    source_league_key,
                    source_team_key,
                    bridge_method,
                    note
                )
                VALUES (
                    'NFFL',
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    spec.current_season_year,
                    spec.current_league_key,
                    team_key,
                    spec.prior_season_year,
                    spec.prior_league_key,
                    prior_team_key,
                    assignment.resolution_method.lower(),
                    (
                        f"Staged by Initialize New Season; "
                        f"franchise_id={assignment.franchise_id}"
                    ),
                ),
            )

            token = _gateway_token()

            if len(token) != 48:
                raise RuntimeError(
                    "Generated gateway token is not 48 characters."
                )

            cur.execute(
                """
                INSERT INTO nffl.team_gateway_link (
                    league_key,
                    season_year,
                    team_key,
                    link_token,
                    is_active,
                    claim_count
                )
                VALUES (%s, %s, %s, %s, true, 0)
                """,
                (
                    spec.current_league_key,
                    spec.current_season_year,
                    team_key,
                    token,
                ),
            )

        # 6. Create the next context but deliberately leave it inactive.
        cur.execute(
            """
            INSERT INTO nffl.season_context (
                league_code,
                current_season_year,
                current_league_key,
                prior_season_year,
                prior_league_key,
                draft_key,
                is_active,
                context_source,
                note
            )
            VALUES (
                'NFFL',
                %s, %s,
                %s, %s,
                %s,
                false,
                'commissioner_new_season_stage',
                %s
            )
            """,
            (
                spec.current_season_year,
                spec.current_league_key,
                spec.prior_season_year,
                spec.prior_league_key,
                spec.draft_key,
                (
                    "Staged by Commissioner Initialize New Season. "
                    "Remain inactive until runtime configuration is switched "
                    "and verified."
                ),
            ),
        )

        # Final in-transaction proof.
        cur.execute(
            """
            SELECT
                (SELECT count(*)
                   FROM nffl.team
                  WHERE league_key=%s AND season_year=%s),
                (SELECT count(*)
                   FROM public.franchise_season_team
                  WHERE league_key=%s AND season_year=%s),
                (SELECT count(*)
                   FROM nffl.team_season_bridge
                  WHERE league_code='NFFL'
                    AND current_league_key=%s
                    AND current_season_year=%s),
                (SELECT count(*)
                   FROM nffl.team_gateway_link
                  WHERE league_key=%s AND season_year=%s),
                (SELECT count(*)
                   FROM nffl.season_context
                  WHERE league_code='NFFL'
                    AND current_season_year=%s
                    AND is_active=false)
            """,
            (
                spec.current_league_key,
                spec.current_season_year,
                spec.current_league_key,
                spec.current_season_year,
                spec.current_league_key,
                spec.current_season_year,
                spec.current_league_key,
                spec.current_season_year,
                spec.current_season_year,
            ),
        )

        (
            team_count,
            mapping_count,
            bridge_count,
            gateway_count,
            inactive_context_count,
        ) = [int(v or 0) for v in cur.fetchone()]

        expected = spec.manager_count

        if (
            team_count != expected
            or mapping_count != expected
            or bridge_count != expected
            or gateway_count != expected
            or inactive_context_count != 1
        ):
            raise RuntimeError(
                "Staged-season reconciliation failed."
            )

        cur.execute(
            """
            SELECT count(*)
            FROM nffl.season_context
            WHERE league_code='NFFL'
              AND is_active=true
              AND current_season_year=%s
              AND current_league_key=%s
            """,
            (
                spec.prior_season_year,
                spec.prior_league_key,
            ),
        )

        if int(cur.fetchone()[0] or 0) != 1:
            raise RuntimeError(
                "Prior season is no longer the active NFFL context."
            )

    return NfflNewSeasonStageResult(
        season_year=spec.current_season_year,
        league_key=spec.current_league_key,
        teams_created=spec.manager_count,
        franchise_mappings_created=spec.manager_count,
        bridges_created=spec.manager_count,
        gateway_links_created=spec.manager_count,
        season_context_created=1,
        activated=False,
    )
