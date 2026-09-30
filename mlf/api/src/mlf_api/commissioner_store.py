from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from draftboard.state.runtime import (
    get_draft_key,
    get_postgres_dsn,
)


def get_commissioner_draft_order_state() -> dict[str, object]:
    draft_key = str(get_draft_key()).strip()

    if not draft_key:
        raise RuntimeError("Missing active MLF draft key.")

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.draft_key,
                    d.status,
                    d.manager_count,
                    d.draft_order_mode,
                    d.first_standard_round,
                    (
                        SELECT count(*)
                        FROM mlf.draft_selection AS ds
                        WHERE ds.draft_key = d.draft_key
                    ) AS selection_count
                FROM mlf.draft AS d
                WHERE d.draft_key = %s
                """,
                (draft_key,),
            )

            draft = cur.fetchone()

            if draft is None:
                raise RuntimeError(
                    f"MLF draft {draft_key!r} was not found."
                )

            first_standard_round = int(
                draft["first_standard_round"]
            )

            cur.execute(
                """
                SELECT
                    dp.slot_number,
                    dp.column_team_key AS team_key,
                    COALESCE(
                        t.team_name,
                        dp.column_team_key
                    ) AS team_name
                FROM mlf.draft_pick AS dp
                JOIN mlf.draft AS d
                  ON d.draft_key = dp.draft_key
                LEFT JOIN mlf.team AS t
                  ON t.league_key = d.league_key
                 AND t.season_year = d.season_year
                 AND t.team_key = dp.column_team_key
                WHERE dp.draft_key = %s
                  AND dp.round_number = %s
                ORDER BY dp.slot_number
                """,
                (
                    draft_key,
                    first_standard_round,
                ),
            )

            rows = list(cur.fetchall())

    manager_count = int(draft["manager_count"])

    if len(rows) != manager_count:
        raise RuntimeError(
            "Canonical draft-order row count does not "
            f"match manager count: {len(rows)} != "
            f"{manager_count}."
        )

    slots = [
        {
            "slot_number": int(row["slot_number"]),
            "team_key": str(row["team_key"]),
            "team_name": str(row["team_name"]),
        }
        for row in rows
    ]

    team_keys = [
        str(slot["team_key"])
        for slot in slots
    ]

    if len(set(team_keys)) != manager_count:
        raise RuntimeError(
            "Canonical draft order contains duplicate teams."
        )

    selection_count = int(
        draft["selection_count"]
    )

    can_rebase = selection_count == 0

    lock_reason = None

    if not can_rebase:
        lock_reason = (
            "Draft order is locked because "
            f"{selection_count} selections already exist."
        )

    return {
        "draft_key": str(draft["draft_key"]),
        "status": str(draft["status"]),
        "manager_count": manager_count,
        "draft_order_mode": str(
            draft["draft_order_mode"]
        ),
        "first_standard_round":
            first_standard_round,
        "selection_count": selection_count,
        "can_rebase": can_rebase,
        "lock_reason": lock_reason,
        "slots": slots,
    }
