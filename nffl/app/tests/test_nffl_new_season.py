from __future__ import annotations

import unittest

from draftboard.domain.nffl_new_season import (
    build_nffl_new_season_spec,
    suggested_nffl_draft_key,
)


def current_profile() -> dict:
    return {
        "league": {
            "league_key": "470.l.84346",
            "name": "NFFL",
            "platform": "yahoo",
            "sport": "football",
            "season_year": 2026,
            "manager_count": 12,
        },
        "draft": {
            "type": "standard",
            "order_mode": "straight",
            "mode": "offline",
            "rounds_total": 16,
            "qo_rounds": 4,
            "pick_trades_allowed": True,
        },
        "scoring": {
            "format": "h2h_points",
        },
        "features": {
            "keeper": True,
            "keeper_type": "contract",
            "keepers_count": 4,
            "contract_lengths": [4, 3, 2],
            "contracts": True,
            "qualifying_offers": True,
            "franchise_tags": True,
            "prospect_tags": False,
            "commissioner_tools": True,
        },
        "roster": {
            "positions": [
                "QB",
                "WR",
                "RB",
                "TE",
                "W/R/T",
                "K",
                "DEF",
                "BN",
                "IR",
            ],
        },
        "categories": {
            "offense": ["pass_yd"],
        },
        "runtime": {
            "db_scope_mode": "league_key",
        },
    }


class NfflNewSeasonSpecTests(unittest.TestCase):

    def test_builds_2027_spec_without_mutating_source_profile(self) -> None:
        original = current_profile()

        spec = build_nffl_new_season_spec(
            original,
            target_league_key="999.l.12345",
            target_season_year=2027,
            target_draft_key="nffl_2027_preseason",
        )

        self.assertEqual(spec.league_code, "NFFL")
        self.assertEqual(spec.prior_season_year, 2026)
        self.assertEqual(spec.prior_league_key, "470.l.84346")
        self.assertEqual(spec.current_season_year, 2027)
        self.assertEqual(spec.current_league_key, "999.l.12345")
        self.assertEqual(spec.draft_key, "nffl_2027_preseason")

        self.assertEqual(spec.manager_count, 12)
        self.assertEqual(spec.rounds_total, 16)
        self.assertEqual(spec.qo_rounds, 4)
        self.assertEqual(spec.expected_draft_rows, 192)

        self.assertEqual(
            spec.profile["league"]["league_key"],
            "999.l.12345",
        )
        self.assertEqual(
            spec.profile["league"]["season_year"],
            2027,
        )

        # Planner must never mutate the active profile it received.
        self.assertEqual(
            original["league"]["league_key"],
            "470.l.84346",
        )
        self.assertEqual(
            original["league"]["season_year"],
            2026,
        )

    def test_requires_exactly_next_season(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "prior_season_year",
        ):
            build_nffl_new_season_spec(
                current_profile(),
                target_league_key="999.l.12345",
                target_season_year=2028,
                target_draft_key="nffl_2028_preseason",
            )

    def test_rejects_reusing_prior_league_key(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "must differ",
        ):
            build_nffl_new_season_spec(
                current_profile(),
                target_league_key="470.l.84346",
                target_season_year=2027,
                target_draft_key="nffl_2027_preseason",
            )

    def test_qo_disabled_means_zero_qo_rounds(self) -> None:
        profile = current_profile()
        profile["features"]["qualifying_offers"] = False

        spec = build_nffl_new_season_spec(
            profile,
            target_league_key="999.l.12345",
            target_season_year=2027,
            target_draft_key="nffl_2027_preseason",
        )

        self.assertEqual(spec.qo_rounds, 0)

    def test_draft_key_suggestion_is_season_agnostic(self) -> None:
        self.assertEqual(
            suggested_nffl_draft_key(2031),
            "nffl_2031_preseason",
        )


if __name__ == "__main__":
    unittest.main()
