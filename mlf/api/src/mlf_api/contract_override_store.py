from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from draftboard.data.db_players import (
    load_available_players,
)
from draftboard.data.draft_runtime import (
    upsert_contract_override_atomic,
)
from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


class ContractOverrideRequestError(ValueError):
    pass


class ContractOverrideConflict(RuntimeError):
    pass


def _context(
) -> tuple[str, str, str, int]:
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
            "Draft key is unavailable."
        )

    if not league_key:
        raise RuntimeError(
            "League key is unavailable."
        )

    return (
        dsn,
        draft_key,
        league_key,
        season_year,
    )


def _optional_int(
    value: object,
) -> int | None:
    if value is None:
        return None

    return int(value)


def _rank_value(
    value: object,
) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _positions(
    player: object | None,
    fallback: object,
) -> list[str]:
    values: list[str] = []

    if player is not None:
        raw_positions = (
            getattr(
                player,
                "positions",
                None,
            )
            or []
        )

        for item in raw_positions:
            value = str(
                getattr(
                    item,
                    "value",
                    item,
                )
                or ""
            ).strip()

            if value:
                values.append(value)

    if not values:
        text = str(
            fallback or ""
        ).strip()

        if text:
            for part in (
                text
                .replace(",", "/")
                .split("/")
            ):
                value = part.strip()

                if value:
                    values.append(value)

    return values


def get_commissioner_contract_override_state(
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        league_key,
        season_year,
    ) = _context()

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
                        FROM mlf.draft_selection ds
                        WHERE ds.draft_key = d.draft_key
                    ) AS selection_count
                FROM mlf.draft d
                WHERE d.draft_key = %s
                """,
                (
                    draft_key,
                ),
            )

            draft_row = cur.fetchone()

            if draft_row is None:
                raise RuntimeError(
                    "Current MLF draft was not found."
                )

            cur.execute(
                """
                SELECT
                    t.team_key,
                    t.team_name
                FROM mlf.team t
                WHERE t.league_key = %s
                  AND t.season_year = %s
                ORDER BY
                    t.team_name,
                    t.team_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            team_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    pu.yahoo_player_key,
                    pu.player_name,
                    pu.primary_position,

                    ac.team_key
                        AS active_contract_team_key,
                    ac.years_remaining
                        AS active_contract_years_remaining,
                    ac.effective_source,

                    pt.team_key
                        AS prospect_team_key,

                    co.team_key
                        AS override_team_key,
                    co.years_remaining
                        AS override_years_remaining,
                    co.note
                        AS override_note,

                    base.team_key
                        AS base_contract_team_key,
                    base.years_remaining
                        AS base_contract_years_remaining

                FROM mlf.player_universe pu

                LEFT JOIN mlf.v_active_contract ac
                  ON ac.league_key = pu.league_key
                 AND ac.season_year = pu.season_year
                 AND ac.yahoo_player_key =
                        pu.yahoo_player_key

                LEFT JOIN mlf.prospect_tag pt
                  ON pt.league_key = pu.league_key
                 AND pt.season_year = pu.season_year
                 AND pt.yahoo_player_key =
                        pu.yahoo_player_key

                LEFT JOIN mlf.contract_override co
                  ON co.league_key = pu.league_key
                 AND co.season_year = pu.season_year
                 AND co.yahoo_player_key =
                        pu.yahoo_player_key

                LEFT JOIN mlf.contract base
                  ON base.league_key = pu.league_key
                 AND base.season_year = pu.season_year
                 AND base.yahoo_player_key =
                        pu.yahoo_player_key

                WHERE pu.league_key = %s
                  AND pu.season_year = %s
                  AND pu.is_active = true

                ORDER BY
                    pu.player_name,
                    pu.yahoo_player_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            player_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    co.yahoo_player_key,
                    co.team_key,
                    co.years_remaining,
                    co.note,
                    co.updated_at_utc,
                    pu.player_name
                FROM mlf.contract_override co
                LEFT JOIN mlf.player_universe pu
                  ON pu.league_key = co.league_key
                 AND pu.season_year = co.season_year
                 AND pu.yahoo_player_key =
                        co.yahoo_player_key
                WHERE co.league_key = %s
                  AND co.season_year = %s
                ORDER BY
                    COALESCE(
                        pu.player_name,
                        co.yahoo_player_key
                    ),
                    co.yahoo_player_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            override_rows = cur.fetchall()

    teams = [
        {
            "team_key":
                str(row["team_key"]),
            "team_name":
                str(
                    row["team_name"]
                    or row["team_key"]
                ),
        }
        for row in team_rows
    ]

    team_names = {
        row["team_key"]:
            row["team_name"]
        for row in teams
    }

    players: list[dict[str, object]] = []

    for row in player_rows:
        player_key = str(
            row["yahoo_player_key"]
        )

        available = player_map.get(
            player_key
        )

        name = str(
            getattr(
                available,
                "name",
                "",
            )
            or row["player_name"]
            or player_key
        )

        mlb_team = str(
            getattr(
                available,
                "mlb_team",
                "",
            )
            or ""
        )

        rank = _rank_value(
            getattr(
                available,
                "rank_value",
                None,
            )
        )

        active_team = (
            str(
                row[
                    "active_contract_team_key"
                ]
            )
            if row[
                "active_contract_team_key"
            ] is not None
            else None
        )

        prospect_team = (
            str(
                row["prospect_team_key"]
            )
            if row[
                "prospect_team_key"
            ] is not None
            else None
        )

        override_years = _optional_int(
            row[
                "override_years_remaining"
            ]
        )

        players.append(
            {
                "yahoo_player_key":
                    player_key,
                "name":
                    name,
                "mlb_team":
                    mlb_team,
                "positions":
                    _positions(
                        available,
                        row[
                            "primary_position"
                        ],
                    ),
                "rank_value":
                    rank,

                "active_contract_team_key":
                    active_team,
                "active_contract_team_name":
                    (
                        team_names.get(
                            active_team,
                            active_team,
                        )
                        if active_team
                        else None
                    ),
                "active_contract_years_remaining":
                    _optional_int(
                        row[
                            "active_contract_years_remaining"
                        ]
                    ),
                "effective_source":
                    (
                        str(
                            row[
                                "effective_source"
                            ]
                        )
                        if row[
                            "effective_source"
                        ] is not None
                        else None
                    ),

                "prospect_team_key":
                    prospect_team,
                "prospect_team_name":
                    (
                        team_names.get(
                            prospect_team,
                            prospect_team,
                        )
                        if prospect_team
                        else None
                    ),

                "override_exists":
                    override_years
                    is not None,
                "override_team_key":
                    (
                        str(
                            row[
                                "override_team_key"
                            ]
                        )
                        if row[
                            "override_team_key"
                        ] is not None
                        else None
                    ),
                "override_years_remaining":
                    override_years,
                "override_note":
                    (
                        str(
                            row[
                                "override_note"
                            ]
                        )
                        if row[
                            "override_note"
                        ] is not None
                        else None
                    ),

                "base_contract_team_key":
                    (
                        str(
                            row[
                                "base_contract_team_key"
                            ]
                        )
                        if row[
                            "base_contract_team_key"
                        ] is not None
                        else None
                    ),
                "base_contract_years_remaining":
                    _optional_int(
                        row[
                            "base_contract_years_remaining"
                        ]
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
                else 999999
            ),
            str(row["name"]).lower(),
            str(
                row["yahoo_player_key"]
            ),
        )
    )

    player_names = {
        str(row["yahoo_player_key"]):
            str(row["name"])
        for row in players
    }

    overrides: list[
        dict[str, object]
    ] = []

    for row in override_rows:
        player_key = str(
            row["yahoo_player_key"]
        )

        team_key = (
            str(row["team_key"])
            if row["team_key"]
            is not None
            else None
        )

        updated_at = (
            row["updated_at_utc"]
        )

        overrides.append(
            {
                "yahoo_player_key":
                    player_key,
                "player_name":
                    player_names.get(
                        player_key,
                        str(
                            row[
                                "player_name"
                            ]
                            or player_key
                        ),
                    ),
                "team_key":
                    team_key,
                "team_name":
                    (
                        team_names.get(
                            team_key,
                            team_key,
                        )
                        if team_key
                        else None
                    ),
                "years_remaining":
                    int(
                        row[
                            "years_remaining"
                        ]
                    ),
                "note":
                    (
                        str(row["note"])
                        if row["note"]
                        is not None
                        else None
                    ),
                "updated_at_utc":
                    (
                        updated_at.isoformat()
                        if updated_at
                        is not None
                        else None
                    ),
            }
        )

    active_contract_count = sum(
        1
        for row in players
        if row[
            "active_contract_years_remaining"
        ] is not None
    )

    void_override_count = sum(
        1
        for row in overrides
        if int(
            row["years_remaining"]
        ) == 0
    )

    return {
        "draft_key":
            draft_key,
        "draft_status":
            str(draft_row["status"]),
        "selection_count":
            int(
                draft_row[
                    "selection_count"
                ]
            ),
        "can_edit":
            True,
        "lock_reason":
            None,
        "active_contract_count":
            active_contract_count,
        "override_count":
            len(overrides),
        "void_override_count":
            void_override_count,
        "teams":
            teams,
        "players":
            players,
        "overrides":
            overrides,
    }


def _find_player(
    state: dict[str, object],
    player_key: str,
) -> dict[str, object] | None:
    return next(
        (
            row
            for row in state["players"]
            if str(
                row["yahoo_player_key"]
            ) == player_key
        ),
        None,
    )


def save_commissioner_contract_override(
    *,
    yahoo_player_key: str,
    team_key: str | None,
    years_remaining: int,
    note: str | None,
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        _league_key,
        _season_year,
    ) = _context()

    state = (
        get_commissioner_contract_override_state()
    )

    player_key = str(
        yahoo_player_key or ""
    ).strip()

    if not player_key:
        raise ContractOverrideRequestError(
            "Player is required."
        )

    player = _find_player(
        state,
        player_key,
    )

    if player is None:
        raise ContractOverrideRequestError(
            "Player is not in the active "
            "MLF player universe."
        )

    years = int(
        years_remaining
    )

    if years < 0 or years > 5:
        raise ContractOverrideRequestError(
            "Contract years must be "
            "between 0 and 5."
        )

    team_text = str(
        team_key or ""
    ).strip()

    team: str | None = (
        team_text
        or None
    )

    if years > 0:
        if team is None:
            raise ContractOverrideRequestError(
                "Active contract override "
                "requires a team."
            )

        valid_team_keys = {
            str(row["team_key"])
            for row in state["teams"]
        }

        if team not in valid_team_keys:
            raise ContractOverrideRequestError(
                "Unknown MLF team."
            )

        if (
            player[
                "prospect_team_key"
            ]
            is not None
        ):
            raise ContractOverrideRequestError(
                "Player has a Prospect Tag. "
                "Remove the PT in Step 5 "
                "before assigning a contract."
            )

        action = "set"

    else:
        team = None

        if (
            player[
                "active_contract_years_remaining"
            ]
            is None
        ):
            raise ContractOverrideRequestError(
                "Player does not have an "
                "active effective contract "
                "to void."
            )

        action = "void"

    note_text = str(
        note or ""
    ).strip()

    if len(note_text) > 500:
        raise ContractOverrideRequestError(
            "Contract override note must "
            "be 500 characters or fewer."
        )

    try:
        keeper_assignments = (
            upsert_contract_override_atomic(
                dsn=dsn,
                draft_key=draft_key,
                yahoo_player_key=
                    player_key,
                team_key=team,
                years_remaining=years,
                note=(
                    note_text
                    or None
                ),
            )
        )
    except ValueError as exc:
        raise ContractOverrideRequestError(
            str(exc)
        ) from exc
    except (
        RuntimeError,
        psycopg.Error,
    ) as exc:
        raise ContractOverrideConflict(
            str(exc)
        ) from exc

    return {
        "action":
            action,
        "team_key":
            team,
        "yahoo_player_key":
            player_key,
        "years_remaining":
            years,
        "keeper_assignments":
            int(keeper_assignments),
        "state":
            get_commissioner_contract_override_state(),
    }
