BEGIN;

CREATE TABLE mlf.team_gateway_link (
    league_key          text        NOT NULL,
    season_year         integer     NOT NULL,
    franchise_id        bigint      NOT NULL,
    team_key            text        NOT NULL,
    link_token          text        NOT NULL,

    is_active           boolean     NOT NULL DEFAULT true,
    claim_count         integer     NOT NULL DEFAULT 0,
    last_claimed_at_utc timestamptz,

    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT team_gateway_link_pkey
        PRIMARY KEY (
            league_key,
            season_year,
            team_key
        ),

    CONSTRAINT team_gateway_link_token_uq
        UNIQUE (link_token),

    CONSTRAINT team_gateway_link_franchise_season_fk
        FOREIGN KEY (
            franchise_id,
            season_year
        )
        REFERENCES public.franchise_season_team (
            franchise_id,
            season_year
        )
        ON DELETE CASCADE,

    CONSTRAINT team_gateway_link_claim_count_ck
        CHECK (claim_count >= 0),

    CONSTRAINT team_gateway_link_season_ck
        CHECK (season_year > 0)
);

CREATE INDEX team_gateway_link_active_idx
    ON mlf.team_gateway_link (
        league_key,
        season_year,
        is_active
    );

CREATE TABLE mlf.team_gateway_audit (
    audit_id              bigserial   PRIMARY KEY,
    league_key            text        NOT NULL,
    season_year           integer     NOT NULL,

    selected_role         text        NOT NULL,
    selected_franchise_id bigint,
    selected_team_key     text,
    selected_team_name    text,

    action_type           text        NOT NULL,
    action_note           text,

    created_at_utc        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT team_gateway_audit_selected_role_ck
        CHECK (
            selected_role IN (
                'public',
                'manager',
                'commissioner'
            )
        ),

    CONSTRAINT team_gateway_audit_season_ck
        CHECK (season_year > 0)
);

CREATE INDEX team_gateway_audit_recent_idx
    ON mlf.team_gateway_audit (
        league_key,
        season_year,
        audit_id DESC
    );

COMMIT;