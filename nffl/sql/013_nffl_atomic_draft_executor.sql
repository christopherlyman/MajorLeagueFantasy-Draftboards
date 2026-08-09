-- NFFL atomic draft executor
--
-- Goal:
--   ONE canonical database operation for making a real draft pick.
--
-- Eventually used by:
--   * desktop DraftBoard
--   * mobile DraftBoard
--   * commissioner queue
--   * unattended Discord-bot auto-pick
--
-- Safety:
--   * serializes pick execution per draft
--   * verifies exact current pick and owner
--   * refuses already-used picks
--   * refuses already-drafted players
--   * refuses Contract / FT players
--   * performs QO / POACH / FA classification from canonical DB state
--   * records selection + state + clock in one transaction
--   * never DELETEs an existing draft selection
--
-- The bot will NOT receive EXECUTE permission until later testing passes.

BEGIN;

CREATE OR REPLACE FUNCTION nffl.classify_live_pick_kind_db(
    p_league_key text,
    p_season_year integer,
    p_draft_key text,
    p_pick_id text,
    p_selecting_team_key text,
    p_yahoo_player_key text
)
RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog, nffl, public
AS $function$
DECLARE
    v_round_number integer;
    v_max_qo_round integer;
    v_holder_team text;
    v_holder_level integer;
BEGIN
    SELECT p.round_number
      INTO v_round_number
    FROM nffl.draft_pick p
    WHERE p.draft_key = p_draft_key
      AND p.pick_id = p_pick_id;

    IF v_round_number IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(MAX(q.qo_level), 0)
      INTO v_max_qo_round
    FROM public.qualifying_offer q
    WHERE q.league_key = p_league_key
      AND q.season_year = p_season_year;

    IF NOT (
        v_round_number BETWEEN 1 AND v_max_qo_round
    ) THEN
        RETURN 'FA';
    END IF;

    SELECT
        q.team_key,
        q.qo_level
      INTO
        v_holder_team,
        v_holder_level
    FROM nffl.current_qo_placeholders(
        p_league_key,
        p_season_year,
        p_draft_key
    ) q
    WHERE q.yahoo_player_key = p_yahoo_player_key
    ORDER BY q.qo_level
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN 'FA';
    END IF;

    IF (
        v_holder_team = p_selecting_team_key
        AND v_holder_level >= v_round_number
    ) THEN
        RETURN 'QO';
    END IF;

    IF (
        v_holder_team <> p_selecting_team_key
        AND v_holder_level > v_round_number
    ) THEN
        RETURN 'POACH';
    END IF;

    -- Reserved QO, but not legally selectable at this pick.
    RETURN NULL;
END;
$function$;


CREATE OR REPLACE FUNCTION nffl.submit_draft_pick_atomic(
    p_draft_key text,
    p_league_key text,
    p_season_year integer,
    p_expected_pick_id text,
    p_expected_team_key text,
    p_yahoo_player_key text,
    p_initiated_by text DEFAULT 'draftboard'
)
RETURNS TABLE(
    result_status text,
    executed_pick_id text,
    selecting_team_key text,
    selected_player_key text,
    selected_pick_kind text,
    next_pick_id text,
    new_state_sha256 text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, nffl, public
AS $function$
DECLARE
    v_state jsonb;
    v_current_pick_id text;
    v_state_owner_team_key text;

    v_owner_team_key text;
    v_round_number integer;
    v_slot_number integer;

    v_pick_kind text;

    v_player_json jsonb;
    v_player_name text;
    v_primary_position text;

    v_selected_at timestamptz;
    v_selected_ts_iso text;

    v_next_pick_id text;
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

    -- One pick executor at a time for this draft.
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
        RAISE EXCEPTION
            'Draft state not found for %.',
            p_draft_key;
    END IF;

    v_current_pick_id =
        v_state -> 'clock' ->> 'current_pick_id';

    IF v_current_pick_id IS DISTINCT FROM p_expected_pick_id THEN
        RAISE EXCEPTION
            'Current pick moved. Expected %, found %.',
            p_expected_pick_id,
            v_current_pick_id;
    END IF;

    IF (
        v_state -> 'picks' -> p_expected_pick_id
    ) IS NULL THEN
        RAISE EXCEPTION
            'Pick % missing from saved DraftBoard state.',
            p_expected_pick_id;
    END IF;

    IF (
        v_state
        -> 'picks'
        -> p_expected_pick_id
        ->> 'selected_ts_iso'
    ) IS NOT NULL THEN
        RAISE EXCEPTION
            'Pick % is already complete in saved state.',
            p_expected_pick_id;
    END IF;

    v_state_owner_team_key =
        v_state
        -> 'picks'
        -> p_expected_pick_id
        ->> 'owner_team_key';

    SELECT
        p.current_owner_team_key,
        p.round_number,
        p.slot_number
      INTO
        v_owner_team_key,
        v_round_number,
        v_slot_number
    FROM nffl.draft_pick p
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

    IF v_state_owner_team_key IS DISTINCT FROM p_expected_team_key THEN
        RAISE EXCEPTION
            'Saved-state owner does not match canonical pick owner.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM nffl.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.pick_id = p_expected_pick_id
    ) THEN
        RAISE EXCEPTION
            'Pick % already has a real selection.',
            p_expected_pick_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM nffl.draft_selection ds
        WHERE ds.draft_key = p_draft_key
          AND ds.yahoo_player_key = p_yahoo_player_key
    ) THEN
        RAISE EXCEPTION
            'Player % is already drafted.',
            p_yahoo_player_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM nffl.player_universe pu
        WHERE pu.league_key = p_league_key
          AND pu.season_year = p_season_year
          AND pu.yahoo_player_key = p_yahoo_player_key
    ) THEN
        RAISE EXCEPTION
            'Player % is not in the active player universe.',
            p_yahoo_player_key;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM nffl.contract c
        WHERE c.league_key = p_league_key
          AND c.season_year = p_season_year
          AND c.yahoo_player_key = p_yahoo_player_key
          AND c.status = 'active'

        UNION ALL

        SELECT 1
        FROM nffl.offseason_keeper_decision d
        WHERE d.league_key = p_league_key
          AND d.season_year = p_season_year
          AND d.yahoo_player_key = p_yahoo_player_key
          AND d.decision_type = 'FT'
    ) THEN
        RAISE EXCEPTION
            'Contract/FT player % is not draftable.',
            p_yahoo_player_key;
    END IF;

    v_pick_kind =
        nffl.classify_live_pick_kind_db(
            p_league_key,
            p_season_year,
            p_draft_key,
            p_expected_pick_id,
            p_expected_team_key,
            p_yahoo_player_key
        );

    IF v_pick_kind IS NULL THEN
        RAISE EXCEPTION
            'Player % is not legally selectable at pick %.',
            p_yahoo_player_key,
            p_expected_pick_id;
    END IF;

    v_player_json =
        v_state
        -> 'players'
        -> p_yahoo_player_key;

    IF (
        v_player_json IS NULL
        OR jsonb_typeof(v_player_json) <> 'object'
    ) THEN
        RAISE EXCEPTION
            'Player % missing from saved DraftBoard player state.',
            p_yahoo_player_key;
    END IF;

    v_player_name =
        NULLIF(
            v_player_json ->> 'name',
            ''
        );

    -- Match Player.primary_position exactly:
    -- the first entry in the saved positions list.
    v_primary_position =
        NULLIF(
            v_player_json
            -> 'positions'
            ->> 0,
            ''
        );

    IF v_player_name IS NULL
       OR v_primary_position IS NULL
    THEN
        RAISE EXCEPTION
            'Player % is missing name/position in saved state.',
            p_yahoo_player_key;
    END IF;

    v_selected_at = clock_timestamp();

    v_selected_ts_iso =
        to_char(
            v_selected_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US'
        );

    INSERT INTO nffl.draft_selection (
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
        p_expected_team_key,
        p_yahoo_player_key,
        v_pick_kind,
        v_selected_at,
        p_initiated_by,
        'atomic draft executor'
    )
    ON CONFLICT (draft_key, pick_id)
    DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    IF v_inserted <> 1 THEN
        RAISE EXCEPTION
            'Pick % was claimed concurrently.',
            p_expected_pick_id;
    END IF;

    -- Replace any QO placeholder with the actual selection.
    v_state = jsonb_set(
        v_state,
        ARRAY[
            'picks',
            p_expected_pick_id,
            'selected_player_key'
        ],
        to_jsonb(p_yahoo_player_key),
        true
    );

    v_state = jsonb_set(
        v_state,
        ARRAY[
            'picks',
            p_expected_pick_id,
            'selected_ts_iso'
        ],
        to_jsonb(v_selected_ts_iso),
        true
    );

    v_event_id =
        pg_catalog.gen_random_uuid()::text;

    v_log_entry =
        jsonb_build_object(
            'event_id', v_event_id,
            'pick_id', p_expected_pick_id,
            'owner_team_key', p_expected_team_key,
            'player_key', p_yahoo_player_key,
            'player_name', v_player_name,
            'primary_position', v_primary_position,
            'pick_kind', v_pick_kind,
            'ts_iso', v_selected_ts_iso
        );

    v_state = jsonb_set(
        v_state,
        ARRAY['pick_log'],
        COALESCE(
            v_state -> 'pick_log',
            '[]'::jsonb
        )
        || jsonb_build_array(v_log_entry),
        true
    );

    -- Same occupancy rule used by the current DraftBoard:
    -- real selections + Contract/FT placeholders are occupied.
    -- QO placeholders remain pickable.
    SELECT v.pick_id
      INTO v_next_pick_id
    FROM nffl.v_draft_board_current v
    WHERE v.draft_key = p_draft_key
      AND (
            v.round_number > v_round_number
            OR (
                v.round_number = v_round_number
                AND v.slot_number > v_slot_number
            )
          )
      AND v.selected_at_utc IS NULL
      AND COALESCE(
            v.placeholder_source,
            ''
          ) NOT IN ('CONTRACT', 'FT')
    ORDER BY
        v.round_number,
        v.slot_number
    LIMIT 1;

    v_auto_advance =
        COALESCE(
            (
                v_state
                -> 'clock'
                ->> 'auto_advance'
            )::boolean,
            true
        );

    IF v_next_pick_id IS NOT NULL THEN
        v_state = jsonb_set(
            v_state,
            ARRAY['clock', 'current_pick_id'],
            to_jsonb(v_next_pick_id),
            true
        );

        IF v_auto_advance THEN
            v_state = jsonb_set(
                v_state,
                ARRAY['clock', 'pick_started_ts_iso'],
                to_jsonb(v_selected_ts_iso),
                true
            );

            v_state = jsonb_set(
                v_state,
                ARRAY['clock', 'pick_paused_ts_iso'],
                'null'::jsonb,
                true
            );

            v_state = jsonb_set(
                v_state,
                ARRAY['clock', 'elapsed_paused_seconds'],
                to_jsonb(0),
                true
            );

            v_state = jsonb_set(
                v_state,
                ARRAY['clock', 'is_running'],
                to_jsonb(true),
                true
            );
        END IF;
    ELSE
        v_state = jsonb_set(
            v_state,
            ARRAY['clock', 'is_running'],
            to_jsonb(false),
            true
        );

        v_state = jsonb_set(
            v_state,
            ARRAY['clock', 'pick_started_ts_iso'],
            'null'::jsonb,
            true
        );

        v_state = jsonb_set(
            v_state,
            ARRAY['clock', 'pick_paused_ts_iso'],
            'null'::jsonb,
            true
        );

        v_state = jsonb_set(
            v_state,
            ARRAY['clock', 'elapsed_paused_seconds'],
            to_jsonb(0),
            true
        );
    END IF;

    -- This SHA is the database change detector.
    -- The DraftBoard will later rewrite it using its normal
    -- pretty-JSON hash when it reloads/saves the state.
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

    UPDATE public.draftboard_state
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
        v_pick_kind,
        v_next_pick_id,
        v_new_sha;
END;
$function$;


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


REVOKE ALL
ON FUNCTION nffl.classify_live_pick_kind_db(
    text,
    integer,
    text,
    text,
    text,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION nffl.submit_draft_pick_atomic(
    text,
    text,
    integer,
    text,
    text,
    text,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION nffl.execute_armed_autopick(
    text,
    text,
    integer
)
FROM PUBLIC;

COMMIT;
