from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path


if "streamlit" not in sys.modules:
    sys.modules[
        "streamlit"
    ] = types.ModuleType(
        "streamlit"
    )


from draftboard.ui.components.nffl_post_draft_contract_tool import (
    _available_player_keys,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

WORKBENCH_PATH = (
    PROJECT_ROOT
    / "app"
    / "src"
    / "draftboard"
    / "ui"
    / "components"
    / "nffl_team_workbench.py"
)

TOOL_PATH = (
    PROJECT_ROOT
    / "app"
    / "src"
    / "draftboard"
    / "ui"
    / "components"
    / "nffl_post_draft_contract_tool.py"
)


class PostDraftContractToolSourceTest(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workbench = WORKBENCH_PATH.read_text(
            encoding="utf-8"
        )

        cls.tool = TOOL_PATH.read_text(
            encoding="utf-8"
        )

    def test_sources_are_valid_python(self) -> None:
        ast.parse(
            self.workbench,
            filename=str(WORKBENCH_PATH),
        )

        ast.parse(
            self.tool,
            filename=str(TOOL_PATH),
        )

    def test_locked_ft_requires_locked_status(
        self,
    ) -> None:
        self.assertIn(
            'decision_type == "FT"',
            self.workbench,
        )

        self.assertIn(
            'decision_status == "LOCKED"',
            self.workbench,
        )

        self.assertIn(
            "locked_ft_team_keys.add(team_key)",
            self.workbench,
        )

    def test_tool_receives_explicit_ft_state(
        self,
    ) -> None:
        self.assertIn(
            "has_locked_ft: bool",
            self.tool,
        )

        self.assertEqual(
            self.workbench.count(
                "has_locked_ft=has_locked_ft"
            ),
            2,
        )

        self.assertNotIn(
            "has_locked_ft = any(",
            self.tool,
        )

    def test_drafted_player_filter_is_preserved(
        self,
    ) -> None:
        self.assertIn(
            'row.get("roster_source")',
            self.tool,
        )

        self.assertIn(
            '!= "DRAFTED"',
            self.tool,
        )

        self.assertIn(
            "ELIGIBLE_PICK_KINDS",
            self.tool,
        )

    def test_sample_data_is_absent(
        self,
    ) -> None:
        for forbidden in (
            "_sample_players",
            "Sample Quarterback",
            "Sample Running Back",
            "Sample Wide Receiver",
            "Sample Tight End",
            "preview-qb",
            "preview-rb",
            "preview-wr",
            "preview-te",
            "Sample players",
        ):
            self.assertNotIn(
                forbidden,
                self.tool,
            )

        self.assertIn(
            "will populate as this team makes",
            self.tool,
        )

    def test_selected_player_is_removed_from_other_slots(
        self,
    ) -> None:
        player_keys = [
            "player-a",
            "player-b",
            "player-c",
        ]

        selections = {
            4: "player-a",
            3: "",
            2: "player-c",
        }

        self.assertEqual(
            _available_player_keys(
                all_player_keys=player_keys,
                selections_by_year=selections,
                current_year=3,
            ),
            ["player-b"],
        )

        self.assertEqual(
            _available_player_keys(
                all_player_keys=player_keys,
                selections_by_year=selections,
                current_year=4,
            ),
            [
                "player-a",
                "player-b",
            ],
        )

    def test_cleared_player_returns_to_other_slots(
        self,
    ) -> None:
        player_keys = [
            "player-a",
            "player-b",
        ]

        selections = {
            4: "",
            3: "",
            2: "",
        }

        for years in (4, 3, 2):
            self.assertEqual(
                _available_player_keys(
                    all_player_keys=player_keys,
                    selections_by_year=selections,
                    current_year=years,
                ),
                player_keys,
            )

    def test_empty_dropdowns_are_disabled(
        self,
    ) -> None:
        self.assertIn(
            "disabled=not bool(",
            self.tool,
        )

    def test_commissioner_can_review_before_reveal(
        self,
    ) -> None:
        self.assertIn(
            "and not qoft_revealed",
            self.workbench,
        )

        self.assertIn(
            "Commissioner Design Review",
            self.workbench,
        )

    def test_post_reveal_replacement_is_preserved(
        self,
    ) -> None:
        self.assertIn(
            "and qoft_revealed",
            self.workbench,
        )

        self.assertIn(
            "elif can_manage_qoft:",
            self.workbench,
        )

    def test_tool_has_no_database_access(
        self,
    ) -> None:
        for forbidden in (
            "psycopg",
            "INSERT INTO",
            "UPDATE nffl.",
            "DELETE FROM",
        ):
            self.assertNotIn(
                forbidden,
                self.tool,
            )

    def test_second_app_artifacts_are_absent(
        self,
    ) -> None:
        self.assertNotIn(
            "NFFL_POST_DRAFT_CONTRACT_PREVIEW",
            self.tool,
        )

        self.assertNotIn(
            "post_draft_contract_preview",
            self.workbench,
        )


if __name__ == "__main__":
    unittest.main()
