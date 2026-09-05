BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


-- ============================================================
-- Upsert an MLF Prospect Tag and rebuild keeper assignments in
-- the same transaction.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.upsert_prospect_tag_atomic(
    p_draft_key text,
    p_team_key text,
    p_yahoo_player_key text,
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
    v_assignment_count integer;
    v_note text;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_team_key), '') IS NULL
       OR NULLIF(BTRIM(p_yahoo_player_key), '') IS NULL
    THEN
        RAISE EXCEPTION 'Missing draft/team/player input.';
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
          AND t.team_key = p_team_key
    ) THEN
        RAISE EXCEPTION
            'Team % is not in draft %.',
            p_team_key,
            p_draft_key;
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
            'Active player % is not in the MLF player universe.',
            p_yahoo_player_key;
    END IF;

    v_note = NULLIF(
        BTRIM(COALESCE(p_note, '')),
        ''
    );

    INSERT INTO mlf.prospect_tag AS pt (
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
        p_team_key,
        p_yahoo_player_key,
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
               pt.note
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
-- Delete an MLF Prospect Tag and rebuild keeper assignments in
-- the same transaction.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.delete_prospect_tag_atomic(
    p_draft_key text,
    p_yahoo_player_key text
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
    v_assignment_count integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_yahoo_player_key), '') IS NULL
    THEN
        RAISE EXCEPTION 'Missing draft/player input.';
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

    DELETE FROM mlf.prospect_tag
    WHERE league_key = v_league_key
      AND season_year = v_season_year
      AND yahoo_player_key = p_yahoo_player_key;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Player % does not have an MLF Prospect Tag.',
            p_yahoo_player_key;
    END IF;

    v_assignment_count =
        mlf.rebuild_draft_keeper_assignments(
            p_draft_key
        );

    RETURN v_assignment_count;
END;
$function$;


-- ============================================================
-- Transfer the effective active contract.
--
-- Contract overrides remain authoritative when present:
--   override exists -> update override owner
--   otherwise       -> update base contract owner
--
-- Keeper reconstruction occurs in the same transaction so a
-- traded-standard-pick collision rolls back the contract move.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.transfer_active_contract_atomic(
    p_draft_key text,
    p_yahoo_player_key text,
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

    v_override_years integer;
    v_contract_years integer;

    v_source text;
    v_note text;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_yahoo_player_key), '') IS NULL
       OR NULLIF(BTRIM(p_to_team_key), '') IS NULL
    THEN
        RAISE EXCEPTION
            'Missing draft/player/team input.';
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
            'Destination team % is not in draft %.',
            p_to_team_key,
            p_draft_key;
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
            'Active player % is not in the MLF player universe.',
            p_yahoo_player_key;
    END IF;

    v_note = NULLIF(
        BTRIM(COALESCE(p_note, '')),
        ''
    );

    v_override_years = NULL;

    SELECT o.years_remaining
    INTO v_override_years
    FROM mlf.contract_override o
    WHERE o.league_key = v_league_key
      AND o.season_year = v_season_year
      AND o.yahoo_player_key = p_yahoo_player_key
    FOR UPDATE;

    IF FOUND THEN
        IF v_override_years <= 0 THEN
            RAISE EXCEPTION
                'Player % does not have an active effective contract.',
                p_yahoo_player_key;
        END IF;

        UPDATE mlf.contract_override
           SET team_key = p_to_team_key,
               note = COALESCE(
                   v_note,
                   note
               ),
               updated_at_utc = now()
         WHERE league_key = v_league_key
           AND season_year = v_season_year
           AND yahoo_player_key = p_yahoo_player_key;

        v_source = 'override';

    ELSE
        v_contract_years = NULL;

        SELECT c.years_remaining
        INTO v_contract_years
        FROM mlf.contract c
        WHERE c.league_key = v_league_key
          AND c.season_year = v_season_year
          AND c.yahoo_player_key = p_yahoo_player_key
        FOR UPDATE;

        IF NOT FOUND
           OR v_contract_years <= 0
        THEN
            RAISE EXCEPTION
                'Player % does not have an active effective contract.',
                p_yahoo_player_key;
        END IF;

        UPDATE mlf.contract
           SET team_key = p_to_team_key,
               note = COALESCE(
                   v_note,
                   note
               ),
               updated_at_utc = now()
         WHERE league_key = v_league_key
           AND season_year = v_season_year
           AND yahoo_player_key = p_yahoo_player_key;

        v_source = 'contract';
    END IF;

    PERFORM mlf.rebuild_draft_keeper_assignments(
        p_draft_key
    );

    RETURN v_source;
END;
$function$;


REVOKE ALL
ON FUNCTION mlf.upsert_prospect_tag_atomic(
    text,
    text,
    text,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.delete_prospect_tag_atomic(
    text,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.transfer_active_contract_atomic(
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
    '006',
    'Atomic MLF prospect-tag and active-contract control operations'
);

COMMIT;
