from __future__ import annotations

import unittest
from unittest.mock import patch

from draftboard.data import yahoo_client as client


LEAGUE_KEY = "999.l.12345"
SEASON_YEAR = 2027


def _team(team_id: int) -> dict:
    return {
        "team": [
            [
                {"team_key": f"{LEAGUE_KEY}.t.{team_id}"},
                {"team_id": str(team_id)},
                {"name": f"Team {team_id}"},
                {
                    "managers": [
                        {
                            "manager": {
                                "nickname": f"Owner {team_id}",
                                "guid": f"guid-{team_id}",
                            }
                        }
                    ]
                },
            ]
        ]
    }


def _payload(team_count: int) -> dict:
    teams = {
        str(index): _team(index + 1)
        for index in range(team_count)
    }
    teams["count"] = team_count

    return {
        "fantasy_content": {
            "league": [
                {"league_key": LEAGUE_KEY},
                {"teams": teams},
            ]
        }
    }


class YahooTeamParserTests(unittest.TestCase):
    def test_extract_uses_explicit_manager_count(self) -> None:
        rows = client.extract_yahoo_team_rows(
            _payload(2),
            league_key=LEAGUE_KEY,
            season_year=SEASON_YEAR,
            expected_manager_count=2,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row["team_key"] for row in rows],
            [
                f"{LEAGUE_KEY}.t.1",
                f"{LEAGUE_KEY}.t.2",
            ],
        )

    def test_wrong_team_count_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Expected 2 Yahoo teams",
        ):
            client.extract_yahoo_team_rows(
                _payload(1),
                league_key=LEAGUE_KEY,
                season_year=SEASON_YEAR,
                expected_manager_count=2,
            )

    def test_malformed_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Unexpected Yahoo JSON shape",
        ):
            client.extract_yahoo_team_rows(
                {"fantasy_content": {}},
                league_key=LEAGUE_KEY,
                season_year=SEASON_YEAR,
                expected_manager_count=2,
            )

    def test_wrong_league_team_key_is_rejected(self) -> None:
        payload = _payload(2)
        payload["fantasy_content"]["league"][1]["teams"]["0"][
            "team"
        ][0][0]["team_key"] = "other.league.t.1"

        with self.assertRaisesRegex(
            ValueError,
            "does not belong to league",
        ):
            client.extract_yahoo_team_rows(
                payload,
                league_key=LEAGUE_KEY,
                season_year=SEASON_YEAR,
                expected_manager_count=2,
            )



class FakeCursor:
    def __init__(self) -> None:
        self.execute_calls = []
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params) -> None:
        self.execute_calls.append((sql, params))

    def executemany(self, sql, values) -> None:
        self.executemany_calls.append((sql, list(values)))


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_object = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_object


class YahooTeamRefreshTests(unittest.TestCase):
    def test_refresh_replaces_validated_snapshot(self) -> None:
        connection = FakeConnection()

        with (
            patch.object(
                client,
                "get_yahoo_access_token",
                return_value="token",
            ),
            patch.object(
                client,
                "fetch_yahoo_json",
                return_value=_payload(2),
            ),
            patch.object(
                client.psycopg,
                "connect",
                return_value=connection,
            ) as connect,
        ):
            rows = client.refresh_yahoo_team_map(
                "fake-dsn",
                league_key=LEAGUE_KEY,
                season_year=SEASON_YEAR,
                expected_manager_count=2,
            )

        self.assertEqual(len(rows), 2)
        connect.assert_called_once_with("fake-dsn")

        cursor = connection.cursor_object

        self.assertEqual(len(cursor.execute_calls), 1)
        self.assertIn(
            "DELETE FROM public.yahoo_team_map",
            cursor.execute_calls[0][0],
        )
        self.assertEqual(
            cursor.execute_calls[0][1],
            (LEAGUE_KEY, SEASON_YEAR),
        )

        self.assertEqual(len(cursor.executemany_calls), 1)
        self.assertIn(
            "INSERT INTO public.yahoo_team_map",
            cursor.executemany_calls[0][0],
        )
        self.assertEqual(
            len(cursor.executemany_calls[0][1]),
            2,
        )

    def test_invalid_team_count_never_opens_database(self) -> None:
        with (
            patch.object(
                client,
                "get_yahoo_access_token",
                return_value="token",
            ),
            patch.object(
                client,
                "fetch_yahoo_json",
                return_value=_payload(1),
            ),
            patch.object(
                client.psycopg,
                "connect",
            ) as connect,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Expected 2 Yahoo teams",
            ):
                client.refresh_yahoo_team_map(
                    "fake-dsn",
                    league_key=LEAGUE_KEY,
                    season_year=SEASON_YEAR,
                    expected_manager_count=2,
                )

        connect.assert_not_called()



class YahooLeagueIdResolverTests(unittest.TestCase):
    def _games_payload(self) -> dict:
        return {
            "fantasy_content": {
                "users": [
                    {
                        "user": [
                            {},
                            {
                                "games": {
                                    "0": {
                                        "game": [
                                            {"game_key": "469"},
                                            {"code": "mlb"},
                                            {"season": "2026"},
                                            {"name": "Baseball"},
                                        ]
                                    },
                                    "1": {
                                        "game": [
                                            {"game_key": "470"},
                                            {"code": "nfl"},
                                            {"season": "2026"},
                                            {"name": "Football"},
                                        ]
                                    },
                                    "count": 2,
                                }
                            },
                        ]
                    }
                ]
            }
        }

    def test_extracts_nfl_game_key_for_target_season(self) -> None:
        game_key = client.extract_yahoo_game_key(
            self._games_payload(),
            sport_code="nfl",
            season_year=2026,
        )

        self.assertEqual(game_key, "470")

    def test_missing_target_season_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "has not exposed a nfl game for season 2027",
        ):
            client.extract_yahoo_game_key(
                self._games_payload(),
                sport_code="nfl",
                season_year=2027,
            )

    def test_visible_league_id_resolves_full_league_key(self) -> None:
        with (
            patch.object(
                client,
                "get_yahoo_access_token",
                return_value="token",
            ),
            patch.object(
                client,
                "fetch_yahoo_json",
                return_value=self._games_payload(),
            ) as fetch,
        ):
            league_key = client.resolve_yahoo_league_key(
                "84346",
                season_year=2026,
            )

        self.assertEqual(
            league_key,
            "470.l.84346",
        )

        fetch.assert_called_once_with(
            "token",
            (
                "https://fantasysports.yahooapis.com/"
                "fantasy/v2/"
                "users;use_login=1/games?format=json"
            ),
        )

    def test_non_numeric_league_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "positive numeric ID",
        ):
            client.resolve_yahoo_league_key(
                "nffl65469",
                season_year=2026,
            )


if __name__ == "__main__":
    unittest.main()
