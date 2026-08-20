-- NFFL legacy-to-modern contract episode identity bridge.
--
-- Purpose:
--   The imported 2021-2025 historical workbook intentionally preserves
--   separate contract episodes, including multiple episodes for the same
--   player/franchise.
--
--   historical_contract_episode.yahoo_player_key was left nullable because
--   the workbook itself did not contain Yahoo player identifiers.
--
--   This one-time 2025 -> 2026 boundary migration identifies exactly the
--   legacy contract episodes that continued into the 2026 operational
--   contract set and stores the corresponding 2026 Yahoo player key.
--
-- Identity rule:
--   1. Same normalized current franchise/team.
--   2. Canonical player identity using the existing contract-import alias
--      rules, with aliases taking precedence over normalized direct matches.
--   3. The legacy episode's 2025 cell must be CONTRACT.
--   4. 2025 contract years must equal 2026 operational years + 1.
--
-- Proven result before this migration was authored:
--   operational 2026 contracts                 = 63
--   candidate rows                             = 63
--   contracts with exactly one candidate       = 63
--   ambiguous contracts                        = 0
--   contracts without candidate                = 0
--   distinct legacy episodes                   = 63
--
-- Important:
--   This does NOT assign Yahoo identity to every historical episode.
--   Older completed episodes for a player remain separate and may keep
--   yahoo_player_key NULL.
--
--   This does NOT merge episodes or rewrite historical season cells.
--
-- Idempotence:
--   Valid pre-state is either zero linked boundary episodes or the exact
--   63 links created by this migration. Any partial or conflicting state
--   is rejected.
--
-- Transaction policy:
--   This migration does not issue BEGIN or COMMIT. The caller controls the
--   surrounding transaction.


DO $migration$
DECLARE
    v_operational_contracts integer;
    v_candidate_rows integer;
    v_candidate_contracts integer;
    v_distinct_episodes integer;

    v_existing_links integer;
    v_conflicting_links integer;
    v_unexpected_links integer;

    v_updated_rows integer;
    v_final_links integer;
    v_final_matching_links integer;
BEGIN
    SELECT count(*)
      INTO v_operational_contracts
      FROM nffl.contract c
     WHERE c.league_key = '470.l.84346'
       AND c.season_year = 2026;

    IF v_operational_contracts <> 63 THEN
        RAISE EXCEPTION
            '026 expected exactly 63 operational 2026 contracts; found %.',
            v_operational_contracts;
    END IF;


    WITH alias_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            a.yahoo_player_key,
            'ALIAS'::text AS match_method
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_name_alias a
          ON a.league_key = e.league_key
         AND a.season_year = 2026
         AND a.alias_scope = 'contract_import'
         AND nffl.norm_player_name(a.alias_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
    ),
    direct_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            pu.yahoo_player_key,
            'NORMALIZED'::text AS match_method
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_universe pu
          ON pu.league_key = e.league_key
         AND pu.season_year = 2026
         AND nffl.norm_player_name(pu.full_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
          AND NOT EXISTS (
              SELECT 1
              FROM alias_identity ai
              WHERE ai.league_key = e.league_key
                AND ai.team_key = e.team_key
                AND ai.source_row_number =
                    e.source_row_number
          )
    ),
    legacy_identity AS (
        SELECT *
        FROM alias_identity

        UNION ALL

        SELECT *
        FROM direct_identity
    ),
    candidates AS (
        SELECT
            c.league_key,
            c.season_year,
            c.team_key,
            c.yahoo_player_key,
            c.contract_years_remaining,
            c.status,

            li.source_row_number,
            li.match_method,

            s.contract_years AS legacy_2025_years
        FROM nffl.contract c
        JOIN legacy_identity li
          ON li.league_key = c.league_key
         AND li.team_key = c.team_key
         AND li.yahoo_player_key =
             c.yahoo_player_key
        JOIN nffl.historical_contract_season s
          ON s.league_key = li.league_key
         AND s.team_key = li.team_key
         AND s.source_row_number =
             li.source_row_number
         AND s.season_year = 2025
         AND s.contract_status = 'CONTRACT'
         AND s.contract_years =
             c.contract_years_remaining + 1
        WHERE c.league_key = '470.l.84346'
          AND c.season_year = 2026
    )
    SELECT
        count(*),
        count(
            DISTINCT (
                team_key,
                yahoo_player_key
            )
        ),
        count(
            DISTINCT (
                team_key,
                source_row_number
            )
        )
      INTO
        v_candidate_rows,
        v_candidate_contracts,
        v_distinct_episodes
      FROM candidates;


    IF v_candidate_rows <> 63
       OR v_candidate_contracts <> 63
       OR v_distinct_episodes <> 63 THEN
        RAISE EXCEPTION
            '026 candidate-set invariant failed: rows=%, contracts=%, episodes=%; expected 63/63/63.',
            v_candidate_rows,
            v_candidate_contracts,
            v_distinct_episodes;
    END IF;


    SELECT count(*)
      INTO v_existing_links
      FROM nffl.historical_contract_episode e
     WHERE e.league_key = '470.l.84346'
       AND e.yahoo_player_key IS NOT NULL
       AND btrim(e.yahoo_player_key) <> '';

    IF v_existing_links NOT IN (0, 63) THEN
        RAISE EXCEPTION
            '026 found partial/unexpected legacy Yahoo identity pre-state: % linked episodes; expected 0 or 63.',
            v_existing_links;
    END IF;


    WITH alias_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            a.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_name_alias a
          ON a.league_key = e.league_key
         AND a.season_year = 2026
         AND a.alias_scope = 'contract_import'
         AND nffl.norm_player_name(a.alias_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
    ),
    direct_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            pu.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_universe pu
          ON pu.league_key = e.league_key
         AND pu.season_year = 2026
         AND nffl.norm_player_name(pu.full_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
          AND NOT EXISTS (
              SELECT 1
              FROM alias_identity ai
              WHERE ai.league_key = e.league_key
                AND ai.team_key = e.team_key
                AND ai.source_row_number =
                    e.source_row_number
          )
    ),
    legacy_identity AS (
        SELECT *
        FROM alias_identity

        UNION ALL

        SELECT *
        FROM direct_identity
    ),
    candidates AS (
        SELECT
            c.league_key,
            c.team_key,
            c.yahoo_player_key,
            li.source_row_number
        FROM nffl.contract c
        JOIN legacy_identity li
          ON li.league_key = c.league_key
         AND li.team_key = c.team_key
         AND li.yahoo_player_key =
             c.yahoo_player_key
        JOIN nffl.historical_contract_season s
          ON s.league_key = li.league_key
         AND s.team_key = li.team_key
         AND s.source_row_number =
             li.source_row_number
         AND s.season_year = 2025
         AND s.contract_status = 'CONTRACT'
         AND s.contract_years =
             c.contract_years_remaining + 1
        WHERE c.league_key = '470.l.84346'
          AND c.season_year = 2026
    )
    SELECT count(*)
      INTO v_conflicting_links
      FROM candidates x
      JOIN nffl.historical_contract_episode e
        ON e.league_key = x.league_key
       AND e.team_key = x.team_key
       AND e.source_row_number =
           x.source_row_number
     WHERE e.yahoo_player_key IS NOT NULL
       AND btrim(e.yahoo_player_key) <> ''
       AND e.yahoo_player_key
           IS DISTINCT FROM x.yahoo_player_key;

    IF v_conflicting_links <> 0 THEN
        RAISE EXCEPTION
            '026 found % candidate episodes already linked to a conflicting Yahoo player key.',
            v_conflicting_links;
    END IF;


    WITH alias_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            a.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_name_alias a
          ON a.league_key = e.league_key
         AND a.season_year = 2026
         AND a.alias_scope = 'contract_import'
         AND nffl.norm_player_name(a.alias_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
    ),
    direct_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            pu.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_universe pu
          ON pu.league_key = e.league_key
         AND pu.season_year = 2026
         AND nffl.norm_player_name(pu.full_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
          AND NOT EXISTS (
              SELECT 1
              FROM alias_identity ai
              WHERE ai.league_key = e.league_key
                AND ai.team_key = e.team_key
                AND ai.source_row_number =
                    e.source_row_number
          )
    ),
    legacy_identity AS (
        SELECT *
        FROM alias_identity

        UNION ALL

        SELECT *
        FROM direct_identity
    ),
    candidates AS (
        SELECT
            c.league_key,
            c.team_key,
            c.yahoo_player_key,
            li.source_row_number
        FROM nffl.contract c
        JOIN legacy_identity li
          ON li.league_key = c.league_key
         AND li.team_key = c.team_key
         AND li.yahoo_player_key =
             c.yahoo_player_key
        JOIN nffl.historical_contract_season s
          ON s.league_key = li.league_key
         AND s.team_key = li.team_key
         AND s.source_row_number =
             li.source_row_number
         AND s.season_year = 2025
         AND s.contract_status = 'CONTRACT'
         AND s.contract_years =
             c.contract_years_remaining + 1
        WHERE c.league_key = '470.l.84346'
          AND c.season_year = 2026
    )
    SELECT count(*)
      INTO v_unexpected_links
      FROM nffl.historical_contract_episode e
     WHERE e.league_key = '470.l.84346'
       AND e.yahoo_player_key IS NOT NULL
       AND btrim(e.yahoo_player_key) <> ''
       AND NOT EXISTS (
           SELECT 1
           FROM candidates x
           WHERE x.league_key = e.league_key
             AND x.team_key = e.team_key
             AND x.source_row_number =
                 e.source_row_number
             AND x.yahoo_player_key =
                 e.yahoo_player_key
       );

    IF v_unexpected_links <> 0 THEN
        RAISE EXCEPTION
            '026 found % existing historical Yahoo identity links outside the deterministic boundary candidate set.',
            v_unexpected_links;
    END IF;


    WITH alias_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            a.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_name_alias a
          ON a.league_key = e.league_key
         AND a.season_year = 2026
         AND a.alias_scope = 'contract_import'
         AND nffl.norm_player_name(a.alias_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
    ),
    direct_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            pu.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_universe pu
          ON pu.league_key = e.league_key
         AND pu.season_year = 2026
         AND nffl.norm_player_name(pu.full_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
          AND NOT EXISTS (
              SELECT 1
              FROM alias_identity ai
              WHERE ai.league_key = e.league_key
                AND ai.team_key = e.team_key
                AND ai.source_row_number =
                    e.source_row_number
          )
    ),
    legacy_identity AS (
        SELECT *
        FROM alias_identity

        UNION ALL

        SELECT *
        FROM direct_identity
    ),
    candidates AS (
        SELECT
            c.league_key,
            c.team_key,
            c.yahoo_player_key,
            li.source_row_number
        FROM nffl.contract c
        JOIN legacy_identity li
          ON li.league_key = c.league_key
         AND li.team_key = c.team_key
         AND li.yahoo_player_key =
             c.yahoo_player_key
        JOIN nffl.historical_contract_season s
          ON s.league_key = li.league_key
         AND s.team_key = li.team_key
         AND s.source_row_number =
             li.source_row_number
         AND s.season_year = 2025
         AND s.contract_status = 'CONTRACT'
         AND s.contract_years =
             c.contract_years_remaining + 1
        WHERE c.league_key = '470.l.84346'
          AND c.season_year = 2026
    )
    UPDATE nffl.historical_contract_episode e
       SET yahoo_player_key = x.yahoo_player_key
      FROM candidates x
     WHERE e.league_key = x.league_key
       AND e.team_key = x.team_key
       AND e.source_row_number =
           x.source_row_number
       AND e.yahoo_player_key IS NULL;

    GET DIAGNOSTICS v_updated_rows = ROW_COUNT;


    IF v_existing_links = 0
       AND v_updated_rows <> 63 THEN
        RAISE EXCEPTION
            '026 expected to create 63 legacy episode identity links from clean pre-state; updated %.',
            v_updated_rows;
    END IF;

    IF v_existing_links = 63
       AND v_updated_rows <> 0 THEN
        RAISE EXCEPTION
            '026 idempotent re-run unexpectedly updated % rows.',
            v_updated_rows;
    END IF;


    SELECT count(*)
      INTO v_final_links
      FROM nffl.historical_contract_episode e
     WHERE e.league_key = '470.l.84346'
       AND e.yahoo_player_key IS NOT NULL
       AND btrim(e.yahoo_player_key) <> '';

    IF v_final_links <> 63 THEN
        RAISE EXCEPTION
            '026 post-state expected exactly 63 linked legacy episodes; found %.',
            v_final_links;
    END IF;


    WITH alias_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            a.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_name_alias a
          ON a.league_key = e.league_key
         AND a.season_year = 2026
         AND a.alias_scope = 'contract_import'
         AND nffl.norm_player_name(a.alias_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
    ),
    direct_identity AS (
        SELECT
            e.league_key,
            e.team_key,
            e.source_row_number,
            pu.yahoo_player_key
        FROM nffl.historical_contract_episode e
        JOIN nffl.player_universe pu
          ON pu.league_key = e.league_key
         AND pu.season_year = 2026
         AND nffl.norm_player_name(pu.full_name) =
             nffl.norm_player_name(e.player_name)
        WHERE e.league_key = '470.l.84346'
          AND NOT EXISTS (
              SELECT 1
              FROM alias_identity ai
              WHERE ai.league_key = e.league_key
                AND ai.team_key = e.team_key
                AND ai.source_row_number =
                    e.source_row_number
          )
    ),
    legacy_identity AS (
        SELECT *
        FROM alias_identity

        UNION ALL

        SELECT *
        FROM direct_identity
    ),
    candidates AS (
        SELECT
            c.league_key,
            c.team_key,
            c.yahoo_player_key,
            li.source_row_number
        FROM nffl.contract c
        JOIN legacy_identity li
          ON li.league_key = c.league_key
         AND li.team_key = c.team_key
         AND li.yahoo_player_key =
             c.yahoo_player_key
        JOIN nffl.historical_contract_season s
          ON s.league_key = li.league_key
         AND s.team_key = li.team_key
         AND s.source_row_number =
             li.source_row_number
         AND s.season_year = 2025
         AND s.contract_status = 'CONTRACT'
         AND s.contract_years =
             c.contract_years_remaining + 1
        WHERE c.league_key = '470.l.84346'
          AND c.season_year = 2026
    )
    SELECT count(*)
      INTO v_final_matching_links
      FROM candidates x
      JOIN nffl.historical_contract_episode e
        ON e.league_key = x.league_key
       AND e.team_key = x.team_key
       AND e.source_row_number =
           x.source_row_number
       AND e.yahoo_player_key =
           x.yahoo_player_key;

    IF v_final_matching_links <> 63 THEN
        RAISE EXCEPTION
            '026 post-state expected 63 exact candidate identity links; found %.',
            v_final_matching_links;
    END IF;


    RAISE NOTICE
        '026 legacy identity links verified: preexisting=%, updated=%, final=%.',
        v_existing_links,
        v_updated_rows,
        v_final_links;
END
$migration$;


COMMENT ON COLUMN
nffl.historical_contract_episode.yahoo_player_key
IS
'Optional modern Yahoo player key for a legacy contract episode when that specific episode has been deterministically linked across the historical-to-modern lifecycle boundary. Separate older episodes for the same player may remain NULL.';