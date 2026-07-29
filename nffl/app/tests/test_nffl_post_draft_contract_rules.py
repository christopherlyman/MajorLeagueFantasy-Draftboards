from __future__ import annotations

import unittest

from draftboard.domain.nffl_post_draft_contract_rules import (
    allowed_contract_years,
    eligible_drafted_player_keys,
    validate_contract_selections,
)


class PostDraftContractRulesTest(
    unittest.TestCase
):
    def test_team_without_ft_gets_all_slots(
        self,
    ) -> None:
        self.assertEqual(
            allowed_contract_years(False),
            (4, 3, 2),
        )

    def test_team_with_ft_gets_no_two_year_slot(
        self,
    ) -> None:
        self.assertEqual(
            allowed_contract_years(True),
            (4, 3),
        )

    def test_qo_poach_and_fa_are_drafted(
        self,
    ) -> None:
        result = eligible_drafted_player_keys(
            [
                {
                    "yahoo_player_key": "player-qo",
                    "pick_kind": "QO",
                },
                {
                    "yahoo_player_key": "player-poach",
                    "pick_kind": "POACH",
                },
                {
                    "yahoo_player_key": "player-fa",
                    "pick_kind": "FA",
                },
            ],
            set(),
            set(),
        )

        self.assertEqual(
            result,
            {
                "player-qo",
                "player-poach",
                "player-fa",
            },
        )

    def test_contract_and_pt_are_not_newly_drafted(
        self,
    ) -> None:
        result = eligible_drafted_player_keys(
            [
                {
                    "yahoo_player_key":
                        "player-contract",
                    "pick_kind": "CONTRACT",
                },
                {
                    "yahoo_player_key": "player-pt",
                    "pick_kind": "PT",
                },
            ],
            set(),
            set(),
        )

        self.assertEqual(result, set())

    def test_contract_and_ft_players_are_blocked(
        self,
    ) -> None:
        result = eligible_drafted_player_keys(
            [
                {
                    "yahoo_player_key": "contracted",
                    "pick_kind": "FA",
                },
                {
                    "yahoo_player_key": "tagged",
                    "pick_kind": "QO",
                },
                {
                    "yahoo_player_key": "eligible",
                    "pick_kind": "POACH",
                },
            ],
            {
                "contracted",
            },
            {
                "tagged",
            },
        )

        self.assertEqual(
            result,
            {
                "eligible",
            },
        )

    def test_valid_partial_plan(
        self,
    ) -> None:
        result = validate_contract_selections(
            {
                4: "player-1",
                3: "",
                2: "player-2",
            },
            {
                "player-1",
                "player-2",
            },
            False,
        )

        self.assertEqual(
            result,
            {
                4: "player-1",
                2: "player-2",
            },
        )

    def test_ft_team_cannot_use_two_year_slot(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "2-year contract slot is not available",
        ):
            validate_contract_selections(
                {
                    2: "player-1",
                },
                {
                    "player-1",
                },
                True,
            )

    def test_duplicate_player_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "only receive one new contract",
        ):
            validate_contract_selections(
                {
                    4: "player-1",
                    3: "player-1",
                },
                {
                    "player-1",
                },
                False,
            )

    def test_ineligible_player_is_rejected(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "not eligible",
        ):
            validate_contract_selections(
                {
                    4: "player-2",
                },
                {
                    "player-1",
                },
                False,
            )

    def test_empty_plan_is_allowed(
        self,
    ) -> None:
        result = validate_contract_selections(
            {
                4: "",
                3: "",
                2: "",
            },
            set(),
            False,
        )

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
