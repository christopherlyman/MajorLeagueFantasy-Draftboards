DROP TABLE IF EXISTS tmp_nffl_contract_import_staging;

CREATE TEMP TABLE tmp_nffl_contract_import_staging (
    sheet_name text,
    owner_name text,
    sheet_team_name text,
    team_key text,
    excel_row integer,
    player_name text,
    raw_2025 text,
    status text,
    years_remaining_2026 integer
);

\copy tmp_nffl_contract_import_staging (sheet_name, owner_name, sheet_team_name, team_key, excel_row, player_name, raw_2025, status, years_remaining_2026) FROM '/tmp/nffl_contracts_2026_staging_from_2025_sheet.csv' WITH (FORMAT csv, HEADER true);

DELETE FROM nffl.contract_import_staging
WHERE import_batch='2026_from_2025_sheet_v1';

INSERT INTO nffl.contract_import_staging (
    import_batch,
    league_key,
    season_year,
    sheet_name,
    owner_name,
    sheet_team_name,
    team_key,
    excel_row,
    player_name,
    raw_2025,
    status,
    years_remaining_2026
)
SELECT
    '2026_from_2025_sheet_v1',
    '470.l.84346',
    2026,
    sheet_name,
    owner_name,
    sheet_team_name,
    team_key,
    excel_row,
    player_name,
    raw_2025,
    status,
    years_remaining_2026
FROM tmp_nffl_contract_import_staging;

SELECT
    'LOADED_ROWS' AS check_name,
    count(*) AS rows
FROM nffl.contract_import_staging
WHERE import_batch='2026_from_2025_sheet_v1';
