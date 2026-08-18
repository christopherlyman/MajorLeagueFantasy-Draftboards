from __future__ import annotations

from typing import Any
import random

import psycopg
from psycopg.rows import dict_row

from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


def _fetch_all(
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _fetch_one(
    sql: str,
    params: tuple[Any, ...],
) -> dict[str, Any]:
    rows = _fetch_all(sql, params)

    if not rows:
        raise RuntimeError(
            "Expected one NFHL database row but received none."
        )

    return rows[0]


def get_dashboard_summary() -> dict[str, Any]:
    return _fetch_one(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM nfhl.team
                WHERE league_key = %s
                  AND season_year = %s
            ) AS team_count,

            (
                SELECT COUNT(*)
                FROM nfhl.player_universe
                WHERE league_key = %s
                  AND season_year = %s
            ) AS player_count,

            (
                SELECT status
                FROM nfhl.draft
                WHERE draft_key = %s
            ) AS draft_status
        """,
        (
            get_league_key(),
            get_season_year(),
            get_league_key(),
            get_season_year(),
            get_draft_key(),
        ),
    )


def get_teams() -> list[dict[str, Any]]:
    return _fetch_all(
        """
        SELECT
            team_key,
            team_id,
            team_name,
            owner_name
        FROM nfhl.team
        WHERE league_key = %s
          AND season_year = %s
        ORDER BY
            CASE
                WHEN team_id ~ '^[0-9]+$'
                THEN team_id::integer
                ELSE 999999
            END,
            team_name
        """,
        (
            get_league_key(),
            get_season_year(),
        ),
    )


def get_player_universe() -> list[dict[str, Any]]:
    """
    Current-season Yahoo player_universe is authoritative.

    Historical stats are enrichment only. A player is never removed
    because prior-season statistics do not exist.
    """
    return _fetch_all(
        """
        SELECT
            p.yahoo_player_key,
            p.full_name,
            p.nhl_team_abbr,
            p.eligible_positions,
            p.primary_position,
            p.position_type,
            p.player_status,
            p.percent_owned,
            p.rank_value,
            p.percent_drafted,
            p.preseason_percent_drafted,

            s.stats_season_year,
            s.gp,
            s.g,
            s.a,
            s.pim,
            s.ppp,
            s.shp,
            s.sog,
            s.hit,
            s.blk,
            s.w,
            s.ga,
            s.sv,
            s.sho,
            s.nfhl_fpts,
            s.nfhl_fpts_per_game

        FROM nfhl.player_universe p

        LEFT JOIN nfhl.player_season_stats s
          ON s.league_key = p.league_key
         AND s.draft_season_year = p.season_year
         AND s.current_yahoo_player_key = p.yahoo_player_key
         AND s.stats_season_year = p.season_year - 1

        WHERE p.league_key = %s
          AND p.season_year = %s

        ORDER BY
            p.rank_value NULLS LAST,
            p.full_name
        """,
        (
            get_league_key(),
            get_season_year(),
        ),
    )



# NFHL_DRAFT_LOTTERY_ENGINE_START
# ================================================================
# NFHL DRAFT LOTTERY
#
# Current-season nfhl.team membership is authoritative.
# No fake teams are ever synthesized.
#
# Initialization persists the ENTIRE random permutation before
# any result is revealed.
#
# Public/read-only consumers never receive hidden team identities.
# ================================================================


def get_lottery_state() -> dict[str, Any] | None:
    """
    Return the active NFHL lottery without exposing hidden teams.

    Hidden slots return NULL team identity until revealed.
    """

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    lottery_run_id,
                    draft_key,
                    configured_team_count,
                    status,
                    created_at_utc,
                    finalized_at_utc,
                    voided_at_utc
                FROM nfhl.draft_order_lottery_run
                WHERE draft_key = %s
                  AND status <> 'VOID'
                ORDER BY lottery_run_id DESC
                LIMIT 1
                """,
                (get_draft_key(),),
            )

            run = cur.fetchone()

            if run is None:
                return None

            cur.execute(
                """
                SELECT
                    p.slot_number,

                    CASE
                        WHEN p.revealed_at_utc IS NOT NULL
                        THEN p.team_key
                        ELSE NULL
                    END AS team_key,

                    CASE
                        WHEN p.revealed_at_utc IS NOT NULL
                        THEN t.team_name
                        ELSE NULL
                    END AS team_name,

                    CASE
                        WHEN p.revealed_at_utc IS NOT NULL
                        THEN t.owner_name
                        ELSE NULL
                    END AS owner_name,

                    p.revealed_at_utc

                FROM nfhl.draft_order_lottery_pick p

                JOIN nfhl.draft_order_lottery_run r
                  ON r.lottery_run_id = p.lottery_run_id

                JOIN nfhl.draft d
                  ON d.draft_key = r.draft_key

                LEFT JOIN nfhl.team t
                  ON t.league_key = d.league_key
                 AND t.season_year = d.season_year
                 AND t.team_key = p.team_key

                WHERE p.lottery_run_id = %s

                ORDER BY p.slot_number DESC
                """,
                (run["lottery_run_id"],),
            )

            picks = list(cur.fetchall())

    revealed_count = sum(
        1
        for row in picks
        if row["revealed_at_utc"] is not None
    )

    return {
        "run": dict(run),
        "picks": [dict(row) for row in picks],
        "revealed_count": revealed_count,
    }


def initialize_lottery(
    *,
    expected_team_count: int,
    actor: str,
) -> int:
    """
    Persist one complete equal-odds random permutation.

    The draft row is locked so two initialization requests cannot
    independently generate competing lotteries.
    """

    if expected_team_count <= 0:
        raise ValueError(
            "expected_team_count must be greater than zero."
        )

    actor = str(actor or "").strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        draft_key,
                        league_key,
                        season_year,
                        manager_count,
                        status
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    FOR UPDATE
                    """,
                    (get_draft_key(),),
                )

                draft = cur.fetchone()

                if draft is None:
                    raise RuntimeError(
                        "NFHL draft row does not exist."
                    )

                if int(draft["manager_count"]) != int(
                    expected_team_count
                ):
                    raise RuntimeError(
                        "Configured manager target does not match "
                        "nfhl.draft.manager_count."
                    )

                if str(draft["status"]).upper() not in {
                    "PREP",
                    "SETUP",
                }:
                    raise RuntimeError(
                        "Lottery may only be initialized while "
                        "the draft is in PREP or SETUP."
                    )

                cur.execute(
                    """
                    SELECT
                        team_key
                    FROM nfhl.team
                    WHERE league_key = %s
                      AND season_year = %s
                    ORDER BY team_key
                    """,
                    (
                        draft["league_key"],
                        draft["season_year"],
                    ),
                )

                team_keys = [
                    str(row["team_key"])
                    for row in cur.fetchall()
                ]

                if len(team_keys) != expected_team_count:
                    raise RuntimeError(
                        "Lottery locked: expected "
                        f"{expected_team_count} teams but found "
                        f"{len(team_keys)}."
                    )

                if len(set(team_keys)) != expected_team_count:
                    raise RuntimeError(
                        "Lottery initialization found duplicate "
                        "team keys."
                    )

                cur.execute(
                    """
                    SELECT
                        lottery_run_id,
                        status
                    FROM nfhl.draft_order_lottery_run
                    WHERE draft_key = %s
                      AND status <> 'VOID'
                    LIMIT 1
                    """,
                    (get_draft_key(),),
                )

                existing = cur.fetchone()

                if existing is not None:
                    raise RuntimeError(
                        "An active NFHL lottery already exists."
                    )

                # SystemRandom uses the operating system's
                # cryptographic randomness source.
                rng = random.SystemRandom()
                rng.shuffle(team_keys)

                cur.execute(
                    """
                    INSERT INTO nfhl.draft_order_lottery_run (
                        draft_key,
                        configured_team_count,
                        status,
                        created_by
                    )
                    VALUES (
                        %s,
                        %s,
                        'INITIALIZED',
                        %s
                    )
                    RETURNING lottery_run_id
                    """,
                    (
                        get_draft_key(),
                        expected_team_count,
                        actor,
                    ),
                )

                lottery_run_id = int(
                    cur.fetchone()["lottery_run_id"]
                )

                for slot_number, team_key in enumerate(
                    team_keys,
                    start=1,
                ):
                    cur.execute(
                        """
                        INSERT INTO nfhl.draft_order_lottery_pick (
                            lottery_run_id,
                            slot_number,
                            team_key
                        )
                        VALUES (%s, %s, %s)
                        """,
                        (
                            lottery_run_id,
                            slot_number,
                            team_key,
                        ),
                    )

    return lottery_run_id


def reveal_next_lottery_slot(
    *,
    actor: str,
) -> dict[str, Any] | None:
    """
    Reveal the highest-numbered remaining hidden slot.

    For 14 teams:
      14 -> 13 -> ... -> 1
    """

    actor = str(actor or "").strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        lottery_run_id,
                        draft_key,
                        configured_team_count,
                        status
                    FROM nfhl.draft_order_lottery_run
                    WHERE draft_key = %s
                      AND status <> 'VOID'
                    ORDER BY lottery_run_id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (get_draft_key(),),
                )

                run = cur.fetchone()

                if run is None:
                    raise RuntimeError(
                        "No active NFHL lottery exists."
                    )

                if run["status"] == "FINALIZED":
                    raise RuntimeError(
                        "The NFHL lottery is already finalized."
                    )

                cur.execute(
                    """
                    SELECT
                        slot_number,
                        team_key
                    FROM nfhl.draft_order_lottery_pick
                    WHERE lottery_run_id = %s
                      AND revealed_at_utc IS NULL
                    ORDER BY slot_number DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (run["lottery_run_id"],),
                )

                next_pick = cur.fetchone()

                if next_pick is None:
                    return None

                cur.execute(
                    """
                    UPDATE nfhl.draft_order_lottery_pick
                    SET
                        revealed_at_utc = now(),
                        revealed_by = %s
                    WHERE lottery_run_id = %s
                      AND slot_number = %s
                    """,
                    (
                        actor,
                        run["lottery_run_id"],
                        next_pick["slot_number"],
                    ),
                )

                cur.execute(
                    """
                    SELECT COUNT(*) AS remaining
                    FROM nfhl.draft_order_lottery_pick
                    WHERE lottery_run_id = %s
                      AND revealed_at_utc IS NULL
                    """,
                    (run["lottery_run_id"],),
                )

                remaining = int(
                    cur.fetchone()["remaining"]
                )

                new_status = (
                    "REVEALED"
                    if remaining == 0
                    else "REVEALING"
                )

                cur.execute(
                    """
                    UPDATE nfhl.draft_order_lottery_run
                    SET status = %s
                    WHERE lottery_run_id = %s
                    """,
                    (
                        new_status,
                        run["lottery_run_id"],
                    ),
                )

                cur.execute(
                    """
                    SELECT
                        p.slot_number,
                        p.team_key,
                        t.team_name,
                        t.owner_name,
                        p.revealed_at_utc

                    FROM nfhl.draft_order_lottery_pick p

                    JOIN nfhl.draft_order_lottery_run r
                      ON r.lottery_run_id = p.lottery_run_id

                    JOIN nfhl.draft d
                      ON d.draft_key = r.draft_key

                    LEFT JOIN nfhl.team t
                      ON t.league_key = d.league_key
                     AND t.season_year = d.season_year
                     AND t.team_key = p.team_key

                    WHERE p.lottery_run_id = %s
                      AND p.slot_number = %s
                    """,
                    (
                        run["lottery_run_id"],
                        next_pick["slot_number"],
                    ),
                )

                revealed = cur.fetchone()

                return (
                    dict(revealed)
                    if revealed is not None
                    else None
                )


def finalize_lottery(
    *,
    actor: str,
) -> None:
    """
    Lock the fully revealed lottery.

    This does NOT initialize draft_pick rows yet. Production draft
    order initialization remains a separate step.
    """

    actor = str(actor or "").strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        lottery_run_id,
                        status
                    FROM nfhl.draft_order_lottery_run
                    WHERE draft_key = %s
                      AND status <> 'VOID'
                    ORDER BY lottery_run_id DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (get_draft_key(),),
                )

                run = cur.fetchone()

                if run is None:
                    raise RuntimeError(
                        "No active NFHL lottery exists."
                    )

                if run["status"] == "FINALIZED":
                    return

                cur.execute(
                    """
                    SELECT COUNT(*) AS hidden_count
                    FROM nfhl.draft_order_lottery_pick
                    WHERE lottery_run_id = %s
                      AND revealed_at_utc IS NULL
                    """,
                    (run["lottery_run_id"],),
                )

                hidden_count = int(
                    cur.fetchone()["hidden_count"]
                )

                if hidden_count != 0:
                    raise RuntimeError(
                        "Lottery cannot be finalized until all "
                        "slots have been revealed."
                    )

                cur.execute(
                    """
                    UPDATE nfhl.draft_order_lottery_run
                    SET
                        status = 'FINALIZED',
                        finalized_at_utc = now(),
                        finalized_by = %s
                    WHERE lottery_run_id = %s
                    """,
                    (
                        actor,
                        run["lottery_run_id"],
                    ),
                )


def void_lottery(
    *,
    actor: str,
    reason: str,
) -> None:
    """
    Explicit commissioner reset.

    Voided history remains in the database for auditability.
    """

    actor = str(actor or "").strip() or "commissioner"
    reason = str(reason or "").strip()

    if not reason:
        raise ValueError(
            "A reason is required to void an NFHL lottery."
        )

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        d.status AS draft_status,
                        r.lottery_run_id,
                        r.status AS lottery_status

                    FROM nfhl.draft d

                    LEFT JOIN nfhl.draft_order_lottery_run r
                      ON r.draft_key = d.draft_key
                     AND r.status <> 'VOID'

                    WHERE d.draft_key = %s

                    FOR UPDATE OF d
                    """,
                    (get_draft_key(),),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL draft row does not exist."
                    )

                if row["lottery_run_id"] is None:
                    raise RuntimeError(
                        "No active NFHL lottery exists."
                    )

                if str(row["draft_status"]).upper() not in {
                    "PREP",
                    "SETUP",
                }:
                    raise RuntimeError(
                        "Lottery reset is blocked after the draft "
                        "leaves PREP/SETUP."
                    )

                cur.execute(
                    """
                    UPDATE nfhl.draft_order_lottery_run
                    SET
                        status = 'VOID',
                        voided_at_utc = now(),
                        voided_by = %s,
                        void_reason = %s
                    WHERE lottery_run_id = %s
                    """,
                    (
                        actor,
                        reason,
                        row["lottery_run_id"],
                    ),
                )


# NFHL_DRAFT_LOTTERY_ENGINE_END





# NFHL_LIVE_DRAFT_DB_HELPERS_START
# ================================================================
# NFHL LIVE DRAFT
#
# PostgreSQL remains authoritative for:
#   - clock transitions
#   - atomic manual picks
#   - late-pick handling
#   - duplicate-player protection
# ================================================================


def get_live_draft_state() -> dict[str, Any]:
    """
    Read the durable NFHL draft state plus current draft metadata.
    """

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
                    d.rounds_total,
                    d.draft_order_mode,
                    s.state_json
                FROM nfhl.draft d
                LEFT JOIN nfhl.draft_state s
                  ON s.draft_key = d.draft_key
                WHERE d.draft_key = %s
                """,
                (
                    get_draft_key(),
                ),
            )

            row = cur.fetchone()

            if row is None:
                raise RuntimeError(
                    "NFHL draft row does not exist."
                )

            return dict(row)


def get_live_draft_board() -> list[dict[str, Any]]:
    """
    Return the current authoritative NFHL draft board.
    """

    return _fetch_all(
        """
        SELECT
            draft_key,
            pick_id,
            round_number,
            slot_number,
            round_label,
            column_team_key,
            column_team_name,
            current_owner_team_key,
            current_owner_team_name,
            traded_flag,
            ownership_note,
            yahoo_player_key,
            selected_player_name,
            selected_at_utc,
            selected_primary_position
        FROM nfhl.v_draft_board_current
        WHERE draft_key = %s
        ORDER BY
            round_number,
            slot_number
        """,
        (
            get_draft_key(),
        ),
    )


def process_live_draft_clock() -> dict[str, Any]:
    """
    Let PostgreSQL process any due NFHL clock transition.
    """

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM nfhl.process_draft_clock(%s)
                    """,
                    (
                        get_draft_key(),
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL clock engine returned no result."
                    )

                return dict(row)


def submit_manual_draft_pick(
    *,
    expected_pick_id: str,
    expected_team_key: str,
    yahoo_player_key: str,
    actor: str,
) -> dict[str, Any]:
    """
    Submit one manual pick through the atomic PostgreSQL engine.
    """

    expected_pick_id = str(
        expected_pick_id or ""
    ).strip()

    expected_team_key = str(
        expected_team_key or ""
    ).strip()

    yahoo_player_key = str(
        yahoo_player_key or ""
    ).strip()

    actor = str(
        actor or ""
    ).strip() or "manual_pick"

    if not expected_pick_id:
        raise ValueError(
            "expected_pick_id is required."
        )

    if not expected_team_key:
        raise ValueError(
            "expected_team_key is required."
        )

    if not yahoo_player_key:
        raise ValueError(
            "yahoo_player_key is required."
        )

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM nfhl.submit_draft_pick_atomic(
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        get_draft_key(),
                        expected_pick_id,
                        expected_team_key,
                        yahoo_player_key,
                        actor,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL atomic pick engine returned no result."
                    )

                return dict(row)


# NFHL_LIVE_DRAFT_DB_HELPERS_END


# NFHL_DRAFT_INITIALIZER_START
# ================================================================
# NFHL PURE REDRAFT INITIALIZATION
#
# NFHL has:
#   - no keepers
#   - no qualifying offers
#   - no franchise tags
#   - no contracts
#   - no poaching
#
# The finalized lottery supplies the team order.
# Straight/snake applies to all 18 ordinary draft rounds.
# ================================================================


def get_draft_initialization_readiness() -> dict[str, Any]:
    """
    Report whether the real NFHL draft can safely be initialized.

    This function is READ ONLY.
    """

    draft_key = get_draft_key()
    league_key = get_league_key()
    season_year = get_season_year()

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    draft_key,
                    status,
                    manager_count,
                    rounds_total,
                    draft_order_mode
                FROM nfhl.draft
                WHERE draft_key = %s
                """,
                (
                    draft_key,
                ),
            )

            draft = cur.fetchone()

            if draft is None:
                raise RuntimeError(
                    f"NFHL draft {draft_key!r} does not exist."
                )

            cur.execute(
                """
                SELECT COUNT(*) AS team_count
                FROM nfhl.team
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            team_count = int(
                cur.fetchone()["team_count"]
            )

            cur.execute(
                """
                SELECT
                    COUNT(*) AS finalized_count,
                    MAX(lottery_run_id) AS lottery_run_id
                FROM nfhl.draft_order_lottery_run
                WHERE draft_key = %s
                  AND status = 'FINALIZED'
                """,
                (
                    draft_key,
                ),
            )

            lottery = cur.fetchone()

            finalized_count = int(
                lottery["finalized_count"]
            )

            lottery_run_id = (
                lottery["lottery_run_id"]
            )

            lottery_pick_count = 0

            if (
                finalized_count == 1
                and lottery_run_id is not None
            ):
                cur.execute(
                    """
                    SELECT COUNT(*) AS pick_count
                    FROM nfhl.draft_order_lottery_pick
                    WHERE lottery_run_id = %s
                    """,
                    (
                        lottery_run_id,
                    ),
                )

                lottery_pick_count = int(
                    cur.fetchone()["pick_count"]
                )

            cur.execute(
                """
                SELECT COUNT(*) AS draft_pick_count
                FROM nfhl.draft_pick
                WHERE draft_key = %s
                """,
                (
                    draft_key,
                ),
            )

            draft_pick_count = int(
                cur.fetchone()["draft_pick_count"]
            )

    manager_count = int(
        draft["manager_count"]
    )

    rounds_total = int(
        draft["rounds_total"]
    )

    draft_status = str(
        draft["status"]
        or ""
    ).upper()

    order_mode = str(
        draft["draft_order_mode"]
        or ""
    ).lower()

    blockers: list[str] = []

    if draft_status != "PREP":
        blockers.append(
            f"Draft status must be PREP; found {draft_status or 'NULL'}."
        )

    if team_count != manager_count:
        blockers.append(
            f"Requires exactly {manager_count} live teams; "
            f"found {team_count}."
        )

    if order_mode not in {
        "straight",
        "snake",
    }:
        blockers.append(
            "Draft order mode has not been verified "
            "as straight or snake."
        )

    if finalized_count != 1:
        blockers.append(
            "Requires exactly one FINALIZED draft lottery; "
            f"found {finalized_count}."
        )

    if (
        finalized_count == 1
        and lottery_pick_count != manager_count
    ):
        blockers.append(
            f"Finalized lottery must contain exactly "
            f"{manager_count} teams; found {lottery_pick_count}."
        )

    if draft_pick_count != 0:
        blockers.append(
            "Draft has already been initialized "
            f"({draft_pick_count} draft_pick rows exist)."
        )

    return {
        "ready": not blockers,
        "draft_key": draft_key,
        "status": draft_status,
        "manager_count": manager_count,
        "live_team_count": team_count,
        "rounds_total": rounds_total,
        "draft_order_mode": (
            order_mode
            if order_mode
            else None
        ),
        "finalized_lottery_count": finalized_count,
        "lottery_run_id": lottery_run_id,
        "lottery_pick_count": lottery_pick_count,
        "draft_pick_count": draft_pick_count,
        "expected_draft_pick_count": (
            manager_count * rounds_total
        ),
        "blockers": blockers,
    }


def initialize_draft_from_lottery(
    *,
    actor: str,
) -> dict[str, Any]:
    """
    Atomically initialize the real NFHL pure-redraft board.

    The operation refuses to run unless:
      * draft is PREP
      * all real teams are present
      * exactly one lottery is FINALIZED
      * lottery contains every team exactly once
      * order mode is explicitly straight or snake
      * no draft_pick rows already exist

    Initialization creates the board but does NOT start the clock.
    """

    import json

    from draftboard.data.picks_grid import (
        build_pick_grid,
    )

    draft_key = get_draft_key()
    league_key = get_league_key()
    season_year = get_season_year()

    actor = str(
        actor or ""
    ).strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                # Serialize initialization attempts for this draft.
                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (
                        "nfhl:init:" + draft_key,
                    ),
                )

                cur.execute(
                    """
                    SELECT
                        draft_key,
                        status,
                        manager_count,
                        rounds_total,
                        draft_order_mode
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    FOR UPDATE
                    """,
                    (
                        draft_key,
                    ),
                )

                draft = cur.fetchone()

                if draft is None:
                    raise RuntimeError(
                        f"NFHL draft {draft_key!r} does not exist."
                    )

                status = str(
                    draft["status"]
                    or ""
                ).upper()

                manager_count = int(
                    draft["manager_count"]
                )

                rounds_total = int(
                    draft["rounds_total"]
                )

                order_mode = str(
                    draft["draft_order_mode"]
                    or ""
                ).lower()

                if status != "PREP":
                    raise RuntimeError(
                        "Draft initialization requires PREP status; "
                        f"found {status or 'NULL'}."
                    )

                if order_mode not in {
                    "straight",
                    "snake",
                }:
                    raise RuntimeError(
                        "Draft order mode must be explicitly "
                        "set to straight or snake."
                    )

                # ------------------------------------------------
                # Exact real-team count.
                # ------------------------------------------------

                cur.execute(
                    """
                    SELECT
                        team_key
                    FROM nfhl.team
                    WHERE league_key = %s
                      AND season_year = %s
                    ORDER BY team_key
                    """,
                    (
                        league_key,
                        season_year,
                    ),
                )

                real_team_keys = [
                    str(row["team_key"])
                    for row in cur.fetchall()
                ]

                if len(real_team_keys) != manager_count:
                    raise RuntimeError(
                        f"Production draft requires exactly "
                        f"{manager_count} real Yahoo teams; "
                        f"found {len(real_team_keys)}."
                    )

                # ------------------------------------------------
                # Exactly one finalized lottery.
                # ------------------------------------------------

                cur.execute(
                    """
                    SELECT
                        lottery_run_id,
                        configured_team_count
                    FROM nfhl.draft_order_lottery_run
                    WHERE draft_key = %s
                      AND status = 'FINALIZED'
                    ORDER BY lottery_run_id
                    FOR UPDATE
                    """,
                    (
                        draft_key,
                    ),
                )

                finalized_runs = cur.fetchall()

                if len(finalized_runs) != 1:
                    raise RuntimeError(
                        "Draft initialization requires exactly "
                        "one FINALIZED lottery; "
                        f"found {len(finalized_runs)}."
                    )

                lottery_run = finalized_runs[0]

                lottery_run_id = int(
                    lottery_run["lottery_run_id"]
                )

                configured_team_count = int(
                    lottery_run["configured_team_count"]
                )

                if configured_team_count != manager_count:
                    raise RuntimeError(
                        "Finalized lottery team count does not "
                        "match the draft manager count."
                    )

                cur.execute(
                    """
                    SELECT
                        slot_number,
                        team_key
                    FROM nfhl.draft_order_lottery_pick
                    WHERE lottery_run_id = %s
                    ORDER BY slot_number
                    FOR UPDATE
                    """,
                    (
                        lottery_run_id,
                    ),
                )

                lottery_rows = cur.fetchall()

                if len(lottery_rows) != manager_count:
                    raise RuntimeError(
                        f"Finalized lottery must contain "
                        f"{manager_count} assignments; "
                        f"found {len(lottery_rows)}."
                    )

                expected_slots = list(
                    range(
                        1,
                        manager_count + 1,
                    )
                )

                actual_slots = [
                    int(row["slot_number"])
                    for row in lottery_rows
                ]

                if actual_slots != expected_slots:
                    raise RuntimeError(
                        "Finalized lottery slot numbers are "
                        "not a complete 1..manager_count sequence."
                    )

                lottery_team_keys = [
                    str(row["team_key"])
                    for row in lottery_rows
                ]

                if (
                    len(set(lottery_team_keys))
                    != manager_count
                ):
                    raise RuntimeError(
                        "Finalized lottery contains duplicate teams."
                    )

                if (
                    set(lottery_team_keys)
                    != set(real_team_keys)
                ):
                    raise RuntimeError(
                        "Finalized lottery teams do not exactly "
                        "match the current Yahoo team field."
                    )

                # ------------------------------------------------
                # Absolutely no existing board.
                # ------------------------------------------------

                cur.execute(
                    """
                    SELECT COUNT(*) AS row_count
                    FROM nfhl.draft_pick
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                existing_rows = int(
                    cur.fetchone()["row_count"]
                )

                if existing_rows != 0:
                    raise RuntimeError(
                        "Draft has already been initialized; "
                        f"{existing_rows} draft_pick rows exist."
                    )

                # ------------------------------------------------
                # Build pure-redraft execution grid.
                # ------------------------------------------------

                slots = build_pick_grid(
                    lottery_team_keys,
                    manager_count=manager_count,
                    rounds_total=rounds_total,
                    order_mode=order_mode,
                )

                expected_pick_count = (
                    manager_count
                    * rounds_total
                )

                if len(slots) != expected_pick_count:
                    raise RuntimeError(
                        "Generated draft grid has the wrong "
                        f"size: expected {expected_pick_count}, "
                        f"found {len(slots)}."
                    )

                pick_rows = []
                pick_order = []

                for slot in slots:

                    pick_id = (
                        f"R{slot.round_number:02d}-"
                        f"{slot.pick_in_round:02d}"
                    )

                    pick_order.append(
                        pick_id
                    )

                    pick_rows.append(
                        (
                            draft_key,
                            pick_id,
                            slot.round_number,
                            slot.pick_in_round,
                            f"R{slot.round_number:02d}",
                            slot.team_key,
                            slot.team_key,
                            False,
                            None,
                        )
                    )

                if len(set(pick_order)) != expected_pick_count:
                    raise RuntimeError(
                        "Generated pick IDs are not unique."
                    )

                # ------------------------------------------------
                # Insert the full board.
                # ------------------------------------------------

                cur.executemany(
                    """
                    INSERT INTO nfhl.draft_pick (
                        draft_key,
                        pick_id,
                        round_number,
                        slot_number,
                        round_label,
                        column_team_key,
                        current_owner_team_key,
                        traded_flag,
                        ownership_note
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    pick_rows,
                )

                cur.execute(
                    """
                    SELECT COUNT(*) AS row_count
                    FROM nfhl.draft_pick
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                inserted_count = int(
                    cur.fetchone()["row_count"]
                )

                if inserted_count != expected_pick_count:
                    raise RuntimeError(
                        "Draft initialization row-count verification "
                        f"failed: expected {expected_pick_count}, "
                        f"found {inserted_count}."
                    )

                # ------------------------------------------------
                # Durable initial state.
                #
                # Board is initialized and first pick is known,
                # but draft/clock remain stopped until Start Draft.
                # ------------------------------------------------

                first_pick_id = (
                    pick_order[0]
                    if pick_order
                    else None
                )

                state = {
                    "schema_version": "nfhl-1",
                    "draft_order_team_keys_by_slot": (
                        lottery_team_keys
                    ),
                    "pick_order": pick_order,
                    "pick_log": [],
                    "clock": {
                        "current_pick_id": first_pick_id,
                        "is_running": False,
                        "pick_started_ts_iso": None,
                        "pick_paused_ts_iso": None,
                        "elapsed_paused_seconds": 0,
                    },
                }

                state_text = json.dumps(
                    state,
                    separators=(",", ":"),
                )

                cur.execute(
                    """
                    UPDATE nfhl.draft_state
                       SET schema_version = 'nfhl-1',
                           state_json = %s::jsonb,
                           state_sha256 =
                               encode(
                                   public.digest(
                                       pg_catalog.convert_to(
                                           %s::jsonb::text,
                                           'UTF8'
                                       ),
                                       'sha256'
                                   ),
                                   'hex'
                               ),
                           updated_at_utc = now()
                     WHERE draft_key = %s
                    """,
                    (
                        state_text,
                        state_text,
                        draft_key,
                    ),
                )

                if cur.rowcount != 1:
                    raise RuntimeError(
                        "Failed to update NFHL draft_state."
                    )

                return {
                    "result_status": "INITIALIZED",
                    "draft_key": draft_key,
                    "lottery_run_id": lottery_run_id,
                    "manager_count": manager_count,
                    "rounds_total": rounds_total,
                    "draft_order_mode": order_mode,
                    "draft_pick_count": inserted_count,
                    "first_pick_id": first_pick_id,
                    "initialized_by": actor,
                }


# NFHL_DRAFT_INITIALIZER_END


# NFHL_DRAFT_LIFECYCLE_DB_START
# ================================================================
# NFHL DRAFT LIFECYCLE
#
# Operational lifecycle:
#
#   PREP
#     -> configure clock
#     -> initialize board
#     -> Start Draft
#
#   ACTIVE / RUNNING
#     -> Pause
#
#   ACTIVE / PAUSED
#     -> Resume
#
# PostgreSQL remains authoritative for clock expiration,
# reminders, missed picks, and advancement.
# ================================================================


def _nfhl_clock_iso_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _nfhl_parse_clock_iso(
    value: object,
):
    from datetime import datetime

    text = str(
        value or ""
    ).strip()

    if not text:
        return None

    if text.endswith("Z"):
        text = (
            text[:-1]
            + "+00:00"
        )

    return datetime.fromisoformat(
        text
    )


def _nfhl_write_state_locked(
    cur,
    *,
    draft_key: str,
    state: dict[str, Any],
) -> None:
    import json

    state_text = json.dumps(
        state,
        separators=(",", ":"),
    )

    cur.execute(
        """
        UPDATE nfhl.draft_state
           SET state_json = %s::jsonb,
               state_sha256 =
                   encode(
                       public.digest(
                           pg_catalog.convert_to(
                               %s::jsonb::text,
                               'UTF8'
                           ),
                           'sha256'
                       ),
                       'hex'
                   ),
               updated_at_utc = now()
         WHERE draft_key = %s
        """,
        (
            state_text,
            state_text,
            draft_key,
        ),
    )

    if cur.rowcount != 1:
        raise RuntimeError(
            "Failed to update NFHL draft_state."
        )


def get_draft_clock_config() -> dict[str, Any]:
    """
    Read the NFHL draft clock and reminder configuration.
    """

    draft_key = get_draft_key()

    config_rows = _fetch_all(
        """
        SELECT
            seconds_per_pick,
            timezone,
            auto_advance,
            weekends_count,
            created_at_utc,
            updated_at_utc
        FROM nfhl.draft_clock_config
        WHERE draft_key = %s
        """,
        (
            draft_key,
        ),
    )

    reminder_rows = _fetch_all(
        """
        SELECT
            reminder_code,
            seconds_remaining,
            enabled
        FROM nfhl.draft_clock_reminder_config
        WHERE draft_key = %s
        ORDER BY seconds_remaining DESC
        """,
        (
            draft_key,
        ),
    )

    if not config_rows:
        return {
            "configured": False,
            "seconds_per_pick": None,
            "timezone": "America/New_York",
            "auto_advance": True,
            "weekends_count": True,
            "reminders": [],
        }

    row = config_rows[0]

    return {
        "configured": True,
        "seconds_per_pick": int(
            row["seconds_per_pick"]
        ),
        "timezone": str(
            row["timezone"]
        ),
        "auto_advance": bool(
            row["auto_advance"]
        ),
        "weekends_count": bool(
            row["weekends_count"]
        ),
        "created_at_utc": row[
            "created_at_utc"
        ],
        "updated_at_utc": row[
            "updated_at_utc"
        ],
        "reminders": [
            {
                "reminder_code": str(
                    reminder[
                        "reminder_code"
                    ]
                ),
                "seconds_remaining": int(
                    reminder[
                        "seconds_remaining"
                    ]
                ),
                "enabled": bool(
                    reminder["enabled"]
                ),
            }
            for reminder in reminder_rows
        ],
    }


def save_draft_clock_config(
    *,
    seconds_per_pick: int,
    reminder_seconds: list[int],
    actor: str,
) -> dict[str, Any]:
    """
    Configure the NFHL slow-draft clock.

    Configuration may be changed only while the draft is PREP.
    """

    draft_key = get_draft_key()

    seconds_per_pick = int(
        seconds_per_pick
    )

    if seconds_per_pick <= 0:
        raise ValueError(
            "seconds_per_pick must be positive."
        )

    normalized_reminders = sorted(
        {
            int(value)
            for value in reminder_seconds
            if int(value) > 0
        },
        reverse=True,
    )

    for seconds in normalized_reminders:
        if seconds >= seconds_per_pick:
            raise ValueError(
                "Every reminder must occur before "
                "the pick deadline."
            )

    actor = str(
        actor or ""
    ).strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (
                        draft_key,
                    ),
                )

                cur.execute(
                    """
                    SELECT status
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    FOR UPDATE
                    """,
                    (
                        draft_key,
                    ),
                )

                draft = cur.fetchone()

                if draft is None:
                    raise RuntimeError(
                        "NFHL draft does not exist."
                    )

                status = str(
                    draft["status"]
                    or ""
                ).upper()

                if status != "PREP":
                    raise RuntimeError(
                        "Clock configuration may only "
                        "be changed while the draft is PREP."
                    )

                cur.execute(
                    """
                    INSERT INTO nfhl.draft_clock_config (
                        draft_key,
                        seconds_per_pick,
                        timezone,
                        auto_advance,
                        weekends_count,
                        created_at_utc,
                        updated_at_utc
                    )
                    VALUES (
                        %s,
                        %s,
                        'America/New_York',
                        true,
                        true,
                        now(),
                        now()
                    )
                    ON CONFLICT (draft_key)
                    DO UPDATE SET
                        seconds_per_pick =
                            EXCLUDED.seconds_per_pick,
                        timezone =
                            EXCLUDED.timezone,
                        auto_advance =
                            EXCLUDED.auto_advance,
                        weekends_count =
                            EXCLUDED.weekends_count,
                        updated_at_utc = now()
                    """,
                    (
                        draft_key,
                        seconds_per_pick,
                    ),
                )

                cur.execute(
                    """
                    DELETE FROM
                        nfhl.draft_clock_reminder_config
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                for seconds in normalized_reminders:

                    reminder_code = (
                        f"T_MINUS_{seconds}"
                    )

                    cur.execute(
                        """
                        INSERT INTO
                            nfhl.draft_clock_reminder_config (
                                draft_key,
                                reminder_code,
                                seconds_remaining,
                                enabled,
                                created_at_utc,
                                updated_at_utc
                            )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            true,
                            now(),
                            now()
                        )
                        """,
                        (
                            draft_key,
                            reminder_code,
                            seconds,
                        ),
                    )

    return {
        "result_status": "SAVED",
        "draft_key": draft_key,
        "seconds_per_pick": seconds_per_pick,
        "reminder_seconds": (
            normalized_reminders
        ),
        "updated_by": actor,
    }


def get_draft_clock_snapshot() -> dict[str, Any]:
    """
    Read current draft/clock status and calculate elapsed/remaining
    time without changing the clock.
    """

    from datetime import datetime, timezone

    live_state = get_live_draft_state()
    clock_config = get_draft_clock_config()

    state = (
        live_state.get("state_json")
        or {}
    )

    clock = (
        state.get("clock")
        or {}
    )

    status = str(
        live_state.get("status")
        or ""
    ).upper()

    current_pick_id = str(
        clock.get("current_pick_id")
        or ""
    ).strip() or None

    is_running = bool(
        clock.get("is_running")
    )

    elapsed = int(
        clock.get(
            "elapsed_paused_seconds"
        )
        or 0
    )

    started = _nfhl_parse_clock_iso(
        clock.get(
            "pick_started_ts_iso"
        )
    )

    if (
        is_running
        and started is not None
    ):
        now = datetime.now(
            timezone.utc
        )

        delta = int(
            (
                now
                - started
            ).total_seconds()
        )

        if delta > 0:
            elapsed += delta

    seconds_per_pick = (
        clock_config.get(
            "seconds_per_pick"
        )
        if clock_config.get(
            "configured"
        )
        else None
    )

    remaining = None

    if seconds_per_pick is not None:
        remaining = max(
            int(seconds_per_pick)
            - elapsed,
            0,
        )

    current_team_key = None
    current_team_name = None
    round_number = None
    slot_number = None

    if current_pick_id:
        rows = _fetch_all(
            """
            SELECT
                round_number,
                slot_number,
                current_owner_team_key,
                current_owner_team_name
            FROM nfhl.v_draft_board_current
            WHERE draft_key = %s
              AND pick_id = %s
            """,
            (
                get_draft_key(),
                current_pick_id,
            ),
        )

        if rows:
            current = rows[0]

            current_team_key = (
                current[
                    "current_owner_team_key"
                ]
            )

            current_team_name = (
                current[
                    "current_owner_team_name"
                ]
            )

            round_number = int(
                current["round_number"]
            )

            slot_number = int(
                current["slot_number"]
            )

    return {
        "status": status,
        "configured": bool(
            clock_config.get(
                "configured"
            )
        ),
        "seconds_per_pick": (
            seconds_per_pick
        ),
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "is_running": is_running,
        "current_pick_id": current_pick_id,
        "current_team_key": current_team_key,
        "current_team_name": current_team_name,
        "round_number": round_number,
        "slot_number": slot_number,
        "reminders": (
            clock_config.get(
                "reminders"
            )
            or []
        ),
    }


def start_nfhl_draft(
    *,
    actor: str,
) -> dict[str, Any]:
    """
    Activate the initialized NFHL draft and start pick #1.
    """

    draft_key = get_draft_key()

    actor = str(
        actor or ""
    ).strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (
                        draft_key,
                    ),
                )

                cur.execute(
                    """
                    SELECT
                        d.status,
                        d.manager_count,
                        d.rounds_total,
                        s.state_json
                    FROM nfhl.draft d
                    JOIN nfhl.draft_state s
                      ON s.draft_key = d.draft_key
                    WHERE d.draft_key = %s
                    FOR UPDATE OF d, s
                    """,
                    (
                        draft_key,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL draft/state does not exist."
                    )

                status = str(
                    row["status"]
                    or ""
                ).upper()

                if status != "PREP":
                    raise RuntimeError(
                        "Start Draft requires PREP status."
                    )

                manager_count = int(
                    row["manager_count"]
                )

                rounds_total = int(
                    row["rounds_total"]
                )

                expected_rows = (
                    manager_count
                    * rounds_total
                )

                cur.execute(
                    """
                    SELECT COUNT(*) AS row_count
                    FROM nfhl.draft_pick
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                board_rows = int(
                    cur.fetchone()[
                        "row_count"
                    ]
                )

                if board_rows != expected_rows:
                    raise RuntimeError(
                        "Draft Board is not fully initialized: "
                        f"expected {expected_rows} picks, "
                        f"found {board_rows}."
                    )

                cur.execute(
                    """
                    SELECT
                        seconds_per_pick,
                        auto_advance,
                        weekends_count
                    FROM nfhl.draft_clock_config
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                clock_config = (
                    cur.fetchone()
                )

                if clock_config is None:
                    raise RuntimeError(
                        "Configure the NFHL draft clock "
                        "before starting the draft."
                    )

                if int(
                    clock_config[
                        "seconds_per_pick"
                    ]
                ) != 86400:
                    raise RuntimeError(
                        "NFHL requires the standard "
                        "24-hour pick clock."
                    )

                if not bool(
                    clock_config[
                        "auto_advance"
                    ]
                ):
                    raise RuntimeError(
                        "NFHL draft clock auto_advance "
                        "must be enabled."
                    )

                if not bool(
                    clock_config[
                        "weekends_count"
                    ]
                ):
                    raise RuntimeError(
                        "NFHL currently requires weekends "
                        "to count toward the draft clock."
                    )

                state = dict(
                    row["state_json"]
                    or {}
                )

                pick_order = list(
                    state.get(
                        "pick_order"
                    )
                    or []
                )

                if not pick_order:
                    raise RuntimeError(
                        "Draft state has no pick_order."
                    )

                clock = dict(
                    state.get("clock")
                    or {}
                )

                current_pick_id = str(
                    clock.get(
                        "current_pick_id"
                    )
                    or pick_order[0]
                ).strip()

                if not current_pick_id:
                    raise RuntimeError(
                        "Draft state has no current pick."
                    )

                cur.execute(
                    """
                    SELECT 1
                    FROM nfhl.draft_pick dp
                    LEFT JOIN nfhl.draft_selection ds
                      ON ds.draft_key = dp.draft_key
                     AND ds.pick_id = dp.pick_id
                    WHERE dp.draft_key = %s
                      AND dp.pick_id = %s
                      AND ds.pick_id IS NULL
                    """,
                    (
                        draft_key,
                        current_pick_id,
                    ),
                )

                if cur.fetchone() is None:
                    raise RuntimeError(
                        "The initial current pick is not "
                        "an open Draft Board slot."
                    )

                now_iso = (
                    _nfhl_clock_iso_now()
                )

                clock[
                    "current_pick_id"
                ] = current_pick_id

                clock[
                    "is_running"
                ] = True

                clock[
                    "pick_started_ts_iso"
                ] = now_iso

                clock[
                    "pick_paused_ts_iso"
                ] = None

                clock[
                    "elapsed_paused_seconds"
                ] = 0

                state["clock"] = clock

                cur.execute(
                    """
                    UPDATE nfhl.draft
                       SET status = 'ACTIVE'
                     WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                _nfhl_write_state_locked(
                    cur,
                    draft_key=draft_key,
                    state=state,
                )

    return {
        "result_status": "STARTED",
        "draft_key": draft_key,
        "current_pick_id": current_pick_id,
        "started_by": actor,
    }


def pause_nfhl_draft(
    *,
    actor: str,
) -> dict[str, Any]:
    """
    Pause the active NFHL draft clock while preserving elapsed time.
    """

    from datetime import datetime, timezone

    draft_key = get_draft_key()

    actor = str(
        actor or ""
    ).strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                # First process any expiration that became due
                # before the commissioner clicked Pause.
                cur.execute(
                    """
                    SELECT *
                    FROM nfhl.process_draft_clock(%s)
                    """,
                    (
                        draft_key,
                    ),
                )

                cur.fetchone()

                cur.execute(
                    """
                    SELECT
                        d.status,
                        s.state_json
                    FROM nfhl.draft d
                    JOIN nfhl.draft_state s
                      ON s.draft_key = d.draft_key
                    WHERE d.draft_key = %s
                    FOR UPDATE OF d, s
                    """,
                    (
                        draft_key,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL draft/state does not exist."
                    )

                if str(
                    row["status"]
                    or ""
                ).upper() != "ACTIVE":
                    raise RuntimeError(
                        "Only an ACTIVE draft may be paused."
                    )

                state = dict(
                    row["state_json"]
                    or {}
                )

                clock = dict(
                    state.get("clock")
                    or {}
                )

                if not bool(
                    clock.get(
                        "is_running"
                    )
                ):
                    raise RuntimeError(
                        "NFHL draft clock is already paused."
                    )

                started = (
                    _nfhl_parse_clock_iso(
                        clock.get(
                            "pick_started_ts_iso"
                        )
                    )
                )

                elapsed = int(
                    clock.get(
                        "elapsed_paused_seconds"
                    )
                    or 0
                )

                now = datetime.now(
                    timezone.utc
                )

                if started is not None:
                    delta = int(
                        (
                            now
                            - started
                        ).total_seconds()
                    )

                    if delta > 0:
                        elapsed += delta

                now_iso = (
                    now
                    .isoformat()
                    .replace(
                        "+00:00",
                        "Z",
                    )
                )

                clock[
                    "is_running"
                ] = False

                clock[
                    "pick_paused_ts_iso"
                ] = now_iso

                clock[
                    "elapsed_paused_seconds"
                ] = elapsed

                state["clock"] = clock

                _nfhl_write_state_locked(
                    cur,
                    draft_key=draft_key,
                    state=state,
                )

                current_pick_id = (
                    clock.get(
                        "current_pick_id"
                    )
                )

    return {
        "result_status": "PAUSED",
        "draft_key": draft_key,
        "current_pick_id": current_pick_id,
        "elapsed_seconds": elapsed,
        "paused_by": actor,
    }


def resume_nfhl_draft(
    *,
    actor: str,
) -> dict[str, Any]:
    """
    Resume a paused NFHL draft clock.
    """

    draft_key = get_draft_key()

    actor = str(
        actor or ""
    ).strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (
                        draft_key,
                    ),
                )

                cur.execute(
                    """
                    SELECT
                        d.status,
                        s.state_json
                    FROM nfhl.draft d
                    JOIN nfhl.draft_state s
                      ON s.draft_key = d.draft_key
                    WHERE d.draft_key = %s
                    FOR UPDATE OF d, s
                    """,
                    (
                        draft_key,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL draft/state does not exist."
                    )

                if str(
                    row["status"]
                    or ""
                ).upper() != "ACTIVE":
                    raise RuntimeError(
                        "Only an ACTIVE draft may be resumed."
                    )

                state = dict(
                    row["state_json"]
                    or {}
                )

                clock = dict(
                    state.get("clock")
                    or {}
                )

                if bool(
                    clock.get(
                        "is_running"
                    )
                ):
                    raise RuntimeError(
                        "NFHL draft clock is already running."
                    )

                current_pick_id = str(
                    clock.get(
                        "current_pick_id"
                    )
                    or ""
                ).strip()

                if not current_pick_id:
                    raise RuntimeError(
                        "Draft has no current pick to resume."
                    )

                cur.execute(
                    """
                    SELECT seconds_per_pick
                    FROM nfhl.draft_clock_config
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                config = cur.fetchone()

                if config is None:
                    raise RuntimeError(
                        "NFHL draft clock is not configured."
                    )

                elapsed = int(
                    clock.get(
                        "elapsed_paused_seconds"
                    )
                    or 0
                )

                if elapsed >= int(
                    config[
                        "seconds_per_pick"
                    ]
                ):
                    raise RuntimeError(
                        "The current pick has already consumed "
                        "its configured clock window."
                    )

                clock[
                    "is_running"
                ] = True

                clock[
                    "pick_started_ts_iso"
                ] = (
                    _nfhl_clock_iso_now()
                )

                clock[
                    "pick_paused_ts_iso"
                ] = None

                state["clock"] = clock

                _nfhl_write_state_locked(
                    cur,
                    draft_key=draft_key,
                    state=state,
                )

    return {
        "result_status": "RESUMED",
        "draft_key": draft_key,
        "current_pick_id": current_pick_id,
        "elapsed_seconds": elapsed,
        "resumed_by": actor,
    }


# NFHL_DRAFT_LIFECYCLE_DB_END


# NFHL_NFFL_CLOCK_ALIGNMENT_START
# ================================================================
# NFHL CLOCK STANDARD
#
# Ported from the proven generic NFFL slow-draft behavior:
#
#   24 hours per pick
#   reminders at 12h / 6h / 1h remaining
#   pause freezes elapsed time
#   late picks never disturb the active clock
#
# NFHL keeps its own database schema and hockey-only rules.
# ================================================================

NFHL_STANDARD_PICK_SECONDS = 24 * 60 * 60

NFHL_STANDARD_REMINDER_SECONDS = [
    12 * 60 * 60,
    6 * 60 * 60,
    1 * 60 * 60,
]


def apply_nfhl_standard_clock_config(
    *,
    actor: str,
) -> dict[str, Any]:
    """
    Apply the league-standard NFHL 24-hour clock.

    Uses the existing NFHL clock-config writer so reminder-code
    conventions remain native to the NFHL engine.
    """

    return save_draft_clock_config(
        seconds_per_pick=(
            NFHL_STANDARD_PICK_SECONDS
        ),
        reminder_seconds=list(
            NFHL_STANDARD_REMINDER_SECONDS
        ),
        actor=actor,
    )


def is_nfhl_standard_clock_configured() -> bool:
    """
    True only when NFHL has the canonical 24h / 12h / 6h / 1h
    clock configuration.
    """

    config = get_draft_clock_config()

    if not config.get("configured"):
        return False

    if int(
        config.get("seconds_per_pick")
        or 0
    ) != NFHL_STANDARD_PICK_SECONDS:
        return False

    if not bool(
        config.get("auto_advance")
    ):
        return False

    if not bool(
        config.get("weekends_count")
    ):
        return False

    actual_reminders = {
        int(
            reminder["seconds_remaining"]
        )
        for reminder
        in (
            config.get("reminders")
            or []
        )
        if reminder.get("enabled")
    }

    return actual_reminders == set(
        NFHL_STANDARD_REMINDER_SECONDS
    )


def set_nfhl_current_pick_remaining(
    *,
    remaining_seconds: int,
    actor: str,
) -> dict[str, Any]:
    """
    Commissioner adjustment for the CURRENT pick only.

    The league-wide rule remains 24 hours. This changes how much
    of the current manager's 24-hour window remains.

    This avoids the NFFL UI/runtime mismatch where changing
    seconds_per_pick away from 24h makes the production processor
    refuse to operate.

    Allowed remaining time:
        1 minute through 24 hours.

    To grant an indefinite extension, Pause the draft clock.
    """

    from datetime import datetime, timezone

    draft_key = get_draft_key()

    remaining_seconds = int(
        remaining_seconds
    )

    if remaining_seconds < 60:
        raise ValueError(
            "Remaining time must be at least one minute."
        )

    if (
        remaining_seconds
        > NFHL_STANDARD_PICK_SECONDS
    ):
        raise ValueError(
            "Remaining time cannot exceed the standard "
            "24-hour pick window. Pause the clock for a "
            "longer commissioner hold."
        )

    actor = str(
        actor or ""
    ).strip() or "commissioner"

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                # Same draft-wide lock used by clock/pick execution.
                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (
                        draft_key,
                    ),
                )

                # Enforce any deadline that was already due before
                # the commissioner attempted an adjustment.
                cur.execute(
                    """
                    SELECT *
                    FROM nfhl.process_draft_clock(%s)
                    """,
                    (
                        draft_key,
                    ),
                )

                cur.fetchone()

                cur.execute(
                    """
                    SELECT
                        d.status,
                        s.state_json
                    FROM nfhl.draft d
                    JOIN nfhl.draft_state s
                      ON s.draft_key = d.draft_key
                    WHERE d.draft_key = %s
                    FOR UPDATE OF d, s
                    """,
                    (
                        draft_key,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    raise RuntimeError(
                        "NFHL draft/state does not exist."
                    )

                if str(
                    row["status"]
                    or ""
                ).upper() != "ACTIVE":
                    raise RuntimeError(
                        "Current-pick clock adjustment requires "
                        "an ACTIVE draft."
                    )

                cur.execute(
                    """
                    SELECT
                        seconds_per_pick
                    FROM nfhl.draft_clock_config
                    WHERE draft_key = %s
                    """,
                    (
                        draft_key,
                    ),
                )

                config = cur.fetchone()

                if config is None:
                    raise RuntimeError(
                        "NFHL draft clock is not configured."
                    )

                if int(
                    config["seconds_per_pick"]
                ) != NFHL_STANDARD_PICK_SECONDS:
                    raise RuntimeError(
                        "NFHL clock is not configured for "
                        "the standard 24-hour format."
                    )

                state = dict(
                    row["state_json"]
                    or {}
                )

                clock = dict(
                    state.get("clock")
                    or {}
                )

                current_pick_id = str(
                    clock.get(
                        "current_pick_id"
                    )
                    or ""
                ).strip()

                if not current_pick_id:
                    raise RuntimeError(
                        "NFHL draft has no current pick."
                    )

                elapsed_seconds = (
                    NFHL_STANDARD_PICK_SECONDS
                    - remaining_seconds
                )

                now = datetime.now(
                    timezone.utc
                )

                now_iso = (
                    now.isoformat()
                    .replace(
                        "+00:00",
                        "Z",
                    )
                )

                is_running = bool(
                    clock.get(
                        "is_running"
                    )
                )

                clock[
                    "elapsed_paused_seconds"
                ] = elapsed_seconds

                if is_running:
                    # Start a fresh running segment now with the
                    # desired already-consumed amount preserved.
                    clock[
                        "pick_started_ts_iso"
                    ] = now_iso

                    clock[
                        "pick_paused_ts_iso"
                    ] = None

                else:
                    # Preserve paused semantics. Resume will start
                    # a new running segment from this accumulated
                    # elapsed value.
                    clock[
                        "pick_paused_ts_iso"
                    ] = now_iso

                state["clock"] = clock

                _nfhl_write_state_locked(
                    cur,
                    draft_key=draft_key,
                    state=state,
                )

                # If the commissioner resets/changes the window,
                # allow the current pick's reminders to fire again
                # against the revised deadline.
                cur.execute(
                    """
                    DELETE FROM nfhl.draft_clock_event
                    WHERE draft_key = %s
                      AND pick_id = %s
                      AND event_type LIKE 'REMINDER%%'
                    """,
                    (
                        draft_key,
                        current_pick_id,
                    ),
                )

    return {
        "result_status": "ADJUSTED",
        "draft_key": draft_key,
        "current_pick_id": current_pick_id,
        "remaining_seconds": (
            remaining_seconds
        ),
        "adjusted_by": actor,
    }


# NFHL_NFFL_CLOCK_ALIGNMENT_END


# NFHL_AUTOPICK_DB_HELPERS_START
# ================================================================
# NFHL DRAFT QUEUE / AUTO-PICK
#
# Queue:
#   - maximum five ranked players
#   - one instance of each player per team
#
# Auto-Pick:
#   - OFF by default
#   - armed only for the team's exact next open pick
#   - one-shot execution is enforced by PostgreSQL
# ================================================================


def get_autopick_open_picks(
    team_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return currently unselected draft picks.

    If team_key is supplied, return only that team's open picks.
    """

    team_key = str(
        team_key or ""
    ).strip()

    if team_key:
        return _fetch_all(
            """
            SELECT
                v.pick_id,
                v.round_number,
                v.slot_number,
                v.current_owner_team_key,
                v.current_owner_team_name

            FROM nfhl.v_draft_board_current v

            WHERE v.draft_key = %s
              AND v.selected_at_utc IS NULL
              AND v.current_owner_team_key = %s

            ORDER BY
                v.round_number,
                v.slot_number
            """,
            (
                get_draft_key(),
                team_key,
            ),
        )

    return _fetch_all(
        """
        SELECT
            v.pick_id,
            v.round_number,
            v.slot_number,
            v.current_owner_team_key,
            v.current_owner_team_name

        FROM nfhl.v_draft_board_current v

        WHERE v.draft_key = %s
          AND v.selected_at_utc IS NULL

        ORDER BY
            v.round_number,
            v.slot_number
        """,
        (
            get_draft_key(),
        ),
    )


def get_autopick_state(
    team_key: str,
) -> dict[str, Any]:
    """
    Read one team's Auto-Pick control and ranked queue.

    Missing control rows mean Auto-Pick is OFF.
    This function never creates database rows.
    """

    team_key = str(
        team_key or ""
    ).strip()

    if not team_key:
        raise ValueError(
            "team_key is required."
        )

    control_rows = _fetch_all(
        """
        SELECT
            enabled,
            armed_pick_id,
            updated_at_utc,
            updated_by

        FROM nfhl.draft_autopick_control

        WHERE draft_key = %s
          AND team_key = %s
        """,
        (
            get_draft_key(),
            team_key,
        ),
    )

    queue = _fetch_all(
        """
        SELECT
            q.queue_rank,
            q.yahoo_player_key,
            p.full_name,
            p.nhl_team_abbr,
            p.primary_position,
            p.eligible_positions

        FROM nfhl.draft_autopick_queue q

        JOIN nfhl.player_universe p
          ON p.league_key = %s
         AND p.season_year = %s
         AND p.yahoo_player_key = q.yahoo_player_key

        WHERE q.draft_key = %s
          AND q.team_key = %s

        ORDER BY q.queue_rank
        """,
        (
            get_league_key(),
            get_season_year(),
            get_draft_key(),
            team_key,
        ),
    )

    if control_rows:
        control = control_rows[0]

        enabled = bool(
            control["enabled"]
        )

        armed_pick_id = (
            control["armed_pick_id"]
        )

        updated_at_utc = (
            control["updated_at_utc"]
        )

        updated_by = (
            control["updated_by"]
        )

    else:
        enabled = False
        armed_pick_id = None
        updated_at_utc = None
        updated_by = None

    return {
        "team_key": team_key,
        "enabled": enabled,
        "armed_pick_id": armed_pick_id,
        "updated_at_utc": updated_at_utc,
        "updated_by": updated_by,
        "queue": queue,
    }


def save_autopick_queue(
    *,
    team_key: str,
    player_keys: list[str],
    actor: str,
) -> None:
    """
    Replace one team's ranked Auto-Pick queue.

    Editing the queue automatically DISARMS Auto-Pick.
    The manager must explicitly arm again after any queue change.
    """

    team_key = str(
        team_key or ""
    ).strip()

    actor = str(
        actor or ""
    ).strip() or "manager"

    if not team_key:
        raise ValueError(
            "team_key is required."
        )

    normalized: list[str] = []

    for value in player_keys:
        player_key = str(
            value or ""
        ).strip()

        if not player_key:
            continue

        if player_key in normalized:
            raise ValueError(
                "The same player cannot appear "
                "more than once in the queue."
            )

        normalized.append(
            player_key
        )

    if len(normalized) > 5:
        raise ValueError(
            "NFHL Auto-Pick queues are limited "
            "to five players."
        )

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                # ------------------------------------------------
                # Team must be a real current-season NFHL team.
                # ------------------------------------------------

                cur.execute(
                    """
                    SELECT 1
                    FROM nfhl.team
                    WHERE league_key = %s
                      AND season_year = %s
                      AND team_key = %s
                    """,
                    (
                        get_league_key(),
                        get_season_year(),
                        team_key,
                    ),
                )

                if cur.fetchone() is None:
                    raise RuntimeError(
                        "NFHL team does not exist "
                        "for the current season."
                    )

                # ------------------------------------------------
                # Validate every queued player.
                # ------------------------------------------------

                for player_key in normalized:

                    cur.execute(
                        """
                        SELECT 1
                        FROM nfhl.player_universe
                        WHERE league_key = %s
                          AND season_year = %s
                          AND yahoo_player_key = %s
                        """,
                        (
                            get_league_key(),
                            get_season_year(),
                            player_key,
                        ),
                    )

                    if cur.fetchone() is None:
                        raise RuntimeError(
                            "Queued player is not in the "
                            "current NFHL player universe: "
                            f"{player_key}"
                        )

                    cur.execute(
                        """
                        SELECT 1
                        FROM nfhl.draft_selection
                        WHERE draft_key = %s
                          AND yahoo_player_key = %s
                        """,
                        (
                            get_draft_key(),
                            player_key,
                        ),
                    )

                    if cur.fetchone() is not None:
                        raise RuntimeError(
                            "A player already drafted cannot "
                            "be added to the Auto-Pick queue."
                        )

                # ------------------------------------------------
                # Queue rows require a control parent.
                # Auto-Pick remains OFF by default.
                # ------------------------------------------------

                cur.execute(
                    """
                    INSERT INTO nfhl.draft_autopick_control (
                        draft_key,
                        team_key,
                        enabled,
                        armed_pick_id,
                        updated_at_utc,
                        updated_by
                    )
                    VALUES (
                        %s,
                        %s,
                        false,
                        NULL,
                        now(),
                        %s
                    )

                    ON CONFLICT (
                        draft_key,
                        team_key
                    )
                    DO UPDATE SET
                        enabled = false,
                        armed_pick_id = NULL,
                        updated_at_utc = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        get_draft_key(),
                        team_key,
                        actor,
                    ),
                )

                cur.execute(
                    """
                    DELETE FROM nfhl.draft_autopick_queue
                    WHERE draft_key = %s
                      AND team_key = %s
                    """,
                    (
                        get_draft_key(),
                        team_key,
                    ),
                )

                for rank, player_key in enumerate(
                    normalized,
                    start=1,
                ):
                    cur.execute(
                        """
                        INSERT INTO nfhl.draft_autopick_queue (
                            draft_key,
                            team_key,
                            queue_rank,
                            yahoo_player_key,
                            created_at_utc,
                            updated_at_utc
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            now(),
                            now()
                        )
                        """,
                        (
                            get_draft_key(),
                            team_key,
                            rank,
                            player_key,
                        ),
                    )


def arm_autopick(
    *,
    team_key: str,
    pick_id: str,
    actor: str,
) -> None:
    """
    Arm one-shot Auto-Pick for the team's exact next open pick.
    """

    team_key = str(
        team_key or ""
    ).strip()

    pick_id = str(
        pick_id or ""
    ).strip()

    actor = str(
        actor or ""
    ).strip() or "manager"

    if not team_key:
        raise ValueError(
            "team_key is required."
        )

    if not pick_id:
        raise ValueError(
            "pick_id is required."
        )

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                # Auto-Pick is meaningful only during a live draft.
                cur.execute(
                    """
                    SELECT status
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    FOR UPDATE
                    """,
                    (
                        get_draft_key(),
                    ),
                )

                draft = cur.fetchone()

                if (
                    draft is None
                    or str(
                        draft["status"]
                    ).upper()
                    != "ACTIVE"
                ):
                    raise RuntimeError(
                        "Auto-Pick may only be armed "
                        "while the NFHL draft is ACTIVE."
                    )

                # Exact next open pick for this team.
                cur.execute(
                    """
                    SELECT
                        dp.pick_id

                    FROM nfhl.draft_pick dp

                    LEFT JOIN nfhl.draft_selection ds
                      ON ds.draft_key = dp.draft_key
                     AND ds.pick_id = dp.pick_id

                    WHERE dp.draft_key = %s
                      AND dp.current_owner_team_key = %s
                      AND ds.pick_id IS NULL

                    ORDER BY
                        dp.round_number,
                        dp.slot_number

                    LIMIT 1
                    """,
                    (
                        get_draft_key(),
                        team_key,
                    ),
                )

                next_pick = cur.fetchone()

                if next_pick is None:
                    raise RuntimeError(
                        "This team has no open NFHL draft pick."
                    )

                if str(
                    next_pick["pick_id"]
                ) != pick_id:
                    raise RuntimeError(
                        "Auto-Pick may only be armed for "
                        "the team's exact next open pick."
                    )

                cur.execute(
                    """
                    SELECT COUNT(*) AS queue_count
                    FROM nfhl.draft_autopick_queue
                    WHERE draft_key = %s
                      AND team_key = %s
                    """,
                    (
                        get_draft_key(),
                        team_key,
                    ),
                )

                queue_count = int(
                    cur.fetchone()[
                        "queue_count"
                    ]
                )

                if queue_count <= 0:
                    raise RuntimeError(
                        "Add at least one player to the "
                        "Auto-Pick queue before arming."
                    )

                cur.execute(
                    """
                    INSERT INTO nfhl.draft_autopick_control (
                        draft_key,
                        team_key,
                        enabled,
                        armed_pick_id,
                        updated_at_utc,
                        updated_by
                    )
                    VALUES (
                        %s,
                        %s,
                        true,
                        %s,
                        now(),
                        %s
                    )

                    ON CONFLICT (
                        draft_key,
                        team_key
                    )
                    DO UPDATE SET
                        enabled = true,
                        armed_pick_id = EXCLUDED.armed_pick_id,
                        updated_at_utc = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        get_draft_key(),
                        team_key,
                        pick_id,
                        actor,
                    ),
                )


def disable_autopick(
    *,
    team_key: str,
    actor: str,
) -> None:
    """
    Disable Auto-Pick and clear the armed pick.
    """

    team_key = str(
        team_key or ""
    ).strip()

    actor = str(
        actor or ""
    ).strip() or "manager"

    if not team_key:
        raise ValueError(
            "team_key is required."
        )

    with psycopg.connect(
        get_postgres_dsn(),
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    INSERT INTO nfhl.draft_autopick_control (
                        draft_key,
                        team_key,
                        enabled,
                        armed_pick_id,
                        updated_at_utc,
                        updated_by
                    )
                    VALUES (
                        %s,
                        %s,
                        false,
                        NULL,
                        now(),
                        %s
                    )

                    ON CONFLICT (
                        draft_key,
                        team_key
                    )
                    DO UPDATE SET
                        enabled = false,
                        armed_pick_id = NULL,
                        updated_at_utc = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        get_draft_key(),
                        team_key,
                        actor,
                    ),
                )


# NFHL_AUTOPICK_DB_HELPERS_END


# NFHL_TEAM_GATEWAY_DB_START
# ================================================================
# NFHL TEAM GATEWAY
# ================================================================


def ensure_team_gateway_links() -> int:
    """
    Create links only for real teams that currently exist in
    nfhl.team. Existing links are never regenerated here.
    """

    import secrets

    league_key = get_league_key()
    season_year = get_season_year()

    created = 0

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT t.team_key
                    FROM nfhl.team t

                    LEFT JOIN nfhl.team_gateway_link g
                      ON g.league_key = t.league_key
                     AND g.season_year = t.season_year
                     AND g.team_key = t.team_key

                    WHERE t.league_key = %s
                      AND t.season_year = %s
                      AND g.team_key IS NULL

                    ORDER BY t.team_id::integer
                    """,
                    (
                        league_key,
                        season_year,
                    ),
                )

                missing = [
                    str(row["team_key"])
                    for row in cur.fetchall()
                ]

                for team_key in missing:

                    token = secrets.token_urlsafe(32)

                    cur.execute(
                        """
                        INSERT INTO nfhl.team_gateway_link (
                            league_key,
                            season_year,
                            team_key,
                            link_token
                        )
                        VALUES (%s, %s, %s, %s)

                        ON CONFLICT (
                            league_key,
                            season_year,
                            team_key
                        )
                        DO NOTHING
                        """,
                        (
                            league_key,
                            season_year,
                            team_key,
                            token,
                        ),
                    )

                    created += cur.rowcount

    return created


def get_team_gateway_links() -> list[dict[str, Any]]:
    """
    Commissioner-facing manager link inventory.
    """

    return _fetch_all(
        """
        SELECT
            t.team_id,
            t.team_key,
            t.team_name,
            t.owner_name,
            g.link_token,
            g.is_active,
            g.claim_count,
            g.last_claimed_at_utc

        FROM nfhl.team t

        LEFT JOIN nfhl.team_gateway_link g
          ON g.league_key = t.league_key
         AND g.season_year = t.season_year
         AND g.team_key = t.team_key

        WHERE t.league_key = %s
          AND t.season_year = %s

        ORDER BY t.team_id::integer
        """,
        (
            get_league_key(),
            get_season_year(),
        ),
    )


def claim_team_gateway_link(
    link_token: str,
) -> dict[str, Any] | None:
    """
    Validate and record use of one active current-season team link.
    """

    token = str(link_token or "").strip()

    if not token:
        return None

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        l.league_key,
                        l.season_year,
                        l.team_key,
                        t.team_name,
                        t.owner_name

                    FROM nfhl.team_gateway_link l

                    JOIN nfhl.team t
                      ON t.league_key = l.league_key
                     AND t.season_year = l.season_year
                     AND t.team_key = l.team_key

                    WHERE l.link_token = %s
                      AND l.is_active = true
                      AND l.league_key = %s
                      AND l.season_year = %s

                    LIMIT 1
                    """,
                    (
                        token,
                        get_league_key(),
                        get_season_year(),
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    return None

                cur.execute(
                    """
                    UPDATE nfhl.team_gateway_link
                    SET
                        claim_count = claim_count + 1,
                        last_claimed_at_utc = now(),
                        updated_at_utc = now()

                    WHERE link_token = %s
                      AND is_active = true
                    """,
                    (token,),
                )

    return dict(row)


def write_team_gateway_audit(
    *,
    selected_role: str,
    selected_team_key: str | None,
    selected_team_name: str | None,
    previous_role: str | None,
    previous_team_key: str | None,
    previous_team_name: str | None,
    action_type: str,
    action_note: str | None,
    query_string: str | None,
) -> None:
    """
    Durable NFHL-local gateway audit event.
    """

    with psycopg.connect(
        get_postgres_dsn(),
    ) as conn:

        with conn.transaction():

            with conn.cursor() as cur:

                cur.execute(
                    """
                    INSERT INTO nfhl.team_gateway_audit (
                        league_key,
                        season_year,
                        selected_role,
                        selected_team_key,
                        selected_team_name,
                        previous_role,
                        previous_team_key,
                        previous_team_name,
                        action_type,
                        action_note,
                        query_string
                    )
                    VALUES (
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    (
                        get_league_key(),
                        get_season_year(),
                        selected_role,
                        selected_team_key,
                        selected_team_name,
                        previous_role,
                        previous_team_key,
                        previous_team_name,
                        action_type,
                        action_note,
                        query_string,
                    ),
                )


# NFHL_TEAM_GATEWAY_DB_END
