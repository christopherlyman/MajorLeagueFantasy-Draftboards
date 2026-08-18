BEGIN;

CREATE SCHEMA nfhl;

-- ================================================================
-- TEAM
-- Current membership may be incomplete during roll call.
-- No fake teams are permitted.
-- ================================================================

CREATE TABLE nfhl.team (
    league_key          text        NOT NULL,
    season_year         integer     NOT NULL,
    team_key            text        NOT NULL,
    team_id             text,
    team_name           text        NOT NULL,
    owner_name          text,
    owner_guid          text,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT team_pkey
        PRIMARY KEY (league_key, season_year, team_key),

    CONSTRAINT team_season_year_ck
        CHECK (season_year > 0)
);


-- ================================================================
-- PLAYER UNIVERSE
-- Yahoo player key is authoritative.
-- Hockey-native metadata only.
-- ================================================================

CREATE TABLE nfhl.player_universe (
    league_key                  text        NOT NULL,
    season_year                 integer     NOT NULL,
    yahoo_player_key            text        NOT NULL,
    source_game_key             text        NOT NULL,
    full_name                   text        NOT NULL,

    nhl_team_abbr               text,

    eligible_positions          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    primary_position            text,
    position_type               text,
    player_status               text,

    percent_owned               numeric,
    rank_value                  numeric,
    percent_drafted             numeric,
    preseason_percent_drafted   numeric,

    raw_payload                 jsonb,

    created_at_utc              timestamptz NOT NULL DEFAULT now(),
    updated_at_utc              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT player_universe_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT player_universe_season_year_ck
        CHECK (season_year > 0),

    CONSTRAINT player_universe_position_type_ck
        CHECK (
            position_type IS NULL
            OR position_type IN ('P', 'G')
        ),

    CONSTRAINT player_universe_positions_array_ck
        CHECK (jsonb_typeof(eligible_positions) = 'array')
);

CREATE INDEX player_universe_name_idx
    ON nfhl.player_universe (lower(full_name));


-- ================================================================
-- DRAFT
--
-- draft_order_mode intentionally remains NULL until verified.
-- status PREP permits development during incomplete roll call.
-- ================================================================

CREATE TABLE nfhl.draft (
    draft_key           text        PRIMARY KEY,
    league_key          text        NOT NULL,
    season_year         integer     NOT NULL,
    draft_label         text        NOT NULL,
    manager_count       integer     NOT NULL,
    rounds_total        integer     NOT NULL,

    draft_order_mode    text,

    status              text        NOT NULL DEFAULT 'PREP',

    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_manager_count_ck
        CHECK (manager_count > 0),

    CONSTRAINT draft_rounds_total_ck
        CHECK (rounds_total > 0),

    CONSTRAINT draft_order_mode_ck
        CHECK (
            draft_order_mode IS NULL
            OR draft_order_mode IN ('straight', 'snake')
        ),

    CONSTRAINT draft_status_ck
        CHECK (
            status IN (
                'PREP',
                'SETUP',
                'ACTIVE',
                'COMPLETE',
                'ARCHIVED'
            )
        )
);


-- ================================================================
-- DRAFT PICK
--
-- Every NFHL pick is a normal redraft pick.
-- There is intentionally NO pick_type / QO concept.
--
-- column_team_key = original draft-board column.
-- current_owner_team_key = current owner after any pick trade.
-- ================================================================

CREATE TABLE nfhl.draft_pick (
    draft_key               text        NOT NULL,
    pick_id                 text        NOT NULL,

    round_number            integer     NOT NULL,
    slot_number             integer     NOT NULL,
    round_label             text        NOT NULL,

    column_team_key         text        NOT NULL,
    current_owner_team_key  text        NOT NULL,

    traded_flag             boolean     NOT NULL DEFAULT false,
    ownership_note          text,

    created_at_utc          timestamptz NOT NULL DEFAULT now(),
    updated_at_utc          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_pick_pkey
        PRIMARY KEY (draft_key, pick_id),

    CONSTRAINT draft_pick_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_pick_slot_uq
        UNIQUE (draft_key, round_number, slot_number),

    CONSTRAINT draft_pick_round_ck
        CHECK (round_number > 0),

    CONSTRAINT draft_pick_slot_ck
        CHECK (slot_number > 0)
);

CREATE INDEX draft_pick_column_idx
    ON nfhl.draft_pick (draft_key, column_team_key);

CREATE INDEX draft_pick_owner_idx
    ON nfhl.draft_pick (draft_key, current_owner_team_key);


-- ================================================================
-- DRAFT SELECTION
--
-- There is intentionally NO pick_kind.
-- A selection is simply a redraft selection.
-- ================================================================

CREATE TABLE nfhl.draft_selection (
    draft_key           text        NOT NULL,
    pick_id             text        NOT NULL,
    selecting_team_key  text        NOT NULL,
    yahoo_player_key    text        NOT NULL,

    selected_at_utc     timestamptz NOT NULL DEFAULT now(),
    selected_by         text,
    note                text,

    CONSTRAINT draft_selection_pkey
        PRIMARY KEY (draft_key, pick_id),

    CONSTRAINT draft_selection_pick_fk
        FOREIGN KEY (draft_key, pick_id)
        REFERENCES nfhl.draft_pick(draft_key, pick_id)
        ON DELETE CASCADE,

    CONSTRAINT draft_selection_player_uq
        UNIQUE (draft_key, yahoo_player_key)
);


-- ================================================================
-- INITIAL 2026 NFHL DRAFT CONTEXT
--
-- 18 rounds is the current working inference from the 18
-- active + bench roster positions.
--
-- Draft order mode remains NULL until independently verified.
-- ================================================================

INSERT INTO nfhl.draft (
    draft_key,
    league_key,
    season_year,
    draft_label,
    manager_count,
    rounds_total,
    draft_order_mode,
    status
)
VALUES (
    'nfhl_2026_preseason',
    '477.l.10961',
    2026,
    'NFHL 2026 Draft',
    14,
    18,
    NULL,
    'PREP'
);

COMMIT;
