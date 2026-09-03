BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- ============================================================
-- Reconstruct current QO ladder from baseline QOs plus all
-- remaining real QO-round selections.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.rebuild_draft_qo_current(
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
    v_qo_rounds integer;

    v_owner_current_slot_player text;
    v_selected_qo_team_key text;
    v_selected_qo_level integer;

    v_count integer;

    r record;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION 'Missing draft key.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
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
        RAISE EXCEPTION 'Draft % not found.', p_draft_key;
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

    FOR r IN
        SELECT
            dp.round_number,
            dp.slot_number,
            ds.selecting_team_key,
            ds.yahoo_player_key,
            ds.selected_at_utc
        FROM mlf.draft_selection ds
        JOIN mlf.draft_pick dp
          ON dp.draft_key = ds.draft_key
         AND dp.pick_id = ds.pick_id
        WHERE ds.draft_key = p_draft_key
          AND dp.round_number <= v_qo_rounds
        ORDER BY
            ds.selected_at_utc,
            dp.round_number,
            dp.slot_number
    LOOP
        v_owner_current_slot_player = NULL;
        v_selected_qo_team_key = NULL;
        v_selected_qo_level = NULL;

        SELECT qc.yahoo_player_key
        INTO v_owner_current_slot_player
        FROM mlf.draft_qo_current qc
        WHERE qc.draft_key = p_draft_key
          AND qc.team_key = r.selecting_team_key
          AND qc.qo_level = r.round_number;

        SELECT
            qc.team_key,
            qc.qo_level
        INTO
            v_selected_qo_team_key,
            v_selected_qo_level
        FROM mlf.draft_qo_current qc
        WHERE qc.draft_key = p_draft_key
          AND qc.yahoo_player_key = r.yahoo_player_key;

        IF NOT (
            v_owner_current_slot_player IS NOT NULL
            AND r.yahoo_player_key = v_owner_current_slot_player
        )
        THEN
            DELETE FROM mlf.draft_qo_current qc
            WHERE qc.draft_key = p_draft_key
              AND qc.team_key = r.selecting_team_key
              AND qc.qo_level = r.round_number;

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
    END LOOP;

    UPDATE mlf.draft_runtime
       SET qo_state_seeded = true,
           updated_at_utc = now()
     WHERE draft_key = p_draft_key;

    SELECT count(*)
    INTO v_count
    FROM mlf.draft_qo_current
    WHERE draft_key = p_draft_key;

    RETURN v_count;
END;
$function$;


-- ============================================================
-- Commissioner reset: remove real selections, restore original
-- QO ladder, and reset runtime to the first non-keeper slot.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.reset_draft_atomic(
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
        RAISE EXCEPTION 'Missing draft key.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
    );

    PERFORM 1
    FROM mlf.draft
    WHERE draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Draft % not found.', p_draft_key;
    END IF;

    PERFORM 1
    FROM mlf.draft_runtime
    WHERE draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft runtime is not initialized for %.',
            p_draft_key;
    END IF;

    DELETE FROM mlf.draft_selection
    WHERE draft_key = p_draft_key;

    PERFORM mlf.rebuild_draft_qo_current(p_draft_key);

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

    UPDATE mlf.draft_runtime
       SET current_pick_id = v_first_pick_id,
           is_running = false,
           pick_started_at_utc = NULL,
           pick_paused_at_utc = NULL,
           elapsed_paused_seconds = 0,
           updated_at_utc = now()
     WHERE draft_key = p_draft_key;

    RETURN v_first_pick_id;
END;
$function$;


-- ============================================================
-- Delete one real selection and rebuild evolving QO state.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.delete_draft_selection_atomic(
    p_draft_key text,
    p_pick_id text,
    p_rewind_clock boolean DEFAULT false
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_runtime_pick text;
    v_open_pick text;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_pick_id), '') IS NULL
    THEN
        RAISE EXCEPTION 'Missing draft/pick input.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
    );

    PERFORM 1
    FROM mlf.draft_runtime
    WHERE draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft runtime is not initialized for %.',
            p_draft_key;
    END IF;

    DELETE FROM mlf.draft_selection
    WHERE draft_key = p_draft_key
      AND pick_id = p_pick_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Pick % has no real selection.',
            p_pick_id;
    END IF;

    PERFORM mlf.rebuild_draft_qo_current(p_draft_key);

    IF COALESCE(p_rewind_clock, false) THEN
        IF EXISTS (
            SELECT 1
            FROM mlf.draft_keeper_assignment ka
            WHERE ka.draft_key = p_draft_key
              AND ka.pick_id = p_pick_id
        ) THEN
            RAISE EXCEPTION
                'Cannot rewind to keeper-occupied pick %.',
                p_pick_id;
        END IF;

        UPDATE mlf.draft_runtime
           SET current_pick_id = p_pick_id,
               is_running = false,
               pick_started_at_utc = NULL,
               pick_paused_at_utc = NULL,
               elapsed_paused_seconds = 0,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

        RETURN p_pick_id;
    END IF;

    SELECT current_pick_id
    INTO v_runtime_pick
    FROM mlf.draft_runtime
    WHERE draft_key = p_draft_key;

    IF v_runtime_pick IS NULL THEN
        SELECT dp.pick_id
        INTO v_open_pick
        FROM mlf.draft_pick dp
        LEFT JOIN mlf.draft_selection ds
          ON ds.draft_key = dp.draft_key
         AND ds.pick_id = dp.pick_id
        LEFT JOIN mlf.draft_keeper_assignment ka
          ON ka.draft_key = dp.draft_key
         AND ka.pick_id = dp.pick_id
        WHERE dp.draft_key = p_draft_key
          AND ds.pick_id IS NULL
          AND ka.pick_id IS NULL
        ORDER BY
            dp.round_number,
            dp.slot_number
        LIMIT 1;

        UPDATE mlf.draft_runtime
           SET current_pick_id = v_open_pick,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

        RETURN v_open_pick;
    END IF;

    RETURN v_runtime_pick;
END;
$function$;


-- ============================================================
-- Explicit commissioner current-pick move.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.set_current_pick_atomic(
    p_draft_key text,
    p_pick_id text
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_pick_id), '') IS NULL
    THEN
        RAISE EXCEPTION 'Missing draft/pick input.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
    );

    PERFORM 1
    FROM mlf.draft_runtime
    WHERE draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft runtime is not initialized for %.',
            p_draft_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.draft_pick dp
        WHERE dp.draft_key = p_draft_key
          AND dp.pick_id = p_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % does not exist.',
            p_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.pick_id = p_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % already contains a real selection.',
            p_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_keeper_assignment ka
        WHERE ka.draft_key = p_draft_key
          AND ka.pick_id = p_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % is keeper-occupied.',
            p_pick_id;
    END IF;

    UPDATE mlf.draft_runtime
       SET current_pick_id = p_pick_id,
           updated_at_utc = now()
     WHERE draft_key = p_draft_key;

    RETURN p_pick_id;
END;
$function$;


-- ============================================================
-- Clock mutation boundary.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.update_draft_clock_atomic(
    p_draft_key text,
    p_action text,
    p_seconds_per_pick integer DEFAULT NULL,
    p_weekends_count boolean DEFAULT NULL
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_action text;
    v_now timestamptz;
    v_started timestamptz;
    v_running boolean;
    v_paused timestamptz;
    v_elapsed integer;
    v_current_pick text;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION 'Missing draft key.';
    END IF;

    v_action = UPPER(BTRIM(COALESCE(p_action, '')));

    IF v_action NOT IN (
        'START',
        'PAUSE',
        'RESUME',
        'STOP',
        'SET_DURATION',
        'SET_WEEKENDS'
    ) THEN
        RAISE EXCEPTION
            'Unsupported clock action %.',
            v_action;
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
    );

    SELECT
        dr.current_pick_id,
        dr.is_running,
        dr.pick_started_at_utc,
        dr.pick_paused_at_utc,
        dr.elapsed_paused_seconds
    INTO
        v_current_pick,
        v_running,
        v_started,
        v_paused,
        v_elapsed
    FROM mlf.draft_runtime dr
    WHERE dr.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft runtime is not initialized for %.',
            p_draft_key;
    END IF;

    v_now = pg_catalog.clock_timestamp();

    IF v_action = 'START' THEN
        IF v_current_pick IS NULL THEN
            RAISE EXCEPTION 'Cannot start a completed draft.';
        END IF;

        UPDATE mlf.draft_runtime
           SET is_running = true,
               pick_started_at_utc = v_now,
               pick_paused_at_utc = NULL,
               elapsed_paused_seconds = 0,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

    ELSIF v_action = 'PAUSE' THEN
        IF NOT v_running OR v_started IS NULL THEN
            RAISE EXCEPTION 'Draft clock is not running.';
        END IF;

        UPDATE mlf.draft_runtime
           SET elapsed_paused_seconds =
                   v_elapsed
                   + GREATEST(
                       0,
                       FLOOR(
                           EXTRACT(EPOCH FROM (v_now - v_started))
                       )::integer
                   ),
               pick_paused_at_utc = v_now,
               is_running = false,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

    ELSIF v_action = 'RESUME' THEN
        IF v_paused IS NULL THEN
            RAISE EXCEPTION 'Draft clock is not paused.';
        END IF;

        UPDATE mlf.draft_runtime
           SET pick_started_at_utc = v_now,
               pick_paused_at_utc = NULL,
               is_running = true,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

    ELSIF v_action = 'STOP' THEN
        UPDATE mlf.draft_runtime
           SET is_running = false,
               pick_started_at_utc = NULL,
               pick_paused_at_utc = NULL,
               elapsed_paused_seconds = 0,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

    ELSIF v_action = 'SET_DURATION' THEN
        IF p_seconds_per_pick IS NULL
           OR p_seconds_per_pick <= 0
        THEN
            RAISE EXCEPTION
                'seconds_per_pick must be positive.';
        END IF;

        UPDATE mlf.draft_runtime
           SET seconds_per_pick = p_seconds_per_pick,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;

    ELSIF v_action = 'SET_WEEKENDS' THEN
        IF p_weekends_count IS NULL THEN
            RAISE EXCEPTION
                'weekends_count is required.';
        END IF;

        UPDATE mlf.draft_runtime
           SET weekends_count = p_weekends_count,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key;
    END IF;

    RETURN v_action;
END;
$function$;


-- ============================================================
-- Persist a traded-pick owner in relational draft truth.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.transfer_draft_pick_atomic(
    p_draft_key text,
    p_pick_id text,
    p_to_team_key text,
    p_note text DEFAULT NULL
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_pick_id), '') IS NULL
       OR NULLIF(BTRIM(p_to_team_key), '') IS NULL
    THEN
        RAISE EXCEPTION 'Missing draft/pick/team input.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
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
        RAISE EXCEPTION 'Draft % not found.', p_draft_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.team t
        WHERE t.league_key = v_league_key
          AND t.season_year = v_season_year
          AND t.team_key = p_to_team_key
    ) THEN
        RAISE EXCEPTION
            'Destination team % is not in this draft.',
            p_to_team_key;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.pick_id = p_pick_id
    ) THEN
        RAISE EXCEPTION
            'Cannot trade already-selected pick %.',
            p_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_keeper_assignment ka
        WHERE ka.draft_key = p_draft_key
          AND ka.pick_id = p_pick_id
    ) THEN
        RAISE EXCEPTION
            'Cannot trade keeper-occupied pick %.',
            p_pick_id;
    END IF;

    UPDATE mlf.draft_pick dp
       SET current_owner_team_key = p_to_team_key,
           traded_flag = (p_to_team_key <> dp.column_team_key),
           ownership_note =
               NULLIF(BTRIM(COALESCE(p_note, '')), '')
     WHERE dp.draft_key = p_draft_key
       AND dp.pick_id = p_pick_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Pick % does not exist.',
            p_pick_id;
    END IF;

    RETURN p_to_team_key;
END;
$function$;


-- ============================================================
-- Persist slot-order changes. Existing traded ownership is
-- preserved; untraded picks follow their new slot baseline.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.rebase_draft_order_atomic(
    p_draft_key text,
    p_team_keys text[]
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
    v_manager_count integer;
    v_count integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION 'Missing draft key.';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(p_draft_key, 0)
    );

    SELECT
        d.league_key,
        d.season_year,
        d.manager_count
    INTO
        v_league_key,
        v_season_year,
        v_manager_count
    FROM mlf.draft d
    WHERE d.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Draft % not found.', p_draft_key;
    END IF;

    IF pg_catalog.cardinality(p_team_keys) <> v_manager_count THEN
        RAISE EXCEPTION
            'Expected % draft-order teams; received %.',
            v_manager_count,
            pg_catalog.cardinality(p_team_keys);
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.unnest(p_team_keys) AS x(team_key)
        WHERE NULLIF(BTRIM(x.team_key), '') IS NULL
    ) THEN
        RAISE EXCEPTION 'Draft order contains a blank team key.';
    END IF;

    SELECT count(DISTINCT x.team_key)
    INTO v_count
    FROM pg_catalog.unnest(p_team_keys) AS x(team_key);

    IF v_count <> v_manager_count THEN
        RAISE EXCEPTION 'Draft order contains duplicate teams.';
    END IF;

    SELECT count(*)
    INTO v_count
    FROM pg_catalog.unnest(p_team_keys) AS x(team_key)
    JOIN mlf.team t
      ON t.league_key = v_league_key
     AND t.season_year = v_season_year
     AND t.team_key = x.team_key;

    IF v_count <> v_manager_count THEN
        RAISE EXCEPTION
            'Draft order contains a team outside this draft.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mlf.draft_selection ds
        WHERE ds.draft_key = p_draft_key
    ) THEN
        RAISE EXCEPTION
            'Draft order cannot be rebased after selections exist.';
    END IF;

    WITH mapping AS (
        SELECT
            x.team_key,
            x.ordinality::integer AS slot_number
        FROM pg_catalog.unnest(p_team_keys)
             WITH ORDINALITY AS x(team_key, ordinality)
    )
    UPDATE mlf.draft_pick dp
       SET current_owner_team_key =
               CASE
                   WHEN dp.current_owner_team_key = dp.column_team_key
                       THEN mapping.team_key
                   ELSE dp.current_owner_team_key
               END,
           column_team_key = mapping.team_key,
           traded_flag =
               CASE
                   WHEN dp.current_owner_team_key = dp.column_team_key
                       THEN false
                   ELSE dp.current_owner_team_key <> mapping.team_key
               END
      FROM mapping
     WHERE dp.draft_key = p_draft_key
       AND dp.slot_number = mapping.slot_number;

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN v_count;
END;
$function$;


REVOKE ALL
ON FUNCTION mlf.rebuild_draft_qo_current(text)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.reset_draft_atomic(text)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.delete_draft_selection_atomic(text, text, boolean)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.set_current_pick_atomic(text, text)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.update_draft_clock_atomic(text, text, integer, boolean)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.transfer_draft_pick_atomic(text, text, text, text)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.rebase_draft_order_atomic(text, text[])
FROM PUBLIC;


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '004',
    'Atomic MLF commissioner draft and clock runtime operations'
);

COMMIT;
