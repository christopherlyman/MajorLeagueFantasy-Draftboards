-- MLF Relational Migration - 011
-- Canonical source-grounded MLF contract history storage.
--
-- One source row represents one historical contract episode.
-- Repeated player rows remain distinct by source provenance.
--
-- Current operational contract/PT authority remains separate.
-- This table stores historical evidence only.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';


CREATE TABLE mlf.contract_history_cell (
    source_id text NOT NULL,
    source_kind text NOT NULL,
    source_sha256 text,

    source_sheet_name text NOT NULL,
    source_row_number integer NOT NULL,
    source_col_index integer NOT NULL,
    source_col_label text NOT NULL,

    franchise_id bigint NOT NULL,

    player_name text NOT NULL,
    yahoo_player_key text,

    season_year integer NOT NULL,

    raw_value text NOT NULL,

    years_remaining integer,
    contract_label text,
    event_flags text[] NOT NULL
        DEFAULT ARRAY[]::text[],

    is_year_marker boolean NOT NULL
        DEFAULT false,

    loaded_at_utc timestamp with time zone NOT NULL
        DEFAULT now(),

    CONSTRAINT contract_history_cell_pkey
        PRIMARY KEY (
            source_id,
            source_sheet_name,
            source_row_number,
            season_year
        ),

    CONSTRAINT contract_history_cell_franchise_fk
        FOREIGN KEY (franchise_id)
        REFERENCES public.franchise(franchise_id),

    CONSTRAINT contract_history_cell_source_id_ck
        CHECK (btrim(source_id) <> ''),

    CONSTRAINT contract_history_cell_source_kind_ck
        CHECK (btrim(source_kind) <> ''),

    CONSTRAINT contract_history_cell_source_sha256_ck
        CHECK (
            source_sha256 IS NULL
            OR source_sha256 ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT contract_history_cell_sheet_name_ck
        CHECK (btrim(source_sheet_name) <> ''),

    CONSTRAINT contract_history_cell_row_number_ck
        CHECK (source_row_number > 0),

    CONSTRAINT contract_history_cell_col_index_ck
        CHECK (source_col_index > 0),

    CONSTRAINT contract_history_cell_col_label_ck
        CHECK (btrim(source_col_label) <> ''),

    CONSTRAINT contract_history_cell_player_name_ck
        CHECK (btrim(player_name) <> ''),

    CONSTRAINT contract_history_cell_yahoo_player_key_ck
        CHECK (
            yahoo_player_key IS NULL
            OR btrim(yahoo_player_key) <> ''
        ),

    CONSTRAINT contract_history_cell_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    CONSTRAINT contract_history_cell_raw_value_ck
        CHECK (btrim(raw_value) <> ''),

    CONSTRAINT contract_history_cell_years_remaining_ck
        CHECK (
            years_remaining IS NULL
            OR years_remaining BETWEEN 1 AND 5
        ),

    CONSTRAINT contract_history_cell_contract_label_ck
        CHECK (
            contract_label IS NULL
            OR contract_label IN ('FT', 'PT')
        ),

    CONSTRAINT contract_history_cell_year_marker_ck
        CHECK (
            is_year_marker =
                (
                    btrim(raw_value) =
                    season_year::text
                )
        ),

    CONSTRAINT contract_history_cell_year_marker_payload_ck
        CHECK (
            NOT is_year_marker
            OR (
                years_remaining IS NULL
                AND contract_label IS NULL
                AND cardinality(event_flags) = 0
            )
        )
);


CREATE INDEX ix_mlf_contract_history_cell_franchise
ON mlf.contract_history_cell (
    franchise_id,
    season_year DESC,
    player_name,
    source_row_number
);


INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '011',
    'Canonical source-grounded MLF contract history storage'
);


COMMIT;
