from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

from api import main as commercial_main


def request_with_cookie(
    token: str = "valid-session",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": (
                "/api/providers/yahoo/"
                "connections/11/leagues"
            ),
            "query_string":
                b"game_key=469",
            "headers": [
                (
                    b"cookie",
                    f"mlf_auth={token}".encode(
                        "ascii"
                    ),
                )
            ],
        }
    )


class DummyConnection:
    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False


class YahooLeagueEndpointTests(
    unittest.TestCase
):
    def test_route_is_registered(
        self,
    ):
        paths = {
            getattr(
                route,
                "path",
                None,
            )
            for route
            in commercial_main.app.routes
        }

        self.assertIn(
            (
                "/api/providers/yahoo/"
                "connections/"
                "{provider_connection_id}/"
                "leagues"
            ),
            paths,
        )

    def test_authentication_happens_before_provider_lookup(
        self,
    ):
        db = DummyConnection()

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                side_effect=HTTPException(
                    status_code=401,
                    detail="Authentication required.",
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
            ) as load_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.get_yahoo_connection_leagues(
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    game_key="469",
                )

        self.assertEqual(
            caught.exception.status_code,
            401,
        )
        load_mock.assert_not_called()

    def test_foreign_or_missing_connection_returns_404(
        self,
    ):
        db = DummyConnection()
        principal = SimpleNamespace(
            user_id=7
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=principal,
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=None,
            ) as load_mock,
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
            ) as token_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.get_yahoo_connection_leagues(
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    game_key="469",
                )

        self.assertEqual(
            caught.exception.status_code,
            404,
        )

        load_mock.assert_called_once_with(
            db,
            user_id=7,
            provider_connection_id=11,
        )

        token_mock.assert_not_called()

    def test_inactive_connection_is_rejected(
        self,
    ):
        db = DummyConnection()

        provider = SimpleNamespace(
            provider_connection_id=11,
            provider_code="yahoo",
            status="pending",
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=SimpleNamespace(
                    user_id=7
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider,
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
            ) as token_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.get_yahoo_connection_leagues(
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    game_key="469",
                )

        self.assertEqual(
            caught.exception.status_code,
            409,
        )

        token_mock.assert_not_called()

    def test_non_yahoo_connection_is_rejected(
        self,
    ):
        db = DummyConnection()

        provider = SimpleNamespace(
            provider_connection_id=11,
            provider_code="espn",
            status="active",
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=SimpleNamespace(
                    user_id=7
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider,
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
            ) as token_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.get_yahoo_connection_leagues(
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    game_key="469",
                )

        self.assertEqual(
            caught.exception.status_code,
            409,
        )
        token_mock.assert_not_called()

    def test_invalid_game_key_is_rejected_before_token_use(
        self,
    ):
        db = DummyConnection()

        provider = SimpleNamespace(
            provider_connection_id=11,
            provider_code="yahoo",
            status="active",
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=SimpleNamespace(
                    user_id=7
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider,
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
            ) as token_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.get_yahoo_connection_leagues(
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    game_key="../secret",
                )

        self.assertEqual(
            caught.exception.status_code,
            422,
        )
        token_mock.assert_not_called()

    def test_success_returns_structured_leagues_without_token(
        self,
    ):
        db = DummyConnection()

        provider = SimpleNamespace(
            provider_connection_id=11,
            provider_code="yahoo",
            status="active",
        )

        principal = SimpleNamespace(
            user_id=7
        )

        leagues = [
            SimpleNamespace(
                league_key="469.l.41640",
                league_id="41640",
                name="Example League",
                game_key="469",
                season=2026,
                num_teams=14,
            )
        ]

        adapter = Mock()
        adapter.fetch_leagues.return_value = (
            leagues
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=principal,
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider,
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
                return_value="server-secret-token",
            ),
            patch.object(
                commercial_main,
                "YahooFantasyAdapter",
                return_value=adapter,
            ),
        ):
            result = (
                commercial_main
                .get_yahoo_connection_leagues(
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    game_key="469",
                )
            )

        self.assertEqual(
            result,
            {
                "provider_connection_id":
                    11,
                "provider":
                    "yahoo",
                "game_key":
                    "469",
                "leagues": [
                    {
                        "league_key":
                            "469.l.41640",
                        "league_id":
                            "41640",
                        "name":
                            "Example League",
                        "game_key":
                            "469",
                        "season":
                            2026,
                        "num_teams":
                            14,
                    }
                ],
            },
        )

        self.assertNotIn(
            "server-secret-token",
            repr(result),
        )

        adapter.fetch_leagues.assert_called_once_with(
            access_token="server-secret-token",
            game_key="469",
        )


if __name__ == "__main__":
    unittest.main()
