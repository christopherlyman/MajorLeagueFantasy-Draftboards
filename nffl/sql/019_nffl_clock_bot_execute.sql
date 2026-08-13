BEGIN;

REVOKE ALL
ON FUNCTION nffl.process_draft_clock(text)
FROM PUBLIC;

GRANT EXECUTE
ON FUNCTION nffl.process_draft_clock(text)
TO nffl_discord_reader;

COMMIT;
