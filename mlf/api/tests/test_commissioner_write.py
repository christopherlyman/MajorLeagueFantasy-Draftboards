from __future__ import annotations

import unittest
from unittest.mock import patch

import bcrypt
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from mlf_api import main
from mlf_api.auth import (
    is_commissioner_writer,
    verify_password,
)
from mlf_api.models import (
    CommissionerWriteLoginRequest,
)


def browser_request(
    *,
    method: str = "POST",
    path: str = "/gateway/commissioner/write-login",
    cookie: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = [
        (
            b"content-type",
            b"application/json",
        ),
        (
            b"origin",
            b"https://mlf.majorleaguefantasy.app",
        ),
    ]

    if cookie:
        headers.append(
            (
                b"cookie",
                cookie.encode("ascii"),
            )
        )

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "server": (
                "mlf.majorleaguefantasy.app",
                443,
            ),
            "client": (
                "127.0.0.1",
                12345,
            ),
        }
    )


class CommissionerWriteTests(
    unittest.TestCase
):
    def test_password_helper(self) -> None:
        hashed = bcrypt.hashpw(
            b"correct-password",
            bcrypt.gensalt(),
        ).decode("utf-8")

        self.assertTrue(
            verify_password(
                "correct-password",
                hashed,
            )
        )

        self.assertFalse(
            verify_password(
                "wrong-password",
                hashed,
            )
        )

    def test_writer_policy(self) -> None:
        self.assertTrue(
            is_commissioner_writer(
                {
                    "is_site_admin": True,
                    "league_role": None,
                    "must_change_password": False,
                }
            )
        )

        self.assertTrue(
            is_commissioner_writer(
                {
                    "is_site_admin": False,
                    "league_role": "commissioner",
                    "must_change_password": False,
                }
            )
        )

        self.assertFalse(
            is_commissioner_writer(
                {
                    "is_site_admin": False,
                    "league_role": "manager",
                    "must_change_password": False,
                }
            )
        )

        self.assertFalse(
            is_commissioner_writer(
                {
                    "is_site_admin": True,
                    "league_role": None,
                    "must_change_password": True,
                }
            )
        )

    def test_write_status_requires_workspace(
        self,
    ) -> None:
        with patch.object(
            main,
            "_resolve_commissioner_request",
            return_value=None,
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_write_status(
                    browser_request(
                        method="GET",
                        path=(
                            "/gateway/commissioner/"
                            "write-status"
                        ),
                    )
                )

        self.assertEqual(
            raised.exception.status_code,
            403,
        )

    def test_write_status_without_auth_session(
        self,
    ) -> None:
        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role": "commissioner"
                },
            ),
            patch.object(
                main,
                "_resolve_commissioner_write_principal",
                return_value=None,
            ),
        ):
            result = (
                main.commissioner_write_status(
                    browser_request(
                        method="GET",
                        path=(
                            "/gateway/commissioner/"
                            "write-status"
                        ),
                    )
                )
            )

        self.assertFalse(
            result.write_enabled
        )

    def test_login_sets_auth_cookie(
        self,
    ) -> None:
        principal = {
            "user_id": 7,
            "email": "commissioner@example.com",
            "password_hash": "hash",
            "active": True,
            "is_site_admin": False,
            "must_change_password": False,
            "league_role": "commissioner",
        }

        response = Response()

        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role": "commissioner"
                },
            ),
            patch.object(
                main,
                "is_login_rate_limited",
                return_value=False,
            ),
            patch.object(
                main,
                "load_login_user",
                return_value=principal,
            ),
            patch.object(
                main,
                "verify_password",
                return_value=True,
            ),
            patch.object(
                main,
                "record_login_attempt",
            ),
            patch.object(
                main,
                "create_auth_session",
                return_value="session-token",
            ),
        ):
            result = (
                main.commissioner_write_login(
                    CommissionerWriteLoginRequest(
                        email=(
                            "commissioner@example.com"
                        ),
                        password="secret",
                    ),
                    browser_request(),
                    response,
                )
            )

        self.assertTrue(
            result.write_enabled
        )

        cookie = response.headers.get(
            "set-cookie",
            "",
        )

        self.assertIn(
            "mlf_auth=session-token",
            cookie,
        )
        self.assertIn(
            "HttpOnly",
            cookie,
        )
        self.assertIn(
            "Secure",
            cookie,
        )

    def test_non_commissioner_cannot_login_for_writes(
        self,
    ) -> None:
        principal = {
            "user_id": 8,
            "email": "manager@example.com",
            "password_hash": "hash",
            "active": True,
            "is_site_admin": False,
            "must_change_password": False,
            "league_role": "manager",
        }

        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role": "commissioner"
                },
            ),
            patch.object(
                main,
                "is_login_rate_limited",
                return_value=False,
            ),
            patch.object(
                main,
                "load_login_user",
                return_value=principal,
            ),
            patch.object(
                main,
                "verify_password",
                return_value=True,
            ),
            patch.object(
                main,
                "record_login_attempt",
            ),
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_write_login(
                    CommissionerWriteLoginRequest(
                        email="manager@example.com",
                        password="secret",
                    ),
                    browser_request(),
                    Response(),
                )

        self.assertEqual(
            raised.exception.status_code,
            403,
        )

    def test_yahoo_refresh_requires_write_identity(
        self,
    ) -> None:
        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role": "commissioner"
                },
            ),
            patch.object(
                main,
                "_require_commissioner_write_principal",
                side_effect=HTTPException(
                    status_code=401,
                    detail={
                        "code":
                            "authentication_required"
                    },
                ),
            ),
            patch.object(
                main,
                "refresh_yahoo_player_universe",
            ) as refresh,
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_yahoo_player_universe_refresh(
                    browser_request(
                        path=(
                            "/gateway/commissioner/"
                            "yahoo-player-universe/"
                            "refresh"
                        )
                    )
                )

        self.assertEqual(
            raised.exception.status_code,
            401,
        )

        refresh.assert_not_called()

    def test_yahoo_refresh_delegates_once(
        self,
    ) -> None:
        refresh_result = {
            "finished_at_utc":
                "2026-09-30T12:00:00+00:00",
            "duration_sec": 12.5,
            "players_before": 1000,
            "players_after": 1001,
            "meta_updated_last_10m": 999,
            "stats_season": 2025,
        }

        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role": "commissioner"
                },
            ),
            patch.object(
                main,
                "_require_commissioner_write_principal",
                return_value={
                    "user_id": 7,
                    "email":
                        "commissioner@example.com",
                    "is_site_admin": False,
                    "must_change_password": False,
                    "league_role":
                        "commissioner",
                },
            ),
            patch.object(
                main,
                "refresh_yahoo_player_universe",
                return_value=refresh_result,
            ) as refresh,
        ):
            result = (
                main.commissioner_yahoo_player_universe_refresh(
                    browser_request(
                        path=(
                            "/gateway/commissioner/"
                            "yahoo-player-universe/"
                            "refresh"
                        )
                    )
                )
            )

        refresh.assert_called_once_with()

        self.assertEqual(
            result.players_after,
            1001,
        )
        self.assertEqual(
            result.stats_season,
            2025,
        )
        self.assertEqual(
            result.performed_by,
            "commissioner@example.com",
        )


if __name__ == "__main__":
    unittest.main()
