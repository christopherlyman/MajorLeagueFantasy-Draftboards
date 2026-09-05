
BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


-- ============================================================
-- Replace one Prospect Tag with another atomically.
--
-- This exists because the commissioner UI may replace a team's
-- current PT. Delete-old + insert-new must not be split across
-- transactions.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.replace_prospect_tag_atomic(
    p_draft_key text,
    p_old_yahoo_player_key text,
    p_team_key text,
    p_new_yahoo_player_key text,
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
    v_old_player text;
    v_new_player text;
    v_team_key text;
    v_note text;
    v_assignment_count integer;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL
       OR NULLIF(BTRIM(p_team_key), '') IS NULL
       OR NULLIF(BTRIM(p_new_yahoo_player_key), '') IS NULL
    THEN
        RAISE EXCEPTION
            'Missing draft/team/new-player input.';
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

    v_old_player =
        NULLIF(
            BTRIM(
                COALESCE(
                    p_old_yahoo_player_key,
                    ''
                )
            ),
            ''
        );

    v_new_player =
        BTRIM(p_new_yahoo_player_key);

    v_team_key =
        BTRIM(p_team_key);

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

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.team t
        WHERE t.league_key = v_league_key
          AND t.season_year = v_season_year
          AND t.team_key = v_team_key
    ) THEN
        RAISE EXCEPTION
            'Team % is not in draft %.',
            v_team_key,
            p_draft_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM mlf.player_universe pu
        WHERE pu.league_key = v_league_key
          AND pu.season_year = v_season_year
          AND pu.yahoo_player_key = v_new_player
          AND pu.is_active = true
    ) THEN
        RAISE EXCEPTION
            'Active player % is not in the MLF player universe.',
            v_new_player;
    END IF;

    IF v_old_player IS NOT NULL
       AND v_old_player <> v_new_player
    THEN
        DELETE FROM mlf.prospect_tag
        WHERE league_key = v_league_key
          AND season_year = v_season_year
          AND yahoo_player_key = v_old_player;
    END IF;

    INSERT INTO mlf.prospect_tag AS existing (
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
        v_team_key,
        v_new_player,
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


-- ============================================================
-- Apply all canonical player/pick effects of one commissioner
-- trade in ONE transaction and rebuild keepers only after the
-- full final ownership state exists.
--
-- JSON asset shape:
--
-- {
--   "asset_type": "PLAYER" | "PICK",
--   "asset_id": "...",
--   "from_team_key": "...",
--   "to_team_key": "...",
--   "snapshot": {
--       "contract_years": <integer>
--   }
-- }
--
-- PLAYER assets with contract_years <= 0 are receipt-only,
-- preserving the existing MLF Trade Builder behavior.
-- ============================================================

CREATE OR REPLACE FUNCTION mlf.apply_trade_assets_atomic(
    p_draft_key text,
    p_assets jsonb,
    p_note text DEFAULT NULL
)
RETURNS TABLE (
    player_updates integer,
    pick_updates integer,
    keeper_assignments integer
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, mlf
AS $function$
DECLARE
    v_league_key text;
    v_season_year integer;

    v_asset jsonb;
    v_asset_type text;
    v_asset_id text;
    v_from_team_key text;
    v_to_team_key text;
    v_contract_years integer;

    v_pick_owner text;
    v_pick_column_team text;

    v_override_team text;
    v_override_years integer;
    v_contract_team text;
    v_contract_years_actual integer;
    v_override_found boolean;
    v_contract_found boolean;

    v_note text;

    v_player_updates integer := 0;
    v_pick_updates integer := 0;
    v_keeper_assignments integer := 0;
BEGIN
    IF NULLIF(BTRIM(p_draft_key), '') IS NULL THEN
        RAISE EXCEPTION
            'Missing draft key.';
    END IF;

    IF p_assets IS NULL
       OR pg_catalog.jsonb_typeof(p_assets) <> 'array'
    THEN
        RAISE EXCEPTION
            'Trade assets must be a JSON array.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM (
            SELECT
                UPPER(
                    BTRIM(
                        COALESCE(
                            asset.value ->> 'asset_type',
                            ''
                        )
                    )
                ) AS asset_type,
                BTRIM(
                    COALESCE(
                        asset.value ->> 'asset_id',
                        ''
                    )
                ) AS asset_id,
                count(*) AS n
            FROM pg_catalog.jsonb_array_elements(
                p_assets
            ) AS asset(value)
            GROUP BY
                UPPER(
                    BTRIM(
                        COALESCE(
                            asset.value ->> 'asset_type',
                            ''
                        )
                    )
                ),
                BTRIM(
                    COALESCE(
                        asset.value ->> 'asset_id',
                        ''
                    )
                )
            HAVING count(*) > 1
        ) duplicates
    ) THEN
        RAISE EXCEPTION
            'Trade contains a duplicate asset.';
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

    FOR v_asset IN
        SELECT asset.value
        FROM pg_catalog.jsonb_array_elements(
            p_assets
        ) AS asset(value)
    LOOP
        v_asset_type =
            UPPER(
                BTRIM(
                    COALESCE(
                        v_asset ->> 'asset_type',
                        ''
                    )
                )
            );

        v_asset_id =
            BTRIM(
                COALESCE(
                    v_asset ->> 'asset_id',
                    ''
                )
            );

        v_from_team_key =
            BTRIM(
                COALESCE(
                    v_asset ->> 'from_team_key',
                    ''
                )
            );

        v_to_team_key =
            BTRIM(
                COALESCE(
                    v_asset ->> 'to_team_key',
                    ''
                )
            );

        IF v_asset_type NOT IN (
            'PLAYER',
            'PICK'
        ) THEN
            RAISE EXCEPTION
                'Unsupported trade asset type %.',
                v_asset_type;
        END IF;

        IF v_asset_id = ''
           OR v_from_team_key = ''
           OR v_to_team_key = ''
        THEN
            RAISE EXCEPTION
                'Trade asset is missing identity/team fields.';
        END IF;

        IF v_from_team_key = v_to_team_key THEN
            RAISE EXCEPTION
                'Trade asset % has identical source and destination teams.',
                v_asset_id;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM mlf.team t
            WHERE t.league_key = v_league_key
              AND t.season_year = v_season_year
              AND t.team_key = v_from_team_key
        ) THEN
            RAISE EXCEPTION
                'Trade source team % is not in draft %.',
                v_from_team_key,
                p_draft_key;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM mlf.team t
            WHERE t.league_key = v_league_key
              AND t.season_year = v_season_year
              AND t.team_key = v_to_team_key
        ) THEN
            RAISE EXCEPTION
                'Trade destination team % is not in draft %.',
                v_to_team_key,
                p_draft_key;
        END IF;

        IF v_asset_type = 'PLAYER' THEN
            v_contract_years =
                COALESCE(
                    NULLIF(
                        BTRIM(
                            COALESCE(
                                v_asset #>> '{snapshot,contract_years}',
                                ''
                            )
                        ),
                        ''
                    )::integer,
                    0
                );

            -- Non-contract player trade:
            -- receipt/history only; no draft-control mutation.
            IF v_contract_years <= 0 THEN
                CONTINUE;
            END IF;

            v_override_team = NULL;
            v_override_years = NULL;
            v_override_found = false;

            SELECT
                co.team_key,
                co.years_remaining
            INTO
                v_override_team,
                v_override_years
            FROM mlf.contract_override co
            WHERE co.league_key = v_league_key
              AND co.season_year = v_season_year
              AND co.yahoo_player_key = v_asset_id
            FOR UPDATE;

            v_override_found = FOUND;

            IF v_override_found THEN
                IF v_override_years <= 0 THEN
                    RAISE EXCEPTION
                        'Player % has no active effective contract.',
                        v_asset_id;
                END IF;

                IF v_override_team IS DISTINCT FROM v_from_team_key THEN
                    RAISE EXCEPTION
                        'Player % effective contract owner changed: expected %, found %.',
                        v_asset_id,
                        v_from_team_key,
                        v_override_team;
                END IF;

                UPDATE mlf.contract_override
                   SET team_key = v_to_team_key,
                       note = COALESCE(
                           v_note,
                           note
                       ),
                       updated_at_utc = now()
                 WHERE league_key = v_league_key
                   AND season_year = v_season_year
                   AND yahoo_player_key = v_asset_id;

            ELSE
                v_contract_team = NULL;
                v_contract_years_actual = NULL;
                v_contract_found = false;

                SELECT
                    c.team_key,
                    c.years_remaining
                INTO
                    v_contract_team,
                    v_contract_years_actual
                FROM mlf.contract c
                WHERE c.league_key = v_league_key
                  AND c.season_year = v_season_year
                  AND c.yahoo_player_key = v_asset_id
                FOR UPDATE;

                v_contract_found = FOUND;

                IF NOT v_contract_found
                   OR v_contract_years_actual <= 0
                THEN
                    RAISE EXCEPTION
                        'Player % has no active effective contract.',
                        v_asset_id;
                END IF;

                IF v_contract_team IS DISTINCT FROM v_from_team_key THEN
                    RAISE EXCEPTION
                        'Player % contract owner changed: expected %, found %.',
                        v_asset_id,
                        v_from_team_key,
                        v_contract_team;
                END IF;

                UPDATE mlf.contract
                   SET team_key = v_to_team_key,
                       note = COALESCE(
                           v_note,
                           note
                       ),
                       updated_at_utc = now()
                 WHERE league_key = v_league_key
                   AND season_year = v_season_year
                   AND yahoo_player_key = v_asset_id;
            END IF;

            v_player_updates =
                v_player_updates + 1;

            CONTINUE;
        END IF;

        -- PICK asset.
        v_pick_owner = NULL;
        v_pick_column_team = NULL;

        SELECT
            dp.current_owner_team_key,
            dp.column_team_key
        INTO
            v_pick_owner,
            v_pick_column_team
        FROM mlf.draft_pick dp
        WHERE dp.draft_key = p_draft_key
          AND dp.pick_id = v_asset_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Draft pick % not found.',
                v_asset_id;
        END IF;

        IF EXISTS (
            SELECT 1
            FROM mlf.draft_selection ds
            WHERE ds.draft_key = p_draft_key
              AND ds.pick_id = v_asset_id
        ) THEN
            RAISE EXCEPTION
                'Draft pick % already contains a real selection.',
                v_asset_id;
        END IF;

        IF v_pick_owner IS DISTINCT FROM v_from_team_key THEN
            RAISE EXCEPTION
                'Pick % owner changed: expected %, found %.',
                v_asset_id,
                v_from_team_key,
                v_pick_owner;
        END IF;

        UPDATE mlf.draft_pick
           SET current_owner_team_key = v_to_team_key,
               traded_flag = (
                   v_to_team_key <> v_pick_column_team
               ),
               ownership_note = COALESCE(
                   v_note,
                   ownership_note
               ),
               updated_at_utc = now()
         WHERE draft_key = p_draft_key
           AND pick_id = v_asset_id;

        INSERT INTO mlf.draft_pick_trade (
            draft_key,
            pick_id,
            from_team_key,
            to_team_key,
            trade_date,
            note
        )
        VALUES (
            p_draft_key,
            v_asset_id,
            v_from_team_key,
            v_to_team_key,
            CURRENT_DATE,
            v_note
        );

        v_pick_updates =
            v_pick_updates + 1;
    END LOOP;

    -- Reconstruct keepers once, against the FINAL ownership state.
    v_keeper_assignments =
        mlf.rebuild_draft_keeper_assignments(
            p_draft_key
        );

    RETURN QUERY
    SELECT
        v_player_updates,
        v_pick_updates,
        v_keeper_assignments;
END;
$function$;


REVOKE ALL
ON FUNCTION mlf.replace_prospect_tag_atomic(
    text,
    text,
    text,
    text,
    text
)
FROM PUBLIC;

REVOKE ALL
ON FUNCTION mlf.apply_trade_assets_atomic(
    text,
    jsonb,
    text
)
FROM PUBLIC;


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '008',
    'Atomic MLF multi-asset trade and Prospect Tag replacement operations'
);

COMMIT;
