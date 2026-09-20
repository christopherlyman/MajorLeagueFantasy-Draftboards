-- Commissioner Tools commercial league authorization.
--
-- This table is intentionally independent of auth_user_league_role because
-- commercial commissioner authorization must exist before franchises exist.

CREATE TABLE IF NOT EXISTS public.commercial_league_user_role (
    user_id bigint NOT NULL,
    league_key text NOT NULL,
    season_year integer NOT NULL,
    role_code text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    updated_at_utc timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT commercial_league_user_role_pkey
        PRIMARY KEY (
            user_id,
            league_key,
            season_year,
            role_code
        ),

    CONSTRAINT commercial_league_user_role_user_fk
        FOREIGN KEY (user_id)
        REFERENCES public.auth_user (user_id)
        ON DELETE CASCADE,

    CONSTRAINT commercial_league_user_role_profile_fk
        FOREIGN KEY (
            league_key,
            season_year
        )
        REFERENCES public.league_profile (
            league_key,
            season_year
        )
        ON DELETE CASCADE,

    CONSTRAINT commercial_league_user_role_key_check
        CHECK (btrim(league_key) <> ''),

    CONSTRAINT commercial_league_user_role_season_check
        CHECK (season_year > 0),

    CONSTRAINT commercial_league_user_role_role_check
        CHECK (role_code = 'commissioner')
);

CREATE INDEX IF NOT EXISTS
    ix_commercial_league_user_role_user
ON public.commercial_league_user_role (
    user_id,
    active,
    league_key,
    season_year
);

CREATE INDEX IF NOT EXISTS
    ix_commercial_league_user_role_league
ON public.commercial_league_user_role (
    league_key,
    season_year,
    active,
    role_code
);
