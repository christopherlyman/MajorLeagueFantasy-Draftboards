CREATE TABLE nffl.historical_contract_episode (
    league_key text NOT NULL,
    team_key text NOT NULL,
    source_row_number integer NOT NULL,
    source_owner_name text,
    source_team_name text,
    player_name text NOT NULL,
    yahoo_player_key text,
    source_note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT historical_contract_episode_pkey
        PRIMARY KEY (
            league_key,
            team_key,
            source_row_number
        ),

    CONSTRAINT historical_contract_episode_row_check
        CHECK (source_row_number > 0)
);

CREATE INDEX ix_nffl_historical_contract_episode_player
    ON nffl.historical_contract_episode (
        league_key,
        yahoo_player_key
    );

CREATE TABLE nffl.historical_contract_season (
    league_key text NOT NULL,
    team_key text NOT NULL,
    source_row_number integer NOT NULL,
    season_year integer NOT NULL,
    contract_years integer,
    contract_status text NOT NULL,
    acquisition_type text NOT NULL DEFAULT 'NONE',
    source_value text NOT NULL,
    note text,
    created_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT historical_contract_season_pkey
        PRIMARY KEY (
            league_key,
            team_key,
            source_row_number,
            season_year
        ),

    CONSTRAINT historical_contract_season_episode_fkey
        FOREIGN KEY (
            league_key,
            team_key,
            source_row_number
        )
        REFERENCES nffl.historical_contract_episode (
            league_key,
            team_key,
            source_row_number
        )
        ON DELETE CASCADE,

    CONSTRAINT historical_contract_season_year_check
        CHECK (season_year BETWEEN 2000 AND 2100),

    CONSTRAINT historical_contract_season_status_check
        CHECK (
            contract_status IN (
                'CONTRACT',
                'FT',
                'NO_CONTRACT',
                'EXPIRED',
                'DROPPED'
            )
        ),

    CONSTRAINT historical_contract_season_acquisition_check
        CHECK (
            acquisition_type IN (
                'NONE',
                'TRADE',
                'WAIVER'
            )
        ),

    CONSTRAINT historical_contract_season_value_check
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
        )
);

CREATE INDEX ix_nffl_historical_contract_season_team
    ON nffl.historical_contract_season (
        league_key,
        team_key,
        season_year DESC,
        source_row_number
    );

COMMENT ON TABLE nffl.historical_contract_episode IS
    'One immutable row from a team historical contract worksheet. Duplicate player names represent separate contract episodes.';

COMMENT ON TABLE nffl.historical_contract_season IS
    'One season cell within an immutable historical contract worksheet row.';
