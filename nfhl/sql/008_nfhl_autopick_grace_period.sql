-- NFHL unattended Auto-Pick grace period
--
-- Port the proven NFFL behavior:
--   * Auto-Pick waits 180 ACTIVE clock seconds after the manager
--     comes on the clock.
--   * Paused time does not consume the grace period.
--   * Auto-Pick cannot execute while the clock is paused/stopped.
--
-- This migration changes only execution timing. Existing NFHL:
--   * exact-pick arming
--   * ranked queue
--   * availability checks
--   * atomic submission
--   * one-shot disable
--   * audit behavior
-- remain intact.

BEGIN;

CREATE OR REPLACE FUNCTION nfhl.execute_armed_autopick(
    p_draft_key text
)
RETURNS TABLE(
    result_status text,
    executed_pick_id text,
    selecting_team_key text,
    selected_player_key text,
    selected_queue_rank integer,
    late_pick boolean,
    next_pick_id text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, nfhl, public
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

COMMIT;
