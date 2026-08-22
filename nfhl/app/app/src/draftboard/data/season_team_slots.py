from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


LEAGUE_KEY = "477.l.10961"
SEASON_YEAR = 2026
DRAFT_KEY = "nfhl_2026_preseason"


def _connect() -> psycopg.Connection:
    """
    Use the same runtime database configuration already supplied
    to the NFHL container.

    Prefer an existing DSN helper from draftboard.data.db when
    available, then fall back to common environment variables.
    """
    try:
        from draftboard.data import db as existing_db

        for name in (
            "_get_dsn",
            "get_dsn",
            "_dsn",
        ):
            fn = getattr(
                existing_db,
                name,
                None,
            )

            if not callable(fn):
                continue

            try:
                dsn = fn()
            except TypeError:
                continue

            if dsn:
                return psycopg.connect(
                    str(dsn)
                )

        for name in (
            "DATABASE_URL",
            "DB_DSN",
            "POSTGRES_DSN",
        ):
            value = getattr(
                existing_db,
                name,
                None,
            )

            if value:
                return psycopg.connect(
                    str(value)
                )

    except Exception:
        pass

    for env_name in (
        "DATABASE_URL",
        "DB_DSN",
        "POSTGRES_DSN",
        "DRAFTBOARD_DATABASE_URL",
    ):
        dsn = str(
            os.environ.get(
                env_name,
                "",
            )
            or ""
        ).strip()

        if dsn:
            return psycopg.connect(
                dsn
            )

    user = str(
        os.environ.get(
            "POSTGRES_USER",
            "",
        )
        or ""
    ).strip()

    password = str(
        os.environ.get(
            "POSTGRES_PASSWORD",
            "",
        )
        or ""
    )

    database = str(
        os.environ.get(
            "POSTGRES_DB",
            "",
        )
        or user
    ).strip()

    host = str(
        os.environ.get(
            "POSTGRES_HOST",
            "mlf_postgres",
        )
        or "mlf_postgres"
    ).strip()

    port = int(
        os.environ.get(
            "POSTGRES_PORT",
            "5432",
        )
        or 5432
    )

    if user:
        return psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
        )

    raise RuntimeError(
        "Unable to resolve NFHL PostgreSQL connection configuration."
    )


def get_season_team_slots() -> list[dict[str, Any]]:
    sql = """
        SELECT
            s.league_slot_number,
            s.prior_season_year,
            s.prior_manager_name,
            s.prior_team_name,
            s.assignment_status,
            s.replacement_manager_name,
            s.replacement_team_name,
            s.current_team_key,
            s.commissioner_note,
            s.updated_at_utc,

            t.team_id AS current_team_id,
            t.team_name AS current_team_name,
            t.owner_name AS current_owner_name

        FROM nfhl.season_team_slot s

        LEFT JOIN nfhl.team t
          ON t.league_key = s.league_key
         AND t.season_year = s.season_year
         AND t.team_key = s.current_team_key

        WHERE s.league_key = %s
          AND s.season_year = %s

        ORDER BY s.league_slot_number
    """

    with _connect() as conn:
        with conn.cursor(
            row_factory=dict_row
        ) as cur:
            cur.execute(
                sql,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            return [
                dict(row)
                for row in cur.fetchall()
            ]


def get_preview_board_meta() -> dict[str, Any]:
    sql = """
        SELECT
            d.status,
            d.rounds_total,

            (
                SELECT COUNT(*)
                FROM nfhl.draft_pick p
                WHERE p.draft_key = d.draft_key
            ) AS draft_pick_rows,

            (
                SELECT COUNT(*)
                FROM nfhl.draft_selection s
                WHERE s.draft_key = d.draft_key
            ) AS draft_selections

        FROM nfhl.draft d

        WHERE d.draft_key = %s
    """

    with _connect() as conn:
        with conn.cursor(
            row_factory=dict_row
        ) as cur:
            cur.execute(
                sql,
                (DRAFT_KEY,),
            )

            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            f"NFHL draft not found: {DRAFT_KEY}"
        )

    return dict(row)


def auto_match_season_team_slots() -> dict[str, int]:
    """
    Automatically resolve returning NFHL managers.

    A slot is auto-linked only when:
    - the slot is still PENDING and unassigned;
    - the prior manager name is nonblank;
    - exactly one current Yahoo team has that manager name;
    - exactly one prior-season slot has that manager name;
    - the current Yahoo team is not already assigned elsewhere.

    Matching is case-insensitive and ignores surrounding whitespace.

    Team names are deliberately NOT used for identity matching.
    A renamed team remains a returning manager. A replacement manager
    reusing an old team name must not be classified as RETURNING.

    Existing RETURNING/REPLACED/manual mappings are never overwritten.

    PREP only.
    """
    with _connect() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT status
                FROM nfhl.draft
                WHERE draft_key = %s
                FOR UPDATE
                """,
                (DRAFT_KEY,),
            )

            row = cur.fetchone()

            if row is None:
                raise RuntimeError(
                    f"Draft not found: {DRAFT_KEY}"
                )

            if str(row[0] or "").upper() != "PREP":
                raise RuntimeError(
                    "Season-team assignments may only be "
                    "changed while the draft is PREP."
                )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM nfhl.season_team_slot
                WHERE league_key = %s
                  AND season_year = %s
                  AND current_team_key IS NOT NULL
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            before = int(
                cur.fetchone()[0]
                or 0
            )

            cur.execute(
                """
                WITH candidates AS (
                    SELECT
                        s.league_slot_number,
                        t.team_key

                    FROM nfhl.season_team_slot s

                    JOIN nfhl.team t
                      ON t.league_key = s.league_key
                     AND t.season_year = s.season_year
                     AND NULLIF(
                            trim(t.owner_name),
                            ''
                         ) IS NOT NULL
                     AND lower(trim(t.owner_name))
                         = lower(trim(s.prior_manager_name))

                    WHERE s.league_key = %s
                      AND s.season_year = %s
                      AND s.current_team_key IS NULL
                      AND s.assignment_status = 'PENDING'

                      AND NULLIF(
                            trim(s.prior_manager_name),
                            ''
                          ) IS NOT NULL

                      AND (
                          SELECT COUNT(*)
                          FROM nfhl.team t2
                          WHERE t2.league_key = s.league_key
                            AND t2.season_year = s.season_year
                            AND NULLIF(
                                  trim(t2.owner_name),
                                  ''
                                ) IS NOT NULL
                            AND lower(
                                  trim(t2.owner_name)
                                )
                                = lower(
                                  trim(
                                    s.prior_manager_name
                                  )
                                )
                      ) = 1

                      AND (
                          SELECT COUNT(*)
                          FROM nfhl.season_team_slot s2
                          WHERE s2.league_key = s.league_key
                            AND s2.season_year = s.season_year
                            AND NULLIF(
                                  trim(
                                    s2.prior_manager_name
                                  ),
                                  ''
                                ) IS NOT NULL
                            AND lower(
                                  trim(
                                    s2.prior_manager_name
                                  )
                                )
                                = lower(
                                  trim(
                                    s.prior_manager_name
                                  )
                                )
                      ) = 1

                      AND NOT EXISTS (
                          SELECT 1
                          FROM nfhl.season_team_slot other
                          WHERE other.league_key = s.league_key
                            AND other.season_year = s.season_year
                            AND other.current_team_key = t.team_key
                      )
                )

                UPDATE nfhl.season_team_slot s

                SET
                    current_team_key = c.team_key,
                    assignment_status = 'RETURNING',
                    replacement_manager_name = NULL,
                    replacement_team_name = NULL,
                    updated_at_utc = now()

                FROM candidates c

                WHERE s.league_key = %s
                  AND s.season_year = %s
                  AND s.league_slot_number
                      = c.league_slot_number
                  AND s.current_team_key IS NULL
                  AND s.assignment_status = 'PENDING'
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM nfhl.season_team_slot
                WHERE league_key = %s
                  AND season_year = %s
                  AND current_team_key IS NOT NULL
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            after = int(
                cur.fetchone()[0]
                or 0
            )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM nfhl.season_team_slot
                WHERE league_key = %s
                  AND season_year = %s
                  AND assignment_status = 'PENDING'
                  AND current_team_key IS NULL
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            unresolved = int(
                cur.fetchone()[0]
                or 0
            )

            cur.execute(
                """
                SELECT COUNT(*)

                FROM nfhl.season_team_slot s

                WHERE s.league_key = %s
                  AND s.season_year = %s
                  AND s.assignment_status = 'PENDING'
                  AND s.current_team_key IS NULL

                  AND (
                      SELECT COUNT(*)
                      FROM nfhl.team t
                      WHERE t.league_key = s.league_key
                        AND t.season_year = s.season_year
                        AND NULLIF(
                              trim(t.owner_name),
                              ''
                            ) IS NOT NULL
                        AND lower(trim(t.owner_name))
                            = lower(
                              trim(
                                s.prior_manager_name
                              )
                            )
                  ) > 1
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                ),
            )

            ambiguous = int(
                cur.fetchone()[0]
                or 0
            )

    return {
        "before": before,
        "after": after,
        "matched": max(
            0,
            after - before,
        ),
        "unresolved": unresolved,
        "ambiguous": ambiguous,
    }


def save_season_team_slot_assignment(
    *,
    league_slot_number: int,
    assignment_status: str,
    current_team_key: str | None,
    replacement_manager_name: str | None,
    replacement_team_name: str | None,
    commissioner_note: str | None,
) -> None:
    slot_number = int(
        league_slot_number
    )

    status = str(
        assignment_status
        or ""
    ).strip().upper()

    if slot_number < 1 or slot_number > 14:
        raise ValueError(
            "League slot must be between 1 and 14."
        )

    if status not in {
        "PENDING",
        "RETURNING",
        "REPLACED",
    }:
        raise ValueError(
            f"Invalid assignment status: {status}"
        )

    team_key = (
        str(current_team_key).strip()
        if current_team_key
        else None
    )

    replacement_manager = (
        str(
            replacement_manager_name
            or ""
        ).strip()
        or None
    )

    replacement_team = (
        str(
            replacement_team_name
            or ""
        ).strip()
        or None
    )

    note = (
        str(
            commissioner_note
            or ""
        ).strip()
        or None
    )

    if status == "PENDING":
        team_key = None
        replacement_manager = None
        replacement_team = None

    if status == "RETURNING":
        if not team_key:
            raise ValueError(
                "RETURNING requires a real 2026 Yahoo team."
            )

        replacement_manager = None
        replacement_team = None

    with _connect() as conn:
        with conn.cursor(
            row_factory=dict_row
        ) as cur:

            cur.execute(
                """
                SELECT status
                FROM nfhl.draft
                WHERE draft_key = %s
                FOR UPDATE
                """,
                (DRAFT_KEY,),
            )

            draft_row = cur.fetchone()

            if draft_row is None:
                raise RuntimeError(
                    f"Draft not found: {DRAFT_KEY}"
                )

            if str(
                draft_row["status"]
                or ""
            ).upper() != "PREP":
                raise RuntimeError(
                    "League assignments are locked after the draft leaves PREP."
                )

            cur.execute(
                """
                SELECT
                    league_slot_number,
                    current_team_key
                FROM nfhl.season_team_slot
                WHERE league_key = %s
                  AND season_year = %s
                  AND league_slot_number = %s
                FOR UPDATE
                """,
                (
                    LEAGUE_KEY,
                    SEASON_YEAR,
                    slot_number,
                ),
            )

            slot_row = cur.fetchone()

            if slot_row is None:
                raise RuntimeError(
                    f"League slot {slot_number} does not exist."
                )

            current_team = None

            if team_key:

                cur.execute(
                    """
                    SELECT
                        team_key,
                        team_name,
                        owner_name
                    FROM nfhl.team
                    WHERE league_key = %s
                      AND season_year = %s
                      AND team_key = %s
                    """,
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        team_key,
                    ),
                )

                current_team = cur.fetchone()

                if current_team is None:
                    raise ValueError(
                        "Selected Yahoo team does not exist in the current NFHL team table."
                    )

                cur.execute(
                    """
                    SELECT league_slot_number
                    FROM nfhl.season_team_slot
                    WHERE league_key = %s
                      AND season_year = %s
                      AND current_team_key = %s
                      AND league_slot_number <> %s
                    """,
                    (
                        LEAGUE_KEY,
                        SEASON_YEAR,
                        team_key,
                        slot_number,
                    ),
                )

                conflict = cur.fetchone()

                if conflict is not None:
                    raise ValueError(
                        "That Yahoo team is already assigned "
                        f"to league slot {int(conflict['league_slot_number'])}."
                    )

            if (
                status == "REPLACED"
                and current_team is not None
            ):
                if not replacement_manager:
                    replacement_manager = (
                        str(
                            current_team[
                                "owner_name"
                            ]
                            or ""
                        ).strip()
                        or None
                    )

                if not replacement_team:
                    replacement_team = (
                        str(
                            current_team[
                                "team_name"
                            ]
                            or ""
                        ).strip()
                        or None
                    )

            cur.execute(
                """
                UPDATE nfhl.season_team_slot
                SET
                    assignment_status = %s,
                    current_team_key = %s,
                    replacement_manager_name = %s,
                    replacement_team_name = %s,
                    commissioner_note = %s,
                    updated_at_utc = now()

                WHERE league_key = %s
                  AND season_year = %s
                  AND league_slot_number = %s
                """,
                (
                    status,
                    team_key,
                    replacement_manager,
                    replacement_team,
                    note,
                    LEAGUE_KEY,
                    SEASON_YEAR,
                    slot_number,
                ),
            )

            if cur.rowcount != 1:
                raise RuntimeError(
                    "Season-team-slot update did not affect exactly one row."
                )
