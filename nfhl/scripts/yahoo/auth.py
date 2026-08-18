import os
import time
import requests
import psycopg
from typing import Optional


YAHOO_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
APP_NAME = os.environ.get("YAHOO_APP_NAME", "mlf_tools")


def _get_db_conn():
    dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN or MLF_POSTGRES_DSN environment variable is not set")
    return psycopg.connect(dsn)


def _get_refresh_token(conn) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT refresh_token
            FROM yahoo_oauth_token
            WHERE app_name = %s
            """,
            (APP_NAME,),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"No refresh token found for app_name='{APP_NAME}'")
        return row[0]


def _store_tokens(
    conn,
    refresh_token: str,
    access_token: str,
    token_type: str,
    expires_in: int,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO yahoo_oauth_token (
                app_name,
                refresh_token,
                access_token,
                token_type,
                expires_in,
                obtained_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (app_name)
            DO UPDATE SET
                refresh_token = EXCLUDED.refresh_token,
                access_token  = EXCLUDED.access_token,
                token_type    = EXCLUDED.token_type,
                expires_in    = EXCLUDED.expires_in,
                updated_at    = now()
            """,
            (
                APP_NAME,
                refresh_token,
                access_token,
                token_type,
                expires_in,
            ),
        )
    conn.commit()


def get_access_token() -> str:
    """
    Returns a valid Yahoo access token.
    Refreshes and persists tokens as needed.
    """
    client_id = os.environ.get("YAHOO_CLIENT_ID")
    client_secret = os.environ.get("YAHOO_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError("YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET not set")

    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)

    with _get_db_conn() as conn:
        refresh_token = _get_refresh_token(conn)

        resp = requests.post(
            YAHOO_TOKEN_URL,
            auth=auth,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Yahoo token refresh failed: {resp.status_code} {resp.text}"
            )

        payload = resp.json()

        access_token = payload["access_token"]
        new_refresh_token = payload.get("refresh_token", refresh_token)
        token_type = payload.get("token_type", "bearer")
        expires_in = int(payload.get("expires_in", 3600))

        _store_tokens(
            conn,
            refresh_token=new_refresh_token,
            access_token=access_token,
            token_type=token_type,
            expires_in=expires_in,
        )

        return access_token


if __name__ == "__main__":
    token = get_access_token()
    print("Access token retrieved (length):", len(token))

