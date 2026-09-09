from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from draftboard.state import commercial_league_profile_repository as repo


_MISSING = object()


def _profile(name: str = "Example") -> dict:
    return {
        "league": {
            "league_key": "commercial.test",
            "season_year": 2027,
            "name": name,
        }
    }


def _call_save(connection, profile, expected=_MISSING):
    values = {
        "connection": connection,
        "conn": connection,
        "profile": profile,
        "actor": "unit-test",
        "changed_by": "unit-test",
        "updated_by": "unit-test",
        "notes": "test save",
        "expected_profile_version": expected,
    }
    kwargs = {}
    for name, parameter in inspect.signature(
        repo.save_commercial_league_profile
    ).parameters.items():
        if name not in values:
            if parameter.default is inspect.Parameter.empty:
                raise AssertionError(f"Unhandled required save parameter: {name}")
            continue
        if name == "expected_profile_version" and expected is _MISSING:
            continue
        kwargs[name] = values[name]
    return repo.save_commercial_league_profile(**kwargs)


def _call_load(connection):
    values = {
        "connection": connection,
        "conn": connection,
        "league_key": "commercial.test",
        "season_year": 2027,
    }
    kwargs = {}
    for name, parameter in inspect.signature(
        repo.load_commercial_league_profile
    ).parameters.items():
        if name not in values:
            if parameter.default is inspect.Parameter.empty:
                raise AssertionError(f"Unhandled required load parameter: {name}")
            continue
        kwargs[name] = values[name]
    return repo.load_commercial_league_profile(**kwargs)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.connection.calls.append((normalized, params))

        if (
            self.connection.fail_contains
            and self.connection.fail_contains in normalized
        ):
            raise RuntimeError("forced database failure")

        if normalized.startswith("SELECT"):
            if self.connection.select_rows:
                self._row = self.connection.select_rows.pop(0)
            else:
                self._row = None

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, select_rows=None, fail_contains=None):
        self.select_rows = list(select_rows or [])
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
    return [call for call in connection.calls if fragment in call[0]]


class CommercialLeagueProfileRepositoryTests(unittest.TestCase):
    def test_initial_create_starts_at_version_one(self):
        connection = FakeConnection(select_rows=[None])
        with patch.object(
            repo,
            "serialize_commercial_league_profile",
            return_value="profile: new\\n",
        ):
            result = _call_save(connection, _profile())

        self.assertTrue(result.created)
        self.assertTrue(result.changed)
        self.assertEqual(result.profile_version, 1)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(
            len(_sql_calls(connection, "INSERT INTO public.league_profile (")),
            1,
        )
        self.assertEqual(
            len(_sql_calls(connection, "league_profile_history")),
            0,
        )

    def test_update_archives_prior_snapshot_and_increments_version(self):
        old_profile = _profile("Old")
        connection = FakeConnection(select_rows=[(3, "profile: old\\n")])

        with patch.object(
            repo,
            "serialize_commercial_league_profile",
            return_value="profile: new\\n",
        ), patch.object(
            repo,
            "parse_commercial_league_profile_yaml",
            return_value=old_profile,
        ):
            result = _call_save(
                connection,
                _profile("New"),
                expected=3,
            )

        self.assertFalse(result.created)
        self.assertTrue(result.changed)
        self.assertEqual(result.profile_version, 4)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(
            len(_sql_calls(connection, "league_profile_history")),
            1,
        )
        self.assertEqual(
            len(_sql_calls(connection, "UPDATE public.league_profile")),
            1,
        )

    def test_stale_expected_version_is_rejected(self):
        connection = FakeConnection(select_rows=[(4, "profile: old\\n")])
        with patch.object(repo, "serialize_commercial_league_profile",
                          return_value="profile: new\\n"):
            with self.assertRaises(
                repo.CommercialLeagueProfileVersionConflict
            ):
                _call_save(
                    connection,
                    _profile("New"),
                    expected=3,
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(
            len(_sql_calls(connection, "league_profile_history")),
            0,
        )
        self.assertEqual(
            len(_sql_calls(connection, "UPDATE public.league_profile")),
            0,
        )

    def test_database_failure_rolls_back(self):
        connection = FakeConnection(
            select_rows=[(2, "profile: old\\n")],
            fail_contains="INSERT INTO public.league_profile_history",
        )

        with patch.object(
            repo,
            "serialize_commercial_league_profile",
            return_value="profile: new\\n",
        ), patch.object(
            repo,
            "parse_commercial_league_profile_yaml",
            return_value=_profile("Old"),
        ):
            with self.assertRaises(RuntimeError):
                _call_save(
                    connection,
                    _profile("New"),
                    expected=2,
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(
            len(_sql_calls(connection, "UPDATE public.league_profile")),
            0,
        )


if __name__ == "__main__":
    unittest.main()
