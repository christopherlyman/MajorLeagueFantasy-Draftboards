from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from auth import (
    DEFAULT_AUTH_COOKIE_NAME,
    get_auth_cookie_name,
    read_auth_session_token,
    require_commercial_principal,
    resolve_commercial_principal,
)


PRINCIPAL_ROW = (
    7,
    "commissioner@example.com",
    False,
    False,
)


class FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})


class ScriptedCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append(
            (" ".join(str(sql).split()), params)
        )

        if not self.connection.responses:
            raise AssertionError(
                "Unexpected SQL execution with no scripted response."
            )

        self.current = self.connection.responses.pop(0)

    def fetchone(self):
        if self.current is None:
            raise AssertionError("fetchone called before execute.")

        return self.current.get("one")


class ScriptedConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed = []

    def cursor(self):
        return ScriptedCursor(self)


class CommercialApiAuthTests(unittest.TestCase):
    def test_default_cookie_name_is_mlf_auth(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                get_auth_cookie_name(),
                DEFAULT_AUTH_COOKIE_NAME,
            )
            self.assertEqual(
                get_auth_cookie_name(),
                "mlf_auth",
            )

    def test_cookie_name_can_be_overridden(self):
        with patch.dict(
            os.environ,
            {"COMMERCIAL_AUTH_COOKIE_NAME": "commissioner_auth"},
            clear=True,
        ):
            self.assertEqual(
                get_auth_cookie_name(),
                "commissioner_auth",
            )

    def test_missing_cookie_returns_401(self):
        request = FakeRequest()

        with self.assertRaises(HTTPException) as caught:
            read_auth_session_token(request)

        self.assertEqual(
            caught.exception.status_code,
            401,
        )

    def test_valid_session_resolves_active_user(self):
        db = ScriptedConnection(
            [
                {"one": PRINCIPAL_ROW},
            ]
        )

        principal = resolve_commercial_principal(
            db,
            session_token="opaque-session-token",
        )

        self.assertIsNotNone(principal)
        self.assertEqual(principal.user_id, 7)
        self.assertEqual(
            principal.email_normalized,
            "commissioner@example.com",
        )
        self.assertFalse(principal.is_site_admin)
        self.assertFalse(principal.must_change_password)

        sql, params = db.executed[0]

        self.assertIn(
            "s.revoked_at_utc IS NULL",
            sql,
        )
        self.assertIn(
            "s.expires_at_utc > now()",
            sql,
        )
        self.assertIn(
            "u.active = true",
            sql,
        )
        self.assertEqual(
            params,
            ("opaque-session-token",),
        )

    def test_invalid_session_resolves_no_principal(self):
        db = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        principal = resolve_commercial_principal(
            db,
            session_token="invalid-token",
        )

        self.assertIsNone(principal)

    def test_require_principal_uses_cookie_not_client_user_id(self):
        db = ScriptedConnection(
            [
                {"one": PRINCIPAL_ROW},
            ]
        )

        request = FakeRequest(
            {
                "mlf_auth": "opaque-session-token",
                "user_id": "999999",
            }
        )

        principal = require_commercial_principal(
            db,
            request=request,
        )

        self.assertEqual(principal.user_id, 7)

    def test_invalid_cookie_session_returns_401(self):
        db = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        request = FakeRequest(
            {
                "mlf_auth": "expired-or-invalid",
            }
        )

        with self.assertRaises(HTTPException) as caught:
            require_commercial_principal(
                db,
                request=request,
            )

        self.assertEqual(
            caught.exception.status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
