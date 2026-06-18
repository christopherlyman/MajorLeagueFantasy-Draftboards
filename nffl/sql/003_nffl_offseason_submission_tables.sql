CREATE TABLE IF NOT EXISTS nffl.league_event (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    event_code text NOT NULL,
    event_label text NOT NULL,
    event_date date NOT NULL,
    event_time_local time,
    timezone text NOT NULL DEFAULT 'America/New_York',
    is_active boolean NOT NULL DEFAULT true,
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, event_code)
);

INSERT INTO nffl.league_event (
    league_key,
    season_year,
    event_code,
    event_label,
    event_date,
    event_time_local,
    timezone,
    is_active,
    note,
    updated_at_utc
)
VALUES
(
    '470.l.84346',
    2026,
    'QO_FT_SUBMISSION_DEADLINE',
    'QO/FT Submission Deadline',
    DATE '2026-08-01',
    NULL,
    'America/New_York',
    true,
    'Managers may reset and resubmit QO/FT choices until this date unless commissioner locks earlier.',
    now()
),
(
    '470.l.84346',
    2026,
    'DRAFT_START',
    'Draft Start',
    DATE '2026-08-02',
    NULL,
    'America/New_York',
    true,
    'NFFL 2026 draft target start date.',
    now()
)
ON CONFLICT (league_key, season_year, event_code)
DO UPDATE SET
    event_label = EXCLUDED.event_label,
    event_date = EXCLUDED.event_date,
    event_time_local = EXCLUDED.event_time_local,
    timezone = EXCLUDED.timezone,
    is_active = EXCLUDED.is_active,
    note = EXCLUDED.note,
    updated_at_utc = now();

CREATE TABLE IF NOT EXISTS nffl.offseason_team_submission (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    submission_status text NOT NULL DEFAULT 'DRAFT',
    revision_number integer NOT NULL DEFAULT 0,
    submitted_at_utc timestamptz,
    submitted_by text,
    reset_at_utc timestamptz,
    reset_by text,
    reset_count integer NOT NULL DEFAULT 0,
    locked_at_utc timestamptz,
    locked_by text,
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, team_key),
    CHECK (submission_status IN ('DRAFT', 'SUBMITTED', 'LOCKED'))
);

CREATE TABLE IF NOT EXISTS nffl.offseason_keeper_decision (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    decision_type text NOT NULL,
    decision_status text NOT NULL DEFAULT 'DRAFT',
    revision_number integer NOT NULL DEFAULT 0,
    decided_by text,
    decided_at_utc timestamptz NOT NULL DEFAULT now(),
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, season_year, team_key, yahoo_player_key),
    CHECK (decision_type IN ('QO1', 'QO2', 'QO3', 'QO4', 'FT', 'RELEASE')),
    CHECK (decision_status IN ('DRAFT', 'SUBMITTED', 'LOCKED'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_nffl_offseason_keeper_decision_one_qo_ft_slot
ON nffl.offseason_keeper_decision (league_key, season_year, team_key, decision_type)
WHERE decision_type IN ('QO1', 'QO2', 'QO3', 'QO4', 'FT');

CREATE TABLE IF NOT EXISTS nffl.offseason_keeper_decision_audit (
    audit_id bigserial PRIMARY KEY,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    action_type text NOT NULL,
    revision_number integer NOT NULL,
    action_by text,
    action_at_utc timestamptz NOT NULL DEFAULT now(),
    decision_payload jsonb NOT NULL DEFAULT '[]'::jsonb,
    note text,
    CHECK (action_type IN ('SAVE_DRAFT', 'SUBMIT', 'RESET', 'LOCK', 'UNLOCK'))
);

INSERT INTO nffl.offseason_team_submission (
    league_key,
    season_year,
    team_key,
    submission_status,
    updated_at_utc
)
SELECT
    league_key,
    season_year,
    team_key,
    'DRAFT',
    now()
FROM nffl.team
WHERE league_key='470.l.84346'
  AND season_year=2026
ON CONFLICT (league_key, season_year, team_key)
DO NOTHING;

CREATE OR REPLACE VIEW nffl.v_offseason_submission_status AS
SELECT
    t.league_key,
    t.season_year,
    t.team_key,
    t.team_name,
    t.owner_name,
    COALESCE(s.submission_status, 'DRAFT') AS submission_status,
    COALESCE(s.revision_number, 0) AS revision_number,
    COALESCE(s.reset_count, 0) AS reset_count,
    s.submitted_at_utc,
    s.reset_at_utc,
    s.locked_at_utc,
    deadline.event_date AS qo_ft_submission_deadline,
    draft_start.event_date AS draft_start_date
FROM nffl.team t
LEFT JOIN nffl.offseason_team_submission s
  ON s.league_key = t.league_key
 AND s.season_year = t.season_year
 AND s.team_key = t.team_key
LEFT JOIN nffl.league_event deadline
  ON deadline.league_key = t.league_key
 AND deadline.season_year = t.season_year
 AND deadline.event_code = 'QO_FT_SUBMISSION_DEADLINE'
LEFT JOIN nffl.league_event draft_start
  ON draft_start.league_key = t.league_key
 AND draft_start.season_year = t.season_year
 AND draft_start.event_code = 'DRAFT_START';
