BEGIN;

-- ================================================================
-- NFHL CONFIGURABLE DRAFT CLOCK
-- ================================================================

CREATE FUNCTION nfhl.process_draft_clock(
    p_draft_key text
)
RETURNS TABLE(
    result_status text,
    active_pick_id text,
    elapsed_seconds bigint,
    events_created integer,
    expired_count integer,
    next_pick_id text,
    state_sha256 text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, nfhl, public
AS $function$
DECLARE
    v_state jsonb;
    v_state_sha text;
    v_draft_status text;

    v_seconds_per_pick integer;
    v_auto_advance boolean;
    v_weekends_count boolean;

    v_current_pick_id text;
    v_current_team_key text;
    v_round_number integer;
    v_slot_number integer;

    v_is_running boolean;
    v_started_text text;
    v_paused_text text;
    v_elapsed_paused bigint;
    v_started_at timestamptz;
    v_elapsed bigint;

    v_now timestamptz := clock_timestamp();
    v_deadline_at timestamptz;

    v_next_pick_id text;
    v_next_team_key text;

    v_reminder_code text;
    v_reminder_seconds_remaining integer;
    v_reminder_elapsed integer;
    v_reminder_event_type text;

    v_inserted integer;
    v_events_created integer := 0;
    v_expired_count integer := 0;

    v_new_sha text;
    v_last_status text := 'CLOCK_ACTIVE';
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION 'Missing draft key.';
    END IF;

    -- Same draft-wide transaction lock is used by clock,
    -- manual selection, and Auto-Pick.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    SELECT
        d.status,
        s.state_json,
        s.state_sha256
    INTO
        v_draft_status,
        v_state,
        v_state_sha
    FROM nfhl.draft d
    JOIN nfhl.draft_state s
      ON s.draft_key = d.draft_key
    WHERE d.draft_key = p_draft_key
    FOR UPDATE OF s;

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT
            'NO_DRAFT_STATE'::text,
            NULL::text,
            NULL::bigint,
            0,
            0,
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    IF UPPER(COALESCE(v_draft_status, '')) <> 'ACTIVE' THEN
        RETURN QUERY
        SELECT
            'NO_ACTIVE_DRAFT'::text,
            NULL::text,
            NULL::bigint,
            0,
            0,
            NULL::text,
            v_state_sha;
        RETURN;
    END IF;

    SELECT
        c.seconds_per_pick,
        c.auto_advance,
        c.weekends_count
    INTO
        v_seconds_per_pick,
        v_auto_advance,
        v_weekends_count
    FROM nfhl.draft_clock_config c
    WHERE c.draft_key = p_draft_key;

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT
            'NO_CLOCK_CONFIG'::text,
            NULL::text,
            NULL::bigint,
            0,
            0,
            NULL::text,
            v_state_sha;
        RETURN;
    END IF;

    /*
     * We deliberately do not silently pretend weekend exclusion
     * exists. If NFHL later chooses weekends_count=false, implement
     * calendar-aware elapsed-time arithmetic before enabling it.
     */
    IF NOT v_weekends_count THEN
        RAISE EXCEPTION
            'weekends_count=false is not yet implemented for NFHL clock processing.';
    END IF;

    IF NOT v_auto_advance THEN
        RETURN QUERY
        SELECT
            'AUTO_ADVANCE_DISABLED'::text,
            v_state -> 'clock' ->> 'current_pick_id',
            NULL::bigint,
            0,
            0,
            NULL::text,
            v_state_sha;
        RETURN;
    END IF;

    /*
     * A delayed worker may discover multiple expired picks.
     * Keep advancing using the same wall-clock v_now.
     */
    LOOP
        v_current_pick_id =
            NULLIF(
                BTRIM(
                    COALESCE(
                        v_state
                        -> 'clock'
                        ->> 'current_pick_id',
                        ''
                    )
                ),
                ''
            );

        IF v_current_pick_id IS NULL THEN
            RETURN QUERY
            SELECT
                'NO_CURRENT_PICK'::text,
                NULL::text,
                NULL::bigint,
                v_events_created,
                v_expired_count,
                NULL::text,
                v_state_sha;
            RETURN;
        END IF;

        v_is_running =
            COALESCE(
                (
                    v_state
                    -> 'clock'
                    ->> 'is_running'
                )::boolean,
                false
            );

        v_started_text =
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

        v_paused_text =
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

        v_elapsed_paused =
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

        IF v_paused_text IS NOT NULL THEN
            RETURN QUERY
            SELECT
                'CLOCK_PAUSED'::text,
                v_current_pick_id,
                v_elapsed_paused,
                v_events_created,
                v_expired_count,
                NULL::text,
                v_state_sha;
            RETURN;
        END IF;

        IF NOT v_is_running OR v_started_text IS NULL THEN
            RETURN QUERY
            SELECT
                'CLOCK_STOPPED'::text,
                v_current_pick_id,
                v_elapsed_paused,
                v_events_created,
                v_expired_count,
                NULL::text,
                v_state_sha;
            RETURN;
        END IF;

        -- NFHL stores these values as UTC wall-clock strings.
        v_started_at =
            v_started_text::timestamp
            AT TIME ZONE 'UTC';

        v_elapsed =
            GREATEST(
                0,
                v_elapsed_paused
                +
                FLOOR(
                    EXTRACT(
                        EPOCH FROM
                        (v_now - v_started_at)
                    )
                )::bigint
            );

        SELECT
            p.current_owner_team_key,
            p.round_number,
            p.slot_number
        INTO
            v_current_team_key,
            v_round_number,
            v_slot_number
        FROM nfhl.draft_pick p
        WHERE p.draft_key = p_draft_key
          AND p.pick_id = v_current_pick_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Current clock pick % does not exist.',
                v_current_pick_id;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM nfhl.draft_selection ds
            WHERE ds.draft_key = p_draft_key
              AND ds.pick_id = v_current_pick_id
        ) THEN
            RETURN QUERY
            SELECT
                'CURRENT_PICK_ALREADY_SELECTED'::text,
                v_current_pick_id,
                v_elapsed,
                v_events_created,
                v_expired_count,
                NULL::text,
                v_state_sha;
            RETURN;
        END IF;

        -- ------------------------------------------------------------
        -- CONFIGURABLE REMINDERS
        --
        -- Pick only the most recently crossed configured reminder.
        -- This prevents stale-message bursts after worker downtime.
        -- ------------------------------------------------------------

        IF v_elapsed < v_seconds_per_pick THEN
            v_inserted := 0;
            v_reminder_code := NULL;
            v_reminder_seconds_remaining := NULL;

            SELECT
                r.reminder_code,
                r.seconds_remaining
            INTO
                v_reminder_code,
                v_reminder_seconds_remaining
            FROM nfhl.draft_clock_reminder_config r
            WHERE r.draft_key = p_draft_key
              AND r.enabled
              AND r.seconds_remaining < v_seconds_per_pick
              AND v_elapsed >=
                  (
                      v_seconds_per_pick
                      - r.seconds_remaining
                  )
            ORDER BY
                r.seconds_remaining ASC
            LIMIT 1;

            IF FOUND THEN
                v_reminder_elapsed =
                    v_seconds_per_pick
                    - v_reminder_seconds_remaining;

                v_reminder_event_type =
                    'REMINDER:' || v_reminder_code;

                INSERT INTO nfhl.draft_clock_event (
                    draft_key,
                    pick_id,
                    event_type,
                    team_key,
                    occurred_at_utc
                )
                VALUES (
                    p_draft_key,
                    v_current_pick_id,
                    v_reminder_event_type,
                    v_current_team_key,
                    v_started_at
                    +
                    make_interval(
                        secs =>
                            GREATEST(
                                0,
                                v_reminder_elapsed
                                - v_elapsed_paused
                            )
                    )
                )
                ON CONFLICT DO NOTHING;

                GET DIAGNOSTICS
                    v_inserted = ROW_COUNT;
            END IF;

            v_events_created =
                v_events_created
                + COALESCE(v_inserted, 0);

            IF v_expired_count > 0 THEN
                v_last_status = 'EXPIRED_ADVANCED';
            ELSIF v_inserted = 1 THEN
                v_last_status = 'REMINDER_RECORDED';
            ELSE
                v_last_status = 'CLOCK_ACTIVE';
            END IF;

            RETURN QUERY
            SELECT
                v_last_status,
                v_current_pick_id,
                v_elapsed,
                v_events_created,
                v_expired_count,
                CASE
                    WHEN v_expired_count > 0
                    THEN v_current_pick_id
                    ELSE NULL
                END,
                v_state_sha;

            RETURN;
        END IF;

        -- ------------------------------------------------------------
        -- EXPIRATION
        -- ------------------------------------------------------------

        v_deadline_at =
            v_started_at
            +
            make_interval(
                secs =>
                    GREATEST(
                        0,
                        v_seconds_per_pick
                        - v_elapsed_paused
                    )
            );

        INSERT INTO nfhl.draft_expired_pick (
            draft_key,
            pick_id,
            expired_at_utc
        )
        VALUES (
            p_draft_key,
            v_current_pick_id,
            v_deadline_at
        )
        ON CONFLICT (draft_key, pick_id)
        DO NOTHING;

        INSERT INTO nfhl.draft_clock_event (
            draft_key,
            pick_id,
            event_type,
            team_key,
            occurred_at_utc
        )
        VALUES (
            p_draft_key,
            v_current_pick_id,
            'EXPIRED',
            v_current_team_key,
            v_deadline_at
        )
        ON CONFLICT DO NOTHING;

        GET DIAGNOSTICS
            v_inserted = ROW_COUNT;

        v_events_created =
            v_events_created
            + COALESCE(v_inserted, 0);

        v_expired_count =
            v_expired_count + 1;

        v_last_status =
            'EXPIRED_ADVANCED';

        /*
         * Find only a later open slot.
         *
         * Earlier expired picks remain selectable, but never regain
         * the live clock.
         */
        SELECT
            v.pick_id,
            v.current_owner_team_key
        INTO
            v_next_pick_id,
            v_next_team_key
        FROM nfhl.v_draft_board_current v
        WHERE v.draft_key = p_draft_key
          AND (
                v.round_number > v_round_number
                OR (
                    v.round_number = v_round_number
                    AND v.slot_number > v_slot_number
                )
              )
          AND v.selected_at_utc IS NULL
        ORDER BY
            v.round_number,
            v.slot_number
        LIMIT 1;

        IF v_next_pick_id IS NULL THEN
            -- Final pick expired. It remains selectable as expired/open.
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

            v_state_sha = v_new_sha;

            RETURN QUERY
            SELECT
                'FINAL_PICK_EXPIRED'::text,
                v_current_pick_id,
                v_elapsed,
                v_events_created,
                v_expired_count,
                NULL::text,
                v_state_sha;

            RETURN;
        END IF;

        /*
         * The next manager's clock begins at the exact previous
         * deadline, not at the later worker-poll time.
         */
        v_state =
            jsonb_set(
                v_state,
                ARRAY['clock', 'current_pick_id'],
                to_jsonb(v_next_pick_id),
                true
            );

        v_state =
            jsonb_set(
                v_state,
                ARRAY['clock', 'pick_started_ts_iso'],
                to_jsonb(
                    to_char(
                        v_deadline_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US'
                    )
                ),
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
            v_deadline_at
        )
        ON CONFLICT DO NOTHING;

        GET DIAGNOSTICS
            v_inserted = ROW_COUNT;

        v_events_created =
            v_events_created
            + COALESCE(v_inserted, 0);

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

        v_state_sha = v_new_sha;

        -- Continue with the same v_now in case another full
        -- configured pick window has already elapsed.
    END LOOP;
END;
$function$;


-- ================================================================
-- NFHL ATOMIC MANUAL / LATE-PICK EXECUTOR
-- ================================================================

CREATE FUNCTION nfhl.submit_draft_pick_atomic(
    p_draft_key text,
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
    late_pick boolean,
    next_pick_id text,
    new_state_sha256 text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO pg_catalog, nfhl, public
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
      AND pu.yahoo_player_key = p_yahoo_player_key;

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
            v.pick_id,
            v.current_owner_team_key
        INTO
            v_next_pick_id,
            v_next_team_key
        FROM nfhl.v_draft_board_current v
        WHERE v.draft_key = p_draft_key
          AND (
                v.round_number > v_round_number
                OR (
                    v.round_number = v_round_number
                    AND v.slot_number > v_slot_number
                )
              )
          AND v.selected_at_utc IS NULL
        ORDER BY
            v.round_number,
            v.slot_number
        LIMIT 1;

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


-- ================================================================
-- NFHL ONE-SHOT AUTO-PICK
-- ================================================================

CREATE FUNCTION nfhl.execute_armed_autopick(
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
SECURITY DEFINER
SET search_path TO pg_catalog, nfhl, public
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

    v_candidate_player_key text;
    v_candidate_rank integer;

    v_queue record;
    v_submit record;
BEGIN
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
       OR UPPER(COALESCE(v_draft_status, '')) <> 'ACTIVE'
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
     * Ensure a just-expired manager cannot Auto-Pick before the
     * clock transition is recognized.
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
       OR NOT COALESCE(v_enabled, false)
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

    IF v_armed_pick_id IS DISTINCT FROM v_current_pick_id THEN
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

    -- One-shot behavior is intrinsic to NFHL.
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
        'Unattended atomic NFHL Auto-Pick',
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
