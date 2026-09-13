from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class NfflNewSeasonSpec:
    """Validated, side-effect-free plan for one NFFL season transition."""

    league_code: str
    prior_season_year: int
    prior_league_key: str
    current_season_year: int
    current_league_key: str
    draft_key: str
    manager_count: int
    rounds_total: int
    qo_rounds: int
    expected_draft_rows: int
    profile: dict[str, Any]


def suggested_nffl_draft_key(season_year: int) -> str:
    """Return the conventional NFFL preseason draft key for a season."""
    year = int(season_year)
    if year <= 0:
        raise ValueError("season_year must be positive.")
    return f"nffl_{year}_preseason"


def build_nffl_new_season_spec(
    current_profile: Mapping[str, Any],
    *,
    target_league_key: str,
    target_season_year: int,
    target_draft_key: str,
) -> NfflNewSeasonSpec:
    """
    Build the next-season NFFL configuration without writing anything.

    The current active league profile is the template. League rules carry
    forward unchanged; only the season-scoped identity is replaced here.
    Future Commissioner UI may deliberately edit the proposed profile before
    it is persisted.
    """
    profile = deepcopy(dict(current_profile))

    league = profile.get("league")
    draft = profile.get("draft")
    features = profile.get("features")

    if not isinstance(league, dict):
        raise ValueError("current_profile.league must be a mapping.")
    if not isinstance(draft, dict):
        raise ValueError("current_profile.draft must be a mapping.")
    if not isinstance(features, dict):
        raise ValueError("current_profile.features must be a mapping.")

    prior_league_key = str(
        league.get("league_key") or ""
    ).strip()
    if not prior_league_key:
        raise ValueError(
            "current_profile.league.league_key must be non-empty."
        )

    try:
        prior_season_year = int(league["season_year"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "current_profile.league.season_year must be an integer."
        ) from exc

    new_league_key = str(target_league_key or "").strip()
    if not new_league_key:
        raise ValueError("target_league_key must be non-empty.")

    new_season_year = int(target_season_year)
    if new_season_year != prior_season_year + 1:
        raise ValueError(
            "target_season_year must equal prior_season_year + 1."
        )

    if new_league_key == prior_league_key:
        raise ValueError(
            "target_league_key must differ from the prior-season league key."
        )

    new_draft_key = str(target_draft_key or "").strip()
    if not new_draft_key:
        raise ValueError("target_draft_key must be non-empty.")

    try:
        manager_count = int(league["manager_count"])
        rounds_total = int(draft["rounds_total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "manager_count and rounds_total must be integers."
        ) from exc

    if manager_count <= 0:
        raise ValueError("manager_count must be positive.")
    if rounds_total <= 0:
        raise ValueError("rounds_total must be positive.")

    qo_enabled = bool(
        features.get("qualifying_offers", False)
    )

    if qo_enabled:
        try:
            qo_rounds = int(draft.get("qo_rounds", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "draft.qo_rounds must be an integer."
            ) from exc
    else:
        qo_rounds = 0

    if qo_rounds < 0:
        raise ValueError("qo_rounds must not be negative.")
    if qo_rounds > rounds_total:
        raise ValueError(
            "qo_rounds must not exceed rounds_total."
        )

    # Carry league rules forward unchanged while replacing only the
    # Yahoo/season-scoped identity.
    league["league_key"] = new_league_key
    league["season_year"] = new_season_year

    return NfflNewSeasonSpec(
        league_code="NFFL",
        prior_season_year=prior_season_year,
        prior_league_key=prior_league_key,
        current_season_year=new_season_year,
        current_league_key=new_league_key,
        draft_key=new_draft_key,
        manager_count=manager_count,
        rounds_total=rounds_total,
        qo_rounds=qo_rounds,
        expected_draft_rows=manager_count * rounds_total,
        profile=profile,
    )


@dataclass(frozen=True)
class NfflFranchiseMatch:
    """One proposed new-season team-to-franchise assignment."""

    target_team_key: str
    target_team_name: str
    target_owner_name: str | None
    target_owner_guid: str | None
    status: str
    franchise_id: int | None
    prior_team_key: str | None
    prior_team_name: str | None
    reason: str


@dataclass(frozen=True)
class NfflFranchiseMatchPreview:
    """Side-effect-free franchise rollover readiness result."""

    rows: tuple[NfflFranchiseMatch, ...]
    expected_manager_count: int
    auto_match_count: int
    review_count: int
    ready_for_apply: bool


def _clean_optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def build_nffl_franchise_match_preview(
    prior_mappings: list[Mapping[str, Any]],
    target_teams: list[Mapping[str, Any]],
    *,
    expected_manager_count: int,
) -> NfflFranchiseMatchPreview:
    """
    Preview new-season franchise continuity without writing anything.

    Safe automatic matching is intentionally conservative:
    - only a unique, nonblank owner_guid match may auto-link;
    - missing owner_guid requires Commissioner review;
    - changed/unmatched owner_guid requires Commissioner review;
    - duplicate prior owner_guid requires Commissioner review;
    - duplicate target owner_guid requires Commissioner review.

    owner_guid is only rollover evidence. franchise_id remains the stable
    cross-season identity.
    """
    expected = int(expected_manager_count)

    if expected <= 0:
        raise ValueError(
            "expected_manager_count must be positive."
        )

    prior_rows = list(prior_mappings or [])
    target_rows = list(target_teams or [])

    if len(prior_rows) != expected:
        raise ValueError(
            "prior_mappings count must equal expected_manager_count."
        )

    if len(target_rows) != expected:
        raise ValueError(
            "target_teams count must equal expected_manager_count."
        )

    prior_team_keys: set[str] = set()
    prior_franchise_ids: set[int] = set()
    prior_by_owner_guid: dict[
        str,
        list[Mapping[str, Any]],
    ] = {}

    for row in prior_rows:
        team_key = str(
            row.get("team_key") or ""
        ).strip()

        if not team_key:
            raise ValueError(
                "Every prior mapping must have a non-empty team_key."
            )

        if team_key in prior_team_keys:
            raise ValueError(
                f"Duplicate prior team_key: {team_key}"
            )

        prior_team_keys.add(team_key)

        raw_franchise_id = row.get("franchise_id")

        try:
            franchise_id = int(raw_franchise_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Every prior mapping must have an integer franchise_id."
            ) from exc

        if franchise_id <= 0:
            raise ValueError(
                "Every prior franchise_id must be positive."
            )

        if franchise_id in prior_franchise_ids:
            raise ValueError(
                f"Duplicate prior franchise_id: {franchise_id}"
            )

        prior_franchise_ids.add(franchise_id)

        owner_guid = _clean_optional_text(
            row.get("owner_guid")
        )

        if owner_guid:
            prior_by_owner_guid.setdefault(
                owner_guid,
                [],
            ).append(row)

    target_team_keys: set[str] = set()
    target_owner_guid_counts: dict[str, int] = {}

    for row in target_rows:
        team_key = str(
            row.get("team_key") or ""
        ).strip()

        if not team_key:
            raise ValueError(
                "Every target team must have a non-empty team_key."
            )

        if team_key in target_team_keys:
            raise ValueError(
                f"Duplicate target team_key: {team_key}"
            )

        target_team_keys.add(team_key)

        team_name = str(
            row.get("team_name") or ""
        ).strip()

        if not team_name:
            raise ValueError(
                f"Target team {team_key} has a blank team_name."
            )

        owner_guid = _clean_optional_text(
            row.get("owner_guid")
        )

        if owner_guid:
            target_owner_guid_counts[owner_guid] = (
                target_owner_guid_counts.get(
                    owner_guid,
                    0,
                )
                + 1
            )

    result_rows: list[NfflFranchiseMatch] = []

    for target in sorted(
        target_rows,
        key=lambda row: str(
            row.get("team_key") or ""
        ),
    ):
        team_key = str(
            target.get("team_key") or ""
        ).strip()
        team_name = str(
            target.get("team_name") or ""
        ).strip()
        owner_name = _clean_optional_text(
            target.get("owner_name")
        )
        owner_guid = _clean_optional_text(
            target.get("owner_guid")
        )

        status = "REVIEW_REQUIRED"
        franchise_id: int | None = None
        prior_team_key: str | None = None
        prior_team_name: str | None = None

        if not owner_guid:
            reason = "TARGET_OWNER_GUID_MISSING"

        elif target_owner_guid_counts.get(
            owner_guid,
            0,
        ) > 1:
            reason = "TARGET_OWNER_GUID_DUPLICATE"

        else:
            candidates = prior_by_owner_guid.get(
                owner_guid,
                [],
            )

            if not candidates:
                reason = "NO_OWNER_GUID_MATCH"

            elif len(candidates) > 1:
                reason = "PRIOR_OWNER_GUID_AMBIGUOUS"

            else:
                prior = candidates[0]

                franchise_id = int(
                    prior["franchise_id"]
                )
                prior_team_key = str(
                    prior.get("team_key") or ""
                ).strip()
                prior_team_name = _clean_optional_text(
                    prior.get("team_name")
                )

                status = "AUTO_MATCH"
                reason = "UNIQUE_OWNER_GUID_MATCH"

        result_rows.append(
            NfflFranchiseMatch(
                target_team_key=team_key,
                target_team_name=team_name,
                target_owner_name=owner_name,
                target_owner_guid=owner_guid,
                status=status,
                franchise_id=franchise_id,
                prior_team_key=prior_team_key,
                prior_team_name=prior_team_name,
                reason=reason,
            )
        )

    auto_match_count = sum(
        1
        for row in result_rows
        if row.status == "AUTO_MATCH"
    )

    review_count = len(result_rows) - auto_match_count

    return NfflFranchiseMatchPreview(
        rows=tuple(result_rows),
        expected_manager_count=expected,
        auto_match_count=auto_match_count,
        review_count=review_count,
        ready_for_apply=(review_count == 0),
    )

@dataclass(frozen=True)
class NfflResolvedFranchiseAssignment:
    """One final target-team-to-franchise assignment."""

    target_team_key: str
    franchise_id: int
    resolution_method: str


def resolve_nffl_franchise_assignments(
    preview: NfflFranchiseMatchPreview,
    prior_mappings: list[Mapping[str, Any]],
    manual_assignments: Mapping[str, int] | None = None,
) -> tuple[NfflResolvedFranchiseAssignment, ...]:
    """
    Resolve all target teams to exactly one prior-season franchise.

    Automatic matches are preserved. Commissioner assignments are accepted
    only for REVIEW_REQUIRED teams. The final result must be a one-to-one
    mapping across the complete prior franchise set.
    """
    manual = dict(manual_assignments or {})

    prior_franchise_ids: set[int] = set()

    for row in prior_mappings:
        try:
            franchise_id = int(row["franchise_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Every prior mapping must contain an integer franchise_id."
            ) from exc

        if franchise_id <= 0:
            raise ValueError(
                "Every prior franchise_id must be positive."
            )

        if franchise_id in prior_franchise_ids:
            raise ValueError(
                f"Duplicate prior franchise_id: {franchise_id}"
            )

        prior_franchise_ids.add(franchise_id)

    if len(prior_franchise_ids) != preview.expected_manager_count:
        raise ValueError(
            "Prior franchise count must equal expected_manager_count."
        )

    preview_by_team = {
        row.target_team_key: row
        for row in preview.rows
    }

    unknown_manual_teams = (
        set(manual) - set(preview_by_team)
    )

    if unknown_manual_teams:
        raise ValueError(
            "Manual assignment contains unknown target team(s): "
            + ", ".join(sorted(unknown_manual_teams))
        )

    resolved: list[NfflResolvedFranchiseAssignment] = []

    for row in preview.rows:
        if row.status == "AUTO_MATCH":
            if row.franchise_id is None:
                raise ValueError(
                    f"AUTO_MATCH team {row.target_team_key} "
                    "is missing franchise_id."
                )

            if row.target_team_key in manual:
                raise ValueError(
                    f"Manual assignment is not allowed for automatic match "
                    f"{row.target_team_key}."
                )

            franchise_id = int(row.franchise_id)
            method = "AUTO_OWNER_GUID"

        elif row.status == "REVIEW_REQUIRED":
            if row.target_team_key not in manual:
                raise ValueError(
                    f"Manual assignment required for "
                    f"{row.target_team_key}."
                )

            try:
                franchise_id = int(
                    manual[row.target_team_key]
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Manual franchise_id for {row.target_team_key} "
                    "must be an integer."
                ) from exc

            method = "COMMISSIONER_MANUAL"

        else:
            raise ValueError(
                f"Unknown preview status for "
                f"{row.target_team_key}: {row.status}"
            )

        if franchise_id not in prior_franchise_ids:
            raise ValueError(
                f"Franchise {franchise_id} is not part of the "
                "prior-season franchise set."
            )

        resolved.append(
            NfflResolvedFranchiseAssignment(
                target_team_key=row.target_team_key,
                franchise_id=franchise_id,
                resolution_method=method,
            )
        )

    if len(resolved) != preview.expected_manager_count:
        raise ValueError(
            "Resolved assignment count must equal expected_manager_count."
        )

    resolved_team_keys = {
        row.target_team_key
        for row in resolved
    }

    if len(resolved_team_keys) != len(resolved):
        raise ValueError(
            "Target teams must resolve exactly once."
        )

    resolved_franchise_ids = [
        row.franchise_id
        for row in resolved
    ]

    if len(set(resolved_franchise_ids)) != len(resolved_franchise_ids):
        raise ValueError(
            "Each franchise may be assigned to only one target team."
        )

    if set(resolved_franchise_ids) != prior_franchise_ids:
        raise ValueError(
            "Resolved assignments must use every prior franchise "
            "exactly once."
        )

    return tuple(
        sorted(
            resolved,
            key=lambda row: row.target_team_key,
        )
    )
