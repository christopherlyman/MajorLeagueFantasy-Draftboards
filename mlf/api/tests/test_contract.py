from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from fastapi.routing import APIRoute
from starlette.requests import Request

from mlf_api import gateway, main


LEAGUE_KEY = "469.l.41640"
SEASON_YEAR = 2026
TEAM_KEY = "469.l.41640.t.1"


def request_with_cookie(
    value: str | None = None,
) -> Request:
    headers = []

    if value is not None:
        headers.append(
            (
                b"cookie",
                f"mlf_team_gateway={value}".encode("ascii"),
            )
        )

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "query_string": b"",
            "scheme": "https",
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


class GatewayContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = patch.dict(
            os.environ,
            {
                "MLF_GATEWAY_COOKIE_SECRET":
                    "contract-test-secret"
            },
            clear=False,
        )
        self.secret.start()

    def tearDown(self) -> None:
        self.secret.stop()

    def _pack_cookie(self) -> str:
        with (
            patch.object(
                gateway,
                "get_league_key",
                return_value=LEAGUE_KEY,
            ),
            patch.object(
                gateway,
                "get_season_year",
                return_value=SEASON_YEAR,
            ),
        ):
            return gateway.pack_manager_cookie(
                franchise_id=1,
                team_key=TEAM_KEY,
            )

    def test_route_surface(self) -> None:
        routes = [
            route
            for route in main.app.routes
            if isinstance(route, APIRoute)
        ]

        self.assertEqual(
            {route.path for route in routes},
            {
                "/health",
                "/auth/me",
                "/gateway/claim",
                "/gateway/clear",
            },
        )

        for route in routes:
            self.assertEqual(
                route.methods,
                {"GET"},
            )

    def test_schema_endpoints_disabled(self) -> None:
        self.assertIsNone(main.app.docs_url)
        self.assertIsNone(main.app.redoc_url)
        self.assertIsNone(main.app.openapi_url)

    def test_signed_cookie_round_trip(self) -> None:
        token = self._pack_cookie()

        with (
            patch.object(
                gateway,
                "get_league_key",
                return_value=LEAGUE_KEY,
            ),
            patch.object(
                gateway,
                "get_season_year",
                return_value=SEASON_YEAR,
            ),
        ):
            payload = gateway.unpack_manager_cookie(token)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["role"], "manager")
        self.assertEqual(payload["league_key"], LEAGUE_KEY)
        self.assertEqual(payload["season_year"], SEASON_YEAR)
        self.assertEqual(payload["franchise_id"], 1)
        self.assertEqual(payload["team_key"], TEAM_KEY)

    def test_tampered_cookie_rejected(self) -> None:
        token = self._pack_cookie()

        replacement = "0" if token[-1] != "0" else "1"
        tampered = token[:-1] + replacement

        with (
            patch.object(
                gateway,
                "get_league_key",
                return_value=LEAGUE_KEY,
            ),
            patch.object(
                gateway,
                "get_season_year",
                return_value=SEASON_YEAR,
            ),
        ):
            result = gateway.unpack_manager_cookie(tampered)

        self.assertIsNone(result)

    def test_wrong_league_rejected(self) -> None:
        token = self._pack_cookie()

        with (
            patch.object(
                gateway,
                "get_league_key",
                return_value="different.league",
            ),
            patch.object(
                gateway,
                "get_season_year",
                return_value=SEASON_YEAR,
            ),
        ):
            result = gateway.unpack_manager_cookie(token)

        self.assertIsNone(result)

    def test_wrong_season_rejected(self) -> None:
        token = self._pack_cookie()

        with (
            patch.object(
                gateway,
                "get_league_key",
                return_value=LEAGUE_KEY,
            ),
            patch.object(
                gateway,
                "get_season_year",
                return_value=2025,
            ),
        ):
            result = gateway.unpack_manager_cookie(token)

        self.assertIsNone(result)

    def test_missing_secret_fails_closed(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"MLF_GATEWAY_COOKIE_SECRET": ""},
                clear=False,
            ),
            patch.object(
                gateway,
                "get_league_key",
                return_value=LEAGUE_KEY,
            ),
            patch.object(
                gateway,
                "get_season_year",
                return_value=SEASON_YEAR,
            ),
        ):
            with self.assertRaises(RuntimeError):
                gateway.pack_manager_cookie(
                    franchise_id=1,
                    team_key=TEAM_KEY,
                )

    @patch("mlf_api.main.public_principal")
    def test_auth_me_without_cookie_is_public(
        self,
        mock_public,
    ) -> None:
        mock_public.return_value = {
            "is_authenticated": False,
            "role": "public",
            "league_key": LEAGUE_KEY,
            "season_year": SEASON_YEAR,
            "franchise_id": None,
            "team_key": None,
            "team_name": None,
            "display_name": "Public",
            "acting_as": "public",
        }

        result = main.auth_me(
            request_with_cookie(),
            Response(),
        )

        self.assertFalse(result.is_authenticated)
        self.assertEqual(result.role, "public")

    @patch("mlf_api.main.resolve_manager_principal")
    def test_auth_me_valid_manager(
        self,
        mock_resolve,
    ) -> None:
        mock_resolve.return_value = {
            "is_authenticated": True,
            "role": "manager",
            "league_key": LEAGUE_KEY,
            "season_year": SEASON_YEAR,
            "franchise_id": 1,
            "team_key": TEAM_KEY,
            "team_name": "The Gunn Show",
            "display_name": "Manager",
            "acting_as": "manager:The Gunn Show",
        }

        result = main.auth_me(
            request_with_cookie("signed-cookie"),
            Response(),
        )

        self.assertTrue(result.is_authenticated)
        self.assertEqual(result.team_key, TEAM_KEY)

    @patch("mlf_api.main.claim_team_gateway_link")
    @patch("mlf_api.main.pack_manager_cookie")
    def test_claim_sets_secure_cookie(
        self,
        mock_pack,
        mock_claim,
    ) -> None:
        mock_claim.return_value = {
            "franchise_id": 1,
            "team_key": TEAM_KEY,
        }
        mock_pack.return_value = "signed-cookie"

        response = main.gateway_claim(
            token="private-token",
            next="/draft",
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/draft",
        )

        cookie = response.headers["set-cookie"]

        self.assertIn("mlf_team_gateway=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Max-Age=15552000", cookie)

    @patch(
        "mlf_api.main.claim_team_gateway_link",
        return_value=None,
    )
    def test_invalid_claim_is_404(
        self,
        _mock_claim,
    ) -> None:
        with self.assertRaises(HTTPException) as ctx:
            main.gateway_claim(token="invalid")

        self.assertEqual(
            ctx.exception.status_code,
            404,
        )

    def test_external_redirect_is_blocked(self) -> None:
        self.assertEqual(
            main._safe_next_path(
                "https://example.com"
            ),
            "/",
        )
        self.assertEqual(
            main._safe_next_path(
                "//example.com"
            ),
            "/",
        )
        self.assertEqual(
            main._safe_next_path(
                "/draft"
            ),
            "/draft",
        )

    @patch("mlf_api.main._database_ping")
    def test_health_success(
        self,
        mock_ping,
    ) -> None:
        mock_ping.return_value = None

        result = main.health()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.database, "ok")

    @patch("mlf_api.main._database_ping")
    def test_health_failure_is_503(
        self,
        mock_ping,
    ) -> None:
        mock_ping.side_effect = RuntimeError(
            "database unavailable"
        )

        with self.assertRaises(HTTPException) as ctx:
            main.health()

        self.assertEqual(
            ctx.exception.status_code,
            503,
        )


if __name__ == "__main__":
    unittest.main()