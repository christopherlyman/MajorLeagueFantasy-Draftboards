from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


YAHOO_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


class YahooLegacyTokenBridgeError(RuntimeError):
    """Raised when the temporary legacy Yahoo token bridge is unavailable."""


PostForm = Callable[
    [
        str,
        Mapping[str, str],
        Mapping[str, str],
        float,
    ],
    Mapping[str, Any],
]


def _configured_app_name() -> str:
    app_name = str(
        os.environ.get(
            "COMMERCIAL_YAHOO_LEGACY_APP_NAME",
            "",
        )
    ).strip()

    if not app_name:
        raise YahooLegacyTokenBridgeError(
            "Legacy Yahoo token bridge is not configured."
        )

    return app_name


def _client_credentials() -> tuple[str, str]:
    client_id = str(
        os.environ.get("YAHOO_CLIENT_ID", "")
    ).strip()

    client_secret = str(
        os.environ.get("YAHOO_CLIENT_SECRET", "")
    ).strip()

    if not client_id or not client_secret:
        raise YahooLegacyTokenBridgeError(
            "Yahoo client credentials are not configured."
        )

    return client_id, client_secret


def _as_utc(
    value: Any,
) -> datetime | None:
    if not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _is_cached_token_fresh(
    *,
    access_token: Any,
    expires_in: Any,
    obtained_at: Any,
    now_utc: datetime,
) -> bool:
    token = str(
        access_token or ""
    ).strip()

    if not token:
        return False

    try:
        lifetime = int(expires_in)
    except (TypeError, ValueError):
        return False

    obtained = _as_utc(
        obtained_at
    )

    if obtained is None:
        return False

    # Keep a one-minute safety margin.
    usable_seconds = lifetime - 60

    if usable_seconds <= 0:
        return False

    expires_at = obtained + timedelta(
        seconds=usable_seconds
    )

    return now_utc < expires_at


def _default_post_form(
    url: str,
    headers: Mapping[str, str],
    data: Mapping[str, str],
    timeout: float,
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=urlencode(
            dict(data)
        ).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read()

    except HTTPError as exc:
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh failed."
        ) from exc

    except URLError as exc:
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh could not be completed."
        ) from exc

    try:
        payload = json.loads(
            raw.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh returned invalid JSON."
        ) from exc

    if not isinstance(
        payload,
        Mapping,
    ):
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh returned an invalid payload."
        )

    return payload


def get_legacy_yahoo_access_token(
    connection,
    *,
    post_form: PostForm | None = None,
    now_utc: datetime | None = None,
    timeout_seconds: float = 30.0,
) -> str:
    """
    Return a Yahoo access token from the explicitly enabled legacy bridge.

    This is a private transitional mechanism. It is disabled unless
    COMMERCIAL_YAHOO_LEGACY_APP_NAME is configured.

    Tokens are never returned to the browser by this module.
    """
    app_name = _configured_app_name()

    current_time = (
        now_utc
        or datetime.now(
            timezone.utc
        )
    )

    if current_time.tzinfo is None:
        current_time = current_time.replace(
            tzinfo=timezone.utc
        )
    else:
        current_time = current_time.astimezone(
            timezone.utc
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                refresh_token,
                access_token,
                token_type,
                expires_in,
                obtained_at
            FROM public.yahoo_oauth_token
            WHERE app_name = %s
            """,
            (app_name,),
        )

        row = cursor.fetchone()

    if row is None:
        raise YahooLegacyTokenBridgeError(
            "Configured legacy Yahoo token record was not found."
        )

    (
        refresh_token_raw,
        access_token_raw,
        _token_type_raw,
        expires_in_raw,
        obtained_at_raw,
    ) = row

    refresh_token = str(
        refresh_token_raw or ""
    ).strip()

    if not refresh_token:
        raise YahooLegacyTokenBridgeError(
            "Configured legacy Yahoo refresh token is unavailable."
        )

    if _is_cached_token_fresh(
        access_token=access_token_raw,
        expires_in=expires_in_raw,
        obtained_at=obtained_at_raw,
        now_utc=current_time,
    ):
        return str(
            access_token_raw
        ).strip()

    client_id, client_secret = (
        _client_credentials()
    )

    encoded_credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode(
            "utf-8"
        )
    ).decode("ascii")

    poster = (
        post_form
        or _default_post_form
    )

    payload = poster(
        YAHOO_TOKEN_URL,
        {
            "Authorization":
                f"Basic {encoded_credentials}",
            "Content-Type":
                "application/x-www-form-urlencoded",
            "Accept":
                "application/json",
        },
        {
            "grant_type":
                "refresh_token",
            "refresh_token":
                refresh_token,
        },
        float(timeout_seconds),
    )

    access_token = str(
        payload.get(
            "access_token",
            "",
        )
    ).strip()

    if not access_token:
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh returned no access token."
        )

    new_refresh_token = str(
        payload.get(
            "refresh_token",
            refresh_token,
        )
        or refresh_token
    ).strip()

    token_type = str(
        payload.get(
            "token_type",
            "bearer",
        )
        or "bearer"
    ).strip()

    try:
        expires_in = int(
            payload.get(
                "expires_in",
                3600,
            )
        )
    except (TypeError, ValueError) as exc:
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh returned invalid expiry metadata."
        ) from exc

    if expires_in <= 0:
        raise YahooLegacyTokenBridgeError(
            "Yahoo token refresh returned invalid expiry metadata."
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.yahoo_oauth_token
            SET
                refresh_token = %s,
                access_token = %s,
                token_type = %s,
                expires_in = %s,
                obtained_at = now(),
                updated_at = now()
            WHERE app_name = %s
            """,
            (
                new_refresh_token,
                access_token,
                token_type,
                expires_in,
                app_name,
            ),
        )

        if cursor.rowcount != 1:
            raise YahooLegacyTokenBridgeError(
                "Legacy Yahoo token record changed during refresh."
            )

    return access_token
