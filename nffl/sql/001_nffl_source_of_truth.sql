CREATE SCHEMA IF NOT EXISTS nffl;

CREATE TABLE IF NOT EXISTS nffl.team (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    team_id text,
    team_name text NOT NULL,
    owner_name text,
    owner_guid text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, team_key)
);

CREATE TABLE IF NOT EXISTS nffl.player_universe (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    yahoo_player_key text NOT NULL,
    source_game_key text NOT NULL,
    full_name text NOT NULL,
    nfl_team_abbr text,
    eligible_positions jsonb NOT NULL DEFAULT '[]'::jsonb,
    percent_owned numeric,
    rank_value numeric,
    raw_payload jsonb,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, yahoo_player_key)
);

CREATE TABLE IF NOT EXISTS nffl.roster_snapshot (
    snapshot_id text PRIMARY KEY,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    source_season_year integer NOT NULL,
    snapshot_type text NOT NULL,
    snapshot_label text NOT NULL,
    snapshot_as_of_date date,
    source_note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league_key, season_year, source_season_year, snapshot_type)
);

CREATE TABLE IF NOT EXISTS nffl.roster_snapshot_player (
    snapshot_id text NOT NULL REFERENCES nffl.roster_snapshot(snapshot_id) ON DELETE CASCADE,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    roster_slot text,
    roster_status text,
    source_note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, team_key, yahoo_player_key)
);

CREATE TABLE IF NOT EXISTS nffl.contract (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    contract_years_remaining integer NOT NULL,
    contract_source text NOT NULL DEFAULT 'manual',
    source_snapshot_id text,
    status text NOT NULL DEFAULT 'active',
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, yahoo_player_key),
    CHECK (contract_years_remaining >= 0),
    CHECK (status IN ('active', 'expired', 'void', 'needs_review'))
);

CREATE TABLE IF NOT EXISTS nffl.franchise_tag_history (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    tag_status text NOT NULL DEFAULT 'applied',
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, team_key, yahoo_player_key),
    CHECK (tag_status IN ('applied', 'expired', 'void'))
);

CREATE TABLE IF NOT EXISTS nffl.qualifying_offer (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    qo_level integer NOT NULL,
    status text NOT NULL DEFAULT 'submitted',
    source text NOT NULL DEFAULT 'commissioner',
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, team_key, qo_level),
    UNIQUE (league_key, season_year, yahoo_player_key),
    CHECK (qo_level BETWEEN 1 AND 4),
    CHECK (status IN ('submitted', 'retained', 'poached', 'released', 'void'))
);

CREATE TABLE IF NOT EXISTS nffl.draft (
    draft_key text PRIMARY KEY,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    draft_label text NOT NULL,
    manager_count integer NOT NULL,
    rounds_total integer NOT NULL,
    qo_rounds integer NOT NULL,
    first_standard_round integer NOT NULL,
    draft_order_mode text NOT NULL DEFAULT 'straight',
    status text NOT NULL DEFAULT 'setup',
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    CHECK (manager_count > 0),
    CHECK (rounds_total > 0),
    CHECK (qo_rounds >= 0),
    CHECK (first_standard_round = qo_rounds + 1),
    CHECK (status IN ('setup', 'active', 'complete', 'archived'))
);

CREATE TABLE IF NOT EXISTS nffl.draft_pick (
    draft_key text NOT NULL REFERENCES nffl.draft(draft_key) ON DELETE CASCADE,
    pick_id text NOT NULL,
    round_number integer NOT NULL,
    slot_number integer NOT NULL,
    round_label text NOT NULL,
    pick_type text NOT NULL,
    column_team_key text NOT NULL,
    current_owner_team_key text NOT NULL,
    traded_flag boolean NOT NULL DEFAULT false,
    ownership_note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_key, pick_id),
    UNIQUE (draft_key, round_number, slot_number),
    CHECK (round_number > 0),
    CHECK (slot_number > 0),
    CHECK (pick_type IN ('QO', 'STANDARD'))
);

CREATE TABLE IF NOT EXISTS nffl.draft_pick_trade (
    trade_id bigserial PRIMARY KEY,
    draft_key text NOT NULL,
    pick_id text NOT NULL,
    from_team_key text NOT NULL,
    to_team_key text NOT NULL,
    trade_date date,
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    UNIQUE (draft_key, pick_id, from_team_key, to_team_key, trade_date, note)
);

CREATE TABLE IF NOT EXISTS nffl.draft_selection (
    draft_key text NOT NULL,
    pick_id text NOT NULL,
    selecting_team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    pick_kind text NOT NULL,
    selected_at_utc timestamptz NOT NULL DEFAULT now(),
    selected_by text,
    note text,
    PRIMARY KEY (draft_key, pick_id),
    CHECK (pick_kind IN ('QO', 'POACH', 'FA', 'CONTRACT', 'PT'))
);

CREATE OR REPLACE VIEW nffl.v_draft_board_current AS
SELECT
    p.draft_key,
    p.pick_id,
    p.round_number,
    p.slot_number,
    p.round_label,
    p.pick_type,
    p.column_team_key,
    col_team.team_name AS column_team_name,
    p.current_owner_team_key,
    owner_team.team_name AS current_owner_team_name,
    p.traded_flag,
    p.ownership_note,
    s.yahoo_player_key,
    u.full_name AS selected_player_name,
    s.pick_kind,
    s.selected_at_utc
FROM nffl.draft_pick p
LEFT JOIN nffl.team col_team
  ON col_team.league_key = '470.l.84346'
 AND col_team.season_year = 2026
 AND col_team.team_key = p.column_team_key
LEFT JOIN nffl.team owner_team
  ON owner_team.league_key = '470.l.84346'
 AND owner_team.season_year = 2026
 AND owner_team.team_key = p.current_owner_team_key
LEFT JOIN nffl.draft_selection s
  ON s.draft_key = p.draft_key
 AND s.pick_id = p.pick_id
LEFT JOIN nffl.player_universe u
  ON u.league_key = '470.l.84346'
 AND u.season_year = 2026
 AND u.yahoo_player_key = s.yahoo_player_key;

CREATE INDEX IF NOT EXISTS ix_nffl_player_universe_name
    ON nffl.player_universe (lower(full_name));

CREATE INDEX IF NOT EXISTS ix_nffl_roster_snapshot_player_player
    ON nffl.roster_snapshot_player (league_key, season_year, yahoo_player_key);

CREATE INDEX IF NOT EXISTS ix_nffl_contract_team
    ON nffl.contract (league_key, season_year, team_key);

CREATE INDEX IF NOT EXISTS ix_nffl_draft_pick_owner
    ON nffl.draft_pick (draft_key, current_owner_team_key);

CREATE INDEX IF NOT EXISTS ix_nffl_draft_pick_column
    ON nffl.draft_pick (draft_key, column_team_key);
