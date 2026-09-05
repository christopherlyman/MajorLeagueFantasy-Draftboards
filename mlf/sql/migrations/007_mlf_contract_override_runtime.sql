-- MLF Relational Migration - 007
-- Canonical contract-override runtime operations.
--
-- Contract override mutations and keeper reconstruction occur in the
-- same transaction. If the resulting keeper state is invalid, the
-- override mutation rolls back with it.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


CREATE OR REPLACE FUNCTION mlf.upsert_contract_override_atomic(
    p_draft_key text,
    p_yahoo_player_key text,
    p_team_key text,
    p_years_remaining integer,
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
    v_effective_team_key text;
    v_note text;
    v_assignment_count integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_yahoo_player_key), '') IS NULL
    THEN
        RAISE EXCEPTION
            'Missing draft/player input.';
    END IF;

    IF p_years_remaining IS NULL
       OR p_years_remaining < 0
    THEN
        RAISE EXCEPTION
            'years_remaining must be zero or greater.';
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

    IF p_years_remaining > 0 THEN
        v_effective_team_key =
            NULLIF(
                BTRIM(
                    COALESCE(
                        p_team_key,
                        ''
                    )
                ),
                ''
            );

        IF v_effective_team_key IS NULL THEN
            RAISE EXCEPTION
                'Active contract override requires a team.';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM mlf.team t
            WHERE t.league_key = v_league_key
              AND t.season_year = v_season_year
              AND t.team_key = v_effective_team_key
        ) THEN
            RAISE EXCEPTION
                'Team % is not in draft %.',
                v_effective_team_key,
                p_draft_key;
        END IF;
    ELSE
        -- Canonical void override:
        -- years_remaining=0 and no effective owner.
        v_effective_team_key = NULL;
    END IF;

    v_note =
        NULLIF(
            BTRIM(
                COALESCE(
                    p_note,
                    ''
                )
            ),
            ''
        );

    INSERT INTO mlf.contract_override AS existing (
        league_key,
        season_year,
        yahoo_player_key,
        team_key,
        years_remaining,
        note,
        updated_at_utc
    )
    VALUES (
        v_league_key,
        v_season_year,
        p_yahoo_player_key,
        v_effective_team_key,
        p_years_remaining,
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
           years_remaining = EXCLUDED.years_remaining,
           note = COALESCE(
               EXCLUDED.note,
               existing.note
           ),
           updated_at_utc = now();

    v_assignment_count =
        mlf.rebuild_draft_keeper_assignments(
            p_draft_key
        );

    RETURN v_assignment_count;
END;
$function$;


CREATE OR REPLACE FUNCTION mlf.delete_contract_override_atomic(
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
        RAISE EXCEPTION
            'Missing draft/player input.';
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

    DELETE FROM mlf.contract_override
    WHERE league_key = v_league_key
      AND season_year = v_season_year
      AND yahoo_player_key = p_yahoo_player_key;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Player % does not have an MLF contract override.',
            p_yahoo_player_key;
    END IF;

    v_assignment_count =
        mlf.rebuild_draft_keeper_assignments(
            p_draft_key
        );

    RETURN v_assignment_count;
END;
$function$;


REVOKE ALL
ON FUNCTION mlf.upsert_contract_override_atomic(
    text,
    text,
    text,
    integer,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.delete_contract_override_atomic(
    text,
    text
)
FROM PUBLIC;


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '007',
    'Atomic MLF contract-override operations with keeper reconstruction'
);

COMMIT;
