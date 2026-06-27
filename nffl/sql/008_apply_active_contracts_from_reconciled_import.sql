\echo '=== ASSERT ACTIVE CONTRACT IMPORT HAS NO ROSTER RECONCILIATION BLOCKERS ==='

DO $$
DECLARE
    blocker_count integer;
BEGIN
    SELECT count(*)
    INTO blocker_count
    FROM nffl.v_contract_import_active_blockers;

    IF blocker_count > 0 THEN
        RAISE EXCEPTION
            'Refusing active contract import: % active contract staging rows failed roster reconciliation. Review nffl.v_contract_import_active_blockers.',
            blocker_count;
    END IF;
END $$;

\echo '=== UPSERT ACTIVE CONTRACTS FROM ROSTER-RECONCILED IMPORT ==='

INSERT INTO nffl.contract (
    league_key,
    season_year,
    team_key,
    yahoo_player_key,
    contract_years_remaining,
    contract_source,
    source_snapshot_id,
    status,
    note,
    created_at_utc,
    updated_at_utc
)
SELECT
    league_key,
    season_year,
    team_key,
    yahoo_player_key,
    years_remaining_2026,
    import_batch,
    roster_snapshot_id,
    'active',
    'Loaded from contract import after same-team end-of-prior-season roster reconciliation.',
    now(),
    now()
FROM nffl.v_contract_import_active_eligible
ON CONFLICT (league_key, season_year, yahoo_player_key)
DO UPDATE SET
    team_key = EXCLUDED.team_key,
    contract_years_remaining = EXCLUDED.contract_years_remaining,
    contract_source = EXCLUDED.contract_source,
    source_snapshot_id = EXCLUDED.source_snapshot_id,
    status = 'active',
    note = EXCLUDED.note,
    updated_at_utc = now();

\echo '=== ACTIVE CONTRACT IMPORT COMPLETE ==='

SELECT
    import_batch,
    count(*) AS eligible_active_contract_rows
FROM nffl.v_contract_import_active_eligible
GROUP BY import_batch
ORDER BY import_batch;
