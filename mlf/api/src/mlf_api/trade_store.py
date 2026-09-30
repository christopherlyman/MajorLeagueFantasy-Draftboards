from __future__ import annotations

import json
import uuid

import psycopg
from psycopg.rows import dict_row

from draftboard.data.db_players import (
    load_available_players,
)
from draftboard.data.draft_runtime import (
    apply_trade_assets_atomic,
)
from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


class TradeRequestError(ValueError):
    pass


class TradeConflict(RuntimeError):
    pass


def _runtime_context() -> tuple[str, str, int, str]:
    dsn = str(
        get_postgres_dsn() or ""
    ).strip()

    draft_key = str(
        get_draft_key() or ""
    ).strip()

    league_key = str(
        get_league_key() or ""
    ).strip()

    season_year = int(
        get_season_year()
    )

    if not dsn:
        raise RuntimeError(
            "PostgreSQL DSN is unavailable."
        )

    if not draft_key:
        raise RuntimeError(
            "MLF draft key is unavailable."
        )

    if not league_key:
        raise RuntimeError(
            "MLF league key is unavailable."
        )

    return (
        dsn,
        draft_key,
        season_year,
        league_key,
    )


def _active_contract_map(
    cur: psycopg.Cursor,
    *,
    league_key: str,
    season_year: int,
) -> dict[str, dict[str, object]]:
    cur.execute(
        """
        SELECT
            yahoo_player_key,
            team_key,
            years_remaining
        FROM mlf.v_active_contract
        WHERE league_key = %s
          AND season_year = %s
        """,
        (
            league_key,
            season_year,
        ),
    )

    result: dict[str, dict[str, object]] = {}

    for row in cur.fetchall():
        key = str(
            row["yahoo_player_key"]
        )

        result[key] = {
            "team_key": (
                str(row["team_key"])
                if row["team_key"] is not None
                else None
            ),
            "years_remaining": int(
                row["years_remaining"]
            ),
        }

    return result


def get_trade_builder_state(
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        season_year,
        league_key,
    ) = _runtime_context()

    player_map = load_available_players(
        dsn
    )

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.status,
                    (
                        SELECT count(*)
                        FROM mlf.draft_selection AS ds
                        WHERE ds.draft_key =
                            d.draft_key
                    ) AS selection_count
                FROM mlf.draft AS d
                WHERE d.draft_key = %s
                """,
                (draft_key,),
            )

            draft = cur.fetchone()

            if draft is None:
                raise RuntimeError(
                    f"Draft {draft_key!r} "
                    "was not found."
                )

            cur.execute(
                """
                SELECT
                    team_key,
                    team_name
                FROM mlf.team
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY
                    lower(team_name),
                    team_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            teams = [
                {
                    "team_key":
                        str(row["team_key"]),
                    "team_name":
                        str(row["team_name"]),
                }
                for row in cur.fetchall()
            ]

            contracts = (
                _active_contract_map(
                    cur,
                    league_key=league_key,
                    season_year=season_year,
                )
            )

            cur.execute(
                """
                SELECT
                    dp.pick_id,
                    dp.round_number,
                    dp.slot_number,
                    dp.current_owner_team_key,
                    COALESCE(
                        t.team_name,
                        dp.current_owner_team_key
                    ) AS owner_team_name,
                    dp.traded_flag
                FROM mlf.draft_pick AS dp
                LEFT JOIN mlf.team AS t
                  ON t.league_key = %s
                 AND t.season_year = %s
                 AND t.team_key =
                     dp.current_owner_team_key
                WHERE dp.draft_key = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM mlf.draft_selection AS ds
                      WHERE ds.draft_key =
                            dp.draft_key
                        AND ds.pick_id =
                            dp.pick_id
                  )
                ORDER BY
                    dp.round_number,
                    dp.slot_number,
                    dp.pick_id
                """,
                (
                    league_key,
                    season_year,
                    draft_key,
                ),
            )

            picks = [
                {
                    "pick_id":
                        str(row["pick_id"]),
                    "round_number":
                        int(row["round_number"]),
                    "slot_number":
                        int(row["slot_number"]),
                    "current_owner_team_key":
                        str(
                            row[
                                "current_owner_team_key"
                            ]
                        ),
                    "owner_team_name":
                        str(
                            row[
                                "owner_team_name"
                            ]
                        ),
                    "traded_flag":
                        bool(row["traded_flag"]),
                }
                for row in cur.fetchall()
            ]

    players: list[dict[str, object]] = []

    for key, player in player_map.items():
        player_key = str(key)

        rank_raw = getattr(
            player,
            "rank_value",
            None,
        )

        rank_value = (
            float(rank_raw)
            if rank_raw is not None
            else None
        )

        contract = contracts.get(
            player_key
        )

        players.append(
            {
                "yahoo_player_key":
                    player_key,
                "name": str(
                    getattr(
                        player,
                        "name",
                        None,
                    )
                    or player_key
                ),
                "rank_value":
                    rank_value,
                "contract_team_key": (
                    str(
                        contract["team_key"]
                    )
                    if (
                        contract
                        and contract[
                            "team_key"
                        ] is not None
                    )
                    else None
                ),
                "contract_years": (
                    int(
                        contract[
                            "years_remaining"
                        ]
                    )
                    if contract
                    else 0
                ),
            }
        )

    players.sort(
        key=lambda row: (
            row["rank_value"] is None,
            (
                row["rank_value"]
                if row["rank_value"]
                is not None
                else 999999999
            ),
            str(row["name"]).lower(),
            str(
                row["yahoo_player_key"]
            ),
        )
    )

    return {
        "draft_key": draft_key,
        "draft_status":
            str(draft["status"]),
        "selection_count":
            int(draft["selection_count"]),
        "teams": teams,
        "players": players,
        "picks": picks,
    }


def _build_trade_assets(
    *,
    team_a_key: str,
    team_b_key: str,
    team_a_gets: list[dict[str, object]],
    team_b_gets: list[dict[str, object]],
    valid_teams: set[str],
    valid_players: set[str],
    contracts: dict[
        str,
        dict[str, object],
    ],
) -> list[dict[str, object]]:
    team_a = str(
        team_a_key or ""
    ).strip()

    team_b = str(
        team_b_key or ""
    ).strip()

    if not team_a or not team_b:
        raise TradeRequestError(
            "Both trade teams are required."
        )

    if team_a == team_b:
        raise TradeRequestError(
            "Trade teams must be different."
        )

    if (
        team_a not in valid_teams
        or team_b not in valid_teams
    ):
        raise TradeRequestError(
            "Trade team is not in the "
            "active MLF season."
        )

    if (
        len(team_a_gets) > 20
        or len(team_b_gets) > 20
    ):
        raise TradeRequestError(
            "Trade contains too many assets."
        )

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def append_side(
        items: list[dict[str, object]],
        *,
        from_team: str,
        to_team: str,
    ) -> None:
        for item in items:
            asset_type = str(
                item.get("asset_type")
                or ""
            ).strip().upper()

            asset_id = str(
                item.get("asset_id")
                or ""
            ).strip()

            if asset_type not in {
                "PLAYER",
                "PICK",
            }:
                raise TradeRequestError(
                    "Unsupported trade asset type."
                )

            if not asset_id:
                raise TradeRequestError(
                    "Trade asset identity "
                    "is required."
                )

            identity = (
                asset_type,
                asset_id,
            )

            if identity in seen:
                raise TradeRequestError(
                    "Trade contains a "
                    "duplicate asset."
                )

            seen.add(identity)

            snapshot: dict[str, object] = {}

            if asset_type == "PLAYER":
                if asset_id not in valid_players:
                    raise TradeRequestError(
                        "Player is not in the "
                        "active MLF player universe."
                    )

                contract = contracts.get(
                    asset_id
                )

                snapshot[
                    "contract_years"
                ] = (
                    int(
                        contract[
                            "years_remaining"
                        ]
                    )
                    if contract
                    else 0
                )

            rows.append(
                {
                    "asset_type":
                        asset_type,
                    "asset_id":
                        asset_id,
                    "from_team_key":
                        from_team,
                    "to_team_key":
                        to_team,
                    "snapshot":
                        snapshot,
                }
            )

    # Team A receives assets FROM Team B.
    append_side(
        team_a_gets,
        from_team=team_b,
        to_team=team_a,
    )

    # Team B receives assets FROM Team A.
    append_side(
        team_b_gets,
        from_team=team_a,
        to_team=team_b,
    )

    if not rows:
        raise TradeRequestError(
            "Trade must contain at least "
            "one asset."
        )

    return rows


def _insert_trade_receipt(
    *,
    dsn: str,
    league_key: str,
    season_year: int,
    created_by: str,
    assets: list[dict[str, object]],
) -> tuple[str, int]:
    trade_id = str(uuid.uuid4())

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.trade (
                    trade_id,
                    league_key,
                    season_year,
                    created_at,
                    created_by,
                    notes
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    now(),
                    %s,
                    %s
                )
                """,
                (
                    trade_id,
                    league_key,
                    season_year,
                    created_by,
                    "commissioner_trade_builder",
                ),
            )

            asset_count = 0

            for asset in assets:
                cur.execute(
                    """
                    INSERT INTO public.trade_asset (
                        trade_asset_id,
                        trade_id,
                        asset_type,
                        asset_id,
                        from_team_key,
                        to_team_key,
                        snapshot_json,
                        created_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        now()
                    )
                    """,
                    (
                        str(uuid.uuid4()),
                        trade_id,
                        str(
                            asset[
                                "asset_type"
                            ]
                        ),
                        str(
                            asset[
                                "asset_id"
                            ]
                        ),
                        str(
                            asset[
                                "from_team_key"
                            ]
                        ),
                        str(
                            asset[
                                "to_team_key"
                            ]
                        ),
                        json.dumps(
                            asset.get(
                                "snapshot"
                            )
                            or {},
                            separators=(
                                ",",
                                ":",
                            ),
                        ),
                    ),
                )

                asset_count += 1

        conn.commit()

    return (
        trade_id,
        asset_count,
    )


def submit_commissioner_trade(
    *,
    payload: dict[str, object],
    created_by: str,
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        season_year,
        league_key,
    ) = _runtime_context()

    player_map = load_available_players(
        dsn
    )

    valid_players = {
        str(key)
        for key in player_map.keys()
    }

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT team_key
                FROM mlf.team
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            valid_teams = {
                str(row["team_key"])
                for row in cur.fetchall()
            }

            contracts = (
                _active_contract_map(
                    cur,
                    league_key=league_key,
                    season_year=season_year,
                )
            )

    assets = _build_trade_assets(
        team_a_key=str(
            payload.get("team_a_key")
            or ""
        ),
        team_b_key=str(
            payload.get("team_b_key")
            or ""
        ),
        team_a_gets=list(
            payload.get("team_a_gets")
            or []
        ),
        team_b_gets=list(
            payload.get("team_b_gets")
            or []
        ),
        valid_teams=valid_teams,
        valid_players=valid_players,
        contracts=contracts,
    )

    try:
        (
            player_updates,
            pick_updates,
            keeper_assignments,
        ) = apply_trade_assets_atomic(
            dsn=dsn,
            draft_key=draft_key,
            assets=assets,
            note=(
                "commissioner_trade_builder"
            ),
        )
    except psycopg.Error as exc:
        raise TradeConflict(
            "Canonical MLF trade was rejected."
        ) from exc

    receipt_written = False
    receipt_trade_id: str | None = None
    receipt_asset_count = 0
    receipt_warning: str | None = None

    try:
        (
            receipt_trade_id,
            receipt_asset_count,
        ) = _insert_trade_receipt(
            dsn=dsn,
            league_key=league_key,
            season_year=season_year,
            created_by=created_by,
            assets=assets,
        )

        receipt_written = True

    except Exception:
        # Canonical MLF state has already committed.
        # Never imply that a receipt failure means
        # the commissioner should blindly resubmit.
        receipt_warning = (
            "canonical_trade_applied_"
            "legacy_receipt_failed"
        )

    return {
        "player_updates":
            int(player_updates),
        "pick_updates":
            int(pick_updates),
        "keeper_assignments":
            int(keeper_assignments),
        "receipt_written":
            receipt_written,
        "receipt_trade_id":
            receipt_trade_id,
        "receipt_asset_count":
            int(receipt_asset_count),
        "receipt_warning":
            receipt_warning,
    }
