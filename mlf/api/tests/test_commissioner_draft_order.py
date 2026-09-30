from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from mlf_api import main


def request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/commissioner/draft-order",
            "raw_path": b"/commissioner/draft-order",
            "query_string": b"",
            "headers": [],
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


class CommissionerDraftOrderTests(
    unittest.TestCase
):
    def test_public_request_is_denied(self) -> None:
        with patch.object(
            main,
            "_resolve_commissioner_request",
            return_value=None,
        ):
            with self.assertRaises(
                HTTPException
            ) as raised:
                main.commissioner_draft_order(
                    request()
                )

        self.assertEqual(
            raised.exception.status_code,
            403,
        )

    def test_commissioner_get_returns_state(
        self,
    ) -> None:
        state = {
            "draft_key": "mlf_2026_preseason",
            "status": "complete",
            "manager_count": 2,
            "draft_order_mode": "straight",
            "first_standard_round": 6,
            "selection_count": 8,
            "can_rebase": False,
            "lock_reason":
                "Draft order is locked because "
                "8 selections already exist.",
            "slots": [
                {
                    "slot_number": 1,
                    "team_key": "team.1",
                    "team_name": "Team One",
                },
                {
                    "slot_number": 2,
                    "team_key": "team.2",
                    "team_name": "Team Two",
                },
            ],
        }

        with (
            patch.object(
                main,
                "_resolve_commissioner_request",
                return_value={
                    "role": "commissioner",
                },
            ),
            patch.object(
                main,
                "get_commissioner_draft_order_state",
                return_value=state,
            ),
        ):
            result = (
                main.commissioner_draft_order(
                    request()
                )
            )

        self.assertEqual(
            result.draft_key,
            "mlf_2026_preseason",
        )
        self.assertEqual(
            result.selection_count,
            8,
        )
        self.assertFalse(
            result.can_rebase
        )
        self.assertEqual(
            len(result.slots),
            2,
        )


if __name__ == "__main__":
    unittest.main()
