BEGIN;

CREATE TABLE IF NOT EXISTS nfhl.season_team_slot (
    league_key               text        NOT NULL,
    season_year              integer     NOT NULL,
    league_slot_number       integer     NOT NULL,

    prior_season_year         integer     NOT NULL,
    prior_manager_name        text        NOT NULL,
    prior_team_name           text        NOT NULL,

    assignment_status         text        NOT NULL DEFAULT 'PENDING',

    replacement_manager_name  text,
    replacement_team_name     text,

    current_team_key          text,

    commissioner_note         text,

    created_at_utc            timestamptz NOT NULL DEFAULT now(),
    updated_at_utc            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT season_team_slot_pk
        PRIMARY KEY (
            league_key,
            season_year,
            league_slot_number
        ),

    CONSTRAINT season_team_slot_number_ck
        CHECK (
            league_slot_number BETWEEN 1 AND 14
        ),

    CONSTRAINT season_team_slot_year_ck
        CHECK (
            prior_season_year < season_year
        ),

    CONSTRAINT season_team_slot_status_ck
        CHECK (
            assignment_status IN (
                'PENDING',
                'RETURNING',
                'REPLACED'
            )
        ),

    CONSTRAINT season_team_slot_team_fk
        FOREIGN KEY (
            league_key,
            season_year,
            current_team_key
        )
        REFERENCES nfhl.team (
            league_key,
            season_year,
            team_key
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    season_team_slot_current_team_ux
ON nfhl.season_team_slot (
    league_key,
    season_year,
    current_team_key
)
WHERE current_team_key IS NOT NULL;

COMMENT ON TABLE nfhl.season_team_slot IS
'Administrative league-membership slots used for roll call and season-to-season manager/team assignment. league_slot_number is NOT draft order.';

COMMENT ON COLUMN nfhl.season_team_slot.league_slot_number IS
'Persistent administrative league slot. This value must never be interpreted as draft position.';

COMMENT ON COLUMN nfhl.season_team_slot.assignment_status IS
'PENDING = prior slot unresolved; RETURNING = prior manager/team returning; REPLACED = prior slot assigned to a replacement manager.';


-- ================================================================
-- Seed the fourteen known 2025 NFHL league membership slots.
--
-- These rows DO NOT create Yahoo teams and DO NOT satisfy the
-- production 14-team initialization requirement.
-- ================================================================

INSERT INTO nfhl.season_team_slot (
    league_key,
    season_year,
    league_slot_number,
    prior_season_year,
    prior_manager_name,
    prior_team_name,
    assignment_status
)
VALUES
    ('477.l.10961', 2026,  1, 2025, 'Jackson',       'Took An Arrow To The Knee', 'PENDING'),
    ('477.l.10961', 2026,  2, 2025, 'robert',        'Face Washers',               'PENDING'),
    ('477.l.10961', 2026,  3, 2025, 'Steady',        'Drop The Gloves',             'PENDING'),
    ('477.l.10961', 2026,  4, 2025, 'tim',           'Debbie BarDowners',           'PENDING'),
    ('477.l.10961', 2026,  5, 2025, 'Christopher',   'Putting on the Foil',         'PENDING'),
    ('477.l.10961', 2026,  6, 2025, 'Victor',        'What is a blue line?',        'PENDING'),
    ('477.l.10961', 2026,  7, 2025, 'Dan',           'Duck Dynasty',                'PENDING'),
    ('477.l.10961', 2026,  8, 2025, 'Brent',         'Big Cat Habitat',             'PENDING'),
    ('477.l.10961', 2026,  9, 2025, 'Sid The Adult', 'Jake N Bake',                 'PENDING'),
    ('477.l.10961', 2026, 10, 2025, 'Michael',       'Lucic''s Mugshot',            'PENDING'),
    ('477.l.10961', 2026, 11, 2025, 'William',       'Leafs Cup Parade',            'PENDING'),
    ('477.l.10961', 2026, 12, 2025, 'Connor',        'The empire strikes Bratt',    'PENDING'),
    ('477.l.10961', 2026, 13, 2025, 'Chance',        'Kraken a cold one',           'PENDING'),
    ('477.l.10961', 2026, 14, 2025, 'Zachary',       'Mantha Rays',                 'PENDING')
ON CONFLICT (
    league_key,
    season_year,
    league_slot_number
)
DO NOTHING;


-- ================================================================
-- AUTO-LINK PHASE 1
-- Exact prior/current team-name match.
-- ================================================================

UPDATE nfhl.season_team_slot s
SET
    current_team_key = t.team_key,
    assignment_status = 'RETURNING',
    updated_at_utc = now()
FROM nfhl.team t
WHERE
    s.league_key = '477.l.10961'
    AND s.season_year = 2026
    AND s.current_team_key IS NULL
    AND s.assignment_status = 'PENDING'

    AND t.league_key = s.league_key
    AND t.season_year = s.season_year

    AND lower(trim(t.team_name))
        = lower(trim(s.prior_team_name));


-- ================================================================
-- AUTO-LINK PHASE 2
-- Remaining exact prior/current Yahoo-manager-name matches.
-- ================================================================

UPDATE nfhl.season_team_slot s
SET
    current_team_key = t.team_key,
    assignment_status = 'RETURNING',
    updated_at_utc = now()
FROM nfhl.team t
WHERE
    s.league_key = '477.l.10961'
    AND s.season_year = 2026
    AND s.current_team_key IS NULL
    AND s.assignment_status = 'PENDING'

    AND t.league_key = s.league_key
    AND t.season_year = s.season_year

    AND NULLIF(trim(t.owner_name), '') IS NOT NULL
    AND lower(trim(t.owner_name))
        = lower(trim(s.prior_manager_name))

    AND NOT EXISTS (
        SELECT 1
        FROM nfhl.season_team_slot other
        WHERE
            other.league_key = s.league_key
            AND other.season_year = s.season_year
            AND other.current_team_key = t.team_key
    );


-- ================================================================
-- Integrity: exactly fourteen administrative league slots.
-- ================================================================

DO $$
DECLARE
    v_count integer;
BEGIN
    SELECT COUNT(*)
    INTO v_count
    FROM nfhl.season_team_slot
    WHERE league_key = '477.l.10961'
      AND season_year = 2026;

    IF v_count <> 14 THEN
        RAISE EXCEPTION
            'NFHL season-team-slot integrity failed: expected 14, found %',
            v_count;
    END IF;
END
$$;

COMMIT;
