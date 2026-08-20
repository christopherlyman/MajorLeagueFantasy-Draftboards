-- NFFL commissioner contract-history season overrides.
--
-- Purpose:
--   Preserve immutable original contract-award history while allowing an
--   explicit commissioner correction to the displayed/lifecycle state for
--   one player in one season.
--
-- Semantics:
--   nffl.contract
--       Current operational contract truth.
--
--   nffl.contract_history_episode
--       Immutable original modern contract award.
--
--   nffl.contract_history_season_override
--       Sparse commissioner-authored correction for one season.
--
--   nffl.franchise_tag_history
--       Remains authoritative for Franchise Tags.
--
-- This migration is transaction-neutral. The caller controls BEGIN / COMMIT.


-- A commissioner may need to reconstruct a legitimate modern contract
-- award that was omitted from the normal post-draft publication workflow.
-- Do not mislabel that provenance as QO, POACH, or FA.
ALTER TABLE nffl.contract_history_episode
    DROP CONSTRAINT contract_history_episode_pick_kind_check;

ALTER TABLE nffl.contract_history_episode
    ADD CONSTRAINT contract_history_episode_pick_kind_check
    CHECK (
        source_pick_kind IN (
            'QO',
            'POACH',
            'FA',
            'COMMISSIONER'
        )
    );


CREATE TABLE nffl.contract_history_season_override (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    yahoo_player_key text NOT NULL,
    team_key text NOT NULL,

    contract_status text NOT NULL,
    acquisition_type text NOT NULL DEFAULT 'NONE',
    contract_years integer,

    acquisition_from_team_key text,

    origin_contract_episode_id bigint,

    note text,

    updated_by text NOT NULL DEFAULT 'commissioner',

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT contract_history_season_override_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT contract_history_season_override_year_check
        CHECK (
            season_year BETWEEN 2000 AND 2100
        ),

    CONSTRAINT contract_history_season_override_status_check
        CHECK (
            contract_status IN (
                'CONTRACT',
                'NO_CONTRACT',
                'DROPPED',
                'EXPIRED',
                'NEEDS_REVIEW'
            )
        ),

    CONSTRAINT contract_history_season_override_acquisition_check
        CHECK (
            acquisition_type IN (
                'NONE',
                'TRADE',
                'WAIVER'
            )
        ),

    CONSTRAINT contract_history_season_override_value_check
        CHECK (
            (
                contract_status = 'CONTRACT'
                AND contract_years BETWEEN 1 AND 4
            )
            OR
            (
                contract_status <> 'CONTRACT'
                AND contract_years IS NULL
            )
        ),

    CONSTRAINT contract_history_season_override_acquisition_state_check
        CHECK (
            acquisition_type = 'NONE'
            OR contract_status = 'CONTRACT'
        ),

    CONSTRAINT contract_history_season_override_origin_fkey
        FOREIGN KEY (origin_contract_episode_id)
        REFERENCES nffl.contract_history_episode (
            contract_episode_id
        )
);


CREATE INDEX ix_contract_history_season_override_team
    ON nffl.contract_history_season_override (
        league_key,
        season_year,
        team_key
    );


CREATE INDEX ix_contract_history_season_override_episode
    ON nffl.contract_history_season_override (
        origin_contract_episode_id
    )
    WHERE origin_contract_episode_id IS NOT NULL;


COMMENT ON TABLE
    nffl.contract_history_season_override
IS
    'Sparse commissioner-authored correction to one player contract-history season. Original award episodes remain immutable.';


COMMENT ON COLUMN
    nffl.contract_history_season_override.contract_status
IS
    'Displayed contract state for this season: CONTRACT, NO_CONTRACT, DROPPED, EXPIRED, or NEEDS_REVIEW. Franchise Tags remain in franchise_tag_history.';


COMMENT ON COLUMN
    nffl.contract_history_season_override.acquisition_type
IS
    'How the player arrived for this season: NONE, TRADE, or WAIVER. This is independent of active contract state.';


COMMENT ON COLUMN
    nffl.contract_history_season_override.origin_contract_episode_id
IS
    'Modern immutable contract episode when one exists. NULL is valid for legacy spreadsheet-origin contracts.';


COMMENT ON COLUMN
    nffl.contract_history_season_override.acquisition_from_team_key
IS
    'Optional prior NFFL team for a TRADE correction.';
