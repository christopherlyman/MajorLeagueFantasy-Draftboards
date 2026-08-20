-- NFFL contract source-snapshot provenance hardening.
--
-- Semantics:
--
--   contract.contract_source
--       Identifies HOW the operational contract row was created.
--
--   contract.source_snapshot_id
--       Optional finalized roster snapshot used as evidence when importing
--       or carrying forward the contract state.
--
--       This column must not contain an import-batch identifier, draft key,
--       or other non-snapshot provenance.
--
--   contract_history_episode
--       Stores post-draft award provenance such as draft_key, source pick,
--       revision, publisher, and awarded contract length.
--
-- Historical repair:
--   The original 2026 contract rows predate the roster-reconciliation gate
--   and contain the import batch identifier 2026_from_2025_sheet_v1 in
--   source_snapshot_id. Their actual roster evidence is the finalized
--   nffl_2026_from_2025_end_roster snapshot.
--
-- This migration is intentionally transaction-neutral. The caller controls
-- BEGIN / COMMIT during rehearsal and deployment.


DO $$
DECLARE
    finalized_snapshot_count integer;
    imported_contract_count integer;
    legacy_source_value_count integer;
    active_same_team_count integer;
    void_not_rostered_count integer;
BEGIN
    SELECT count(*)
      INTO finalized_snapshot_count
      FROM nffl.roster_snapshot_finalization
     WHERE snapshot_id = 'nffl_2026_from_2025_end_roster';

    IF finalized_snapshot_count <> 1 THEN
        RAISE EXCEPTION
            'Expected finalized snapshot nffl_2026_from_2025_end_roster; found %.',
            finalized_snapshot_count;
    END IF;


    SELECT count(*)
      INTO imported_contract_count
      FROM nffl.contract
     WHERE league_key = '470.l.84346'
       AND season_year = 2026
       AND contract_source = '2026_from_2025_sheet_v1';

    IF imported_contract_count <> 63 THEN
        RAISE EXCEPTION
            'Expected 63 legacy 2026 imported contract rows; found %.',
            imported_contract_count;
    END IF;


    SELECT count(*)
      INTO legacy_source_value_count
      FROM nffl.contract
     WHERE league_key = '470.l.84346'
       AND season_year = 2026
       AND contract_source = '2026_from_2025_sheet_v1'
       AND source_snapshot_id = '2026_from_2025_sheet_v1';

    IF legacy_source_value_count NOT IN (0, 63) THEN
        RAISE EXCEPTION
            'Legacy source_snapshot_id repair is in an unexpected partial state: % of 63 rows still contain the import batch.',
            legacy_source_value_count;
    END IF;


    SELECT count(*)
      INTO active_same_team_count
      FROM nffl.contract c
      JOIN nffl.v_contract_import_roster_reconciliation r
        ON r.league_key = c.league_key
       AND r.season_year = c.season_year
       AND r.team_key = c.team_key
       AND r.yahoo_player_key = c.yahoo_player_key
       AND r.import_status = 'ACTIVE_CONTRACT'
     WHERE c.league_key = '470.l.84346'
       AND c.season_year = 2026
       AND c.contract_source = '2026_from_2025_sheet_v1'
       AND c.status = 'active'
       AND r.reconciliation_status = 'ACTIVE_ELIGIBLE_SAME_TEAM';

    IF active_same_team_count <> 61 THEN
        RAISE EXCEPTION
            'Expected 61 active same-team reconciled contracts; found %.',
            active_same_team_count;
    END IF;


    SELECT count(*)
      INTO void_not_rostered_count
      FROM nffl.contract c
      JOIN nffl.v_contract_import_roster_reconciliation r
        ON r.league_key = c.league_key
       AND r.season_year = c.season_year
       AND r.team_key = c.team_key
       AND r.yahoo_player_key = c.yahoo_player_key
       AND r.import_status = 'ACTIVE_CONTRACT'
     WHERE c.league_key = '470.l.84346'
       AND c.season_year = 2026
       AND c.contract_source = '2026_from_2025_sheet_v1'
       AND c.status = 'void'
       AND r.reconciliation_status = 'BLOCKED_NOT_ON_END_ROSTER';

    IF void_not_rostered_count <> 2 THEN
        RAISE EXCEPTION
            'Expected 2 void not-on-end-roster contracts; found %.',
            void_not_rostered_count;
    END IF;
END
$$;


UPDATE nffl.contract
   SET source_snapshot_id = 'nffl_2026_from_2025_end_roster'
 WHERE league_key = '470.l.84346'
   AND season_year = 2026
   AND contract_source = '2026_from_2025_sheet_v1'
   AND source_snapshot_id = '2026_from_2025_sheet_v1';


DO $$
DECLARE
    repaired_count integer;
    invalid_nonnull_count integer;
BEGIN
    SELECT count(*)
      INTO repaired_count
      FROM nffl.contract
     WHERE league_key = '470.l.84346'
       AND season_year = 2026
       AND contract_source = '2026_from_2025_sheet_v1'
       AND source_snapshot_id = 'nffl_2026_from_2025_end_roster';

    IF repaired_count <> 63 THEN
        RAISE EXCEPTION
            'Expected all 63 imported contracts to reference the finalized roster snapshot after repair; found %.',
            repaired_count;
    END IF;


    SELECT count(*)
      INTO invalid_nonnull_count
      FROM nffl.contract c
      LEFT JOIN nffl.roster_snapshot_finalization f
        ON f.snapshot_id = c.source_snapshot_id
     WHERE c.source_snapshot_id IS NOT NULL
       AND f.snapshot_id IS NULL;

    IF invalid_nonnull_count <> 0 THEN
        RAISE EXCEPTION
            'Cannot enforce contract source-snapshot provenance: % non-null source_snapshot_id values do not reference finalized snapshots.',
            invalid_nonnull_count;
    END IF;
END
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'nffl.contract'::regclass
           AND conname = 'contract_source_snapshot_finalized_fk'
    ) THEN
        ALTER TABLE nffl.contract
            ADD CONSTRAINT contract_source_snapshot_finalized_fk
            FOREIGN KEY (source_snapshot_id)
            REFERENCES nffl.roster_snapshot_finalization(snapshot_id);
    END IF;
END
$$;


COMMENT ON COLUMN nffl.contract.source_snapshot_id
IS 'Optional finalized roster snapshot used as evidence when importing or carrying forward this operational contract state. NULL for newly awarded contracts whose provenance is stored elsewhere, such as nffl.contract_history_episode.';