CREATE TABLE IF NOT EXISTS nffl.player_name_alias (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    alias_name text NOT NULL,
    yahoo_player_key text NOT NULL,
    alias_scope text NOT NULL DEFAULT 'contract_import',
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, alias_name, alias_scope),
    UNIQUE (league_key, season_year, yahoo_player_key, alias_scope)
);

CREATE OR REPLACE VIEW nffl.v_contract_import_match AS
WITH s AS (
    SELECT
        *,
        nffl.norm_player_name(player_name) AS staging_norm
    FROM nffl.contract_import_staging
    WHERE import_batch = '2026_from_2025_sheet_v1'
),
u AS (
    SELECT
        league_key,
        season_year,
        yahoo_player_key,
        full_name,
        nfl_team_abbr,
        eligible_positions,
        nffl.norm_player_name(full_name) AS universe_norm
    FROM nffl.player_universe
    WHERE league_key='470.l.84346'
      AND season_year=2026
),
alias_match AS (
    SELECT
        s.import_batch,
        s.league_key,
        s.season_year,
        s.team_key,
        s.excel_row,
        a.yahoo_player_key
    FROM s
    JOIN nffl.player_name_alias a
      ON a.league_key = s.league_key
     AND a.season_year = s.season_year
     AND a.alias_scope = 'contract_import'
     AND nffl.norm_player_name(a.alias_name) = s.staging_norm
),
direct_match AS (
    SELECT
        s.import_batch,
        s.league_key,
        s.season_year,
        s.team_key,
        s.excel_row,
        u.yahoo_player_key
    FROM s
    JOIN u
      ON u.universe_norm = s.staging_norm
),
chosen_match AS (
    SELECT * FROM alias_match
    UNION
    SELECT * FROM direct_match
    WHERE NOT EXISTS (
        SELECT 1
        FROM alias_match a
        WHERE a.import_batch = direct_match.import_batch
          AND a.league_key = direct_match.league_key
          AND a.season_year = direct_match.season_year
          AND a.team_key = direct_match.team_key
          AND a.excel_row = direct_match.excel_row
    )
),
matches AS (
    SELECT
        s.import_batch,
        s.league_key,
        s.season_year,
        s.sheet_name,
        s.owner_name,
        s.sheet_team_name,
        s.team_key,
        s.excel_row,
        s.player_name,
        s.raw_2025,
        s.status,
        s.years_remaining_2026,
        u.yahoo_player_key,
        u.full_name AS matched_full_name,
        u.nfl_team_abbr,
        u.eligible_positions,
        count(u.yahoo_player_key) OVER (
            PARTITION BY s.import_batch, s.team_key, s.excel_row
        ) AS match_count,
        CASE
            WHEN a.yahoo_player_key IS NOT NULL THEN true
            ELSE false
        END AS matched_by_alias
    FROM s
    LEFT JOIN chosen_match cm
      ON cm.import_batch = s.import_batch
     AND cm.league_key = s.league_key
     AND cm.season_year = s.season_year
     AND cm.team_key = s.team_key
     AND cm.excel_row = s.excel_row
    LEFT JOIN u
      ON u.league_key = s.league_key
     AND u.season_year = s.season_year
     AND u.yahoo_player_key = cm.yahoo_player_key
    LEFT JOIN alias_match a
      ON a.import_batch = s.import_batch
     AND a.league_key = s.league_key
     AND a.season_year = s.season_year
     AND a.team_key = s.team_key
     AND a.excel_row = s.excel_row
     AND a.yahoo_player_key = cm.yahoo_player_key
)
SELECT
    *,
    CASE
        WHEN yahoo_player_key IS NULL THEN 'UNMATCHED'
        WHEN match_count = 1 AND matched_by_alias THEN 'MATCHED_ALIAS'
        WHEN match_count = 1 THEN 'MATCHED_EXACT_NORMALIZED'
        ELSE 'AMBIGUOUS'
    END AS match_status
FROM matches;
