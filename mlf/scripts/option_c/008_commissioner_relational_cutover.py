from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys


ROOT = Path(sys.argv[1])

MIGRATION = (
    ROOT
    / "mlf/sql/option_c/008_mlf_atomic_trade_runtime.sql"
)

RUNTIME = (
    ROOT
    / "mlf/app/src/draftboard/data/draft_runtime.py"
)

COMMISSIONER = (
    ROOT
    / "mlf/app/src/draftboard/ui/components/commissioner_tools.py"
)


def read(path: Path) -> str:
    return (
        path.read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "")
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        text.rstrip("\n") + "\n",
        encoding="utf-8",
        newline="\n",
    )


def replace_once(
    text: str,
    old: str,
    new: str,
    label: str,
) -> str:
    count = text.count(old)

    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one match; found {count}."
        )

    return text.replace(old, new, 1)


def regex_once(
    text: str,
    pattern: str,
    replacement: str,
    label: str,
) -> str:
    updated, count = re.subn(
        pattern,
        replacement,
        text,
        count=1,
        flags=re.DOTALL,
    )

    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one match; found {count}."
        )

    return updated


# ================================================================
# Migration 008
# ================================================================

if MIGRATION.exists():
    raise RuntimeError(
        "Migration 008 already exists."
    )


migration = r'''
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


-- ============================================================
-- Replace one Prospect Tag with another atomically.
--
-- This exists because the commissioner UI may replace a team's
-- current PT. Delete-old + insert-new must not be split across
-- transactions.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.replace_prospect_tag_atomic(
    p_draft_key text,
    p_old_yahoo_player_key text,
    p_team_key text,
    p_new_yahoo_player_key text,
    p_note text DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;
    v_old_player text;
    v_new_player text;
    v_team_key text;
    v_note text;
    v_assignment_count integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_team_key), '') IS NULL
       OR NULLIF(BTRIM(p_new_yahoo_player_key), '') IS NULL
    THEN
        RAISE EXCEPTION
            'Missing draft/team/new-player input.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT
        d.league_key,
        d.season_year
    INTO
        v_league_key,
        v_season_year
    FROM mlf.draft d
    WHERE d.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft % not found.',
            p_draft_key;
    END IF;

    v_old_player =
        NULLIF(
            BTRIM(
                COALESCE(
                    p_old_yahoo_player_key,
                    ''
                )
            ),
            ''
        );

    v_new_player =
        BTRIM(p_new_yahoo_player_key);

    v_team_key =
        BTRIM(p_team_key);

    v_note =
        NULLIF(
            BTRIM(
                COALESCE(
                    p_note,
                    ''
                )
            ),
            ''
        );

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.team t
        WHERE t.league_key = v_league_key
          AND t.season_year = v_season_year
          AND t.team_key = v_team_key
    ) THEN
        RAISE EXCEPTION
            'Team % is not in draft %.',
            v_team_key,
            p_draft_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.player_universe pu
        WHERE pu.league_key = v_league_key
          AND pu.season_year = v_season_year
          AND pu.yahoo_player_key = v_new_player
          AND pu.is_active = true
    ) THEN
        RAISE EXCEPTION
            'Active player % is not in the MLF player universe.',
            v_new_player;
    END IF;

    IF v_old_player IS NOT NULL
       AND v_old_player <> v_new_player
    THEN
        DELETE FROM mlf.prospect_tag
        WHERE league_key = v_league_key
          AND season_year = v_season_year
          AND yahoo_player_key = v_old_player;
    END IF;

    INSERT INTO mlf.prospect_tag AS existing (
        league_key,
        season_year,
        team_key,
        yahoo_player_key,
        note,
        updated_at_utc
    )
    VALUES (
        v_league_key,
        v_season_year,
        v_team_key,
        v_new_player,
        v_note,
        now()
    )
    ON CONFLICT (
        league_key,
        season_year,
        yahoo_player_key
    )
    DO UPDATE
       SET team_key = EXCLUDED.team_key,
           note = COALESCE(
               EXCLUDED.note,
               existing.note
           ),
           updated_at_utc = now();

    v_assignment_count =
        mlf.rebuild_draft_keeper_assignments(
            p_draft_key
        );

    RETURN v_assignment_count;
END;
$function$;


-- ============================================================
-- Apply all canonical player/pick effects of one commissioner
-- trade in ONE transaction and rebuild keepers only after the
-- full final ownership state exists.
--
-- JSON asset shape:
--
-- {
--   "asset_type": "PLAYER" | "PICK",
--   "asset_id": "...",
--   "from_team_key": "...",
--   "to_team_key": "...",
--   "snapshot": {
--       "contract_years": <integer>
--   }
-- }
--
-- PLAYER assets with contract_years <= 0 are receipt-only,
-- preserving the existing MLF Trade Builder behavior.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.apply_trade_assets_atomic(
    p_draft_key text,
    p_assets jsonb,
    p_note text DEFAULT NULL
)
RETURNS TABLE (
    player_updates integer,
    pick_updates integer,
    keeper_assignments integer
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;

    v_asset jsonb;
    v_asset_type text;
    v_asset_id text;
    v_from_team_key text;
    v_to_team_key text;
    v_contract_years integer;

    v_pick_owner text;
    v_pick_column_team text;

    v_override_team text;
    v_override_years integer;
    v_contract_team text;
    v_contract_years_actual integer;
    v_override_found boolean;
    v_contract_found boolean;

    v_note text;

    v_player_updates integer := 0;
    v_pick_updates integer := 0;
    v_keeper_assignments integer := 0;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION
            'Missing draft key.';
    END IF;

    IF p_assets IS NULL
       OR pg_catalog.jsonb_typeof(p_assets) <> 'array'
    THEN
        RAISE EXCEPTION
            'Trade assets must be a JSON array.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT
                UPPER(
                    BTRIM(
                        COALESCE(
                            asset.value ->> 'asset_type',
                            ''
                        )
                    )
                ) AS asset_type,
                BTRIM(
                    COALESCE(
                        asset.value ->> 'asset_id',
                        ''
                    )
                ) AS asset_id,
                count(*) AS n
            FROM pg_catalog.jsonb_array_elements(
                p_assets
            ) AS asset(value)
            GROUP BY
                UPPER(
                    BTRIM(
                        COALESCE(
                            asset.value ->> 'asset_type',
                            ''
                        )
                    )
                ),
                BTRIM(
                    COALESCE(
                        asset.value ->> 'asset_id',
                        ''
                    )
                )
            HAVING count(*) > 1
        ) duplicates
    ) THEN
        RAISE EXCEPTION
            'Trade contains a duplicate asset.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT
        d.league_key,
        d.season_year
    INTO
        v_league_key,
        v_season_year
    FROM mlf.draft d
    WHERE d.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft % not found.',
            p_draft_key;
    END IF;

    v_note =
        NULLIF(
            BTRIM(
                COALESCE(
                    p_note,
                    ''
                )
            ),
            ''
        );

    FOR v_asset IN
        SELECT asset.value
        FROM pg_catalog.jsonb_array_elements(
            p_assets
        ) AS asset(value)
    LOOP
        v_asset_type =
            UPPER(
                BTRIM(
                    COALESCE(
                        v_asset ->> 'asset_type',
                        ''
                    )
                )
            );

        v_asset_id =
            BTRIM(
                COALESCE(
                    v_asset ->> 'asset_id',
                    ''
                )
            );

        v_from_team_key =
            BTRIM(
                COALESCE(
                    v_asset ->> 'from_team_key',
                    ''
                )
            );

        v_to_team_key =
            BTRIM(
                COALESCE(
                    v_asset ->> 'to_team_key',
                    ''
                )
            );

        IF v_asset_type NOT IN (
            'PLAYER',
            'PICK'
        ) THEN
            RAISE EXCEPTION
                'Unsupported trade asset type %.',
                v_asset_type;
        END IF;

        IF v_asset_id = ''
           OR v_from_team_key = ''
           OR v_to_team_key = ''
        THEN
            RAISE EXCEPTION
                'Trade asset is missing identity/team fields.';
        END IF;

        IF v_from_team_key = v_to_team_key THEN
            RAISE EXCEPTION
                'Trade asset % has identical source and destination teams.',
                v_asset_id;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM mlf.team t
            WHERE t.league_key = v_league_key
              AND t.season_year = v_season_year
              AND t.team_key = v_from_team_key
        ) THEN
            RAISE EXCEPTION
                'Trade source team % is not in draft %.',
                v_from_team_key,
                p_draft_key;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM mlf.team t
            WHERE t.league_key = v_league_key
              AND t.season_year = v_season_year
              AND t.team_key = v_to_team_key
        ) THEN
            RAISE EXCEPTION
                'Trade destination team % is not in draft %.',
                v_to_team_key,
                p_draft_key;
        END IF;

        IF v_asset_type = 'PLAYER' THEN
            v_contract_years =
                COALESCE(
                    NULLIF(
                        BTRIM(
                            COALESCE(
                                v_asset #>> '{snapshot,contract_years}',
                                ''
                            )
                        ),
                        ''
                    )::integer,
                    0
                );

            -- Non-contract player trade:
            -- receipt/history only; no draft-control mutation.
            IF v_contract_years <= 0 THEN
                CONTINUE;
            END IF;

            v_override_team = NULL;
            v_override_years = NULL;
            v_override_found = false;

            SELECT
                co.team_key,
                co.years_remaining
            INTO
                v_override_team,
                v_override_years
            FROM mlf.contract_override co
            WHERE co.league_key = v_league_key
              AND co.season_year = v_season_year
              AND co.yahoo_player_key = v_asset_id
            FOR UPDATE;

            v_override_found = FOUND;

            IF v_override_found THEN
                IF v_override_years <= 0 THEN
                    RAISE EXCEPTION
                        'Player % has no active effective contract.',
                        v_asset_id;
                END IF;

                IF v_override_team IS DISTINCT FROM v_from_team_key THEN
                    RAISE EXCEPTION
                        'Player % effective contract owner changed: expected %, found %.',
                        v_asset_id,
                        v_from_team_key,
                        v_override_team;
                END IF;

                UPDATE mlf.contract_override
                   SET team_key = v_to_team_key,
                       note = COALESCE(
                           v_note,
                           note
                       ),
                       updated_at_utc = now()
                 WHERE league_key = v_league_key
                   AND season_year = v_season_year
                   AND yahoo_player_key = v_asset_id;

            ELSE
                v_contract_team = NULL;
                v_contract_years_actual = NULL;
                v_contract_found = false;

                SELECT
                    c.team_key,
                    c.years_remaining
                INTO
                    v_contract_team,
                    v_contract_years_actual
                FROM mlf.contract c
                WHERE c.league_key = v_league_key
                  AND c.season_year = v_season_year
                  AND c.yahoo_player_key = v_asset_id
                FOR UPDATE;

                v_contract_found = FOUND;

                IF NOT v_contract_found
                   OR v_contract_years_actual <= 0
                THEN
                    RAISE EXCEPTION
                        'Player % has no active effective contract.',
                        v_asset_id;
                END IF;

                IF v_contract_team IS DISTINCT FROM v_from_team_key THEN
                    RAISE EXCEPTION
                        'Player % contract owner changed: expected %, found %.',
                        v_asset_id,
                        v_from_team_key,
                        v_contract_team;
                END IF;

                UPDATE mlf.contract
                   SET team_key = v_to_team_key,
                       note = COALESCE(
                           v_note,
                           note
                       ),
                       updated_at_utc = now()
                 WHERE league_key = v_league_key
                   AND season_year = v_season_year
                   AND yahoo_player_key = v_asset_id;
            END IF;

            v_player_updates =
                v_player_updates + 1;

            CONTINUE;
        END IF;

        -- PICK asset.
        v_pick_owner = NULL;
        v_pick_column_team = NULL;

        SELECT
            dp.current_owner_team_key,
            dp.column_team_key
        INTO
            v_pick_owner,
            v_pick_column_team
        FROM mlf.draft_pick dp
        WHERE dp.draft_key = p_draft_key
          AND dp.pick_id = v_asset_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Draft pick % not found.',
                v_asset_id;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM mlf.draft_selection ds
            WHERE ds.draft_key = p_draft_key
              AND ds.pick_id = v_asset_id
        ) THEN
            RAISE EXCEPTION
                'Draft pick % already contains a real selection.',
                v_asset_id;
        END IF;

        IF v_pick_owner IS DISTINCT FROM v_from_team_key THEN
            RAISE EXCEPTION
                'Pick % owner changed: expected %, found %.',
                v_asset_id,
                v_from_team_key,
                v_pick_owner;
        END IF;

        UPDATE mlf.draft_pick
           SET current_owner_team_key = v_to_team_key,
               traded_flag = (
                   v_to_team_key <> v_pick_column_team
               ),
               ownership_note = COALESCE(
                   v_note,
                   ownership_note
               ),
               updated_at_utc = now()
         WHERE draft_key = p_draft_key
           AND pick_id = v_asset_id;

        INSERT INTO mlf.draft_pick_trade (
            draft_key,
            pick_id,
            from_team_key,
            to_team_key,
            trade_date,
            note
        )
        VALUES (
            p_draft_key,
            v_asset_id,
            v_from_team_key,
            v_to_team_key,
            CURRENT_DATE,
            v_note
        );

        v_pick_updates =
            v_pick_updates + 1;
    END LOOP;

    -- Reconstruct keepers once, against the FINAL ownership state.
    v_keeper_assignments =
        mlf.rebuild_draft_keeper_assignments(
            p_draft_key
        );

    RETURN QUERY
    SELECT
        v_player_updates,
        v_pick_updates,
        v_keeper_assignments;
END;
$function$;


REVOKE ALL
ON FUNCTION mlf.replace_prospect_tag_atomic(
    text,
    text,
    text,
    text,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.apply_trade_assets_atomic(
    text,
    jsonb,
    text
)
FROM PUBLIC;


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '008',
    'Atomic MLF multi-asset trade and Prospect Tag replacement operations'
);

COMMIT;
'''

write(
    MIGRATION,
    migration,
)


# ================================================================
# Python relational boundary
# ================================================================

runtime = read(RUNTIME)

if "import json\n" not in runtime:
    runtime = replace_once(
        runtime,
        "from __future__ import annotations\n",
        "from __future__ import annotations\n\nimport json\n",
        "runtime json import",
    )

if "def replace_prospect_tag_atomic(" in runtime:
    raise RuntimeError(
        "replace_prospect_tag_atomic already exists."
    )

if "def apply_trade_assets_atomic(" in runtime:
    raise RuntimeError(
        "apply_trade_assets_atomic already exists."
    )

runtime += r'''


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
'''

write(
    RUNTIME,
    runtime,
)


# ================================================================
# Commissioner UI cutover
# ================================================================

ui = read(COMMISSIONER)


# ----------------------------------------------------------------
# Imports
# ----------------------------------------------------------------

ui = replace_once(
    ui,
    '''from draftboard.domain.clock import compute_clock_status, start_pick_clock
from draftboard.state.autosave import save_autosave
from draftboard.state.store import DraftState, set_current_pick
''',
    '''from draftboard.data.draft_projection import load_draft_state_projection
from draftboard.data.draft_runtime import (
    apply_trade_assets_atomic,
    delete_contract_override_atomic,
    delete_draft_selection_atomic,
    delete_prospect_tag_atomic,
    rebase_draft_order_atomic,
    replace_prospect_tag_atomic,
    reset_draft_atomic,
    set_current_pick_atomic,
    transfer_active_contract_atomic,
    transfer_draft_pick_atomic,
    update_draft_clock_atomic,
    upsert_contract_override_atomic,
    upsert_prospect_tag_atomic,
)
from draftboard.domain.clock import compute_clock_status
from draftboard.state.store import DraftState, replace_state
''',
    "commissioner relational imports",
)


# ----------------------------------------------------------------
# Shared projection/cache helpers.
# ----------------------------------------------------------------

anchor = '''def _get_season_year() -> int:
    return get_season_year()
'''

helpers = r'''


def _refresh_relational_state(
    state: DraftState,
) -> DraftState:
    """
    Reload canonical MLF draft facts after a relational mutation.

    The caller's DraftState object is refreshed in place so code already
    holding this reference cannot continue rendering stale draft facts.
    """
    refreshed = load_draft_state_projection(
        dsn=_get_dsn(),
        draft_key=_get_draft_key(),
        base_state=state,
    )

    for field_name in DraftState.__dataclass_fields__:
        setattr(
            state,
            field_name,
            getattr(
                refreshed,
                field_name,
            ),
        )

    replace_state(state)

    return state


def _refresh_option_c_runtime_caches(
    state: DraftState,
) -> None:
    """
    Refresh transitional Streamlit contract caches from mlf.* truth.

    These caches remain UI conveniences only. They are not persistence.
    """
    league_key = _get_league_key()
    season_year = _get_season_year()

    rows: list[dict] = []

    with psycopg.connect(_get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    yahoo_player_key,
                    team_key,
                    years_remaining
                FROM mlf.v_active_contract
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY yahoo_player_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            for (
                player_key,
                team_key,
                years_remaining,
            ) in cur.fetchall():
                player_key = str(player_key)
                team_key = str(team_key or "")

                team = state.teams.get(
                    team_key
                )

                rows.append(
                    {
                        "yahoo_player_key": player_key,
                        "years_remaining": int(
                            years_remaining
                        ),
                        "yahoo_team_key": team_key,
                        "yahoo_team_name": (
                            team.name
                            if team is not None
                            else team_key
                        ),
                    }
                )

    contract_years_map = {
        str(row["yahoo_player_key"]):
            int(row["years_remaining"])
        for row in rows
    }

    pt_map = dict(
        getattr(
            state,
            "pt_player_team_map",
            {},
        )
        or {}
    )

    contracted_keys = (
        set(contract_years_map)
        | set(pt_map)
    )

    st.session_state["contract_rows"] = rows
    st.session_state["contract_years_map"] = (
        contract_years_map
    )
    st.session_state["contracted_keys"] = (
        contracted_keys
    )
    st.session_state["pt_player_team_map"] = (
        pt_map
    )

    # Retain old aliases during this transition only.
    st.session_state["contract_rows_2026"] = rows
    st.session_state["contracted_keys_2026"] = (
        contracted_keys
    )


def _load_relational_contract_overrides(
    *,
    state: DraftState,
    dsn: str,
    league_key: str,
    season_year: int,
) -> list[dict]:
    rows: list[dict] = []

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    yahoo_player_key,
                    team_key,
                    years_remaining,
                    note,
                    updated_at_utc
                FROM mlf.contract_override
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY
                    updated_at_utc DESC,
                    yahoo_player_key
                """,
                (
                    league_key,
                    season_year,
                ),
            )

            for (
                player_key,
                team_key,
                years_remaining,
                note,
                updated_at,
            ) in cur.fetchall():
                team_key_text = str(
                    team_key or ""
                )

                team = state.teams.get(
                    team_key_text
                )

                rows.append(
                    {
                        "yahoo_player_key": str(
                            player_key
                        ),
                        "years_remaining": int(
                            years_remaining
                        ),
                        "yahoo_team_key":
                            team_key_text,
                        "yahoo_team_name": (
                            team.name
                            if team is not None
                            else team_key_text
                        ),
                        "note": str(
                            note or ""
                        ),
                        "updated_at": (
                            updated_at.isoformat()
                            if updated_at is not None
                            else ""
                        ),
                    }
                )

    return rows
'''

ui = replace_once(
    ui,
    anchor,
    anchor + helpers,
    "commissioner shared relational helpers",
)


# ----------------------------------------------------------------
# Reset/delete/clock helpers.
# ----------------------------------------------------------------

ui = regex_once(
    ui,
    r'''def reset_draft_state\(state: DraftState\) -> None:
.*?
(?=def render_commissioner_actions)''',
    r'''def reset_draft_state(
    state: DraftState,
) -> None:
    reset_draft_atomic(
        dsn=_get_dsn(),
        draft_key=_get_draft_key(),
    )

    _refresh_relational_state(state)


def delete_pick(
    state: DraftState,
    pick_id: str,
    rewind_clock: bool,
) -> None:
    if pick_id not in state.picks:
        return

    delete_draft_selection_atomic(
        dsn=_get_dsn(),
        draft_key=_get_draft_key(),
        pick_id=pick_id,
        rewind_clock=bool(
            rewind_clock
        ),
    )

    _refresh_relational_state(state)


def _pause_clock(
    state: DraftState,
) -> None:
    if state.clock.pick_started_ts_iso is None:
        return

    if state.clock.pick_paused_ts_iso is not None:
        return

    update_draft_clock_atomic(
        dsn=_get_dsn(),
        draft_key=_get_draft_key(),
        action="PAUSE",
    )

    _refresh_relational_state(state)


def _resume_clock(
    state: DraftState,
) -> None:
    if state.clock.pick_started_ts_iso is None:
        return

    if state.clock.pick_paused_ts_iso is None:
        return

    update_draft_clock_atomic(
        dsn=_get_dsn(),
        draft_key=_get_draft_key(),
        action="RESUME",
    )

    _refresh_relational_state(state)


def _start_clock_if_needed(
    state: DraftState,
) -> None:
    action = (
        "RESUME"
        if state.clock.pick_paused_ts_iso is not None
        else "START"
    )

    update_draft_clock_atomic(
        dsn=_get_dsn(),
        draft_key=_get_draft_key(),
        action=action,
    )

    _refresh_relational_state(state)


''',
    "reset/delete/clock helper cutover",
)


# ----------------------------------------------------------------
# Draft-order save.
# ----------------------------------------------------------------

ui = regex_once(
    ui,
    r'''                # Save to state \(canonical slot order\)
.*?
                save_autosave\(state\)
''',
    r'''                rebase_draft_order_atomic(
                    dsn=_get_dsn(),
                    draft_key=_get_draft_key(),
                    team_keys=order,
                )

                _refresh_relational_state(state)
                _refresh_option_c_runtime_caches(state)
''',
    "draft-order relational cutover",
)


# ----------------------------------------------------------------
# Player-universe refresh no longer writes autosave.
# ----------------------------------------------------------------

ui = replace_once(
    ui,
    '''                    save_autosave(state)

                # Show a short success toast before rerun (the receipt persists anyway)
''',
    '''                    _refresh_option_c_runtime_caches(state)

                # Show a short success toast before rerun (the receipt persists anyway)
''',
    "player refresh autosave removal",
)


# ----------------------------------------------------------------
# Trade Builder.
# Canonical trade is applied first and atomically.
# Legacy public.* receipt remains audit-only.
# ----------------------------------------------------------------

ui = regex_once(
    ui,
    r'''                trade_id = _insert_trade\(
.*?
                st\.success\(
                    f"Trade saved to DB\. trade_id=\{trade_id\} assets=\{n_assets\} "
                    f"contract_updates=\{n_contract_updates\} pick_updates=\{n_pick_updates\} "
                    f"matched_picks=\{matched_pick_ids\} missing_picks=\{missing_pick_ids\}"
                \)
''',
    r'''                (
                    n_contract_updates,
                    n_pick_updates,
                    keeper_assignments,
                ) = apply_trade_assets_atomic(
                    dsn=dsn,
                    draft_key=_get_draft_key(),
                    assets=asset_rows,
                    note="commissioner_trade_builder",
                )

                _refresh_relational_state(state)
                _refresh_option_c_runtime_caches(state)

                # Legacy receipt is audit/history only.
                # Canonical MLF state has already committed successfully.
                try:
                    trade_id = _insert_trade(
                        dsn=dsn,
                        league_key=league_key,
                        season_year=season_year,
                        created_by="commissioner",
                        notes="",
                    )

                    n_assets = _insert_trade_assets(
                        dsn=dsn,
                        trade_id=trade_id,
                        rows=asset_rows,
                    )

                    st.success(
                        f"Trade applied transactionally. trade_id={trade_id} "
                        f"assets={n_assets} "
                        f"contract_updates={n_contract_updates} "
                        f"pick_updates={n_pick_updates} "
                        f"keepers={keeper_assignments}"
                    )

                except Exception as receipt_exc:
                    st.warning(
                        "Canonical trade was applied successfully, "
                        "but the legacy audit receipt failed: "
                        f"{receipt_exc}"
                    )
''',
    "trade builder relational cutover",
)


# ----------------------------------------------------------------
# PT actions.
# ----------------------------------------------------------------

ui = replace_once(
    ui,
    '''                if st.button("Add / Update PT", type="primary", key="pt_add_btn", disabled=(pt_player_key == "")):
                    if dsn and current_pt_player_key:
                        _delete_pt_player(dsn, league_key, season_year, current_pt_player_key)
                    if dsn:
                        _upsert_pt_player(dsn, league_key, season_year, pt_team_key, pt_player_key)
                        state.pt_player_team_map = _load_pt_map(dsn, league_key, season_year)
                    st.success("PT saved.")
                    save_autosave(state)
                    st.rerun()
''',
    '''                if st.button("Add / Update PT", type="primary", key="pt_add_btn", disabled=(pt_player_key == "")):
                    replace_prospect_tag_atomic(
                        dsn=dsn,
                        draft_key=_get_draft_key(),
                        old_yahoo_player_key=current_pt_player_key or None,
                        team_key=pt_team_key,
                        new_yahoo_player_key=pt_player_key,
                        note="commissioner",
                    )

                    _refresh_relational_state(state)
                    _refresh_option_c_runtime_caches(state)

                    st.success("PT saved.")
                    st.rerun()
''',
    "PT add/update cutover",
)

ui = replace_once(
    ui,
    '''                if st.button("Remove PT", key="pt_remove_btn", disabled=(current_pt_player_key == "")):
                    if dsn and current_pt_player_key:
                        _delete_pt_player(dsn, league_key, season_year, current_pt_player_key)
                        state.pt_player_team_map = _load_pt_map(dsn, league_key, season_year)
                    st.success("PT removed.")
                    save_autosave(state)
                    st.rerun()
''',
    '''                if st.button("Remove PT", key="pt_remove_btn", disabled=(current_pt_player_key == "")):
                    if current_pt_player_key:
                        delete_prospect_tag_atomic(
                            dsn=dsn,
                            draft_key=_get_draft_key(),
                            yahoo_player_key=current_pt_player_key,
                        )

                        _refresh_relational_state(state)
                        _refresh_option_c_runtime_caches(state)

                    st.success("PT removed.")
                    st.rerun()
''',
    "PT remove cutover",
)


# ----------------------------------------------------------------
# Contract overrides.
# ----------------------------------------------------------------

ui = regex_once(
    ui,
    r'''                if st\.button\("Save Contract Action", type="primary", key="contract_override_save"\):
.*?
                    else:
                        st\.error\("Select a player first\."\)
''',
    r'''                if st.button("Save Contract Action", type="primary", key="contract_override_save"):
                    if override_player_key:
                        upsert_contract_override_atomic(
                            dsn=dsn,
                            draft_key=_get_draft_key(),
                            yahoo_player_key=override_player_key,
                            team_key=(
                                None
                                if mode == "Void contract (years=0)"
                                else str(team_key)
                            ),
                            years_remaining=int(years),
                            note=note,
                        )

                        _refresh_relational_state(state)
                        _refresh_option_c_runtime_caches(state)

                        if mode == "Void contract (years=0)":
                            st.success(
                                "Contract override void saved."
                            )
                        else:
                            st.success(
                                "Contract override saved."
                            )

                        st.rerun()
                    else:
                        st.error("Select a player first.")
''',
    "contract override save cutover",
)

ui = replace_once(
    ui,
    '''                if st.button("Delete Override", key="contract_override_delete"):
                    n = _delete_contract_override(dsn, league_key, season_year, override_player_key)
                    _refresh_contract_cache_into_session_state()
                    st.success(f"Deleted {n} override row(s). Contract cache refreshed.")
                    st.rerun()
''',
    '''                if st.button("Delete Override", key="contract_override_delete"):
                    if override_player_key:
                        delete_contract_override_atomic(
                            dsn=dsn,
                            draft_key=_get_draft_key(),
                            yahoo_player_key=override_player_key,
                        )

                        _refresh_relational_state(state)
                        _refresh_option_c_runtime_caches(state)

                        st.success(
                            "Contract override deleted."
                        )
                        st.rerun()
                    else:
                        st.error("Select a player first.")
''',
    "contract override delete cutover",
)

ui = replace_once(
    ui,
    '''                if st.button("Refresh contract cache only", key="contract_override_refresh_cache"):
                    _refresh_contract_cache_into_session_state()
                    st.success("Contract cache refreshed.")
                    st.rerun()
''',
    '''                if st.button("Refresh contract cache only", key="contract_override_refresh_cache"):
                    _refresh_option_c_runtime_caches(state)
                    st.success("Contract cache refreshed.")
                    st.rerun()
''',
    "contract cache refresh cutover",
)

ui = replace_once(
    ui,
    '''                rows = _load_contract_overrides(dsn, league_key, season_year)
''',
    '''                rows = _load_relational_contract_overrides(
                    state=state,
                    dsn=dsn,
                    league_key=league_key,
                    season_year=season_year,
                )
''',
    "contract override relational reader",
)


# ----------------------------------------------------------------
# Current pick.
# ----------------------------------------------------------------

ui = replace_once(
    ui,
    '''        if new_pick != state.clock.current_pick_id:
            set_current_pick(new_pick)
            save_autosave(state)
            st.success(f"Current pick set to {new_pick}.")
            st.rerun()
''',
    '''        if new_pick != state.clock.current_pick_id:
            set_current_pick_atomic(
                dsn=_get_dsn(),
                draft_key=_get_draft_key(),
                pick_id=new_pick,
            )

            _refresh_relational_state(state)

            st.success(f"Current pick set to {new_pick}.")
            st.rerun()
''',
    "current-pick relational cutover",
)


# ----------------------------------------------------------------
# Clock buttons rerun after canonical mutation.
# ----------------------------------------------------------------

ui = replace_once(
    ui,
    '''            if st.button("Start Draft", type="primary", key="clock_start", disabled=not can_start):
                _start_clock_if_needed(state)
                st.success("Clock started.")
''',
    '''            if st.button("Start Draft", type="primary", key="clock_start", disabled=not can_start):
                _start_clock_if_needed(state)
                st.success("Clock started.")
                st.rerun()
''',
    "clock start rerun",
)

ui = replace_once(
    ui,
    '''            if st.button("Pause Clock", type="secondary", key="clock_pause", disabled=not can_pause):
                _pause_clock(state)
                st.success("Clock paused.")
''',
    '''            if st.button("Pause Clock", type="secondary", key="clock_pause", disabled=not can_pause):
                _pause_clock(state)
                st.success("Clock paused.")
                st.rerun()
''',
    "clock pause rerun",
)

ui = replace_once(
    ui,
    '''            if st.button("Resume Clock", type="secondary", key="clock_resume", disabled=not can_resume):
                _resume_clock(state)
                st.success("Clock resumed.")
''',
    '''            if st.button("Resume Clock", type="secondary", key="clock_resume", disabled=not can_resume):
                _resume_clock(state)
                st.success("Clock resumed.")
                st.rerun()
''',
    "clock resume rerun",
)


# ----------------------------------------------------------------
# Clock configuration.
# ----------------------------------------------------------------

ui = replace_once(
    ui,
    '''        if int(new_seconds) != int(state.clock.seconds_per_pick):
            state.clock.seconds_per_pick = int(new_seconds)
            save_autosave(state)
            st.info("Pick duration updated.")
''',
    '''        if int(new_seconds) != int(state.clock.seconds_per_pick):
            update_draft_clock_atomic(
                dsn=_get_dsn(),
                draft_key=_get_draft_key(),
                action="SET_DURATION",
                seconds_per_pick=int(new_seconds),
            )

            _refresh_relational_state(state)

            st.info("Pick duration updated.")
            st.rerun()
''',
    "clock duration cutover",
)

ui = replace_once(
    ui,
    '''        if bool(weekends) != bool(state.clock.weekends_count):
            state.clock.weekends_count = bool(weekends)
            save_autosave(state)
            st.info("Weekend rule updated.")
''',
    '''        if bool(weekends) != bool(state.clock.weekends_count):
            update_draft_clock_atomic(
                dsn=_get_dsn(),
                draft_key=_get_draft_key(),
                action="SET_WEEKENDS",
                weekends_count=bool(weekends),
            )

            _refresh_relational_state(state)

            st.info("Weekend rule updated.")
            st.rerun()
''',
    "clock weekend cutover",
)


# ----------------------------------------------------------------
# Remove any remaining commissioner autosave writes.
# At this point any survivor is a defect, not something to erase blindly.
# ----------------------------------------------------------------

if "save_autosave" in ui:
    lines = [
        line
        for line in ui.splitlines()
        if "save_autosave" in line
    ]

    raise RuntimeError(
        "Commissioner autosave reference survived:\n"
        + "\n".join(lines)
    )


# Ensure legacy mutation helpers are no longer CALLED.
tree = ast.parse(
    ui,
    filename=str(COMMISSIONER),
)

legacy_calls = {
    "_update_contract_team_key",
    "_upsert_contract_override",
    "_delete_contract_override",
    "_upsert_pt_player",
    "_delete_pt_player",
    "_void_contract_ssot",
    "_upsert_contract_ssot",
}

bad_calls: list[str] = []

for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue

    if isinstance(node.func, ast.Name):
        name = node.func.id

        if name in legacy_calls:
            bad_calls.append(
                f"{name}@{node.lineno}"
            )

if bad_calls:
    raise RuntimeError(
        "Legacy commissioner mutation call survived: "
        + ", ".join(bad_calls)
    )


required_ui_calls = {
    "apply_trade_assets_atomic",
    "replace_prospect_tag_atomic",
    "delete_prospect_tag_atomic",
    "upsert_contract_override_atomic",
    "delete_contract_override_atomic",
    "reset_draft_atomic",
    "delete_draft_selection_atomic",
    "set_current_pick_atomic",
    "update_draft_clock_atomic",
    "rebase_draft_order_atomic",
    "load_draft_state_projection",
    "replace_state",
}

seen_calls: set[str] = set()

for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            seen_calls.add(
                node.func.id
            )

missing = sorted(
    required_ui_calls - seen_calls
)

if missing:
    raise RuntimeError(
        "Required relational UI call missing: "
        + ", ".join(missing)
    )


write(
    COMMISSIONER,
    ui,
)


# Parse all outputs after writing.
for path in (
    RUNTIME,
    COMMISSIONER,
):
    ast.parse(
        read(path),
        filename=str(path),
    )


print(
    "CREATED="
    + MIGRATION.relative_to(ROOT).as_posix()
)

print(
    "UPDATED="
    + RUNTIME.relative_to(ROOT).as_posix()
)

print(
    "UPDATED="
    + COMMISSIONER.relative_to(ROOT).as_posix()
)

print("OPTION_C_008_PATCH_SOURCE=PASS")
