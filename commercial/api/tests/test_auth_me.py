from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from api import main as commercial_main


PRINCIPAL_ROW = (
    7,
    "commissioner@example.com",
    False,
    False,
)


def request_with_cookie(
    cookie: str | None = None,
) -> Request:
    headers = []

    if cookie is not None:
        headers.append(
            (
                b"cookie",
                f"mlf_auth={cookie}".encode("ascii"),
            )
        )

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/me",
            "headers": headers,
        }
    )


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
            raise AssertionError(
                "fetchone called before execute."
            )

        return self.current.get("one")


class ScriptedConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return ScriptedCursor(self)


class CommercialAuthMeTests(unittest.TestCase):
    def test_route_is_registered(self):
        paths = {
            getattr(route, "path", None)
            for route in commercial_main.app.routes
        }

        self.assertIn(
            "/api/auth/me",
            paths,
        )

    def test_no_cookie_returns_401(self):
        db = ScriptedConnection([])

        with patch.object(
            commercial_main,
            "database_connection",
            return_value=db,
        ):
            with self.assertRaises(HTTPException) as caught:
                commercial_main.auth_me(
                    request_with_cookie(),
                )

        self.assertEqual(
            caught.exception.status_code,
            401,
        )
        self.assertEqual(
            db.executed,
            [],
        )

    def test_invalid_session_returns_401(self):
        db = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        with patch.object(
            commercial_main,
            "database_connection",
            return_value=db,
        ):
            with self.assertRaises(HTTPException) as caught:
                commercial_main.auth_me(
                    request_with_cookie(
                        "invalid-session-token"
                    ),
                )

        self.assertEqual(
            caught.exception.status_code,
            401,
        )

        self.assertEqual(
            db.executed[0][1],
            ("invalid-session-token",),
        )

    def test_valid_session_returns_server_principal(self):
        db = ScriptedConnection(
            [
                {"one": PRINCIPAL_ROW},
            ]
        )

        with patch.object(
            commercial_main,
            "database_connection",
            return_value=db,
        ):
            result = commercial_main.auth_me(
                request_with_cookie(
                    "valid-session-token"
                ),
            )

        self.assertTrue(result["authenticated"])

        self.assertEqual(
            result["user"]["user_id"],
            7,
        )

        self.assertEqual(
            result["user"]["email"],
            "commissioner@example.com",
        )

        self.assertFalse(
            result["user"]["is_site_admin"],
        )

        self.assertFalse(
            result["user"]["must_change_password"],
        )

        self.assertEqual(
            db.executed[0][1],
            ("valid-session-token",),
        )


if __name__ == "__main__":
    unittest.main()
