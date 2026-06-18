CREATE TABLE IF NOT EXISTS nffl.app_user (
    user_id bigserial PRIMARY KEY,
    username text NOT NULL UNIQUE,
    display_name text NOT NULL,
    email text,
    password_hash text,
    role text NOT NULL DEFAULT 'manager',
    is_active boolean NOT NULL DEFAULT true,
    must_set_password boolean NOT NULL DEFAULT true,
    last_login_at_utc timestamptz,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    CHECK (role IN ('manager', 'commissioner', 'admin', 'viewer'))
);

CREATE TABLE IF NOT EXISTS nffl.app_user_team_access (
    user_id bigint NOT NULL REFERENCES nffl.app_user(user_id) ON DELETE CASCADE,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    access_role text NOT NULL DEFAULT 'manager',
    can_edit_qo_ft boolean NOT NULL DEFAULT true,
    can_reset_submission boolean NOT NULL DEFAULT true,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, league_key, season_year, team_key),
    CHECK (access_role IN ('manager', 'co_manager', 'commissioner_view'))
);

CREATE TABLE IF NOT EXISTS nffl.app_user_password_token (
    token_id bigserial PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES nffl.app_user(user_id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    token_type text NOT NULL,
    expires_at_utc timestamptz NOT NULL,
    used_at_utc timestamptz,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    CHECK (token_type IN ('initial_set_password', 'password_reset'))
);

CREATE TABLE IF NOT EXISTS nffl.app_auth_audit (
    audit_id bigserial PRIMARY KEY,
    user_id bigint REFERENCES nffl.app_user(user_id) ON DELETE SET NULL,
    username text,
    action_type text NOT NULL,
    success boolean NOT NULL,
    ip_address text,
    user_agent text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    note text,
    CHECK (action_type IN ('login', 'logout', 'set_password', 'reset_password', 'failed_login'))
);

CREATE OR REPLACE VIEW nffl.v_app_user_access AS
SELECT
    u.user_id,
    u.username,
    u.display_name,
    u.email,
    u.role,
    u.is_active,
    u.must_set_password,
    a.league_key,
    a.season_year,
    a.team_key,
    t.team_name,
    t.owner_name,
    a.access_role,
    a.can_edit_qo_ft,
    a.can_reset_submission
FROM nffl.app_user u
LEFT JOIN nffl.app_user_team_access a
  ON a.user_id = u.user_id
LEFT JOIN nffl.team t
  ON t.league_key = a.league_key
 AND t.season_year = a.season_year
 AND t.team_key = a.team_key;

-- Commissioner account: all-team access comes from role, not per-team row.
INSERT INTO nffl.app_user (
    username,
    display_name,
    role,
    is_active,
    must_set_password,
    updated_at_utc
)
VALUES (
    'commissioner',
    'Commissioner',
    'commissioner',
    true,
    true,
    now()
)
ON CONFLICT (username)
DO UPDATE SET
    display_name = EXCLUDED.display_name,
    role = EXCLUDED.role,
    is_active = EXCLUDED.is_active,
    updated_at_utc = now();

-- One manager login per 2026 team.
WITH manager_seed AS (
    SELECT
        team_key,
        team_name,
        owner_name,
        CASE
            WHEN lower(owner_name) = 'chad b' THEN 'chadb'
            ELSE regexp_replace(lower(owner_name), '[^a-z0-9]+', '', 'g')
        END AS username
    FROM nffl.team
    WHERE league_key='470.l.84346'
      AND season_year=2026
),
upserted_users AS (
    INSERT INTO nffl.app_user (
        username,
        display_name,
        role,
        is_active,
        must_set_password,
        updated_at_utc
    )
    SELECT
        username,
        owner_name,
        'manager',
        true,
        true,
        now()
    FROM manager_seed
    ON CONFLICT (username)
    DO UPDATE SET
        display_name = EXCLUDED.display_name,
        role = EXCLUDED.role,
        is_active = EXCLUDED.is_active,
        updated_at_utc = now()
    RETURNING user_id, username
)
INSERT INTO nffl.app_user_team_access (
    user_id,
    league_key,
    season_year,
    team_key,
    access_role,
    can_edit_qo_ft,
    can_reset_submission,
    updated_at_utc
)
SELECT
    u.user_id,
    '470.l.84346',
    2026,
    s.team_key,
    'manager',
    true,
    true,
    now()
FROM manager_seed s
JOIN upserted_users u
  ON u.username = s.username
ON CONFLICT (user_id, league_key, season_year, team_key)
DO UPDATE SET
    access_role = EXCLUDED.access_role,
    can_edit_qo_ft = EXCLUDED.can_edit_qo_ft,
    can_reset_submission = EXCLUDED.can_reset_submission,
    updated_at_utc = now();
