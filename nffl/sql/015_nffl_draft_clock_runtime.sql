BEGIN;

-- Runtime record for a pick whose 24-hour clock expired but which
-- remains legally open for a later selection.
--
-- No row means the pick is not currently in expired/open state.
-- resolved_at_utc IS NULL means the expired pick is still open.
CREATE TABLE nffl.draft_expired_pick (
    draft_key text NOT NULL,
    pick_id text NOT NULL,
    expired_at_utc timestamptz NOT NULL,
    resolved_at_utc timestamptz NULL,
    resolved_by text NULL,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_expired_pick_pkey
        PRIMARY KEY (draft_key, pick_id),

    CONSTRAINT draft_expired_pick_pick_fkey
        FOREIGN KEY (draft_key, pick_id)
        REFERENCES nffl.draft_pick (draft_key, pick_id)
        ON DELETE CASCADE,

    CONSTRAINT draft_expired_pick_resolution_time_check
        CHECK (
            resolved_at_utc IS NULL
            OR resolved_at_utc >= expired_at_utc
        )
);

CREATE INDEX draft_expired_pick_open_idx
    ON nffl.draft_expired_pick (draft_key, expired_at_utc, pick_id)
    WHERE resolved_at_utc IS NULL;


-- Durable event ledger for deterministic draft-clock events.
--
-- The primary key guarantees that polling PostgreSQL repeatedly cannot
-- create the same event more than once for the same pick.
CREATE TABLE nffl.draft_clock_event (
    draft_key text NOT NULL,
    pick_id text NOT NULL,
    event_type text NOT NULL,
    team_key text NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    created_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT draft_clock_event_pkey
        PRIMARY KEY (draft_key, pick_id, event_type),

    CONSTRAINT draft_clock_event_pick_fkey
        FOREIGN KEY (draft_key, pick_id)
        REFERENCES nffl.draft_pick (draft_key, pick_id)
        ON DELETE CASCADE,

    CONSTRAINT draft_clock_event_type_check
        CHECK (
            event_type IN (
                'ON_CLOCK',
                'REMINDER_12H',
                'REMINDER_6H',
                'REMINDER_1H',
                'EXPIRED'
            )
        )
);

CREATE INDEX draft_clock_event_time_idx
    ON nffl.draft_clock_event (
        draft_key,
        occurred_at_utc,
        pick_id,
        event_type
    );


-- Discord remains read-only at the table level.
REVOKE ALL
ON nffl.draft_expired_pick
FROM PUBLIC;

REVOKE ALL
ON nffl.draft_clock_event
FROM PUBLIC;

GRANT SELECT
ON nffl.draft_clock_event
TO nffl_discord_reader;

GRANT SELECT
ON nffl.draft_expired_pick
TO nffl_discord_reader;

COMMIT;
