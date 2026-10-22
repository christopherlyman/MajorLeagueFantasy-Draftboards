from __future__ import annotations

from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from draftboard.data.draft_runtime import (
    delete_draft_selection_atomic,
    set_current_pick_atomic,
    update_draft_clock_atomic,
)
from draftboard.state.runtime import (
    get_draft_key,
    get_postgres_dsn,
)
from mlf_api.commissioner_store import (
    get_commissioner_draft_order_state,
)


_ALLOWED_CLOCK_ACTIONS = frozenset(
    {
        "START",
        "PAUSE",
        "RESUME",
        "SET_DURATION",
        "SET_WEEKENDS",
    }
)


def _required_text(
    value: object,
    *,
    field: str,
) -> str:
    text = str(
        value or ""
    ).strip()

    if not text:
        raise ValueError(
            f"{field} is required."
        )

    return text


def _iso(
    value: object,
) -> str | None:
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    return str(value)


def _pick_row(
    row: dict[str, object],
    team_names: dict[str, str],
) -> dict[str, object]:
    owner_key = str(
        row["current_owner_team_key"]
        or ""
    )

    player_key = (
        str(
            row["selected_player_key"]
        )
        if row["selected_player_key"]
        is not None
        else None
    )

    player_name = (
        str(
            row["selected_player_name"]
        )
        if row["selected_player_name"]
        is not None
        else player_key
    )

    return {
        "pick_id":
            str(row["pick_id"]),
        "round_number":
            int(row["round_number"]),
        "slot_number":
            int(row["slot_number"]),
        "current_owner_team_key":
            owner_key,
        "current_owner_team_name":
            team_names.get(
                owner_key,
                owner_key,
            ),
        "selected_player_key":
            player_key,
        "selected_player_name":
            player_name,
        "pick_kind":
            (
                str(row["pick_kind"])
                if row["pick_kind"]
                is not None
                else None
            ),
        "selected_at_utc":
            _iso(
                row["selected_at_utc"]
            ),
    }


def get_commissioner_draft_tools_state(
) -> dict[str, object]:
    draft_key = _required_text(
        get_draft_key(),
        field="draft_key",
    )

    order_state = (
        get_commissioner_draft_order_state()
    )

    team_names = {
        str(slot["team_key"]):
            str(slot["team_name"])
        for slot in order_state["slots"]
    }

    with psycopg.connect(
        _required_text(
            get_postgres_dsn(),
            field="dsn",
        ),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_pick_id,
                    is_running,
                    pick_started_at_utc,
                    pick_paused_at_utc,
                    elapsed_paused_seconds,
                    seconds_per_pick,
                    weekends_count,
                    timezone_name
                FROM mlf.draft_runtime
                WHERE draft_key = %s
                """,
                (draft_key,),
            )

            runtime = cur.fetchone()

            if runtime is None:
                raise RuntimeError(
                    "MLF draft runtime "
                    "is not initialized."
                )

            cur.execute(
                """
                SELECT
                    dp.pick_id,
                    dp.round_number,
                    dp.slot_number,
                    dp.current_owner_team_key,

                    ds.yahoo_player_key
                        AS selected_player_key,
                    ds.pick_kind,
                    ds.selected_at_utc,

                    pu.player_name
                        AS selected_player_name,

                    (
                        ka.pick_id IS NOT NULL
                    ) AS keeper_occupied

                FROM mlf.draft_pick AS dp

                JOIN mlf.draft AS d
                  ON d.draft_key =
                     dp.draft_key

                LEFT JOIN mlf.draft_selection AS ds
                  ON ds.draft_key =
                     dp.draft_key
                 AND ds.pick_id =
                     dp.pick_id

                LEFT JOIN
                    mlf.draft_keeper_assignment
                    AS ka
                  ON ka.draft_key =
                     dp.draft_key
                 AND ka.pick_id =
                     dp.pick_id

                LEFT JOIN mlf.player_universe AS pu
                  ON pu.league_key =
                     d.league_key
                 AND pu.season_year =
                     d.season_year
                 AND pu.yahoo_player_key =
                     ds.yahoo_player_key

                WHERE dp.draft_key = %s

                ORDER BY
                    dp.round_number,
                    dp.slot_number
                """,
                (draft_key,),
            )

            rows = list(
                cur.fetchall()
            )

    open_picks: list[
        dict[str, object]
    ] = []

    selected_picks: list[
        dict[str, object]
    ] = []

    for row in rows:
        if (
            row["selected_player_key"]
            is not None
        ):
            selected_picks.append(
                _pick_row(
                    row,
                    team_names,
                )
            )
            continue

        if bool(
            row["keeper_occupied"]
        ):
            continue

        open_picks.append(
            _pick_row(
                row,
                team_names,
            )
        )

    current_pick_id = (
        str(
            runtime["current_pick_id"]
        )
        if runtime["current_pick_id"]
        is not None
        else None
    )

    return {
        "draft_key":
            draft_key,
        "draft_status":
            str(
                order_state["status"]
            ),
        "current_pick_id":
            current_pick_id,
        "is_running":
            bool(
                runtime["is_running"]
            ),
        "pick_started_at_utc":
            _iso(
                runtime[
                    "pick_started_at_utc"
                ]
            ),
        "pick_paused_at_utc":
            _iso(
                runtime[
                    "pick_paused_at_utc"
                ]
            ),
        "elapsed_paused_seconds":
            int(
                runtime[
                    "elapsed_paused_seconds"
                ]
            ),
        "seconds_per_pick":
            int(
                runtime[
                    "seconds_per_pick"
                ]
            ),
        "weekends_count":
            bool(
                runtime[
                    "weekends_count"
                ]
            ),
        "timezone_name":
            str(
                runtime[
                    "timezone_name"
                ]
                or ""
            ),
        "selection_count":
            len(
                selected_picks
            ),
        "open_pick_count":
            len(
                open_picks
            ),
        "open_picks":
            open_picks,
        "selected_picks":
            selected_picks,
    }


def set_commissioner_current_pick(
    *,
    pick_id: str,
) -> dict[str, object]:
    result = set_current_pick_atomic(
        dsn=get_postgres_dsn(),
        draft_key=get_draft_key(),
        pick_id=_required_text(
            pick_id,
            field="pick_id",
        ),
    )

    return {
        "action":
            "set_current_pick",
        "result":
            result,
        "state":
            get_commissioner_draft_tools_state(),
    }


def update_commissioner_draft_clock(
    *,
    action: str,
    seconds_per_pick: int | None = None,
    weekends_count: bool | None = None,
) -> dict[str, object]:
    action_text = _required_text(
        action,
        field="action",
    ).upper()

    if (
        action_text
        not in _ALLOWED_CLOCK_ACTIONS
    ):
        raise ValueError(
            "Unsupported Draft Tool "
            "clock action."
        )

    seconds: int | None = None
    weekends: bool | None = None

    if action_text == "SET_DURATION":
        if seconds_per_pick is None:
            raise ValueError(
                "seconds_per_pick is required."
            )

        seconds = int(
            seconds_per_pick
        )

        if (
            seconds < 3600
            or seconds > (72 * 3600)
            or seconds % 3600 != 0
        ):
            raise ValueError(
                "Pick duration must be "
                "1-72 whole hours."
            )

    if action_text == "SET_WEEKENDS":
        if weekends_count is None:
            raise ValueError(
                "weekends_count is required."
            )

        weekends = bool(
            weekends_count
        )

    result = update_draft_clock_atomic(
        dsn=get_postgres_dsn(),
        draft_key=get_draft_key(),
        action=action_text,
        seconds_per_pick=seconds,
        weekends_count=weekends,
    )

    return {
        "action":
            action_text.lower(),
        "result":
            result,
        "state":
            get_commissioner_draft_tools_state(),
    }


def clear_commissioner_draft_selection(
    *,
    pick_id: str,
    rewind_clock: bool,
) -> dict[str, object]:
    result = delete_draft_selection_atomic(
        dsn=get_postgres_dsn(),
        draft_key=get_draft_key(),
        pick_id=_required_text(
            pick_id,
            field="pick_id",
        ),
        rewind_clock=bool(
            rewind_clock
        ),
    )

    return {
        "action":
            (
                "clear_pick_and_rewind"
                if rewind_clock
                else "clear_pick"
            ),
        "result":
            result,
        "state":
            get_commissioner_draft_tools_state(),
    }
