-- 01_schema.sql

CREATE TABLE IF NOT EXISTS ingest_snapshot (
  snapshot_id      TEXT PRIMARY KEY,          -- e.g. '2026-02-02__post-renewal'
  snapshot_type    TEXT NOT NULL,             -- 'contracts'
  snapshot_label   TEXT NOT NULL,             -- 'post-renewal'
  snapshot_path    TEXT NOT NULL,             -- filesystem path
  source_system    TEXT NOT NULL,             -- 'google_sheets_apps_script'
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Managers sheet (minimal, raw)
CREATE TABLE IF NOT EXISTS managers_raw_row (
  snapshot_id   TEXT NOT NULL REFERENCES ingest_snapshot(snapshot_id) ON DELETE CASCADE,
  row_number    INT  NOT NULL,
  row_values    TEXT[] NOT NULL,
  PRIMARY KEY (snapshot_id, row_number)
);

-- Contract cell truth table (append-only per snapshot)
CREATE TABLE IF NOT EXISTS contracts_cell_raw (
  snapshot_id    TEXT NOT NULL REFERENCES ingest_snapshot(snapshot_id) ON DELETE CASCADE,

  owner_name     TEXT NOT NULL,   -- derived from filename (e.g. 'Chris L')
  sheet_name     TEXT NOT NULL,   -- same as owner_name for now

  player_name    TEXT NOT NULL,
  season_year    INT  NOT NULL,   -- derived from header mapping (e.g. 2025, 2024...)

  raw_value      TEXT NOT NULL,   -- exact cell text
  row_number     INT  NOT NULL,   -- line number in CSV (1-based)
  col_index      INT  NOT NULL,   -- 0-based column index
  col_label      TEXT NOT NULL,   -- 'A','B','C'...

  PRIMARY KEY (snapshot_id, owner_name, sheet_name, player_name, season_year, row_number, col_index)
);

CREATE INDEX IF NOT EXISTS ix_contracts_cell_raw_lookup
  ON contracts_cell_raw (snapshot_id, owner_name, player_name, season_year);

-- Minimal parsed fields (safe, optional)
CREATE TABLE IF NOT EXISTS contracts_cell_parsed (
  snapshot_id    TEXT NOT NULL REFERENCES ingest_snapshot(snapshot_id) ON DELETE CASCADE,
  owner_name     TEXT NOT NULL,
  sheet_name     TEXT NOT NULL,
  player_name    TEXT NOT NULL,
  season_year    INT  NOT NULL,

  years_remaining INT NULL,
  contract_label  TEXT NULL,      -- 'FT','PT', or NULL
  event_flags     TEXT[] NULL,    -- e.g. {'to','from','fa','waiver'}

  PRIMARY KEY (snapshot_id, owner_name, sheet_name, player_name, season_year)
);

CREATE OR REPLACE VIEW v_latest_contracts_snapshot AS
SELECT snapshot_id
FROM ingest_snapshot
WHERE snapshot_type = 'contracts'
ORDER BY ingested_at DESC
LIMIT 1;
