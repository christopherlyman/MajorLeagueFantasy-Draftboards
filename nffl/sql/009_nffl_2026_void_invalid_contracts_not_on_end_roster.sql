\echo '=== NFFL 2026 VOID INVALID ACTIVE CONTRACTS NOT ON END-OF-PRIOR-SEASON ROSTER ==='

BEGIN;

WITH target_invalid_contracts AS (
    SELECT
        c.league_key,
        c.season_year,
        c.team_key,
        c.yahoo_player_key,
        t.team_name,
        pu.full_name
    FROM nffl.contract c
    JOIN nffl.player_universe pu
      ON pu.league_key = c.league_key
     AND pu.season_year = c.season_year
     AND pu.yahoo_player_key = c.yahoo_player_key
    JOIN nffl.team t
      ON t.league_key = c.league_key
     AND t.season_year = c.season_year
     AND t.team_key = c.team_key
    LEFT JOIN nffl.roster_snapshot_player rsp
      ON rsp.league_key = c.league_key
     AND rsp.season_year = c.season_year
     AND rsp.team_key = c.team_key
     AND rsp.yahoo_player_key = c.yahoo_player_key
    WHERE c.league_key = '470.l.84346'
      AND c.season_year = 2026
      AND rsp.yahoo_player_key IS NULL
      AND (
          (c.team_key = '470.l.84346.t.3' AND pu.full_name = 'Christian Kirk')
          OR
          (c.team_key = '470.l.84346.t.1' AND pu.full_name = 'Keon Coleman')
      )
),
updated AS (
    UPDATE nffl.contract c
       SET status = 'void',
           note = CASE
               WHEN coalesce(c.note, '') LIKE '%Voided: player was not on the same team in 2025 end-of-prior-season roster snapshot.%'
                   THEN c.note
               ELSE concat_ws(
                   ' | ',
                   nullif(btrim(c.note), ''),
                   'Voided: player was not on the same team in 2025 end-of-prior-season roster snapshot.'
               )
           END,
           updated_at_utc = now()
      FROM target_invalid_contracts tic
     WHERE c.league_key = tic.league_key
       AND c.season_year = tic.season_year
       AND c.team_key = tic.team_key
       AND c.yahoo_player_key = tic.yahoo_player_key
     RETURNING
        c.league_key,
        c.season_year,
        c.team_key,
        c.yahoo_player_key,
        c.status,
        c.note
)
SELECT * FROM updated
ORDER BY team_key, yahoo_player_key;

COMMIT;

\echo '=== INVALID ACTIVE CONTRACTS REMAINING ==='

WITH active_contracts AS (
    SELECT
        c.league_key,
        c.season_year,
        c.team_key,
        t.team_name,
        c.yahoo_player_key,
        pu.full_name,
        c.contract_years_remaining,
        c.status
    FROM nffl.contract c
    LEFT JOIN nffl.team t
      ON t.league_key = c.league_key
     AND t.season_year = c.season_year
     AND t.team_key = c.team_key
    LEFT JOIN nffl.player_universe pu
      ON pu.league_key = c.league_key
     AND pu.season_year = c.season_year
     AND pu.yahoo_player_key = c.yahoo_player_key
    WHERE c.league_key = '470.l.84346'
      AND c.season_year = 2026
      AND c.status = 'active'
),
rostered_same_team AS (
    SELECT DISTINCT
        rsp.league_key,
        rsp.season_year,
        rsp.team_key,
        rsp.yahoo_player_key
    FROM nffl.roster_snapshot_player rsp
    WHERE rsp.league_key = '470.l.84346'
      AND rsp.season_year = 2026
)
SELECT
    ac.team_name,
    ac.full_name,
    ac.yahoo_player_key,
    ac.contract_years_remaining,
    ac.status
FROM active_contracts ac
LEFT JOIN rostered_same_team r
  ON r.league_key = ac.league_key
 AND r.season_year = ac.season_year
 AND r.team_key = ac.team_key
 AND r.yahoo_player_key = ac.yahoo_player_key
WHERE r.yahoo_player_key IS NULL
ORDER BY ac.team_name, ac.full_name;
