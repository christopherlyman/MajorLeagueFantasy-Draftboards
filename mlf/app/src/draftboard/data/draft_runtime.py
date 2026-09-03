from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row


_ALLOWED_PICK_KINDS = frozenset({"FA", "QO", "POACH"})


@dataclass(frozen=True, slots=True)
class DraftPickExecution:
    """Result returned by the canonical MLF atomic pick executor."""

    result_status: str
    executed_pick_id: str
    selecting_team_key: str
    selected_player_key: str
    selected_pick_kind: str
    next_pick_id: str | None
    selected_at_utc: datetime | None


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()

    if not text:
        raise ValueError(f"{field} is required.")

    return text


def submit_draft_pick_atomic(
    *,
    dsn: str,
    draft_key: str,
    pick_id: str,
    expected_owner_team_key: str,
    yahoo_player_key: str,
    expected_pick_kind: str,
    selected_by: str = "draftboard_manual",
) -> DraftPickExecution:
    """
    Execute one real MLF draft pick through PostgreSQL.

    PostgreSQL is authoritative for:
      - active-pick validation
      - pick ownership
      - contract/PT protection
      - QO/POACH classification
      - duplicate-player protection
      - concurrent submission protection
      - QO ladder mutation
      - next-pick advancement
      - draft-runtime state

    This function does not mutate Streamlit DraftState and does not persist
    JSON DraftState. The caller must refresh its derived projection after a
    successful execution.
    """

    dsn_text = _required_text(dsn, field="dsn")
    draft = _required_text(draft_key, field="draft_key")
    pick = _required_text(pick_id, field="pick_id")
    owner = _required_text(
        expected_owner_team_key,
        field="expected_owner_team_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )
    kind = _required_text(
        expected_pick_kind,
        field="expected_pick_kind",
    ).upper()
    actor = _required_text(
        selected_by,
        field="selected_by",
    )

    if kind not in _ALLOWED_PICK_KINDS:
        raise ValueError(
            "expected_pick_kind must be one of "
            f"{sorted(_ALLOWED_PICK_KINDS)}; got {kind!r}."
        )

    sql = """
        SELECT
            result_status,
            executed_pick_id,
            selecting_team_key,
            selected_player_key,
            selected_pick_kind,
            next_pick_id,
            selected_at_utc
        FROM mlf.submit_draft_pick_atomic(
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """

    with psycopg.connect(dsn_text) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql,
                (
                    draft,
                    pick,
                    owner,
                    player,
                    kind,
                    actor,
                ),
            )

            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF atomic draft executor returned no result."
        )

    selected_at = row["selected_at_utc"]

    if (
        selected_at is not None
        and not isinstance(selected_at, datetime)
    ):
        raise RuntimeError(
            "MLF atomic draft executor returned an invalid "
            "selected_at_utc value."
        )

    result = DraftPickExecution(
        result_status=str(row["result_status"] or ""),
        executed_pick_id=str(row["executed_pick_id"] or ""),
        selecting_team_key=str(row["selecting_team_key"] or ""),
        selected_player_key=str(row["selected_player_key"] or ""),
        selected_pick_kind=str(row["selected_pick_kind"] or ""),
        next_pick_id=(
            str(row["next_pick_id"])
            if row["next_pick_id"] is not None
            else None
        ),
        selected_at_utc=selected_at,
    )

    if result.result_status != "EXECUTED":
        raise RuntimeError(
            "MLF atomic draft executor returned unexpected status "
            f"{result.result_status!r}."
        )

    if result.executed_pick_id != pick:
        raise RuntimeError(
            "MLF atomic draft executor returned a different pick: "
            f"expected {pick!r}, got {result.executed_pick_id!r}."
        )

    if result.selecting_team_key != owner:
        raise RuntimeError(
            "MLF atomic draft executor returned a different owner: "
            f"expected {owner!r}, got {result.selecting_team_key!r}."
        )

    if result.selected_player_key != player:
        raise RuntimeError(
            "MLF atomic draft executor returned a different player: "
            f"expected {player!r}, got {result.selected_player_key!r}."
        )

    if result.selected_pick_kind != kind:
        raise RuntimeError(
            "MLF atomic draft executor classification disagreed with "
            f"the caller: expected {kind!r}, "
            f"got {result.selected_pick_kind!r}."
        )

    return result
