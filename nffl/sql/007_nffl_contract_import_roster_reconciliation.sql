CREATE OR REPLACE VIEW nffl.v_contract_import_roster_reconciliation AS
WITH m AS (
    SELECT *
    FROM nffl.v_contract_import_match
),
snap AS (
    SELECT DISTINCT ON (league_key, season_year)
        snapshot_id,
        league_key,
        season_year,
        source_season_year,
        snapshot_type,
        snapshot_label,
        updated_at_utc
    FROM nffl.roster_snapshot
    WHERE snapshot_type = 'END_OF_PRIOR_SEASON_ROSTER'
    ORDER BY
        league_key,
        season_year,
        updated_at_utc DESC,
        snapshot_id
),
same_team_roster AS (
    SELECT DISTINCT
        rsp.snapshot_id,
        rsp.league_key,
        rsp.season_year,
        rsp.team_key,
        rsp.yahoo_player_key
    FROM nffl.roster_snapshot_player rsp
),
any_team_roster AS (
    SELECT DISTINCT
        rsp.snapshot_id,
        rsp.league_key,
        rsp.season_year,
        rsp.team_key,
        t.team_name,
        rsp.yahoo_player_key
    FROM nffl.roster_snapshot_player rsp
    LEFT JOIN nffl.team t
      ON t.league_key = rsp.league_key
     AND t.season_year = rsp.season_year
     AND t.team_key = rsp.team_key
)
SELECT
    m.import_batch,
    m.league_key,
    m.season_year,
    m.sheet_name,
    m.owner_name,
    m.sheet_team_name,
    m.team_key,
    t.team_name,
    m.excel_row,
    m.player_name,
    m.raw_2025,
    m.status AS import_status,
    m.years_remaining_2026,
    m.yahoo_player_key,
    m.matched_full_name,
    m.match_status,
    m.matched_by_alias,
    snap.snapshot_id AS roster_snapshot_id,
    snap.source_season_year,
    str.team_key AS same_team_roster_team_key,
    atr.team_key AS any_roster_team_key,
    atr.team_name AS any_roster_team_name,
    CASE
        WHEN m.match_status = 'UNMATCHED' THEN 'BLOCKED_PLAYER_UNMATCHED'
        WHEN m.match_status = 'AMBIGUOUS' THEN 'BLOCKED_PLAYER_AMBIGUOUS'
        WHEN m.match_status NOT IN ('MATCHED_EXACT_NORMALIZED', 'MATCHED_ALIAS') THEN 'BLOCKED_PLAYER_MATCH_STATUS'
        WHEN m.status <> 'ACTIVE_CONTRACT' THEN 'NON_ACTIVE_CONTRACT_IMPORT_ROW'
        WHEN m.years_remaining_2026 <= 0 THEN 'NON_ACTIVE_ZERO_YEARS_REMAINING'
        WHEN snap.snapshot_id IS NULL THEN 'BLOCKED_NO_END_ROSTER_SNAPSHOT'
        WHEN str.yahoo_player_key IS NOT NULL THEN 'ACTIVE_ELIGIBLE_SAME_TEAM'
        WHEN atr.yahoo_player_key IS NOT NULL THEN 'BLOCKED_ROSTERED_BY_DIFFERENT_TEAM'
        ELSE 'BLOCKED_NOT_ON_END_ROSTER'
    END AS reconciliation_status,
    CASE
        WHEN m.match_status = 'UNMATCHED' THEN 'Contract-sheet player name did not match current Yahoo player universe.'
        WHEN m.match_status = 'AMBIGUOUS' THEN 'Contract-sheet player name matched multiple Yahoo player-universe rows.'
        WHEN m.match_status NOT IN ('MATCHED_EXACT_NORMALIZED', 'MATCHED_ALIAS') THEN 'Contract-sheet player has a non-accepted match status.'
        WHEN m.status <> 'ACTIVE_CONTRACT' THEN 'Contract-sheet row is not an active contract row.'
        WHEN m.years_remaining_2026 <= 0 THEN 'Contract-sheet row has zero or negative years remaining.'
        WHEN snap.snapshot_id IS NULL THEN 'No end-of-prior-season roster snapshot exists for this league/season.'
        WHEN str.yahoo_player_key IS NOT NULL THEN 'Player matched the same team in the end-of-prior-season roster snapshot.'
        WHEN atr.yahoo_player_key IS NOT NULL THEN 'Player was found on a different team in the end-of-prior-season roster snapshot.'
        ELSE 'Player was not found on any end-of-prior-season roster snapshot row for this league/season.'
    END AS reconciliation_reason
FROM m
LEFT JOIN nffl.team t
  ON t.league_key = m.league_key
 AND t.season_year = m.season_year
 AND t.team_key = m.team_key
LEFT JOIN snap
  ON snap.league_key = m.league_key
 AND snap.season_year = m.season_year
LEFT JOIN same_team_roster str
  ON str.snapshot_id = snap.snapshot_id
 AND str.league_key = m.league_key
 AND str.season_year = m.season_year
 AND str.team_key = m.team_key
 AND str.yahoo_player_key = m.yahoo_player_key
LEFT JOIN any_team_roster atr
  ON atr.snapshot_id = snap.snapshot_id
 AND atr.league_key = m.league_key
 AND atr.season_year = m.season_year
 AND atr.yahoo_player_key = m.yahoo_player_key
 AND atr.team_key <> m.team_key;

CREATE OR REPLACE VIEW nffl.v_contract_import_active_eligible AS
SELECT *
FROM nffl.v_contract_import_roster_reconciliation
WHERE import_status = 'ACTIVE_CONTRACT'
  AND reconciliation_status = 'ACTIVE_ELIGIBLE_SAME_TEAM';

CREATE OR REPLACE VIEW nffl.v_contract_import_active_blockers AS
SELECT *
FROM nffl.v_contract_import_roster_reconciliation
WHERE import_status = 'ACTIVE_CONTRACT'
  AND reconciliation_status <> 'ACTIVE_ELIGIBLE_SAME_TEAM';
