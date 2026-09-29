from __future__ import annotations

import os
from collections import Counter
from uuid import uuid4

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.auth import require_commercial_principal
from api.yahoo_legacy_token import (
    YahooLegacyTokenBridgeError,
    get_legacy_yahoo_access_token,
)

from draftboard.state.commercial_league_profile import (
    CommercialLeagueProfileError,
    summarize_commercial_league_profile,
    validate_commercial_league_profile,
)
from draftboard.state.commercial_authorization_repository import (
    CommercialAuthorizationRepositoryError,
    can_administer_commercial_league,
    grant_commercial_commissioner,
)
from draftboard.state.commercial_franchise_repository import (
    CommercialFranchiseRepositoryError,
    initialize_commercial_league_franchises,
    load_commercial_league_franchises,
)
from draftboard.state.commercial_provider_repository import (
    CommercialProviderRepositoryError,
    load_provider_connection,
)
from draftboard.state.commercial_yahoo_adapter import (
    YahooFantasyAdapter,
    YahooFantasyAdapterError,
)
from draftboard.state.commercial_league_profile_repository import (
    CommercialLeagueProfileRepositoryError,
    load_commercial_league_profile,
    save_commercial_league_profile,
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


def normalize_setup(
    setup: LeagueSetupDraft,
    *,
    league_key: str | None = None,
) -> dict:
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
            "league_key": league_key or f"validation.{uuid4().hex}",
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



def database_connection():
    required = (
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
    )

    missing = [
        key
        for key in required
        if not os.getenv(key)
    ]

    if missing:
        raise RuntimeError(
            "Database configuration is incomplete: "
            + ", ".join(missing)
        )

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname=os.environ["PGDATABASE"],
    )


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


@app.get("/api/auth/me")
def auth_me(
    request: Request,
) -> dict:
    with database_connection() as connection:
        principal = require_commercial_principal(
            connection,
            request=request,
        )

    return {
        "authenticated": True,
        "user": {
            "user_id": principal.user_id,
            "email": principal.email_normalized,
            "is_site_admin": principal.is_site_admin,
            "must_change_password": (
                principal.must_change_password
            ),
        },
    }


def _yahoo_league_response(item) -> dict:
    return {
        "league_key": item.league_key,
        "league_id": item.league_id,
        "name": item.name,
        "game_key": item.game_key,
        "season": item.season,
        "num_teams": item.num_teams,
    }


@app.get(
    "/api/providers/yahoo/connections/"
    "{provider_connection_id}/leagues"
)
def get_yahoo_connection_leagues(
    provider_connection_id: int,
    request: Request,
    game_key: str,
) -> dict:
    try:
        with database_connection() as connection:
            principal = require_commercial_principal(
                connection,
                request=request,
            )

            provider_connection = (
                load_provider_connection(
                    connection,
                    user_id=principal.user_id,
                    provider_connection_id=(
                        provider_connection_id
                    ),
                )
            )

            if provider_connection is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "Provider connection was not found."
                    ),
                )

            if (
                provider_connection.provider_code
                != "yahoo"
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Provider connection is not Yahoo."
                    ),
                )

            if provider_connection.status != "active":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Yahoo provider connection "
                        "is not active."
                    ),
                )

            normalized_game_key = str(
                game_key or ""
            ).strip()

            if (
                not normalized_game_key
                or not normalized_game_key.isdigit()
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Yahoo game_key must be numeric."
                    ),
                )

            access_token = (
                get_legacy_yahoo_access_token(
                    connection
                )
            )

        leagues = (
            YahooFantasyAdapter()
            .fetch_leagues(
                access_token=access_token,
                game_key=normalized_game_key,
            )
        )

        return {
            "provider_connection_id":
                provider_connection.provider_connection_id,
            "provider": "yahoo",
            "game_key":
                normalized_game_key,
            "leagues": [
                _yahoo_league_response(
                    item
                )
                for item in leagues
            ],
        }

    except HTTPException:
        raise

    except CommercialProviderRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Provider connection service "
                "is unavailable."
            ),
        ) from exc

    except YahooLegacyTokenBridgeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Yahoo connection is unavailable."
            ),
        ) from exc

    except YahooFantasyAdapterError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Yahoo Fantasy service is unavailable."
            ),
        ) from exc

    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Yahoo league discovery "
                "is unavailable."
            ),
        ) from exc


def _yahoo_team_response(item) -> dict:
    return {
        "team_key": item.team_key,
        "team_id": item.team_id,
        "name": item.name,
        "owner_name": item.owner_name,
        "provider_manager_id": item.manager_id,
        "owner_guid": item.owner_guid,
    }


@app.get(
    "/api/leagues/{league_key}/{season_year}/"
    "providers/yahoo/connections/"
    "{provider_connection_id}/teams/preview"
)
def preview_yahoo_league_teams(
    league_key: str,
    season_year: int,
    provider_connection_id: int,
    request: Request,
    provider_league_key: str,
) -> dict:
    try:
        normalized_provider_league_key = str(
            provider_league_key or ""
        ).strip()

        parts = normalized_provider_league_key.split(
            ".l.",
            1,
        )

        if (
            len(parts) != 2
            or not parts[0].isdigit()
            or not parts[1].strip()
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Yahoo provider_league_key "
                    "has an invalid format."
                ),
            )

        provider_game_key = parts[0]

        with database_connection() as connection:
            principal = require_commercial_principal(
                connection,
                request=request,
            )

            if not can_administer_commercial_league(
                connection,
                user_id=principal.user_id,
                league_key=league_key,
                season_year=season_year,
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Commissioner access required.",
                )

            stored_profile = (
                load_commercial_league_profile(
                    connection,
                    league_key,
                    season_year,
                )
            )

            league_profile = (
                stored_profile.profile.get("league")
            )

            if not isinstance(
                league_profile,
                dict,
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Commercial league profile "
                        "is missing league metadata."
                    ),
                )

            profile_platform = str(
                league_profile.get(
                    "platform",
                    "",
                )
            ).strip().lower()

            if profile_platform != "yahoo":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Commercial league platform "
                        "is not Yahoo."
                    ),
                )

            expected_manager_count = (
                league_profile.get(
                    "manager_count"
                )
            )

            if (
                isinstance(
                    expected_manager_count,
                    bool,
                )
                or not isinstance(
                    expected_manager_count,
                    int,
                )
                or expected_manager_count <= 0
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Commercial league manager_count "
                        "is invalid."
                    ),
                )

            provider_connection = (
                load_provider_connection(
                    connection,
                    user_id=principal.user_id,
                    provider_connection_id=(
                        provider_connection_id
                    ),
                )
            )

            if provider_connection is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "Provider connection was not found."
                    ),
                )

            if (
                provider_connection.provider_code
                != "yahoo"
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Provider connection is not Yahoo."
                    ),
                )

            if provider_connection.status != "active":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Yahoo provider connection "
                        "is not active."
                    ),
                )

            access_token = (
                get_legacy_yahoo_access_token(
                    connection
                )
            )

        adapter = YahooFantasyAdapter()

        visible_leagues = adapter.fetch_leagues(
            access_token=access_token,
            game_key=provider_game_key,
        )

        selected_league = next(
            (
                item
                for item in visible_leagues
                if item.league_key
                == normalized_provider_league_key
            ),
            None,
        )

        if selected_league is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Selected Yahoo league is not "
                    "available to this connection."
                ),
            )

        teams = adapter.fetch_teams(
            access_token=access_token,
            league_key=(
                normalized_provider_league_key
            ),
        )

        actual_team_count = len(teams)

        team_count_matches = (
            actual_team_count
            == expected_manager_count
        )

        return {
            "commercial_league": {
                "league_key": league_key,
                "season_year": season_year,
                "name": league_profile.get("name"),
                "manager_count":
                    expected_manager_count,
            },
            "provider_connection_id":
                provider_connection.provider_connection_id,
            "provider": "yahoo",
            "selected_league":
                _yahoo_league_response(
                    selected_league
                ),
            "import_preview": {
                "expected_team_count":
                    expected_manager_count,
                "provider_declared_team_count":
                    selected_league.num_teams,
                "actual_team_count":
                    actual_team_count,
                "team_count_matches":
                    team_count_matches,
                "ready_to_import":
                    team_count_matches,
            },
            "teams": [
                _yahoo_team_response(item)
                for item in teams
            ],
        }

    except HTTPException:
        raise

    except CommercialAuthorizationRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "League authorization is unavailable."
            ),
        ) from exc

    except CommercialLeagueProfileRepositoryError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except CommercialProviderRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Provider connection service "
                "is unavailable."
            ),
        ) from exc

    except YahooLegacyTokenBridgeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Yahoo connection is unavailable."
            ),
        ) from exc

    except YahooFantasyAdapterError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Yahoo Fantasy service is unavailable."
            ),
        ) from exc

    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Yahoo team preview is unavailable."
            ),
        ) from exc


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


@app.post("/api/leagues")
def create_league(
    setup: LeagueSetupDraft,
    request: Request,
) -> dict:
    league_key = f"commercial.{uuid4().hex}"

    try:
        profile = normalize_setup(
            setup,
            league_key=league_key,
        )
        validate_commercial_league_profile(profile)

        with database_connection() as connection:
            principal = require_commercial_principal(
                connection,
                request=request,
            )

            result = save_commercial_league_profile(
                connection,
                profile,
                changed_by=f"auth_user:{principal.user_id}",
                notes="Initial league creation.",
                expected_profile_version=0,
                manage_transaction=False,
            )

            if not result.created:
                raise CommercialLeagueProfileRepositoryError(
                    "Generated league key unexpectedly already existed."
                )

            if result.profile_version != 1:
                raise CommercialLeagueProfileRepositoryError(
                    "Initial league profile did not start at version 1."
                )

            stored = load_commercial_league_profile(
                connection,
                result.league_key,
                result.season_year,
            )

            if stored.profile_version != 1:
                raise CommercialLeagueProfileRepositoryError(
                    "Reloaded league profile was not version 1."
                )

            if stored.profile != profile:
                raise CommercialLeagueProfileRepositoryError(
                    "Reloaded profile did not match the saved profile."
                )

            commissioner_role = grant_commercial_commissioner(
                connection,
                user_id=principal.user_id,
                league_key=stored.league_key,
                season_year=stored.season_year,
                manage_transaction=False,
            )

            if (
                commissioner_role.user_id != principal.user_id
                or commissioner_role.league_key != stored.league_key
                or commissioner_role.season_year != stored.season_year
                or commissioner_role.role_code != "commissioner"
                or not commissioner_role.active
            ):
                raise CommercialAuthorizationRepositoryError(
                    "Commissioner ownership verification failed."
                )

        return {
            "created": True,
            "league_key": stored.league_key,
            "season_year": stored.season_year,
            "profile_version": stored.profile_version,
            "profile": stored.profile,
            "summary": summarize_commercial_league_profile(
                stored.profile
            ),
        }

    except CommercialLeagueProfileError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except CommercialLeagueProfileRepositoryError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except CommercialAuthorizationRepositoryError as exc:
        raise HTTPException(
            status_code=503,
            detail="League authorization is unavailable.",
        ) from exc

    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail="League persistence is unavailable.",
        ) from exc


@app.get("/api/leagues/{league_key}/{season_year}")
def get_league(
    league_key: str,
    season_year: int,
) -> dict:
    try:
        with database_connection() as connection:
            stored = load_commercial_league_profile(
                connection,
                league_key,
                season_year,
            )

        return {
            "league_key": stored.league_key,
            "season_year": stored.season_year,
            "profile_version": stored.profile_version,
            "is_active": stored.is_active,
            "profile": stored.profile,
            "summary": summarize_commercial_league_profile(
                stored.profile
            ),
        }

    except CommercialLeagueProfileRepositoryError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail="League persistence is unavailable.",
        ) from exc


class FranchiseDraft(BaseModel):
    team_name: str
    owner_name: str | None = None


class FranchiseSetupRequest(BaseModel):
    franchises: list[FranchiseDraft]


def _franchise_response(item) -> dict:
    return {
        "franchise_id": item.franchise_id,
        "franchise_name": item.franchise_name,
        "league_key": item.league_key,
        "season_year": item.season_year,
        "team_key": item.team_key,
        "team_name": item.team_name,
        "owner_name": item.owner_name,
        "source": item.source,
    }


@app.get(
    "/api/leagues/{league_key}/{season_year}/franchises"
)
def get_league_franchises(
    league_key: str,
    season_year: int,
) -> dict:
    try:
        with database_connection() as connection:
            load_commercial_league_profile(
                connection,
                league_key,
                season_year,
            )

            franchises = load_commercial_league_franchises(
                connection,
                league_key,
                season_year,
            )

        return {
            "league_key": league_key,
            "season_year": season_year,
            "count": len(franchises),
            "franchises": [
                _franchise_response(item)
                for item in franchises
            ],
        }

    except CommercialLeagueProfileRepositoryError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except CommercialFranchiseRepositoryError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail="Franchise persistence is unavailable.",
        ) from exc


@app.post(
    "/api/leagues/{league_key}/{season_year}/franchises"
)
def initialize_league_franchises(
    league_key: str,
    season_year: int,
    request: FranchiseSetupRequest,
) -> dict:
    try:
        with database_connection() as connection:
            load_commercial_league_profile(
                connection,
                league_key,
                season_year,
            )

            franchises = initialize_commercial_league_franchises(
                connection,
                league_key,
                season_year,
                [
                    {
                        "team_name": item.team_name,
                        "owner_name": item.owner_name,
                    }
                    for item in request.franchises
                ],
                source="manual",
            )

        return {
            "initialized": True,
            "league_key": league_key,
            "season_year": season_year,
            "count": len(franchises),
            "franchises": [
                _franchise_response(item)
                for item in franchises
            ],
        }

    except CommercialLeagueProfileRepositoryError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except CommercialFranchiseRepositoryError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(
            status_code=503,
            detail="Franchise persistence is unavailable.",
        ) from exc
