-- NFHL Yahoo-current player draftability enforcement.
--
-- Requires migration 011:
--   nfhl.player_universe.is_yahoo_current
--
-- Player rows remain retained for draft history and saved
-- Auto-Pick identity, but only players present in the most
-- recent complete validated Yahoo player universe may be
-- newly selected.
--
-- These definitions were derived from the live production
-- PostgreSQL functions. Only their player-universe eligibility
-- predicates are changed.

CREATE OR REPLACE FUNCTION nfhl.submit_draft_pick_atomic(p_draft_key text, p_expected_pick_id text, p_expected_team_key text, p_yahoo_player_key text, p_initiated_by text DEFAULT 'draftboard'::text)
 RETURNS TABLE(result_status text, executed_pick_id text, selecting_team_key text, selected_player_key text, late_pick boolean, next_pick_id text, new_state_sha256 text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'nfhl', 'public'
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;
    v_draft_status text;

    v_state jsonb;
    v_current_pick_id text;
    v_is_late_pick boolean := false;

    v_owner_team_key text;
    v_round_number integer;
    v_slot_number integer;

    v_player_name text;
    v_primary_position text;

    v_selected_at timestamptz;
    v_selected_ts_iso text;

    v_next_pick_id text;
    v_next_team_key text;

    v_auto_advance boolean;

    v_event_id text;
    v_log_entry jsonb;

    v_new_sha text;
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

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT
        d.league_key,
        d.season_year,
        d.status
    INTO
        v_league_key,
        v_season_year,
        v_draft_status
    FROM nfhl.draft d
    WHERE d.draft_key = p_draft_key;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft % does not exist.',
            p_draft_key;
    END IF;

    IF UPPER(COALESCE(v_draft_status, '')) <> 'ACTIVE' THEN
        RAISE EXCEPTION
            'Draft % is not active.',
            p_draft_key;
    END IF;

    SELECT c.auto_advance
      INTO v_auto_advance
    FROM nfhl.draft_clock_config c
    WHERE c.draft_key = p_draft_key;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft clock configuration is missing for %.',
            p_draft_key;
    END IF;

    /*
     * Process an exact deadline before evaluating the attempted pick.
     * This closes the race between selection and timeout.
     */
    PERFORM 1
    FROM nfhl.process_draft_clock(
        p_draft_key
    );

    SELECT s.state_json
      INTO v_state
    FROM nfhl.draft_state s
    WHERE s.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft state not found for %.',
            p_draft_key;
    END IF;

    v_current_pick_id =
        v_state
        -> 'clock'
        ->> 'current_pick_id';

    /*
     * An expired/open pick is a late pick even if it is the final
     * pick and current_pick_id still happens to point to it.
     */
    SELECT EXISTS (
        SELECT 1
        FROM nfhl.draft_expired_pick ep
        WHERE ep.draft_key = p_draft_key
          AND ep.pick_id = p_expected_pick_id
          AND ep.resolved_at_utc IS NULL
    )
    INTO v_is_late_pick;

    IF v_current_pick_id IS DISTINCT FROM p_expected_pick_id
       AND NOT v_is_late_pick
    THEN
        RAISE EXCEPTION
            'Pick % is neither active nor expired/open. Active pick is %.',
            p_expected_pick_id,
            v_current_pick_id;
    END IF;

    SELECT
        p.current_owner_team_key,
        p.round_number,
        p.slot_number
    INTO
        v_owner_team_key,
        v_round_number,
        v_slot_number
    FROM nfhl.draft_pick p
    WHERE p.draft_key = p_draft_key
      AND p.pick_id = p_expected_pick_id
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
        FROM nfhl.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.pick_id = p_expected_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % already has a selection.',
            p_expected_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM nfhl.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.yahoo_player_key = p_yahoo_player_key
    ) THEN
        RAISE EXCEPTION
            'Player % is already drafted.',
            p_yahoo_player_key;
    END IF;

    SELECT
        pu.full_name,
        pu.primary_position
    INTO
        v_player_name,
        v_primary_position
    FROM nfhl.player_universe pu
    WHERE pu.league_key = v_league_key
      AND pu.season_year = v_season_year
      AND pu.yahoo_player_key = p_yahoo_player_key
      AND pu.is_yahoo_current;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Player % is not in the active NFHL player universe.',
            p_yahoo_player_key;
    END IF;

    v_selected_at =
        clock_timestamp();

    v_selected_ts_iso =
        to_char(
            v_selected_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US'
        );

    INSERT INTO nfhl.draft_selection (
        draft_key,
        pick_id,
        selecting_team_key,
        yahoo_player_key,
        selected_at_utc,
        selected_by,
        note
    )
    VALUES (
        p_draft_key,
        p_expected_pick_id,
        p_expected_team_key,
        p_yahoo_player_key,
        v_selected_at,
        p_initiated_by,
        CASE
            WHEN v_is_late_pick
            THEN 'atomic late-pick executor'
            ELSE 'atomic draft executor'
        END
    )
    ON CONFLICT (draft_key, pick_id)
    DO NOTHING;

    GET DIAGNOSTICS
        v_inserted = ROW_COUNT;

    IF v_inserted <> 1 THEN
        RAISE EXCEPTION
            'Pick % was claimed concurrently.',
            p_expected_pick_id;
    END IF;

    v_event_id =
        pg_catalog.gen_random_uuid()::text;

    v_log_entry =
        jsonb_build_object(
            'event_id',
                v_event_id,
            'pick_id',
                p_expected_pick_id,
            'owner_team_key',
                p_expected_team_key,
            'player_key',
                p_yahoo_player_key,
            'player_name',
                v_player_name,
            'primary_position',
                v_primary_position,
            'late_pick',
                v_is_late_pick,
            'ts_iso',
                v_selected_ts_iso
        );

    v_state =
        jsonb_set(
            v_state,
            ARRAY['pick_log'],
            COALESCE(
                v_state -> 'pick_log',
                '[]'::jsonb
            )
            ||
            jsonb_build_array(
                v_log_entry
            ),
            true
        );

    IF v_is_late_pick THEN
        UPDATE nfhl.draft_expired_pick
           SET resolved_at_utc = v_selected_at,
               resolved_by = p_initiated_by,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key
           AND pick_id = p_expected_pick_id
           AND resolved_at_utc IS NULL;

        GET DIAGNOSTICS
            v_inserted = ROW_COUNT;

        IF v_inserted <> 1 THEN
            RAISE EXCEPTION
                'Expired/open pick % was resolved concurrently.',
                p_expected_pick_id;
        END IF;

        -- Absolutely no clock rewind on a late pick.
        v_next_pick_id =
            v_current_pick_id;

    ELSE
        SELECT
            n.pick_id,
            n.team_key
        INTO
            v_next_pick_id,
            v_next_team_key
        FROM nfhl.next_open_pick_after(
            p_draft_key,
            v_current_pick_id
        ) n;

        IF v_next_pick_id IS NOT NULL THEN
            v_state =
                jsonb_set(
                    v_state,
                    ARRAY['clock', 'current_pick_id'],
                    to_jsonb(v_next_pick_id),
                    true
                );

            IF v_auto_advance THEN
                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'pick_started_ts_iso'],
                        to_jsonb(v_selected_ts_iso),
                        true
                    );

                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'pick_paused_ts_iso'],
                        'null'::jsonb,
                        true
                    );

                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'elapsed_paused_seconds'],
                        to_jsonb(0),
                        true
                    );

                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'is_running'],
                        to_jsonb(true),
                        true
                    );

                INSERT INTO nfhl.draft_clock_event (
                    draft_key,
                    pick_id,
                    event_type,
                    team_key,
                    occurred_at_utc
                )
                VALUES (
                    p_draft_key,
                    v_next_pick_id,
                    'ON_CLOCK',
                    v_next_team_key,
                    v_selected_at
                )
                ON CONFLICT DO NOTHING;

            ELSE
                -- If automatic clock advancement is disabled, move
                -- the current-pick pointer but leave the clock stopped.
                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'is_running'],
                        to_jsonb(false),
                        true
                    );

                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'pick_started_ts_iso'],
                        'null'::jsonb,
                        true
                    );

                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'pick_paused_ts_iso'],
                        'null'::jsonb,
                        true
                    );

                v_state =
                    jsonb_set(
                        v_state,
                        ARRAY['clock', 'elapsed_paused_seconds'],
                        to_jsonb(0),
                        true
                    );
            END IF;

        ELSE
            v_state =
                jsonb_set(
                    v_state,
                    ARRAY['clock', 'is_running'],
                    to_jsonb(false),
                    true
                );

            v_state =
                jsonb_set(
                    v_state,
                    ARRAY['clock', 'pick_started_ts_iso'],
                    'null'::jsonb,
                    true
                );

            v_state =
                jsonb_set(
                    v_state,
                    ARRAY['clock', 'pick_paused_ts_iso'],
                    'null'::jsonb,
                    true
                );

            v_state =
                jsonb_set(
                    v_state,
                    ARRAY['clock', 'elapsed_paused_seconds'],
                    to_jsonb(0),
                    true
                );
        END IF;
    END IF;

    v_new_sha =
        encode(
            public.digest(
                pg_catalog.convert_to(
                    v_state::text,
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        );

    UPDATE nfhl.draft_state
       SET state_json = v_state,
           state_sha256 = v_new_sha,
           updated_at_utc = now()
     WHERE draft_key = p_draft_key;

    RETURN QUERY
    SELECT
        'EXECUTED'::text,
        p_expected_pick_id,
        p_expected_team_key,
        p_yahoo_player_key,
        v_is_late_pick,
        v_next_pick_id,
        v_new_sha;
END;
$function$;

CREATE OR REPLACE FUNCTION nfhl.execute_armed_autopick(p_draft_key text)
 RETURNS TABLE(result_status text, executed_pick_id text, selecting_team_key text, selected_player_key text, selected_queue_rank integer, late_pick boolean, next_pick_id text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'nfhl', 'public'
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;
    v_draft_status text;

    v_state jsonb;
    v_current_pick_id text;
    v_team_key text;

    v_enabled boolean;
    v_armed_pick_id text;

    -- Auto-Pick grace-period clock state.
    v_clock_running boolean;
    v_clock_started_text text;
    v_clock_paused_text text;
    v_clock_elapsed_paused bigint;
    v_clock_started_at timestamptz;
    v_autopick_elapsed bigint;
    v_autopick_grace_seconds integer := 180;

    v_candidate_player_key text;
    v_candidate_rank integer;

    v_queue record;
    v_submit record;
BEGIN
    -- Same draft-wide lock used by the canonical NFHL engine.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT
        d.league_key,
        d.season_year,
        d.status
    INTO
        v_league_key,
        v_season_year,
        v_draft_status
    FROM nfhl.draft d
    WHERE d.draft_key = p_draft_key;

    IF NOT FOUND
       OR UPPER(
            COALESCE(
                v_draft_status,
                ''
            )
       ) <> 'ACTIVE'
    THEN
        RETURN QUERY
        SELECT
            'NO_ACTIVE_DRAFT'::text,
            NULL::text,
            NULL::text,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    /*
     * Process any clock transition that is already due before
     * considering unattended execution.
     */
    PERFORM 1
    FROM nfhl.process_draft_clock(
        p_draft_key
    );

    SELECT s.state_json
      INTO v_state
    FROM nfhl.draft_state s
    WHERE s.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT
            'NO_DRAFT_STATE'::text,
            NULL::text,
            NULL::text,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    v_current_pick_id =
        v_state
        -> 'clock'
        ->> 'current_pick_id';

    SELECT p.current_owner_team_key
      INTO v_team_key
    FROM nfhl.draft_pick p
    WHERE p.draft_key = p_draft_key
      AND p.pick_id = v_current_pick_id;

    IF NOT FOUND
       OR v_team_key IS NULL
    THEN
        RETURN QUERY
        SELECT
            'NO_CURRENT_PICK'::text,
            v_current_pick_id,
            NULL::text,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    SELECT
        c.enabled,
        c.armed_pick_id
    INTO
        v_enabled,
        v_armed_pick_id
    FROM nfhl.draft_autopick_control c
    WHERE c.draft_key = p_draft_key
      AND c.team_key = v_team_key
    FOR UPDATE;

    IF NOT FOUND
       OR NOT COALESCE(
            v_enabled,
            false
       )
    THEN
        RETURN QUERY
        SELECT
            'NO_ARMED_QUEUE'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    IF v_armed_pick_id
       IS DISTINCT FROM
       v_current_pick_id
    THEN
        RETURN QUERY
        SELECT
            'ARMED_FOR_DIFFERENT_PICK'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    /*
     * ------------------------------------------------------------
     * 3-MINUTE ACTIVE-CLOCK GRACE PERIOD
     * ------------------------------------------------------------
     *
     * Match NFHL's normal clock accounting:
     *
     *     elapsed_paused_seconds
     *       + current running segment
     *
     * A paused/stopped clock cannot consume or complete Auto-Pick
     * grace time.
     */

    v_clock_running =
        COALESCE(
            (
                v_state
                -> 'clock'
                ->> 'is_running'
            )::boolean,
            false
        );

    v_clock_started_text =
        NULLIF(
            BTRIM(
                COALESCE(
                    v_state
                    -> 'clock'
                    ->> 'pick_started_ts_iso',
                    ''
                )
            ),
            ''
        );

    v_clock_paused_text =
        NULLIF(
            BTRIM(
                COALESCE(
                    v_state
                    -> 'clock'
                    ->> 'pick_paused_ts_iso',
                    ''
                )
            ),
            ''
        );

    v_clock_elapsed_paused =
        GREATEST(
            0,
            COALESCE(
                (
                    v_state
                    -> 'clock'
                    ->> 'elapsed_paused_seconds'
                )::bigint,
                0
            )
        );

    IF (
        NOT v_clock_running
        OR v_clock_started_text IS NULL
        OR v_clock_paused_text IS NOT NULL
    ) THEN
        RETURN QUERY
        SELECT
            'AUTOPICK_CLOCK_NOT_RUNNING'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    v_clock_started_at =
        v_clock_started_text::timestamptz;

    v_autopick_elapsed =
        GREATEST(
            0,
            v_clock_elapsed_paused
            +
            FLOOR(
                EXTRACT(
                    EPOCH FROM (
                        clock_timestamp()
                        - v_clock_started_at
                    )
                )
            )::bigint
        );

    IF (
        v_autopick_elapsed
        < v_autopick_grace_seconds
    ) THEN
        RETURN QUERY
        SELECT
            'AUTOPICK_GRACE_PERIOD'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    -- ------------------------------------------------------------
    -- EXISTING NFHL QUEUE / ONE-SHOT EXECUTION
    -- ------------------------------------------------------------

    FOR v_queue IN
        SELECT
            q.queue_rank,
            q.yahoo_player_key
        FROM nfhl.draft_autopick_queue q
        WHERE q.draft_key = p_draft_key
          AND q.team_key = v_team_key
        ORDER BY q.queue_rank
    LOOP
        -- Already selected by another team.
        IF EXISTS (
            SELECT 1
            FROM nfhl.draft_selection ds
            WHERE ds.draft_key = p_draft_key
              AND ds.yahoo_player_key =
                  v_queue.yahoo_player_key
        ) THEN
            CONTINUE;
        END IF;

        -- Must exist in this NFHL league/season universe.
        IF NOT EXISTS (
            SELECT 1
            FROM nfhl.player_universe pu
            WHERE pu.league_key = v_league_key
              AND pu.season_year = v_season_year
              AND pu.yahoo_player_key =
                  v_queue.yahoo_player_key
              AND pu.is_yahoo_current
        ) THEN
            CONTINUE;
        END IF;

        v_candidate_player_key =
            v_queue.yahoo_player_key;

        v_candidate_rank =
            v_queue.queue_rank;

        EXIT;
    END LOOP;

    IF v_candidate_player_key IS NULL THEN
        RETURN QUERY
        SELECT
            'NO_LEGAL_CANDIDATE'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::boolean,
            NULL::text;

        RETURN;
    END IF;

    SELECT *
      INTO v_submit
    FROM nfhl.submit_draft_pick_atomic(
        p_draft_key,
        v_current_pick_id,
        v_team_key,
        v_candidate_player_key,
        'autopick_executor'
    );

    -- NFHL Auto-Pick remains one-shot.
    UPDATE nfhl.draft_autopick_control
       SET enabled = false,
           armed_pick_id = NULL,
           updated_at_utc = now(),
           updated_by = 'autopick_executor'
     WHERE draft_key = p_draft_key
       AND team_key = v_team_key;

    INSERT INTO nfhl.draft_autopick_audit (
        draft_key,
        team_key,
        pick_id,
        result,
        selected_player_key,
        selected_queue_rank,
        detail,
        initiated_by
    )
    VALUES (
        p_draft_key,
        v_team_key,
        v_submit.executed_pick_id,
        'EXECUTED',
        v_submit.selected_player_key,
        v_candidate_rank,
        'Unattended atomic NFHL Auto-Pick after '
            || v_autopick_grace_seconds
            || ' active clock seconds',
        'autopick_executor'
    );

    RETURN QUERY
    SELECT
        v_submit.result_status,
        v_submit.executed_pick_id,
        v_submit.selecting_team_key,
        v_submit.selected_player_key,
        v_candidate_rank,
        v_submit.late_pick,
        v_submit.next_pick_id;
END;
$function$;
