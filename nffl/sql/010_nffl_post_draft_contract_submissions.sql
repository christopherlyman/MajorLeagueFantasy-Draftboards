BEGIN;

ALTER TABLE
    nffl.league_visibility_state
ADD COLUMN
    post_draft_contracts_revealed boolean
        NOT NULL
        DEFAULT false,
ADD COLUMN
    post_draft_contracts_revealed_at_utc timestamptz,
ADD COLUMN
    post_draft_contracts_revealed_by text;

CREATE TABLE
    nffl.post_draft_contract_submission (
        league_key text NOT NULL,
        season_year integer NOT NULL,
        draft_key text NOT NULL,
        team_key text NOT NULL,

        submission_status text NOT NULL
            DEFAULT 'DRAFT',

        revision_number integer NOT NULL
            DEFAULT 0,

        published_at_utc timestamptz,
        published_by text,
        note text,

        created_at_utc timestamptz NOT NULL
            DEFAULT now(),

        updated_at_utc timestamptz NOT NULL
            DEFAULT now(),

        PRIMARY KEY (
            league_key,
            season_year,
            team_key
        ),

        CONSTRAINT
            post_draft_contract_submission_status_check
        CHECK (
            submission_status IN (
                'DRAFT',
                'PUBLISHED'
            )
        ),

        CONSTRAINT
            post_draft_contract_submission_revision_check
        CHECK (
            revision_number >= 0
        ),

        CONSTRAINT
            post_draft_contract_submission_publication_check
        CHECK (
            (
                submission_status = 'DRAFT'
                AND published_at_utc IS NULL
                AND published_by IS NULL
            )
            OR
            (
                submission_status = 'PUBLISHED'
                AND published_at_utc IS NOT NULL
                AND published_by IS NOT NULL
            )
        )
    );

CREATE TABLE
    nffl.post_draft_contract_decision (
        league_key text NOT NULL,
        season_year integer NOT NULL,
        draft_key text NOT NULL,
        team_key text NOT NULL,

        contract_years integer NOT NULL,
        yahoo_player_key text NOT NULL,

        source_pick_id text NOT NULL,
        source_pick_kind text NOT NULL,

        revision_number integer NOT NULL,
        decided_by text,

        decided_at_utc timestamptz NOT NULL
            DEFAULT now(),

        created_at_utc timestamptz NOT NULL
            DEFAULT now(),

        updated_at_utc timestamptz NOT NULL
            DEFAULT now(),

        PRIMARY KEY (
            league_key,
            season_year,
            team_key,
            contract_years
        ),

        CONSTRAINT
            post_draft_contract_decision_years_check
        CHECK (
            contract_years IN (2, 3, 4)
        ),

        CONSTRAINT
            post_draft_contract_decision_pick_kind_check
        CHECK (
            source_pick_kind IN (
                'QO',
                'POACH',
                'FA'
            )
        ),

        CONSTRAINT
            post_draft_contract_decision_revision_check
        CHECK (
            revision_number > 0
        ),

        CONSTRAINT
            post_draft_contract_decision_player_unique
        UNIQUE (
            league_key,
            season_year,
            yahoo_player_key
        )
    );

CREATE INDEX
    ix_post_draft_contract_decision_team
ON nffl.post_draft_contract_decision (
    league_key,
    season_year,
    team_key
);

CREATE INDEX
    ix_post_draft_contract_decision_draft
ON nffl.post_draft_contract_decision (
    draft_key,
    team_key
);

CREATE TABLE
    nffl.contract_history_episode (
        contract_episode_id bigint
            GENERATED ALWAYS AS IDENTITY
            PRIMARY KEY,

        league_key text NOT NULL,
        season_year integer NOT NULL,
        draft_key text NOT NULL,
        team_key text NOT NULL,
        yahoo_player_key text NOT NULL,

        contract_years_awarded integer NOT NULL,

        source_pick_id text NOT NULL,
        source_pick_kind text NOT NULL,
        source_revision_number integer NOT NULL,

        published_at_utc timestamptz NOT NULL,
        published_by text NOT NULL,

        created_at_utc timestamptz NOT NULL
            DEFAULT now(),

        CONSTRAINT
            contract_history_episode_years_check
        CHECK (
            contract_years_awarded IN (2, 3, 4)
        ),

        CONSTRAINT
            contract_history_episode_pick_kind_check
        CHECK (
            source_pick_kind IN (
                'QO',
                'POACH',
                'FA'
            )
        ),

        CONSTRAINT
            contract_history_episode_revision_check
        CHECK (
            source_revision_number > 0
        ),

        CONSTRAINT
            contract_history_episode_player_unique
        UNIQUE (
            league_key,
            season_year,
            yahoo_player_key
        ),

        CONSTRAINT
            contract_history_episode_slot_unique
        UNIQUE (
            league_key,
            season_year,
            team_key,
            contract_years_awarded
        )
    );

CREATE INDEX
    ix_contract_history_episode_team
ON nffl.contract_history_episode (
    league_key,
    team_key,
    season_year DESC,
    contract_years_awarded DESC
);

CREATE TABLE
    nffl.post_draft_contract_audit (
        audit_id bigint
            GENERATED ALWAYS AS IDENTITY
            PRIMARY KEY,

        league_key text NOT NULL,
        season_year integer NOT NULL,
        draft_key text NOT NULL,
        team_key text NOT NULL,

        action_type text NOT NULL,
        revision_number integer NOT NULL,
        action_by text,

        action_at_utc timestamptz NOT NULL
            DEFAULT now(),

        decision_payload jsonb NOT NULL
            DEFAULT '[]'::jsonb,

        note text,

        CONSTRAINT
            post_draft_contract_audit_action_check
        CHECK (
            action_type IN (
                'SAVE_DRAFT',
                'PUBLISH'
            )
        ),

        CONSTRAINT
            post_draft_contract_audit_revision_check
        CHECK (
            revision_number >= 0
        )
    );

CREATE INDEX
    ix_post_draft_contract_audit_team
ON nffl.post_draft_contract_audit (
    league_key,
    season_year,
    team_key,
    action_at_utc
);

COMMENT ON COLUMN
    nffl.league_visibility_state.post_draft_contracts_revealed
IS
    'True only after the commissioner atomically finalizes and reveals all new post-draft contracts.';

COMMENT ON TABLE
    nffl.post_draft_contract_submission
IS
    'Current editable or published new-contract submission status for each team. Publication is both finalization and reveal.';

COMMENT ON TABLE
    nffl.post_draft_contract_decision
IS
    'Current 4-year, 3-year, and conditional 2-year selections. Eligibility remains derived from actual nffl.draft_selection rows.';

COMMENT ON TABLE
    nffl.contract_history_episode
IS
    'Immutable history of each published new-contract episode, retained separately from current-season nffl.contract state.';

COMMENT ON TABLE
    nffl.post_draft_contract_audit
IS
    'Immutable audit history for draft saves and atomic commissioner publication.';

COMMIT;