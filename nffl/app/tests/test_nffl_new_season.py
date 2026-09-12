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



from draftboard.domain.nffl_new_season import (
    build_nffl_franchise_match_preview,
)


class NfflFranchiseMatchPreviewTests(unittest.TestCase):

    def _prior_rows(self) -> list[dict]:
        return [
            {
                "franchise_id": 33,
                "team_key": "old.t.1",
                "team_name": "Alpha",
                "owner_name": "Owner A",
                "owner_guid": "GUID-A",
            },
            {
                "franchise_id": 34,
                "team_key": "old.t.2",
                "team_name": "Beta",
                "owner_name": "Owner B",
                "owner_guid": "GUID-B",
            },
        ]

    def test_unique_owner_guid_matches_are_automatic(self) -> None:
        preview = build_nffl_franchise_match_preview(
            self._prior_rows(),
            [
                {
                    "team_key": "new.t.1",
                    "team_name": "Alpha Renamed",
                    "owner_name": "Owner A",
                    "owner_guid": "GUID-A",
                },
                {
                    "team_key": "new.t.2",
                    "team_name": "Beta",
                    "owner_name": "Owner B",
                    "owner_guid": "GUID-B",
                },
            ],
            expected_manager_count=2,
        )

        self.assertEqual(preview.auto_match_count, 2)
        self.assertEqual(preview.review_count, 0)
        self.assertTrue(preview.ready_for_apply)

        by_team = {
            row.target_team_key: row
            for row in preview.rows
        }

        self.assertEqual(
            by_team["new.t.1"].franchise_id,
            33,
        )
        self.assertEqual(
            by_team["new.t.1"].reason,
            "UNIQUE_OWNER_GUID_MATCH",
        )

    def test_missing_target_owner_guid_requires_review(self) -> None:
        preview = build_nffl_franchise_match_preview(
            self._prior_rows(),
            [
                {
                    "team_key": "new.t.1",
                    "team_name": "Alpha",
                    "owner_name": "Owner A",
                    "owner_guid": None,
                },
                {
                    "team_key": "new.t.2",
                    "team_name": "Beta",
                    "owner_name": "Owner B",
                    "owner_guid": "GUID-B",
                },
            ],
            expected_manager_count=2,
        )

        self.assertEqual(preview.auto_match_count, 1)
        self.assertEqual(preview.review_count, 1)
        self.assertFalse(preview.ready_for_apply)

        review = next(
            row
            for row in preview.rows
            if row.target_team_key == "new.t.1"
        )

        self.assertEqual(
            review.reason,
            "TARGET_OWNER_GUID_MISSING",
        )
        self.assertIsNone(review.franchise_id)

    def test_changed_owner_guid_requires_review(self) -> None:
        preview = build_nffl_franchise_match_preview(
            self._prior_rows(),
            [
                {
                    "team_key": "new.t.1",
                    "team_name": "Alpha",
                    "owner_name": "New Owner",
                    "owner_guid": "GUID-NEW",
                },
                {
                    "team_key": "new.t.2",
                    "team_name": "Beta",
                    "owner_name": "Owner B",
                    "owner_guid": "GUID-B",
                },
            ],
            expected_manager_count=2,
        )

        review = next(
            row
            for row in preview.rows
            if row.target_team_key == "new.t.1"
        )

        self.assertEqual(
            review.reason,
            "NO_OWNER_GUID_MATCH",
        )
        self.assertFalse(preview.ready_for_apply)

    def test_duplicate_prior_owner_guid_requires_review(self) -> None:
        prior = self._prior_rows()
        prior[1]["owner_guid"] = "GUID-A"

        preview = build_nffl_franchise_match_preview(
            prior,
            [
                {
                    "team_key": "new.t.1",
                    "team_name": "Alpha",
                    "owner_name": "Owner A",
                    "owner_guid": "GUID-A",
                },
                {
                    "team_key": "new.t.2",
                    "team_name": "Beta",
                    "owner_name": "Owner C",
                    "owner_guid": "GUID-C",
                },
            ],
            expected_manager_count=2,
        )

        alpha = next(
            row
            for row in preview.rows
            if row.target_team_key == "new.t.1"
        )

        self.assertEqual(
            alpha.reason,
            "PRIOR_OWNER_GUID_AMBIGUOUS",
        )
        self.assertIsNone(alpha.franchise_id)

    def test_duplicate_target_owner_guid_requires_review(self) -> None:
        preview = build_nffl_franchise_match_preview(
            self._prior_rows(),
            [
                {
                    "team_key": "new.t.1",
                    "team_name": "Alpha",
                    "owner_name": "Owner A",
                    "owner_guid": "GUID-A",
                },
                {
                    "team_key": "new.t.2",
                    "team_name": "Another Team",
                    "owner_name": "Owner A Again",
                    "owner_guid": "GUID-A",
                },
            ],
            expected_manager_count=2,
        )

        self.assertEqual(preview.auto_match_count, 0)
        self.assertEqual(preview.review_count, 2)
        self.assertFalse(preview.ready_for_apply)

        self.assertTrue(
            all(
                row.reason
                == "TARGET_OWNER_GUID_DUPLICATE"
                for row in preview.rows
            )
        )

    def test_team_count_must_match_active_profile(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "target_teams count",
        ):
            build_nffl_franchise_match_preview(
                self._prior_rows(),
                [
                    {
                        "team_key": "new.t.1",
                        "team_name": "Alpha",
                        "owner_name": "Owner A",
                        "owner_guid": "GUID-A",
                    },
                ],
                expected_manager_count=2,
            )

if __name__ == "__main__":
    unittest.main()
