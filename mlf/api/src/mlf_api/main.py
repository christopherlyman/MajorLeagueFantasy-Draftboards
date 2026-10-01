from __future__ import annotations

import os

import psycopg
from urllib.parse import urlsplit
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from draftboard.data.draft_runtime import submit_draft_pick_atomic
from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)

from .gateway import (
    GATEWAY_COOKIE_MAX_AGE_SECONDS,
    get_gateway_cookie_name,
    pack_manager_cookie,
    public_principal,
    resolve_manager_principal,
)
from .gateway_store import (
    claim_team_gateway_link,
    record_clear_browser,
)
from .models import (
    DraftPickSubmitRequest,
    DraftPickSubmitResponse,
    GatewayPrincipal,
    HealthResponse,
)


app = FastAPI(
    title="MLF DraftBoard API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

PRODUCTION_ORIGIN = "https://mlf.majorleaguefantasy.app"


def _service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "service_unavailable"},
    )


def _invalid_gateway_link() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "invalid_gateway_link"},
    )



def _bad_request(
    code: str = "invalid_request",
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code},
    )


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "authentication_required"},
    )


def _forbidden(
    code: str = "forbidden",
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code},
    )


def _not_found(
    code: str = "not_found",
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": code},
    )


def _conflict(
    code: str = "draft_conflict",
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code},
    )


def _normalized_origin(
    value: str | None,
) -> str | None:
    raw = str(value or "").strip()

    if not raw:
        return None

    try:
        parsed = urlsplit(raw)
        scheme = str(parsed.scheme or "").lower()
        hostname = str(parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None

    if scheme not in {"http", "https"} or not hostname:
        return None

    if (
        port is None
        or (scheme == "https" and port == 443)
        or (scheme == "http" and port == 80)
    ):
        return f"{scheme}://{hostname}"

    return f"{scheme}://{hostname}:{port}"


def _require_json_content_type(
    request: Request,
) -> None:
    content_type = str(
        request.headers.get("content-type") or ""
    ).split(";", 1)[0].strip().lower()

    if content_type != "application/json":
        raise _bad_request("json_required")


def _require_same_origin(
    request: Request,
) -> None:
    origin = request.headers.get("origin")

    if origin:
        candidate = _normalized_origin(origin)
    else:
        candidate = _normalized_origin(
            request.headers.get("referer")
        )

    if candidate != PRODUCTION_ORIGIN:
        raise _forbidden("invalid_origin")


def _require_manager_principal(
    request: Request,
) -> dict[str, object]:
    raw_cookie = request.cookies.get(
        get_gateway_cookie_name()
    )

    if not raw_cookie:
        raise _unauthenticated()

    try:
        principal = resolve_manager_principal(
            raw_cookie
        )
    except Exception:
        raise _service_unavailable() from None

    if (
        principal is None
        or principal.get("is_authenticated") is not True
        or principal.get("role") != "manager"
        or not principal.get("team_key")
        or not principal.get("franchise_id")
    ):
        raise _unauthenticated()

    return principal


@app.exception_handler(RequestValidationError)
async def _request_validation_error(
    _request: Request,
    _exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": {
                "code": "invalid_request",
            }
        },
    )


def _safe_next_path(
    value: str | None,
) -> str:
    path = str(value or "/").strip()

    if (
        not path.startswith("/")
        or path.startswith("//")
    ):
        return "/"

    return path


def _database_ping() -> None:
    with psycopg.connect(
        get_postgres_dsn()
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()

    if row is None or row[0] != 1:
        raise RuntimeError(
            "Database health proof did not return 1."
        )


@app.get(
    "/health",
    response_model=HealthResponse,
)
def health() -> HealthResponse:
    try:
        _database_ping()
    except Exception:
        raise _service_unavailable() from None

    return HealthResponse(
        status="ok",
        database="ok",
    )


@app.get(
    "/auth/me",
    response_model=GatewayPrincipal,
)
def auth_me(
    request: Request,
    response: Response,
) -> GatewayPrincipal:
    cookie_name = get_gateway_cookie_name()
    raw_cookie = request.cookies.get(cookie_name)

    if not raw_cookie:
        return GatewayPrincipal(
            **public_principal()
        )

    try:
        principal = resolve_manager_principal(
            raw_cookie
        )
    except Exception:
        raise _service_unavailable() from None

    if principal is None:
        response.delete_cookie(
            key=cookie_name,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )

        return GatewayPrincipal(
            **public_principal()
        )

    return GatewayPrincipal(
        **principal
    )



@app.post(
    "/drafts/{draft_key}/picks",
    response_model=DraftPickSubmitResponse,
)
def submit_draft_pick(
    draft_key: str,
    payload: DraftPickSubmitRequest,
    request: Request,
) -> DraftPickSubmitResponse:
    _require_json_content_type(request)
    _require_same_origin(request)

    principal = _require_manager_principal(
        request
    )

    active_draft_key = str(
        get_draft_key() or ""
    ).strip()

    if not active_draft_key:
        raise _service_unavailable()

    if str(draft_key).strip() != active_draft_key:
        raise _not_found("draft_not_found")

    principal_team_key = str(
        principal["team_key"]
    ).strip()

    requested_owner = str(
        payload.expected_owner_team_key or ""
    ).strip()

    if requested_owner != principal_team_key:
        raise _forbidden(
            "pick_owner_forbidden"
        )

    actor = (
        "api:manager:"
        f"{int(principal['franchise_id'])}"
    )

    try:
        execution = submit_draft_pick_atomic(
            dsn=get_postgres_dsn(),
            draft_key=active_draft_key,
            pick_id=payload.pick_id,
            expected_owner_team_key=principal_team_key,
            yahoo_player_key=payload.yahoo_player_key,
            expected_pick_kind=payload.expected_pick_kind,
            selected_by=actor,
        )

    except ValueError:
        raise _bad_request() from None

    except psycopg.errors.RaiseException as exc:
        message = str(
            getattr(
                exc.diag,
                "message_primary",
                "",
            )
            or ""
        ).strip()

        if (
            (
                message.startswith("Draft ")
                and message.endswith(" not found.")
            )
            or (
                message.startswith("Draft pick ")
                and message.endswith(" not found.")
            )
            or (
                message.startswith("Player ")
                and message.endswith(
                    " is not in the active MLF player universe."
                )
            )
        ):
            raise _not_found() from None

        raise _conflict() from None

    except psycopg.Error:
        raise _service_unavailable() from None

    except RuntimeError:
        raise _service_unavailable() from None

    return DraftPickSubmitResponse(
        result_status=execution.result_status,
        executed_pick_id=execution.executed_pick_id,
        selecting_team_key=execution.selecting_team_key,
        selected_player_key=execution.selected_player_key,
        selected_pick_kind=execution.selected_pick_kind,
        next_pick_id=execution.next_pick_id,
        selected_at_utc=execution.selected_at_utc,
    )


@app.get("/gateway/claim")
def gateway_claim(
    token: str,
    next: str = "/",
) -> RedirectResponse:
    try:
        linked_team = claim_team_gateway_link(
            token
        )
    except Exception:
        raise _service_unavailable() from None

    if linked_team is None:
        raise _invalid_gateway_link()

    try:
        signed_cookie = pack_manager_cookie(
            franchise_id=int(
                linked_team["franchise_id"]
            ),
            team_key=str(
                linked_team["team_key"]
            ),
        )
    except Exception:
        raise _service_unavailable() from None

    response = RedirectResponse(
        url=_safe_next_path(next),
        status_code=status.HTTP_303_SEE_OTHER,
    )

    response.set_cookie(
        key=get_gateway_cookie_name(),
        value=signed_cookie,
        max_age=GATEWAY_COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return response


@app.get("/gateway/clear")
def gateway_clear(
    request: Request,
    next: str = "/",
) -> RedirectResponse:
    cookie_name = get_gateway_cookie_name()
    raw_cookie = request.cookies.get(cookie_name)

    if raw_cookie:
        try:
            principal = resolve_manager_principal(
                raw_cookie
            )

            if principal is not None:
                record_clear_browser(
                    franchise_id=int(
                        principal["franchise_id"]
                    ),
                    team_key=str(
                        principal["team_key"]
                    ),
                    team_name=(
                        str(principal["team_name"])
                        if principal["team_name"] is not None
                        else None
                    ),
                )
        except Exception:
            pass

    response = RedirectResponse(
        url=_safe_next_path(next),
        status_code=status.HTTP_303_SEE_OTHER,
    )

    response.delete_cookie(
        key=cookie_name,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return response

# ------------------------------------------------------------------
# MLF COMMISSIONER READ-ONLY GATEWAY
#
# This authority is deliberately separate from the manager Team Gateway.
# It currently authorizes only read-only commissioner endpoints.
# Future commissioner mutations require a separately proven authorization
# contract and must not rely on this read-only bearer credential alone.
# ------------------------------------------------------------------

from urllib.parse import urlencode

from mlf_api.commissioner_auth import (
    COMMISSIONER_COOKIE_MAX_AGE_SECONDS,
    commissioner_token_is_valid,
    get_commissioner_cookie_name,
    pack_commissioner_cookie,
    resolve_commissioner_cookie,
)
from mlf_api.commissioner_store import (
    get_commissioner_draft_order_state,
)
from mlf_api.gateway_store import get_team_gateway_links
from mlf_api.models import (
    CommissionerDraftOrderState,
    CommissionerPrincipal,
    ManagerGatewayLink,
)


def _public_commissioner_principal() -> dict[str, object]:
    return {
        "is_authenticated": False,
        "role": "public",
        "league_key": str(get_league_key()),
        "season_year": int(get_season_year()),
        "display_name": "Public",
        "acting_as": "public",
    }


def _resolve_commissioner_request(
    request: Request,
) -> dict[str, object] | None:
    raw_cookie = request.cookies.get(
        get_commissioner_cookie_name()
    )

    if not raw_cookie:
        return None

    return resolve_commissioner_cookie(
        raw_cookie
    )


@app.get("/gateway/commissioner/claim")
def commissioner_gateway_claim(
    token: str,
    next: str = "/commissioner",
) -> RedirectResponse:
    try:
        valid = commissioner_token_is_valid(
            token
        )
    except Exception:
        raise _service_unavailable() from None

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "invalid_commissioner_link"
            },
        )

    try:
        signed_cookie = pack_commissioner_cookie()
    except Exception:
        raise _service_unavailable() from None

    response = RedirectResponse(
        url=_safe_next_path(next),
        status_code=status.HTTP_303_SEE_OTHER,
    )

    response.set_cookie(
        key=get_commissioner_cookie_name(),
        value=signed_cookie,
        max_age=COMMISSIONER_COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return response


@app.get("/gateway/commissioner/clear")
def commissioner_gateway_clear(
    next: str = "/",
) -> RedirectResponse:
    response = RedirectResponse(
        url=_safe_next_path(next),
        status_code=status.HTTP_303_SEE_OTHER,
    )

    response.delete_cookie(
        key=get_commissioner_cookie_name(),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return response


@app.get(
    "/commissioner/auth/me",
    response_model=CommissionerPrincipal,
)
def commissioner_auth_me(
    request: Request,
    response: Response,
) -> CommissionerPrincipal:
    try:
        principal = _resolve_commissioner_request(
            request
        )
    except Exception:
        raise _service_unavailable() from None

    if principal is None:
        return CommissionerPrincipal(
            **_public_commissioner_principal()
        )

    return CommissionerPrincipal(
        **principal
    )


@app.get(
    "/commissioner/draft-order",
    response_model=CommissionerDraftOrderState,
)
def commissioner_draft_order(
    request: Request,
) -> CommissionerDraftOrderState:
    try:
        principal = _resolve_commissioner_request(
            request
        )
    except Exception:
        raise _service_unavailable() from None

    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "commissioner_required"
            },
        )

    try:
        state = get_commissioner_draft_order_state()
    except Exception:
        raise _service_unavailable() from None

    return CommissionerDraftOrderState(
        **state
    )


@app.get(
    "/commissioner/manager-links",
    response_model=list[ManagerGatewayLink],
)
def commissioner_manager_links(
    request: Request,
) -> list[ManagerGatewayLink]:
    try:
        principal = _resolve_commissioner_request(
            request
        )
    except Exception:
        raise _service_unavailable() from None

    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "commissioner_required"
            },
        )

    try:
        rows = get_team_gateway_links()
    except Exception:
        raise _service_unavailable() from None

    base_url = str(
        os.environ.get(
            "MLF_PUBLIC_URL",
            "https://mlf.majorleaguefantasy.app",
        )
        or "https://mlf.majorleaguefantasy.app"
    ).rstrip("/")

    output: list[ManagerGatewayLink] = []

    for row in rows:
        link_token = str(
            row.get("link_token")
            or ""
        ).strip()

        query = urlencode(
            {
                "token": link_token,
                "next": "/",
            }
        )

        claimed = row.get(
            "last_claimed_at_utc"
        )

        output.append(
            ManagerGatewayLink(
                franchise_id=int(
                    row["franchise_id"]
                ),
                team_key=str(
                    row["team_key"]
                ),
                team_name=str(
                    row.get("team_name")
                    or row["team_key"]
                ),
                owner_name=(
                    str(row["owner_name"])
                    if row.get("owner_name")
                    is not None
                    else None
                ),
                is_active=bool(
                    row["is_active"]
                ),
                claim_count=int(
                    row.get("claim_count")
                    or 0
                ),
                last_claimed_at_utc=(
                    claimed.isoformat()
                    if hasattr(
                        claimed,
                        "isoformat",
                    )
                    else (
                        str(claimed)
                        if claimed is not None
                        else None
                    )
                ),
                manager_url=(
                    f"{base_url}"
                    f"/gateway/claim?"
                    f"{query}"
                ),
            )
        )

    return output
# MLF_COMMISSIONER_WRITE_BOUNDARY_V1

from mlf_api.auth import (
    AUTH_COOKIE_MAX_AGE_SECONDS,
    create_auth_session,
    get_auth_cookie_name,
    is_commissioner_writer,
    is_login_rate_limited,
    load_login_user,
    record_login_attempt,
    resolve_auth_session,
    revoke_auth_session,
    verify_password,
)
from mlf_api.models import (
    CommissionerWriteLoginRequest,
    CommissionerWriteStatus,
    YahooPlayerUniverseRefreshResponse,
)
from mlf_api.yahoo_refresh import (
    YahooRefreshBusy,
    YahooRefreshFailure,
    refresh_yahoo_player_universe,
)


def _require_commissioner_workspace(
    request: Request,
) -> dict[str, object]:
    try:
        workspace = _resolve_commissioner_request(
            request
        )
    except Exception:
        raise _service_unavailable() from None

    if workspace is None:
        raise _forbidden(
            "commissioner_workspace_required"
        )

    return workspace


def _resolve_commissioner_write_principal(
    request: Request,
) -> dict[str, object] | None:
    raw_cookie = request.cookies.get(
        get_auth_cookie_name()
    )

    if not raw_cookie:
        return None

    try:
        return resolve_auth_session(
            raw_cookie
        )
    except Exception:
        raise _service_unavailable() from None


def _commissioner_write_status(
    principal: dict[str, object] | None,
) -> CommissionerWriteStatus:
    if principal is None:
        return CommissionerWriteStatus(
            write_enabled=False,
            user_id=None,
            email=None,
            authority="none",
            must_change_password=False,
        )

    write_enabled = (
        is_commissioner_writer(principal)
    )

    if bool(principal.get("is_site_admin")):
        authority = "site_admin"
    elif (
        str(
            principal.get("league_role")
            or ""
        ).strip().lower()
        == "commissioner"
    ):
        authority = "commissioner"
    else:
        authority = "none"

    return CommissionerWriteStatus(
        write_enabled=write_enabled,
        user_id=int(principal["user_id"]),
        email=str(principal["email"]),
        authority=authority,
        must_change_password=bool(
            principal.get(
                "must_change_password",
                False,
            )
        ),
    )


def _require_commissioner_write_principal(
    request: Request,
) -> dict[str, object]:
    principal = (
        _resolve_commissioner_write_principal(
            request
        )
    )

    if principal is None:
        raise _unauthenticated()

    if bool(
        principal.get("must_change_password")
    ):
        raise _forbidden(
            "password_change_required"
        )

    if not is_commissioner_writer(principal):
        raise _forbidden(
            "commissioner_write_required"
        )

    return principal


@app.get(
    "/gateway/commissioner/write-status",
    response_model=CommissionerWriteStatus,
)
def commissioner_write_status(
    request: Request,
) -> CommissionerWriteStatus:
    _require_commissioner_workspace(request)

    principal = (
        _resolve_commissioner_write_principal(
            request
        )
    )

    return _commissioner_write_status(
        principal
    )


@app.post(
    "/gateway/commissioner/write-login",
    response_model=CommissionerWriteStatus,
)
def commissioner_write_login(
    payload: CommissionerWriteLoginRequest,
    request: Request,
    response: Response,
) -> CommissionerWriteStatus:
    _require_json_content_type(request)
    _require_same_origin(request)
    _require_commissioner_workspace(request)

    email = str(
        payload.email or ""
    ).strip().lower()

    password = str(
        payload.password or ""
    )

    if not email or not password:
        raise _bad_request(
            "credentials_required"
        )

    try:
        limited = is_login_rate_limited(
            email_normalized=email,
            max_failures=5,
            window_minutes=10,
        )
    except Exception:
        raise _service_unavailable() from None

    if limited:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "login_rate_limited"
            },
        )

    try:
        principal = load_login_user(email)
    except Exception:
        raise _service_unavailable() from None

    valid_password = (
        principal is not None
        and bool(principal.get("active"))
        and verify_password(
            password,
            str(
                principal.get("password_hash")
                or ""
            ),
        )
    )

    if not valid_password:
        record_login_attempt(
            email_normalized=email,
            success=False,
        )
        raise _unauthenticated()

    record_login_attempt(
        email_normalized=email,
        success=True,
    )

    assert principal is not None

    if bool(
        principal.get("must_change_password")
    ):
        raise _forbidden(
            "password_change_required"
        )

    if not is_commissioner_writer(principal):
        raise _forbidden(
            "commissioner_write_required"
        )

    try:
        session_token = create_auth_session(
            user_id=int(principal["user_id"])
        )
    except Exception:
        raise _service_unavailable() from None

    response.set_cookie(
        key=get_auth_cookie_name(),
        value=session_token,
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return _commissioner_write_status(
        principal
    )


@app.post(
    "/gateway/commissioner/write-logout",
    response_model=CommissionerWriteStatus,
)
def commissioner_write_logout(
    request: Request,
    response: Response,
) -> CommissionerWriteStatus:
    _require_json_content_type(request)
    _require_same_origin(request)
    _require_commissioner_workspace(request)

    cookie_name = get_auth_cookie_name()

    raw_cookie = request.cookies.get(
        cookie_name
    )

    if raw_cookie:
        try:
            revoke_auth_session(
                raw_cookie
            )
        except Exception:
            raise _service_unavailable() from None

    response.delete_cookie(
        key=cookie_name,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )

    return _commissioner_write_status(
        None
    )


@app.post(
    "/gateway/commissioner/"
    "yahoo-player-universe/refresh",
    response_model=YahooPlayerUniverseRefreshResponse,
)
def commissioner_yahoo_player_universe_refresh(
    request: Request,
) -> YahooPlayerUniverseRefreshResponse:
    _require_json_content_type(request)
    _require_same_origin(request)

    _require_commissioner_workspace(request)

    principal = (
        _require_commissioner_write_principal(
            request
        )
    )

    try:
        result = (
            refresh_yahoo_player_universe()
        )
    except YahooRefreshBusy:
        raise _conflict(
            "yahoo_refresh_in_progress"
        ) from None
    except YahooRefreshFailure:
        raise _service_unavailable() from None
    except Exception:
        raise _service_unavailable() from None

    return YahooPlayerUniverseRefreshResponse(
        **result,
        performed_by=str(
            principal["email"]
        ),
    )
# MLF_COMMISSIONER_TRADE_BUILDER_V1

from mlf_api.models import (
    CommissionerTradeBuilderState,
    CommissionerTradeRequest,
    CommissionerTradeResponse,
)
from mlf_api.trade_store import (
    TradeConflict,
    TradeRequestError,
    get_trade_builder_state,
    submit_commissioner_trade,
)


@app.get(
    "/gateway/commissioner/trade-builder",
    response_model=CommissionerTradeBuilderState,
)
def commissioner_trade_builder_state(
    request: Request,
) -> CommissionerTradeBuilderState:
    _require_commissioner_workspace(
        request
    )

    try:
        state = get_trade_builder_state()
    except Exception:
        raise _service_unavailable() from None

    return CommissionerTradeBuilderState(
        **state
    )


@app.post(
    "/gateway/commissioner/trade-builder/submit",
    response_model=CommissionerTradeResponse,
)
def commissioner_trade_builder_submit(
    payload: CommissionerTradeRequest,
    request: Request,
) -> CommissionerTradeResponse:
    _require_json_content_type(request)
    _require_same_origin(request)
    _require_commissioner_workspace(
        request
    )

    principal = (
        _require_commissioner_write_principal(
            request
        )
    )

    try:
        result = submit_commissioner_trade(
            payload=payload.model_dump(),
            created_by=(
                "api:user:"
                + str(
                    principal["user_id"]
                )
            ),
        )
    except TradeRequestError:
        raise _bad_request(
            "invalid_trade_request"
        ) from None
    except TradeConflict:
        raise _conflict(
            "trade_state_conflict"
        ) from None
    except Exception:
        raise _service_unavailable() from None

    return CommissionerTradeResponse(
        **result,
        performed_by=str(
            principal["email"]
        ),
    )
# MLF_COMMISSIONER_QO_V1

from mlf_api.models import (
    CommissionerQOState,
    CommissionerQOUpdateRequest,
    CommissionerQOUpdateResponse,
)
from mlf_api.qo_store import (
    QOConflict,
    QORequestError,
    get_commissioner_qo_state,
    save_commissioner_qos,
)


@app.get(
    "/gateway/commissioner/qualifying-offers",
    response_model=CommissionerQOState,
)
def commissioner_qualifying_offers(
    request: Request,
) -> CommissionerQOState:
    _require_commissioner_workspace(
        request
    )

    try:
        state = (
            get_commissioner_qo_state()
        )
    except Exception:
        raise _service_unavailable() from None

    return CommissionerQOState(
        **state
    )


@app.post(
    "/gateway/commissioner/qualifying-offers/{team_key}",
    response_model=CommissionerQOUpdateResponse,
)
def commissioner_qualifying_offers_update(
    team_key: str,
    payload: CommissionerQOUpdateRequest,
    request: Request,
) -> CommissionerQOUpdateResponse:
    _require_json_content_type(request)
    _require_same_origin(request)
    _require_commissioner_workspace(
        request
    )

    principal = (
        _require_commissioner_write_principal(
            request
        )
    )

    try:
        result = save_commissioner_qos(
            team_key=team_key,
            player_keys=payload.player_keys,
            created_by=(
                "commissioner_api:user:"
                + str(
                    principal["user_id"]
                )
            ),
        )
    except QORequestError:
        raise _bad_request(
            "invalid_qualifying_offers"
        ) from None
    except QOConflict:
        raise _conflict(
            "qualifying_offers_locked"
        ) from None
    except Exception:
        raise _service_unavailable() from None

    return CommissionerQOUpdateResponse(
        **result,
        performed_by=str(
            principal["email"]
        ),
    )
# MLF_COMMISSIONER_PROSPECT_TAG_V1

from mlf_api.models import (
    CommissionerProspectMutationResponse,
    CommissionerProspectState,
    CommissionerProspectUpdateRequest,
)
from mlf_api.prospect_store import (
    ProspectConflict,
    ProspectRequestError,
    get_commissioner_prospect_state,
    remove_commissioner_prospect_tag,
    save_commissioner_prospect_tag,
)


@app.get(
    "/gateway/commissioner/prospect-tags",
    response_model=CommissionerProspectState,
)
def commissioner_prospect_tags(
    request: Request,
) -> CommissionerProspectState:
    _require_commissioner_workspace(
        request
    )

    try:
        state = (
            get_commissioner_prospect_state()
        )
    except Exception:
        raise _service_unavailable() from None

    return CommissionerProspectState(
        **state
    )


@app.post(
    "/gateway/commissioner/prospect-tags/{team_key}",
    response_model=CommissionerProspectMutationResponse,
)
def commissioner_prospect_tags_update(
    team_key: str,
    payload: CommissionerProspectUpdateRequest,
    request: Request,
) -> CommissionerProspectMutationResponse:
    _require_json_content_type(request)
    _require_same_origin(request)
    _require_commissioner_workspace(
        request
    )

    principal = (
        _require_commissioner_write_principal(
            request
        )
    )

    try:
        result = (
            save_commissioner_prospect_tag(
                team_key=team_key,
                yahoo_player_key=
                    payload.yahoo_player_key,
                created_by=(
                    "commissioner_api:user:"
                    + str(
                        principal["user_id"]
                    )
                ),
            )
        )
    except ProspectRequestError:
        raise _bad_request(
            "invalid_prospect_tag"
        ) from None
    except ProspectConflict:
        raise _conflict(
            "prospect_tag_state_conflict"
        ) from None
    except Exception:
        raise _service_unavailable() from None

    return CommissionerProspectMutationResponse(
        **result,
        performed_by=str(
            principal["email"]
        ),
    )


@app.delete(
    "/gateway/commissioner/prospect-tags/{team_key}",
    response_model=CommissionerProspectMutationResponse,
)
def commissioner_prospect_tags_delete(
    team_key: str,
    request: Request,
) -> CommissionerProspectMutationResponse:
    _require_json_content_type(request)
    _require_same_origin(request)
    _require_commissioner_workspace(
        request
    )

    principal = (
        _require_commissioner_write_principal(
            request
        )
    )

    try:
        result = (
            remove_commissioner_prospect_tag(
                team_key=team_key,
            )
        )
    except ProspectRequestError:
        raise _bad_request(
            "invalid_prospect_tag"
        ) from None
    except ProspectConflict:
        raise _conflict(
            "prospect_tag_state_conflict"
        ) from None
    except Exception:
        raise _service_unavailable() from None

    return CommissionerProspectMutationResponse(
        **result,
        performed_by=str(
            principal["email"]
        ),
    )
