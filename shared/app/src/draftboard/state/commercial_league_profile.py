from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class CommercialLeagueProfileError(ValueError):
    """Raised when a commercial league profile violates the supported schema."""


SUPPORTED_SPORTS = {"baseball", "football"}
SUPPORTED_DRAFT_TYPES = {"standard"}
SUPPORTED_ORDER_MODES = {"snake", "straight"}
SUPPORTED_DRAFT_MODES = {"offline", "live"}
SUPPORTED_SCORING_FORMATS = {"h2h_points", "h2h_categories", "roto"}


def _section(profile: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = profile.get(key)
    if not isinstance(value, Mapping):
        raise CommercialLeagueProfileError(
            f"Commercial league profile section '{key}' must be a mapping."
        )
    return value


def _nonempty_text(section: Mapping[str, Any], key: str, path: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommercialLeagueProfileError(f"{path}.{key} must be non-empty text.")
    return value.strip()


def _integer(
    section: Mapping[str, Any],
    key: str,
    path: str,
    *,
    minimum: int | None = None,
) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommercialLeagueProfileError(f"{path}.{key} must be an integer.")
    if minimum is not None and value < minimum:
        raise CommercialLeagueProfileError(f"{path}.{key} must be >= {minimum}.")
    return value


def _boolean(section: Mapping[str, Any], key: str, path: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise CommercialLeagueProfileError(f"{path}.{key} must be boolean.")
    return value


def _validate_tag(player_control: Mapping[str, Any], key: str) -> None:
    path = f"player_control.{key}"
    tag = _section(player_control, key)
    enabled = _boolean(tag, "enabled", path)
    max_per_team = _integer(tag, "max_per_team", path, minimum=0)

    if enabled and max_per_team < 1:
        raise CommercialLeagueProfileError(
            f"{path}.max_per_team must be >= 1 when enabled=true."
        )
    if not enabled and max_per_team != 0:
        raise CommercialLeagueProfileError(
            f"{path}.max_per_team must equal 0 when enabled=false."
        )


def validate_commercial_league_profile(profile: Mapping[str, Any]) -> None:
    """
    Validate the provider-independent commercial league-profile boundary.

    This validator is intentionally additive. It does not replace the current
    shared/NFFL/MLF validators while the commercial product is being proven.
    """

    if not isinstance(profile, Mapping):
        raise CommercialLeagueProfileError(
            "Commercial league profile must be a top-level mapping."
        )

    required_sections = (
        "league",
        "draft",
        "scoring",
        "roster",
        "categories",
        "player_control",
        "runtime",
    )
    for key in required_sections:
        _section(profile, key)

    league = _section(profile, "league")
    draft = _section(profile, "draft")
    scoring = _section(profile, "scoring")
    roster = _section(profile, "roster")
    categories = _section(profile, "categories")
    player_control = _section(profile, "player_control")
    runtime = _section(profile, "runtime")

    _nonempty_text(league, "league_key", "league")
    _nonempty_text(league, "name", "league")
    _nonempty_text(league, "platform", "league")

    sport = _nonempty_text(league, "sport", "league").lower()
    if sport not in SUPPORTED_SPORTS:
        raise CommercialLeagueProfileError(
            f"league.sport must be one of: {', '.join(sorted(SUPPORTED_SPORTS))}."
        )

    _integer(league, "season_year", "league", minimum=2000)
    _integer(league, "manager_count", "league", minimum=2)

    draft_type = _nonempty_text(draft, "type", "draft").lower()
    if draft_type not in SUPPORTED_DRAFT_TYPES:
        raise CommercialLeagueProfileError(
            f"draft.type must be one of: {', '.join(sorted(SUPPORTED_DRAFT_TYPES))}."
        )

    order_mode = _nonempty_text(draft, "order_mode", "draft").lower()
    if order_mode not in SUPPORTED_ORDER_MODES:
        raise CommercialLeagueProfileError(
            "draft.order_mode must be one of: snake, straight."
        )

    draft_mode = _nonempty_text(draft, "mode", "draft").lower()
    if draft_mode not in SUPPORTED_DRAFT_MODES:
        raise CommercialLeagueProfileError(
            "draft.mode must be one of: offline, live."
        )

    _integer(draft, "rounds_total", "draft", minimum=1)
    _boolean(draft, "pick_trades_allowed", "draft")

    scoring_format = _nonempty_text(scoring, "format", "scoring").lower()
    if scoring_format not in SUPPORTED_SCORING_FORMATS:
        raise CommercialLeagueProfileError(
            "scoring.format must be one of: h2h_points, h2h_categories, roto."
        )

    positions = roster.get("positions")
    if (
        not isinstance(positions, list)
        or not positions
        or any(not isinstance(value, str) or not value.strip() for value in positions)
    ):
        raise CommercialLeagueProfileError(
            "roster.positions must be a non-empty list of non-empty strings."
        )

    if sport == "baseball":
        for key in ("batting", "pitching"):
            values = categories.get(key)
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
            ):
                raise CommercialLeagueProfileError(
                    f"categories.{key} must be a non-empty list of non-empty strings."
                )
    else:
        nonempty_lists = [
            value
            for value in categories.values()
            if isinstance(value, list) and value
        ]
        if not nonempty_lists:
            raise CommercialLeagueProfileError(
                "Football categories must contain at least one non-empty list."
            )

    if runtime.get("db_scope_mode") != "league_key":
        raise CommercialLeagueProfileError(
            "runtime.db_scope_mode must equal 'league_key'."
        )

    contracts = _section(player_control, "contracts")
    if not _boolean(contracts, "enabled", "player_control.contracts"):
        raise CommercialLeagueProfileError(
            "player_control.contracts.enabled must be true for the commercial MSP."
        )

    slots = contracts.get("slots")
    if not isinstance(slots, list) or not slots:
        raise CommercialLeagueProfileError(
            "player_control.contracts.slots must be a non-empty list."
        )

    durations: set[int] = set()

    for index, slot in enumerate(slots):
        path = f"player_control.contracts.slots[{index}]"
        if not isinstance(slot, Mapping):
            raise CommercialLeagueProfileError(f"{path} must be a mapping.")

        duration_years = _integer(slot, "duration_years", path, minimum=1)
        _integer(slot, "count", path, minimum=1)

        if duration_years in durations:
            raise CommercialLeagueProfileError(
                "Contract duration_years values must be unique; use count for repeated slots."
            )

        durations.add(duration_years)

    qualifying_offers = _section(player_control, "qualifying_offers")
    qo_enabled = _boolean(
        qualifying_offers,
        "enabled",
        "player_control.qualifying_offers",
    )
    qo_count = _integer(
        qualifying_offers,
        "count",
        "player_control.qualifying_offers",
        minimum=0,
    )

    if qo_enabled and qo_count < 1:
        raise CommercialLeagueProfileError(
            "player_control.qualifying_offers.count must be >= 1 when enabled=true."
        )
    if not qo_enabled and qo_count != 0:
        raise CommercialLeagueProfileError(
            "player_control.qualifying_offers.count must equal 0 when enabled=false."
        )

    _validate_tag(player_control, "franchise_tag")
    _validate_tag(player_control, "prospect_tag")


def summarize_commercial_league_profile(
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the small profile summary needed by setup and diagnostics."""

    validate_commercial_league_profile(profile)

    league = _section(profile, "league")
    draft = _section(profile, "draft")
    player_control = _section(profile, "player_control")
    contracts = _section(player_control, "contracts")
    qualifying_offers = _section(player_control, "qualifying_offers")

    return {
        "league_key": league["league_key"],
        "name": league["name"],
        "platform": league["platform"],
        "sport": str(league["sport"]).lower(),
        "season_year": league["season_year"],
        "manager_count": league["manager_count"],
        "rounds_total": draft["rounds_total"],
        "pick_trades_allowed": draft["pick_trades_allowed"],
        "contract_slots": sum(int(slot["count"]) for slot in contracts["slots"]),
        "contract_durations": [
            int(slot["duration_years"]) for slot in contracts["slots"]
        ],
        "qualifying_offers": qualifying_offers["count"],
        "franchise_tag_enabled": _section(
            player_control, "franchise_tag"
        )["enabled"],
        "prospect_tag_enabled": _section(
            player_control, "prospect_tag"
        )["enabled"],
    }
