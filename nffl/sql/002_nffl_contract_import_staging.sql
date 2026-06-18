CREATE TABLE IF NOT EXISTS nffl.contract_import_staging (
    import_batch text NOT NULL,
    league_key text NOT NULL DEFAULT '470.l.84346',
    season_year integer NOT NULL DEFAULT 2026,
    sheet_name text NOT NULL,
    owner_name text NOT NULL,
    sheet_team_name text NOT NULL,
    team_key text NOT NULL,
    excel_row integer NOT NULL,
    player_name text NOT NULL,
    raw_2025 text NOT NULL,
    status text NOT NULL,
    years_remaining_2026 integer NOT NULL,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (import_batch, team_key, excel_row)
);

CREATE OR REPLACE FUNCTION nffl.norm_player_name(v text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT regexp_replace(
        regexp_replace(
            regexp_replace(
                lower(coalesce(v, '')),
                '\b(sr|jr|ii|iii|iv|v)\b\.?',
                '',
                'g'
            ),
            '[^a-z0-9]+',
            '',
            'g'
        ),
        '\s+',
        '',
        'g'
    );
$$;

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
        ) AS match_count
    FROM s
    LEFT JOIN u
      ON u.universe_norm = s.staging_norm
)
SELECT
    *,
    CASE
        WHEN yahoo_player_key IS NULL THEN 'UNMATCHED'
        WHEN match_count = 1 THEN 'MATCHED_EXACT_NORMALIZED'
        ELSE 'AMBIGUOUS'
    END AS match_status
FROM matches;
