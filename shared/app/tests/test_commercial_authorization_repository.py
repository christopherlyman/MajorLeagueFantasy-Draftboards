from __future__ import annotations

import unittest

from draftboard.state.commercial_authorization_repository import (
    CommercialAuthorizationRepositoryError,
    can_administer_commercial_league,
    grant_commercial_commissioner,
    load_commercial_commissioner_role,
)


ROLE_ROW = (
    7,
    "commercial.test",
    2027,
    "commissioner",
    True,
    "created",
    "updated",
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
        normalized = " ".join(str(sql).split())

        self.connection.executed.append(
            (normalized, params)
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


class CommercialAuthorizationRepositoryTests(unittest.TestCase):
    def test_grant_commissioner_for_active_user_and_profile(self):
        db = ScriptedConnection(
            [
                {"one": (1,)},
                {"one": (True,)},
                {"one": ROLE_ROW},
            ]
        )

        stored = grant_commercial_commissioner(
            db,
            user_id=7,
            league_key="commercial.test",
            season_year=2027,
        )

        self.assertEqual(stored.user_id, 7)
        self.assertEqual(
            stored.league_key,
            "commercial.test",
        )
        self.assertEqual(stored.season_year, 2027)
        self.assertEqual(
            stored.role_code,
            "commissioner",
        )
        self.assertTrue(stored.active)

        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(len(db.responses), 0)

        grant_sql = db.executed[2][0]

        self.assertIn(
            "ON CONFLICT",
            grant_sql,
        )
        self.assertIn(
            "DO UPDATE SET active = true",
            grant_sql,
        )

    def test_grant_rejects_missing_or_inactive_user(self):
        db = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        with self.assertRaisesRegex(
            CommercialAuthorizationRepositoryError,
            "Active auth_user",
        ):
            grant_commercial_commissioner(
                db,
                user_id=999,
                league_key="commercial.test",
                season_year=2027,
            )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_grant_rejects_missing_profile(self):
        db = ScriptedConnection(
            [
                {"one": (1,)},
                {"one": None},
            ]
        )

        with self.assertRaisesRegex(
            CommercialAuthorizationRepositoryError,
            "Active commercial league profile",
        ):
            grant_commercial_commissioner(
                db,
                user_id=7,
                league_key="commercial.missing",
                season_year=2027,
            )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_grant_rejects_inactive_profile(self):
        db = ScriptedConnection(
            [
                {"one": (1,)},
                {"one": (False,)},
            ]
        )

        with self.assertRaisesRegex(
            CommercialAuthorizationRepositoryError,
            "Active commercial league profile",
        ):
            grant_commercial_commissioner(
                db,
                user_id=7,
                league_key="commercial.test",
                season_year=2027,
            )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_load_commissioner_role_is_user_and_league_scoped(self):
        db = ScriptedConnection(
            [
                {"one": ROLE_ROW},
            ]
        )

        stored = load_commercial_commissioner_role(
            db,
            user_id=7,
            league_key="commercial.test",
            season_year=2027,
        )

        self.assertIsNotNone(stored)
        self.assertEqual(stored.user_id, 7)
        self.assertEqual(
            stored.role_code,
            "commissioner",
        )

        sql, params = db.executed[0]

        self.assertIn(
            "role_code = 'commissioner'",
            sql,
        )
        self.assertEqual(
            params,
            (7, "commercial.test", 2027),
        )

    def test_can_administer_requires_complete_active_chain(self):
        allowed = ScriptedConnection(
            [
                {"one": (1,)},
            ]
        )

        denied = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        self.assertTrue(
            can_administer_commercial_league(
                allowed,
                user_id=7,
                league_key="commercial.test",
                season_year=2027,
            )
        )

        self.assertFalse(
            can_administer_commercial_league(
                denied,
                user_id=7,
                league_key="commercial.test",
                season_year=2027,
            )
        )

        sql = allowed.executed[0][0]

        self.assertIn(
            "r.active = true",
            sql,
        )
        self.assertIn(
            "u.active = true",
            sql,
        )
        self.assertIn(
            "p.is_active = true",
            sql,
        )


    def test_caller_managed_transaction_leaves_control_to_caller(self):
        success = ScriptedConnection(
            [
                {"one": (1,)},
                {"one": (True,)},
                {"one": ROLE_ROW},
            ]
        )

        stored = grant_commercial_commissioner(
            success,
            user_id=7,
            league_key="commercial.test",
            season_year=2027,
            manage_transaction=False,
        )

        self.assertEqual(stored.user_id, 7)
        self.assertEqual(success.commits, 0)
        self.assertEqual(success.rollbacks, 0)

        failure = ScriptedConnection(
            [
                {"one": None},
            ]
        )

        with self.assertRaises(
            CommercialAuthorizationRepositoryError
        ):
            grant_commercial_commissioner(
                failure,
                user_id=999,
                league_key="commercial.test",
                season_year=2027,
                manage_transaction=False,
            )

        self.assertEqual(failure.commits, 0)
        self.assertEqual(failure.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
