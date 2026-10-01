from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from mlf_api import main
from mlf_api.models import (
    CommissionerProspectUpdateRequest,
)
from mlf_api.prospect_store import (
    ProspectConflict,
    _player_pt_eligibility,
)


def request(
    *,
    method: str = "GET",
    path: str = (
        "/gateway/commissioner/prospect-tags"
    ),
) -> Request:
    headers: list[
        tuple[bytes, bytes]
    ] = []

    if method in {
        "POST",
        "DELETE",
    }:
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


def state() -> dict[str, object]:
    return {
        "draft_key":
            "mlf_2026_preseason",
        "draft_status":
            "complete",
        "selection_count":
            256,
        "mirror_synced":
            True,
        "team_multiplicity_valid":
            True,
        "can_edit":
            True,
        "lock_reason":
            None,
        "prospect_count":
            9,
        "keeper_pt_count":
            9,
        "eligible_count":
            100,
        "teams": [
            {
                "team_key":
                    "team.a",
                "team_name":
                    "Team A",
                "prospect_player_keys": [
                    "player.old",
                ],
            },
            {
                "team_key":
                    "team.b",
                "team_name":
                    "Team B",
                "prospect_player_keys": [],
            },
        ],
        "players": [
            {
                "yahoo_player_key":
                    "player.old",
                "name":
                    "Old Prospect",
                "mlb_team":
                    "AAA",
                "positions":
                    ["SS"],
                "rank_value":
                    50.0,
                "h_ab":
                    "42/100",
                "ip":
                    None,
                "percent_owned":
                    5.0,
                "is_qo_eligible":
                    False,
                "eligible_for_pt":
                    False,
                "ineligibility_reason":
                    "existing_prospect_tag",
                "prospect_team_key":
                    "team.a",
                "prospect_note":
                    None,
            },
            {
                "yahoo_player_key":
                    "player.new",
                "name":
                    "New Prospect",
                "mlb_team":
                    "AA",
                "positions":
                    ["OF"],
                "rank_value":
                    75.0,
                "h_ab":
                    "18/50",
                "ip":
                    None,
                "percent_owned":
                    2.0,
                "is_qo_eligible":
                    False,
                "eligible_for_pt":
                    True,
                "ineligibility_reason":
                    None,
                "prospect_team_key":
                    None,
                "prospect_note":
                    None,
            },
        ],
    }


class CommissionerProspectTests(
    unittest.TestCase
):
    def test_pt_eligibility_excludes_contract(
        self,
    ) -> None:
        eligible, reason = (
            _player_pt_eligibility(
                player_key="p1",
                is_qo_eligible=False,
                contracted={"p1"},
                tagged=set(),
            )
        )

        self.assertFalse(eligible)
        self.assertEqual(
            reason,
            "active_contract",
        )

    def test_pt_eligibility_excludes_qo(
        self,
    ) -> None:
        eligible, reason = (
            _player_pt_eligibility(
                player_key="p1",
                is_qo_eligible=True,
                contracted=set(),
                tagged=set(),
            )
        )

        self.assertFalse(eligible)
        self.assertEqual(
            reason,
            "qo_eligible",
        )

    def test_pt_eligibility_excludes_existing_pt(
        self,
    ) -> None:
        eligible, reason = (
            _player_pt_eligibility(
                player_key="p1",
                is_qo_eligible=False,
                contracted=set(),
                tagged={"p1"},
            )
        )

        self.assertFalse(eligible)
        self.assertEqual(
            reason,
            "existing_prospect_tag",
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
                main.commissioner_prospect_tags(
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
                "get_commissioner_prospect_state",
                return_value=state(),
            ),
        ):
            result = (
                main.commissioner_prospect_tags(
                    request()
                )
            )

        self.assertEqual(
            result.prospect_count,
            9,
        )

        self.assertEqual(
            result.keeper_pt_count,
            9,
        )

    def test_post_requires_write_identity(
        self,
    ) -> None:
        payload = (
            CommissionerProspectUpdateRequest(
                yahoo_player_key=
                    "player.new"
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
                "save_commissioner_prospect_tag",
            ) as save,
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_prospect_tags_update(
                    "team.a",
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "prospect-tags/team.a"
                        ),
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            401,
        )

        save.assert_not_called()

    def test_post_delegates_once(
        self,
    ) -> None:
        payload = (
            CommissionerProspectUpdateRequest(
                yahoo_player_key=
                    "player.new"
            )
        )

        service_result = {
            "action":
                "replace",
            "team_key":
                "team.a",
            "yahoo_player_key":
                "player.new",
            "keeper_assignments":
                144,
            "state":
                state(),
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
                "save_commissioner_prospect_tag",
                return_value=service_result,
            ) as save,
        ):
            result = (
                main.commissioner_prospect_tags_update(
                    "team.a",
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "prospect-tags/team.a"
                        ),
                    ),
                )
            )

        save.assert_called_once_with(
            team_key="team.a",
            yahoo_player_key="player.new",
            created_by=(
                "commissioner_api:user:7"
            ),
        )

        self.assertEqual(
            result.action,
            "replace",
        )

    def test_delete_delegates_once(
        self,
    ) -> None:
        service_result = {
            "action":
                "remove",
            "team_key":
                "team.a",
            "yahoo_player_key":
                "player.old",
            "keeper_assignments":
                143,
            "state":
                state(),
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
                "remove_commissioner_prospect_tag",
                return_value=service_result,
            ) as remove,
        ):
            result = (
                main.commissioner_prospect_tags_delete(
                    "team.a",
                    request(
                        method="DELETE",
                        path=(
                            "/gateway/commissioner/"
                            "prospect-tags/team.a"
                        ),
                    ),
                )
            )

        remove.assert_called_once_with(
            team_key="team.a",
        )

        self.assertEqual(
            result.action,
            "remove",
        )

    def test_conflict_returns_409(
        self,
    ) -> None:
        payload = (
            CommissionerProspectUpdateRequest(
                yahoo_player_key=
                    "player.new"
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
                "save_commissioner_prospect_tag",
                side_effect=ProspectConflict(
                    "changed"
                ),
            ),
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_prospect_tags_update(
                    "team.a",
                    payload,
                    request(
                        method="POST",
                        path=(
                            "/gateway/commissioner/"
                            "prospect-tags/team.a"
                        ),
                    ),
                )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )


if __name__ == "__main__":
    unittest.main()
