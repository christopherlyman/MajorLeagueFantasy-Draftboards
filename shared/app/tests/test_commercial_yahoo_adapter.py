import unittest

from draftboard.state.commercial_yahoo_adapter import (
    YahooFantasyAdapter,
    YahooFantasyAdapterError,
    parse_yahoo_games,
    parse_yahoo_leagues,
    parse_yahoo_teams,
)


class YahooFantasyAdapterTests(
    unittest.TestCase
):
    def test_parse_games_from_yahoo_user_games_shape(
        self,
    ):
        payload = {
            "fantasy_content": {
                "users": {
                    "0": {
                        "user": [
                            {
                                "guid":
                                    "user-guid"
                            },
                            {
                                "games": {
                                    "0": {
                                        "game": [
                                            [
                                                {
                                                    "game_key":
                                                        "469"
                                                },
                                                {
                                                    "game_id":
                                                        "469"
                                                },
                                                {
                                                    "name":
                                                        "Baseball"
                                                },
                                                {
                                                    "code":
                                                        "mlb"
                                                },
                                                {
                                                    "season":
                                                        "2026"
                                                },
                                            ]
                                        ]
                                    },
                                    "count": 1,
                                }
                            },
                        ]
                    },
                    "count": 1,
                }
            }
        }

        games = parse_yahoo_games(
            payload
        )

        self.assertEqual(
            len(games),
            1,
        )
        self.assertEqual(
            games[0].game_key,
            "469",
        )
        self.assertEqual(
            games[0].name,
            "Baseball",
        )
        self.assertEqual(
            games[0].code,
            "mlb",
        )
        self.assertEqual(
            games[0].season,
            2026,
        )

    def test_parse_leagues_from_yahoo_user_game_leagues_shape(
        self,
    ):
        payload = {
            "fantasy_content": {
                "users": {
                    "0": {
                        "user": [
                            {
                                "guid":
                                    "user-guid"
                            },
                            {
                                "games": {
                                    "0": {
                                        "game": [
                                            [
                                                {
                                                    "game_key":
                                                        "469"
                                                }
                                            ],
                                            {
                                                "leagues": {
                                                    "0": {
                                                        "league": [
                                                            [
                                                                {
                                                                    "league_key":
                                                                        "469.l.41640"
                                                                },
                                                                {
                                                                    "league_id":
                                                                        "41640"
                                                                },
                                                                {
                                                                    "name":
                                                                        "Example League"
                                                                },
                                                                {
                                                                    "season":
                                                                        "2026"
                                                                },
                                                                {
                                                                    "num_teams":
                                                                        14
                                                                },
                                                            ]
                                                        ]
                                                    },
                                                    "count": 1,
                                                }
                                            },
                                        ]
                                    }
                                }
                            },
                        ]
                    }
                }
            }
        }

        leagues = parse_yahoo_leagues(
            payload
        )

        self.assertEqual(
            len(leagues),
            1,
        )
        self.assertEqual(
            leagues[0].league_key,
            "469.l.41640",
        )
        self.assertEqual(
            leagues[0].league_id,
            "41640",
        )
        self.assertEqual(
            leagues[0].name,
            "Example League",
        )
        self.assertEqual(
            leagues[0].game_key,
            "469",
        )
        self.assertEqual(
            leagues[0].season,
            2026,
        )
        self.assertEqual(
            leagues[0].num_teams,
            14,
        )

    def test_parse_teams_prefers_commissioner_manager(
        self,
    ):
        payload = {
            "fantasy_content": {
                "league": [
                    [
                        {
                            "league_key":
                                "469.l.41640"
                        }
                    ],
                    {
                        "teams": {
                            "0": {
                                "team": [
                                    [
                                        {
                                            "team_key":
                                                "469.l.41640.t.1"
                                        },
                                        {
                                            "team_id":
                                                "1"
                                        },
                                        {
                                            "name":
                                                "Example Team"
                                        },
                                        {
                                            "managers": [
                                                {
                                                    "manager": {
                                                        "nickname":
                                                            "First Manager",
                                                        "manager_id":
                                                            "101",
                                                        "guid":
                                                            "first-guid",
                                                        "is_commissioner":
                                                            "0",
                                                    }
                                                },
                                                {
                                                    "manager": {
                                                        "nickname":
                                                            "Commissioner",
                                                        "manager_id":
                                                            "102",
                                                        "guid":
                                                            "commissioner-guid",
                                                        "is_commissioner":
                                                            "1",
                                                    }
                                                },
                                            ]
                                        },
                                    ]
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            }
        }

        teams = parse_yahoo_teams(
            payload
        )

        self.assertEqual(
            len(teams),
            1,
        )
        self.assertEqual(
            teams[0].team_key,
            "469.l.41640.t.1",
        )
        self.assertEqual(
            teams[0].team_id,
            "1",
        )
        self.assertEqual(
            teams[0].name,
            "Example Team",
        )
        self.assertEqual(
            teams[0].owner_name,
            "Commissioner",
        )
        self.assertEqual(
            teams[0].manager_id,
            "102",
        )
        self.assertEqual(
            teams[0].owner_guid,
            "commissioner-guid",
        )

    def test_parse_teams_uses_first_manager_without_commissioner(
        self,
    ):
        payload = {
            "fantasy_content": {
                "league": [
                    [
                        {
                            "league_key":
                                "469.l.41640"
                        }
                    ],
                    {
                        "teams": {
                            "0": {
                                "team": [
                                    [
                                        {
                                            "team_key":
                                                "469.l.41640.t.2"
                                        },
                                        {
                                            "team_id":
                                                2
                                        },
                                        {
                                            "name":
                                                "Second Team"
                                        },
                                        {
                                            "managers": [
                                                {
                                                    "manager": {
                                                        "nickname":
                                                            "First Manager",
                                                        "manager_id":
                                                            "101",
                                                        "guid":
                                                            "first-guid",
                                                    }
                                                },
                                                {
                                                    "manager": {
                                                        "nickname":
                                                            "Second Manager",
                                                        "manager_id":
                                                            "202",
                                                        "guid":
                                                            "second-guid",
                                                    }
                                                },
                                            ]
                                        },
                                    ]
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            }
        }

        teams = parse_yahoo_teams(
            payload
        )

        self.assertEqual(
            len(teams),
            1,
        )
        self.assertEqual(
            teams[0].owner_name,
            "First Manager",
        )
        self.assertEqual(
            teams[0].manager_id,
            "101",
        )
        self.assertEqual(
            teams[0].owner_guid,
            "first-guid",
        )

    def test_duplicate_entities_are_deduplicated_by_provider_key(
        self,
    ):
        payload = {
            "fantasy_content": {
                "league": [
                    [
                        {
                            "league_key":
                                "469.l.41640"
                        },
                        {
                            "name":
                                "Example League"
                        },
                    ],
                    {
                        "duplicate_metadata": [
                            {
                                "league_key":
                                    "469.l.41640"
                            },
                            {
                                "name":
                                    "Example League"
                            },
                        ]
                    },
                ]
            }
        }

        leagues = parse_yahoo_leagues(
            payload
        )

        self.assertEqual(
            len(leagues),
            1,
        )

    def test_missing_fantasy_content_is_rejected(
        self,
    ):
        with self.assertRaises(
            YahooFantasyAdapterError
        ):
            parse_yahoo_games({})

    def test_fetch_leagues_uses_scoped_yahoo_url_and_bearer_token(
        self,
    ):
        calls = []

        def get_json(
            url,
            headers,
            timeout,
        ):
            calls.append(
                (
                    url,
                    dict(headers),
                    timeout,
                )
            )

            return {
                "fantasy_content": {
                    "users": {
                        "0": {
                            "user": [
                                {
                                    "games": {
                                        "0": {
                                            "game": [
                                                [
                                                    {
                                                        "game_key":
                                                            "469"
                                                    }
                                                ],
                                                {
                                                    "leagues": {
                                                        "count":
                                                            0
                                                    }
                                                },
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            }

        adapter = YahooFantasyAdapter(
            get_json=get_json,
            timeout_seconds=12,
        )

        leagues = adapter.fetch_leagues(
            access_token="secret-token",
            game_key="469",
        )

        self.assertEqual(
            leagues,
            [],
        )
        self.assertEqual(
            len(calls),
            1,
        )

        url, headers, timeout = calls[0]

        self.assertEqual(
            url,
            "https://fantasysports.yahooapis.com/"
            "fantasy/v2/users;use_login=1/games;"
            "game_keys=469/leagues?format=json",
        )
        self.assertEqual(
            headers,
            {
                "Authorization":
                    "Bearer secret-token"
            },
        )
        self.assertEqual(
            timeout,
            12.0,
        )

    def test_fetch_teams_uses_expected_yahoo_endpoint(
        self,
    ):
        calls = []

        def get_json(
            url,
            headers,
            timeout,
        ):
            calls.append(
                (
                    url,
                    dict(headers),
                    timeout,
                )
            )

            return {
                "fantasy_content": {
                    "league": [
                        [
                            {
                                "league_key":
                                    "469.l.41640"
                            }
                        ],
                        {
                            "teams": {
                                "count": 0
                            }
                        },
                    ]
                }
            }

        adapter = YahooFantasyAdapter(
            get_json=get_json
        )

        teams = adapter.fetch_teams(
            access_token="secret-token",
            league_key="469.l.41640",
        )

        self.assertEqual(
            teams,
            [],
        )
        self.assertEqual(
            calls[0][0],
            "https://fantasysports.yahooapis.com/"
            "fantasy/v2/league/469.l.41640/"
            "teams?format=json",
        )

    def test_blank_access_token_is_rejected_before_transport(
        self,
    ):
        def get_json(
            url,
            headers,
            timeout,
        ):
            raise AssertionError(
                "transport should not run"
            )

        adapter = YahooFantasyAdapter(
            get_json=get_json
        )

        with self.assertRaises(
            YahooFantasyAdapterError
        ):
            adapter.fetch_games(
                access_token="   "
            )


if __name__ == "__main__":
    unittest.main()
