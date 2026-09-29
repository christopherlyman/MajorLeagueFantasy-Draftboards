from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import Response
from starlette.requests import Request

from mlf_api import main

from mlf_api.commissioner_auth import (
    commissioner_token_is_valid,
    pack_commissioner_cookie,
    resolve_commissioner_cookie,
)


class CommissionerAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = (
            "commissioner-test-token-"
            "0123456789abcdefghijklmnopqrstuvwxyz"
        )

    def _patch_runtime(self):
        return (
            patch.dict(
                os.environ,
                {
                    "MLF_COMMISSIONER_GATEWAY_TOKEN":
                        self.secret
                },
                clear=False,
            ),
            patch(
                "mlf_api.commissioner_auth.get_league_key",
                return_value="469.l.41640",
            ),
            patch(
                "mlf_api.commissioner_auth.get_season_year",
                return_value=2026,
            ),
        )

    def test_private_commissioner_token_uses_constant_contract(
        self,
    ) -> None:
        env_patch, league_patch, season_patch = (
            self._patch_runtime()
        )

        with env_patch, league_patch, season_patch:
            self.assertTrue(
                commissioner_token_is_valid(
                    self.secret
                )
            )
            self.assertFalse(
                commissioner_token_is_valid(
                    self.secret + "-wrong"
                )
            )

    def test_signed_cookie_round_trip(
        self,
    ) -> None:
        env_patch, league_patch, season_patch = (
            self._patch_runtime()
        )

        with env_patch, league_patch, season_patch:
            cookie = pack_commissioner_cookie()
            principal = (
                resolve_commissioner_cookie(
                    cookie
                )
            )

        self.assertIsNotNone(principal)
        assert principal is not None

        self.assertTrue(
            principal["is_authenticated"]
        )
        self.assertEqual(
            principal["role"],
            "commissioner",
        )
        self.assertEqual(
            principal["league_key"],
            "469.l.41640",
        )
        self.assertEqual(
            principal["season_year"],
            2026,
        )

    def test_tampered_cookie_fails_closed(
        self,
    ) -> None:
        env_patch, league_patch, season_patch = (
            self._patch_runtime()
        )

        with env_patch, league_patch, season_patch:
            cookie = pack_commissioner_cookie()

            prefix, signature = cookie.split(
                ".",
                1,
            )

            tampered = (
                prefix
                + "x."
                + signature
            )

            principal = (
                resolve_commissioner_cookie(
                    tampered
                )
            )

        self.assertIsNone(principal)


    def test_public_commissioner_route_returns_public_principal(
        self,
    ) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/commissioner/auth/me",
                "headers": [],
                "scheme": "https",
                "server": (
                    "mlf.majorleaguefantasy.app",
                    443,
                ),
                "client": ("127.0.0.1", 12345),
            }
        )

        with (
            patch.object(
                main,
                "get_league_key",
                return_value="469.l.41640",
            ),
            patch.object(
                main,
                "get_season_year",
                return_value=2026,
            ),
        ):
            result = main.commissioner_auth_me(
                request=request,
                response=Response(),
            )

        self.assertFalse(result.is_authenticated)
        self.assertEqual(result.role, "public")
        self.assertEqual(
            result.league_key,
            "469.l.41640",
        )
        self.assertEqual(result.season_year, 2026)

if __name__ == "__main__":
    unittest.main()
