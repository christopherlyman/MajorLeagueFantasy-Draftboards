-- NFHL current-Yahoo player-universe availability.
--
-- Player rows are retained even when Yahoo no longer returns them so that:
--   * completed draft selections retain historical player metadata;
--   * saved Auto-Pick queue identities are not silently deleted;
--   * historical/enrichment relationships remain intact.
--
-- Draftability is represented independently from row existence.
--
-- A validated Yahoo player refresh will:
--   * set is_yahoo_current = true for every player in the complete response;
--   * set last_yahoo_seen_at_utc for those returned players;
--   * set is_yahoo_current = false for current-season players absent from the
--     complete validated Yahoo response;
--   * never delete player_universe rows as part of normal synchronization.

ALTER TABLE nfhl.player_universe
    ADD COLUMN is_yahoo_current boolean NOT NULL DEFAULT true;

ALTER TABLE nfhl.player_universe
    ADD COLUMN last_yahoo_seen_at_utc timestamptz;

COMMENT ON COLUMN nfhl.player_universe.is_yahoo_current IS
    'True when the player was present in the most recent complete validated Yahoo league player-universe refresh.';

COMMENT ON COLUMN nfhl.player_universe.last_yahoo_seen_at_utc IS
    'UTC timestamp of the most recent complete validated Yahoo refresh that returned this player.';
