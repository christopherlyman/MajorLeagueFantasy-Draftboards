-- NFFL annual contract reconciliation / rollover engine.
--
-- This migration defines two database functions:
--
--   nffl.preview_contract_season_reconciliation(...)
--       Read-only deterministic reconciliation of ACTIVE contracts against
--       one finalized end-of-season Yahoo roster snapshot.
--
--   nffl.apply_contract_season_reconciliation(...)
--       Writes immutable/revisioned contract_season_audit rows and creates
--       only deterministic next-season CARRY_FORWARD contract rows.
--
-- Core rules:
--
--   SAME_TEAM + years > 1
--       CARRY_FORWARD with years - 1.
--
--   SAME_TEAM + years = 1
--       EXPIRE.
--
--   NOT_ROSTERED
--       DROP.
--
--   DIFFERENT_TEAM
--       NEEDS_REVIEW. Do not assume that an NFFL trade automatically
--       transfers the contract.
--
--   SOURCE_PLAYER_UNMAPPED
--       NEEDS_REVIEW.
--
-- Stable identity:
--   Yahoo's numeric player_id suffix is used across Yahoo game/season keys.
--   Full yahoo_player_key values remain season-specific evidence.
--
-- Operational scope:
--   Only nffl.contract rows whose status='active' are reconciled.
--   Historical void/expired rows are not rolled forward.
--
-- Provenance:
--   A carried-forward next-season contract uses the finalized end-of-season
--   roster snapshot as source_snapshot_id.
--
--   New post-draft award provenance remains in contract_history_episode;
--   this engine does not create new award episodes for carry-forwards.
--
-- Idempotence:
--   Re-running apply against the same unchanged source state and finalized
--   snapshot creates no duplicate audits or contracts.
--
--   If a current audit or next-season contract conflicts with the
--   deterministic expected state, the function refuses to overwrite it.
--   Corrections must use the explicit revision/review workflow rather than
--   silently replacing audit history.
--
-- This migration is transaction-neutral. The caller controls BEGIN/COMMIT.


CREATE OR REPLACE FUNCTION
nffl.preview_contract_season_reconciliation(
    p_league_key text,
    p_season_year integer,
    p_end_roster_snapshot_id text
)
RETURNS TABLE (
    league_key text,
    season_year integer,
    team_key_at_start text,
    yahoo_player_key_at_start text,
    stable_player_id text,
    contract_years_at_start integer,
    contract_status_at_start text,
    contract_source_at_start text,
    contract_source_snapshot_id text,
    origin_contract_episode_id bigint,
    end_roster_snapshot_id text,
    roster_reconciliation_status text,
    observed_source_team_key text,
    observed_source_yahoo_player_key text,
    observed_current_team_key text,
    observed_current_yahoo_player_key text,
    rollover_action text,
    next_season_year integer,
    next_league_key text,
    next_team_key text,
    next_yahoo_player_key text,
    next_contract_years integer,
    reconciliation_reason text
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_snapshot nffl.roster_snapshot%ROWTYPE;
    v_finalized_count integer;
    v_active_contract_count integer;
    v_invalid_contract_id_count integer;
    v_invalid_active_year_count integer;
    v_source_scope_mismatch_count integer;
    v_duplicate_raw_player_id_count integer;
BEGIN
    IF p_league_key IS NULL
       OR btrim(p_league_key) = '' THEN
        RAISE EXCEPTION
            'league_key is required.';
    END IF;

    IF p_season_year IS NULL
       OR p_season_year < 2000
       OR p_season_year > 2099 THEN
        RAISE EXCEPTION
            'season_year must be between 2000 and 2099.';
    END IF;

    IF p_end_roster_snapshot_id IS NULL
       OR btrim(p_end_roster_snapshot_id) = '' THEN
        RAISE EXCEPTION
            'end_roster_snapshot_id is required.';
    END IF;


    SELECT rs.*
      INTO v_snapshot
      FROM nffl.roster_snapshot rs
     WHERE rs.snapshot_id = p_end_roster_snapshot_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Roster snapshot % does not exist.',
            p_end_roster_snapshot_id;
    END IF;


    SELECT count(*)
      INTO v_finalized_count
      FROM nffl.roster_snapshot_finalization f
     WHERE f.snapshot_id = p_end_roster_snapshot_id;

    IF v_finalized_count <> 1 THEN
        RAISE EXCEPTION
            'Roster snapshot % is not finalized.',
            p_end_roster_snapshot_id;
    END IF;


    IF v_snapshot.snapshot_type
       IS DISTINCT FROM 'END_OF_PRIOR_SEASON_ROSTER' THEN
        RAISE EXCEPTION
            'Roster snapshot % has type %, expected END_OF_PRIOR_SEASON_ROSTER.',
            p_end_roster_snapshot_id,
            v_snapshot.snapshot_type;
    END IF;


    IF v_snapshot.source_season_year
       IS DISTINCT FROM p_season_year THEN
        RAISE EXCEPTION
            'Roster snapshot % has source season %, expected %.',
            p_end_roster_snapshot_id,
            v_snapshot.source_season_year,
            p_season_year;
    END IF;


    IF v_snapshot.season_year
       IS DISTINCT FROM (p_season_year + 1) THEN
        RAISE EXCEPTION
            'Roster snapshot % targets season %, expected %.',
            p_end_roster_snapshot_id,
            v_snapshot.season_year,
            p_season_year + 1;
    END IF;


    SELECT count(*)
      INTO v_source_scope_mismatch_count
      FROM nffl.roster_snapshot_source_player rsp
     WHERE rsp.snapshot_id = p_end_roster_snapshot_id
       AND (
            rsp.source_season_year
                IS DISTINCT FROM p_season_year
         OR rsp.source_league_key
                IS DISTINCT FROM p_league_key
       );

    IF v_source_scope_mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Finalized roster snapshot % contains % raw rows outside source league % / season %.',
            p_end_roster_snapshot_id,
            v_source_scope_mismatch_count,
            p_league_key,
            p_season_year;
    END IF;


    SELECT count(*)
      INTO v_active_contract_count
      FROM nffl.contract c
     WHERE c.league_key = p_league_key
       AND c.season_year = p_season_year
       AND c.status = 'active';

    IF v_active_contract_count = 0 THEN
        RAISE EXCEPTION
            'No active contracts exist for league % season %.',
            p_league_key,
            p_season_year;
    END IF;


    SELECT count(*)
      INTO v_invalid_contract_id_count
      FROM nffl.contract c
     WHERE c.league_key = p_league_key
       AND c.season_year = p_season_year
       AND c.status = 'active'
       AND NOT (
           split_part(
               c.yahoo_player_key,
               '.p.',
               2
           ) ~ '^[0-9]+$'
       );

    IF v_invalid_contract_id_count <> 0 THEN
        RAISE EXCEPTION
            'League % season % has % active contracts without a valid numeric Yahoo stable player_id.',
            p_league_key,
            p_season_year,
            v_invalid_contract_id_count;
    END IF;


    SELECT count(*)
      INTO v_invalid_active_year_count
      FROM nffl.contract c
     WHERE c.league_key = p_league_key
       AND c.season_year = p_season_year
       AND c.status = 'active'
       AND c.contract_years_remaining < 1;

    IF v_invalid_active_year_count <> 0 THEN
        RAISE EXCEPTION
            'League % season % has % active contracts with fewer than one year remaining.',
            p_league_key,
            p_season_year,
            v_invalid_active_year_count;
    END IF;


    SELECT count(*)
      INTO v_duplicate_raw_player_id_count
      FROM (
          SELECT rsp.source_player_id
          FROM nffl.roster_snapshot_source_player rsp
          WHERE rsp.snapshot_id = p_end_roster_snapshot_id
          GROUP BY rsp.source_player_id
          HAVING count(*) > 1
      ) duplicate_ids;

    IF v_duplicate_raw_player_id_count <> 0 THEN
        RAISE EXCEPTION
            'Finalized roster snapshot % contains % duplicate stable Yahoo player IDs.',
            p_end_roster_snapshot_id,
            v_duplicate_raw_player_id_count;
    END IF;


    RETURN QUERY
    WITH active_contracts AS (
        SELECT
            c.league_key,
            c.season_year,
            c.team_key,
            c.yahoo_player_key,
            split_part(
                c.yahoo_player_key,
                '.p.',
                2
            ) AS stable_player_id,
            c.contract_years_remaining,
            c.status,
            c.contract_source,
            c.source_snapshot_id
        FROM nffl.contract c
        WHERE c.league_key = p_league_key
          AND c.season_year = p_season_year
          AND c.status = 'active'
    ),
    matched AS (
        SELECT
            c.league_key,
            c.season_year,
            c.team_key,
            c.yahoo_player_key,
            c.stable_player_id,
            c.contract_years_remaining,
            c.status,
            c.contract_source,
            c.source_snapshot_id,

            rsp.source_team_key,
            rsp.source_yahoo_player_key,
            rsp.current_team_key,
            rsp.current_yahoo_player_key,
            rsp.mapping_status
        FROM active_contracts c
        LEFT JOIN nffl.roster_snapshot_source_player rsp
          ON rsp.snapshot_id = p_end_roster_snapshot_id
         AND rsp.source_player_id = c.stable_player_id
    ),
    classified AS (
        SELECT
            m.*,

            CASE
                WHEN m.source_yahoo_player_key IS NULL
                    THEN 'NOT_ROSTERED'

                WHEN m.mapping_status = 'NOT_IN_CURRENT_UNIVERSE'
                    THEN 'SOURCE_PLAYER_UNMAPPED'

                WHEN m.source_team_key = m.team_key
                    THEN 'SAME_TEAM'

                ELSE 'DIFFERENT_TEAM'
            END AS reconciliation_status
        FROM matched m
    ),
    resolved AS (
        SELECT
            x.*,

            COALESCE(
                (
                    SELECT e.contract_episode_id
                    FROM nffl.contract_history_episode e
                    WHERE e.league_key = x.league_key
                      AND e.season_year = x.season_year
                      AND e.yahoo_player_key =
                          x.yahoo_player_key
                    LIMIT 1
                ),
                (
                    SELECT prior.origin_contract_episode_id
                    FROM nffl.v_contract_season_audit_current prior
                    WHERE prior.rollover_action =
                              'CARRY_FORWARD'
                      AND prior.next_league_key =
                              x.league_key
                      AND prior.next_season_year =
                              x.season_year
                      AND prior.next_yahoo_player_key =
                              x.yahoo_player_key
                    ORDER BY
                        prior.contract_season_audit_id DESC
                    LIMIT 1
                )
            ) AS origin_episode_id,

            CASE
                WHEN x.reconciliation_status =
                         'SAME_TEAM'
                     AND x.contract_years_remaining > 1
                    THEN 'CARRY_FORWARD'

                WHEN x.reconciliation_status =
                         'SAME_TEAM'
                     AND x.contract_years_remaining = 1
                    THEN 'EXPIRE'

                WHEN x.reconciliation_status =
                         'NOT_ROSTERED'
                    THEN 'DROP'

                WHEN x.reconciliation_status IN (
                         'DIFFERENT_TEAM',
                         'SOURCE_PLAYER_UNMAPPED'
                     )
                    THEN 'NEEDS_REVIEW'

                ELSE 'NEEDS_REVIEW'
            END AS expected_rollover_action
        FROM classified x
    )
    SELECT
        r.league_key,
        r.season_year,
        r.team_key AS team_key_at_start,
        r.yahoo_player_key AS yahoo_player_key_at_start,
        r.stable_player_id,
        r.contract_years_remaining
            AS contract_years_at_start,
        r.status AS contract_status_at_start,
        r.contract_source AS contract_source_at_start,
        r.source_snapshot_id
            AS contract_source_snapshot_id,
        r.origin_episode_id
            AS origin_contract_episode_id,

        p_end_roster_snapshot_id
            AS end_roster_snapshot_id,

        r.reconciliation_status
            AS roster_reconciliation_status,

        r.source_team_key
            AS observed_source_team_key,

        r.source_yahoo_player_key
            AS observed_source_yahoo_player_key,

        r.current_team_key
            AS observed_current_team_key,

        r.current_yahoo_player_key
            AS observed_current_yahoo_player_key,

        r.expected_rollover_action
            AS rollover_action,

        CASE
            WHEN r.expected_rollover_action =
                     'CARRY_FORWARD'
                THEN v_snapshot.season_year
            ELSE NULL
        END AS next_season_year,

        CASE
            WHEN r.expected_rollover_action =
                     'CARRY_FORWARD'
                THEN v_snapshot.league_key
            ELSE NULL
        END AS next_league_key,

        CASE
            WHEN r.expected_rollover_action =
                     'CARRY_FORWARD'
                THEN r.current_team_key
            ELSE NULL
        END AS next_team_key,

        CASE
            WHEN r.expected_rollover_action =
                     'CARRY_FORWARD'
                THEN r.current_yahoo_player_key
            ELSE NULL
        END AS next_yahoo_player_key,

        CASE
            WHEN r.expected_rollover_action =
                     'CARRY_FORWARD'
                THEN r.contract_years_remaining - 1
            ELSE NULL
        END AS next_contract_years,

        CASE
            WHEN r.reconciliation_status =
                     'SAME_TEAM'
                 AND r.contract_years_remaining > 1
                THEN
                    'Player remained on the same NFFL franchise in finalized end-of-season roster evidence; contract carries forward with one fewer year.'

            WHEN r.reconciliation_status =
                     'SAME_TEAM'
                 AND r.contract_years_remaining = 1
                THEN
                    'Player remained on the same NFFL franchise, but the final contract year has been completed; contract expires.'

            WHEN r.reconciliation_status =
                     'NOT_ROSTERED'
                THEN
                    'Player was not present on any finalized end-of-season NFFL roster; contract is dropped.'

            WHEN r.reconciliation_status =
                     'SOURCE_PLAYER_UNMAPPED'
                THEN
                    'Player was present in finalized source roster evidence but could not be mapped into the next-season Yahoo player universe; commissioner review is required.'

            WHEN r.reconciliation_status =
                     'DIFFERENT_TEAM'
                THEN
                    'Player was present on a different NFFL franchise in finalized end-of-season roster evidence; automatic contract transfer is intentionally blocked pending commissioner review.'

            ELSE
                'Roster reconciliation requires commissioner review.'
        END AS reconciliation_reason

    FROM resolved r
    ORDER BY
        r.team_key,
        r.yahoo_player_key;
END
$$;


COMMENT ON FUNCTION
nffl.preview_contract_season_reconciliation(
    text,
    integer,
    text
)
IS
'Read-only deterministic annual NFFL contract reconciliation against one finalized end-of-season raw Yahoo roster snapshot. Uses numeric Yahoo player_id as stable cross-season identity.';


CREATE OR REPLACE FUNCTION
nffl.apply_contract_season_reconciliation(
    p_league_key text,
    p_season_year integer,
    p_end_roster_snapshot_id text,
    p_audited_by text
)
RETURNS TABLE (
    active_contract_count bigint,
    same_team_count bigint,
    different_team_count bigint,
    not_rostered_count bigint,
    source_player_unmapped_count bigint,
    carry_forward_count bigint,
    expire_count bigint,
    drop_count bigint,
    needs_review_count bigint,
    audit_rows_inserted bigint,
    next_contract_rows_inserted bigint
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_existing_audit_mismatch_count integer;
    v_target_contract_conflict_count integer;
    v_postwrite_contract_mismatch_count integer;

    v_audit_rows_inserted bigint := 0;
    v_next_contract_rows_inserted bigint := 0;
BEGIN
    IF p_audited_by IS NULL
       OR btrim(p_audited_by) = '' THEN
        RAISE EXCEPTION
            'audited_by is required.';
    END IF;


    -- Serialize reconciliation for one league/season. This prevents two
    -- commissioner sessions from simultaneously passing the idempotence
    -- gates and racing into audit or next-season contract creation.
    PERFORM pg_advisory_xact_lock(
        hashtext(p_league_key),
        p_season_year
    );


    -- Calling the preview function here performs all finalized-snapshot,
    -- source-scope, identity, and active-contract validations before writes.
    PERFORM 1
    FROM nffl.preview_contract_season_reconciliation(
        p_league_key,
        p_season_year,
        p_end_roster_snapshot_id
    )
    LIMIT 1;


    -- Idempotence / audit-integrity gate.
    --
    -- If a current audit already exists for one of these source contracts,
    -- it must exactly match the deterministic result. A disagreement is not
    -- silently overwritten; it requires an explicit revision workflow.
    SELECT count(*)
      INTO v_existing_audit_mismatch_count
      FROM
        nffl.preview_contract_season_reconciliation(
            p_league_key,
            p_season_year,
            p_end_roster_snapshot_id
        ) expected
      JOIN nffl.v_contract_season_audit_current existing
        ON existing.league_key = expected.league_key
       AND existing.season_year = expected.season_year
       AND existing.yahoo_player_key_at_start =
           expected.yahoo_player_key_at_start
     WHERE
           existing.team_key_at_start
               IS DISTINCT FROM expected.team_key_at_start
        OR existing.stable_player_id
               IS DISTINCT FROM expected.stable_player_id
        OR existing.contract_years_at_start
               IS DISTINCT FROM expected.contract_years_at_start
        OR existing.contract_status_at_start
               IS DISTINCT FROM expected.contract_status_at_start
        OR existing.contract_source_at_start
               IS DISTINCT FROM expected.contract_source_at_start
        OR existing.contract_source_snapshot_id
               IS DISTINCT FROM expected.contract_source_snapshot_id
        OR existing.origin_contract_episode_id
               IS DISTINCT FROM expected.origin_contract_episode_id
        OR existing.end_roster_snapshot_id
               IS DISTINCT FROM expected.end_roster_snapshot_id
        OR existing.roster_reconciliation_status
               IS DISTINCT FROM expected.roster_reconciliation_status
        OR existing.observed_source_team_key
               IS DISTINCT FROM expected.observed_source_team_key
        OR existing.observed_source_yahoo_player_key
               IS DISTINCT FROM expected.observed_source_yahoo_player_key
        OR existing.observed_current_team_key
               IS DISTINCT FROM expected.observed_current_team_key
        OR existing.observed_current_yahoo_player_key
               IS DISTINCT FROM expected.observed_current_yahoo_player_key
        OR existing.rollover_action
               IS DISTINCT FROM expected.rollover_action
        OR existing.next_season_year
               IS DISTINCT FROM expected.next_season_year
        OR existing.next_league_key
               IS DISTINCT FROM expected.next_league_key
        OR existing.next_team_key
               IS DISTINCT FROM expected.next_team_key
        OR existing.next_yahoo_player_key
               IS DISTINCT FROM expected.next_yahoo_player_key
        OR existing.next_contract_years
               IS DISTINCT FROM expected.next_contract_years
        OR existing.reconciliation_reason
               IS DISTINCT FROM expected.reconciliation_reason;

    IF v_existing_audit_mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Refusing contract reconciliation: % current audit rows conflict with the deterministic expected state. Use an explicit audit revision workflow.',
            v_existing_audit_mismatch_count;
    END IF;


    -- Refuse to overwrite an unrelated or conflicting next-season contract.
    --
    -- Exact previously-generated season_rollover rows are accepted so that
    -- a repeated run remains idempotent.
    SELECT count(*)
      INTO v_target_contract_conflict_count
      FROM
        nffl.preview_contract_season_reconciliation(
            p_league_key,
            p_season_year,
            p_end_roster_snapshot_id
        ) expected
      JOIN nffl.contract target
        ON target.league_key = expected.next_league_key
       AND target.season_year = expected.next_season_year
       AND split_part(
               target.yahoo_player_key,
               '.p.',
               2
           ) = expected.stable_player_id
     WHERE expected.rollover_action = 'CARRY_FORWARD'
       AND (
            target.yahoo_player_key
                IS DISTINCT FROM expected.next_yahoo_player_key
         OR target.team_key
                IS DISTINCT FROM expected.next_team_key
         OR target.contract_years_remaining
                IS DISTINCT FROM expected.next_contract_years
         OR target.contract_source
                IS DISTINCT FROM 'season_rollover'
         OR target.source_snapshot_id
                IS DISTINCT FROM p_end_roster_snapshot_id
         OR target.status
                IS DISTINCT FROM 'active'
       );

    IF v_target_contract_conflict_count <> 0 THEN
        RAISE EXCEPTION
            'Refusing contract reconciliation: % conflicting next-season contract rows already exist.',
            v_target_contract_conflict_count;
    END IF;


    INSERT INTO nffl.contract_season_audit (
        league_key,
        season_year,
        team_key_at_start,
        yahoo_player_key_at_start,
        stable_player_id,
        contract_years_at_start,
        contract_status_at_start,
        contract_source_at_start,
        contract_source_snapshot_id,
        origin_contract_episode_id,
        end_roster_snapshot_id,
        roster_reconciliation_status,
        observed_source_team_key,
        observed_source_yahoo_player_key,
        observed_current_team_key,
        observed_current_yahoo_player_key,
        rollover_action,
        next_season_year,
        next_league_key,
        next_team_key,
        next_yahoo_player_key,
        next_contract_years,
        reconciliation_reason,
        audit_revision,
        supersedes_audit_id,
        audited_at_utc,
        audited_by
    )
    SELECT
        expected.league_key,
        expected.season_year,
        expected.team_key_at_start,
        expected.yahoo_player_key_at_start,
        expected.stable_player_id,
        expected.contract_years_at_start,
        expected.contract_status_at_start,
        expected.contract_source_at_start,
        expected.contract_source_snapshot_id,
        expected.origin_contract_episode_id,
        expected.end_roster_snapshot_id,
        expected.roster_reconciliation_status,
        expected.observed_source_team_key,
        expected.observed_source_yahoo_player_key,
        expected.observed_current_team_key,
        expected.observed_current_yahoo_player_key,
        expected.rollover_action,
        expected.next_season_year,
        expected.next_league_key,
        expected.next_team_key,
        expected.next_yahoo_player_key,
        expected.next_contract_years,
        expected.reconciliation_reason,
        1,
        NULL,
        now(),
        btrim(p_audited_by)
    FROM
        nffl.preview_contract_season_reconciliation(
            p_league_key,
            p_season_year,
            p_end_roster_snapshot_id
        ) expected
    LEFT JOIN nffl.v_contract_season_audit_current existing
      ON existing.league_key = expected.league_key
     AND existing.season_year = expected.season_year
     AND existing.yahoo_player_key_at_start =
         expected.yahoo_player_key_at_start
    WHERE existing.contract_season_audit_id IS NULL;

    GET DIAGNOSTICS
        v_audit_rows_inserted = ROW_COUNT;


    INSERT INTO nffl.contract (
        league_key,
        season_year,
        team_key,
        yahoo_player_key,
        contract_years_remaining,
        contract_source,
        source_snapshot_id,
        status,
        note,
        created_at_utc,
        updated_at_utc
    )
    SELECT
        expected.next_league_key,
        expected.next_season_year,
        expected.next_team_key,
        expected.next_yahoo_player_key,
        expected.next_contract_years,
        'season_rollover',
        p_end_roster_snapshot_id,
        'active',
        format(
            'Carried forward from %s contract after finalized end-of-season roster reconciliation.',
            p_season_year
        ),
        now(),
        now()
    FROM
        nffl.preview_contract_season_reconciliation(
            p_league_key,
            p_season_year,
            p_end_roster_snapshot_id
        ) expected
    WHERE expected.rollover_action = 'CARRY_FORWARD'
    ON CONFLICT (
        league_key,
        season_year,
        yahoo_player_key
    )
    DO NOTHING;

    GET DIAGNOSTICS
        v_next_contract_rows_inserted = ROW_COUNT;


    -- Post-write assertion: every expected carry-forward must now exist
    -- exactly as the deterministic engine specified.
    SELECT count(*)
      INTO v_postwrite_contract_mismatch_count
      FROM
        nffl.preview_contract_season_reconciliation(
            p_league_key,
            p_season_year,
            p_end_roster_snapshot_id
        ) expected
      LEFT JOIN nffl.contract target
        ON target.league_key = expected.next_league_key
       AND target.season_year = expected.next_season_year
       AND target.yahoo_player_key =
           expected.next_yahoo_player_key
     WHERE expected.rollover_action = 'CARRY_FORWARD'
       AND (
            target.yahoo_player_key IS NULL
         OR target.team_key
                IS DISTINCT FROM expected.next_team_key
         OR target.contract_years_remaining
                IS DISTINCT FROM expected.next_contract_years
         OR target.contract_source
                IS DISTINCT FROM 'season_rollover'
         OR target.source_snapshot_id
                IS DISTINCT FROM p_end_roster_snapshot_id
         OR target.status
                IS DISTINCT FROM 'active'
       );

    IF v_postwrite_contract_mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Contract reconciliation post-write validation failed for % carry-forward rows.',
            v_postwrite_contract_mismatch_count;
    END IF;


    RETURN QUERY
    SELECT
        count(*)::bigint
            AS active_contract_count,

        count(*) FILTER (
            WHERE expected.roster_reconciliation_status =
                  'SAME_TEAM'
        )::bigint
            AS same_team_count,

        count(*) FILTER (
            WHERE expected.roster_reconciliation_status =
                  'DIFFERENT_TEAM'
        )::bigint
            AS different_team_count,

        count(*) FILTER (
            WHERE expected.roster_reconciliation_status =
                  'NOT_ROSTERED'
        )::bigint
            AS not_rostered_count,

        count(*) FILTER (
            WHERE expected.roster_reconciliation_status =
                  'SOURCE_PLAYER_UNMAPPED'
        )::bigint
            AS source_player_unmapped_count,

        count(*) FILTER (
            WHERE expected.rollover_action =
                  'CARRY_FORWARD'
        )::bigint
            AS carry_forward_count,

        count(*) FILTER (
            WHERE expected.rollover_action =
                  'EXPIRE'
        )::bigint
            AS expire_count,

        count(*) FILTER (
            WHERE expected.rollover_action =
                  'DROP'
        )::bigint
            AS drop_count,

        count(*) FILTER (
            WHERE expected.rollover_action =
                  'NEEDS_REVIEW'
        )::bigint
            AS needs_review_count,

        v_audit_rows_inserted
            AS audit_rows_inserted,

        v_next_contract_rows_inserted
            AS next_contract_rows_inserted

    FROM
        nffl.preview_contract_season_reconciliation(
            p_league_key,
            p_season_year,
            p_end_roster_snapshot_id
        ) expected;
END
$$;


COMMENT ON FUNCTION
nffl.apply_contract_season_reconciliation(
    text,
    integer,
    text,
    text
)
IS
'Idempotently writes annual NFFL contract audit rows and deterministic same-team carry-forward contracts from finalized Yahoo roster evidence. Different-team and unmapped cases remain NEEDS_REVIEW.';