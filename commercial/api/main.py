from __future__ import annotations

import os
from collections import Counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from draftboard.state.commercial_league_profile import (
    CommercialLeagueProfileError,
    summarize_commercial_league_profile,
    validate_commercial_league_profile,
)


class LeagueSetupDraft(BaseModel):
    leagueName: str = Field(min_length=1)
    sport: str
    platform: str
    seasonYear: int = Field(ge=2000)
    managerCount: int = Field(ge=2)

    leagueModel: str

    keeperCount: int = Field(default=0, ge=0)
    keeperCostMode: str = "none"

    contractDurations: list[int] = Field(default_factory=list)
    restrictedRights: bool = False
    restrictedRightsLabel: str = ""
    prospectDesignation: bool = False
    franchiseDesignation: bool = False
    futurePickTrading: bool = False
    annualDraft: bool = False

    draftMethod: str
    executionMode: str = "offline"
    startingBudget: int = Field(default=0, ge=0)
    minimumBid: int = Field(default=0, ge=0)


SPORTS = {
    "Baseball": "baseball",
    "Football": "football",
    "Hockey": "hockey",
}

PLATFORMS = {
    "Yahoo": "yahoo",
    "ESPN": "espn",
    "Sleeper": "sleeper",
    "Fantrax": "fantrax",
    "Fleaflicker": "fleaflicker",
    "Manual / Other": "manual",
}

LEAGUE_MODELS = {
    "Redraft": "redraft",
    "Keeper": "keeper",
    "Dynasty": "dynasty",
    "Contract Keeper": "contract_keeper",
}

DRAFT_METHODS = {
    "Snake": "snake",
    "Straight / Linear": "straight",
    "Auction": "auction",
    "Custom / Commissioner-defined": "custom",
}

KEEPER_COST_MODES = {
    "none": "none",
    "round": "draft_round",
    "custom": "custom",
}


def _mapped(mapping: dict[str, str], value: str, field: str) -> str:
    try:
        return mapping[value]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported {field}: {value!r}."
        ) from exc


def _contract_slots(durations: list[int]) -> list[dict[str, int]]:
    if not durations:
        return []

    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        for value in durations
    ):
        raise ValueError(
            "contractDurations must contain integers >= 1."
        )

    counts = Counter(durations)

    return [
        {
            "duration_years": duration,
            "count": counts[duration],
        }
        for duration in sorted(counts, reverse=True)
    ]


def normalize_setup(setup: LeagueSetupDraft) -> dict:
    sport = _mapped(SPORTS, setup.sport, "sport")
    platform = _mapped(PLATFORMS, setup.platform, "platform")
    model = _mapped(
        LEAGUE_MODELS,
        setup.leagueModel,
        "league model",
    )
    method = _mapped(
        DRAFT_METHODS,
        setup.draftMethod,
        "draft method",
    )

    execution_mode = setup.executionMode.strip().lower()
    if execution_mode != "offline":
        raise ValueError(
            "The current commercial setup flow supports "
            "commissioner-operated offline execution only."
        )

    keeper_enabled = model == "keeper"
    contract_enabled = model == "contract_keeper"

    if keeper_enabled:
        keeper_cost = _mapped(
            KEEPER_COST_MODES,
            setup.keeperCostMode,
            "keeper cost mode",
        )
    else:
        keeper_cost = "none"

    profile = {
        "league": {
            "league_key": f"commercial.{uuid4().hex}",
            "name": setup.leagueName.strip(),
            "platform": platform,
            "sport": sport,
            "league_model": model,
            "season_year": setup.seasonYear,
            "manager_count": setup.managerCount,
        },
        "draft": {
            "method": method,
            "execution_mode": execution_mode,
            "pick_trades_allowed": (
                setup.futurePickTrading
                if model in {"dynasty", "contract_keeper"}
                else False
            ),
        },
        "player_control": {
            "keeper": {
                "enabled": keeper_enabled,
                "count": setup.keeperCount if keeper_enabled else 0,
                "cost_mode": keeper_cost,
            },
            "contracts": {
                "enabled": contract_enabled,
                "slots": (
                    _contract_slots(setup.contractDurations)
                    if contract_enabled
                    else []
                ),
            },
            "restricted_rights": {
                "enabled": (
                    contract_enabled
                    and setup.restrictedRights
                ),
                "label": (
                    setup.restrictedRightsLabel.strip()
                    if contract_enabled
                    and setup.restrictedRights
                    else ""
                ),
            },
            "franchise_designation": {
                "enabled": (
                    contract_enabled
                    and setup.franchiseDesignation
                ),
            },
            "prospect_designation": {
                "enabled": (
                    contract_enabled
                    and setup.prospectDesignation
                ),
            },
            "future_pick_trading": (
                setup.futurePickTrading
                if model in {"dynasty", "contract_keeper"}
                else False
            ),
            "annual_draft": (
                setup.annualDraft
                if model == "dynasty"
                else False
            ),
        },
        "runtime": {
            "db_scope_mode": "league_key",
        },
    }

    if method == "auction":
        profile["draft"]["starting_budget"] = setup.startingBudget
        profile["draft"]["minimum_bid"] = setup.minimumBid

    return profile


app = FastAPI(
    title="Commissioner Tools API",
    version="0.1.0",
)

origins = [
    value.strip()
    for value in os.getenv(
        "COMMERCIAL_WEB_ORIGINS",
        "http://localhost:3002",
    ).split(",")
    if value.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "commissioner-tools-api",
    }


@app.post("/api/leagues/validate")
def validate_league(
    setup: LeagueSetupDraft,
) -> dict:
    try:
        profile = normalize_setup(setup)
        validate_commercial_league_profile(profile)
        summary = summarize_commercial_league_profile(profile)
    except (ValueError, CommercialLeagueProfileError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return {
        "valid": True,
        "profile": profile,
        "summary": summary,
    }
