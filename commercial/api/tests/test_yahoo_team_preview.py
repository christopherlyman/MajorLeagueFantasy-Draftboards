from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

from api import main as commercial_main


def request_with_cookie() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": (
                "/api/leagues/commercial.test/2027/"
                "providers/yahoo/connections/11/"
                "teams/preview"
            ),
            "query_string": (
                b"provider_league_key=469.l.41640"
            ),
            "headers": [
                (
                    b"cookie",
                    b"mlf_auth=valid-session",
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


def profile(
    *,
    manager_count: int = 2,
    platform: str = "yahoo",
):
    return SimpleNamespace(
        profile={
            "league": {
                "league_key":
                    "commercial.test",
                "season_year":
                    2027,
                "name":
                    "Preview Test",
                "platform":
                    platform,
                "manager_count":
                    manager_count,
            }
        }
    )


def provider(
    *,
    code: str = "yahoo",
    status: str = "active",
):
    return SimpleNamespace(
        provider_connection_id=11,
        provider_code=code,
        status=status,
    )


def yahoo_league(
    *,
    league_key: str = "469.l.41640",
    num_teams: int = 2,
):
    return SimpleNamespace(
        league_key=league_key,
        league_id="41640",
        name="Major League Fantasy",
        game_key="469",
        season=2026,
        num_teams=num_teams,
    )


def yahoo_team(
    slot: int,
    owner: str,
):
    return SimpleNamespace(
        team_key=(
            f"469.l.41640.t.{slot}"
        ),
        team_id=str(slot),
        name=f"Team {slot}",
        owner_name=owner,
        owner_guid=f"guid-{slot}",
    )


class YahooTeamPreviewTests(
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
                "/api/leagues/{league_key}/"
                "{season_year}/providers/yahoo/"
                "connections/"
                "{provider_connection_id}/"
                "teams/preview"
            ),
            paths,
        )

    def test_authentication_occurs_before_authorization(
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
                    detail=(
                        "Authentication required."
                    ),
                ),
            ),
            patch.object(
                commercial_main,
                "can_administer_commercial_league",
            ) as admin_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )

        self.assertEqual(
            caught.exception.status_code,
            401,
        )
        admin_mock.assert_not_called()

    def test_non_commissioner_is_rejected_before_provider_lookup(
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
                return_value=SimpleNamespace(
                    user_id=7
                ),
            ),
            patch.object(
                commercial_main,
                "can_administer_commercial_league",
                return_value=False,
            ) as admin_mock,
            patch.object(
                commercial_main,
                "load_provider_connection",
            ) as provider_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )

        self.assertEqual(
            caught.exception.status_code,
            403,
        )

        admin_mock.assert_called_once_with(
            db,
            user_id=7,
            league_key="commercial.test",
            season_year=2027,
        )

        provider_mock.assert_not_called()

    def test_foreign_provider_connection_returns_404(
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
                return_value=SimpleNamespace(
                    user_id=7
                ),
            ),
            patch.object(
                commercial_main,
                "can_administer_commercial_league",
                return_value=True,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=profile(),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=None,
            ) as provider_mock,
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
            ) as token_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )

        self.assertEqual(
            caught.exception.status_code,
            404,
        )

        provider_mock.assert_called_once_with(
            db,
            user_id=7,
            provider_connection_id=11,
        )

        token_mock.assert_not_called()

    def test_non_yahoo_commercial_profile_is_rejected(
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
                return_value=SimpleNamespace(
                    user_id=7
                ),
            ),
            patch.object(
                commercial_main,
                "can_administer_commercial_league",
                return_value=True,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=profile(
                    platform="manual"
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
            ) as provider_mock,
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )

        self.assertEqual(
            caught.exception.status_code,
            409,
        )
        provider_mock.assert_not_called()

    def test_selected_league_must_be_visible_to_connection(
        self,
    ):
        db = DummyConnection()

        adapter = Mock()
        adapter.fetch_leagues.return_value = [
            yahoo_league(
                league_key="469.l.22528"
            )
        ]

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
                "can_administer_commercial_league",
                return_value=True,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=profile(),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider(),
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
                return_value="secret-token",
            ),
            patch.object(
                commercial_main,
                "YahooFantasyAdapter",
                return_value=adapter,
            ),
        ):
            with self.assertRaises(
                HTTPException
            ) as caught:
                commercial_main.preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )

        self.assertEqual(
            caught.exception.status_code,
            404,
        )

        adapter.fetch_leagues.assert_called_once_with(
            access_token="secret-token",
            game_key="469",
        )

        adapter.fetch_teams.assert_not_called()

    def test_success_returns_team_and_manager_preview(
        self,
    ):
        db = DummyConnection()

        adapter = Mock()
        adapter.fetch_leagues.return_value = [
            yahoo_league(
                num_teams=2
            )
        ]
        adapter.fetch_teams.return_value = [
            yahoo_team(
                1,
                "Commissioner",
            ),
            yahoo_team(
                2,
                "Manager Two",
            ),
        ]

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
                "can_administer_commercial_league",
                return_value=True,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=profile(
                    manager_count=2
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider(),
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
                return_value="secret-token",
            ),
            patch.object(
                commercial_main,
                "YahooFantasyAdapter",
                return_value=adapter,
            ),
        ):
            result = (
                commercial_main
                .preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )
            )

        self.assertEqual(
            result["commercial_league"][
                "manager_count"
            ],
            2,
        )

        self.assertEqual(
            result["selected_league"][
                "league_key"
            ],
            "469.l.41640",
        )

        self.assertEqual(
            result["import_preview"],
            {
                "expected_team_count": 2,
                "provider_declared_team_count": 2,
                "actual_team_count": 2,
                "team_count_matches": True,
                "ready_to_import": True,
            },
        )

        self.assertEqual(
            result["teams"][0],
            {
                "team_key":
                    "469.l.41640.t.1",
                "team_id":
                    "1",
                "name":
                    "Team 1",
                "owner_name":
                    "Commissioner",
                "owner_guid":
                    "guid-1",
            },
        )

        self.assertNotIn(
            "secret-token",
            repr(result),
        )

        adapter.fetch_teams.assert_called_once_with(
            access_token="secret-token",
            league_key="469.l.41640",
        )

    def test_team_count_mismatch_is_reported_not_hidden(
        self,
    ):
        db = DummyConnection()

        adapter = Mock()
        adapter.fetch_leagues.return_value = [
            yahoo_league(
                num_teams=16
            )
        ]
        adapter.fetch_teams.return_value = [
            yahoo_team(
                slot,
                f"Manager {slot}",
            )
            for slot in range(
                1,
                17,
            )
        ]

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
                "can_administer_commercial_league",
                return_value=True,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=profile(
                    manager_count=12
                ),
            ),
            patch.object(
                commercial_main,
                "load_provider_connection",
                return_value=provider(),
            ),
            patch.object(
                commercial_main,
                "get_legacy_yahoo_access_token",
                return_value="secret-token",
            ),
            patch.object(
                commercial_main,
                "YahooFantasyAdapter",
                return_value=adapter,
            ),
        ):
            result = (
                commercial_main
                .preview_yahoo_league_teams(
                    league_key="commercial.test",
                    season_year=2027,
                    provider_connection_id=11,
                    request=request_with_cookie(),
                    provider_league_key="469.l.41640",
                )
            )

        preview = result[
            "import_preview"
        ]

        self.assertEqual(
            preview[
                "expected_team_count"
            ],
            12,
        )

        self.assertEqual(
            preview[
                "actual_team_count"
            ],
            16,
        )

        self.assertFalse(
            preview[
                "team_count_matches"
            ]
        )

        self.assertFalse(
            preview[
                "ready_to_import"
            ]
        )

        self.assertEqual(
            len(result["teams"]),
            16,
        )


if __name__ == "__main__":
    unittest.main()
