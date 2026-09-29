from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from draftboard.state.runtime import (
    get_league_key,
    get_season_year,
)


COMMISSIONER_COOKIE_NAME = "mlf_commissioner_gateway"
COMMISSIONER_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
COMMISSIONER_TOKEN_ENV = "MLF_COMMISSIONER_GATEWAY_TOKEN"
COMMISSIONER_PAYLOAD_VERSION = 1


def get_commissioner_cookie_name() -> str:
    return COMMISSIONER_COOKIE_NAME


def _commissioner_secret() -> str:
    secret = str(
        os.environ.get(COMMISSIONER_TOKEN_ENV, "")
        or ""
    ).strip()

    if len(secret) < 32:
        raise RuntimeError(
            f"{COMMISSIONER_TOKEN_ENV} must contain at least 32 characters."
        )

    return secret


def commissioner_token_is_valid(token: str) -> bool:
    candidate = str(token or "").strip()
    if not candidate:
        return False

    secret = _commissioner_secret()

    return hmac.compare_digest(
        candidate.encode("utf-8"),
        secret.encode("utf-8"),
    )


def _b64encode(raw: bytes) -> str:
    return (
        base64.urlsafe_b64encode(raw)
        .decode("ascii")
        .rstrip("=")
    )


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(
        value + padding
    )


def pack_commissioner_cookie() -> str:
    secret = _commissioner_secret()

    payload = {
        "v": COMMISSIONER_PAYLOAD_VERSION,
        "role": "commissioner",
        "league_key": str(get_league_key()),
        "season_year": int(get_season_year()),
        "issued_at_utc": int(time.time()),
    }

    payload_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    encoded_payload = _b64encode(payload_json)

    signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    return (
        encoded_payload
        + "."
        + _b64encode(signature)
    )


def resolve_commissioner_cookie(
    raw_cookie: str,
) -> dict[str, Any] | None:
    token = str(raw_cookie or "").strip()
    if not token:
        return None

    secret = _commissioner_secret()

    try:
        encoded_payload, encoded_signature = token.split(".", 1)

        expected_signature = hmac.new(
            secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()

        supplied_signature = _b64decode(
            encoded_signature
        )

        if not hmac.compare_digest(
            expected_signature,
            supplied_signature,
        ):
            return None

        payload = json.loads(
            _b64decode(encoded_payload).decode("utf-8")
        )
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    if (
        payload.get("v")
        != COMMISSIONER_PAYLOAD_VERSION
    ):
        return None

    if payload.get("role") != "commissioner":
        return None

    if str(payload.get("league_key") or "") != str(
        get_league_key()
    ):
        return None

    try:
        season_year = int(payload.get("season_year"))
        issued_at_utc = int(payload.get("issued_at_utc"))
    except (TypeError, ValueError):
        return None

    if season_year != int(get_season_year()):
        return None

    now = int(time.time())

    if issued_at_utc > now + 60:
        return None

    if (
        now - issued_at_utc
        > COMMISSIONER_COOKIE_MAX_AGE_SECONDS
    ):
        return None

    return {
        "is_authenticated": True,
        "role": "commissioner",
        "league_key": str(get_league_key()),
        "season_year": int(get_season_year()),
        "display_name": "Commissioner",
        "acting_as": "commissioner",
        "issued_at_utc": issued_at_utc,
    }
