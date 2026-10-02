--
-- Explicit authority for immutable MLF Contract History sources.
--
-- Source versions remain preserved by source_id.
-- Authority is explicit; ingestion time is not authority.
--

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


CREATE TABLE mlf.contract_history_source (
    source_id text PRIMARY KEY,

    source_kind text NOT NULL,

    source_sha256 text,

    is_active boolean NOT NULL
        DEFAULT false,

    imported_at_utc timestamp with time zone NOT NULL
        DEFAULT now(),

    activated_at_utc timestamp with time zone,

    CONSTRAINT contract_history_source_source_id_ck
        CHECK (btrim(source_id) <> ''),

    CONSTRAINT contract_history_source_source_kind_ck
        CHECK (btrim(source_kind) <> ''),

    CONSTRAINT contract_history_source_sha256_ck
        CHECK (
            source_sha256 IS NULL
            OR source_sha256 ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT contract_history_source_activation_ck
        CHECK (
            NOT is_active
            OR activated_at_utc IS NOT NULL
        )
);


CREATE UNIQUE INDEX
    uq_mlf_contract_history_source_active_kind
ON mlf.contract_history_source (
    source_kind
)
WHERE is_active;


ALTER TABLE mlf.contract_history_cell
ADD CONSTRAINT contract_history_cell_source_fk
FOREIGN KEY (source_id)
REFERENCES mlf.contract_history_source(source_id)
ON DELETE RESTRICT;


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '012',
    'Explicit authority for immutable MLF contract history sources'
);


COMMIT;
