from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import psycopg
from fastapi import HTTPException
from starlette.requests import Request

from draftboard.data.draft_runtime import DraftPickExecution
from mlf_api import main
from mlf_api.models import DraftPickSubmitRequest


DRAFT_KEY = "mlf_2026_preseason"
TEAM_KEY = "469.l.41640.t.1"
OTHER_TEAM_KEY = "469.l.41640.t.2"
PLAYER_KEY = "mlb.p.12345"


def manager_principal() -> dict[str, object]:
    return {
        "is_authenticated": True,
        "role": "manager",
        "league_key": "469.l.41640",
        "season_year": 2026,
        "franchise_id": 1,
        "team_key": TEAM_KEY,
        "team_name": "The Gunn Show",
        "display_name": "Manager",
        "acting_as": "manager:The Gunn Show",
    }


def request_for_write(
    *,
    origin: str | None = main.PRODUCTION_ORIGIN,
    referer: str | None = None,
    cookie: bool = True,
    content_type: str = "application/json",
) -> Request:
    headers: list[tuple[bytes, bytes]] = [
        (
            b"content-type",
            content_type.encode("ascii"),
        )
    ]

    if origin is not None:
        headers.append(
            (
                b"origin",
                origin.encode("ascii"),
            )
        )

    if referer is not None:
        headers.append(
            (
                b"referer",
                referer.encode("ascii"),
            )
        )

    if cookie:
        headers.append(
            (
                b"cookie",
                b"mlf_team_gateway=signed-cookie",
            )
        )

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/drafts/{DRAFT_KEY}/picks",
            "headers": headers,
            "scheme": "https",
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


def payload(
    *,
    owner: str = TEAM_KEY,
) -> DraftPickSubmitRequest:
    return DraftPickSubmitRequest(
        pick_id="R01-S01",
        expected_owner_team_key=owner,
        yahoo_player_key=PLAYER_KEY,
        expected_pick_kind="FA",
    )


class DraftWriteContractTests(unittest.TestCase):
    @patch(
        "mlf_api.main.get_gateway_cookie_name",
        return_value="mlf_team_gateway",
    )
    @patch(
        "mlf_api.main.resolve_manager_principal",
        return_value=manager_principal(),
    )
    @patch(
        "mlf_api.main.get_draft_key",
        return_value=DRAFT_KEY,
    )
    @patch(
        "mlf_api.main.submit_draft_pick_atomic",
    )
    def test_manager_delegates_to_atomic_wrapper(
        self,
        mock_submit,
        _mock_draft,
        _mock_resolve,
        _mock_cookie,
    ) -> None:
        selected_at = datetime(
            2026,
            9,
            23,
            12,
            0,
            tzinfo=timezone.utc,
        )

        mock_submit.return_value = DraftPickExecution(
            result_status="EXECUTED",
            executed_pick_id="R01-S01",
            selecting_team_key=TEAM_KEY,
            selected_player_key=PLAYER_KEY,
            selected_pick_kind="FA",
            next_pick_id="R01-S02",
            selected_at_utc=selected_at,
        )

        with patch(
            "mlf_api.main.get_postgres_dsn",
            return_value="postgresql://unused",
        ):
            result = main.submit_draft_pick(
                DRAFT_KEY,
                payload(),
                request_for_write(),
            )

        self.assertEqual(
            result.result_status,
            "EXECUTED",
        )

        mock_submit.assert_called_once_with(
            dsn="postgresql://unused",
            draft_key=DRAFT_KEY,
            pick_id="R01-S01",
            expected_owner_team_key=TEAM_KEY,
            yahoo_player_key=PLAYER_KEY,
            expected_pick_kind="FA",
            selected_by="api:manager:1",
        )

    @patch(
        "mlf_api.main.get_gateway_cookie_name",
        return_value="mlf_team_gateway",
    )
    def test_missing_cookie_is_401(
        self,
        _mock_cookie,
    ) -> None:
        with self.assertRaises(
            HTTPException
        ) as ctx:
            main.submit_draft_pick(
                DRAFT_KEY,
                payload(),
                request_for_write(
                    cookie=False
                ),
            )

        self.assertEqual(
            ctx.exception.status_code,
            401,
        )

    @patch(
        "mlf_api.main.get_gateway_cookie_name",
        return_value="mlf_team_gateway",
    )
    @patch(
        "mlf_api.main.resolve_manager_principal",
        return_value=manager_principal(),
    )
    @patch(
        "mlf_api.main.get_draft_key",
        return_value=DRAFT_KEY,
    )
    @patch(
        "mlf_api.main.submit_draft_pick_atomic",
    )
    def test_manager_cannot_submit_other_team(
        self,
        mock_submit,
        _mock_draft,
        _mock_resolve,
        _mock_cookie,
    ) -> None:
        with self.assertRaises(
            HTTPException
        ) as ctx:
            main.submit_draft_pick(
                DRAFT_KEY,
                payload(
                    owner=OTHER_TEAM_KEY
                ),
                request_for_write(),
            )

        self.assertEqual(
            ctx.exception.status_code,
            403,
        )

        mock_submit.assert_not_called()

    def test_missing_origin_and_referer_is_403(
        self,
    ) -> None:
        with self.assertRaises(
            HTTPException
        ) as ctx:
            main.submit_draft_pick(
                DRAFT_KEY,
                payload(),
                request_for_write(
                    origin=None,
                    referer=None,
                ),
            )

        self.assertEqual(
            ctx.exception.status_code,
            403,
        )

    def test_wrong_origin_is_403(
        self,
    ) -> None:
        with self.assertRaises(
            HTTPException
        ) as ctx:
            main.submit_draft_pick(
                DRAFT_KEY,
                payload(),
                request_for_write(
                    origin="https://example.com",
                ),
            )

        self.assertEqual(
            ctx.exception.status_code,
            403,
        )

    @patch(
        "mlf_api.main.get_gateway_cookie_name",
        return_value="mlf_team_gateway",
    )
    @patch(
        "mlf_api.main.resolve_manager_principal",
        return_value=manager_principal(),
    )
    @patch(
        "mlf_api.main.get_draft_key",
        return_value=DRAFT_KEY,
    )
    @patch(
        "mlf_api.main.submit_draft_pick_atomic",
    )
    def test_referer_fallback_is_accepted(
        self,
        mock_submit,
        _mock_draft,
        _mock_resolve,
        _mock_cookie,
    ) -> None:
        mock_submit.return_value = DraftPickExecution(
            result_status="EXECUTED",
            executed_pick_id="R01-S01",
            selecting_team_key=TEAM_KEY,
            selected_player_key=PLAYER_KEY,
            selected_pick_kind="FA",
            next_pick_id=None,
            selected_at_utc=None,
        )

        with patch(
            "mlf_api.main.get_postgres_dsn",
            return_value="postgresql://unused",
        ):
            result = main.submit_draft_pick(
                DRAFT_KEY,
                payload(),
                request_for_write(
                    origin=None,
                    referer=(
                        "https://mlf.majorleaguefantasy.app/"
                        "draft"
                    ),
                ),
            )

        self.assertEqual(
            result.result_status,
            "EXECUTED",
        )

    @patch(
        "mlf_api.main.get_gateway_cookie_name",
        return_value="mlf_team_gateway",
    )
    @patch(
        "mlf_api.main.resolve_manager_principal",
        return_value=manager_principal(),
    )
    @patch(
        "mlf_api.main.get_draft_key",
        return_value=DRAFT_KEY,
    )
    @patch(
        "mlf_api.main.submit_draft_pick_atomic",
    )
    def test_postgres_rejection_is_409(
        self,
        mock_submit,
        _mock_draft,
        _mock_resolve,
        _mock_cookie,
    ) -> None:
        mock_submit.side_effect = (
            psycopg.errors.RaiseException(
                "Draft is complete."
            )
        )

        with patch(
            "mlf_api.main.get_postgres_dsn",
            return_value="postgresql://unused",
        ):
            with self.assertRaises(
                HTTPException
            ) as ctx:
                main.submit_draft_pick(
                    DRAFT_KEY,
                    payload(),
                    request_for_write(),
                )

        self.assertEqual(
            ctx.exception.status_code,
            409,
        )

    @patch(
        "mlf_api.main.get_gateway_cookie_name",
        return_value="mlf_team_gateway",
    )
    @patch(
        "mlf_api.main.resolve_manager_principal",
        return_value=manager_principal(),
    )
    @patch(
        "mlf_api.main.get_draft_key",
        return_value=DRAFT_KEY,
    )
    @patch(
        "mlf_api.main.submit_draft_pick_atomic",
    )
    def test_wrong_draft_path_is_404(
        self,
        mock_submit,
        _mock_draft,
        _mock_resolve,
        _mock_cookie,
    ) -> None:
        with self.assertRaises(
            HTTPException
        ) as ctx:
            main.submit_draft_pick(
                "wrong-draft",
                payload(),
                request_for_write(),
            )

        self.assertEqual(
            ctx.exception.status_code,
            404,
        )

        mock_submit.assert_not_called()

    def test_non_json_is_400(
        self,
    ) -> None:
        with self.assertRaises(
            HTTPException
        ) as ctx:
            main.submit_draft_pick(
                DRAFT_KEY,
                payload(),
                request_for_write(
                    content_type="text/plain",
                ),
            )

        self.assertEqual(
            ctx.exception.status_code,
            400,
        )


if __name__ == "__main__":
    unittest.main()
