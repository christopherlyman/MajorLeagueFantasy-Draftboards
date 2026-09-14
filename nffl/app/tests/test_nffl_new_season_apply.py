from __future__ import annotations

import unittest
from unittest.mock import patch

from draftboard.domain.nffl_new_season import (
    NfflNewSeasonSpec,
    NfflResolvedFranchiseAssignment,
)
from draftboard.domain.nffl_new_season_apply import (
    _gateway_token,
    _profile_yaml,
)


class NfflNewSeasonApplyPureTests(unittest.TestCase):

    def _spec(self) -> NfflNewSeasonSpec:
        profile = {
            "league": {
                "league_key": "new.league",
                "season_year": 2027,
                "manager_count": 2,
            },
            "draft": {
                "rounds_total": 16,
                "qo_rounds": 4,
            },
            "features": {
                "qualifying_offers": True,
            },
        }

        return NfflNewSeasonSpec(
            league_code="NFFL",
            prior_season_year=2026,
            prior_league_key="old.league",
            current_season_year=2027,
            current_league_key="new.league",
            draft_key="nffl_2027_preseason",
            manager_count=2,
            rounds_total=16,
            qo_rounds=4,
            expected_draft_rows=32,
            profile=profile,
        )

    def test_gateway_tokens_match_existing_nffl_shape(self) -> None:
        token = _gateway_token()

        self.assertEqual(len(token), 48)
        self.assertNotIn("=", token)

    def test_profile_yaml_preserves_target_identity(self) -> None:
        text = _profile_yaml(self._spec())

        self.assertIn('"league_key": "new.league"', text)
        self.assertIn('"season_year": 2027', text)
        self.assertIn('"manager_count": 2', text)

    def test_assignment_dataclass_can_represent_auto_and_manual(self) -> None:
        auto = NfflResolvedFranchiseAssignment(
            target_team_key="new.t.1",
            franchise_id=33,
            resolution_method="AUTO_OWNER_GUID",
        )
        manual = NfflResolvedFranchiseAssignment(
            target_team_key="new.t.2",
            franchise_id=34,
            resolution_method="COMMISSIONER_MANUAL",
        )

        self.assertEqual(auto.franchise_id, 33)
        self.assertEqual(
            manual.resolution_method,
            "COMMISSIONER_MANUAL",
        )




from draftboard.domain.nffl_new_season_apply import (
    validate_nffl_activation_runtime,
)


class NfflActivationRuntimeTests(unittest.TestCase):

    def test_exact_runtime_identity_passes(self) -> None:
        validate_nffl_activation_runtime(
            staged_season_year=2027,
            staged_league_key="477.l.12345",
            staged_draft_key="nffl_2027_preseason",
            runtime_season_year=2027,
            runtime_league_key="477.l.12345",
            runtime_draft_key="nffl_2027_preseason",
        )

    def test_runtime_year_mismatch_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "SEASON_YEAR",
        ):
            validate_nffl_activation_runtime(
                staged_season_year=2027,
                staged_league_key="477.l.12345",
                staged_draft_key="nffl_2027_preseason",
                runtime_season_year=2026,
                runtime_league_key="477.l.12345",
                runtime_draft_key="nffl_2027_preseason",
            )

    def test_runtime_league_mismatch_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "LEAGUE_KEY",
        ):
            validate_nffl_activation_runtime(
                staged_season_year=2027,
                staged_league_key="477.l.12345",
                staged_draft_key="nffl_2027_preseason",
                runtime_season_year=2027,
                runtime_league_key="wrong.league",
                runtime_draft_key="nffl_2027_preseason",
            )

    def test_runtime_draft_key_mismatch_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "DRAFTBOARD_DRAFT_KEY",
        ):
            validate_nffl_activation_runtime(
                staged_season_year=2027,
                staged_league_key="477.l.12345",
                staged_draft_key="nffl_2027_preseason",
                runtime_season_year=2027,
                runtime_league_key="477.l.12345",
                runtime_draft_key="wrong_draft_key",
            )


if __name__ == "__main__":
    unittest.main()
