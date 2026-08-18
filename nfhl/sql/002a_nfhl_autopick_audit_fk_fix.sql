BEGIN;

ALTER TABLE nfhl.draft_autopick_audit
    DROP CONSTRAINT draft_autopick_audit_pick_fk;

ALTER TABLE nfhl.draft_autopick_audit
    ADD CONSTRAINT draft_autopick_audit_pick_fk
    FOREIGN KEY (draft_key, pick_id)
    REFERENCES nfhl.draft_pick(draft_key, pick_id)
    ON DELETE SET NULL (pick_id);

COMMIT;
