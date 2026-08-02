from __future__ import annotations

import unittest

from draftboard.domain.live_pick_kind import (
    classify_live_pick_kind,
)


class LivePickKindTest(unittest.TestCase):
    def setUp(self) -> None:
        self.current_qos = {
            "team-a": {
                1: "a-qo1",
                3: "a-qo3",
            },
            "team-b": {
                1: "b-qo1",
                2: "b-qo2",
                4: "b-qo4",
            },
        }

        self.team_names = {
            "team-a": "Alpha",
            "team-b": "Bravo",
        }

    def classify(
        self,
        *,
        current_round: int,
        current_team_key: str,
        chosen_player_key: str,
    ) -> str:
        return classify_live_pick_kind(
            current_round=current_round,
            current_team_key=current_team_key,
            chosen_player_key=chosen_player_key,
            current_qos=self.current_qos,
            active_qo_rounds=4,
            team_name_by_key=self.team_names,
        )

    def test_free_agent_is_fa(self) -> None:
        self.assertEqual(
            self.classify(
                current_round=1,
                current_team_key="team-a",
                chosen_player_key="free-agent",
            ),
            "FA",
        )

    def test_standard_round_player_is_fa(self) -> None:
        self.assertEqual(
            self.classify(
                current_round=5,
                current_team_key="team-a",
                chosen_player_key="b-qo4",
            ),
            "FA",
        )

    def test_team_retains_same_level_qo(self) -> None:
        self.assertEqual(
            self.classify(
                current_round=1,
                current_team_key="team-a",
                chosen_player_key="a-qo1",
            ),
            "QO",
        )

    def test_team_promotes_own_lower_qo(self) -> None:
        self.assertEqual(
            self.classify(
                current_round=1,
                current_team_key="team-a",
                chosen_player_key="a-qo3",
            ),
            "QO",
        )

    def test_team_poaches_eligible_lower_qo(self) -> None:
        self.assertEqual(
            self.classify(
                current_round=1,
                current_team_key="team-a",
                chosen_player_key="b-qo2",
            ),
            "POACH",
        )

    def test_same_round_opposing_qo_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            (
                "Not poach-eligible. "
                "Reserved for Bravo at QO1."
            ),
        ):
            self.classify(
                current_round=1,
                current_team_key="team-a",
                chosen_player_key="b-qo1",
            )

    def test_expired_opposing_qo_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            (
                "Not poach-eligible. "
                "Reserved for Bravo at QO2."
            ),
        ):
            self.classify(
                current_round=3,
                current_team_key="team-a",
                chosen_player_key="b-qo2",
            )


if __name__ == "__main__":
    unittest.main()
