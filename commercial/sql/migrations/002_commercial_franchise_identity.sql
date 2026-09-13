CREATE TABLE IF NOT EXISTS public.franchise (
    franchise_id bigserial PRIMARY KEY,
    franchise_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.franchise_season_team (
    franchise_id bigint NOT NULL,
    season_year integer NOT NULL,
    league_key text NOT NULL,
    team_key text NOT NULL,
    team_id text,
    team_name text,
    owner_guid text,
    owner_name text,
    source text NOT NULL DEFAULT 'auto',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT franchise_season_team_pkey
        PRIMARY KEY (franchise_id, season_year),

    CONSTRAINT franchise_season_team_franchise_id_fkey
        FOREIGN KEY (franchise_id)
        REFERENCES public.franchise (franchise_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_fst_season_teamkey
    ON public.franchise_season_team (
        season_year,
        team_key
    );
