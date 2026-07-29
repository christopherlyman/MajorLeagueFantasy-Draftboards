from __future__ import annotations

from collections.abc import Collection, Mapping


CONTRACT_YEARS = (4, 3, 2)

ELIGIBLE_DRAFT_PICK_KINDS = frozenset(
    {
        "QO",
        "POACH",
        "FA",
    }
)


def allowed_contract_years(
    has_locked_ft: bool,
) -> tuple[int, ...]:
    """
    Return the new-contract slots available to a team.

    Every team receives 4-year and 3-year slots.
    The 2-year slot is unavailable when the team
    used a finalized, locked Franchise Tag.
    """
    if has_locked_ft:
        return (4, 3)

    return CONTRACT_YEARS


def eligible_drafted_player_keys(
    selection_rows: Collection[
        Mapping[str, object]
    ],
    active_contract_keys: Collection[str],
    locked_ft_keys: Collection[str],
) -> set[str]:
    """
    Derive eligibility from actual current-draft results.

    QO, POACH, and FA rows represent completed draft
    selections. CONTRACT and PT rows are prefilled
    commitments and do not create new eligibility.
    """
    blocked_keys = {
        str(player_key).strip()
        for player_key in (
            list(active_contract_keys)
            + list(locked_ft_keys)
        )
        if str(player_key).strip()
    }

    eligible_keys: set[str] = set()

    for row in selection_rows:
        player_key = str(
            row.get("yahoo_player_key") or ""
        ).strip()

        pick_kind = str(
            row.get("pick_kind") or ""
        ).strip().upper()

        if (
            player_key
            and pick_kind
            in ELIGIBLE_DRAFT_PICK_KINDS
            and player_key not in blocked_keys
        ):
            eligible_keys.add(player_key)

    return eligible_keys


def validate_contract_selections(
    selections: Mapping[int, str],
    eligible_player_keys: Collection[str],
    has_locked_ft: bool,
) -> dict[int, str]:
    """
    Validate an editable new-contract plan.

    Player eligibility must be recalculated from
    current database state immediately before saving.
    """
    allowed_years = set(
        allowed_contract_years(has_locked_ft)
    )

    eligible = {
        str(player_key).strip()
        for player_key in eligible_player_keys
        if str(player_key).strip()
    }

    normalized: dict[int, str] = {}

    for raw_years, raw_player_key in selections.items():
        years = int(raw_years)

        player_key = str(
            raw_player_key or ""
        ).strip()

        if years not in allowed_years:
            raise ValueError(
                f"{years}-year contract slot "
                "is not available."
            )

        if player_key:
            normalized[years] = player_key

    selected_players = list(
        normalized.values()
    )

    if len(selected_players) != len(
        set(selected_players)
    ):
        raise ValueError(
            "A player can only receive "
            "one new contract."
        )

    ineligible = sorted(
        set(selected_players) - eligible
    )

    if ineligible:
        raise ValueError(
            "One or more selected players are "
            "not eligible for a new contract: "
            + ", ".join(ineligible)
        )

    return normalized
