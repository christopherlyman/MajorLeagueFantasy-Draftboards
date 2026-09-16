from __future__ import annotations

import psycopg
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse

from draftboard.state.runtime import get_postgres_dsn

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
    GatewayPrincipal,
    HealthResponse,
)


app = FastAPI(
    title="MLF DraftBoard API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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