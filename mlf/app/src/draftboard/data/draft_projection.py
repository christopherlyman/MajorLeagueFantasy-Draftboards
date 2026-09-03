from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from draftboard.domain.models import (
    PickLogEntry,
    PickSlot,
    Position,
    RoundType,
)
from draftboard.state.store import DraftClock, DraftState


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()

    if not text:
        raise ValueError(f"{field} is required.")

    return text


def _to_naive_utc_iso(value: datetime | None) -> str | None:
    """
    Match the existing DraftState clock/pick timestamp convention:
    naive ISO text representing UTC.
    """
    if value is None:
        return None

    if not isinstance(value, datetime):
        raise RuntimeError(
            f"Expected datetime or None; got {type(value).__name__}."
        )

    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)

    return value.isoformat()


def _require_player(state: DraftState, player_key: str):
    player = state.players.get(player_key)

    if player is None:
        raise RuntimeError(
            "Relational draft state references player "
            f"{player_key!r}, but that player is absent from the "
            "loaded DraftState player universe."
        )

    return player


def _primary_position_for_log(
    state: DraftState,
    player_key: str,
) -> Position:
    player = _require_player(state, player_key)

    try:
        position = player.primary_position
    except Exception as exc:
        raise RuntimeError(
            "Cannot derive primary position for relational selection "
            f"{player_key!r}."
        ) from exc

    if isinstance(position, Position):
        return position

    try:
        return Position(str(position))
    except Exception as exc:
        raise RuntimeError(
            "Unsupported primary position for relational selection "
            f"{player_key!r}: {position!r}."
        ) from exc


def _build_pick_order(
    *,
    rows: list[dict],
    draft_order_mode: str,
    first_standard_round: int,
) -> list[str]:
    by_round: dict[int, list[dict]] = {}

    for row in rows:
        round_number = int(row["round_number"])
        by_round.setdefault(round_number, []).append(row)

    out: list[str] = []

    for round_number in sorted(by_round):
        round_rows = sorted(
            by_round[round_number],
            key=lambda row: int(row["slot_number"]),
        )

        if not round_rows:
            continue

        pick_type = str(round_rows[0]["pick_type"])

        if (
            pick_type == "STANDARD"
            and draft_order_mode == "snake"
        ):
            standard_round_index = (
                int(round_number) - int(first_standard_round)
            )

            if standard_round_index % 2 == 1:
                round_rows = list(reversed(round_rows))

        out.extend(str(row["pick_id"]) for row in round_rows)

    return out


def load_draft_state_projection(
    *,
    dsn: str,
    draft_key: str,
    base_state: DraftState,
) -> DraftState:
    """
    Return a DraftState read model projected from canonical MLF tables.

    The existing DraftState supplies UI/session preferences plus the rich
    Yahoo Team/Player objects already loaded by the application.

    PostgreSQL supplies authoritative draft facts:
      - draft_pick: slot identity and current ownership
      - draft_selection: real picks
      - draft_keeper_assignment: CONTRACT/PT placeholders
      - draft_qo_current: evolving QO placeholders
      - draft_runtime: current pick and clock state

    This function performs SELECTs only. It does not mutate base_state,
    Streamlit session state, or PostgreSQL.
    """

    dsn_text = _required_text(dsn, field="dsn")
    draft = _required_text(draft_key, field="draft_key")

    if not isinstance(base_state, DraftState):
        raise TypeError("base_state must be a DraftState.")

    with psycopg.connect(dsn_text) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    draft_key,
                    league_key,
                    season_year,
                    manager_count,
                    rounds_total,
                    qo_rounds,
                    first_standard_round,
                    draft_order_mode,
                    status
                FROM mlf.draft
                WHERE draft_key = %s
                """,
                (draft,),
            )

            draft_row = cur.fetchone()

            if draft_row is None:
                raise RuntimeError(
                    f"MLF draft {draft!r} was not found."
                )

            cur.execute(
                """
                SELECT
                    dp.pick_id,
                    dp.round_number,
                    dp.slot_number,
                    dp.pick_type,
                    dp.column_team_key,
                    dp.current_owner_team_key,

                    ds.selecting_team_key,
                    ds.yahoo_player_key AS selected_player_key,
                    ds.pick_kind,
                    ds.selected_at_utc,

                    dka.team_key AS keeper_team_key,
                    dka.yahoo_player_key AS keeper_player_key,
                    dka.keeper_kind
                FROM mlf.draft_pick AS dp
                LEFT JOIN mlf.draft_selection AS ds
                    ON ds.draft_key = dp.draft_key
                   AND ds.pick_id = dp.pick_id
                LEFT JOIN mlf.draft_keeper_assignment AS dka
                    ON dka.draft_key = dp.draft_key
                   AND dka.pick_id = dp.pick_id
                WHERE dp.draft_key = %s
                ORDER BY
                    dp.round_number,
                    dp.slot_number
                """,
                (draft,),
            )

            pick_rows = list(cur.fetchall())

            cur.execute(
                """
                SELECT
                    team_key,
                    qo_level,
                    yahoo_player_key
                FROM mlf.draft_qo_current
                WHERE draft_key = %s
                ORDER BY
                    team_key,
                    qo_level
                """,
                (draft,),
            )

            qo_rows = list(cur.fetchall())

            cur.execute(
                """
                SELECT
                    current_pick_id,
                    auto_advance,
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
                (draft,),
            )

            runtime_row = cur.fetchone()

    if not pick_rows:
        raise RuntimeError(
            f"MLF draft {draft!r} contains no draft_pick rows."
        )

    if runtime_row is None:
        raise RuntimeError(
            f"MLF draft {draft!r} has no draft_runtime row."
        )

    manager_count = int(draft_row["manager_count"])
    first_standard_round = int(
        draft_row["first_standard_round"]
    )
    order_mode = str(draft_row["draft_order_mode"])

    if order_mode not in {"straight", "snake"}:
        raise RuntimeError(
            f"Unsupported relational draft order mode {order_mode!r}."
        )

    projected = deepcopy(base_state)

    qo_by_team_level: dict[tuple[str, int], str] = {}

    for row in qo_rows:
        team_key = _required_text(
            row["team_key"],
            field="draft_qo_current.team_key",
        )
        level = int(row["qo_level"])
        player_key = _required_text(
            row["yahoo_player_key"],
            field="draft_qo_current.yahoo_player_key",
        )

        if level < 1 or level > 5:
            raise RuntimeError(
                f"Invalid QO level {level} for team {team_key!r}."
            )

        _require_player(projected, player_key)

        qo_by_team_level[(team_key, level)] = player_key

    picks: dict[str, PickSlot] = {}
    selection_rows: list[dict] = []
    pt_player_team_map: dict[str, str] = {}

    for row in pick_rows:
        pick_id = _required_text(
            row["pick_id"],
            field="draft_pick.pick_id",
        )
        pick_type_text = _required_text(
            row["pick_type"],
            field=f"{pick_id}.pick_type",
        )
        original_team_key = _required_text(
            row["column_team_key"],
            field=f"{pick_id}.column_team_key",
        )
        owner_team_key = _required_text(
            row["current_owner_team_key"],
            field=f"{pick_id}.current_owner_team_key",
        )

        if original_team_key not in projected.teams:
            raise RuntimeError(
                f"{pick_id} column team {original_team_key!r} "
                "is absent from DraftState teams."
            )

        if owner_team_key not in projected.teams:
            raise RuntimeError(
                f"{pick_id} owner {owner_team_key!r} "
                "is absent from DraftState teams."
            )

        try:
            round_type = RoundType(pick_type_text)
        except Exception as exc:
            raise RuntimeError(
                f"{pick_id} has unsupported pick_type "
                f"{pick_type_text!r}."
            ) from exc

        selected_player_key = (
            str(row["selected_player_key"]).strip()
            if row["selected_player_key"] is not None
            else ""
        )
        keeper_player_key = (
            str(row["keeper_player_key"]).strip()
            if row["keeper_player_key"] is not None
            else ""
        )

        if selected_player_key and keeper_player_key:
            raise RuntimeError(
                f"{pick_id} is both a real selection and "
                "a keeper assignment."
            )

        selected_ts_iso: str | None = None
        projected_player_key: str | None = None

        if selected_player_key:
            _require_player(projected, selected_player_key)

            projected_player_key = selected_player_key
            selected_ts_iso = _to_naive_utc_iso(
                row["selected_at_utc"]
            )
            selection_rows.append(row)

        elif keeper_player_key:
            _require_player(projected, keeper_player_key)

            keeper_team_key = _required_text(
                row["keeper_team_key"],
                field=f"{pick_id}.keeper_team_key",
            )
            keeper_kind = _required_text(
                row["keeper_kind"],
                field=f"{pick_id}.keeper_kind",
            )

            if keeper_team_key != owner_team_key:
                raise RuntimeError(
                    f"{pick_id} keeper team {keeper_team_key!r} "
                    f"does not match pick owner {owner_team_key!r}."
                )

            if keeper_kind not in {"CONTRACT", "PT"}:
                raise RuntimeError(
                    f"{pick_id} has unsupported keeper kind "
                    f"{keeper_kind!r}."
                )

            projected_player_key = keeper_player_key

            if keeper_kind == "PT":
                pt_player_team_map[
                    keeper_player_key
                ] = keeper_team_key

        elif round_type == RoundType.QO:
            projected_player_key = qo_by_team_level.get(
                (
                    owner_team_key,
                    int(row["round_number"]),
                )
            )

        picks[pick_id] = PickSlot(
            pick_id=pick_id,
            round_type=round_type,
            round_number=int(row["round_number"]),
            slot=int(row["slot_number"]),
            original_team_key=original_team_key,
            owner_team_key=owner_team_key,
            selected_player_key=projected_player_key,
            selected_ts_iso=selected_ts_iso,
        )

    pick_order = _build_pick_order(
        rows=pick_rows,
        draft_order_mode=order_mode,
        first_standard_round=first_standard_round,
    )

    if set(pick_order) != set(picks):
        raise RuntimeError(
            "Projected pick_order does not contain exactly the "
            "relational draft_pick IDs."
        )

    first_standard_rows = sorted(
        (
            row
            for row in pick_rows
            if int(row["round_number"])
            == first_standard_round
        ),
        key=lambda row: int(row["slot_number"]),
    )

    draft_order_team_keys_by_slot = [
        str(row["column_team_key"])
        for row in first_standard_rows
    ]

    if len(draft_order_team_keys_by_slot) != manager_count:
        raise RuntimeError(
            "Could not derive complete relational draft slot order: "
            f"expected {manager_count}, found "
            f"{len(draft_order_team_keys_by_slot)}."
        )

    if len(set(draft_order_team_keys_by_slot)) != manager_count:
        raise RuntimeError(
            "Relational draft slot order contains duplicate teams."
        )

    for team_key in draft_order_team_keys_by_slot:
        if team_key not in projected.teams:
            raise RuntimeError(
                "Relational slot order references unknown team "
                f"{team_key!r}."
            )

    selection_rows.sort(
        key=lambda row: (
            row["selected_at_utc"],
            int(row["round_number"]),
            int(row["slot_number"]),
        )
    )

    pick_log: list[PickLogEntry] = []

    for row in selection_rows:
        pick_id = str(row["pick_id"])
        player_key = str(row["selected_player_key"])
        player = _require_player(projected, player_key)
        selecting_team_key = _required_text(
            row["selecting_team_key"],
            field=f"{pick_id}.selecting_team_key",
        )
        pick_kind = _required_text(
            row["pick_kind"],
            field=f"{pick_id}.pick_kind",
        )
        ts_iso = _to_naive_utc_iso(row["selected_at_utc"])

        if ts_iso is None:
            raise RuntimeError(
                f"{pick_id} real selection has no timestamp."
            )

        pick_log.append(
            PickLogEntry(
                event_id=f"db:{draft}:{pick_id}",
                pick_id=pick_id,
                owner_team_key=selecting_team_key,
                player_key=player_key,
                player_name=player.name,
                primary_position=_primary_position_for_log(
                    projected,
                    player_key,
                ),
                pick_kind=pick_kind,
                ts_iso=ts_iso,
            )
        )

    current_pick_id = (
        str(runtime_row["current_pick_id"])
        if runtime_row["current_pick_id"] is not None
        else ""
    )

    if current_pick_id and current_pick_id not in picks:
        raise RuntimeError(
            "draft_runtime.current_pick_id references unknown pick "
            f"{current_pick_id!r}."
        )

    projected.picks = picks
    projected.pick_order = pick_order
    projected.pick_log = pick_log
    projected.pt_player_team_map = pt_player_team_map
    projected.draft_order_team_keys_by_slot = (
        draft_order_team_keys_by_slot
    )

    projected.clock = DraftClock(
        current_pick_id=current_pick_id,
        auto_advance=bool(runtime_row["auto_advance"]),
        is_running=bool(runtime_row["is_running"]),
        pick_started_ts_iso=_to_naive_utc_iso(
            runtime_row["pick_started_at_utc"]
        ),
        pick_paused_ts_iso=_to_naive_utc_iso(
            runtime_row["pick_paused_at_utc"]
        ),
        elapsed_paused_seconds=int(
            runtime_row["elapsed_paused_seconds"]
        ),
        seconds_per_pick=int(
            runtime_row["seconds_per_pick"]
        ),
        weekends_count=bool(
            runtime_row["weekends_count"]
        ),
        timezone=str(runtime_row["timezone_name"]),
    )

    return projected
