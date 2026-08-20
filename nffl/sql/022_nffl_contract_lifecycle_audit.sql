-- NFFL contract lifecycle audit infrastructure.
--
-- Purpose:
--   1. Preserve every player actually returned by Yahoo for an
--      end-of-season roster, even when that player cannot be mapped
--      into the following season's Yahoo player universe.
--   2. Allow an end-of-season roster snapshot to be explicitly
--      finalized with a deterministic content fingerprint.
--   3. Preserve immutable/revisioned annual contract reconciliation
--      decisions used to roll contracts into the following season.
--
-- This migration is intentionally additive.
-- It does not alter or replace existing DraftBoard tables.

CREATE TABLE nffl.roster_snapshot_source_player (
    snapshot_id text NOT NULL,

    source_league_key text NOT NULL,
    source_season_year integer NOT NULL,
    source_team_key text NOT NULL,
    source_yahoo_player_key text NOT NULL,
    source_player_id text NOT NULL,

    player_name text NOT NULL,
    source_team_abbr text,
    display_position text,
    roster_slot text,
    roster_status text,

    current_team_key text NOT NULL,
    current_yahoo_player_key text,

    mapping_status text NOT NULL,

    source_note text,

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT roster_snapshot_source_player_pkey
        PRIMARY KEY (
            snapshot_id,
            source_team_key,
            source_yahoo_player_key
        ),

    CONSTRAINT roster_snapshot_source_player_snapshot_fkey
        FOREIGN KEY (snapshot_id)
        REFERENCES nffl.roster_snapshot (snapshot_id)
        ON DELETE CASCADE,

    CONSTRAINT roster_snapshot_source_player_season_check
        CHECK (source_season_year BETWEEN 2000 AND 2100),

    CONSTRAINT roster_snapshot_source_player_id_check
        CHECK (btrim(source_player_id) <> ''),

    CONSTRAINT roster_snapshot_source_player_mapping_check
        CHECK (
            mapping_status IN (
                'MAPPED_CURRENT_UNIVERSE',
                'NOT_IN_CURRENT_UNIVERSE'
            )
        ),

    CONSTRAINT roster_snapshot_source_player_mapping_value_check
        CHECK (
            (
                mapping_status = 'MAPPED_CURRENT_UNIVERSE'
                AND current_yahoo_player_key IS NOT NULL
            )
            OR
            (
                mapping_status = 'NOT_IN_CURRENT_UNIVERSE'
                AND current_yahoo_player_key IS NULL
            )
        )
);

CREATE INDEX ix_nffl_roster_snapshot_source_player_id
    ON nffl.roster_snapshot_source_player (
        snapshot_id,
        source_player_id
    );

CREATE INDEX ix_nffl_roster_snapshot_source_player_mapping
    ON nffl.roster_snapshot_source_player (
        snapshot_id,
        mapping_status
    );


CREATE TABLE nffl.roster_snapshot_finalization (
    snapshot_id text NOT NULL,

    source_player_count integer NOT NULL,
    normalized_player_count integer NOT NULL,
    unmapped_player_count integer NOT NULL,

    content_sha256 text NOT NULL,

    finalized_at_utc timestamptz NOT NULL DEFAULT now(),
    finalized_by text NOT NULL,
    note text,

    CONSTRAINT roster_snapshot_finalization_pkey
        PRIMARY KEY (snapshot_id),

    CONSTRAINT roster_snapshot_finalization_snapshot_fkey
        FOREIGN KEY (snapshot_id)
        REFERENCES nffl.roster_snapshot (snapshot_id),

    CONSTRAINT roster_snapshot_finalization_counts_check
        CHECK (
            source_player_count >= 0
            AND normalized_player_count >= 0
            AND unmapped_player_count >= 0
            AND source_player_count =
                normalized_player_count + unmapped_player_count
        ),

    CONSTRAINT roster_snapshot_finalization_sha_check
        CHECK (
            content_sha256 ~ '^[0-9A-Fa-f]{64}$'
        ),

    CONSTRAINT roster_snapshot_finalization_user_check
        CHECK (btrim(finalized_by) <> '')
);


CREATE TABLE nffl.contract_season_audit (
    contract_season_audit_id bigint
        GENERATED ALWAYS AS IDENTITY,

    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key_at_start text NOT NULL,
    yahoo_player_key_at_start text NOT NULL,
    stable_player_id text NOT NULL,

    contract_years_at_start integer NOT NULL,
    contract_status_at_start text NOT NULL,
    contract_source_at_start text NOT NULL,
    contract_source_snapshot_id text,

    origin_contract_episode_id bigint,

    end_roster_snapshot_id text NOT NULL,

    roster_reconciliation_status text NOT NULL,

    observed_source_team_key text,
    observed_source_yahoo_player_key text,

    observed_current_team_key text,
    observed_current_yahoo_player_key text,

    rollover_action text NOT NULL,

    next_season_year integer,
    next_league_key text,
    next_team_key text,
    next_yahoo_player_key text,
    next_contract_years integer,

    reconciliation_reason text NOT NULL,

    audit_revision integer NOT NULL DEFAULT 1,
    supersedes_audit_id bigint,

    audited_at_utc timestamptz NOT NULL DEFAULT now(),
    audited_by text NOT NULL,

    CONSTRAINT contract_season_audit_pkey
        PRIMARY KEY (contract_season_audit_id),

    CONSTRAINT contract_season_audit_revision_unique
        UNIQUE (
            league_key,
            season_year,
            yahoo_player_key_at_start,
            audit_revision
        ),

    CONSTRAINT contract_season_audit_snapshot_fkey
        FOREIGN KEY (end_roster_snapshot_id)
        REFERENCES nffl.roster_snapshot_finalization (snapshot_id),

    CONSTRAINT contract_season_audit_origin_episode_fkey
        FOREIGN KEY (origin_contract_episode_id)
        REFERENCES nffl.contract_history_episode (
            contract_episode_id
        ),

    CONSTRAINT contract_season_audit_supersedes_fkey
        FOREIGN KEY (supersedes_audit_id)
        REFERENCES nffl.contract_season_audit (
            contract_season_audit_id
        ),

    CONSTRAINT contract_season_audit_season_check
        CHECK (season_year BETWEEN 2000 AND 2100),

    CONSTRAINT contract_season_audit_player_id_check
        CHECK (btrim(stable_player_id) <> ''),

    CONSTRAINT contract_season_audit_years_check
        CHECK (contract_years_at_start >= 0),

    CONSTRAINT contract_season_audit_revision_check
        CHECK (audit_revision > 0),

    CONSTRAINT contract_season_audit_roster_status_check
        CHECK (
            roster_reconciliation_status IN (
                'SAME_TEAM',
                'DIFFERENT_TEAM',
                'NOT_ROSTERED',
                'SOURCE_PLAYER_UNMAPPED',
                'NEEDS_REVIEW'
            )
        ),

    CONSTRAINT contract_season_audit_rollover_action_check
        CHECK (
            rollover_action IN (
                'CARRY_FORWARD',
                'EXPIRE',
                'DROP',
                'VOID',
                'NEEDS_REVIEW',
                'NO_ACTION'
            )
        ),

    CONSTRAINT contract_season_audit_next_years_check
        CHECK (
            next_contract_years IS NULL
            OR next_contract_years >= 0
        ),

    CONSTRAINT contract_season_audit_carry_forward_check
        CHECK (
            rollover_action <> 'CARRY_FORWARD'
            OR (
                contract_years_at_start >= 2
                AND next_season_year IS NOT NULL
                AND next_league_key IS NOT NULL
                AND next_team_key IS NOT NULL
                AND next_yahoo_player_key IS NOT NULL
                AND next_contract_years =
                    contract_years_at_start - 1
            )
        ),

    CONSTRAINT contract_season_audit_noncarry_check
        CHECK (
            rollover_action = 'CARRY_FORWARD'
            OR (
                next_season_year IS NULL
                AND next_league_key IS NULL
                AND next_team_key IS NULL
                AND next_yahoo_player_key IS NULL
                AND next_contract_years IS NULL
            )
        ),

    CONSTRAINT contract_season_audit_user_check
        CHECK (btrim(audited_by) <> '')
);


CREATE INDEX ix_nffl_contract_season_audit_player
    ON nffl.contract_season_audit (
        stable_player_id,
        season_year DESC
    );

CREATE INDEX ix_nffl_contract_season_audit_team
    ON nffl.contract_season_audit (
        league_key,
        season_year,
        team_key_at_start
    );

CREATE UNIQUE INDEX ux_nffl_contract_season_audit_superseded_once
    ON nffl.contract_season_audit (
        supersedes_audit_id
    )
    WHERE supersedes_audit_id IS NOT NULL;


CREATE VIEW nffl.v_contract_season_audit_current AS
SELECT
    ranked.contract_season_audit_id,
    ranked.league_key,
    ranked.season_year,
    ranked.team_key_at_start,
    ranked.yahoo_player_key_at_start,
    ranked.stable_player_id,
    ranked.contract_years_at_start,
    ranked.contract_status_at_start,
    ranked.contract_source_at_start,
    ranked.contract_source_snapshot_id,
    ranked.origin_contract_episode_id,
    ranked.end_roster_snapshot_id,
    ranked.roster_reconciliation_status,
    ranked.observed_source_team_key,
    ranked.observed_source_yahoo_player_key,
    ranked.observed_current_team_key,
    ranked.observed_current_yahoo_player_key,
    ranked.rollover_action,
    ranked.next_season_year,
    ranked.next_league_key,
    ranked.next_team_key,
    ranked.next_yahoo_player_key,
    ranked.next_contract_years,
    ranked.reconciliation_reason,
    ranked.audit_revision,
    ranked.supersedes_audit_id,
    ranked.audited_at_utc,
    ranked.audited_by
FROM (
    SELECT
        a.*,
        row_number() OVER (
            PARTITION BY
                a.league_key,
                a.season_year,
                a.yahoo_player_key_at_start
            ORDER BY
                a.audit_revision DESC,
                a.contract_season_audit_id DESC
        ) AS audit_rank
    FROM nffl.contract_season_audit a
) ranked
WHERE ranked.audit_rank = 1;


COMMENT ON TABLE nffl.roster_snapshot_source_player IS
    'Raw player-level evidence returned by Yahoo for an end-of-season roster before next-season player-key normalization.';

COMMENT ON TABLE nffl.roster_snapshot_finalization IS
    'Finalization record and deterministic fingerprint for an end-of-season roster snapshot. Presence indicates the snapshot is approved as audit evidence.';

COMMENT ON TABLE nffl.contract_season_audit IS
    'Immutable/revisioned annual reconciliation of a contract against finalized end-of-season Yahoo roster evidence and its approved rollover disposition.';

COMMENT ON VIEW nffl.v_contract_season_audit_current IS
    'Latest annual contract reconciliation revision for each league-season-player contract state.';
