-- MLF Relational Migration - 013
-- Persist source-workbook episode section for Contract History.
--
-- "current" and "past" describe workbook source sections only.
-- They do not replace operational contract/PT authority.
--
-- The new column is intentionally NOT NULL without a default.
-- Applying this migration over pre-existing history rows fails
-- closed rather than fabricating an episode classification.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


ALTER TABLE mlf.contract_history_cell
ADD COLUMN source_section text NOT NULL;


ALTER TABLE mlf.contract_history_cell
ADD CONSTRAINT contract_history_cell_source_section_ck
CHECK (
    source_section IN (
        'current',
        'past'
    )
);


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '013',
    'Persist Contract History workbook source section'
);


COMMIT;
