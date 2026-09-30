from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from mlf_api import main
from mlf_api.models import (
    CommissionerQOUpdateRequest,
)
from mlf_api.qo_store import (
    QOConflict,
    QORequestError,
    _validate_player_keys,
)


def request(
    *,
    method: str = "GET",
    path: str = (
        "/gateway/commissioner/"
        "qualifying-offers"
    ),
) -> Request:
    headers: list[
        tuple[bytes, bytes]
    ] = []

    if method == "POST":
        headers = [
            (
                b"content-type",
                b"application/json",
            ),
            (
                b"origin",
                b"https://mlf.majorleaguefantasy.app",
            ),
        ]

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path":
                path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "server": (
                "mlf.majorleaguefantasy.app",
                443,
            ),
            "client": (
                "127.0.0.1",
                12345,
            ),
        }
    )


def state(
    *,
    can_edit: bool,
) -> dict[str, object]:
    return {
        "draft_key":
            "mlf_2026_preseason",
        "draft_status":
            "complete",
        "selection_count":
            0 if can_edit else 256,
        "qo_rounds":
            5,
        "baseline_synced":
            True,
        "can_edit":
            can_edit,
        "lock_reason": (
            None
            if can_edit
            else (
                "Predraft qualifying offers "
                "are locked."
            )
        ),
        "predraft_count":
            80,
        "current_count":
            24,
        "teams": [
            {
                "team_key":
                    "team.a",
                "team_name":
                    "Team A",
                "predraft": [
                    "p1",
                    "p2",
                    "p3",
                    "p4",
                    "p5",
                ],
                "current": [
                    "p1",
                    None,
                    None,
                    None,
                    None,
                ],
            }
        ],
        "players": [
            {
                "yahoo_player_key":
                    f"p{i}",
                "name":
                    f"Player {i}",
                "rank_value":
                    float(i),
                "predraft_qo_team_key":
                    "team.a",
            }
            for i in range(1, 6)
        ],
    }


class CommissionerQOTests(
    unittest.TestCase
):
    def test_validate_requires_five_unique_players(
        self,
    ) -> None:
        with self.assertRaises(
            QORequestError
        ):
            _validate_player_keys(
                [
                    "p1",
                    "p2",
                ]
            )

        with self.assertRaises(
            QORequestError
        ):
            _validate_player_keys(
                [
                    "p1",
                    "p1",
                    "p2",
                    "p3",
                    "p4",
                ]
            )

    def test_get_requires_workspace(
        self,
    ) -> None:
        with patch.object(
            main,
            "_require_commissioner_workspace",
            side_effect=HTTPException(
                status_code=403,
                detail={
                    "code":
                        "commissioner_workspace_required"
                },
            ),
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_qualifying_offers(
                    request()
                )

        self.assertEqual(
            raised.exception.status_code,
            403,
        )

    def test_get_returns_state(
        self,
    ) -> None:
        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role":
                        "commissioner"
                },
            ),
            patch.object(
                main,
                "get_commissioner_qo_state",
                return_value=state(
                    can_edit=False
                ),
            ),
        ):
            result = (
                main.commissioner_qualifying_offers(
                    request()
                )
            )

        self.assertFalse(
            result.can_edit
        )

        self.assertEqual(
            result.predraft_count,
            80,
        )

    def test_post_requires_write_identity(
        self,
    ) -> None:
        payload = (
            CommissionerQOUpdateRequest(
                player_keys=[
                    "p1",
                    "p2",
                    "p3",
                    "p4",
                    "p5",
                ]
            )
        )

        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role":
                        "commissioner"
                },
            ),
            patch.object(
                main,
                "_require_commissioner_write_principal",
                side_effect=HTTPException(
                    status_code=401,
                    detail={
                        "code":
                            "authentication_required"
                    },
                ),
            ),
            patch.object(
                main,
                "save_commissioner_qos",
            ) as save,
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_qualifying_offers_update(
                    "team.a",
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "qualifying-offers/"
                            "team.a"
                        ),
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            401,
        )

        save.assert_not_called()

    def test_locked_qos_return_409(
        self,
    ) -> None:
        payload = (
            CommissionerQOUpdateRequest(
                player_keys=[
                    "p1",
                    "p2",
                    "p3",
                    "p4",
                    "p5",
                ]
            )
        )

        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role":
                        "commissioner"
                },
            ),
            patch.object(
                main,
                "_require_commissioner_write_principal",
                return_value={
                    "user_id": 7,
                    "email":
                        "commissioner@example.com",
                },
            ),
            patch.object(
                main,
                "save_commissioner_qos",
                side_effect=QOConflict(
                    "locked"
                ),
            ),
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_qualifying_offers_update(
                    "team.a",
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "qualifying-offers/"
                            "team.a"
                        ),
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )

    def test_future_predraft_save_delegates_once(
        self,
    ) -> None:
        payload = (
            CommissionerQOUpdateRequest(
                player_keys=[
                    "p1",
                    "p2",
                    "p3",
                    "p4",
                    "p5",
                ]
            )
        )

        service_result = {
            "updated_team_key":
                "team.a",
            "current_qo_count":
                80,
            "state":
                state(
                    can_edit=True
                ),
        }

        with (
            patch.object(
                main,
                "_require_commissioner_workspace",
                return_value={
                    "role":
                        "commissioner"
                },
            ),
            patch.object(
                main,
                "_require_commissioner_write_principal",
                return_value={
                    "user_id": 7,
                    "email":
                        "commissioner@example.com",
                },
            ),
            patch.object(
                main,
                "save_commissioner_qos",
                return_value=service_result,
            ) as save,
        ):
            result = (
                main.commissioner_qualifying_offers_update(
                    "team.a",
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "qualifying-offers/"
                            "team.a"
                        ),
                    ),
                )
            )

        save.assert_called_once_with(
            team_key="team.a",
            player_keys=[
                "p1",
                "p2",
                "p3",
                "p4",
                "p5",
            ],
            created_by=(
                "commissioner_api:user:7"
            ),
        )

        self.assertEqual(
            result.updated_team_key,
            "team.a",
        )

        self.assertEqual(
            result.performed_by,
            "commissioner@example.com",
        )


if __name__ == "__main__":
    unittest.main()
