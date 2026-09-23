from __future__ import annotations

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
from draftboard.state.runtime import get_draft_key, get_postgres_dsn

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