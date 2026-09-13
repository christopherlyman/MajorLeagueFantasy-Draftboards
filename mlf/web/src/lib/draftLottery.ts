import { getPool } from "./db";

export type DraftOrderRow = {
  pickNumber: number;
  teamKey: string;
  teamName: string;
  ownerName: string;
};

export type DraftLotterySnapshot = {
  draftLabel: string;
  status: string;
  managerCount: number;
  roundsTotal: number;
  firstStandardRound: number;
  orderMode: string;
  order: DraftOrderRow[];
};

type RawOrderRow = {
  slot_number: number | string;
  team_key: string;
  team_name: string | null;
  owner_name: string | null;
};

function numberValue(
  value: number | string,
  label: string,
): number {
  const parsed = Number(value);

  if (!Number.isFinite(parsed)) {
    throw new Error(
      `Invalid ${label}: ${String(value)}`,
    );
  }

  return parsed;
}

export async function loadDraftLottery(
  draftKey: string,
): Promise<DraftLotterySnapshot> {
  const pool = getPool();
  const client = await pool.connect();

  try {
    await client.query("BEGIN READ ONLY");

    const meta =
      await client.query<{
        draft_label: string;
        league_key: string;
        season_year: number;
        status: string;
        manager_count: number;
        rounds_total: number;
        first_standard_round: number;
        draft_order_mode: string;
      }>(
        `
        SELECT
            draft_label,
            league_key,
            season_year,
            status,
            manager_count,
            rounds_total,
            first_standard_round,
            draft_order_mode
        FROM mlf.draft
        WHERE draft_key = $1
        `,
        [draftKey],
      );

    if (meta.rows.length !== 1) {
      throw new Error(
        `Expected one draft row for ${draftKey}; received ${meta.rows.length}.`,
      );
    }

    const draft = meta.rows[0];

    const managerCount =
      numberValue(
        draft.manager_count,
        "manager count",
      );

    const roundsTotal =
      numberValue(
        draft.rounds_total,
        "round count",
      );

    const firstStandardRound =
      numberValue(
        draft.first_standard_round,
        "first standard round",
      );

    if (managerCount !== 16) {
      throw new Error(
        `Expected 16 managers; received ${managerCount}.`,
      );
    }

    if (firstStandardRound !== 6) {
      throw new Error(
        `Expected first standard round 6; received ${firstStandardRound}.`,
      );
    }

    if (draft.draft_order_mode !== "straight") {
      throw new Error(
        `Expected straight draft order; received ${draft.draft_order_mode}.`,
      );
    }

    const result =
      await client.query<RawOrderRow>(
        `
        SELECT
            dp.slot_number,
            dp.column_team_key AS team_key,
            COALESCE(
                ytm.team_name,
                t.team_name,
                dp.column_team_key
            ) AS team_name,
            COALESCE(
                ytm.owner_name,
                ''
            ) AS owner_name

        FROM mlf.draft_pick AS dp

        LEFT JOIN mlf.team AS t
          ON t.league_key = $2
         AND t.season_year = $3
         AND t.team_key = dp.column_team_key

        LEFT JOIN public.yahoo_team_map AS ytm
          ON ytm.league_key = $2
         AND ytm.season_year = $3
         AND ytm.team_key = dp.column_team_key

        WHERE dp.draft_key = $1
          AND dp.round_number = $4

        ORDER BY dp.slot_number
        `,
        [
          draftKey,
          draft.league_key,
          draft.season_year,
          firstStandardRound,
        ],
      );

    if (result.rows.length !== managerCount) {
      throw new Error(
        `Expected ${managerCount} draft-order rows; received ${result.rows.length}.`,
      );
    }

    const order: DraftOrderRow[] =
      result.rows.map((row) => ({
        pickNumber:
          numberValue(
            row.slot_number,
            "draft slot",
          ),
        teamKey: row.team_key,
        teamName:
          row.team_name?.trim() ||
          row.team_key,
        ownerName:
          row.owner_name?.trim() ?? "",
      }));

    const teamKeys =
      order.map(
        (row) => row.teamKey,
      );

    if (
      new Set(teamKeys).size !==
      managerCount
    ) {
      throw new Error(
        "Draft order contains duplicate teams.",
      );
    }

    const pickNumbers =
      order.map(
        (row) => row.pickNumber,
      );

    const expectedPicks =
      Array.from(
        { length: managerCount },
        (_, index) => index + 1,
      );

    if (
      pickNumbers.join(",") !==
      expectedPicks.join(",")
    ) {
      throw new Error(
        "Draft order does not contain canonical slots 1-16.",
      );
    }

    const standardRounds =
      await client.query<{
        round_number: number;
        team_order: string[];
      }>(
        `
        SELECT
            round_number,
            array_agg(
                column_team_key
                ORDER BY slot_number
            ) AS team_order
        FROM mlf.draft_pick
        WHERE draft_key = $1
          AND round_number >= $2
        GROUP BY round_number
        ORDER BY round_number
        `,
        [
          draftKey,
          firstStandardRound,
        ],
      );

    for (const round of standardRounds.rows) {
      if (
        round.team_order.join(",") !==
        teamKeys.join(",")
      ) {
        throw new Error(
          `Straight draft order mismatch in round ${round.round_number}.`,
        );
      }
    }

    await client.query("ROLLBACK");

    return {
      draftLabel: draft.draft_label,
      status: draft.status,
      managerCount,
      roundsTotal,
      firstStandardRound,
      orderMode: draft.draft_order_mode,
      order,
    };
  }
  catch (error) {
    try {
      await client.query("ROLLBACK");
    }
    catch {
      // Preserve original error.
    }

    throw error;
  }
  finally {
    client.release();
  }
}