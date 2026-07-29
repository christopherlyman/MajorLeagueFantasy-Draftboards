from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from draftboard.ui.components import (
    nffl_team_workbench as workbench,
)


TEAM = {
    "league_key": "league-2026",
    "season_year": 2026,
    "team_key": "team-1",
}


class FakeCursor:
    def __init__(
        self,
        rows: list[dict],
    ) -> None:
        self.rows = rows
        self.calls: list[
            tuple[str, object]
        ] = []

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def execute(
        self,
        sql: str,
        params=None,
    ) -> None:
        self.calls.append(
            (
                " ".join(sql.split()),
                params,
            )
        )

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(
        self,
        rows: list[dict],
    ) -> None:
        self.cursor_object = FakeCursor(
            rows
        )

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def cursor(self, row_factory=None):
        return self.cursor_object


class NewContractLoadTest(
    unittest.TestCase
):
    def load(
        self,
        rows: list[dict],
    ) -> tuple[
        dict[int, str],
        int,
        FakeConnection,
    ]:
        connection = FakeConnection(rows)

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            plan, revision = (
                workbench._load_new_contract_plan(
                    "fake-dsn",
                    TEAM,
                )
            )

        return plan, revision, connection

    def test_saved_plan_and_revision_load(
        self,
    ) -> None:
        plan, revision, connection = self.load(
            [
                {
                    "submission_status": "DRAFT",
                    "revision_number": 7,
                    "contract_years": 4,
                    "yahoo_player_key": "p1",
                },
                {
                    "submission_status": "DRAFT",
                    "revision_number": 7,
                    "contract_years": 3,
                    "yahoo_player_key": "p2",
                },
                {
                    "submission_status": "DRAFT",
                    "revision_number": 7,
                    "contract_years": 2,
                    "yahoo_player_key": "p3",
                },
            ]
        )

        self.assertEqual(
            plan,
            {
                4: "p1",
                3: "p2",
                2: "p3",
            },
        )
        self.assertEqual(revision, 7)
        self.assertEqual(
            len(
                connection.cursor_object.calls
            ),
            1,
        )

        sql, params = (
            connection.cursor_object.calls[0]
        )

        self.assertIn(
            "LEFT JOIN "
            "nffl.post_draft_contract_decision",
            sql,
        )
        self.assertEqual(
            params,
            (
                "league-2026",
                2026,
                "team-1",
            ),
        )

    def test_missing_submission_is_empty(
        self,
    ) -> None:
        plan, revision, _ = self.load([])

        self.assertEqual(plan, {})
        self.assertEqual(revision, 0)

    def test_saved_empty_plan_keeps_revision(
        self,
    ) -> None:
        plan, revision, _ = self.load(
            [
                {
                    "submission_status": "DRAFT",
                    "revision_number": 5,
                    "contract_years": None,
                    "yahoo_player_key": None,
                }
            ]
        )

        self.assertEqual(plan, {})
        self.assertEqual(revision, 5)

    def test_duplicate_player_is_rejected(
        self,
    ) -> None:
        connection = FakeConnection(
            [
                {
                    "submission_status": "DRAFT",
                    "revision_number": 3,
                    "contract_years": 4,
                    "yahoo_player_key": "p1",
                },
                {
                    "submission_status": "DRAFT",
                    "revision_number": 3,
                    "contract_years": 3,
                    "yahoo_player_key": "p1",
                },
            ]
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "more than once",
            ):
                workbench._load_new_contract_plan(
                    "fake-dsn",
                    TEAM,
                )

    def test_load_is_wired_to_tool(
        self,
    ) -> None:
        app_root = (
            Path(__file__).resolve().parents[1]
        )

        workbench_source = (
            app_root
            / "app/src/draftboard/ui/components"
            / "nffl_team_workbench.py"
        ).read_text(encoding="utf-8")

        tool_source = (
            app_root
            / "app/src/draftboard/ui/components"
            / "nffl_post_draft_contract_tool.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            workbench_source.count(
                "_load_new_contract_plan("
            ),
            2,
        )
        self.assertEqual(
            workbench_source.count(
                "persisted_plan="
                "loaded_contract_plan"
            ),
            1,
        )
        self.assertIn(
            "persisted_revision: int = 0",
            tool_source,
        )
        self.assertIn(
            "session_revision",
            tool_source,
        )
        self.assertIn(
            "!= database_revision",
            tool_source,
        )


if __name__ == "__main__":
    unittest.main()
