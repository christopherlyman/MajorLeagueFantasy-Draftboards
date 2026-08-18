BEGIN;

-- ================================================================
-- NFHL PERSISTED DRAFT STATE
--
-- This is deliberately NFHL-local. It does NOT use
-- public.draftboard_state.
--
-- Relational tables remain authoritative for picks/selections/players.
-- JSON state holds only runtime/order/log state that benefits from
-- atomic document updates.
-- ================================================================

CREATE TABLE nfhl.draft_state (
    draft_key           text        NOT NULL,
    schema_version      text        NOT NULL,
    state_json          jsonb       NOT NULL,
    state_sha256        text        NOT NULL,
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_state_pkey
        PRIMARY KEY (draft_key),

    CONSTRAINT draft_state_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_state_json_object_ck
        CHECK (jsonb_typeof(state_json) = 'object')
);

CREATE INDEX draft_state_updated_idx
    ON nfhl.draft_state (updated_at_utc DESC);


-- ================================================================
-- SIMPLE REDRAFT BOARD VIEW
--
-- No QO.
-- No FT.
-- No contracts.
-- No keeper overlays.
-- No hard-coded league key.
-- ================================================================

CREATE VIEW nfhl.v_draft_board_current AS
SELECT
    p.draft_key,
    p.pick_id,
    p.round_number,
    p.slot_number,
    p.round_label,

    p.column_team_key,
    column_team.team_name AS column_team_name,

    p.current_owner_team_key,
    owner_team.team_name AS current_owner_team_name,

    p.traded_flag,
    p.ownership_note,

    s.yahoo_player_key,
    player.full_name AS selected_player_name,
    s.selected_at_utc,
    player.primary_position AS selected_primary_position

FROM nfhl.draft_pick p

JOIN nfhl.draft d
  ON d.draft_key = p.draft_key

LEFT JOIN nfhl.team column_team
  ON column_team.league_key = d.league_key
 AND column_team.season_year = d.season_year
 AND column_team.team_key = p.column_team_key

LEFT JOIN nfhl.team owner_team
  ON owner_team.league_key = d.league_key
 AND owner_team.season_year = d.season_year
 AND owner_team.team_key = p.current_owner_team_key

LEFT JOIN nfhl.draft_selection s
  ON s.draft_key = p.draft_key
 AND s.pick_id = p.pick_id

LEFT JOIN nfhl.player_universe player
  ON player.league_key = d.league_key
 AND player.season_year = d.season_year
 AND player.yahoo_player_key = s.yahoo_player_key;


-- ================================================================
-- AUTO-PICK
--
-- NFHL behavior:
--   * OFF by default
--   * max five ranked candidates
--   * armed to one exact pick
--   * successful execution is one-shot
--
-- No unused one_shot flag is carried forward.
-- ================================================================

CREATE TABLE nfhl.draft_autopick_control (
    draft_key           text        NOT NULL,
    team_key            text        NOT NULL,
    enabled             boolean     NOT NULL DEFAULT false,
    armed_pick_id       text,
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_by          text,

    CONSTRAINT draft_autopick_control_pkey
        PRIMARY KEY (draft_key, team_key),

    CONSTRAINT draft_autopick_control_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_autopick_control_enabled_ck
        CHECK (
            NOT enabled
            OR armed_pick_id IS NOT NULL
        ),

    CONSTRAINT draft_autopick_control_armed_pick_fk
        FOREIGN KEY (draft_key, armed_pick_id)
        REFERENCES nfhl.draft_pick(draft_key, pick_id)
        ON DELETE CASCADE
);

CREATE TABLE nfhl.draft_autopick_queue (
    draft_key           text        NOT NULL,
    team_key            text        NOT NULL,
    queue_rank          integer     NOT NULL,
    yahoo_player_key    text        NOT NULL,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_autopick_queue_pkey
        PRIMARY KEY (
            draft_key,
            team_key,
            queue_rank
        ),

    CONSTRAINT draft_autopick_queue_control_fk
        FOREIGN KEY (draft_key, team_key)
        REFERENCES nfhl.draft_autopick_control(
            draft_key,
            team_key
        )
        ON DELETE CASCADE,

    CONSTRAINT draft_autopick_queue_rank_ck
        CHECK (
            queue_rank >= 1
            AND queue_rank <= 5
        ),

    CONSTRAINT draft_autopick_queue_player_uq
        UNIQUE (
            draft_key,
            team_key,
            yahoo_player_key
        )
);

CREATE TABLE nfhl.draft_autopick_audit (
    audit_id                bigint GENERATED ALWAYS AS IDENTITY,
    draft_key               text        NOT NULL,
    team_key                text        NOT NULL,
    pick_id                 text,
    attempted_at_utc        timestamptz NOT NULL DEFAULT now(),
    result                  text        NOT NULL,
    selected_player_key     text,
    selected_queue_rank     integer,
    detail                  text,
    initiated_by            text,

    CONSTRAINT draft_autopick_audit_pkey
        PRIMARY KEY (audit_id),

    CONSTRAINT draft_autopick_audit_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_autopick_audit_pick_fk
        FOREIGN KEY (draft_key, pick_id)
        REFERENCES nfhl.draft_pick(draft_key, pick_id)
        ON DELETE SET NULL
);

CREATE INDEX draft_autopick_audit_lookup_idx
    ON nfhl.draft_autopick_audit (
        draft_key,
        team_key,
        attempted_at_utc DESC
    );


-- ================================================================
-- DRAFT CLOCK CONFIGURATION
--
-- No clock duration is assumed here.
-- No 24-hour value is inserted.
--
-- A production draft cannot start until a configuration row exists.
-- ================================================================

CREATE TABLE nfhl.draft_clock_config (
    draft_key           text        NOT NULL,
    seconds_per_pick    integer     NOT NULL,
    timezone            text        NOT NULL DEFAULT 'America/New_York',
    auto_advance        boolean     NOT NULL DEFAULT true,
    weekends_count      boolean     NOT NULL DEFAULT true,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_clock_config_pkey
        PRIMARY KEY (draft_key),

    CONSTRAINT draft_clock_config_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_clock_config_seconds_ck
        CHECK (seconds_per_pick > 0),

    CONSTRAINT draft_clock_config_timezone_ck
        CHECK (BTRIM(timezone) <> '')
);


-- ================================================================
-- CLOCK REMINDERS
--
-- Reminder timing is relational/configurable rather than embedded
-- in process_draft_clock().
--
-- Example future rows might represent 12h, 6h, 1h remaining,
-- but this migration intentionally inserts NONE.
-- ================================================================

CREATE TABLE nfhl.draft_clock_reminder_config (
    draft_key               text        NOT NULL,
    reminder_code           text        NOT NULL,
    seconds_remaining       integer     NOT NULL,
    enabled                 boolean     NOT NULL DEFAULT true,
    created_at_utc          timestamptz NOT NULL DEFAULT now(),
    updated_at_utc          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_clock_reminder_config_pkey
        PRIMARY KEY (
            draft_key,
            reminder_code
        ),

    CONSTRAINT draft_clock_reminder_config_clock_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft_clock_config(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_clock_reminder_seconds_ck
        CHECK (seconds_remaining > 0),

    CONSTRAINT draft_clock_reminder_code_ck
        CHECK (BTRIM(reminder_code) <> ''),

    CONSTRAINT draft_clock_reminder_seconds_uq
        UNIQUE (
            draft_key,
            seconds_remaining
        )
);


-- ================================================================
-- DURABLE CLOCK EVENTS
--
-- Dynamic reminders use:
--     REMINDER:<configured reminder_code>
--
-- This avoids hard-coding 12H / 6H / 1H into the schema.
-- ================================================================

CREATE TABLE nfhl.draft_clock_event (
    draft_key           text        NOT NULL,
    pick_id             text        NOT NULL,
    event_type          text        NOT NULL,
    team_key            text        NOT NULL,
    occurred_at_utc     timestamptz NOT NULL,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_clock_event_pkey
        PRIMARY KEY (
            draft_key,
            pick_id,
            event_type
        ),

    CONSTRAINT draft_clock_event_pick_fk
        FOREIGN KEY (draft_key, pick_id)
        REFERENCES nfhl.draft_pick(draft_key, pick_id)
        ON DELETE CASCADE,

    CONSTRAINT draft_clock_event_type_ck
        CHECK (
            event_type IN (
                'ON_CLOCK',
                'EXPIRED'
            )
            OR event_type LIKE 'REMINDER:%'
        )
);

CREATE INDEX draft_clock_event_time_idx
    ON nfhl.draft_clock_event (
        draft_key,
        occurred_at_utc,
        pick_id,
        event_type
    );


-- ================================================================
-- EXPIRED / STILL-OPEN PICKS
--
-- A timeout advances the live clock but does not destroy the
-- manager's missed selection opportunity.
-- ================================================================

CREATE TABLE nfhl.draft_expired_pick (
    draft_key           text        NOT NULL,
    pick_id             text        NOT NULL,
    expired_at_utc      timestamptz NOT NULL,
    resolved_at_utc     timestamptz,
    resolved_by         text,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_expired_pick_pkey
        PRIMARY KEY (
            draft_key,
            pick_id
        ),

    CONSTRAINT draft_expired_pick_pick_fk
        FOREIGN KEY (draft_key, pick_id)
        REFERENCES nfhl.draft_pick(draft_key, pick_id)
        ON DELETE CASCADE,

    CONSTRAINT draft_expired_pick_resolution_ck
        CHECK (
            resolved_at_utc IS NULL
            OR resolved_at_utc >= expired_at_utc
        )
);

CREATE INDEX draft_expired_pick_open_idx
    ON nfhl.draft_expired_pick (
        draft_key,
        expired_at_utc,
        pick_id
    )
    WHERE resolved_at_utc IS NULL;


-- ================================================================
-- INITIAL NFHL STATE
--
-- Notice what is NOT here:
--   QO rules
--   PT maps
--   keeper fields
--   contract fields
--   duplicated player objects
--   duplicated pick selection truth
--
-- Picks/selections/players remain relational.
-- ================================================================

WITH payload AS (
    SELECT jsonb_build_object(
        'schema_version',
            'nfhl-1',

        'draft_order_team_keys_by_slot',
            '[]'::jsonb,

        'pick_order',
            '[]'::jsonb,

        'pick_log',
            '[]'::jsonb,

        'clock',
            jsonb_build_object(
                'current_pick_id', NULL,
                'is_running', false,
                'pick_started_ts_iso', NULL,
                'pick_paused_ts_iso', NULL,
                'elapsed_paused_seconds', 0
            )
    ) AS state_json
)
INSERT INTO nfhl.draft_state (
    draft_key,
    schema_version,
    state_json,
    state_sha256
)
SELECT
    'nfhl_2026_preseason',
    'nfhl-1',
    state_json,
    encode(
        public.digest(
            pg_catalog.convert_to(
                state_json::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    )
FROM payload;


COMMIT;
