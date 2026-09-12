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
