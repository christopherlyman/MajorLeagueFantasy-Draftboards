from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from draftboard.data.db_players import (
    load_available_players,
)
from draftboard.data.draft_runtime import (
    delete_prospect_tag_mirrored_atomic,
    replace_prospect_tag_mirrored_atomic,
)
from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


class ProspectRequestError(ValueError):
    pass


class ProspectConflict(RuntimeError):
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


def _player_pt_eligibility(
    *,
    player_key: str,
    is_qo_eligible: bool,
    contracted: set[str],
    tagged: set[str],
) -> tuple[bool, str | None]:
    if player_key in contracted:
        return (
            False,
            "active_contract",
        )

    if player_key in tagged:
        return (
            False,
            "existing_prospect_tag",
        )

    if is_qo_eligible:
        return (
            False,
            "qo_eligible",
        )

    return (
        True,
        None,
    )


def get_commissioner_prospect_state(
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
                    yahoo_player_key,
                    note
                FROM mlf.prospect_tag
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY
                    team_key,
                    yahoo_player_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            pt_rows = cur.fetchall()

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
                        yahoo_player_key
                    FROM public.prospect_tag
                    WHERE league_key = %s
                      AND season_year = %s
                ),
                mlf_rows AS (
                    SELECT
                        team_key,
                        yahoo_player_key
                    FROM mlf.prospect_tag
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

            mirror = cur.fetchone()

            cur.execute(
                """
                SELECT count(*) AS invalid_team_count
                FROM (
                    SELECT team_key
                    FROM mlf.prospect_tag
                    WHERE league_key = %s
                      AND season_year = %s
                    GROUP BY team_key
                    HAVING count(*) > 1
                ) AS bad
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            invalid_team_count = int(
                cur.fetchone()[
                    "invalid_team_count"
                ]
            )

            cur.execute(
                """
                SELECT count(*) AS pt_keeper_count
                FROM mlf.draft_keeper_assignment
                WHERE draft_key = %s
                  AND keeper_kind = 'PT'
                """,
                (draft_key,),
            )

            keeper_pt_count = int(
                cur.fetchone()[
                    "pt_keeper_count"
                ]
            )

    mirror_synced = (
        int(mirror["public_only"]) == 0
        and int(mirror["mlf_only"]) == 0
    )

    team_multiplicity_valid = (
        invalid_team_count == 0
    )

    can_edit = (
        mirror_synced
        and team_multiplicity_valid
    )

    lock_reason: str | None = None

    if not mirror_synced:
        lock_reason = (
            "Prospect Tag compatibility mirror "
            "is out of sync."
        )
    elif not team_multiplicity_valid:
        lock_reason = (
            "At least one team has multiple "
            "Prospect Tags."
        )

    by_team: dict[
        str,
        list[str],
    ] = {}

    pt_owner_by_player: dict[
        str,
        str,
    ] = {}

    pt_note_by_player: dict[
        str,
        str | None,
    ] = {}

    for row in pt_rows:
        team_key = str(
            row["team_key"]
        )

        player_key = str(
            row["yahoo_player_key"]
        )

        by_team.setdefault(
            team_key,
            [],
        ).append(
            player_key
        )

        pt_owner_by_player[
            player_key
        ] = team_key

        pt_note_by_player[
            player_key
        ] = (
            str(row["note"])
            if row["note"] is not None
            else None
        )

    tagged = set(
        pt_owner_by_player
    )

    teams = [
        {
            "team_key":
                str(row["team_key"]),
            "team_name":
                str(row["team_name"]),
            "prospect_player_keys":
                by_team.get(
                    str(row["team_key"]),
                    [],
                ),
        }
        for row in raw_teams
    ]

    players: list[
        dict[str, object]
    ] = []

    eligible_count = 0

    for key, player in player_map.items():
        player_key = str(key)

        is_qo_eligible = bool(
            getattr(
                player,
                "is_qo_eligible",
                False,
            )
        )

        eligible, reason = (
            _player_pt_eligibility(
                player_key=player_key,
                is_qo_eligible=
                    is_qo_eligible,
                contracted=contracted,
                tagged=tagged,
            )
        )

        if eligible:
            eligible_count += 1

        rank_raw = getattr(
            player,
            "rank_value",
            None,
        )

        positions_raw = (
            getattr(
                player,
                "positions",
                None,
            )
            or []
        )

        positions = [
            str(
                getattr(
                    position,
                    "value",
                    position,
                )
            )
            for position in positions_raw
        ]

        h_ab_raw = getattr(
            player,
            "h_ab",
            None,
        )

        ip_raw = getattr(
            player,
            "ip",
            None,
        )

        owned_raw = getattr(
            player,
            "percent_owned",
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
                "mlb_team":
                    str(
                        getattr(
                            player,
                            "mlb_team",
                            None,
                        )
                        or ""
                    ),
                "positions":
                    positions,
                "rank_value": (
                    float(rank_raw)
                    if rank_raw is not None
                    else None
                ),
                "h_ab": (
                    str(h_ab_raw)
                    if h_ab_raw is not None
                    else None
                ),
                "ip": (
                    float(ip_raw)
                    if ip_raw is not None
                    else None
                ),
                "percent_owned": (
                    float(owned_raw)
                    if owned_raw is not None
                    else None
                ),
                "is_qo_eligible":
                    is_qo_eligible,
                "eligible_for_pt":
                    eligible,
                "ineligibility_reason":
                    reason,
                "prospect_team_key":
                    pt_owner_by_player.get(
                        player_key
                    ),
                "prospect_note":
                    pt_note_by_player.get(
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
            int(
                draft["selection_count"]
            ),
        "mirror_synced":
            mirror_synced,
        "team_multiplicity_valid":
            team_multiplicity_valid,
        "can_edit":
            can_edit,
        "lock_reason":
            lock_reason,
        "prospect_count":
            len(pt_rows),
        "keeper_pt_count":
            keeper_pt_count,
        "eligible_count":
            eligible_count,
        "teams":
            teams,
        "players":
            players,
    }


def save_commissioner_prospect_tag(
    *,
    team_key: str,
    yahoo_player_key: str,
    created_by: str,
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        _league_key,
        _season_year,
    ) = _context()

    state = (
        get_commissioner_prospect_state()
    )

    if not bool(
        state["can_edit"]
    ):
        raise ProspectConflict(
            str(
                state["lock_reason"]
                or "Prospect Tags are locked."
            )
        )

    team = str(
        team_key or ""
    ).strip()

    player = str(
        yahoo_player_key or ""
    ).strip()

    if not team or not player:
        raise ProspectRequestError(
            "Team and player are required."
        )

    team_state = next(
        (
            row
            for row in state["teams"]
            if str(row["team_key"]) == team
        ),
        None,
    )

    if team_state is None:
        raise ProspectRequestError(
            "Unknown MLF team."
        )

    current_rows = list(
        team_state[
            "prospect_player_keys"
        ]
    )

    if len(current_rows) > 1:
        raise ProspectConflict(
            "Team has multiple Prospect Tags."
        )

    current = (
        str(current_rows[0])
        if current_rows
        else None
    )

    if current == player:
        raise ProspectRequestError(
            "Player is already this team's "
            "Prospect Tag."
        )

    player_state = next(
        (
            row
            for row in state["players"]
            if str(
                row["yahoo_player_key"]
            ) == player
        ),
        None,
    )

    if player_state is None:
        raise ProspectRequestError(
            "Player is not in the active "
            "MLF player universe."
        )

    if not bool(
        player_state[
            "eligible_for_pt"
        ]
    ):
        raise ProspectRequestError(
            "Player is not eligible for a "
            "Prospect Tag."
        )

    try:
        keeper_assignments = (
            replace_prospect_tag_mirrored_atomic(
                dsn=dsn,
                draft_key=draft_key,
                team_key=team,
                new_yahoo_player_key=player,
                expected_old_yahoo_player_key=current,
                note=created_by,
            )
        )
    except ValueError as exc:
        raise ProspectRequestError(
            str(exc)
        ) from exc
    except (
        RuntimeError,
        psycopg.Error,
    ) as exc:
        raise ProspectConflict(
            str(exc)
        ) from exc

    return {
        "action":
            (
                "replace"
                if current is not None
                else "add"
            ),
        "team_key":
            team,
        "yahoo_player_key":
            player,
        "keeper_assignments":
            int(keeper_assignments),
        "state":
            get_commissioner_prospect_state(),
    }


def remove_commissioner_prospect_tag(
    *,
    team_key: str,
) -> dict[str, object]:
    (
        dsn,
        draft_key,
        _league_key,
        _season_year,
    ) = _context()

    state = (
        get_commissioner_prospect_state()
    )

    if not bool(
        state["can_edit"]
    ):
        raise ProspectConflict(
            str(
                state["lock_reason"]
                or "Prospect Tags are locked."
            )
        )

    team = str(
        team_key or ""
    ).strip()

    team_state = next(
        (
            row
            for row in state["teams"]
            if str(row["team_key"]) == team
        ),
        None,
    )

    if team_state is None:
        raise ProspectRequestError(
            "Unknown MLF team."
        )

    current_rows = list(
        team_state[
            "prospect_player_keys"
        ]
    )

    if len(current_rows) != 1:
        raise ProspectRequestError(
            "Team does not have exactly one "
            "Prospect Tag to remove."
        )

    player = str(
        current_rows[0]
    )

    try:
        keeper_assignments = (
            delete_prospect_tag_mirrored_atomic(
                dsn=dsn,
                draft_key=draft_key,
                team_key=team,
                expected_yahoo_player_key=player,
            )
        )
    except (
        RuntimeError,
        psycopg.Error,
    ) as exc:
        raise ProspectConflict(
            str(exc)
        ) from exc

    return {
        "action":
            "remove",
        "team_key":
            team,
        "yahoo_player_key":
            player,
        "keeper_assignments":
            int(keeper_assignments),
        "state":
            get_commissioner_prospect_state(),
    }
