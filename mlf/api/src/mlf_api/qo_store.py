from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from draftboard.data.db_players import (
    load_available_players,
)
from draftboard.data.draft_runtime import (
    replace_team_predraft_qos_atomic,
)
from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


class QORequestError(ValueError):
    pass


class QOConflict(RuntimeError):
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


def _validate_player_keys(
    values: list[str],
) -> list[str]:
    players = [
        str(value or "").strip()
        for value in (
            values or []
        )
    ]

    if len(players) != 5:
        raise QORequestError(
            "Exactly five QO players are required."
        )

    if any(not player for player in players):
        raise QORequestError(
            "Every QO level requires a player."
        )

    if len(set(players)) != 5:
        raise QORequestError(
            "QO players must be unique."
        )

    return players


def get_commissioner_qo_state(
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
                    d.qo_rounds,
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

            raw_teams = cur.fetchall()

            cur.execute(
                """
                SELECT
                    team_key,
                    qo_level,
                    yahoo_player_key
                FROM mlf.qualifying_offer
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY
                    team_key,
                    qo_level
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            predraft_rows = cur.fetchall()

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
                (draft_key,),
            )

            current_rows = cur.fetchall()

            cur.execute(
                """
                SELECT yahoo_player_key
                FROM mlf.v_active_contract
                WHERE league_key = %s
                  AND season_year = %s
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            contracted = {
                str(
                    row["yahoo_player_key"]
                )
                for row in cur.fetchall()
            }

            cur.execute(
                """
                WITH public_rows AS (
                    SELECT
                        team_key,
                        qo_level,
                        yahoo_player_key
                    FROM public.qualifying_offer
                    WHERE league_key = %s
                      AND season_year = %s
                ),
                mlf_rows AS (
                    SELECT
                        team_key,
                        qo_level,
                        yahoo_player_key
                    FROM mlf.qualifying_offer
                    WHERE league_key = %s
                      AND season_year = %s
                )
                SELECT
                    (
                        SELECT count(*)
                        FROM (
                            SELECT *
                            FROM public_rows

                            EXCEPT

                            SELECT *
                            FROM mlf_rows
                        ) AS public_only
                    ) AS public_only,
                    (
                        SELECT count(*)
                        FROM (
                            SELECT *
                            FROM mlf_rows

                            EXCEPT

                            SELECT *
                            FROM public_rows
                        ) AS mlf_only
                    ) AS mlf_only
                """,
                (
                    league_key,
                    season_year,
                    league_key,
                    season_year,
                ),
            )

            diff = cur.fetchone()

    baseline_synced = (
        int(diff["public_only"]) == 0
        and int(diff["mlf_only"]) == 0
    )

    selection_count = int(
        draft["selection_count"]
    )

    qo_rounds = int(
        draft["qo_rounds"]
    )

    can_edit = (
        selection_count == 0
        and qo_rounds == 5
        and baseline_synced
    )

    lock_reason: str | None = None

    if selection_count != 0:
        lock_reason = (
            "Predraft qualifying offers are "
            f"locked because {selection_count} "
            "draft selections already exist."
        )
    elif qo_rounds != 5:
        lock_reason = (
            "Active draft does not have "
            "five qualifying-offer rounds."
        )
    elif not baseline_synced:
        lock_reason = (
            "QO compatibility mirror is "
            "out of sync with relational truth."
        )

    predraft_by_team: dict[
        str,
        list[str | None],
    ] = {}

    current_by_team: dict[
        str,
        list[str | None],
    ] = {}

    qo_owner_by_player: dict[
        str,
        str,
    ] = {}

    all_qo_keys: set[str] = set()

    for row in predraft_rows:
        team_key = str(
            row["team_key"]
        )

        level = int(
            row["qo_level"]
        )

        player_key = str(
            row["yahoo_player_key"]
        )

        slots = predraft_by_team.setdefault(
            team_key,
            [None, None, None, None, None],
        )

        if 1 <= level <= 5:
            slots[level - 1] = player_key

        qo_owner_by_player[
            player_key
        ] = team_key

        all_qo_keys.add(
            player_key
        )

    for row in current_rows:
        team_key = str(
            row["team_key"]
        )

        level = int(
            row["qo_level"]
        )

        player_key = str(
            row["yahoo_player_key"]
        )

        slots = current_by_team.setdefault(
            team_key,
            [None, None, None, None, None],
        )

        if 1 <= level <= 5:
            slots[level - 1] = player_key

        all_qo_keys.add(
            player_key
        )

    teams: list[dict[str, object]] = []

    for row in raw_teams:
        team_key = str(
            row["team_key"]
        )

        teams.append(
            {
                "team_key":
                    team_key,
                "team_name":
                    str(row["team_name"]),
                "predraft":
                    predraft_by_team.get(
                        team_key,
                        [
                            None,
                            None,
                            None,
                            None,
                            None,
                        ],
                    ),
                "current":
                    current_by_team.get(
                        team_key,
                        [
                            None,
                            None,
                            None,
                            None,
                            None,
                        ],
                    ),
            }
        )

    players: list[dict[str, object]] = []

    for key, player in player_map.items():
        player_key = str(key)

        # Normal editing candidates are uncontracted.
        # Existing historical QO players are retained so
        # a locked/completed draft can still display them.
        if (
            player_key in contracted
            and player_key not in all_qo_keys
        ):
            continue

        rank_raw = getattr(
            player,
            "rank_value",
            None,
        )

        players.append(
            {
                "yahoo_player_key":
                    player_key,
                "name":
                    str(
                        getattr(
                            player,
                            "name",
                            None,
                        )
                        or player_key
                    ),
                "rank_value": (
                    float(rank_raw)
                    if rank_raw is not None
                    else None
                ),
                "predraft_qo_team_key":
                    qo_owner_by_player.get(
                        player_key
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
        "draft_key":
            draft_key,
        "draft_status":
            str(draft["status"]),
        "selection_count":
            selection_count,
        "qo_rounds":
            qo_rounds,
        "baseline_synced":
            baseline_synced,
        "can_edit":
            can_edit,
        "lock_reason":
            lock_reason,
        "predraft_count":
            len(predraft_rows),
        "current_count":
            len(current_rows),
        "teams":
            teams,
        "players":
            players,
    }


def save_commissioner_qos(
    *,
    team_key: str,
    player_keys: list[str],
    created_by: str,
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        _league_key,
        _season_year,
    ) = _context()

    players = _validate_player_keys(
        player_keys
    )

    state = get_commissioner_qo_state()

    if not bool(
        state["can_edit"]
    ):
        raise QOConflict(
            str(
                state["lock_reason"]
                or "Predraft QOs are locked."
            )
        )

    valid_teams = {
        str(team["team_key"])
        for team in state["teams"]
    }

    team = str(
        team_key or ""
    ).strip()

    if team not in valid_teams:
        raise QORequestError(
            "Unknown MLF team."
        )

    visible_players = {
        str(
            player[
                "yahoo_player_key"
            ]
        )
        for player in state["players"]
    }

    if not set(players).issubset(
        visible_players
    ):
        raise QORequestError(
            "Unknown or ineligible "
            "qualifying-offer player."
        )

    try:
        current_count = (
            replace_team_predraft_qos_atomic(
                dsn=dsn,
                draft_key=draft_key,
                team_key=team,
                yahoo_player_keys=players,
                note=created_by,
            )
        )
    except ValueError as exc:
        raise QORequestError(
            str(exc)
        ) from exc
    except (
        RuntimeError,
        psycopg.Error,
    ) as exc:
        raise QOConflict(
            str(exc)
        ) from exc

    return {
        "updated_team_key":
            team,
        "current_qo_count":
            int(current_count),
        "state":
            get_commissioner_qo_state(),
    }
