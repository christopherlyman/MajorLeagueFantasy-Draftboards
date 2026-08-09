-- NFFL unattended auto-pick entrypoint
--
-- Thin entrypoint for external callers such as the Discord bot.
-- It derives league/season from nffl.draft and delegates ALL
-- draft logic to the canonical execute_armed_autopick(...) executor.

BEGIN;

CREATE OR REPLACE FUNCTION nffl.execute_armed_autopick(
    p_draft_key text
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
    v_league_key text;
    v_season_year integer;
BEGIN
    SELECT
        d.league_key,
        d.season_year
      INTO
        v_league_key,
        v_season_year
    FROM nffl.draft d
    WHERE d.draft_key = p_draft_key
      AND d.status = 'active';

    IF NOT FOUND THEN
        RETURN QUERY
        SELECT
            'NO_ACTIVE_DRAFT'::text,
            NULL::text,
            NULL::text,
            NULL::text,
            NULL::integer,
            NULL::text,
            NULL::text;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT *
    FROM nffl.execute_armed_autopick(
        p_draft_key,
        v_league_key,
        v_season_year
    );
END;
$function$;

REVOKE ALL
ON FUNCTION nffl.execute_armed_autopick(text)
FROM PUBLIC;

COMMIT;
