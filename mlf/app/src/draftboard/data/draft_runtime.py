from __future__ import annotations

import json

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row


_ALLOWED_PICK_KINDS = frozenset({"FA", "QO", "POACH"})


@dataclass(frozen=True, slots=True)
class DraftPickExecution:
    """Result returned by the canonical MLF atomic pick executor."""

    result_status: str
    executed_pick_id: str
    selecting_team_key: str
    selected_player_key: str
    selected_pick_kind: str
    next_pick_id: str | None
    selected_at_utc: datetime | None


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()

    if not text:
        raise ValueError(f"{field} is required.")

    return text


def submit_draft_pick_atomic(
    *,
    dsn: str,
    draft_key: str,
    pick_id: str,
    expected_owner_team_key: str,
    yahoo_player_key: str,
    expected_pick_kind: str | None = None,
    selected_by: str = "draftboard_manual",
) -> DraftPickExecution:
    """
    Execute one real MLF draft pick through PostgreSQL.

    PostgreSQL is authoritative for:
      - active-pick validation
      - pick ownership
      - contract/PT protection
      - QO/POACH classification
      - duplicate-player protection
      - concurrent submission protection
      - QO ladder mutation
      - next-pick advancement
      - draft-runtime state

    This function does not mutate Streamlit DraftState and does not persist
    JSON DraftState. The caller must refresh its derived projection after a
    successful execution.
    """

    dsn_text = _required_text(dsn, field="dsn")
    draft = _required_text(draft_key, field="draft_key")
    pick = _required_text(pick_id, field="pick_id")
    owner = _required_text(
        expected_owner_team_key,
        field="expected_owner_team_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )
    kind_text = str(expected_pick_kind or "").strip().upper()
    kind = kind_text or None
    actor = _required_text(
        selected_by,
        field="selected_by",
    )

    if kind is not None and kind not in _ALLOWED_PICK_KINDS:
        raise ValueError(
            "expected_pick_kind must be one of "
            f"{sorted(_ALLOWED_PICK_KINDS)}; got {kind!r}."
        )

    sql = """
        SELECT
            result_status,
            executed_pick_id,
            selecting_team_key,
            selected_player_key,
            selected_pick_kind,
            next_pick_id,
            selected_at_utc
        FROM mlf.submit_draft_pick_atomic(
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """

    with psycopg.connect(dsn_text) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql,
                (
                    draft,
                    pick,
                    owner,
                    player,
                    kind,
                    actor,
                ),
            )

            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF atomic draft executor returned no result."
        )

    selected_at = row["selected_at_utc"]

    if (
        selected_at is not None
        and not isinstance(selected_at, datetime)
    ):
        raise RuntimeError(
            "MLF atomic draft executor returned an invalid "
            "selected_at_utc value."
        )

    result = DraftPickExecution(
        result_status=str(row["result_status"] or ""),
        executed_pick_id=str(row["executed_pick_id"] or ""),
        selecting_team_key=str(row["selecting_team_key"] or ""),
        selected_player_key=str(row["selected_player_key"] or ""),
        selected_pick_kind=str(row["selected_pick_kind"] or ""),
        next_pick_id=(
            str(row["next_pick_id"])
            if row["next_pick_id"] is not None
            else None
        ),
        selected_at_utc=selected_at,
    )

    if result.result_status != "EXECUTED":
        raise RuntimeError(
            "MLF atomic draft executor returned unexpected status "
            f"{result.result_status!r}."
        )

    if result.executed_pick_id != pick:
        raise RuntimeError(
            "MLF atomic draft executor returned a different pick: "
            f"expected {pick!r}, got {result.executed_pick_id!r}."
        )

    if result.selecting_team_key != owner:
        raise RuntimeError(
            "MLF atomic draft executor returned a different owner: "
            f"expected {owner!r}, got {result.selecting_team_key!r}."
        )

    if result.selected_player_key != player:
        raise RuntimeError(
            "MLF atomic draft executor returned a different player: "
            f"expected {player!r}, got {result.selected_player_key!r}."
        )

    if (
        kind is not None
        and result.selected_pick_kind != kind
    ):
        raise RuntimeError(
            "MLF atomic draft executor classification disagreed with "
            f"the caller: expected {kind!r}, "
            f"got {result.selected_pick_kind!r}."
        )

    return result

def reset_draft_atomic(
    *,
    dsn: str,
    draft_key: str,
) -> str:
    draft = _required_text(draft_key, field="draft_key")

    with psycopg.connect(_required_text(dsn, field="dsn")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT mlf.reset_draft_atomic(%s)",
                (draft,),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError("MLF draft reset returned no row.")

    return str(row[0] or "")


def delete_draft_selection_atomic(
    *,
    dsn: str,
    draft_key: str,
    pick_id: str,
    rewind_clock: bool,
) -> str:
    draft = _required_text(draft_key, field="draft_key")
    pick = _required_text(pick_id, field="pick_id")

    with psycopg.connect(_required_text(dsn, field="dsn")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.delete_draft_selection_atomic(
                    %s,
                    %s,
                    %s
                )
                """,
                (draft, pick, bool(rewind_clock)),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError("MLF delete-pick operation returned no row.")

    return str(row[0] or "")


def set_current_pick_atomic(
    *,
    dsn: str,
    draft_key: str,
    pick_id: str,
) -> str:
    draft = _required_text(draft_key, field="draft_key")
    pick = _required_text(pick_id, field="pick_id")

    with psycopg.connect(_required_text(dsn, field="dsn")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT mlf.set_current_pick_atomic(%s, %s)",
                (draft, pick),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError("MLF set-current-pick returned no row.")

    return str(row[0] or "")


def update_draft_clock_atomic(
    *,
    dsn: str,
    draft_key: str,
    action: str,
    seconds_per_pick: int | None = None,
    weekends_count: bool | None = None,
) -> str:
    draft = _required_text(draft_key, field="draft_key")
    action_text = _required_text(action, field="action").upper()

    allowed = {
        "START",
        "PAUSE",
        "RESUME",
        "STOP",
        "SET_DURATION",
        "SET_WEEKENDS",
    }

    if action_text not in allowed:
        raise ValueError(
            f"Unsupported clock action {action_text!r}."
        )

    with psycopg.connect(_required_text(dsn, field="dsn")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.update_draft_clock_atomic(
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    action_text,
                    seconds_per_pick,
                    weekends_count,
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError("MLF clock update returned no row.")

    return str(row[0] or "")


def transfer_draft_pick_atomic(
    *,
    dsn: str,
    draft_key: str,
    pick_id: str,
    to_team_key: str,
    note: str | None = None,
) -> str:
    draft = _required_text(draft_key, field="draft_key")
    pick = _required_text(pick_id, field="pick_id")
    team = _required_text(to_team_key, field="to_team_key")

    with psycopg.connect(_required_text(dsn, field="dsn")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.transfer_draft_pick_atomic(
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (draft, pick, team, note),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError("MLF pick transfer returned no row.")

    return str(row[0] or "")




def rebuild_draft_keeper_assignments(
    *,
    dsn: str,
    draft_key: str,
) -> int:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.rebuild_draft_keeper_assignments(
                    %s
                )
                """,
                (draft,),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF keeper rebuild returned no row."
        )

    return int(row[0])


def rebase_draft_order_atomic(
    *,
    dsn: str,
    draft_key: str,
    team_keys: list[str],
) -> int:
    draft = _required_text(draft_key, field="draft_key")
    teams = [
        _required_text(team_key, field="team_key")
        for team_key in team_keys
    ]

    with psycopg.connect(_required_text(dsn, field="dsn")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.rebase_draft_order_atomic(
                    %s,
                    %s::text[]
                )
                """,
                (draft, teams),
            )
            row = cur.fetchone()

            if row is None:
                raise RuntimeError(
                    "MLF draft-order rebase returned no row."
                )

            changed = int(row[0])

            cur.execute(
                """
                SELECT mlf.rebuild_draft_keeper_assignments(
                    %s
                )
                """,
                (draft,),
            )
            keeper_row = cur.fetchone()

            if keeper_row is None:
                raise RuntimeError(
                    "MLF keeper rebuild after draft-order "
                    "rebase returned no row."
                )

    return changed

def upsert_prospect_tag_atomic(
    *,
    dsn: str,
    draft_key: str,
    team_key: str,
    yahoo_player_key: str,
    note: str | None = None,
) -> int:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )
    team = _required_text(
        team_key,
        field="team_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.upsert_prospect_tag_atomic(
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    team,
                    player,
                    note,
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF Prospect Tag upsert returned no row."
        )

    return int(row[0])


def delete_prospect_tag_atomic(
    *,
    dsn: str,
    draft_key: str,
    yahoo_player_key: str,
) -> int:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.delete_prospect_tag_atomic(
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    player,
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF Prospect Tag deletion returned no row."
        )

    return int(row[0])


def transfer_active_contract_atomic(
    *,
    dsn: str,
    draft_key: str,
    yahoo_player_key: str,
    to_team_key: str,
    note: str | None = None,
) -> str:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )
    team = _required_text(
        to_team_key,
        field="to_team_key",
    )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.transfer_active_contract_atomic(
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    player,
                    team,
                    note,
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF active-contract transfer returned no row."
        )

    return str(row[0])

def upsert_contract_override_atomic(
    *,
    dsn: str,
    draft_key: str,
    yahoo_player_key: str,
    team_key: str | None,
    years_remaining: int,
    note: str | None = None,
) -> int:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )

    years = int(years_remaining)

    if years < 0:
        raise ValueError(
            "years_remaining must be zero or greater."
        )

    team_text = str(team_key or "").strip()
    team = team_text or None

    if years > 0 and team is None:
        raise ValueError(
            "Active contract override requires team_key."
        )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.upsert_contract_override_atomic(
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    player,
                    team,
                    years,
                    note,
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF contract-override upsert returned no row."
        )

    return int(row[0])


def delete_contract_override_atomic(
    *,
    dsn: str,
    draft_key: str,
    yahoo_player_key: str,
) -> int:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )
    player = _required_text(
        yahoo_player_key,
        field="yahoo_player_key",
    )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.delete_contract_override_atomic(
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    player,
                ),
            )
            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF contract-override deletion returned no row."
        )

    return int(row[0])



def replace_prospect_tag_atomic(
    *,
    dsn: str,
    draft_key: str,
    old_yahoo_player_key: str | None,
    team_key: str,
    new_yahoo_player_key: str,
    note: str | None = None,
) -> int:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )
    team = _required_text(
        team_key,
        field="team_key",
    )
    new_player = _required_text(
        new_yahoo_player_key,
        field="new_yahoo_player_key",
    )

    old_text = str(
        old_yahoo_player_key or ""
    ).strip()

    old_player = old_text or None

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mlf.replace_prospect_tag_atomic(
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    draft,
                    old_player,
                    team,
                    new_player,
                    note,
                ),
            )

            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF Prospect Tag replacement returned no row."
        )

    return int(row[0])


def apply_trade_assets_atomic(
    *,
    dsn: str,
    draft_key: str,
    assets: list[dict],
    note: str | None = None,
) -> tuple[int, int, int]:
    draft = _required_text(
        draft_key,
        field="draft_key",
    )

    if not isinstance(assets, list):
        raise TypeError(
            "assets must be a list."
        )

    payload = json.dumps(
        assets,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    with psycopg.connect(
        _required_text(dsn, field="dsn")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    player_updates,
                    pick_updates,
                    keeper_assignments
                FROM mlf.apply_trade_assets_atomic(
                    %s,
                    %s::jsonb,
                    %s
                )
                """,
                (
                    draft,
                    payload,
                    note,
                ),
            )

            row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MLF atomic trade returned no row."
        )

    return (
        int(row[0]),
        int(row[1]),
        int(row[2]),
    )
# MLF_PREDRAFT_QO_REPLACE_ATOMIC_V1

def replace_team_predraft_qos_atomic(
    dsn: str,
    draft_key: str,
    team_key: str,
    yahoo_player_keys: list[str],
    note: str | None = None,
) -> int:
    """
    Replace one team's five predraft qualifying offers.

    mlf.qualifying_offer is the relational draft-runtime source.
    public.qualifying_offer is maintained transactionally as a
    compatibility mirror for remaining legacy readers.

    Predraft QOs are immutable after any real draft selection exists.
    """
    import psycopg

    draft = str(
        draft_key or ""
    ).strip()

    team = str(
        team_key or ""
    ).strip()

    players = [
        str(value or "").strip()
        for value in (
            yahoo_player_keys or []
        )
    ]

    if not draft:
        raise ValueError(
            "Missing draft key."
        )

    if not team:
        raise ValueError(
            "Missing team key."
        )

    if len(players) != 5:
        raise ValueError(
            "Exactly five qualifying offers are required."
        )

    if any(not player for player in players):
        raise ValueError(
            "All five qualifying offers are required."
        )

    if len(set(players)) != 5:
        raise ValueError(
            "Qualifying-offer players must be unique."
        )

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(%s, 0)
                )
                """,
                (draft,),
            )

            cur.execute(
                """
                SELECT
                    league_key,
                    season_year,
                    qo_rounds
                FROM mlf.draft
                WHERE draft_key = %s
                FOR UPDATE
                """,
                (draft,),
            )

            draft_row = cur.fetchone()

            if draft_row is None:
                raise RuntimeError(
                    f"Draft {draft!r} was not found."
                )

            league_key = str(
                draft_row[0]
            )

            season_year = int(
                draft_row[1]
            )

            qo_rounds = int(
                draft_row[2]
            )

            if qo_rounds != 5:
                raise RuntimeError(
                    "Active draft does not have "
                    "five QO rounds."
                )

            cur.execute(
                """
                SELECT count(*)
                FROM mlf.draft_selection
                WHERE draft_key = %s
                """,
                (draft,),
            )

            selection_count = int(
                cur.fetchone()[0]
            )

            if selection_count != 0:
                raise RuntimeError(
                    "Predraft qualifying offers "
                    "cannot be changed after "
                    "draft selections exist."
                )

            cur.execute(
                """
                SELECT 1
                FROM mlf.team
                WHERE league_key = %s
                  AND season_year = %s
                  AND team_key = %s
                """,
                (
                    league_key,
                    season_year,
                    team,
                ),
            )

            if cur.fetchone() is None:
                raise ValueError(
                    "Team is not in the active "
                    "MLF season."
                )

            cur.execute(
                """
                SELECT count(*)
                FROM mlf.player_universe
                WHERE league_key = %s
                  AND season_year = %s
                  AND is_active = true
                  AND yahoo_player_key = ANY(%s)
                """,
                (
                    league_key,
                    season_year,
                    players,
                ),
            )

            if int(cur.fetchone()[0]) != 5:
                raise ValueError(
                    "Every qualifying-offer player "
                    "must exist in the active "
                    "MLF player universe."
                )

            cur.execute(
                """
                SELECT count(*)
                FROM mlf.v_active_contract
                WHERE league_key = %s
                  AND season_year = %s
                  AND yahoo_player_key = ANY(%s)
                """,
                (
                    league_key,
                    season_year,
                    players,
                ),
            )

            if int(cur.fetchone()[0]) != 0:
                raise ValueError(
                    "Contracted players cannot be "
                    "assigned predraft qualifying offers."
                )

            cur.execute(
                """
                SELECT count(*)
                FROM mlf.qualifying_offer
                WHERE league_key = %s
                  AND season_year = %s
                  AND yahoo_player_key = ANY(%s)
                  AND team_key <> %s
                """,
                (
                    league_key,
                    season_year,
                    players,
                    team,
                ),
            )

            if int(cur.fetchone()[0]) != 0:
                raise ValueError(
                    "A selected player is already "
                    "a qualifying offer for another team."
                )

            # Fail closed if the legacy mirror has already
            # diverged from relational truth.
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
                    ),
                    (
                        SELECT count(*)
                        FROM (
                            SELECT *
                            FROM mlf_rows

                            EXCEPT

                            SELECT *
                            FROM public_rows
                        ) AS mlf_only
                    )
                """,
                (
                    league_key,
                    season_year,
                    league_key,
                    season_year,
                ),
            )

            public_only, mlf_only = (
                cur.fetchone()
            )

            if (
                int(public_only) != 0
                or int(mlf_only) != 0
            ):
                raise RuntimeError(
                    "QO compatibility mirror is "
                    "out of sync with relational truth."
                )

            cur.execute(
                """
                DELETE FROM mlf.qualifying_offer
                WHERE league_key = %s
                  AND season_year = %s
                  AND team_key = %s
                """,
                (
                    league_key,
                    season_year,
                    team,
                ),
            )

            cur.execute(
                """
                DELETE FROM public.qualifying_offer
                WHERE league_key = %s
                  AND season_year = %s
                  AND team_key = %s
                """,
                (
                    league_key,
                    season_year,
                    team,
                ),
            )

            audit_note = (
                str(note).strip()
                if note is not None
                else None
            )

            for level, player_key in enumerate(
                players,
                start=1,
            ):
                cur.execute(
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
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        now()
                    )
                    """,
                    (
                        league_key,
                        season_year,
                        team,
                        player_key,
                        level,
                        audit_note,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.qualifying_offer (
                        league_key,
                        season_year,
                        team_key,
                        yahoo_player_key,
                        qo_level,
                        note,
                        updated_at,
                        team_key_yahoo
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        now(),
                        %s
                    )
                    """,
                    (
                        league_key,
                        season_year,
                        team,
                        player_key,
                        level,
                        audit_note,
                        team,
                    ),
                )

            cur.execute(
                """
                SELECT mlf.rebuild_draft_qo_current(%s)
                """,
                (draft,),
            )

            current_qo_count = int(
                cur.fetchone()[0]
            )

            # Verify the compatibility mirror before commit.
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
                      AND team_key = %s
                ),
                mlf_rows AS (
                    SELECT
                        team_key,
                        qo_level,
                        yahoo_player_key
                    FROM mlf.qualifying_offer
                    WHERE league_key = %s
                      AND season_year = %s
                      AND team_key = %s
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
                    ),
                    (
                        SELECT count(*)
                        FROM (
                            SELECT *
                            FROM mlf_rows

                            EXCEPT

                            SELECT *
                            FROM public_rows
                        ) AS mlf_only
                    )
                """,
                (
                    league_key,
                    season_year,
                    team,
                    league_key,
                    season_year,
                    team,
                ),
            )

            public_only, mlf_only = (
                cur.fetchone()
            )

            if (
                int(public_only) != 0
                or int(mlf_only) != 0
            ):
                raise RuntimeError(
                    "QO compatibility mirror "
                    "verification failed."
                )

        conn.commit()

    return current_qo_count
