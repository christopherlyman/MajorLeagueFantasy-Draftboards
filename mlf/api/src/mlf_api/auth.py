from __future__ import annotations

import os
import secrets

import bcrypt
import psycopg
from psycopg.rows import dict_row

from draftboard.state.runtime import (
    get_league_key,
    get_postgres_dsn,
)


DEFAULT_AUTH_COOKIE_NAME = "mlf_auth"
AUTH_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def get_auth_cookie_name() -> str:
    value = str(
        os.environ.get("AUTH_COOKIE_NAME")
        or DEFAULT_AUTH_COOKIE_NAME
    ).strip()

    return value or DEFAULT_AUTH_COOKIE_NAME


def _dsn() -> str:
    value = str(get_postgres_dsn() or "").strip()

    if not value:
        raise RuntimeError(
            "PostgreSQL DSN is unavailable."
        )

    return value


def _league_role_expression() -> str:
    return """
        (
            SELECT r.role_code
            FROM public.auth_user_league_role AS r
            WHERE r.user_id = u.user_id
              AND r.league_key = %s
              AND r.active IS TRUE
            ORDER BY
                CASE r.role_code
                    WHEN 'commissioner' THEN 0
                    WHEN 'manager' THEN 1
                    ELSE 2
                END,
                r.created_at_utc
            LIMIT 1
        ) AS league_role
    """


def resolve_auth_session(
    session_token: str,
) -> dict[str, object] | None:
    token = str(session_token or "").strip()

    if not token:
        return None

    sql = f"""
        SELECT
            u.user_id,
            u.email_normalized,
            u.is_site_admin,
            u.must_change_password,
            {_league_role_expression()}
        FROM public.auth_session AS s
        JOIN public.auth_user AS u
          ON u.user_id = s.user_id
        WHERE s.session_token = %s
          AND s.revoked_at_utc IS NULL
          AND s.expires_at_utc > now()
          AND u.active IS TRUE
        LIMIT 1
    """

    with psycopg.connect(
        _dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(get_league_key()),
                    token,
                ),
            )
            row = cur.fetchone()

    if row is None:
        return None

    return {
        "user_id": int(row["user_id"]),
        "email": str(row["email_normalized"]),
        "is_site_admin": bool(
            row["is_site_admin"]
        ),
        "must_change_password": bool(
            row["must_change_password"]
        ),
        "league_role": (
            str(row["league_role"])
            if row["league_role"] is not None
            else None
        ),
    }


def load_login_user(
    email_normalized: str,
) -> dict[str, object] | None:
    email = str(
        email_normalized or ""
    ).strip().lower()

    if not email:
        return None

    sql = f"""
        SELECT
            u.user_id,
            u.email_normalized,
            u.password_hash,
            u.active,
            u.is_site_admin,
            u.must_change_password,
            {_league_role_expression()}
        FROM public.auth_user AS u
        WHERE u.email_normalized = %s
        LIMIT 1
    """

    with psycopg.connect(
        _dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(get_league_key()),
                    email,
                ),
            )
            row = cur.fetchone()

    if row is None:
        return None

    return {
        "user_id": int(row["user_id"]),
        "email": str(row["email_normalized"]),
        "password_hash": str(
            row["password_hash"] or ""
        ),
        "active": bool(row["active"]),
        "is_site_admin": bool(
            row["is_site_admin"]
        ),
        "must_change_password": bool(
            row["must_change_password"]
        ),
        "league_role": (
            str(row["league_role"])
            if row["league_role"] is not None
            else None
        ),
    }


def verify_password(
    password: str,
    password_hash: str,
) -> bool:
    raw_password = str(password or "")
    raw_hash = str(password_hash or "").strip()

    if not raw_password or not raw_hash:
        return False

    try:
        return bcrypt.checkpw(
            raw_password.encode("utf-8"),
            raw_hash.encode("utf-8"),
        )
    except Exception:
        return False


def is_commissioner_writer(
    principal: dict[str, object] | None,
) -> bool:
    if not principal:
        return False

    if bool(
        principal.get("must_change_password")
    ):
        return False

    if bool(principal.get("is_site_admin")):
        return True

    return (
        str(
            principal.get("league_role")
            or ""
        ).strip().lower()
        == "commissioner"
    )


def create_auth_session(
    *,
    user_id: int,
) -> str:
    token = secrets.token_urlsafe(32)

    sql = """
        INSERT INTO public.auth_session (
            session_token,
            user_id,
            created_at_utc,
            expires_at_utc,
            revoked_at_utc
        )
        VALUES (
            %s,
            %s,
            now(),
            now() + interval '30 days',
            NULL
        )
    """

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    token,
                    int(user_id),
                ),
            )
        conn.commit()

    return token


def revoke_auth_session(
    session_token: str,
) -> None:
    token = str(session_token or "").strip()

    if not token:
        return

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.auth_session
                SET revoked_at_utc = now()
                WHERE session_token = %s
                  AND revoked_at_utc IS NULL
                """,
                (token,),
            )
        conn.commit()


def record_login_attempt(
    *,
    email_normalized: str,
    success: bool,
) -> None:
    email = str(
        email_normalized or ""
    ).strip().lower()

    if not email:
        return

    try:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.auth_login_attempt (
                        email_attempted,
                        ip_address,
                        success,
                        attempt_ts
                    )
                    VALUES (
                        %s,
                        NULL,
                        %s,
                        now()
                    )
                    """,
                    (
                        email,
                        bool(success),
                    ),
                )
            conn.commit()
    except Exception:
        # Audit failure must not expose auth internals
        # or break the login response.
        return


def is_login_rate_limited(
    *,
    email_normalized: str,
    max_failures: int = 5,
    window_minutes: int = 10,
) -> bool:
    email = str(
        email_normalized or ""
    ).strip().lower()

    if not email:
        return False

    try:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)
                    FROM public.auth_login_attempt
                    WHERE success IS FALSE
                      AND email_attempted = %s
                      AND attempt_ts >= (
                          now()
                          - make_interval(
                              mins => %s
                          )
                      )
                    """,
                    (
                        email,
                        int(window_minutes),
                    ),
                )
                row = cur.fetchone()

        failures = (
            int(row[0])
            if row and row[0] is not None
            else 0
        )

        return failures >= int(max_failures)
    except Exception:
        # Preserve the legacy fail-open audit/rate-limit
        # behavior if the auxiliary table is unavailable.
        return False
