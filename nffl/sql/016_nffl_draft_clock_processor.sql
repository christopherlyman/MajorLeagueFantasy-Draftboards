BEGIN;

CREATE OR REPLACE FUNCTION nffl.process_draft_clock(
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
SET search_path TO 'pg_catalog', 'nffl', 'public'
AS $function$
DECLARE
    v_state jsonb;
    v_state_sha text;
    v_draft_status text;

    v_current_pick_id text;
    v_current_team_key text;
    v_round_number integer;
    v_slot_number integer;

    v_seconds_per_pick integer;
    v_auto_advance boolean;
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

    v_inserted integer;
    v_events_created integer := 0;
    v_expired_count integer := 0;

    v_new_sha text;
    v_last_status text := 'CLOCK_ACTIVE';
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION 'Missing draft key.';
    END IF;

    -- Serialize clock transitions with manual picks and Auto-Picks.
    PERFORM pg_advisory_xact_lock(
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
    FROM nffl.draft d
    JOIN public.draftboard_state s
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

    IF LOWER(COALESCE(v_draft_status, '')) <> 'active' THEN
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

    /*
     * A delayed poll may discover that more than one 24-hour deadline
     * has passed. Loop forward deterministically so each expired pick
     * remains open and the active clock lands on the correct later pick.
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

        v_seconds_per_pick =
            COALESCE(
                (
                    v_state
                    -> 'clock'
                    ->> 'seconds_per_pick'
                )::integer,
                86400
            );

        -- This feature is intentionally limited to the 24-hour format.
        IF v_seconds_per_pick <> 86400 THEN
            RETURN QUERY
            SELECT
                'NOT_24_HOUR_CLOCK'::text,
                v_current_pick_id,
                NULL::bigint,
                v_events_created,
                v_expired_count,
                NULL::text,
                v_state_sha;
            RETURN;
        END IF;

        v_auto_advance =
            COALESCE(
                (
                    v_state
                    -> 'clock'
                    ->> 'auto_advance'
                )::boolean,
                true
            );

        IF NOT v_auto_advance THEN
            RETURN QUERY
            SELECT
                'AUTO_ADVANCE_DISABLED'::text,
                v_current_pick_id,
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

        -- A commissioner pause freezes all reminders and expiration.
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

        /*
         * DraftBoard stores these ISO values as UTC wall-clock strings
         * without an offset. Interpret them explicitly as UTC.
         */
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
        FROM nffl.draft_pick p
        WHERE p.draft_key = p_draft_key
          AND p.pick_id = v_current_pick_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Current clock pick % does not exist in draft_pick.',
                v_current_pick_id;
        END IF;

        /*
         * If the pick has somehow already been selected, do not invent
         * a timeout transition. The normal atomic pick executor owns
         * selection-driven clock advancement.
         */
        IF EXISTS (
            SELECT 1
            FROM nffl.draft_selection ds
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

        /*
         * Reminder behavior:
         *
         * Normal polling produces:
         *   12h elapsed -> REMINDER_12H
         *   18h elapsed -> REMINDER_6H
         *   23h elapsed -> REMINDER_1H
         *
         * If the bot was offline across multiple thresholds, create
         * only the most current reminder rather than sending stale
         * reminders in a burst.
         */
        IF v_elapsed < 86400 THEN
            v_inserted := 0;

            IF v_elapsed >= 82800 THEN
                INSERT INTO nffl.draft_clock_event (
                    draft_key,
                    pick_id,
                    event_type,
                    team_key,
                    occurred_at_utc
                )
                VALUES (
                    p_draft_key,
                    v_current_pick_id,
                    'REMINDER_1H',
                    v_current_team_key,
                    v_started_at
                    + make_interval(
                        secs => GREATEST(
                            0,
                            82800 - v_elapsed_paused
                        )
                    )
                )
                ON CONFLICT DO NOTHING;

                GET DIAGNOSTICS v_inserted = ROW_COUNT;

            ELSIF v_elapsed >= 64800 THEN
                INSERT INTO nffl.draft_clock_event (
                    draft_key,
                    pick_id,
                    event_type,
                    team_key,
                    occurred_at_utc
                )
                VALUES (
                    p_draft_key,
                    v_current_pick_id,
                    'REMINDER_6H',
                    v_current_team_key,
                    v_started_at
                    + make_interval(
                        secs => GREATEST(
                            0,
                            64800 - v_elapsed_paused
                        )
                    )
                )
                ON CONFLICT DO NOTHING;

                GET DIAGNOSTICS v_inserted = ROW_COUNT;

            ELSIF v_elapsed >= 43200 THEN
                INSERT INTO nffl.draft_clock_event (
                    draft_key,
                    pick_id,
                    event_type,
                    team_key,
                    occurred_at_utc
                )
                VALUES (
                    p_draft_key,
                    v_current_pick_id,
                    'REMINDER_12H',
                    v_current_team_key,
                    v_started_at
                    + make_interval(
                        secs => GREATEST(
                            0,
                            43200 - v_elapsed_paused
                        )
                    )
                )
                ON CONFLICT DO NOTHING;

                GET DIAGNOSTICS v_inserted = ROW_COUNT;
            END IF;

            v_events_created =
                v_events_created + COALESCE(v_inserted, 0);

            IF v_expired_count > 0 THEN
                -- This invocation expired one or more earlier picks and
                -- has now landed on the current active pick.
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

        /*
         * Exact wall-clock expiration time for this running segment.
         *
         * Example after a pause:
         *   already accumulated = 10h
         *   current running segment needs 14h
         *   deadline = current segment start + 14h
         */
        v_deadline_at =
            v_started_at
            +
            make_interval(
                secs => GREATEST(
                    0,
                    86400 - v_elapsed_paused
                )
            );

        INSERT INTO nffl.draft_expired_pick (
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

        INSERT INTO nffl.draft_clock_event (
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

        GET DIAGNOSTICS v_inserted = ROW_COUNT;
        v_events_created =
            v_events_created + COALESCE(v_inserted, 0);

        v_expired_count = v_expired_count + 1;
        v_last_status = 'EXPIRED_ADVANCED';

        /*
         * Find the next later draft slot that remains truly open.
         * Earlier expired/open picks are intentionally ignored here:
         * they remain selectable later but do not regain the clock.
         */
        SELECT
            v.pick_id,
            v.current_owner_team_key
        INTO
            v_next_pick_id,
            v_next_team_key
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

        IF v_next_pick_id IS NULL THEN
            -- Final active pick expired. It remains EXPIRED_OPEN,
            -- but there is no later manager to place on the clock.
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
         * The next manager's clock begins at the exact prior deadline,
         * not at the bot's later poll time. This preserves true 24-hour
         * windows even if the bot was temporarily offline.
         */
        v_state = jsonb_set(
            v_state,
            ARRAY['clock', 'current_pick_id'],
            to_jsonb(v_next_pick_id),
            true
        );

        v_state = jsonb_set(
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

        INSERT INTO nffl.draft_clock_event (
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

        GET DIAGNOSTICS v_inserted = ROW_COUNT;
        v_events_created =
            v_events_created + COALESCE(v_inserted, 0);

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

        v_state_sha = v_new_sha;

        /*
         * Continue the loop using the same v_now. If bot downtime was
         * longer than another full pick window, the next overdue pick
         * will expire atomically too.
         */
    END LOOP;
END;
$function$;

REVOKE ALL
ON FUNCTION nffl.process_draft_clock(text)
FROM PUBLIC;

-- Deliberately NO GRANT to nffl_discord_reader yet.
-- Bot execution is enabled only after rollback validation and live install
-- verification succeed.

COMMIT;
