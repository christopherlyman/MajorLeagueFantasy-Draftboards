BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- ============================================================
-- Keeper placement needs the same player ranking input that the
-- legacy in-memory MLF prefill used.
-- ============================================================

ALTER TABLE mlf.player_universe
    ADD COLUMN IF NOT EXISTS rank_value numeric;


-- ============================================================
-- Rebuild CONTRACT/PT keeper assignments deterministically.
--
-- Existing MLF behavior preserved:
--   * clear/rebuild all keeper placeholders
--   * CONTRACT takes precedence over PT grouping
--   * contracts ordered by rank_value ASC, NULL last
--   * PT ordered by rank_value ASC, NULL last
--   * contracts placed first, then PT
--   * each team fills bottom-up R25 -> first standard round
--   * already-drafted players are skipped
--   * already-used draft slots are skipped
--
-- Pick-trade guard:
--   Legacy MLF does not define the correct behavior when a slot
--   required by the keeper algorithm has been traded away.
--   Do not silently consume the acquiring manager's pick and do
--   not invent a replacement-slot rule. Fail explicitly instead.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.rebuild_draft_keeper_assignments(
    p_draft_key text
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
    v_first_standard_round integer;
    v_rounds_total integer;

    v_slot_number integer;
    v_round_number integer;
    v_pick_id text;
    v_pick_owner text;

    v_assignment_count integer := 0;
    v_assigned boolean;

    team_row record;
    player_row record;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION 'Missing draft key.';
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
        d.first_standard_round,
        d.rounds_total
    INTO
        v_league_key,
        v_season_year,
        v_first_standard_round,
        v_rounds_total
    FROM mlf.draft d
    WHERE d.draft_key = p_draft_key
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Draft % not found.',
            p_draft_key;
    END IF;

    DELETE FROM mlf.draft_keeper_assignment
    WHERE draft_key = p_draft_key;

    FOR team_row IN
        SELECT DISTINCT control_team.team_key
        FROM (
            SELECT c.team_key
            FROM mlf.v_active_contract c
            JOIN mlf.player_universe pu
              ON pu.league_key = c.league_key
             AND pu.season_year = c.season_year
             AND pu.yahoo_player_key = c.yahoo_player_key
             AND pu.is_active = true
            LEFT JOIN mlf.prospect_tag pt
              ON pt.league_key = c.league_key
             AND pt.season_year = c.season_year
             AND pt.yahoo_player_key = c.yahoo_player_key
            WHERE c.league_key = v_league_key
              AND c.season_year = v_season_year
              AND pt.yahoo_player_key IS NULL

            UNION

            SELECT pt.team_key
            FROM mlf.prospect_tag pt
            JOIN mlf.player_universe pu
              ON pu.league_key = pt.league_key
             AND pu.season_year = pt.season_year
             AND pu.yahoo_player_key = pt.yahoo_player_key
             AND pu.is_active = true
            WHERE pt.league_key = v_league_key
              AND pt.season_year = v_season_year
        ) AS control_team
        ORDER BY control_team.team_key
    LOOP
        v_slot_number = NULL;

        SELECT dp.slot_number
        INTO v_slot_number
        FROM mlf.draft_pick dp
        WHERE dp.draft_key = p_draft_key
          AND dp.round_number = v_first_standard_round
          AND dp.column_team_key = team_row.team_key
        ORDER BY dp.slot_number
        LIMIT 1;

        -- Mirrors legacy behavior: a control row whose team is not
        -- represented in this draft cannot create a placeholder.
        IF v_slot_number IS NULL THEN
            CONTINUE;
        END IF;

        v_round_number = v_rounds_total;

        FOR player_row IN
            SELECT
                controlled.yahoo_player_key,
                controlled.keeper_kind,
                controlled.rank_value
            FROM (
                SELECT
                    c.yahoo_player_key,
                    'CONTRACT'::text AS keeper_kind,
                    0 AS keeper_kind_order,
                    pu.rank_value
                FROM mlf.v_active_contract c
                JOIN mlf.player_universe pu
                  ON pu.league_key = c.league_key
                 AND pu.season_year = c.season_year
                 AND pu.yahoo_player_key = c.yahoo_player_key
                 AND pu.is_active = true
                LEFT JOIN mlf.prospect_tag pt
                  ON pt.league_key = c.league_key
                 AND pt.season_year = c.season_year
                 AND pt.yahoo_player_key = c.yahoo_player_key
                WHERE c.league_key = v_league_key
                  AND c.season_year = v_season_year
                  AND c.team_key = team_row.team_key
                  AND pt.yahoo_player_key IS NULL

                UNION ALL

                SELECT
                    pt.yahoo_player_key,
                    'PT'::text AS keeper_kind,
                    1 AS keeper_kind_order,
                    pu.rank_value
                FROM mlf.prospect_tag pt
                JOIN mlf.player_universe pu
                  ON pu.league_key = pt.league_key
                 AND pu.season_year = pt.season_year
                 AND pu.yahoo_player_key = pt.yahoo_player_key
                 AND pu.is_active = true
                WHERE pt.league_key = v_league_key
                  AND pt.season_year = v_season_year
                  AND pt.team_key = team_row.team_key
            ) AS controlled
            WHERE NOT EXISTS (
                SELECT 1
                FROM mlf.draft_selection ds
                WHERE ds.draft_key = p_draft_key
                  AND ds.yahoo_player_key =
                      controlled.yahoo_player_key
            )
            ORDER BY
                controlled.keeper_kind_order,
                (controlled.rank_value IS NULL),
                controlled.rank_value,
                controlled.yahoo_player_key
        LOOP
            v_assigned = false;

            WHILE v_round_number >= v_first_standard_round
            LOOP
                v_pick_id = NULL;
                v_pick_owner = NULL;

                SELECT
                    dp.pick_id,
                    dp.current_owner_team_key
                INTO
                    v_pick_id,
                    v_pick_owner
                FROM mlf.draft_pick dp
                WHERE dp.draft_key = p_draft_key
                  AND dp.round_number = v_round_number
                  AND dp.slot_number = v_slot_number;

                v_round_number = v_round_number - 1;

                IF v_pick_id IS NULL THEN
                    CONTINUE;
                END IF;

                -- Legacy prefill skips a real/timestamped selection
                -- and moves upward to the next standard-round slot.
                IF EXISTS (
                    SELECT 1
                    FROM mlf.draft_selection ds
                    WHERE ds.draft_key = p_draft_key
                      AND ds.pick_id = v_pick_id
                ) THEN
                    CONTINUE;
                END IF;

                -- Do not silently place Team A's keeper into a pick
                -- that Team B currently owns.
                IF v_pick_owner IS DISTINCT FROM team_row.team_key THEN
                    RAISE EXCEPTION
                        'Keeper placement collision: team % requires slot %, but pick % is currently owned by %. Traded-standard-pick keeper policy requires explicit MLF resolution.',
                        team_row.team_key,
                        v_slot_number,
                        v_pick_id,
                        v_pick_owner;
                END IF;

                INSERT INTO mlf.draft_keeper_assignment (
                    draft_key,
                    pick_id,
                    team_key,
                    yahoo_player_key,
                    keeper_kind
                )
                VALUES (
                    p_draft_key,
                    v_pick_id,
                    team_row.team_key,
                    player_row.yahoo_player_key,
                    player_row.keeper_kind
                );

                v_assignment_count =
                    v_assignment_count + 1;

                v_assigned = true;

                EXIT;
            END LOOP;

            IF NOT v_assigned THEN
                RAISE EXCEPTION
                    'Insufficient standard-round slots for keeper % on team %.',
                    player_row.yahoo_player_key,
                    team_row.team_key;
            END IF;
        END LOOP;
    END LOOP;

    RETURN v_assignment_count;
END;
$function$;


REVOKE ALL
ON FUNCTION mlf.rebuild_draft_keeper_assignments(text)
FROM PUBLIC;


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '005',
    'Deterministic relational MLF contract and PT keeper reconstruction'
);

COMMIT;
