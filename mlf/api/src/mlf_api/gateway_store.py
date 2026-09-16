from __future__ import annotations

import secrets
from typing import Any

import psycopg
from psycopg.rows import dict_row

from draftboard.state.runtime import (
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


def ensure_team_gateway_links() -> int:
    league_key = str(get_league_key())
    season_year = int(get_season_year())
    created = 0

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        fst.franchise_id,
                        fst.team_key
                    FROM public.franchise_season_team fst
                    LEFT JOIN mlf.team_gateway_link g
                      ON g.league_key = fst.league_key
                     AND g.season_year = fst.season_year
                     AND g.team_key = fst.team_key
                    WHERE fst.league_key = %s
                      AND fst.season_year = %s
                      AND g.team_key IS NULL
                    ORDER BY fst.franchise_id
                    """,
                    (
                        league_key,
                        season_year,
                    ),
                )

                missing = cur.fetchall()

                for row in missing:
                    token = secrets.token_urlsafe(32)

                    cur.execute(
                        """
                        INSERT INTO mlf.team_gateway_link (
                            league_key,
                            season_year,
                            franchise_id,
                            team_key,
                            link_token
                        )
                        VALUES (%s, %s, %s, %s, %s)
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
                            int(row["franchise_id"]),
                            str(row["team_key"]),
                            token,
                        ),
                    )

                    created += cur.rowcount

    return created


def claim_team_gateway_link(
    link_token: str,
) -> dict[str, Any] | None:
    token = str(link_token or "").strip()

    if not token:
        return None

    league_key = str(get_league_key())
    season_year = int(get_season_year())

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        g.franchise_id,
                        g.team_key,
                        fst.team_name,
                        fst.owner_name
                    FROM mlf.team_gateway_link g
                    JOIN public.franchise_season_team fst
                      ON fst.franchise_id = g.franchise_id
                     AND fst.season_year = g.season_year
                     AND fst.league_key = g.league_key
                     AND fst.team_key = g.team_key
                    WHERE g.link_token = %s
                      AND g.is_active = true
                      AND g.league_key = %s
                      AND g.season_year = %s
                    LIMIT 1
                    FOR UPDATE OF g
                    """,
                    (
                        token,
                        league_key,
                        season_year,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    return None

                cur.execute(
                    """
                    UPDATE mlf.team_gateway_link
                    SET
                        claim_count = claim_count + 1,
                        last_claimed_at_utc = now(),
                        updated_at_utc = now()
                    WHERE link_token = %s
                      AND league_key = %s
                      AND season_year = %s
                      AND is_active = true
                    """,
                    (
                        token,
                        league_key,
                        season_year,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO mlf.team_gateway_audit (
                        league_key,
                        season_year,
                        selected_role,
                        selected_franchise_id,
                        selected_team_key,
                        selected_team_name,
                        action_type,
                        action_note
                    )
                    VALUES (
                        %s,
                        %s,
                        'manager',
                        %s,
                        %s,
                        %s,
                        'CLAIM_TEAM_LINK',
                        'Browser gateway identity set from manager team link.'
                    )
                    """,
                    (
                        league_key,
                        season_year,
                        int(row["franchise_id"]),
                        str(row["team_key"]),
                        str(
                            row["team_name"]
                            or row["team_key"]
                        ),
                    ),
                )

    return dict(row)


def record_clear_browser(
    *,
    franchise_id: int,
    team_key: str,
    team_name: str | None,
) -> None:
    league_key = str(get_league_key())
    season_year = int(get_season_year())

    with psycopg.connect(
        get_postgres_dsn()
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mlf.team_gateway_audit (
                    league_key,
                    season_year,
                    selected_role,
                    selected_franchise_id,
                    selected_team_key,
                    selected_team_name,
                    action_type,
                    action_note
                )
                VALUES (
                    %s,
                    %s,
                    'public',
                    %s,
                    %s,
                    %s,
                    'CLEAR_BROWSER',
                    'Browser gateway identity cleared.'
                )
                """,
                (
                    league_key,
                    season_year,
                    int(franchise_id),
                    str(team_key),
                    (
                        str(team_name)
                        if team_name is not None
                        else None
                    ),
                ),
            )

        conn.commit()