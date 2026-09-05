from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


LEAGUE_KEY = "469.l.41640"
SEASON_YEAR = 2026
DRAFT_KEY = "mlf_2026_preseason"

EXPECTED_TEAM_COUNT = 16
EXPECTED_PLAYER_COUNT = 2338
EXPECTED_PICK_COUNT = 400
EXPECTED_REAL_SELECTIONS = 256
EXPECTED_KEEPERS = 144
EXPECTED_CONTRACTS = 142
EXPECTED_ACTIVE_CONTRACTS = 135
EXPECTED_CONTRACT_OVERRIDES = 3
EXPECTED_PROSPECT_TAGS = 9
EXPECTED_QOS = 80
EXPECTED_PICK_TRADES = 2
EXPECTED_TRADES = 6
EXPECTED_TRADE_ASSETS = 15

EXPECTED_CONFLICTING_PICK_TRADES = {
    "R07-01": (
        "469.l.41640.t.1",
        "469.l.41640.t.12",
    ),
    "R07-02": (
        "469.l.41640.t.12",
        "469.l.41640.t.1",
    ),
}


def required_text(
    value: object,
    *,
    field: str,
) -> str:
    text = str(
        value
        if value is not None
        else ""
    ).strip()

    if not text:
        raise RuntimeError(
            f"Missing required {field}."
        )

    return text


def json_default(
    value: object,
) -> str:
    if isinstance(
        value,
        (
            datetime,
            date,
            Decimal,
            UUID,
        ),
    ):
        return str(value)

    raise TypeError(
        f"Unsupported fingerprint value: "
        f"{type(value)!r}"
    )


def source_fingerprint(
    snapshot: dict[str, Any],
) -> str:
    payload = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=json_default,
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        payload
    ).hexdigest()


def parse_legacy_timestamp(
    value: object,
) -> datetime:
    text = required_text(
        value,
        field="legacy timestamp",
    )

    parsed = datetime.fromisoformat(
        text
    )

    # Legacy DraftState timestamps are naive UTC ISO strings.
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    else:
        parsed = parsed.astimezone(
            timezone.utc
        )

    return parsed


def connect_kwargs(
    prefix: str,
    args: argparse.Namespace,
) -> dict[str, object]:
    password_env = getattr(
        args,
        f"{prefix}_password_env",
    )

    password = (
        os.environ.get(
            password_env
        )
        if password_env
        else None
    )

    result: dict[str, object] = {
        "host": getattr(
            args,
            f"{prefix}_host",
        ),
        "port": getattr(
            args,
            f"{prefix}_port",
        ),
        "dbname": getattr(
            args,
            f"{prefix}_db",
        ),
        "user": getattr(
            args,
            f"{prefix}_user",
        ),
        "row_factory": dict_row,
    }

    if password:
        result[
            "password"
        ] = password

    return result


def fetch_all(
    cur,
    query: str,
    params: tuple = (),
) -> list[dict]:
    cur.execute(
        query,
        params,
    )

    return list(
        cur.fetchall()
    )


def fetch_one(
    cur,
    query: str,
    params: tuple = (),
) -> dict:
    cur.execute(
        query,
        params,
    )

    row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "Expected one source row."
        )

    return dict(row)


def load_source_snapshot(
    args: argparse.Namespace,
) -> dict[str, Any]:
    conn = psycopg.connect(
        **connect_kwargs(
            "source",
            args,
        )
    )

    try:
        conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute(
                """
                BEGIN TRANSACTION
                    ISOLATION LEVEL REPEATABLE READ
                    READ ONLY
                """
            )

            state_row = fetch_one(
                cur,
                """
                SELECT
                    draft_key,
                    schema_version,
                    state_json,
                    state_sha256,
                    updated_at_utc
                FROM public.draftboard_state
                WHERE draft_key = %s
                """,
                (
                    DRAFT_KEY,
                ),
            )

            teams = fetch_all(
                cur,
                """
                SELECT
                    league_key,
                    season_year,
                    team_key,
                    team_name,
                    owner_name,
                    owner_guid,
                    team_id,
                    updated_at
                FROM public.yahoo_team_map
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY team_key
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            contracts = fetch_all(
                cur,
                """
                SELECT
                    league_key,
                    season_year,
                    team_key,
                    yahoo_player_key,
                    years_remaining,
                    note,
                    updated_at
                FROM public.contract
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY yahoo_player_key
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            overrides = fetch_all(
                cur,
                """
                SELECT
                    league_key,
                    season_year,
                    yahoo_player_key,
                    years_remaining,
                    yahoo_team_key,
                    yahoo_team_name,
                    note,
                    updated_at
                FROM public.contract_override
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY yahoo_player_key
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            prospect_tags = fetch_all(
                cur,
                """
                SELECT
                    league_key,
                    season_year,
                    team_key,
                    yahoo_player_key,
                    note,
                    updated_at
                FROM public.prospect_tag
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY yahoo_player_key
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            qualifying_offers = fetch_all(
                cur,
                """
                SELECT
                    league_key,
                    season_year,
                    team_key,
                    yahoo_player_key,
                    qo_level,
                    note,
                    updated_at
                FROM public.qualifying_offer
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY
                    team_key,
                    qo_level,
                    yahoo_player_key
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            qo_round_state = fetch_all(
                cur,
                """
                SELECT
                    league_key,
                    season_year,
                    current_round,
                    updated_at
                FROM public.qo_round_state
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            trades = fetch_all(
                cur,
                """
                SELECT
                    trade_id,
                    league_key,
                    season_year,
                    created_at,
                    created_by,
                    notes
                FROM public.trade
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY
                    created_at,
                    trade_id
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            trade_assets = fetch_all(
                cur,
                """
                SELECT
                    a.trade_asset_id,
                    a.trade_id,
                    a.asset_type,
                    a.asset_id,
                    a.from_team_key,
                    a.to_team_key,
                    a.snapshot_json,
                    a.created_at,
                    t.created_at AS trade_created_at
                FROM public.trade_asset a
                JOIN public.trade t
                  ON t.trade_id = a.trade_id
                WHERE t.league_key = %s
                  AND t.season_year = %s
                ORDER BY
                    t.created_at,
                    a.created_at,
                    a.trade_asset_id
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            cur.execute(
                "COMMIT"
            )

    finally:
        conn.close()

    snapshot = {
        "state_row": state_row,
        "teams": teams,
        "contracts": contracts,
        "overrides": overrides,
        "prospect_tags": prospect_tags,
        "qualifying_offers":
            qualifying_offers,
        "qo_round_state":
            qo_round_state,
        "trades": trades,
        "trade_assets": trade_assets,
    }

    validate_source_snapshot(
        snapshot
    )

    return snapshot


def effective_keeper_control(
    snapshot: dict[str, Any],
) -> tuple[
    dict[str, str],
    set[str],
]:
    base_by_player = {
        row[
            "yahoo_player_key"
        ]: row
        for row in snapshot[
            "contracts"
        ]
    }

    override_by_player = {
        row[
            "yahoo_player_key"
        ]: row
        for row in snapshot[
            "overrides"
        ]
    }

    active_contracts: dict[
        str,
        str
    ] = {}

    all_players = set(
        base_by_player
    ) | set(
        override_by_player
    )

    for player_key in all_players:
        base = base_by_player.get(
            player_key
        )

        override = (
            override_by_player.get(
                player_key
            )
        )

        if override is not None:
            years = int(
                override[
                    "years_remaining"
                ]
            )

            team_key = str(
                override.get(
                    "yahoo_team_key"
                )
                or ""
            ).strip()

        elif base is not None:
            years = int(
                base[
                    "years_remaining"
                ]
            )

            team_key = str(
                base.get(
                    "team_key"
                )
                or ""
            ).strip()

        else:
            raise AssertionError(
                player_key
            )

        if years > 0:
            if not team_key:
                raise RuntimeError(
                    "Active contract has no team: "
                    + player_key
                )

            active_contracts[
                player_key
            ] = team_key

    pt_players = {
        row[
            "yahoo_player_key"
        ]
        for row in snapshot[
            "prospect_tags"
        ]
    }

    overlap = (
        set(active_contracts)
        & pt_players
    )

    if overlap:
        raise RuntimeError(
            "Contract/PT overlap: "
            + ", ".join(
                sorted(overlap)
            )
        )

    return (
        active_contracts,
        pt_players,
    )


def validate_source_snapshot(
    snapshot: dict[str, Any],
) -> None:
    state_row = snapshot[
        "state_row"
    ]

    if state_row[
        "draft_key"
    ] != DRAFT_KEY:
        raise RuntimeError(
            "Wrong legacy draft key."
        )

    if str(
        state_row[
            "schema_version"
        ]
    ) != "1.0":
        raise RuntimeError(
            "Unexpected legacy schema version."
        )

    state = state_row[
        "state_json"
    ]

    if not isinstance(
        state,
        dict,
    ):
        raise RuntimeError(
            "Legacy state_json is not an object."
        )

    teams = snapshot[
        "teams"
    ]

    contracts = snapshot[
        "contracts"
    ]

    overrides = snapshot[
        "overrides"
    ]

    pts = snapshot[
        "prospect_tags"
    ]

    qos = snapshot[
        "qualifying_offers"
    ]

    trades = snapshot[
        "trades"
    ]

    trade_assets = snapshot[
        "trade_assets"
    ]

    if len(
        teams
    ) != EXPECTED_TEAM_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_TEAM_COUNT} teams; "
            f"found {len(teams)}."
        )

    if len(
        contracts
    ) != EXPECTED_CONTRACTS:
        raise RuntimeError(
            "Contract count changed."
        )

    if len(
        overrides
    ) != EXPECTED_CONTRACT_OVERRIDES:
        raise RuntimeError(
            "Contract override count changed."
        )

    if len(
        pts
    ) != EXPECTED_PROSPECT_TAGS:
        raise RuntimeError(
            "Prospect-tag count changed."
        )

    if len(
        qos
    ) != EXPECTED_QOS:
        raise RuntimeError(
            "QO count changed."
        )

    if len(
        trades
    ) != EXPECTED_TRADES:
        raise RuntimeError(
            "Trade count changed."
        )

    if len(
        trade_assets
    ) != EXPECTED_TRADE_ASSETS:
        raise RuntimeError(
            "Trade-asset count changed."
        )

    if len(
        snapshot[
            "qo_round_state"
        ]
    ) != 1:
        raise RuntimeError(
            "Expected one QO round-state row."
        )

    draft_order = state.get(
        "draft_order_team_keys_by_slot"
    )

    pick_order = state.get(
        "pick_order"
    )

    picks = state.get(
        "picks"
    )

    players = state.get(
        "players"
    )

    pick_log = state.get(
        "pick_log"
    )

    if (
        not isinstance(
            draft_order,
            list,
        )
        or len(
            draft_order
        ) != EXPECTED_TEAM_COUNT
        or len(
            set(
                draft_order
            )
        ) != EXPECTED_TEAM_COUNT
    ):
        raise RuntimeError(
            "Invalid legacy draft order."
        )

    team_keys = {
        row[
            "team_key"
        ]
        for row in teams
    }

    if set(
        draft_order
    ) != team_keys:
        raise RuntimeError(
            "Legacy draft order does not "
            "match Yahoo team map."
        )

    if (
        not isinstance(
            pick_order,
            list,
        )
        or len(
            pick_order
        ) != EXPECTED_PICK_COUNT
        or len(
            set(
                pick_order
            )
        ) != EXPECTED_PICK_COUNT
    ):
        raise RuntimeError(
            "Invalid legacy pick order."
        )

    if (
        not isinstance(
            picks,
            dict,
        )
        or len(
            picks
        ) != EXPECTED_PICK_COUNT
    ):
        raise RuntimeError(
            "Invalid legacy pick map."
        )

    if set(
        pick_order
    ) != set(
        picks
    ):
        raise RuntimeError(
            "pick_order/picks mismatch."
        )

    if (
        not isinstance(
            players,
            dict,
        )
        or len(
            players
        ) != EXPECTED_PLAYER_COUNT
    ):
        raise RuntimeError(
            "Legacy player universe changed."
        )

    if (
        not isinstance(
            pick_log,
            list,
        )
        or len(
            pick_log
        ) != EXPECTED_REAL_SELECTIONS
    ):
        raise RuntimeError(
            "Legacy pick log changed."
        )

    real_picks: dict[
        str,
        dict
    ] = {}

    placeholder_picks: dict[
        str,
        dict
    ] = {}

    for pick_id in pick_order:
        pick = picks[
            pick_id
        ]

        if pick.get(
            "pick_id"
        ) != pick_id:
            raise RuntimeError(
                "Pick ID mismatch: "
                + pick_id
            )

        round_number = int(
            pick[
                "round_number"
            ]
        )

        slot_number = int(
            pick[
                "slot"
            ]
        )

        if (
            round_number < 1
            or round_number > 25
            or slot_number < 1
            or slot_number > 16
        ):
            raise RuntimeError(
                "Invalid pick topology: "
                + pick_id
            )

        expected_column_team = (
            draft_order[
                slot_number - 1
            ]
        )

        if (
            pick.get(
                "original_team_key"
            )
            != expected_column_team
        ):
            raise RuntimeError(
                "Pick original team disagrees "
                "with authoritative draft order: "
                + pick_id
            )

        # Reconciled legacy board contains no
        # current-owner divergences.
        if (
            pick.get(
                "owner_team_key"
            )
            != pick.get(
                "original_team_key"
            )
        ):
            raise RuntimeError(
                "Unexpected legacy owner divergence: "
                + pick_id
            )

        player_key = (
            pick.get(
                "selected_player_key"
            )
        )

        selected_ts = (
            pick.get(
                "selected_ts_iso"
            )
        )

        if player_key and selected_ts:
            real_picks[
                pick_id
            ] = pick

        elif player_key and not selected_ts:
            placeholder_picks[
                pick_id
            ] = pick

        elif not player_key:
            raise RuntimeError(
                "Unexpected truly open legacy pick: "
                + pick_id
            )

    if len(
        real_picks
    ) != EXPECTED_REAL_SELECTIONS:
        raise RuntimeError(
            "Timestamped selection count changed."
        )

    if len(
        placeholder_picks
    ) != EXPECTED_KEEPERS:
        raise RuntimeError(
            "Keeper placeholder count changed."
        )

    log_by_pick: dict[
        str,
        dict
    ] = {}

    selected_players: set[
        str
    ] = set()

    for event in pick_log:
        pick_id = required_text(
            event.get(
                "pick_id"
            ),
            field="pick-log pick_id",
        )

        if pick_id in log_by_pick:
            raise RuntimeError(
                "Duplicate pick-log event: "
                + pick_id
            )

        if pick_id not in real_picks:
            raise RuntimeError(
                "Pick-log row without "
                "timestamped selection: "
                + pick_id
            )

        pick = real_picks[
            pick_id
        ]

        event_player = required_text(
            event.get(
                "player_key"
            ),
            field="pick-log player_key",
        )

        if (
            event_player
            != pick.get(
                "selected_player_key"
            )
        ):
            raise RuntimeError(
                "Pick-log player mismatch: "
                + pick_id
            )

        if (
            event.get(
                "owner_team_key"
            )
            != pick.get(
                "owner_team_key"
            )
        ):
            raise RuntimeError(
                "Pick-log owner mismatch: "
                + pick_id
            )

        if (
            parse_legacy_timestamp(
                event.get(
                    "ts_iso"
                )
            )
            != parse_legacy_timestamp(
                pick.get(
                    "selected_ts_iso"
                )
            )
        ):
            raise RuntimeError(
                "Pick-log timestamp mismatch: "
                + pick_id
            )

        if event_player in selected_players:
            raise RuntimeError(
                "Duplicate real selected player: "
                + event_player
            )

        selected_players.add(
            event_player
        )

        log_by_pick[
            pick_id
        ] = event

    if set(
        log_by_pick
    ) != set(
        real_picks
    ):
        raise RuntimeError(
            "Pick-log/selection parity failed."
        )

    active_contracts, pt_players = (
        effective_keeper_control(
            snapshot
        )
    )

    if len(
        active_contracts
    ) != EXPECTED_ACTIVE_CONTRACTS:
        raise RuntimeError(
            "Effective active-contract count changed."
        )

    keeper_players = (
        set(active_contracts)
        | pt_players
    )

    if len(
        keeper_players
    ) != EXPECTED_KEEPERS:
        raise RuntimeError(
            "Effective keeper population changed."
        )

    overlap = (
        selected_players
        & keeper_players
    )

    if overlap:
        raise RuntimeError(
            "Real selection / keeper overlap: "
            + ", ".join(
                sorted(overlap)
            )
        )

    controlled_players = (
        keeper_players
        | {
            row[
                "yahoo_player_key"
            ]
            for row in qos
        }
        | selected_players
    )

    missing_players = (
        controlled_players
        - set(
            players
        )
    )

    if missing_players:
        raise RuntimeError(
            "Controlled/selected players absent "
            "from legacy universe: "
            + ", ".join(
                sorted(
                    missing_players
                )
            )
        )

    pick_assets = [
        row
        for row in trade_assets
        if str(
            row[
                "asset_type"
            ]
        ).upper() == "PICK"
    ]

    if len(
        pick_assets
    ) != EXPECTED_PICK_TRADES:
        raise RuntimeError(
            "Pick-trade count changed."
        )

    by_pick = {
        row[
            "asset_id"
        ]: row
        for row in pick_assets
    }

    if set(
        by_pick
    ) != set(
        EXPECTED_CONFLICTING_PICK_TRADES
    ):
        raise RuntimeError(
            "Unexpected traded-pick identity set."
        )

    for (
        pick_id,
        (
            expected_from,
            expected_to,
        ),
    ) in EXPECTED_CONFLICTING_PICK_TRADES.items():
        trade_row = by_pick[
            pick_id
        ]

        if (
            trade_row[
                "from_team_key"
            ]
            != expected_from
            or trade_row[
                "to_team_key"
            ]
            != expected_to
        ):
            raise RuntimeError(
                "Traded-pick receipt changed: "
                + pick_id
            )

        pick = picks[
            pick_id
        ]

        if (
            pick[
                "owner_team_key"
            ]
            != expected_from
        ):
            raise RuntimeError(
                "Executed-state ownership changed "
                "for reconciled trade conflict: "
                + pick_id
            )

        if (
            pick[
                "owner_team_key"
            ]
            == expected_to
        ):
            raise RuntimeError(
                "Expected trade conflict disappeared: "
                + pick_id
            )

    qo_counts: dict[
        int,
        int
    ] = {}

    for row in qos:
        level = int(
            row[
                "qo_level"
            ]
        )

        qo_counts[
            level
        ] = (
            qo_counts.get(
                level,
                0,
            )
            + 1
        )

    if qo_counts != {
        1: 16,
        2: 16,
        3: 16,
        4: 16,
        5: 16,
    }:
        raise RuntimeError(
            "Five-level QO baseline changed."
        )


def target_is_empty(
    cur,
) -> None:
    migration_count = fetch_one(
        cur,
        """
        SELECT count(*) AS row_count
        FROM mlf.schema_migration
        """,
    )[
        "row_count"
    ]

    if int(
        migration_count
    ) != 8:
        raise RuntimeError(
            "Target must contain exactly "
            "migrations 001-008 before data migration."
        )

    for table_name in (
        "team",
        "draft",
        "draft_pick",
        "draft_pick_trade",
        "draft_selection",
        "player_universe",
        "contract",
        "contract_override",
        "prospect_tag",
        "qualifying_offer",
        "qo_round_state",
        "draft_runtime",
        "draft_qo_current",
        "draft_keeper_assignment",
    ):
        row = fetch_one(
            cur,
            f"""
            SELECT count(*) AS row_count
            FROM mlf.{table_name}
            """,
        )

        if int(
            row[
                "row_count"
            ]
        ) != 0:
            raise RuntimeError(
                "Target is not empty: "
                + table_name
            )


def migrate_snapshot(
    args: argparse.Namespace,
    snapshot: dict[str, Any],
) -> None:
    state = snapshot[
        "state_row"
    ][
        "state_json"
    ]

    picks: dict[
        str,
        dict
    ] = state[
        "picks"
    ]

    pick_order: list[
        str
    ] = state[
        "pick_order"
    ]

    draft_order: list[
        str
    ] = state[
        "draft_order_team_keys_by_slot"
    ]

    players: dict[
        str,
        dict
    ] = state[
        "players"
    ]

    pick_log: list[
        dict
    ] = state[
        "pick_log"
    ]

    clock: dict = state.get(
        "clock"
    ) or {}

    active_contracts, pt_players = (
        effective_keeper_control(
            snapshot
        )
    )

    expected_placeholder_map: dict[
        str,
        tuple[str, str, str]
    ] = {}

    for pick_id in pick_order:
        pick = picks[
            pick_id
        ]

        player_key = pick.get(
            "selected_player_key"
        )

        selected_ts = pick.get(
            "selected_ts_iso"
        )

        if player_key and not selected_ts:
            if player_key in pt_players:
                keeper_kind = "PT"

            elif player_key in active_contracts:
                keeper_kind = "CONTRACT"

            else:
                raise RuntimeError(
                    "Legacy placeholder is not "
                    "effective contract/PT: "
                    + pick_id
                )

            expected_placeholder_map[
                pick_id
            ] = (
                pick[
                    "owner_team_key"
                ],
                player_key,
                keeper_kind,
            )

    conn = psycopg.connect(
        **connect_kwargs(
            "target",
            args,
        )
    )

    try:
        with conn:
            with conn.cursor() as cur:
                schema_exists = fetch_one(
                    cur,
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.schemata
                        WHERE schema_name = 'mlf'
                    ) AS exists
                    """,
                )[
                    "exists"
                ]

                if not schema_exists:
                    raise RuntimeError(
                        "Target mlf schema does not exist."
                    )

                target_is_empty(
                    cur
                )

                team_rows = []

                for row in snapshot[
                    "teams"
                ]:
                    team_rows.append(
                        (
                            LEAGUE_KEY,
                            SEASON_YEAR,
                            row[
                                "team_key"
                            ],
                            row[
                                "team_name"
                            ],
                            row.get(
                                "owner_name"
                            ),
                            row.get(
                                "owner_guid"
                            ),
                        )
                    )

                cur.executemany(
                    """
                    INSERT INTO mlf.team (
                        league_key,
                        season_year,
                        team_key,
                        team_name,
                        owner_name,
                        owner_guid
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    team_rows,
                )

                player_rows = []

                for (
                    player_key,
                    player,
                ) in sorted(
                    players.items()
                ):
                    positions = (
                        player.get(
                            "positions"
                        )
                        or []
                    )

                    primary_position = (
                        str(
                            positions[
                                0
                            ]
                        )
                        if positions
                        else None
                    )

                    rank_value = player.get(
                        "rank_value"
                    )

                    if rank_value in (
                        "",
                        None,
                    ):
                        normalized_rank = None

                    else:
                        normalized_rank = Decimal(
                            str(
                                rank_value
                            )
                        )

                    player_rows.append(
                        (
                            LEAGUE_KEY,
                            SEASON_YEAR,
                            player_key,
                            player.get(
                                "name"
                            ),
                            primary_position,
                            True,
                            normalized_rank,
                        )
                    )

                cur.executemany(
                    """
                    INSERT INTO mlf.player_universe (
                        league_key,
                        season_year,
                        yahoo_player_key,
                        player_name,
                        primary_position,
                        is_active,
                        rank_value
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    player_rows,
                )

                cur.execute(
                    """
                    INSERT INTO mlf.draft (
                        draft_key,
                        league_key,
                        season_year,
                        draft_label,
                        manager_count,
                        rounds_total,
                        qo_rounds,
                        first_standard_round,
                        draft_order_mode,
                        status
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        16,
                        25,
                        5,
                        6,
                        'straight',
                        'setup'
                    )
                    """,
                    (
                        DRAFT_KEY,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        "MLF 2026 Preseason",
                    ),
                )

                pick_trade_ids = {
                    row[
                        "asset_id"
                    ]
                    for row in snapshot[
                        "trade_assets"
                    ]
                    if str(
                        row[
                            "asset_type"
                        ]
                    ).upper() == "PICK"
                }

                pick_rows = []

                for pick_id in pick_order:
                    pick = picks[
                        pick_id
                    ]

                    round_number = int(
                        pick[
                            "round_number"
                        ]
                    )

                    slot_number = int(
                        pick[
                            "slot"
                        ]
                    )

                    column_team_key = (
                        draft_order[
                            slot_number - 1
                        ]
                    )

                    owner_team_key = (
                        pick[
                            "owner_team_key"
                        ]
                    )

                    pick_type = str(
                        pick[
                            "round_type"
                        ]
                    ).upper()

                    round_label = (
                        f"QO{round_number}"
                        if round_number <= 5
                        else f"R{round_number:02d}"
                    )

                    ownership_note = None

                    if pick_id in pick_trade_ids:
                        ownership_note = (
                            "Legacy executed DraftState "
                            "ownership is canonical. "
                            "A conflicting pre-draft trade "
                            "receipt is preserved in "
                            "mlf.draft_pick_trade for audit."
                        )

                    pick_rows.append(
                        (
                            DRAFT_KEY,
                            pick_id,
                            round_number,
                            slot_number,
                            round_label,
                            pick_type,
                            column_team_key,
                            owner_team_key,
                            False,
                            ownership_note,
                        )
                    )

                cur.executemany(
                    """
                    INSERT INTO mlf.draft_pick (
                        draft_key,
                        pick_id,
                        round_number,
                        slot_number,
                        round_label,
                        pick_type,
                        column_team_key,
                        current_owner_team_key,
                        traded_flag,
                        ownership_note
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    pick_rows,
                )

                contract_rows = [
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        row[
                            "team_key"
                        ],
                        row[
                            "yahoo_player_key"
                        ],
                        int(
                            row[
                                "years_remaining"
                            ]
                        ),
                        row.get(
                            "note"
                        ),
                        row[
                            "updated_at"
                        ],
                    )
                    for row in snapshot[
                        "contracts"
                    ]
                ]

                cur.executemany(
                    """
                    INSERT INTO mlf.contract (
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        years_remaining,
                        note,
                        updated_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    contract_rows,
                )

                override_rows = []

                for row in snapshot[
                    "overrides"
                ]:
                    team_key = str(
                        row.get(
                            "yahoo_team_key"
                        )
                        or ""
                    ).strip()

                    override_rows.append(
                        (
                            LEAGUE_KEY,
                            SEASON_YEAR,
                            row[
                                "yahoo_player_key"
                            ],
                            (
                                team_key
                                if team_key
                                else None
                            ),
                            int(
                                row[
                                    "years_remaining"
                                ]
                            ),
                            row.get(
                                "note"
                            ),
                            row[
                                "updated_at"
                            ],
                        )
                    )

                cur.executemany(
                    """
                    INSERT INTO mlf.contract_override (
                        league_key,
                        season_year,
                        yahoo_player_key,
                        team_key,
                        years_remaining,
                        note,
                        updated_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    override_rows,
                )

                pt_rows = [
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        row[
                            "team_key"
                        ],
                        row[
                            "yahoo_player_key"
                        ],
                        row.get(
                            "note"
                        ),
                        row[
                            "updated_at"
                        ],
                    )
                    for row in snapshot[
                        "prospect_tags"
                    ]
                ]

                cur.executemany(
                    """
                    INSERT INTO mlf.prospect_tag (
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        note,
                        updated_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    pt_rows,
                )

                qo_rows = [
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        row[
                            "team_key"
                        ],
                        row[
                            "yahoo_player_key"
                        ],
                        int(
                            row[
                                "qo_level"
                            ]
                        ),
                        row.get(
                            "note"
                        ),
                        row[
                            "updated_at"
                        ],
                    )
                    for row in snapshot[
                        "qualifying_offers"
                    ]
                ]

                cur.executemany(
                    """
                    INSERT INTO mlf.qualifying_offer (
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        qo_level,
                        note,
                        updated_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    qo_rows,
                )

                qo_state = snapshot[
                    "qo_round_state"
                ][
                    0
                ]

                cur.execute(
                    """
                    INSERT INTO mlf.qo_round_state (
                        league_key,
                        season_year,
                        current_round,
                        updated_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        int(
                            qo_state[
                                "current_round"
                            ]
                        ),
                        qo_state[
                            "updated_at"
                        ],
                    ),
                )

                pick_assets = [
                    row
                    for row in snapshot[
                        "trade_assets"
                    ]
                    if str(
                        row[
                            "asset_type"
                        ]
                    ).upper() == "PICK"
                ]

                for (
                    audit_id,
                    row,
                ) in enumerate(
                    pick_assets,
                    start=1,
                ):
                    note = (
                        "Legacy source trade "
                        f"{row['trade_id']}. "
                        "Audit receipt preserved only; "
                        "executed DraftState ownership "
                        "is canonical for migration."
                    )

                    cur.execute(
                        """
                        INSERT INTO mlf.draft_pick_trade (
                            trade_id,
                            draft_key,
                            pick_id,
                            from_team_key,
                            to_team_key,
                            trade_date,
                            note
                        )
                        VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        """,
                        (
                            audit_id,
                            DRAFT_KEY,
                            row[
                                "asset_id"
                            ],
                            row[
                                "from_team_key"
                            ],
                            row[
                                "to_team_key"
                            ],
                            row[
                                "trade_created_at"
                            ].date(),
                            note,
                        ),
                    )

                runtime_result = fetch_one(
                    cur,
                    """
                    SELECT
                        mlf.initialize_draft_runtime(
                            %s
                        ) AS first_pick_id
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                if (
                    runtime_result[
                        "first_pick_id"
                    ]
                    != "QO1-01"
                ):
                    raise RuntimeError(
                        "Unexpected initial runtime pick."
                    )

                seeded = fetch_one(
                    cur,
                    """
                    SELECT
                        mlf.seed_draft_qo_current(
                            %s
                        ) AS seeded
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                if int(
                    seeded[
                        "seeded"
                    ]
                ) != EXPECTED_QOS:
                    raise RuntimeError(
                        "QO seed count mismatch."
                    )

                selection_rows = []

                for event in pick_log:
                    event_id = required_text(
                        event.get(
                            "event_id"
                        ),
                        field="event_id",
                    )

                    selection_rows.append(
                        (
                            DRAFT_KEY,
                            event[
                                "pick_id"
                            ],
                            event[
                                "owner_team_key"
                            ],
                            event[
                                "player_key"
                            ],
                            event[
                                "pick_kind"
                            ],
                            parse_legacy_timestamp(
                                event[
                                    "ts_iso"
                                ]
                            ),
                            "legacy_pick_log",
                            (
                                "Migrated from legacy "
                                f"pick event {event_id}"
                            ),
                        )
                    )

                cur.executemany(
                    """
                    INSERT INTO mlf.draft_selection (
                        draft_key,
                        pick_id,
                        selecting_team_key,
                        yahoo_player_key,
                        pick_kind,
                        selected_at_utc,
                        selected_by,
                        note
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    selection_rows,
                )

                fetch_one(
                    cur,
                    """
                    SELECT
                        mlf.rebuild_draft_qo_current(
                            %s
                        ) AS rebuilt
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                keeper_result = fetch_one(
                    cur,
                    """
                    SELECT
                        mlf.rebuild_draft_keeper_assignments(
                            %s
                        ) AS keeper_count
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                if int(
                    keeper_result[
                        "keeper_count"
                    ]
                ) != EXPECTED_KEEPERS:
                    raise RuntimeError(
                        "Keeper rebuild count mismatch."
                    )

                seconds_per_pick = int(
                    clock.get(
                        "seconds_per_pick"
                    )
                    or 86400
                )

                weekends_count = bool(
                    clock.get(
                        "weekends_count",
                        False,
                    )
                )

                auto_advance = bool(
                    clock.get(
                        "auto_advance",
                        True,
                    )
                )

                timezone_name = str(
                    clock.get(
                        "timezone"
                    )
                    or "America/New_York"
                )

                cur.execute(
                    """
                    UPDATE mlf.draft_runtime
                    SET
                        current_pick_id = NULL,
                        auto_advance = %s,
                        is_running = false,
                        pick_started_at_utc = NULL,
                        pick_paused_at_utc = NULL,
                        elapsed_paused_seconds = 0,
                        seconds_per_pick = %s,
                        weekends_count = %s,
                        timezone_name = %s,
                        qo_state_seeded = true,
                        updated_at_utc = now()
                    WHERE draft_key = %s
                    """,
                    (
                        auto_advance,
                        seconds_per_pick,
                        weekends_count,
                        timezone_name,
                        DRAFT_KEY,
                    ),
                )

                cur.execute(
                    """
                    UPDATE mlf.draft
                    SET
                        status = 'complete',
                        updated_at_utc = now()
                    WHERE draft_key = %s
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO mlf.schema_migration (
                        migration_version,
                        description
                    )
                    VALUES (
                        '010',
                        'Migrate reconciled real MLF 2026 state'
                    )
                    """
                )

                # ------------------------------------------------
                # Exact historical selection verification.
                # ------------------------------------------------

                target_selections = fetch_all(
                    cur,
                    """
                    SELECT
                        pick_id,
                        selecting_team_key,
                        yahoo_player_key,
                        pick_kind,
                        selected_at_utc
                    FROM mlf.draft_selection
                    WHERE draft_key = %s
                    ORDER BY
                        selected_at_utc,
                        pick_id
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                expected_selections = {
                    event[
                        "pick_id"
                    ]: (
                        event[
                            "owner_team_key"
                        ],
                        event[
                            "player_key"
                        ],
                        event[
                            "pick_kind"
                        ],
                        parse_legacy_timestamp(
                            event[
                                "ts_iso"
                            ]
                        ),
                    )
                    for event in pick_log
                }

                actual_selections = {
                    row[
                        "pick_id"
                    ]: (
                        row[
                            "selecting_team_key"
                        ],
                        row[
                            "yahoo_player_key"
                        ],
                        row[
                            "pick_kind"
                        ],
                        row[
                            "selected_at_utc"
                        ].astimezone(
                            timezone.utc
                        ),
                    )
                    for row in target_selections
                }

                if (
                    actual_selections
                    != expected_selections
                ):
                    raise RuntimeError(
                        "Historical selection replay "
                        "does not exactly match legacy "
                        "pick log."
                    )

                # ------------------------------------------------
                # Exact keeper-placeholder verification.
                # ------------------------------------------------

                target_keepers = fetch_all(
                    cur,
                    """
                    SELECT
                        pick_id,
                        team_key,
                        yahoo_player_key,
                        keeper_kind
                    FROM mlf.draft_keeper_assignment
                    WHERE draft_key = %s
                    ORDER BY pick_id
                    """,
                    (
                        DRAFT_KEY,
                    ),
                )

                actual_placeholder_map = {
                    row[
                        "pick_id"
                    ]: (
                        row[
                            "team_key"
                        ],
                        row[
                            "yahoo_player_key"
                        ],
                        row[
                            "keeper_kind"
                        ],
                    )
                    for row in target_keepers
                }

                if (
                    actual_placeholder_map
                    != expected_placeholder_map
                ):
                    expected_keys = set(
                        expected_placeholder_map
                    )

                    actual_keys = set(
                        actual_placeholder_map
                    )

                    missing = sorted(
                        expected_keys
                        - actual_keys
                    )

                    extra = sorted(
                        actual_keys
                        - expected_keys
                    )

                    mismatched = sorted(
                        key
                        for key in (
                            expected_keys
                            & actual_keys
                        )
                        if (
                            expected_placeholder_map[
                                key
                            ]
                            != actual_placeholder_map[
                                key
                            ]
                        )
                    )

                    raise RuntimeError(
                        "Keeper assignment differs from "
                        "legacy placeholders. "
                        f"missing={missing[:10]} "
                        f"extra={extra[:10]} "
                        f"mismatched={mismatched[:10]}"
                    )

                summary = fetch_one(
                    cur,
                    """
                    SELECT
                        (
                            SELECT count(*)
                            FROM mlf.team
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS team_count,

                        (
                            SELECT count(*)
                            FROM mlf.player_universe
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS player_count,

                        (
                            SELECT count(*)
                            FROM mlf.draft_pick
                            WHERE draft_key = %s
                        ) AS pick_count,

                        (
                            SELECT count(*)
                            FROM mlf.draft_selection
                            WHERE draft_key = %s
                        ) AS selection_count,

                        (
                            SELECT count(*)
                            FROM mlf.draft_keeper_assignment
                            WHERE draft_key = %s
                        ) AS keeper_count,

                        (
                            SELECT count(*)
                            FROM mlf.contract
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS contract_count,

                        (
                            SELECT count(*)
                            FROM mlf.v_active_contract
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS active_contract_count,

                        (
                            SELECT count(*)
                            FROM mlf.contract_override
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS override_count,

                        (
                            SELECT count(*)
                            FROM mlf.prospect_tag
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS pt_count,

                        (
                            SELECT count(*)
                            FROM mlf.qualifying_offer
                            WHERE league_key = %s
                              AND season_year = %s
                        ) AS qo_count,

                        (
                            SELECT count(*)
                            FROM mlf.draft_pick_trade
                            WHERE draft_key = %s
                        ) AS pick_trade_count,

                        (
                            SELECT count(*)
                            FROM mlf.schema_migration
                        ) AS migration_count,

                        (
                            SELECT current_pick_id
                            FROM mlf.draft_runtime
                            WHERE draft_key = %s
                        ) AS current_pick_id,

                        (
                            SELECT is_running
                            FROM mlf.draft_runtime
                            WHERE draft_key = %s
                        ) AS is_running,

                        (
                            SELECT status
                            FROM mlf.draft
                            WHERE draft_key = %s
                        ) AS draft_status,

                        (
                            SELECT count(*)
                            FROM mlf.draft_qo_current
                            WHERE draft_key = %s
                        ) AS qo_current_count
                    """,
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        DRAFT_KEY,
                        DRAFT_KEY,
                        DRAFT_KEY,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        DRAFT_KEY,
                        DRAFT_KEY,
                        DRAFT_KEY,
                        DRAFT_KEY,
                        DRAFT_KEY,
                    ),
                )

                expected = {
                    "team_count":
                        EXPECTED_TEAM_COUNT,
                    "player_count":
                        EXPECTED_PLAYER_COUNT,
                    "pick_count":
                        EXPECTED_PICK_COUNT,
                    "selection_count":
                        EXPECTED_REAL_SELECTIONS,
                    "keeper_count":
                        EXPECTED_KEEPERS,
                    "contract_count":
                        EXPECTED_CONTRACTS,
                    "active_contract_count":
                        EXPECTED_ACTIVE_CONTRACTS,
                    "override_count":
                        EXPECTED_CONTRACT_OVERRIDES,
                    "pt_count":
                        EXPECTED_PROSPECT_TAGS,
                    "qo_count":
                        EXPECTED_QOS,
                    "pick_trade_count":
                        EXPECTED_PICK_TRADES,
                    "migration_count": 9,
                }

                for (
                    field,
                    expected_value,
                ) in expected.items():
                    actual_value = int(
                        summary[
                            field
                        ]
                    )

                    if (
                        actual_value
                        != expected_value
                    ):
                        raise RuntimeError(
                            f"{field}: expected "
                            f"{expected_value}; found "
                            f"{actual_value}."
                        )

                if (
                    summary[
                        "current_pick_id"
                    ]
                    is not None
                ):
                    raise RuntimeError(
                        "Completed migrated draft "
                        "still has a current pick."
                    )

                if bool(
                    summary[
                        "is_running"
                    ]
                ):
                    raise RuntimeError(
                        "Completed migrated draft "
                        "clock is still running."
                    )

                if (
                    summary[
                        "draft_status"
                    ]
                    != "complete"
                ):
                    raise RuntimeError(
                        "Migrated draft is not complete."
                    )

                accounted = (
                    int(
                        summary[
                            "selection_count"
                        ]
                    )
                    + int(
                        summary[
                            "keeper_count"
                        ]
                    )
                )

                if accounted != EXPECTED_PICK_COUNT:
                    raise RuntimeError(
                        "Draft accounting is not 400/400."
                    )

                print(
                    "TARGET_TEAM_COUNT="
                    + str(
                        summary[
                            "team_count"
                        ]
                    )
                )

                print(
                    "TARGET_PLAYER_COUNT="
                    + str(
                        summary[
                            "player_count"
                        ]
                    )
                )

                print(
                    "TARGET_PICK_COUNT="
                    + str(
                        summary[
                            "pick_count"
                        ]
                    )
                )

                print(
                    "TARGET_REAL_SELECTIONS="
                    + str(
                        summary[
                            "selection_count"
                        ]
                    )
                )

                print(
                    "TARGET_KEEPER_ASSIGNMENTS="
                    + str(
                        summary[
                            "keeper_count"
                        ]
                    )
                )

                print(
                    "TARGET_QO_CURRENT_COUNT="
                    + str(
                        summary[
                            "qo_current_count"
                        ]
                    )
                )

                print(
                    "TARGET_DRAFT_ACCOUNTING=400/400"
                )

                print(
                    "HISTORICAL_SELECTION_PARITY=PASS"
                )

                print(
                    "KEEPER_PLACEHOLDER_PARITY=PASS"
                )

                print(
                    "CONFLICTING_PICK_TRADES="
                    "AUDIT_ONLY"
                )

                print(
                    "REAL_MLF_DATA_MIGRATION=PASS"
                )

    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=(
            "apply",
            "fingerprint",
        ),
        required=True,
    )

    for prefix in (
        "source",
        "target",
    ):
        required = (
            prefix == "source"
            or True
        )

        parser.add_argument(
            f"--{prefix}-host",
            required=(
                required
                if prefix == "source"
                else False
            ),
        )

        parser.add_argument(
            f"--{prefix}-port",
            type=int,
            default=5432,
        )

        parser.add_argument(
            f"--{prefix}-db",
            required=(
                required
                if prefix == "source"
                else False
            ),
        )

        parser.add_argument(
            f"--{prefix}-user",
            required=(
                required
                if prefix == "source"
                else False
            ),
        )

        parser.add_argument(
            f"--{prefix}-password-env",
            default=(
                "SOURCE_PASSWORD"
                if prefix == "source"
                else "TARGET_PASSWORD"
            ),
        )

    parser.add_argument(
        "--expected-source-fingerprint",
    )

    args = parser.parse_args()

    if args.mode == "apply":
        for field in (
            "target_host",
            "target_db",
            "target_user",
        ):
            if not getattr(
                args,
                field,
            ):
                parser.error(
                    f"--{field.replace('_', '-')} "
                    "is required in apply mode"
                )

    return args


def main() -> None:
    args = parse_args()

    snapshot = load_source_snapshot(
        args
    )

    fingerprint = source_fingerprint(
        snapshot
    )

    print(
        "SOURCE_FINGERPRINT="
        + fingerprint
    )

    print(
        "SOURCE_STATE_SHA256="
        + str(
            snapshot[
                "state_row"
            ].get(
                "state_sha256"
            )
            or ""
        )
    )

    print(
        "SOURCE_VALIDATION=PASS"
    )

    expected = (
        args.expected_source_fingerprint
    )

    if (
        expected
        and fingerprint != expected
    ):
        raise RuntimeError(
            "Source fingerprint changed. "
            f"expected={expected} "
            f"actual={fingerprint}"
        )

    if args.mode == "fingerprint":
        print(
            "SOURCE_FINGERPRINT_ONLY=PASS"
        )

        return

    migrate_snapshot(
        args,
        snapshot,
    )

    print(
        "PHASE4_REAL_MLF_MIGRATION_RUNNER=PASS"
    )


if __name__ == "__main__":
    main()
