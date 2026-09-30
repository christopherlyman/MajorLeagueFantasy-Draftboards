from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from mlf_api import main
from mlf_api.models import (
    CommissionerTradeAssetRequest,
    CommissionerTradeRequest,
)
from mlf_api.trade_store import (
    TradeConflict,
    TradeRequestError,
    _build_trade_assets,
)


def request(
    *,
    method: str = "GET",
    path: str = (
        "/gateway/commissioner/trade-builder"
    ),
) -> Request:
    headers: list[tuple[bytes, bytes]] = []

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
            "raw_path": path.encode(
                "ascii"
            ),
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


class CommissionerTradeTests(
    unittest.TestCase
):
    def test_asset_builder_uses_canonical_contract_years(
        self,
    ) -> None:
        rows = _build_trade_assets(
            team_a_key="team.a",
            team_b_key="team.b",
            team_a_gets=[
                {
                    "asset_type": "PLAYER",
                    "asset_id": "player.1",
                },
                {
                    "asset_type": "PICK",
                    "asset_id": "R07-02",
                },
            ],
            team_b_gets=[],
            valid_teams={
                "team.a",
                "team.b",
            },
            valid_players={
                "player.1",
            },
            contracts={
                "player.1": {
                    "team_key": "team.b",
                    "years_remaining": 3,
                }
            },
        )

        self.assertEqual(
            rows[0]["snapshot"],
            {
                "contract_years": 3
            },
        )

        self.assertEqual(
            rows[0]["from_team_key"],
            "team.b",
        )

        self.assertEqual(
            rows[0]["to_team_key"],
            "team.a",
        )

        self.assertEqual(
            rows[1]["snapshot"],
            {},
        )

    def test_non_contract_player_is_receipt_only_shape(
        self,
    ) -> None:
        rows = _build_trade_assets(
            team_a_key="team.a",
            team_b_key="team.b",
            team_a_gets=[
                {
                    "asset_type": "PLAYER",
                    "asset_id": "player.2",
                }
            ],
            team_b_gets=[],
            valid_teams={
                "team.a",
                "team.b",
            },
            valid_players={
                "player.2",
            },
            contracts={},
        )

        self.assertEqual(
            rows[0]["snapshot"],
            {
                "contract_years": 0
            },
        )

    def test_duplicate_asset_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            TradeRequestError
        ):
            _build_trade_assets(
                team_a_key="team.a",
                team_b_key="team.b",
                team_a_gets=[
                    {
                        "asset_type": "PICK",
                        "asset_id": "R07-01",
                    }
                ],
                team_b_gets=[
                    {
                        "asset_type": "PICK",
                        "asset_id": "R07-01",
                    }
                ],
                valid_teams={
                    "team.a",
                    "team.b",
                },
                valid_players=set(),
                contracts={},
            )

    def test_state_requires_workspace(
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
                main.commissioner_trade_builder_state(
                    request()
                )

        self.assertEqual(
            raised.exception.status_code,
            403,
        )

    def test_state_returns_options(
        self,
    ) -> None:
        state = {
            "draft_key":
                "mlf_2026_preseason",
            "draft_status":
                "complete",
            "selection_count":
                256,
            "teams": [
                {
                    "team_key": "team.a",
                    "team_name": "Team A",
                },
                {
                    "team_key": "team.b",
                    "team_name": "Team B",
                },
            ],
            "players": [],
            "picks": [],
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
                "get_trade_builder_state",
                return_value=state,
            ),
        ):
            result = (
                main.commissioner_trade_builder_state(
                    request()
                )
            )

        self.assertEqual(
            result.selection_count,
            256,
        )

        self.assertEqual(
            len(result.teams),
            2,
        )

    def test_submit_requires_write_identity(
        self,
    ) -> None:
        payload = CommissionerTradeRequest(
            team_a_key="team.a",
            team_b_key="team.b",
            team_a_gets=[
                CommissionerTradeAssetRequest(
                    asset_type="PLAYER",
                    asset_id="player.1",
                )
            ],
            team_b_gets=[],
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
                "submit_commissioner_trade",
            ) as submit,
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_trade_builder_submit(
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "trade-builder/submit"
                        ),
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            401,
        )

        submit.assert_not_called()

    def test_submit_delegates_once(
        self,
    ) -> None:
        payload = CommissionerTradeRequest(
            team_a_key="team.a",
            team_b_key="team.b",
            team_a_gets=[
                CommissionerTradeAssetRequest(
                    asset_type="PLAYER",
                    asset_id="player.1",
                )
            ],
            team_b_gets=[
                CommissionerTradeAssetRequest(
                    asset_type="PICK",
                    asset_id="R07-01",
                )
            ],
        )

        service_result = {
            "player_updates": 1,
            "pick_updates": 1,
            "keeper_assignments": 4,
            "receipt_written": True,
            "receipt_trade_id":
                "receipt-id",
            "receipt_asset_count": 2,
            "receipt_warning": None,
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
                "submit_commissioner_trade",
                return_value=service_result,
            ) as submit,
        ):
            result = (
                main.commissioner_trade_builder_submit(
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "trade-builder/submit"
                        ),
                    ),
                )
            )

        submit.assert_called_once()

        kwargs = (
            submit.call_args.kwargs
        )

        self.assertEqual(
            kwargs["created_by"],
            "api:user:7",
        )

        self.assertEqual(
            result.pick_updates,
            1,
        )

        self.assertEqual(
            result.performed_by,
            "commissioner@example.com",
        )

    def test_conflict_is_409(
        self,
    ) -> None:
        payload = CommissionerTradeRequest(
            team_a_key="team.a",
            team_b_key="team.b",
            team_a_gets=[
                CommissionerTradeAssetRequest(
                    asset_type="PICK",
                    asset_id="R07-01",
                )
            ],
            team_b_gets=[],
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
                "submit_commissioner_trade",
                side_effect=TradeConflict(
                    "conflict"
                ),
            ),
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_trade_builder_submit(
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "trade-builder/submit"
                        ),
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )


if __name__ == "__main__":
    unittest.main()
