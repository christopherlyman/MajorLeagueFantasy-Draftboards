-- NFFL finalized roster-snapshot immutability.
--
-- Once a roster snapshot has a row in nffl.roster_snapshot_finalization:
--   * snapshot header metadata may not be changed or deleted;
--   * normalized roster evidence may not be inserted, changed, or deleted;
--   * raw Yahoo source evidence may not be inserted, changed, or deleted;
--   * the finalization row itself may not be changed or deleted.
--
-- Finalization insertion also validates that its recorded row counts match
-- the evidence that actually exists at the time of finalization.
--
-- Corrections to finalized evidence require a new snapshot rather than
-- mutation or "unfinalization" of an existing audit artifact.


CREATE OR REPLACE FUNCTION nffl.prevent_finalized_roster_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_snapshot_id text;
    new_snapshot_id text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_snapshot_id := OLD.snapshot_id;
    END IF;

    IF TG_OP <> 'DELETE' THEN
        new_snapshot_id := NEW.snapshot_id;
    END IF;

    IF old_snapshot_id IS NOT NULL
       AND EXISTS (
            SELECT 1
            FROM nffl.roster_snapshot_finalization f
            WHERE f.snapshot_id = old_snapshot_id
       )
    THEN
        RAISE EXCEPTION
            'Snapshot % is finalized and its evidence cannot be modified.',
            old_snapshot_id
            USING ERRCODE = '55000';
    END IF;

    IF new_snapshot_id IS NOT NULL
       AND new_snapshot_id IS DISTINCT FROM old_snapshot_id
       AND EXISTS (
            SELECT 1
            FROM nffl.roster_snapshot_finalization f
            WHERE f.snapshot_id = new_snapshot_id
       )
    THEN
        RAISE EXCEPTION
            'Snapshot % is finalized and its evidence cannot be modified.',
            new_snapshot_id
            USING ERRCODE = '55000';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$;


DROP TRIGGER IF EXISTS trg_roster_snapshot_finalized_immutable
    ON nffl.roster_snapshot;

CREATE TRIGGER trg_roster_snapshot_finalized_immutable
BEFORE INSERT OR UPDATE OR DELETE
ON nffl.roster_snapshot
FOR EACH ROW
EXECUTE FUNCTION nffl.prevent_finalized_roster_snapshot_mutation();


DROP TRIGGER IF EXISTS trg_roster_snapshot_player_finalized_immutable
    ON nffl.roster_snapshot_player;

CREATE TRIGGER trg_roster_snapshot_player_finalized_immutable
BEFORE INSERT OR UPDATE OR DELETE
ON nffl.roster_snapshot_player
FOR EACH ROW
EXECUTE FUNCTION nffl.prevent_finalized_roster_snapshot_mutation();


DROP TRIGGER IF EXISTS trg_roster_snapshot_source_player_finalized_immutable
    ON nffl.roster_snapshot_source_player;

CREATE TRIGGER trg_roster_snapshot_source_player_finalized_immutable
BEFORE INSERT OR UPDATE OR DELETE
ON nffl.roster_snapshot_source_player
FOR EACH ROW
EXECUTE FUNCTION nffl.prevent_finalized_roster_snapshot_mutation();


CREATE OR REPLACE FUNCTION nffl.validate_roster_snapshot_finalization()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    actual_source_count integer;
    actual_normalized_count integer;
    actual_unmapped_count integer;
BEGIN
    SELECT count(*)
      INTO actual_source_count
      FROM nffl.roster_snapshot_source_player
     WHERE snapshot_id = NEW.snapshot_id;

    SELECT count(*)
      INTO actual_normalized_count
      FROM nffl.roster_snapshot_player
     WHERE snapshot_id = NEW.snapshot_id;

    SELECT count(*)
      INTO actual_unmapped_count
      FROM nffl.roster_snapshot_source_player
     WHERE snapshot_id = NEW.snapshot_id
       AND mapping_status = 'NOT_IN_CURRENT_UNIVERSE';

    IF actual_source_count = 0 THEN
        RAISE EXCEPTION
            'Snapshot % cannot be finalized with zero raw source evidence rows.',
            NEW.snapshot_id
            USING ERRCODE = '23514';
    END IF;

    IF NEW.source_player_count <> actual_source_count THEN
        RAISE EXCEPTION
            'Snapshot % source count mismatch: finalization says %, evidence contains %.',
            NEW.snapshot_id,
            NEW.source_player_count,
            actual_source_count
            USING ERRCODE = '23514';
    END IF;

    IF NEW.normalized_player_count <> actual_normalized_count THEN
        RAISE EXCEPTION
            'Snapshot % normalized count mismatch: finalization says %, evidence contains %.',
            NEW.snapshot_id,
            NEW.normalized_player_count,
            actual_normalized_count
            USING ERRCODE = '23514';
    END IF;

    IF NEW.unmapped_player_count <> actual_unmapped_count THEN
        RAISE EXCEPTION
            'Snapshot % unmapped count mismatch: finalization says %, evidence contains %.',
            NEW.snapshot_id,
            NEW.unmapped_player_count,
            actual_unmapped_count
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;


DROP TRIGGER IF EXISTS trg_roster_snapshot_finalization_validate
    ON nffl.roster_snapshot_finalization;

CREATE TRIGGER trg_roster_snapshot_finalization_validate
BEFORE INSERT
ON nffl.roster_snapshot_finalization
FOR EACH ROW
EXECUTE FUNCTION nffl.validate_roster_snapshot_finalization();


CREATE OR REPLACE FUNCTION nffl.prevent_roster_snapshot_unfinalization()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'Snapshot % is finalized; finalization records are immutable.',
        OLD.snapshot_id
        USING ERRCODE = '55000';
END;
$$;


DROP TRIGGER IF EXISTS trg_roster_snapshot_finalization_immutable
    ON nffl.roster_snapshot_finalization;

CREATE TRIGGER trg_roster_snapshot_finalization_immutable
BEFORE UPDATE OR DELETE
ON nffl.roster_snapshot_finalization
FOR EACH ROW
EXECUTE FUNCTION nffl.prevent_roster_snapshot_unfinalization();


COMMENT ON FUNCTION nffl.prevent_finalized_roster_snapshot_mutation()
IS 'Prevents mutation of roster snapshot header or evidence after explicit finalization.';

COMMENT ON FUNCTION nffl.validate_roster_snapshot_finalization()
IS 'Validates finalization counts against the roster evidence present at finalization time.';

COMMENT ON FUNCTION nffl.prevent_roster_snapshot_unfinalization()
IS 'Makes roster snapshot finalization records immutable; corrections require a new snapshot.';