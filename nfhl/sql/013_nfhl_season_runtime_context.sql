BEGIN;

-- ================================================================
-- NFHL DRAFT CONFIG
--
-- Persist the complete season/draft configuration that previously
-- lived only in config/nfhl_<season>.json.
--
-- One immutable historical configuration belongs to each draft.
-- Future season creation will insert a new row here; no annual SQL
-- migration or source-code change should be required.
-- ================================================================

CREATE TABLE nfhl.draft_config (
    draft_key           text        PRIMARY KEY,
    config_version      integer     NOT NULL DEFAULT 1,
    config_json         jsonb       NOT NULL,

    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_config_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_config_version_ck
        CHECK (config_version > 0),

    CONSTRAINT draft_config_json_object_ck
        CHECK (jsonb_typeof(config_json) = 'object'),

    CONSTRAINT draft_config_required_sections_ck
        CHECK (
            config_json ? 'league'
            AND config_json ? 'draft'
            AND config_json ? 'features'
            AND config_json ? 'roster'
            AND config_json ? 'scoring'
            AND config_json ? 'yahoo'
            AND config_json ? 'historical_stats'
        )
);

COMMENT ON TABLE nfhl.draft_config IS
'Versioned per-draft NFHL season configuration. This replaces annual nfhl_<season>.json files as the authoritative runtime configuration source.';

COMMENT ON COLUMN nfhl.draft_config.config_json IS
'Complete configuration snapshot for this draft/season, including league, draft, roster, scoring, Yahoo, and historical-stat settings.';


-- ================================================================
-- NFHL RUNTIME CONTEXT
--
-- Select which persisted draft/season the NFHL application should
-- operate against.
--
-- This is deliberately separate from nfhl.draft.status:
--   draft.status = lifecycle of that historical draft
--   runtime_context = which draft the application is displaying
--
-- Exactly one NFHL row is expected.
-- ================================================================

CREATE TABLE nfhl.runtime_context (
    context_key         text        PRIMARY KEY,
    active_draft_key    text        NOT NULL,

    updated_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_by          text        NOT NULL,

    CONSTRAINT runtime_context_key_ck
        CHECK (context_key = 'NFHL'),

    CONSTRAINT runtime_context_draft_fk
        FOREIGN KEY (active_draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE RESTRICT
);

COMMENT ON TABLE nfhl.runtime_context IS
'Singleton application context identifying the persisted NFHL draft/season currently served by DraftBoard.';

COMMENT ON COLUMN nfhl.runtime_context.active_draft_key IS
'Current NFHL operating draft. Historical draft lifecycle status is independent of this pointer.';


-- ================================================================
-- BACKFILL CURRENT 2026 CONFIGURATION
--
-- Historical bootstrap only. Future seasons must be created through
-- the season-creation workflow rather than new seed migrations.
-- ================================================================

INSERT INTO nfhl.draft_config (
    draft_key,
    config_version,
    config_json
)
VALUES (
    'nfhl_2026_preseason',
    1,
    $json$
    {
      "league": {
        "name": "NFHL",
        "sport": "nhl",
        "platform": "yahoo",
        "season_year": 2026,
        "league_id": "10961",
        "league_key": "477.l.10961",
        "manager_count_target": 14
      },
      "draft": {
        "draft_key": "nfhl_2026_preseason",
        "rounds_total": 18,
        "opening_day_date": "2026-09-29",
        "rounds_total_status": "inferred_from_active_plus_bench_roster",
        "order_mode": "snake",
        "order_mode_status": "verified"
      },
      "features": {
        "keeper": false,
        "contracts": false,
        "qualifying_offers": false,
        "franchise_tags": false,
        "prospect_tags": false,
        "poaching": false
      },
      "roster": {
        "C": 2,
        "LW": 2,
        "RW": 2,
        "F": 1,
        "D": 4,
        "Util": 1,
        "G": 2,
        "BN": 4,
        "IR+": 2,
        "NA": 1
      },
      "scoring": {
        "G": 4.0,
        "A": 2.5,
        "PIM": 0.2,
        "PPP": 1.0,
        "SHP": 1.25,
        "SOG": 0.25,
        "HIT": 0.5,
        "BLK": 0.5,
        "W": 3.0,
        "GA": -1.0,
        "SV": 0.25,
        "SHO": 2.5
      },
      "yahoo": {
        "game_key": "477"
      },
      "historical_stats": {
        "season_year": 2025,
        "game_key": "465",
        "coverage_type": "season"
      }
    }
    $json$::jsonb
)
ON CONFLICT (draft_key)
DO NOTHING;


-- ================================================================
-- BACKFILL CURRENT RUNTIME POINTER
-- ================================================================

INSERT INTO nfhl.runtime_context (
    context_key,
    active_draft_key,
    updated_by
)
VALUES (
    'NFHL',
    'nfhl_2026_preseason',
    'migration_013'
)
ON CONFLICT (context_key)
DO NOTHING;


-- ================================================================
-- BACKFILL INTEGRITY
-- ================================================================

DO $$
DECLARE
    v_draft_count integer;
    v_config_count integer;
    v_context_count integer;
BEGIN
    SELECT COUNT(*)
    INTO v_draft_count
    FROM nfhl.draft
    WHERE draft_key = 'nfhl_2026_preseason';

    IF v_draft_count <> 1 THEN
        RAISE EXCEPTION
            'Migration 013 expected exactly one existing 2026 NFHL draft; found %',
            v_draft_count;
    END IF;

    SELECT COUNT(*)
    INTO v_config_count
    FROM nfhl.draft_config
    WHERE draft_key = 'nfhl_2026_preseason';

    IF v_config_count <> 1 THEN
        RAISE EXCEPTION
            'Migration 013 expected exactly one 2026 draft_config row; found %',
            v_config_count;
    END IF;

    SELECT COUNT(*)
    INTO v_context_count
    FROM nfhl.runtime_context
    WHERE context_key = 'NFHL'
      AND active_draft_key = 'nfhl_2026_preseason';

    IF v_context_count <> 1 THEN
        RAISE EXCEPTION
            'Migration 013 expected one NFHL runtime context pointing to the 2026 draft; found %',
            v_context_count;
    END IF;
END
$$;


COMMIT;
