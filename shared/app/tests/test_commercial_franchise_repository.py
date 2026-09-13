from __future__ import annotations

import unittest
from unittest.mock import patch

from draftboard.state import commercial_franchise_repository as repo


def _profile(manager_count: int = 2) -> dict:
    return {
        "league": {
            "league_key": "commercial.test",
            "name": "Test League",
            "platform": "manual",
            "sport": "baseball",
            "league_model": "redraft",
            "season_year": 2027,
            "manager_count": manager_count,
        }
    }


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._row = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.connection.calls.append((normalized, params))
        self._row = None
        self._rows = []

        if (
            self.connection.fail_contains
            and self.connection.fail_contains in normalized
        ):
            raise RuntimeError("forced database failure")

        if (
            normalized.startswith("SELECT profile_yaml")
            and "FROM public.league_profile" in normalized
        ):
            if self.connection.profile_exists:
                self._row = ("profile: test\n",)
            return

        if (
            normalized.startswith("SELECT COUNT(*)")
            and "FROM public.franchise_season_team" in normalized
        ):
            self._row = (self.connection.existing_count,)
            return

        if normalized.startswith(
            "INSERT INTO public.franchise ("
        ):
            franchise_id = self.connection.next_franchise_id
            self.connection.next_franchise_id += 1
            self._row = (franchise_id,)
            return

        if normalized.startswith("SELECT f.franchise_id"):
            self._rows = list(self.connection.load_rows)
            return

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(
        self,
        *,
        profile_exists=True,
        existing_count=0,
        next_franchise_id=101,
        load_rows=None,
        fail_contains=None,
    ):
        self.profile_exists = profile_exists
        self.existing_count = existing_count
        self.next_franchise_id = next_franchise_id
        self.load_rows = list(load_rows or [])
        self.fail_contains = fail_contains

        self.calls = []
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def _sql_calls(connection, fragment):
    return [
        call
        for call in connection.calls
        if fragment in call[0]
    ]


class CommercialFranchiseRepositoryTests(unittest.TestCase):
    def test_initialization_creates_franchises_and_season_mappings(self):
        connection = FakeConnection()

        with patch.object(
            repo,
            "parse_commercial_league_profile_yaml",
            return_value=_profile(2),
        ):
            created = repo.initialize_commercial_league_franchises(
                connection,
                "commercial.test",
                2027,
                [
                    {
                        "team_name": " Alpha ",
                        "owner_name": " Alex ",
                    },
                    {
                        "team_name": "Beta",
                        "owner_name": "",
                    },
                ],
            )

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)

        self.assertEqual(
            [item.franchise_id for item in created],
            [101, 102],
        )
        self.assertEqual(
            [item.team_key for item in created],
            [
                "commercial.test.t.1",
                "commercial.test.t.2",
            ],
        )
        self.assertEqual(created[0].team_name, "Alpha")
        self.assertEqual(created[0].owner_name, "Alex")
        self.assertIsNone(created[1].owner_name)
        self.assertEqual(created[0].source, "manual")

        franchise_inserts = _sql_calls(
            connection,
            "INSERT INTO public.franchise (",
        )
        mapping_inserts = _sql_calls(
            connection,
            "INSERT INTO public.franchise_season_team (",
        )

        self.assertEqual(len(franchise_inserts), 2)
        self.assertEqual(len(mapping_inserts), 2)

        self.assertEqual(
            mapping_inserts[0][1],
            (
                101,
                2027,
                "commercial.test",
                "commercial.test.t.1",
                "Alpha",
                "Alex",
                "manual",
            ),
        )

        self.assertEqual(
            mapping_inserts[1][1],
            (
                102,
                2027,
                "commercial.test",
                "commercial.test.t.2",
                "Beta",
                None,
                "manual",
            ),
        )

    def test_count_must_equal_profile_manager_count(self):
        connection = FakeConnection()

        with patch.object(
            repo,
            "parse_commercial_league_profile_yaml",
            return_value=_profile(3),
        ):
            with self.assertRaises(
                repo.CommercialFranchiseRepositoryError
            ):
                repo.initialize_commercial_league_franchises(
                    connection,
                    "commercial.test",
                    2027,
                    [
                        {"team_name": "Alpha"},
                        {"team_name": "Beta"},
                    ],
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(
            len(
                _sql_calls(
                    connection,
                    "INSERT INTO public.franchise (",
                )
            ),
            0,
        )

    def test_existing_season_mappings_block_reinitialization(self):
        connection = FakeConnection(existing_count=2)

        with patch.object(
            repo,
            "parse_commercial_league_profile_yaml",
            return_value=_profile(2),
        ):
            with self.assertRaisesRegex(
                repo.CommercialFranchiseRepositoryError,
                "already initialized",
            ):
                repo.initialize_commercial_league_franchises(
                    connection,
                    "commercial.test",
                    2027,
                    [
                        {"team_name": "Alpha"},
                        {"team_name": "Beta"},
                    ],
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)

    def test_missing_profile_is_rejected(self):
        connection = FakeConnection(profile_exists=False)

        with self.assertRaisesRegex(
            repo.CommercialFranchiseRepositoryError,
            "No active commercial league profile",
        ):
            repo.initialize_commercial_league_franchises(
                connection,
                "commercial.test",
                2027,
                [
                    {"team_name": "Alpha"},
                    {"team_name": "Beta"},
                ],
            )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)

    def test_blank_team_name_is_rejected_before_database_writes(self):
        connection = FakeConnection()

        with self.assertRaisesRegex(
            repo.CommercialFranchiseRepositoryError,
            "team_name must be non-empty",
        ):
            repo.initialize_commercial_league_franchises(
                connection,
                "commercial.test",
                2027,
                [
                    {"team_name": "Alpha"},
                    {"team_name": "   "},
                ],
            )

        self.assertEqual(connection.calls, [])
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 0)

    def test_database_failure_rolls_back_all_repository_writes(self):
        connection = FakeConnection(
            fail_contains=(
                "INSERT INTO public.franchise_season_team ("
            )
        )

        with patch.object(
            repo,
            "parse_commercial_league_profile_yaml",
            return_value=_profile(2),
        ):
            with self.assertRaises(RuntimeError):
                repo.initialize_commercial_league_franchises(
                    connection,
                    "commercial.test",
                    2027,
                    [
                        {"team_name": "Alpha"},
                        {"team_name": "Beta"},
                    ],
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)

    def test_load_returns_canonical_franchise_records(self):
        connection = FakeConnection(
            load_rows=[
                (
                    101,
                    "Alpha",
                    "commercial.test",
                    2027,
                    "commercial.test.t.1",
                    "Alpha",
                    "Alex",
                    "manual",
                ),
                (
                    102,
                    "Beta",
                    "commercial.test",
                    2027,
                    "commercial.test.t.2",
                    "Beta",
                    None,
                    "manual",
                ),
            ]
        )

        loaded = repo.load_commercial_league_franchises(
            connection,
            "commercial.test",
            2027,
        )

        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].franchise_id, 101)
        self.assertEqual(
            loaded[0].team_key,
            "commercial.test.t.1",
        )
        self.assertEqual(loaded[0].owner_name, "Alex")
        self.assertIsNone(loaded[1].owner_name)
        self.assertEqual(loaded[1].source, "manual")


if __name__ == "__main__":
    unittest.main()
