-- NFFL unattended Auto-Pick grace period
--
-- Prevent an armed Auto-Pick from executing immediately when its
-- manager first comes on the clock. The executor waits for 180
-- active clock seconds, allowing the manager to revise or disable
-- Auto-Pick and allowing the ON_CLOCK event to be announced first.

BEGIN;

CREATE OR REPLACE FUNCTION nffl.execute_armed_autopick(
    p_draft_key text,
    p_league_key text,
    p_season_year integer
)
RETURNS TABLE(
    result_status text,
    executed_pick_id text,
    selecting_team_key text,
    selected_player_key text,
    selected_queue_rank integer,
    selected_pick_kind text,
    next_pick_id text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, nffl, public
AS $function$
DECLARE
    v_state jsonb;
    v_current_pick_id text;
    v_team_key text;

    v_enabled boolean;
    v_armed_pick_id text;

    v_clock_running boolean;
    v_clock_started_text text;
    v_clock_paused_text text;
    v_clock_elapsed_paused bigint;
    v_clock_started_at timestamptz;
    v_autopick_elapsed bigint;
    v_autopick_grace_seconds integer := 180;

    v_candidate_player_key text;
    v_candidate_rank integer;
    v_candidate_kind text;

    v_queue record;

    v_submit_status text;
    v_submit_pick_id text;
    v_submit_team_key text;
    v_submit_player_key text;
    v_submit_kind text;
    v_submit_next_pick_id text;
    v_submit_sha text;
BEGIN
    -- Same draft-wide lock used by the canonical executor.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT s.state_json
      INTO v_state
    FROM public.draftboard_state s
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
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    v_current_pick_id =
        v_state -> 'clock' ->> 'current_pick_id';

    SELECT p.current_owner_team_key
      INTO v_team_key
    FROM nffl.draft_pick p
    WHERE p.draft_key = p_draft_key
      AND p.pick_id = v_current_pick_id;

    IF NOT FOUND OR v_team_key IS NULL THEN
        RETURN QUERY
        SELECT
            'NO_CURRENT_PICK'::text,
            v_current_pick_id,
            NULL::text,
            NULL::text,
            NULL::integer,
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    SELECT
        c.enabled,
        c.armed_pick_id
      INTO
        v_enabled,
        v_armed_pick_id
    FROM nffl.draft_autopick_control c
    WHERE c.draft_key = p_draft_key
      AND c.team_key = v_team_key
    FOR UPDATE;

    IF NOT FOUND OR NOT COALESCE(v_enabled, false) THEN
        RETURN QUERY
        SELECT
            'NO_ARMED_QUEUE'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    IF v_armed_pick_id IS DISTINCT FROM v_current_pick_id THEN
        RETURN QUERY
        SELECT
            'ARMED_FOR_DIFFERENT_PICK'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    /*
     * Give the newly on-clock manager a short opportunity to
     * change or disable Auto-Pick before unattended execution.
     *
     * Use the same active-clock accounting as process_draft_clock:
     * accumulated paused time plus the current running segment.
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
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    v_clock_started_at =
        v_clock_started_text::timestamp
        AT TIME ZONE 'UTC';

    v_autopick_elapsed =
        GREATEST(
            0,
            v_clock_elapsed_paused
            +
            FLOOR(
                EXTRACT(
                    EPOCH FROM
                    (
                        clock_timestamp()
                        - v_clock_started_at
                    )
                )
            )::bigint
        );

    IF v_autopick_elapsed < v_autopick_grace_seconds THEN
        RETURN QUERY
        SELECT
            'AUTOPICK_GRACE_PERIOD'::text,
            v_current_pick_id,
            v_team_key,
            NULL::text,
            NULL::integer,
            NULL::text,
            NULL::text;
        RETURN;
    END IF;
    FOR v_queue IN
        SELECT
            q.queue_rank,
            q.yahoo_player_key
        FROM nffl.draft_autopick_queue q
        WHERE q.draft_key = p_draft_key
          AND q.team_key = v_team_key
        ORDER BY q.queue_rank
    LOOP
        IF EXISTS (
            SELECT 1
            FROM nffl.draft_selection ds
            WHERE ds.draft_key = p_draft_key
              AND ds.yahoo_player_key =
                  v_queue.yahoo_player_key
        ) THEN
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM nffl.player_universe pu
            WHERE pu.league_key = p_league_key
              AND pu.season_year = p_season_year
              AND pu.yahoo_player_key =
                  v_queue.yahoo_player_key
        ) THEN
            CONTINUE;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM nffl.contract c
            WHERE c.league_key = p_league_key
              AND c.season_year = p_season_year
              AND c.yahoo_player_key =
                  v_queue.yahoo_player_key
              AND c.status = 'active'

            UNION ALL

            SELECT 1
            FROM nffl.offseason_keeper_decision d
            WHERE d.league_key = p_league_key
              AND d.season_year = p_season_year
              AND d.yahoo_player_key =
                  v_queue.yahoo_player_key
              AND d.decision_type = 'FT'
        ) THEN
            CONTINUE;
        END IF;

        v_candidate_kind =
            nffl.classify_live_pick_kind_db(
                p_league_key,
                p_season_year,
                p_draft_key,
                v_current_pick_id,
                v_team_key,
                v_queue.yahoo_player_key
            );

        IF v_candidate_kind IS NULL THEN
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
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    SELECT
        s.result_status,
        s.executed_pick_id,
        s.selecting_team_key,
        s.selected_player_key,
        s.selected_pick_kind,
        s.next_pick_id,
        s.new_state_sha256
      INTO
        v_submit_status,
        v_submit_pick_id,
        v_submit_team_key,
        v_submit_player_key,
        v_submit_kind,
        v_submit_next_pick_id,
        v_submit_sha
    FROM nffl.submit_draft_pick_atomic(
        p_draft_key,
        p_league_key,
        p_season_year,
        v_current_pick_id,
        v_team_key,
        v_candidate_player_key,
        'autopick_executor'
    ) s;

    UPDATE nffl.draft_autopick_control
       SET enabled = false,
           armed_pick_id = NULL,
           updated_at_utc = now(),
           updated_by = 'autopick_executor'
     WHERE draft_key = p_draft_key
       AND team_key = v_team_key;

    INSERT INTO nffl.draft_autopick_audit (
        draft_key,
        team_key,
        pick_id,
        result,
        selected_player_key,
        selected_queue_rank,
        pick_kind,
        detail,
        initiated_by
    )
    VALUES (
        p_draft_key,
        v_team_key,
        v_submit_pick_id,
        'EXECUTED',
        v_submit_player_key,
        v_candidate_rank,
        v_submit_kind,
        'Unattended atomic auto-pick',
        'autopick_executor'
    );

    RETURN QUERY
    SELECT
        v_submit_status,
        v_submit_pick_id,
        v_submit_team_key,
        v_submit_player_key,
        v_candidate_rank,
        v_submit_kind,
        v_submit_next_pick_id;
END;
$function$;

COMMIT;
