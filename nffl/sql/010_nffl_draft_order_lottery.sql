BEGIN;

CREATE TABLE IF NOT EXISTS nffl.draft_order_lottery_run (
    run_id text PRIMARY KEY,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    draft_key text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'COMPLETE', 'APPLIED', 'VOID')),
    champion_team_key text NOT NULL,
    created_by text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    completed_at_utc timestamptz,
    applied_at_utc timestamptz,
    applied_by text,
    audit_hash text,
    note text
);

CREATE TABLE IF NOT EXISTS nffl.draft_order_lottery_pick (
    run_id text NOT NULL REFERENCES nffl.draft_order_lottery_run(run_id) ON DELETE CASCADE,
    pick_number integer NOT NULL CHECK (pick_number BETWEEN 1 AND 12),
    team_key text NOT NULL,
    pool_type text NOT NULL CHECK (pool_type IN ('CHAMPION', 'PLAYOFF', 'CONSOLATION')),
    reveal_order integer NOT NULL CHECK (reveal_order BETWEEN 1 AND 12),
    is_revealed boolean NOT NULL DEFAULT false,
    revealed_at_utc timestamptz,
    revealed_by text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, pick_number),
    UNIQUE (run_id, team_key),
    UNIQUE (run_id, reveal_order)
);

CREATE INDEX IF NOT EXISTS ix_draft_order_lottery_run_league_year_draft
    ON nffl.draft_order_lottery_run (league_key, season_year, draft_key, created_at_utc DESC);

CREATE INDEX IF NOT EXISTS ix_draft_order_lottery_pick_run_reveal
    ON nffl.draft_order_lottery_pick (run_id, reveal_order);

COMMIT;
