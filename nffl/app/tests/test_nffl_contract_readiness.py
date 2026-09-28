from __future__ import annotations

import unittest
from unittest.mock import patch

from draftboard.ui.components import commissioner_tools


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.executed.append(
            (sql, params)
        )

    def fetchall(self):
        if len(self.executed) == 1:
            return [
                (
                    "contract_status",
                    "active",
                    89,
                ),
                (
                    "current_invalid_active_contracts",
                    "invalid_active_contracts",
                    0,
                ),
            ]

        if len(self.executed) == 2:
            return []

        raise AssertionError(
            "Unexpected fetchall call."
        )


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_instance


class NfflContractReadinessTests(
    unittest.TestCase
):
    def test_snapshot_reconciliation_only_checks_snapshot_backed_contracts(
        self,
    ) -> None:
        conn = _FakeConnection()

        with patch.object(
            commissioner_tools.psycopg,
            "connect",
            return_value=conn,
        ):
            result = (
                commissioner_tools
                ._load_nffl_contract_readiness(
                    dsn="postgresql://test",
                    league_key="470.l.84346",
                    season_year=2026,
                )
            )

        self.assertEqual(
            len(conn.cursor_instance.executed),
            2,
        )

        readiness_sql = (
            conn.cursor_instance
            .executed[0][0]
        )

        normalized = " ".join(
            readiness_sql.split()
        )

        self.assertIn(
            "c.status='active' "
            "AND c.source_snapshot_id IS NOT NULL",
            normalized,
        )

        self.assertNotIn(
            "post_draft_contract_submission",
            readiness_sql,
        )

        self.assertEqual(
            result["counts"][
                "current_invalid_active_contracts:"
                "invalid_active_contracts"
            ],
            0,
        )

    def test_readiness_result_shape_is_preserved(
        self,
    ) -> None:
        conn = _FakeConnection()

        with patch.object(
            commissioner_tools.psycopg,
            "connect",
            return_value=conn,
        ):
            result = (
                commissioner_tools
                ._load_nffl_contract_readiness(
                    dsn="postgresql://test",
                    league_key="470.l.84346",
                    season_year=2026,
                )
            )

        self.assertEqual(
            result["counts"][
                "contract_status:active"
            ],
            89,
        )

        self.assertEqual(
            result["blockers"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
