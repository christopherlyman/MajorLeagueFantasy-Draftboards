CREATE TABLE IF NOT EXISTS public.league_profile (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    profile_version integer NOT NULL DEFAULT 1,
    profile_yaml text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    updated_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_by text,
    notes text,
    CONSTRAINT league_profile_pk
        PRIMARY KEY (league_key, season_year)
);

CREATE INDEX IF NOT EXISTS ix_league_profile_active
    ON public.league_profile (is_active);

CREATE TABLE IF NOT EXISTS public.league_profile_history (
    league_key text NOT NULL,
    season_year integer NOT NULL,
    profile_version integer NOT NULL,
    profile_yaml text NOT NULL,
    changed_at_utc timestamptz NOT NULL DEFAULT now(),
    changed_by text,
    notes text,
    CONSTRAINT league_profile_history_pk
        PRIMARY KEY (
            league_key,
            season_year,
            profile_version
        )
);
