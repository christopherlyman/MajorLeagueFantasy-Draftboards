from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


DEFAULT_AUTH_COOKIE_NAME = "mlf_auth"


@dataclass(frozen=True)
class CommercialPrincipal:
    user_id: int
    email_normalized: str
    is_site_admin: bool
    must_change_password: bool


def get_auth_cookie_name() -> str:
    value = str(
        os.environ.get("COMMERCIAL_AUTH_COOKIE_NAME")
        or os.environ.get("AUTH_COOKIE_NAME")
        or DEFAULT_AUTH_COOKIE_NAME
    ).strip()

    return value or DEFAULT_AUTH_COOKIE_NAME


def read_auth_session_token(request: Request) -> str:
    """
    Read the existing opaque auth_session token from the browser cookie.

    The token is never decoded by the commercial application. PostgreSQL
    auth_session remains authoritative for validity, revocation, and expiry.
    """
    raw = request.cookies.get(get_auth_cookie_name())
    token = str(raw or "").strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    return token


def resolve_commercial_principal(
    connection,
    *,
    session_token: str,
) -> CommercialPrincipal | None:
    """
    Resolve a valid commercial principal from auth_session.

    A session is valid only when:
    - the token exists;
    - it has not been revoked;
    - it has not expired;
    - its auth_user still exists and is active.
    """
    token = str(session_token or "").strip()

    if not token:
        return None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                u.user_id,
                u.email_normalized,
                u.is_site_admin,
                u.must_change_password
            FROM public.auth_session s
            JOIN public.auth_user u
              ON u.user_id = s.user_id
            WHERE s.session_token = %s
              AND s.revoked_at_utc IS NULL
              AND s.expires_at_utc > now()
              AND u.active = true
            LIMIT 1
            """,
            (token,),
        )

        row = cursor.fetchone()

    if row is None:
        return None

    return CommercialPrincipal(
        user_id=int(row[0]),
        email_normalized=str(row[1]),
        is_site_admin=bool(row[2]),
        must_change_password=bool(row[3]),
    )


def require_commercial_principal(
    connection,
    *,
    request: Request,
) -> CommercialPrincipal:
    """
    Resolve the authenticated user for a protected commercial API operation.

    The browser never supplies user_id as ownership authority.
    """
    token = read_auth_session_token(request)

    principal = resolve_commercial_principal(
        connection,
        session_token=token,
    )

    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    return principal
