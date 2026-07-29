from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from draftboard.ui.components import (
    nffl_team_workbench as workbench,
)


TEAM = {
    "league_key": "league-2026",
    "season_year": 2026,
    "team_key": "team-1",
}


def base_scenario() -> dict:
    return {
        "context": {
            "league_key": "league-2026",
            "season_year": 2026,
            "draft_key": "draft-2026",
        },
        "visibility": {
            "qoft_revealed": True,
            "post_draft_contracts_revealed": False,
        },
        "submission": {
            "draft_key": "draft-2026",
            "submission_status": "DRAFT",
            "revision_number": 2,
        },
        "draft_rows": [
            {
                "pick_id": "QO1-01",
                "yahoo_player_key": "p1",
                "pick_kind": "QO",
            },
            {
                "pick_id": "QO2-01",
                "yahoo_player_key": "p2",
                "pick_kind": "POACH",
            },
            {
                "pick_id": "R05-01",
                "yahoo_player_key": "p3",
                "pick_kind": "FA",
            },
            {
                "pick_id": "PT-01",
                "yahoo_player_key": "blocked",
                "pick_kind": "PT",
            },
        ],
        "active_contracts": [],
        "locked_ft": [],
        "fail_on_decision_insert": False,
    }


class FakeCursor:
    def __init__(self, scenario: dict) -> None:
        self.scenario = scenario
        self.calls: list[tuple[str, object]] = []
        self.one = None
        self.all: list[dict] = []

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
        normalized = " ".join(sql.split())

        self.calls.append(
            (
                normalized,
                params,
            )
        )

        self.one = None
        self.all = []

        if (
            self.scenario[
                "fail_on_decision_insert"
            ]
            and (
                "INSERT INTO "
                "nffl.post_draft_contract_decision"
                in normalized
            )
        ):
            raise RuntimeError(
                "simulated decision insert failure"
            )

        if (
            "FROM nffl.v_active_season_context"
            in normalized
        ):
            self.one = self.scenario["context"]

        elif (
            "FROM nffl.league_visibility_state"
            in normalized
        ):
            self.one = self.scenario["visibility"]

        elif (
            "FROM "
            "nffl.post_draft_contract_submission"
            in normalized
            and "FOR UPDATE" in normalized
        ):
            self.one = self.scenario["submission"]

        elif (
            "FROM nffl.draft_selection"
            in normalized
        ):
            self.all = self.scenario["draft_rows"]

        elif (
            "FROM nffl.contract"
            in normalized
        ):
            self.all = self.scenario[
                "active_contracts"
            ]

        elif (
            "FROM "
            "nffl.offseason_keeper_decision"
            in normalized
        ):
            self.all = self.scenario["locked_ft"]

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.all)


class FakeConnection:
    def __init__(self, scenario: dict) -> None:
        self.cursor_object = FakeCursor(
            scenario
        )
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        if exc_type is not None:
            self.rollback_count += 1

        self.close_count += 1
        return False

    def cursor(self, row_factory=None):
        return self.cursor_object

    def commit(self) -> None:
        self.commit_count += 1


class NewContractSaveTest(
    unittest.TestCase
):
    def make_connection(
        self,
        scenario: dict,
    ) -> FakeConnection:
        return FakeConnection(scenario)

    def save(
        self,
        scenario: dict,
        selections: dict[int, str],
    ) -> tuple[FakeConnection, int]:
        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            revision = (
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    selections,
                    decided_by="test-user",
                )
            )

        return connection, revision

    def test_valid_choices_are_saved(
        self,
    ) -> None:
        connection, revision = self.save(
            base_scenario(),
            {
                4: "p1",
                3: "p2",
                2: "p3",
            },
        )

        self.assertEqual(revision, 3)
        self.assertEqual(
            connection.commit_count,
            1,
        )
        self.assertEqual(
            connection.rollback_count,
            0,
        )

        decision_inserts = [
            call
            for call
            in connection.cursor_object.calls
            if (
                "INSERT INTO "
                "nffl.post_draft_contract_decision"
                in call[0]
            )
        ]

        self.assertEqual(
            len(decision_inserts),
            3,
        )

        saved_terms = [
            call[1][4]
            for call in decision_inserts
        ]

        self.assertEqual(
            saved_terms,
            [4, 3, 2],
        )

        audit_calls = [
            call
            for call
            in connection.cursor_object.calls
            if (
                "INSERT INTO "
                "nffl.post_draft_contract_audit"
                in call[0]
            )
        ]

        self.assertEqual(
            len(audit_calls),
            1,
        )

        payload = json.loads(
            audit_calls[0][1][6]
        )

        self.assertEqual(
            [
                row["source_pick_kind"]
                for row in payload
            ],
            [
                "QO",
                "POACH",
                "FA",
            ],
        )

    def test_empty_plan_clears_choices(
        self,
    ) -> None:
        connection, revision = self.save(
            base_scenario(),
            {},
        )

        self.assertEqual(revision, 3)

        delete_calls = [
            call
            for call
            in connection.cursor_object.calls
            if (
                "DELETE FROM "
                "nffl.post_draft_contract_decision"
                in call[0]
            )
        ]

        decision_inserts = [
            call
            for call
            in connection.cursor_object.calls
            if (
                "INSERT INTO "
                "nffl.post_draft_contract_decision"
                in call[0]
            )
        ]

        self.assertEqual(
            len(delete_calls),
            1,
        )
        self.assertEqual(
            len(decision_inserts),
            0,
        )
        self.assertEqual(
            connection.commit_count,
            1,
        )

    def test_qoft_must_be_revealed(
        self,
    ) -> None:
        scenario = base_scenario()
        scenario["visibility"][
            "qoft_revealed"
        ] = False

        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "until QO/FT selections",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )

    def test_revealed_contracts_cannot_change(
        self,
    ) -> None:
        scenario = base_scenario()
        scenario["visibility"][
            "post_draft_contracts_revealed"
        ] = True

        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "finalized and revealed",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )

    def test_published_team_cannot_change(
        self,
    ) -> None:
        scenario = base_scenario()
        scenario["submission"][
            "submission_status"
        ] = "PUBLISHED"

        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "already been published",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )

    def test_locked_ft_blocks_two_year(
        self,
    ) -> None:
        scenario = base_scenario()
        scenario["locked_ft"] = [
            {
                "team_key": "team-1",
                "yahoo_player_key": "ft-1",
            }
        ]

        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "2-year contract slot",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {2: "p3"},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )

    def test_ineligible_pick_is_rejected(
        self,
    ) -> None:
        scenario = base_scenario()
        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "not eligible",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {4: "blocked"},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )

    def test_active_contract_is_rejected(
        self,
    ) -> None:
        scenario = base_scenario()
        scenario["active_contracts"] = [
            {
                "yahoo_player_key": "p1",
            }
        ]

        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "not eligible",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {4: "p1"},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )

    def test_failed_write_rolls_back(
        self,
    ) -> None:
        scenario = base_scenario()
        scenario[
            "fail_on_decision_insert"
        ] = True

        connection = self.make_connection(
            scenario
        )

        with patch.object(
            workbench.psycopg,
            "connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated decision insert",
            ):
                workbench._save_new_contracts(
                    "fake-dsn",
                    TEAM,
                    {4: "p1"},
                )

        self.assertEqual(
            connection.commit_count,
            0,
        )
        self.assertEqual(
            connection.rollback_count,
            1,
        )


if __name__ == "__main__":
    unittest.main()