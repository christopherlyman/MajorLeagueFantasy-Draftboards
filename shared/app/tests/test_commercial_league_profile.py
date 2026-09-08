from __future__ import annotations

import unittest

from draftboard.state.commercial_league_profile import (
    CommercialLeagueProfileError,
    summarize_commercial_league_profile,
    validate_commercial_league_profile,
)


def valid_baseball_profile() -> dict:
    # These values exercise configurability. They are not frozen commercial defaults.
    return {
        "league": {
            "league_key": "pilot.baseball.2027",
            "name": "Pilot Baseball",
            "platform": "manual",
            "sport": "baseball",
            "season_year": 2027,
            "manager_count": 12,
        },
        "draft": {
            "type": "standard",
            "order_mode": "straight",
            "mode": "offline",
            "rounds_total": 25,
            "pick_trades_allowed": True,
        },
        "scoring": {
            "format": "h2h_categories",
        },
        "roster": {
            "positions": ["C", "1B", "2B", "3B", "SS", "OF", "SP", "RP", "BN"],
        },
        "categories": {
            "batting": ["R", "HR", "RBI", "SB", "AVG"],
            "pitching": ["W", "K", "ERA", "WHIP", "SV"],
        },
        "player_control": {
            "contracts": {
                "enabled": True,
                "slots": [
                    {"duration_years": 5, "count": 1},
                    {"duration_years": 4, "count": 1},
                    {"duration_years": 3, "count": 1},
                ],
            },
            "qualifying_offers": {
                "enabled": True,
                "count": 3,
            },
            "franchise_tag": {
                "enabled": False,
                "max_per_team": 0,
            },
            "prospect_tag": {
                "enabled": True,
                "max_per_team": 1,
            },
        },
        "runtime": {
            "db_scope_mode": "league_key",
        },
    }


class CommercialLeagueProfileTests(unittest.TestCase):
    def test_valid_manual_baseball_profile(self) -> None:
        profile = valid_baseball_profile()
        validate_commercial_league_profile(profile)

        summary = summarize_commercial_league_profile(profile)

        self.assertEqual(summary["sport"], "baseball")
        self.assertEqual(summary["platform"], "manual")
        self.assertEqual(summary["contract_slots"], 3)
        self.assertEqual(summary["qualifying_offers"], 3)
        self.assertTrue(summary["prospect_tag_enabled"])
        self.assertFalse(summary["franchise_tag_enabled"])

    def test_provider_is_metadata_not_yahoo_requirement(self) -> None:
        profile = valid_baseball_profile()
        profile["league"]["platform"] = "custom_provider"

        validate_commercial_league_profile(profile)

    def test_missing_required_section_fails(self) -> None:
        profile = valid_baseball_profile()
        del profile["player_control"]

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "player_control",
        ):
            validate_commercial_league_profile(profile)

    def test_contracts_are_required_for_commercial_msp(self) -> None:
        profile = valid_baseball_profile()
        profile["player_control"]["contracts"]["enabled"] = False

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "contracts.enabled must be true",
        ):
            validate_commercial_league_profile(profile)

    def test_duplicate_contract_duration_fails(self) -> None:
        profile = valid_baseball_profile()
        profile["player_control"]["contracts"]["slots"].append(
            {"duration_years": 5, "count": 1}
        )

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "duration_years values must be unique",
        ):
            validate_commercial_league_profile(profile)

    def test_qo_count_is_explicit_when_enabled(self) -> None:
        profile = valid_baseball_profile()
        profile["player_control"]["qualifying_offers"]["count"] = 0

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "count must be >= 1",
        ):
            validate_commercial_league_profile(profile)

    def test_disabled_qo_requires_zero_count(self) -> None:
        profile = valid_baseball_profile()
        profile["player_control"]["qualifying_offers"]["enabled"] = False

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "count must equal 0",
        ):
            validate_commercial_league_profile(profile)

    def test_enabled_tag_requires_explicit_capacity(self) -> None:
        profile = valid_baseball_profile()
        profile["player_control"]["prospect_tag"]["max_per_team"] = 0

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "max_per_team must be >= 1",
        ):
            validate_commercial_league_profile(profile)

    def test_baseball_requires_batting_and_pitching_categories(self) -> None:
        profile = valid_baseball_profile()
        del profile["categories"]["pitching"]

        with self.assertRaisesRegex(
            CommercialLeagueProfileError,
            "categories.pitching",
        ):
            validate_commercial_league_profile(profile)

    def test_football_compatibility_target(self) -> None:
        profile = valid_baseball_profile()
        profile["league"]["league_key"] = "pilot.football.2027"
        profile["league"]["name"] = "Pilot Football"
        profile["league"]["sport"] = "football"
        profile["scoring"]["format"] = "h2h_points"
        profile["roster"]["positions"] = ["QB", "RB", "WR", "TE", "FLEX", "BN"]
        profile["categories"] = {"points": ["Fantasy Points"]}
        profile["player_control"]["prospect_tag"] = {
            "enabled": False,
            "max_per_team": 0,
        }

        validate_commercial_league_profile(profile)


if __name__ == "__main__":
    unittest.main()
