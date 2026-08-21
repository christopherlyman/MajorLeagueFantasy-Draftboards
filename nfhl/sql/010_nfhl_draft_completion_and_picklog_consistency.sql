
BEGIN;

CREATE OR REPLACE FUNCTION
nfhl.sync_draft_selection_lifecycle()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, nfhl, public
AS $function$
DECLARE
    v_draft_key text;

    v_expected_count integer;
    v_selection_count integer;

    v_state jsonb;
    v_pick_log jsonb;
    v_new_sha text;
BEGIN

    IF TG_OP = 'INSERT' THEN
        v_draft_key = NEW.draft_key;

    ELSIF TG_OP = 'DELETE' THEN
        v_draft_key = OLD.draft_key;

    ELSE
        RAISE EXCEPTION
            'Unsupported lifecycle operation %.',
            TG_OP;
    END IF;


    SELECT
        d.manager_count
        * d.rounds_total
    INTO
        v_expected_count
    FROM nfhl.draft d
    WHERE d.draft_key =
          v_draft_key
    FOR UPDATE;


    /*
     * During ON DELETE CASCADE of the parent draft,
     * the parent may no longer be visible. Nothing
     * remains to synchronize in that case.
     */
    IF NOT FOUND THEN

        IF TG_OP = 'INSERT' THEN
            RETURN NEW;
        END IF;

        RETURN OLD;

    END IF;


    SELECT COUNT(*)
    INTO v_selection_count
    FROM nfhl.draft_selection s
    WHERE s.draft_key =
          v_draft_key;


    -- ============================================================
    -- INSERT: FINAL SELECTION COMPLETES THE DRAFT
    -- ============================================================

    IF TG_OP = 'INSERT' THEN

        IF (
            v_expected_count > 0
            AND
            v_selection_count
            = v_expected_count
        ) THEN

            UPDATE nfhl.draft
               SET status =
                       'COMPLETE',
                   updated_at_utc =
                       now()
             WHERE draft_key =
                   v_draft_key
               AND status =
                   'ACTIVE';

        END IF;

        RETURN NEW;

    END IF;


    -- ============================================================
    -- DELETE: REMOVE SUPERSEDED LOG HISTORY
    -- ============================================================

    SELECT state_json
    INTO v_state
    FROM nfhl.draft_state
    WHERE draft_key =
          v_draft_key
    FOR UPDATE;


    IF FOUND THEN

        SELECT
            COALESCE(
                jsonb_agg(
                    item.value
                    ORDER BY
                        item.ordinality
                ),
                '[]'::jsonb
            )
        INTO
            v_pick_log
        FROM jsonb_array_elements(
            CASE
                WHEN jsonb_typeof(
                    v_state
                    -> 'pick_log'
                ) = 'array'
                THEN
                    v_state
                    -> 'pick_log'

                ELSE
                    '[]'::jsonb
            END
        )
        WITH ORDINALITY
        AS item(
            value,
            ordinality
        )
        WHERE COALESCE(
            item.value
            ->> 'pick_id',
            ''
        ) <> OLD.pick_id;


        v_state =
            jsonb_set(
                v_state,
                ARRAY[
                    'pick_log'
                ],
                v_pick_log,
                true
            );


        v_new_sha =
            encode(
                public.digest(
                    pg_catalog.convert_to(
                        v_state::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );


        UPDATE nfhl.draft_state
           SET state_json =
                   v_state,
               state_sha256 =
                   v_new_sha,
               updated_at_utc =
                   now()
         WHERE draft_key =
               v_draft_key;

    END IF;


    -- ============================================================
    -- DELETE FROM COMPLETE REOPENS THE DRAFT
    -- ============================================================

    IF (
        v_selection_count
        < v_expected_count
    ) THEN

        UPDATE nfhl.draft
           SET status =
                   'ACTIVE',
               updated_at_utc =
                   now()
         WHERE draft_key =
               v_draft_key
           AND status =
               'COMPLETE';

    END IF;


    RETURN OLD;

END;
$function$;


DROP TRIGGER IF EXISTS
    draft_selection_lifecycle_trg
ON nfhl.draft_selection;


CREATE TRIGGER
    draft_selection_lifecycle_trg
AFTER INSERT OR DELETE
ON nfhl.draft_selection
FOR EACH ROW
EXECUTE FUNCTION
    nfhl.sync_draft_selection_lifecycle();


COMMENT ON FUNCTION
nfhl.sync_draft_selection_lifecycle()
IS
'Keeps NFHL draft lifecycle and durable pick_log synchronized with canonical draft_selection truth.';


COMMIT;
