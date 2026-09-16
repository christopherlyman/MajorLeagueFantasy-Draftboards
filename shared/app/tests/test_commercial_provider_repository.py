from __future__ import annotations

import unittest

from draftboard.state.commercial_provider_repository import (
    CommercialProviderRepositoryError,
    bind_provider_league,
    create_provider_connection,
    update_provider_connection,
)


CONNECTION_ROW_PENDING = (
    101,
    7,
    "yahoo",
    None,
    None,
    "pending",
    "created",
    "updated",
    None,
    None,
)

CONNECTION_ROW_ACTIVE = (
    101,
    7,
    "yahoo",
    "acct-1",
    "Yahoo User",
    "active",
    "created",
    "updated",
    "verified",
    None,
)

BINDING_ROW = (
    201,
    101,
    "yahoo",
    "commercial.test",
    2027,
    "469.l.41640",
    "469",
    "Test Yahoo League",
    "created",
    "updated",
    None,
)


class ScriptedCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append(
            (" ".join(str(sql).split()), params)
        )

        if not self.connection.responses:
            raise AssertionError(
                "Unexpected SQL execution with no scripted response."
            )

        self.current = self.connection.responses.pop(0)

    def fetchone(self):
        if self.current is None:
            raise AssertionError("fetchone called before execute.")

        return self.current.get("one")


class ScriptedConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return ScriptedCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class CommercialProviderRepositoryTests(unittest.TestCase):
    def test_create_pending_connection_for_active_user(self):
        db = ScriptedConnection(
            [
                {"one": (1,)},
                {"one": CONNECTION_ROW_PENDING},
            ]
        )

        stored = create_provider_connection(
            db,
            user_id=7,
            provider_code="Yahoo",
        )

        self.assertEqual(stored.provider_connection_id, 101)
        self.assertEqual(stored.user_id, 7)
        self.assertEqual(stored.provider_code, "yahoo")
        self.assertEqual(stored.status, "pending")
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(len(db.responses), 0)

    def test_create_connection_rejects_missing_active_user(self):
        db = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        with self.assertRaises(
            CommercialProviderRepositoryError
        ):
            create_provider_connection(
                db,
                user_id=999,
                provider_code="yahoo",
            )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_update_connection_is_scoped_to_owner(self):
        db = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        with self.assertRaises(
            CommercialProviderRepositoryError
        ):
            update_provider_connection(
                db,
                user_id=8,
                provider_connection_id=101,
                status="active",
                external_account_id="acct-1",
            )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_bind_active_yahoo_connection_to_yahoo_profile(self):
        profile_yaml = """
league:
  league_key: commercial.test
  name: Test League
  platform: yahoo
  sport: baseball
  league_model: contract_keeper
  season_year: 2027
  manager_count: 12
"""

        db = ScriptedConnection(
            [
                {"one": CONNECTION_ROW_ACTIVE},
                {"one": (profile_yaml, True)},
                {"one": None},
                {"one": BINDING_ROW},
            ]
        )

        stored = bind_provider_league(
            db,
            user_id=7,
            provider_connection_id=101,
            league_key="commercial.test",
            season_year=2027,
            provider_league_id="469.l.41640",
            provider_game_id="469",
            provider_league_name="Test Yahoo League",
        )

        self.assertEqual(
            stored.provider_league_binding_id,
            201,
        )
        self.assertEqual(stored.provider_code, "yahoo")
        self.assertEqual(
            stored.provider_league_id,
            "469.l.41640",
        )
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(len(db.responses), 0)

    def test_bind_rejects_platform_mismatch(self):
        profile_yaml = """
league:
  league_key: commercial.test
  name: Test League
  platform: espn
  sport: baseball
  league_model: contract_keeper
  season_year: 2027
  manager_count: 12
"""

        db = ScriptedConnection(
            [
                {"one": CONNECTION_ROW_ACTIVE},
                {"one": (profile_yaml, True)},
            ]
        )

        with self.assertRaisesRegex(
            CommercialProviderRepositoryError,
            "platform does not match",
        ):
            bind_provider_league(
                db,
                user_id=7,
                provider_connection_id=101,
                league_key="commercial.test",
                season_year=2027,
                provider_league_id="469.l.41640",
            )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_exact_existing_binding_is_idempotent(self):
        profile_yaml = """
league:
  league_key: commercial.test
  name: Test League
  platform: yahoo
  sport: baseball
  league_model: contract_keeper
  season_year: 2027
  manager_count: 12
"""

        db = ScriptedConnection(
            [
                {"one": CONNECTION_ROW_ACTIVE},
                {"one": (profile_yaml, True)},
                {"one": BINDING_ROW},
            ]
        )

        stored = bind_provider_league(
            db,
            user_id=7,
            provider_connection_id=101,
            league_key="commercial.test",
            season_year=2027,
            provider_league_id="469.l.41640",
        )

        self.assertEqual(
            stored.provider_league_binding_id,
            201,
        )
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(len(db.responses), 0)


if __name__ == "__main__":
    unittest.main()
