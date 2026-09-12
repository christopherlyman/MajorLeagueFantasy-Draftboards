from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SUPPORTED_SPORTS = {"baseball", "football", "hockey"}
SUPPORTED_LEAGUE_MODELS = {
    "redraft",
    "keeper",
    "dynasty",
    "contract_keeper",
}
SUPPORTED_DRAFT_METHODS = {
    "snake",
    "straight",
    "auction",
    "custom",
}
SUPPORTED_EXECUTION_MODES = {"offline", "live"}


def _section(value: Mapping[str, Any], key: str, path: str = "") -> Mapping[str, Any]:
    result = value.get(key)
    full_path = f"{path}.{key}" if path else key
    if not isinstance(result, Mapping):
        raise ValueError(f"{full_path} must be a mapping.")
    return result


def _text(section: Mapping[str, Any], key: str, path: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} must be non-empty text.")
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
        raise ValueError(f"{path}.{key} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path}.{key} must be >= {minimum}.")
    return value


def _boolean(section: Mapping[str, Any], key: str, path: str) -> bool:
    value = section.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{path}.{key} must be boolean.")
    return value


def _contract_summary(
    contracts: Mapping[str, Any],
) -> tuple[bool, int, list[int]]:
    enabled = _boolean(
        contracts,
        "enabled",
        "player_control.contracts",
    )

    slots = contracts.get("slots")
    if not isinstance(slots, list):
        raise ValueError(
            "player_control.contracts.slots must be a list."
        )

    if not enabled:
        if slots:
            raise ValueError(
                "player_control.contracts.slots must be empty when enabled=false."
            )
        return False, 0, []

    if not slots:
        raise ValueError(
            "player_control.contracts.slots must be a non-empty list "
            "when enabled=true."
        )

    durations: set[int] = set()
    ordered_durations: list[int] = []
    total = 0

    for index, slot in enumerate(slots):
        path = f"player_control.contracts.slots[{index}]"

        if not isinstance(slot, Mapping):
            raise ValueError(f"{path} must be a mapping.")

        duration = _integer(
            slot,
            "duration_years",
            path,
            minimum=1,
        )
        count = _integer(
            slot,
            "count",
            path,
            minimum=1,
        )

        if duration in durations:
            raise ValueError(
                "Contract duration_years values must be unique; "
                "use count for repeated slots."
            )

        durations.add(duration)
        ordered_durations.append(duration)
        total += count

    return True, total, ordered_durations


def validate_v2_profile(profile: Mapping[str, Any]) -> None:
    if not isinstance(profile, Mapping):
        raise ValueError(
            "Commercial league profile must be a top-level mapping."
        )

    league = _section(profile, "league")
    draft = _section(profile, "draft")
    player_control = _section(profile, "player_control")
    runtime = _section(profile, "runtime")

    _text(league, "league_key", "league")
    _text(league, "name", "league")
    _text(league, "platform", "league")

    sport = _text(league, "sport", "league").lower()
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(
            "league.sport must be one of: baseball, football, hockey."
        )

    model = _text(
        league,
        "league_model",
        "league",
    ).lower()

    if model not in SUPPORTED_LEAGUE_MODELS:
        raise ValueError(
            "league.league_model must be one of: "
            "redraft, keeper, dynasty, contract_keeper."
        )

    _integer(league, "season_year", "league", minimum=2000)
    _integer(league, "manager_count", "league", minimum=2)

    method = _text(draft, "method", "draft").lower()
    if method not in SUPPORTED_DRAFT_METHODS:
        raise ValueError(
            "draft.method must be one of: "
            "snake, straight, auction, custom."
        )

    execution_mode = _text(
        draft,
        "execution_mode",
        "draft",
    ).lower()

    if execution_mode not in SUPPORTED_EXECUTION_MODES:
        raise ValueError(
            "draft.execution_mode must be one of: offline, live."
        )

    _boolean(draft, "pick_trades_allowed", "draft")

    if method == "auction":
        budget = _integer(
            draft,
            "starting_budget",
            "draft",
            minimum=1,
        )
        minimum_bid = _integer(
            draft,
            "minimum_bid",
            "draft",
            minimum=1,
        )
        if minimum_bid > budget:
            raise ValueError(
                "draft.minimum_bid cannot exceed draft.starting_budget."
            )

    keeper = _section(
        player_control,
        "keeper",
        "player_control",
    )
    keeper_enabled = _boolean(
        keeper,
        "enabled",
        "player_control.keeper",
    )
    keeper_count = _integer(
        keeper,
        "count",
        "player_control.keeper",
        minimum=0,
    )
    _text(
        keeper,
        "cost_mode",
        "player_control.keeper",
    )

    if model == "keeper" and not keeper_enabled:
        raise ValueError(
            "player_control.keeper.enabled must be true for keeper leagues."
        )

    if model == "keeper" and keeper_count < 1:
        raise ValueError(
            "player_control.keeper.count must be >= 1 for keeper leagues."
        )

    contracts = _section(
        player_control,
        "contracts",
        "player_control",
    )
    contracts_enabled, _, _ = _contract_summary(contracts)

    if model == "contract_keeper" and not contracts_enabled:
        raise ValueError(
            "player_control.contracts.enabled must be true "
            "for contract_keeper leagues."
        )

    restricted_rights = _section(
        player_control,
        "restricted_rights",
        "player_control",
    )
    restricted_enabled = _boolean(
        restricted_rights,
        "enabled",
        "player_control.restricted_rights",
    )

    if restricted_enabled:
        _text(
            restricted_rights,
            "label",
            "player_control.restricted_rights",
        )

    for key in (
        "franchise_designation",
        "prospect_designation",
    ):
        designation = _section(
            player_control,
            key,
            "player_control",
        )
        _boolean(
            designation,
            "enabled",
            f"player_control.{key}",
        )

    _boolean(
        player_control,
        "future_pick_trading",
        "player_control",
    )
    _boolean(
        player_control,
        "annual_draft",
        "player_control",
    )

    if runtime.get("db_scope_mode") != "league_key":
        raise ValueError(
            "runtime.db_scope_mode must equal 'league_key'."
        )


def summarize_v2_profile(
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    validate_v2_profile(profile)

    league = _section(profile, "league")
    draft = _section(profile, "draft")
    player_control = _section(profile, "player_control")
    keeper = _section(
        player_control,
        "keeper",
        "player_control",
    )
    contracts = _section(
        player_control,
        "contracts",
        "player_control",
    )
    restricted_rights = _section(
        player_control,
        "restricted_rights",
        "player_control",
    )

    _, contract_slots, durations = _contract_summary(contracts)

    return {
        "league_key": league["league_key"],
        "name": league["name"],
        "platform": league["platform"],
        "sport": str(league["sport"]).lower(),
        "league_model": str(league["league_model"]).lower(),
        "season_year": league["season_year"],
        "manager_count": league["manager_count"],
        "draft_method": str(draft["method"]).lower(),
        "execution_mode": str(draft["execution_mode"]).lower(),
        "pick_trades_allowed": draft["pick_trades_allowed"],
        "keeper_count": keeper["count"],
        "contract_slots": contract_slots,
        "contract_durations": durations,
        "restricted_rights_enabled": restricted_rights["enabled"],
        "restricted_rights_label": restricted_rights.get("label", ""),
        "future_pick_trading": player_control["future_pick_trading"],
        "annual_draft": player_control["annual_draft"],
    }
