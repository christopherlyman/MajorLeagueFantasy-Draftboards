import { getPool } from "./db";


export type DraftRuntimeContext = {
  draftStatus: string;
  currentPickId: string | null;
  currentOwnerTeamKey: string | null;
};


type RawDraftRuntimeContext = {
  draft_status: string;
  current_pick_id: string | null;
  current_owner_team_key: string | null;
};


export async function loadDraftRuntimeContext(
  draftKey: string,
): Promise<DraftRuntimeContext> {
  const client =
    await getPool().connect();

  try {
    await client.query(
      "BEGIN READ ONLY",
    );

    const result =
      await client.query<RawDraftRuntimeContext>(
        `
        SELECT
            d.status::text
                AS draft_status,
            dr.current_pick_id::text
                AS current_pick_id,
            dp.current_owner_team_key::text
                AS current_owner_team_key
        FROM mlf.draft AS d
        LEFT JOIN mlf.draft_runtime AS dr
          ON dr.draft_key = d.draft_key
        LEFT JOIN mlf.draft_pick AS dp
          ON dp.draft_key = d.draft_key
         AND dp.pick_id = dr.current_pick_id
        WHERE d.draft_key = $1
        `,
        [draftKey],
      );

    if (result.rows.length !== 1) {
      throw new Error(
        `Expected one MLF draft runtime row for ${draftKey}; `
        + `received ${result.rows.length}.`,
      );
    }

    await client.query(
      "ROLLBACK",
    );

    const row = result.rows[0];

    return {
      draftStatus:
        String(row.draft_status || ""),
      currentPickId:
        row.current_pick_id
          ? String(row.current_pick_id)
          : null,
      currentOwnerTeamKey:
        row.current_owner_team_key
          ? String(
              row.current_owner_team_key,
            )
          : null,
    };
  } catch (error) {
    try {
      await client.query(
        "ROLLBACK",
      );
    } catch {
      // Preserve the original failure.
    }

    throw error;
  } finally {
    client.release();
  }
}
