BEGIN;

-- ================================================================
-- NFHL DRAFT ORDER LOTTERY
--
-- Pure redraft lottery.
-- All participating teams have equal odds.
--
-- One complete randomized team order is persisted at initialization.
-- Reveals expose that already-persisted result from the final slot
-- backward toward slot 1. Refresh/restart never re-randomizes it.
--
-- Voided runs are retained for audit/history.
-- ================================================================

CREATE TABLE nfhl.draft_order_lottery_run (
    lottery_run_id          bigserial   PRIMARY KEY,

    draft_key               text        NOT NULL,

    configured_team_count   integer     NOT NULL,

    status                  text        NOT NULL DEFAULT 'INITIALIZED',

    created_at_utc          timestamptz NOT NULL DEFAULT now(),
    created_by              text,

    finalized_at_utc        timestamptz,
    finalized_by            text,

    voided_at_utc           timestamptz,
    voided_by               text,
    void_reason             text,

    CONSTRAINT draft_order_lottery_run_draft_fk
        FOREIGN KEY (draft_key)
        REFERENCES nfhl.draft(draft_key)
        ON DELETE CASCADE,

    CONSTRAINT draft_order_lottery_run_team_count_ck
        CHECK (configured_team_count > 0),

    CONSTRAINT draft_order_lottery_run_status_ck
        CHECK (
            status IN (
                'INITIALIZED',
                'REVEALING',
                'REVEALED',
                'FINALIZED',
                'VOID'
            )
        )
);


-- At most one non-void lottery may exist for a draft.
-- An explicit commissioner reset will VOID the old run before
-- another lottery may be initialized.
CREATE UNIQUE INDEX draft_order_lottery_one_active_run_uq
    ON nfhl.draft_order_lottery_run (draft_key)
    WHERE status <> 'VOID';


CREATE INDEX draft_order_lottery_run_status_idx
    ON nfhl.draft_order_lottery_run (
        draft_key,
        status
    );


-- ================================================================
-- LOTTERY SLOT ASSIGNMENTS
--
-- slot_number = eventual draft-order slot.
--
-- For a 14-team league:
--   slot 14 is revealed first
--   ...
--   slot 1 is revealed last
--
-- The assignment itself exists before any reveal occurs.
-- ================================================================

CREATE TABLE nfhl.draft_order_lottery_pick (
    lottery_run_id      bigint      NOT NULL,

    slot_number         integer     NOT NULL,

    team_key            text        NOT NULL,

    revealed_at_utc     timestamptz,
    revealed_by         text,

    created_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_order_lottery_pick_pkey
        PRIMARY KEY (
            lottery_run_id,
            slot_number
        ),

    CONSTRAINT draft_order_lottery_pick_run_fk
        FOREIGN KEY (lottery_run_id)
        REFERENCES nfhl.draft_order_lottery_run(lottery_run_id)
        ON DELETE CASCADE,

    CONSTRAINT draft_order_lottery_pick_team_uq
        UNIQUE (
            lottery_run_id,
            team_key
        ),

    CONSTRAINT draft_order_lottery_pick_slot_ck
        CHECK (slot_number > 0)
);


CREATE INDEX draft_order_lottery_pick_reveal_idx
    ON nfhl.draft_order_lottery_pick (
        lottery_run_id,
        slot_number DESC
    );


COMMIT;
