BEGIN;

CREATE TABLE IF NOT EXISTS nfhl.player_season_stats (
    league_key text NOT NULL,
    draft_season_year integer NOT NULL,
    stats_season_year integer NOT NULL,

    yahoo_player_id text NOT NULL,
    source_player_key text NOT NULL,
    current_yahoo_player_key text NOT NULL,
    source_game_key text NOT NULL,

    source_position_type text NULL,

    gp integer NOT NULL DEFAULT 0,

    g integer NOT NULL DEFAULT 0,
    a integer NOT NULL DEFAULT 0,
    pim integer NOT NULL DEFAULT 0,
    ppp integer NOT NULL DEFAULT 0,
    shp integer NOT NULL DEFAULT 0,
    sog integer NOT NULL DEFAULT 0,
    hit integer NOT NULL DEFAULT 0,
    blk integer NOT NULL DEFAULT 0,

    w integer NOT NULL DEFAULT 0,
    ga integer NOT NULL DEFAULT 0,
    sv integer NOT NULL DEFAULT 0,
    sho integer NOT NULL DEFAULT 0,

    nfhl_fpts numeric(14,3) NOT NULL,
    nfhl_fpts_per_game numeric(14,4) NULL,

    scoring_snapshot jsonb NOT NULL,
    raw_payload jsonb NULL,

    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT player_season_stats_pkey
        PRIMARY KEY (
            league_key,
            draft_season_year,
            stats_season_year,
            yahoo_player_id
        ),

    CONSTRAINT player_season_stats_draft_season_ck
        CHECK (draft_season_year > 0),

    CONSTRAINT player_season_stats_stats_season_ck
        CHECK (stats_season_year > 0),

    CONSTRAINT player_season_stats_position_type_ck
        CHECK (
            source_position_type IS NULL
            OR source_position_type IN ('P', 'G')
        ),

    CONSTRAINT player_season_stats_current_player_fk
        FOREIGN KEY (
            league_key,
            draft_season_year,
            current_yahoo_player_key
        )
        REFERENCES nfhl.player_universe (
            league_key,
            season_year,
            yahoo_player_key
        )
);

CREATE INDEX IF NOT EXISTS
    player_season_stats_current_player_idx
ON nfhl.player_season_stats (
    league_key,
    draft_season_year,
    current_yahoo_player_key
);

CREATE INDEX IF NOT EXISTS
    player_season_stats_fpts_idx
ON nfhl.player_season_stats (
    league_key,
    draft_season_year,
    stats_season_year,
    nfhl_fpts DESC
);

COMMIT;
