-- MLF Relational Migration - 002
-- Canonical MLF player-control domain.
--
-- IMPORTANT:
--   * Source-only construction. Do not apply to production yet.
--   * Existing public.* MLF tables remain untouched.
--   * yahoo_player_key is canonical player identity.
--   * All truth is scoped by (league_key, season_year).
--   * Contracts, PT, and QO remain separate truth domains.
--
-- Deliberate normalization from legacy public.*:
--   * contract_override uses canonical team_key rather than
--     retaining redundant yahoo_team_key / yahoo_team_name fields.
--   * qualifying_offer uses canonical team_key and does not retain
--     the legacy redundant Yahoo team-key alias.
--   * timestamps use *_utc naming in the rebuilt schema.

BEGIN;

CREATE TABLE mlf.contract (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text,
    yahoo_player_key text NOT NULL,
    years_remaining integer NOT NULL,
    note text,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT contract_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT contract_team_fk
        FOREIGN KEY (
            league_key,
            season_year,
            team_key
        )
        REFERENCES mlf.team (
            league_key,
            season_year,
            team_key
        ),

    CONSTRAINT contract_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    -- No artificial upper bound here.
    -- MLF contract lengths may begin above the currently observed
    -- years-remaining values.
    CONSTRAINT contract_years_remaining_ck
        CHECK (years_remaining >= 0),

    CONSTRAINT contract_player_key_not_blank_ck
        CHECK (btrim(yahoo_player_key) <> ''),

    CONSTRAINT contract_team_key_not_blank_ck
        CHECK (
            team_key IS NULL
            OR btrim(team_key) <> ''
        )
);

CREATE TABLE mlf.contract_override (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    yahoo_player_key text NOT NULL,

    -- NULL is allowed for a void/ownership-clearing correction.
    team_key text,

    -- >0 defines/replaces effective contract.
    --  0 voids effective contract.
    years_remaining integer NOT NULL,

    note text,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT contract_override_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT contract_override_team_fk
        FOREIGN KEY (
            league_key,
            season_year,
            team_key
        )
        REFERENCES mlf.team (
            league_key,
            season_year,
            team_key
        ),

    CONSTRAINT contract_override_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    CONSTRAINT contract_override_years_remaining_ck
        CHECK (years_remaining >= 0),

    CONSTRAINT contract_override_player_key_not_blank_ck
        CHECK (btrim(yahoo_player_key) <> ''),

    CONSTRAINT contract_override_team_key_not_blank_ck
        CHECK (
            team_key IS NULL
            OR btrim(team_key) <> ''
        )
);

CREATE TABLE mlf.prospect_tag (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    note text,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT prospect_tag_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT prospect_tag_team_fk
        FOREIGN KEY (
            league_key,
            season_year,
            team_key
        )
        REFERENCES mlf.team (
            league_key,
            season_year,
            team_key
        ),

    CONSTRAINT prospect_tag_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    CONSTRAINT prospect_tag_team_key_not_blank_ck
        CHECK (btrim(team_key) <> ''),

    CONSTRAINT prospect_tag_player_key_not_blank_ck
        CHECK (btrim(yahoo_player_key) <> '')
);

CREATE TABLE mlf.qualifying_offer (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    team_key text NOT NULL,
    yahoo_player_key text NOT NULL,
    qo_level integer NOT NULL,
    note text,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT qualifying_offer_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            yahoo_player_key
        ),

    CONSTRAINT qualifying_offer_team_fk
        FOREIGN KEY (
            league_key,
            season_year,
            team_key
        )
        REFERENCES mlf.team (
            league_key,
            season_year,
            team_key
        ),

    CONSTRAINT qualifying_offer_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    CONSTRAINT qualifying_offer_level_ck
        CHECK (
            qo_level >= 1
            AND qo_level <= 5
        ),

    CONSTRAINT qualifying_offer_team_key_not_blank_ck
        CHECK (
            btrim(team_key) <> ''
            AND team_key NOT LIKE 'TEAM_%'
        ),

    CONSTRAINT qualifying_offer_player_key_not_blank_ck
        CHECK (btrim(yahoo_player_key) <> '')
);

CREATE TABLE mlf.qo_round_state (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    current_round integer NOT NULL DEFAULT 0,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT qo_round_state_pkey
        PRIMARY KEY (
            league_key,
            season_year
        ),

    CONSTRAINT qo_round_state_season_year_ck
        CHECK (
            season_year >= 2000
            AND season_year <= 2100
        ),

    CONSTRAINT qo_round_state_current_round_ck
        CHECK (
            current_round >= 0
            AND current_round <= 5
        )
);

-- Effective contract resolution.
--
-- An override may:
--   * replace an existing contract,
--   * define effective contract state,
--   * or void a contract with years_remaining = 0.
--
-- Therefore this is a FULL OUTER JOIN rather than an inner/left-only
-- overlay.

CREATE VIEW mlf.v_contract_effective AS
SELECT
    COALESCE(o.league_key, c.league_key) AS league_key,
    COALESCE(o.season_year, c.season_year) AS season_year,
    COALESCE(
        o.yahoo_player_key,
        c.yahoo_player_key
    ) AS yahoo_player_key,

    CASE
        WHEN o.yahoo_player_key IS NOT NULL
            THEN o.team_key
        ELSE c.team_key
    END AS team_key,

    CASE
        WHEN o.yahoo_player_key IS NOT NULL
            THEN o.years_remaining
        ELSE c.years_remaining
    END AS years_remaining,

    CASE
        WHEN o.yahoo_player_key IS NOT NULL
            THEN o.note
        ELSE c.note
    END AS note,

    (o.yahoo_player_key IS NOT NULL) AS override_applied,

    CASE
        WHEN o.yahoo_player_key IS NOT NULL
            THEN 'override'
        ELSE 'contract'
    END AS effective_source,

    CASE
        WHEN o.yahoo_player_key IS NOT NULL
            THEN o.updated_at_utc
        ELSE c.updated_at_utc
    END AS updated_at_utc

FROM mlf.contract c
FULL OUTER JOIN mlf.contract_override o
    ON o.league_key = c.league_key
   AND o.season_year = c.season_year
   AND o.yahoo_player_key = c.yahoo_player_key;

CREATE VIEW mlf.v_active_contract AS
SELECT
    league_key,
    season_year,
    team_key,
    yahoo_player_key,
    years_remaining,
    note,
    override_applied,
    effective_source,
    updated_at_utc
FROM mlf.v_contract_effective
WHERE years_remaining > 0
  AND team_key IS NOT NULL;

CREATE INDEX ix_mlf_contract_team
    ON mlf.contract (
        league_key,
        season_year,
        team_key
    );

CREATE INDEX ix_mlf_contract_override_team
    ON mlf.contract_override (
        league_key,
        season_year,
        team_key
    );

CREATE INDEX ix_mlf_prospect_tag_team
    ON mlf.prospect_tag (
        league_key,
        season_year,
        team_key
    );

CREATE INDEX ix_mlf_qualifying_offer_team_level
    ON mlf.qualifying_offer (
        league_key,
        season_year,
        team_key,
        qo_level
    );

INSERT INTO mlf.schema_migration (
    migration_version,
    description
)
VALUES (
    '002',
    'Canonical MLF contract, prospect-tag, and qualifying-offer domain'
);

COMMIT;