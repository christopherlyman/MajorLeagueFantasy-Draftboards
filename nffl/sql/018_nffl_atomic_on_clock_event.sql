BEGIN;

-- Defense in depth:
-- one Yahoo player may be selected only once within a draft.
CREATE UNIQUE INDEX IF NOT EXISTS
    draft_selection_draft_player_uniq
ON nffl.draft_selection (
    draft_key,
    yahoo_player_key
);

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
    v_is_late_pick boolean := false;
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

    -- One pick executor at a time for this draft.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_draft_key,
            0
        )
    );

    /*
     * Enforce any due 24-hour transition inside the same
     * transaction as the attempted selection.
     *
     * This closes the race between the exact deadline and the
     * Discord bot's next polling cycle.
     */
    PERFORM 1
    FROM nffl.process_draft_clock(p_draft_key);

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
        PERFORM 1
        FROM nffl.draft_expired_pick ep
        WHERE ep.draft_key = p_draft_key
          AND ep.pick_id = p_expected_pick_id
          AND ep.resolved_at_utc IS NULL
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Pick % is neither active nor expired/open. Active pick is %.',
                p_expected_pick_id,
                v_current_pick_id;
        END IF;

        v_is_late_pick := true;
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

    IF v_is_late_pick THEN
        /*
         * The expired manager has now filled the old pick.
         * Resolve only that expired-open runtime row.
         *
         * Critically, do not rewind, restart, pause, or otherwise
         * mutate the manager who is currently on the clock.
         */
        UPDATE nffl.draft_expired_pick
           SET resolved_at_utc = v_selected_at,
               resolved_by = p_initiated_by,
               updated_at_utc = now()
         WHERE draft_key = p_draft_key
           AND pick_id = p_expected_pick_id
           AND resolved_at_utc IS NULL;

        GET DIAGNOSTICS v_inserted = ROW_COUNT;

        IF v_inserted <> 1 THEN
            RAISE EXCEPTION
                'Expired/open pick % was resolved concurrently.',
                p_expected_pick_id;
        END IF;

        -- For a late pick, the active clock remains the next
        -- actionable pick from the caller's perspective.
        v_next_pick_id = v_current_pick_id;

    ELSE
        -- Same occupancy rule used by the current DraftBoard:
        -- real selections + Contract/FT placeholders are occupied.
        -- QO placeholders remain pickable.
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
                    v_selected_at
                )
                ON CONFLICT DO NOTHING;
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

COMMIT;
