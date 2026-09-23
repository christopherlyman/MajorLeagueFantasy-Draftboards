from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from api import main as commercial_main


PROFILE = {
    "league": {
        "league_key": "commercial.generated",
        "season_year": 2027,
        "name": "Atomic Test",
    }
}


def request_with_cookie(
    token: str | None = "valid-session",
) -> Request:
    headers = []

    if token is not None:
        headers.append(
            (
                b"cookie",
                f"mlf_auth={token}".encode("ascii"),
            )
        )

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/leagues",
            "headers": headers,
        }
    )


def setup_request() -> commercial_main.LeagueSetupDraft:
    return commercial_main.LeagueSetupDraft(
        leagueName="Atomic Test",
        sport="Baseball",
        platform="Manual / Other",
        seasonYear=2027,
        managerCount=10,
        leagueModel="Redraft",
        draftMethod="Snake",
    )


class TransactionConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        if exc_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1

        return False


class AuthenticatedLeagueCreationTests(
    unittest.TestCase
):
    def test_authenticated_creation_is_one_transaction(self):
        db = TransactionConnection()

        principal = SimpleNamespace(
            user_id=7,
        )

        save_result = SimpleNamespace(
            created=True,
            league_key="commercial.generated",
            season_year=2027,
            profile_version=1,
        )

        stored = SimpleNamespace(
            league_key="commercial.generated",
            season_year=2027,
            profile_version=1,
            profile=PROFILE,
        )

        role = SimpleNamespace(
            user_id=7,
            league_key="commercial.generated",
            season_year=2027,
            role_code="commissioner",
            active=True,
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=principal,
            ) as auth_mock,
            patch.object(
                commercial_main,
                "normalize_setup",
                return_value=PROFILE,
            ),
            patch.object(
                commercial_main,
                "validate_commercial_league_profile",
            ),
            patch.object(
                commercial_main,
                "save_commercial_league_profile",
                return_value=save_result,
            ) as save_mock,
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=stored,
            ),
            patch.object(
                commercial_main,
                "grant_commercial_commissioner",
                return_value=role,
            ) as grant_mock,
            patch.object(
                commercial_main,
                "summarize_commercial_league_profile",
                return_value={"name": "Atomic Test"},
            ),
        ):
            result = commercial_main.create_league(
                setup_request(),
                request_with_cookie(),
            )

        self.assertTrue(result["created"])
        self.assertEqual(
            result["league_key"],
            "commercial.generated",
        )

        auth_mock.assert_called_once_with(
            db,
            request=auth_mock.call_args.kwargs[
                "request"
            ],
        )

        self.assertEqual(
            save_mock.call_args.kwargs[
                "changed_by"
            ],
            "auth_user:7",
        )

        self.assertFalse(
            save_mock.call_args.kwargs[
                "manage_transaction"
            ]
        )

        self.assertEqual(
            grant_mock.call_args.kwargs[
                "user_id"
            ],
            7,
        )

        self.assertFalse(
            grant_mock.call_args.kwargs[
                "manage_transaction"
            ]
        )

        self.assertEqual(db.commits, 1)
        self.assertEqual(db.rollbacks, 0)

    def test_commissioner_failure_rolls_back_profile(self):
        db = TransactionConnection()

        principal = SimpleNamespace(
            user_id=7,
        )

        save_result = SimpleNamespace(
            created=True,
            league_key="commercial.generated",
            season_year=2027,
            profile_version=1,
        )

        stored = SimpleNamespace(
            league_key="commercial.generated",
            season_year=2027,
            profile_version=1,
            profile=PROFILE,
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=principal,
            ),
            patch.object(
                commercial_main,
                "normalize_setup",
                return_value=PROFILE,
            ),
            patch.object(
                commercial_main,
                "validate_commercial_league_profile",
            ),
            patch.object(
                commercial_main,
                "save_commercial_league_profile",
                return_value=save_result,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=stored,
            ),
            patch.object(
                commercial_main,
                "grant_commercial_commissioner",
                side_effect=(
                    commercial_main
                    .CommercialAuthorizationRepositoryError(
                        "forced grant failure"
                    )
                ),
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                commercial_main.create_league(
                    setup_request(),
                    request_with_cookie(),
                )

        self.assertEqual(
            caught.exception.status_code,
            503,
        )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_authentication_happens_before_persistence(self):
        db = TransactionConnection()

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "normalize_setup",
                return_value=PROFILE,
            ),
            patch.object(
                commercial_main,
                "validate_commercial_league_profile",
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                side_effect=HTTPException(
                    status_code=401,
                    detail="Authentication required.",
                ),
            ),
            patch.object(
                commercial_main,
                "save_commercial_league_profile",
            ) as save_mock,
            patch.object(
                commercial_main,
                "grant_commercial_commissioner",
            ) as grant_mock,
        ):
            with self.assertRaises(HTTPException) as caught:
                commercial_main.create_league(
                    setup_request(),
                    request_with_cookie(None),
                )

        self.assertEqual(
            caught.exception.status_code,
            401,
        )

        save_mock.assert_not_called()
        grant_mock.assert_not_called()

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)

    def test_ownership_mismatch_rolls_back(self):
        db = TransactionConnection()

        principal = SimpleNamespace(
            user_id=7,
        )

        save_result = SimpleNamespace(
            created=True,
            league_key="commercial.generated",
            season_year=2027,
            profile_version=1,
        )

        stored = SimpleNamespace(
            league_key="commercial.generated",
            season_year=2027,
            profile_version=1,
            profile=PROFILE,
        )

        wrong_role = SimpleNamespace(
            user_id=999,
            league_key="commercial.generated",
            season_year=2027,
            role_code="commissioner",
            active=True,
        )

        with (
            patch.object(
                commercial_main,
                "database_connection",
                return_value=db,
            ),
            patch.object(
                commercial_main,
                "require_commercial_principal",
                return_value=principal,
            ),
            patch.object(
                commercial_main,
                "normalize_setup",
                return_value=PROFILE,
            ),
            patch.object(
                commercial_main,
                "validate_commercial_league_profile",
            ),
            patch.object(
                commercial_main,
                "save_commercial_league_profile",
                return_value=save_result,
            ),
            patch.object(
                commercial_main,
                "load_commercial_league_profile",
                return_value=stored,
            ),
            patch.object(
                commercial_main,
                "grant_commercial_commissioner",
                return_value=wrong_role,
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                commercial_main.create_league(
                    setup_request(),
                    request_with_cookie(),
                )

        self.assertEqual(
            caught.exception.status_code,
            503,
        )

        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
