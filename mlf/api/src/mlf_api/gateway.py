from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

from draftboard.state.runtime import (
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


DEFAULT_GATEWAY_COOKIE_NAME = "mlf_team_gateway"
GATEWAY_COOKIE_MAX_AGE_SECONDS = 180 * 24 * 60 * 60
GATEWAY_COOKIE_VERSION = 1


def get_gateway_cookie_name() -> str:
    value = str(
        os.environ.get(
            "MLF_GATEWAY_COOKIE_NAME",
            DEFAULT_GATEWAY_COOKIE_NAME,
        )
        or DEFAULT_GATEWAY_COOKIE_NAME
    ).strip()

    return value or DEFAULT_GATEWAY_COOKIE_NAME


def _gateway_secret() -> str:
    secret = str(
        os.environ.get(
            "MLF_GATEWAY_COOKIE_SECRET",
            "",
        )
        or ""
    ).strip()

    if not secret:
        raise RuntimeError(
            "MLF Team Gateway signing secret is unavailable."
        )

    return secret


def _gateway_sign(payload_b64: str) -> str:
    return hmac.new(
        _gateway_secret().encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def pack_manager_cookie(
    *,
    franchise_id: int,
    team_key: str,
) -> str:
    payload = {
        "v": GATEWAY_COOKIE_VERSION,
        "role": "manager",
        "league_key": str(get_league_key()),
        "season_year": int(get_season_year()),
        "franchise_id": int(franchise_id),
        "team_key": str(team_key),
        "issued_at_utc": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        ),
    }

    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    payload_b64 = (
        base64.urlsafe_b64encode(raw)
        .decode("ascii")
        .rstrip("=")
    )

    return (
        f"{payload_b64}."
        f"{_gateway_sign(payload_b64)}"
    )


def unpack_manager_cookie(
    token: str | None,
) -> dict[str, Any] | None:
    if not token:
        return None

    token_text = str(token).strip()

    if "." not in token_text:
        return None

    payload_b64, signature = token_text.split(".", 1)

    if not hmac.compare_digest(
        signature,
        _gateway_sign(payload_b64),
    ):
        return None

    try:
        padded = (
            payload_b64
            + "=" * (-len(payload_b64) % 4)
        )

        payload = json.loads(
            base64.urlsafe_b64decode(
                padded.encode("ascii")
            ).decode("utf-8")
        )
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    if payload.get("v") != GATEWAY_COOKIE_VERSION:
        return None

    if (
        str(payload.get("role") or "")
        .strip()
        .lower()
        != "manager"
    ):
        return None

    if str(
        payload.get("league_key")
        or ""
    ) != str(get_league_key()):
        return None

    try:
        season_year = int(
            payload.get("season_year")
        )
        franchise_id = int(
            payload.get("franchise_id")
        )
    except Exception:
        return None

    if season_year != int(get_season_year()):
        return None

    if franchise_id <= 0:
        return None

    team_key = str(
        payload.get("team_key")
        or ""
    ).strip()

    if not team_key:
        return None

    return payload


def public_principal() -> dict[str, object]:
    return {
        "is_authenticated": False,
        "role": "public",
        "league_key": str(get_league_key()),
        "season_year": int(get_season_year()),
        "franchise_id": None,
        "team_key": None,
        "team_name": None,
        "display_name": "Public",
        "acting_as": "public",
    }


def resolve_manager_principal(
    token: str | None,
) -> dict[str, object] | None:
    payload = unpack_manager_cookie(token)

    if payload is None:
        return None

    league_key = str(get_league_key())
    season_year = int(get_season_year())
    franchise_id = int(payload["franchise_id"])
    team_key = str(payload["team_key"])

    sql = """
        SELECT
            fst.franchise_id,
            fst.team_key,
            fst.team_name,
            fst.owner_name
        FROM public.franchise_season_team fst
        WHERE fst.franchise_id = %s
          AND fst.season_year = %s
          AND fst.league_key = %s
          AND fst.team_key = %s
        LIMIT 1
    """

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    franchise_id,
                    season_year,
                    league_key,
                    team_key,
                ),
            )
            row = cur.fetchone()

    if row is None:
        return None

    canonical_team_key = str(
        row["team_key"]
    )

    team_name = str(
        row["team_name"]
        or canonical_team_key
    )

    display_name = str(
        row["owner_name"]
        or team_name
    )

    return {
        "is_authenticated": True,
        "role": "manager",
        "league_key": league_key,
        "season_year": season_year,
        "franchise_id": int(
            row["franchise_id"]
        ),
        "team_key": canonical_team_key,
        "team_name": team_name,
        "display_name": display_name,
        "acting_as": f"manager:{team_name}",
    }