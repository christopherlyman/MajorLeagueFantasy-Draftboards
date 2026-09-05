-- MLF Relational Migration - 003
-- Relational draft runtime + atomic MLF pick execution.
--
-- Source-only construction. Do not apply to production yet.
--
-- Architectural rules:
--
--   * mlf.draft_selection is authoritative for real picks.
--   * mlf.draft_runtime is authoritative for the current pick/clock.
--   * mlf.draft_qo_current is the evolving QO ladder.
--   * mlf.draft_keeper_assignment marks CONTRACT/PT occupied picks.
--   * DraftState JSON is not canonical transaction state.
--   * PostgreSQL independently classifies FA / QO / POACH.
--
-- QO semantics:
--
--   * Own current-slot QO -> QO; current QO ladder remains visible.
--   * Own other current QO -> QO.
--   * Other team's current QO -> POACH only when its current QO
--     level is greater than the current draft round.
--   * A non-current-slot selection consumes the owner's current
--     QO slot.
--   * Removing a QO shifts that team's lower QOs upward.
--
-- Clock-expiry / late-pick behavior is deliberately deferred.
-- Migration 003 proves the core serialized live-pick transaction first.

BEGIN;

CREATE TABLE mlf.player_universe (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    yahoo_player_key text NOT NULL,

    player_name text,
    primary_position text,

    is_active boolean NOT NULL DEFAULT true,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT player_universe_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT player_universe_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    CONSTRAINT player_universe_player_key_ck
        CHECK (btrim(yahoo_player_key) <> '')
);

CREATE TABLE mlf.draft_keeper_assignment (
    draft_key text NOT NULL,
    pick_id text NOT NULL,

    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,

    keeper_kind text NOT NULL,

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_keeper_assignment_pkey
        PRIMARY KEY (
            draft_key,
            pick_id
        ),

    CONSTRAINT draft_keeper_assignment_pick_fk
        FOREIGN KEY (
            draft_key,
            pick_id
        )
        REFERENCES mlf.draft_pick (
            draft_key,
            pick_id
        )
        ON DELETE CASCADE,

    CONSTRAINT draft_keeper_assignment_player_uq
        UNIQUE (
            draft_key,
            yahoo_player_key
        ),

    CONSTRAINT draft_keeper_assignment_kind_ck
        CHECK (
            keeper_kind IN (
                'CONTRACT',
                'PT'
            )
        ),

    CONSTRAINT draft_keeper_assignment_team_ck
        CHECK (btrim(team_key) <> ''),

    CONSTRAINT draft_keeper_assignment_player_ck
        CHECK (btrim(yahoo_player_key) <> '')
);

CREATE TABLE mlf.draft_qo_current (
    draft_key text NOT NULL,
    team_key text NOT NULL,
    qo_level integer NOT NULL,
    yahoo_player_key text NOT NULL,

    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_qo_current_pkey
        PRIMARY KEY (
            draft_key,
            team_key,
            qo_level
        ),

    CONSTRAINT draft_qo_current_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES mlf.draft (draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_qo_current_player_uq
        UNIQUE (
            draft_key,
            yahoo_player_key
        ),

    CONSTRAINT draft_qo_current_level_ck
        CHECK (
            qo_level >= 1
            AND qo_level <= 5
        ),

    CONSTRAINT draft_qo_current_team_ck
        CHECK (
            btrim(team_key) <> ''
            AND team_key NOT LIKE 'TEAM_%'
        ),

    CONSTRAINT draft_qo_current_player_ck
        CHECK (btrim(yahoo_player_key) <> '')
);

CREATE TABLE mlf.draft_runtime (
    draft_key text PRIMARY KEY,

    current_pick_id text,

    auto_advance boolean NOT NULL DEFAULT true,
    is_running boolean NOT NULL DEFAULT false,

    pick_started_at_utc timestamptz,
    pick_paused_at_utc timestamptz,

    elapsed_paused_seconds integer NOT NULL DEFAULT 0,
    seconds_per_pick integer NOT NULL DEFAULT 86400,

    weekends_count boolean NOT NULL DEFAULT false,
    timezone_name text NOT NULL DEFAULT 'America/New_York',

    qo_state_seeded boolean NOT NULL DEFAULT false,

    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_runtime_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES mlf.draft (draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_runtime_current_pick_fk
        FOREIGN KEY (
            draft_key,
            current_pick_id
        )
        REFERENCES mlf.draft_pick (
            draft_key,
            pick_id
        ),

    CONSTRAINT draft_runtime_elapsed_ck
        CHECK (elapsed_paused_seconds >= 0),

    CONSTRAINT draft_runtime_seconds_per_pick_ck
        CHECK (seconds_per_pick > 0),

    CONSTRAINT draft_runtime_timezone_ck
        CHECK (btrim(timezone_name) <> '')
);

CREATE INDEX ix_mlf_player_universe_active
    ON mlf.player_universe (
        league_key,
        season_year,
        is_active,
        yahoo_player_key
    );

CREATE INDEX ix_mlf_draft_keeper_assignment_team
    ON mlf.draft_keeper_assignment (
        draft_key,
        team_key
    );

CREATE INDEX ix_mlf_draft_qo_current_player
    ON mlf.draft_qo_current (
        draft_key,
        yahoo_player_key
    );

CREATE OR REPLACE FUNCTION mlf.initialize_draft_runtime(
    p_draft_key text
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_first_pick_id text;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION
            'Missing draft key.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    PERFORM 1
    FROM mlf.draft d
    WHERE d.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft % not found.',
            p_draft_key;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_runtime dr
        WHERE dr.draft_key = p_draft_key
    ) THEN
        RAISE EXCEPTION
            'Draft runtime already initialized for %.',
            p_draft_key;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
    ) THEN
        RAISE EXCEPTION
            'Cannot initialize runtime after selections exist for %.',
            p_draft_key;
    END IF;

    SELECT dp.pick_id
      INTO v_first_pick_id
    FROM mlf.draft_pick dp
    LEFT JOIN mlf.draft_keeper_assignment ka
      ON ka.draft_key = dp.draft_key
     AND ka.pick_id = dp.pick_id
    WHERE dp.draft_key = p_draft_key
      AND ka.pick_id IS NULL
    ORDER BY
        dp.round_number,
        dp.slot_number
    LIMIT 1;

    INSERT INTO mlf.draft_runtime (
        draft_key,
        current_pick_id
    )
    VALUES (
        p_draft_key,
        v_first_pick_id
    );

    RETURN v_first_pick_id;
END;
$function$;

CREATE OR REPLACE FUNCTION mlf.seed_draft_qo_current(
    p_draft_key text
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
    v_count integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION
            'Missing draft key.';
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

    PERFORM 1
    FROM mlf.draft_runtime dr
    WHERE dr.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft runtime must be initialized before QO seeding.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
    ) THEN
        RAISE EXCEPTION
            'Cannot reseed QO state after selections exist.';
    END IF;

    DELETE FROM mlf.draft_qo_current
    WHERE draft_key = p_draft_key;

    INSERT INTO mlf.draft_qo_current (
        draft_key,
        team_key,
        qo_level,
        yahoo_player_key
    )
    SELECT
        p_draft_key,
        q.team_key,
        q.qo_level,
        q.yahoo_player_key
    FROM mlf.qualifying_offer q
    WHERE q.league_key = v_league_key
      AND q.season_year = v_season_year
    ORDER BY
        q.team_key,
        q.qo_level;

    GET DIAGNOSTICS v_count = ROW_COUNT;

    UPDATE mlf.draft_runtime
       SET qo_state_seeded = true,
           updated_at_utc = now()
     WHERE draft_key = p_draft_key;

    RETURN v_count;
END;
$function$;

CREATE OR REPLACE FUNCTION mlf.submit_draft_pick_atomic(
    p_draft_key text,
    p_expected_pick_id text,
    p_expected_team_key text,
    p_yahoo_player_key text,
    p_expected_pick_kind text DEFAULT NULL,
    p_initiated_by text DEFAULT 'draftboard'
)
RETURNS TABLE(
    result_status text,
    executed_pick_id text,
    selecting_team_key text,
    selected_player_key text,
    selected_pick_kind text,
    next_pick_id text,
    selected_at_utc timestamptz
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;
    v_qo_rounds integer;

    v_current_pick_id text;
    v_auto_advance boolean;
    v_qo_state_seeded boolean;

    v_owner_team_key text;
    v_round_number integer;
    v_slot_number integer;

    v_owner_current_slot_player text;

    v_selected_qo_team_key text;
    v_selected_qo_level integer;

    v_pick_kind text;
    v_expected_kind text;

    v_selected_at timestamptz;
    v_next_pick_id text;

    v_inserted integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_expected_pick_id), '') IS NULL
       OR NULLIF(BTRIM(p_expected_team_key), '') IS NULL
       OR NULLIF(BTRIM(p_yahoo_player_key), '') IS NULL
    THEN
        RAISE EXCEPTION
            'Missing required draft/pick/team/player input.';
    END IF;

    v_expected_kind =
        NULLIF(
            UPPER(
                BTRIM(
                    COALESCE(
                        p_expected_pick_kind,
                        ''
                    )
                )
            ),
            ''
        );

    IF v_expected_kind IS NOT NULL
       AND v_expected_kind NOT IN (
            'FA',
            'QO',
            'POACH'
       )
    THEN
        RAISE EXCEPTION
            'Invalid expected pick kind %.',
            v_expected_kind;
    END IF;

    -- One transactional pick executor at a time per draft.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT
        d.league_key,
        d.season_year,
        d.qo_rounds
      INTO
        v_league_key,
        v_season_year,
        v_qo_rounds
    FROM mlf.draft d
    WHERE d.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft % not found.',
            p_draft_key;
    END IF;

    SELECT
        dr.current_pick_id,
        dr.auto_advance,
        dr.qo_state_seeded
      INTO
        v_current_pick_id,
        v_auto_advance,
        v_qo_state_seeded
    FROM mlf.draft_runtime dr
    WHERE dr.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft runtime not initialized for %.',
            p_draft_key;
    END IF;

    IF v_current_pick_id IS NULL THEN
        RAISE EXCEPTION
            'Draft % is complete.',
            p_draft_key;
    END IF;

    IF v_current_pick_id IS DISTINCT FROM p_expected_pick_id THEN
        RAISE EXCEPTION
            'Pick % is not active. Active pick is %.',
            p_expected_pick_id,
            v_current_pick_id;
    END IF;

    SELECT
        dp.current_owner_team_key,
        dp.round_number,
        dp.slot_number
      INTO
        v_owner_team_key,
        v_round_number,
        v_slot_number
    FROM mlf.draft_pick dp
    WHERE dp.draft_key = p_draft_key
      AND dp.pick_id = p_expected_pick_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft pick % not found.',
            p_expected_pick_id;
    END IF;

    IF v_owner_team_key IS DISTINCT FROM p_expected_team_key THEN
        RAISE EXCEPTION
            'Wrong pick owner. Expected %, found %.',
            p_expected_team_key,
            v_owner_team_key;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_keeper_assignment ka
        WHERE ka.draft_key = p_draft_key
          AND ka.pick_id = p_expected_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % is occupied by a keeper assignment.',
            p_expected_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.pick_id = p_expected_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % already has a real selection.',
            p_expected_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.yahoo_player_key = p_yahoo_player_key
    ) THEN
        RAISE EXCEPTION
            'Player % is already drafted.',
            p_yahoo_player_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.player_universe pu
        WHERE pu.league_key = v_league_key
          AND pu.season_year = v_season_year
          AND pu.yahoo_player_key = p_yahoo_player_key
          AND pu.is_active = true
    ) THEN
        RAISE EXCEPTION
            'Player % is not in the active MLF player universe.',
            p_yahoo_player_key;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.v_active_contract c
        WHERE c.league_key = v_league_key
          AND c.season_year = v_season_year
          AND c.yahoo_player_key = p_yahoo_player_key

        UNION ALL

        SELECT 1
        FROM mlf.prospect_tag pt
        WHERE pt.league_key = v_league_key
          AND pt.season_year = v_season_year
          AND pt.yahoo_player_key = p_yahoo_player_key
    ) THEN
        RAISE EXCEPTION
            'Contract/PT player % is not available for a real pick.',
            p_yahoo_player_key;
    END IF;

    v_owner_current_slot_player = NULL;
    v_selected_qo_team_key = NULL;
    v_selected_qo_level = NULL;

    IF v_round_number <= v_qo_rounds THEN
        IF NOT v_qo_state_seeded THEN
            RAISE EXCEPTION
                'QO runtime state is not seeded for draft %.',
                p_draft_key;
        END IF;

        SELECT qc.yahoo_player_key
          INTO v_owner_current_slot_player
        FROM mlf.draft_qo_current qc
        WHERE qc.draft_key = p_draft_key
          AND qc.team_key = v_owner_team_key
          AND qc.qo_level = v_round_number;

        SELECT
            qc.team_key,
            qc.qo_level
          INTO
            v_selected_qo_team_key,
            v_selected_qo_level
        FROM mlf.draft_qo_current qc
        WHERE qc.draft_key = p_draft_key
          AND qc.yahoo_player_key = p_yahoo_player_key;

        IF v_owner_current_slot_player IS NOT NULL
           AND p_yahoo_player_key = v_owner_current_slot_player
        THEN
            v_pick_kind = 'QO';

        ELSIF v_selected_qo_team_key IS NOT NULL THEN
            IF v_selected_qo_team_key = v_owner_team_key THEN
                v_pick_kind = 'QO';

            ELSIF v_selected_qo_level > v_round_number THEN
                v_pick_kind = 'POACH';

            ELSE
                RAISE EXCEPTION
                    'Player % is reserved by team % at QO% and is not poach-eligible in round %.',
                    p_yahoo_player_key,
                    v_selected_qo_team_key,
                    v_selected_qo_level,
                    v_round_number;
            END IF;

        ELSE
            v_pick_kind = 'FA';
        END IF;

    ELSE
        -- QO rights are a rounds 1..qo_rounds mechanism.
        IF EXISTS (
            SELECT 1
            FROM mlf.draft_qo_current qc
            WHERE qc.draft_key = p_draft_key
              AND qc.yahoo_player_key = p_yahoo_player_key
        ) THEN
            RAISE EXCEPTION
                'Unresolved QO player % cannot be selected outside QO rounds.',
                p_yahoo_player_key;
        END IF;

        v_pick_kind = 'FA';
    END IF;

    IF v_expected_kind IS NOT NULL
       AND v_expected_kind <> v_pick_kind
    THEN
        RAISE EXCEPTION
            'Pick-kind mismatch. Caller expected %, PostgreSQL classified %.',
            v_expected_kind,
            v_pick_kind;
    END IF;

    v_selected_at = pg_catalog.clock_timestamp();

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
        p_draft_key,
        p_expected_pick_id,
        v_owner_team_key,
        p_yahoo_player_key,
        v_pick_kind,
        v_selected_at,
        NULLIF(BTRIM(COALESCE(p_initiated_by, '')), ''),
        'MLF atomic draft executor'
    )
    ON CONFLICT (
        draft_key,
        pick_id
    )
    DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    IF v_inserted <> 1 THEN
        RAISE EXCEPTION
            'Pick % was claimed concurrently.',
            p_expected_pick_id;
    END IF;

    /*
     * Replay the exact MLF current-QO mutation rule transactionally.
     *
     * Own current-slot QO:
     *   no QO ladder mutation.
     *
     * Anything else in a QO round:
     *   consume the pick owner's current-round slot.
     *
     * If the selected player was a current QO:
     *   remove that QO and shift all lower QOs upward.
     */
    IF v_round_number <= v_qo_rounds
       AND NOT (
            v_owner_current_slot_player IS NOT NULL
            AND p_yahoo_player_key = v_owner_current_slot_player
       )
    THEN
        DELETE FROM mlf.draft_qo_current qc
        WHERE qc.draft_key = p_draft_key
          AND qc.team_key = v_owner_team_key
          AND qc.qo_level = v_round_number;

        IF v_selected_qo_team_key IS NOT NULL THEN
            WITH moved AS (
                DELETE FROM mlf.draft_qo_current qc
                WHERE qc.draft_key = p_draft_key
                  AND qc.team_key = v_selected_qo_team_key
                  AND qc.qo_level >= v_selected_qo_level
                RETURNING
                    qc.qo_level,
                    qc.yahoo_player_key
            )
            INSERT INTO mlf.draft_qo_current (
                draft_key,
                team_key,
                qo_level,
                yahoo_player_key,
                updated_at_utc
            )
            SELECT
                p_draft_key,
                v_selected_qo_team_key,
                moved.qo_level - 1,
                moved.yahoo_player_key,
                now()
            FROM moved
            WHERE moved.qo_level > v_selected_qo_level
            ORDER BY moved.qo_level;
        END IF;
    END IF;

    SELECT dp.pick_id
      INTO v_next_pick_id
    FROM mlf.draft_pick dp
    LEFT JOIN mlf.draft_selection ds
      ON ds.draft_key = dp.draft_key
     AND ds.pick_id = dp.pick_id
    LEFT JOIN mlf.draft_keeper_assignment ka
      ON ka.draft_key = dp.draft_key
     AND ka.pick_id = dp.pick_id
    WHERE dp.draft_key = p_draft_key
      AND (
            dp.round_number > v_round_number
            OR (
                dp.round_number = v_round_number
                AND dp.slot_number > v_slot_number
            )
          )
      AND ds.pick_id IS NULL
      AND ka.pick_id IS NULL
    ORDER BY
        dp.round_number,
        dp.slot_number
    LIMIT 1;

    IF v_next_pick_id IS NOT NULL THEN
        UPDATE mlf.draft_runtime
           SET current_pick_id = v_next_pick_id,
               pick_started_at_utc =
                   CASE
                       WHEN v_auto_advance
                           THEN v_selected_at
                       ELSE pick_started_at_utc
                   END,
               pick_paused_at_utc =
                   CASE
                       WHEN v_auto_advance
                           THEN NULL
                       ELSE pick_paused_at_utc
                   END,
               elapsed_paused_seconds =
                   CASE
                       WHEN v_auto_advance
                           THEN 0
                       ELSE elapsed_paused_seconds
                   END,
               is_running =
                   CASE
                       WHEN v_auto_advance
                           THEN true
                       ELSE is_running
                   END,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

    ELSE
        UPDATE mlf.draft_runtime
           SET current_pick_id = NULL,
               is_running = false,
               pick_started_at_utc = NULL,
               pick_paused_at_utc = NULL,
               elapsed_paused_seconds = 0,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;
    END IF;

    RETURN QUERY
    SELECT
        'EXECUTED'::text,
        p_expected_pick_id,
        v_owner_team_key,
        p_yahoo_player_key,
        v_pick_kind,
        v_next_pick_id,
        v_selected_at;
END;
$function$;

REVOKE ALL
ON FUNCTION mlf.initialize_draft_runtime(text)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.seed_draft_qo_current(text)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.submit_draft_pick_atomic(
    text,
    text,
    text,
    text,
    text,
    text
)
FROM PUBLIC;

INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '003',
    'Relational MLF draft runtime, evolving QO state, and atomic pick executor'
);

COMMIT;