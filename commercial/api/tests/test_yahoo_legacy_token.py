from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from api.yahoo_legacy_token import (
    YahooLegacyTokenBridgeError,
    get_legacy_yahoo_access_token,
)


NOW = datetime(
    2026,
    9,
    27,
    20,
    0,
    0,
    tzinfo=timezone.utc,
)


class ScriptedCursor:
    def __init__(
        self,
        connection,
    ):
        self.connection = connection
        self.current = None
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def execute(
        self,
        sql,
        params=None,
    ):
        self.connection.executed.append(
            (
                " ".join(
                    str(sql).split()
                ),
                params,
            )
        )

        if not self.connection.responses:
            raise AssertionError(
                "Unexpected SQL execution."
            )

        self.current = (
            self.connection.responses.pop(0)
        )

        self.rowcount = self.current.get(
            "rowcount",
            -1,
        )

    def fetchone(self):
        if self.current is None:
            raise AssertionError(
                "fetchone before execute."
            )

        return self.current.get("one")


class ScriptedConnection:
    def __init__(
        self,
        responses,
    ):
        self.responses = list(
            responses
        )
        self.executed = []

    def cursor(self):
        return ScriptedCursor(
            self
        )


class YahooLegacyTokenBridgeTests(
    unittest.TestCase
):
    def test_bridge_is_disabled_without_explicit_app_name(
        self,
    ):
        db = ScriptedConnection([])

        with patch.dict(
            os.environ,
            {
                "COMMERCIAL_YAHOO_LEGACY_APP_NAME":
                    "",
            },
            clear=False,
        ):
            with self.assertRaises(
                YahooLegacyTokenBridgeError
            ):
                get_legacy_yahoo_access_token(
                    db,
                    now_utc=NOW,
                )

        self.assertEqual(
            db.executed,
            [],
        )

    def test_fresh_cached_access_token_is_reused(
        self,
    ):
        db = ScriptedConnection(
            [
                {
                    "one": (
                        "refresh-token",
                        "cached-access-token",
                        "bearer",
                        3600,
                        NOW
                        - timedelta(
                            minutes=10
                        ),
                    )
                }
            ]
        )

        def fail_post(
            url,
            headers,
            data,
            timeout,
        ):
            raise AssertionError(
                "Refresh transport should not run."
            )

        with patch.dict(
            os.environ,
            {
                "COMMERCIAL_YAHOO_LEGACY_APP_NAME":
                    "mlf_tools",
            },
            clear=False,
        ):
            result = (
                get_legacy_yahoo_access_token(
                    db,
                    post_form=fail_post,
                    now_utc=NOW,
                )
            )

        self.assertEqual(
            result,
            "cached-access-token",
        )

        self.assertEqual(
            len(db.executed),
            1,
        )

        self.assertEqual(
            db.executed[0][1],
            ("mlf_tools",),
        )

    def test_expired_access_token_is_refreshed_and_persisted(
        self,
    ):
        db = ScriptedConnection(
            [
                {
                    "one": (
                        "old-refresh-token",
                        "expired-access-token",
                        "bearer",
                        3600,
                        NOW
                        - timedelta(
                            hours=2
                        ),
                    )
                },
                {
                    "rowcount": 1,
                },
            ]
        )

        calls = []

        def post_form(
            url,
            headers,
            data,
            timeout,
        ):
            calls.append(
                (
                    url,
                    dict(headers),
                    dict(data),
                    timeout,
                )
            )

            return {
                "access_token":
                    "new-access-token",
                "refresh_token":
                    "new-refresh-token",
                "token_type":
                    "bearer",
                "expires_in":
                    3600,
            }

        with patch.dict(
            os.environ,
            {
                "COMMERCIAL_YAHOO_LEGACY_APP_NAME":
                    "mlf_tools",
                "YAHOO_CLIENT_ID":
                    "client-id",
                "YAHOO_CLIENT_SECRET":
                    "client-secret",
            },
            clear=False,
        ):
            result = (
                get_legacy_yahoo_access_token(
                    db,
                    post_form=post_form,
                    now_utc=NOW,
                )
            )

        self.assertEqual(
            result,
            "new-access-token",
        )

        self.assertEqual(
            len(calls),
            1,
        )

        url, headers, data, timeout = (
            calls[0]
        )

        self.assertEqual(
            url,
            "https://api.login.yahoo.com/oauth2/get_token",
        )

        self.assertTrue(
            headers[
                "Authorization"
            ].startswith(
                "Basic "
            )
        )

        self.assertNotIn(
            "client-secret",
            headers[
                "Authorization"
            ],
        )

        self.assertEqual(
            data,
            {
                "grant_type":
                    "refresh_token",
                "refresh_token":
                    "old-refresh-token",
            },
        )

        self.assertEqual(
            timeout,
            30.0,
        )

        self.assertEqual(
            len(db.executed),
            2,
        )

        update_params = (
            db.executed[1][1]
        )

        self.assertEqual(
            update_params,
            (
                "new-refresh-token",
                "new-access-token",
                "bearer",
                3600,
                "mlf_tools",
            ),
        )


if __name__ == "__main__":
    unittest.main()
