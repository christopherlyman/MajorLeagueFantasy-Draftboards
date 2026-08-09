-- NFFL Draft Queue / Auto-Pick
--
-- Phase 1:
--   * Persistent ranked queue, maximum 5 players per team.
--   * Auto-pick defaults OFF.
--   * Auto-pick may be armed only for one exact draft pick.
--   * One-shot behavior is the default.
--   * Audit storage is provided for preview/execution activity.
--
-- Commissioner pilot:
--   The existing DraftBoard manual-pick machinery remains the only
--   code path that actually records a draft selection.
--
-- Later unattended execution will extend this migration/design only
--   after the commissioner pilot is proven. No parallel pick engine.

BEGIN;

CREATE TABLE nffl.draft_autopick_control (
    draft_key text NOT NULL,
    team_key text NOT NULL,

    enabled boolean NOT NULL DEFAULT false,
    one_shot boolean NOT NULL DEFAULT true,

    -- Safety belt: when enabled, the feature may act only on this exact pick.
    armed_pick_id text,

    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_by text,

    CONSTRAINT draft_autopick_control_pk
        PRIMARY KEY (draft_key, team_key),

    CONSTRAINT draft_autopick_control_enabled_requires_pick_ck
        CHECK (NOT enabled OR armed_pick_id IS NOT NULL)
);

CREATE TABLE nffl.draft_autopick_queue (
    draft_key text NOT NULL,
    team_key text NOT NULL,
    queue_rank integer NOT NULL,
    yahoo_player_key text NOT NULL,

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_autopick_queue_pk
        PRIMARY KEY (draft_key, team_key, queue_rank),

    CONSTRAINT draft_autopick_queue_rank_ck
        CHECK (queue_rank BETWEEN 1 AND 5),

    CONSTRAINT draft_autopick_queue_player_uq
        UNIQUE (draft_key, team_key, yahoo_player_key),

    CONSTRAINT draft_autopick_queue_control_fk
        FOREIGN KEY (draft_key, team_key)
        REFERENCES nffl.draft_autopick_control (draft_key, team_key)
        ON DELETE CASCADE
);

CREATE TABLE nffl.draft_autopick_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    draft_key text NOT NULL,
    team_key text NOT NULL,
    pick_id text,

    attempted_at_utc timestamptz NOT NULL DEFAULT now(),

    result text NOT NULL,
    selected_player_key text,
    selected_queue_rank integer,
    pick_kind text,

    detail text,
    initiated_by text
);

COMMIT;
