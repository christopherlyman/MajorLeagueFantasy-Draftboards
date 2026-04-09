BEGIN;

CREATE TABLE IF NOT EXISTS public.draftboard_state (
  draft_key           text PRIMARY KEY,
  schema_version      text NOT NULL,
  state_json          jsonb NOT NULL,
  state_sha256        text NOT NULL,
  updated_at_utc      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_draftboard_state_updated_at
  ON public.draftboard_state (updated_at_utc DESC);

COMMIT;
