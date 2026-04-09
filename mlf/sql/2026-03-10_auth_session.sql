BEGIN;

CREATE TABLE IF NOT EXISTS public.auth_session (
    session_token text PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES public.auth_user(user_id) ON DELETE CASCADE,
    created_at_utc timestamptz NOT NULL DEFAULT now(),
    expires_at_utc timestamptz NOT NULL,
    revoked_at_utc timestamptz NULL
);

CREATE INDEX IF NOT EXISTS ix_auth_session_user_id
    ON public.auth_session(user_id);

CREATE INDEX IF NOT EXISTS ix_auth_session_expires_at_utc
    ON public.auth_session(expires_at_utc);

CREATE INDEX IF NOT EXISTS ix_auth_session_revoked_at_utc
    ON public.auth_session(revoked_at_utc);

COMMIT;
