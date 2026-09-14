import { getPool } from "./db";

export type PickKind =
  | "FA"
  | "QO"
  | "POACH";

export type PickTrackerRow = {
  overallNumber: number;
  pickId: string;
  roundNumber: number;
  roundLabel: string;
  slotNumber: number;
  selectingTeamKey: string;
  selectingTeamName: string;
  ownerName: string;
  playerKey: string;
  playerName: string;
  mlbTeam: string;
  position: string;
  pickKind: PickKind;
  selectedAtUtc: string;
};

export type PickTrackerSnapshot = {
  rows: PickTrackerRow[];
  totalPicks: number;
  faCount: number;
  qoCount: number;
  poachCount: number;
};

type RawTrackerRow = {
  overall_number: number | string;
  pick_id: string;
  round_number: number | string;
  slot_number: number | string;
  selecting_team_key: string;
  selecting_team_name: string | null;
  owner_name: string | null;
  yahoo_player_key: string;
  player_name: string | null;
  mlb_team: string | null;
  primary_position: string | null;
  pick_kind: string;
  selected_at_utc: string | Date;
};

function asNumber(
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

function asPickKind(
  value: string,
): PickKind {
  const normalized =
    value.trim().toUpperCase();

  if (
    normalized !== "FA" &&
    normalized !== "QO" &&
    normalized !== "POACH"
  ) {
    throw new Error(
      `Unexpected pick kind ${value}.`,
    );
  }

  return normalized;
}

function roundLabel(
  roundNumber: number,
): string {
  if (
    roundNumber >= 1 &&
    roundNumber <= 5
  ) {
    return `QO${roundNumber}`;
  }

  return String(roundNumber);
}

export async function loadPickTracker(
  draftKey: string,
): Promise<PickTrackerSnapshot> {
  const pool = getPool();
  const client = await pool.connect();

  try {
    await client.query("BEGIN READ ONLY");

    const meta =
      await client.query<{
        league_key: string;
        season_year: number;
        manager_count: number;
        qo_rounds: number;
        status: string;
      }>(
        `
        SELECT
            league_key,
            season_year,
            manager_count,
            qo_rounds,
            status
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

    if (Number(draft.manager_count) !== 16) {
      throw new Error(
        `Expected 16 managers; received ${draft.manager_count}.`,
      );
    }

    if (Number(draft.qo_rounds) !== 5) {
      throw new Error(
        `Expected five QO rounds; received ${draft.qo_rounds}.`,
      );
    }

    await client.query(
      `
      SELECT set_config(
          'mlf.league_key',
          $1,
          true
      )
      `,
      [draft.league_key],
    );

    await client.query(
      `
      SELECT set_config(
          'mlf.stats_season',
          $1,
          true
      )
      `,
      [String(draft.season_year - 1)],
    );

    const result =
      await client.query<RawTrackerRow>(
        `
        SELECT
            row_number() OVER (
                ORDER BY
                    ds.selected_at_utc,
                    dp.round_number,
                    dp.slot_number
            ) AS overall_number,

            ds.pick_id,
            dp.round_number,
            dp.slot_number,

            ds.selecting_team_key,

            COALESCE(
                ytm.team_name,
                t.team_name,
                ds.selecting_team_key
            ) AS selecting_team_name,

            COALESCE(
                ytm.owner_name,
                ''
            ) AS owner_name,

            ds.yahoo_player_key,

            COALESCE(
                ap.full_name,
                pu.player_name,
                ds.yahoo_player_key
            ) AS player_name,

            COALESCE(
                ap.editorial_team_abbr,
                ''
            ) AS mlb_team,

            COALESCE(
                ap.primary_position,
                pu.primary_position,
                ''
            ) AS primary_position,

            ds.pick_kind,
            ds.selected_at_utc

        FROM mlf.draft_selection AS ds

        JOIN mlf.draft_pick AS dp
          ON dp.draft_key = ds.draft_key
         AND dp.pick_id = ds.pick_id

        LEFT JOIN mlf.team AS t
          ON t.league_key = $2
         AND t.season_year = $3
         AND t.team_key = ds.selecting_team_key

        LEFT JOIN public.yahoo_team_map AS ytm
          ON ytm.league_key = $2
         AND ytm.season_year = $3
         AND ytm.team_key = ds.selecting_team_key

        LEFT JOIN mlf.player_universe AS pu
          ON pu.league_key = $2
         AND pu.season_year = $3
         AND pu.yahoo_player_key = ds.yahoo_player_key

        LEFT JOIN public.v_mlf_available_players_current AS ap
          ON ap.yahoo_player_key = ds.yahoo_player_key

        WHERE ds.draft_key = $1

        ORDER BY
            ds.selected_at_utc,
            dp.round_number,
            dp.slot_number
        `,
        [
          draftKey,
          draft.league_key,
          draft.season_year,
        ],
      );

    if (result.rows.length !== 256) {
      throw new Error(
        `Expected 256 real selections; received ${result.rows.length}.`,
      );
    }

    const rows: PickTrackerRow[] =
      result.rows.map((row) => {
        const roundNumber =
          asNumber(
            row.round_number,
            "round number",
          );

        return {
          overallNumber:
            asNumber(
              row.overall_number,
              "overall number",
            ),
          pickId: row.pick_id,
          roundNumber,
          roundLabel:
            roundLabel(roundNumber),
          slotNumber:
            asNumber(
              row.slot_number,
              "slot number",
            ),
          selectingTeamKey:
            row.selecting_team_key,
          selectingTeamName:
            row.selecting_team_name?.trim() ||
            row.selecting_team_key,
          ownerName:
            row.owner_name?.trim() ?? "",
          playerKey:
            row.yahoo_player_key,
          playerName:
            row.player_name?.trim() ||
            row.yahoo_player_key,
          mlbTeam:
            row.mlb_team?.trim() ?? "",
          position:
            row.primary_position?.trim() ?? "",
          pickKind:
            asPickKind(row.pick_kind),
          selectedAtUtc:
            row.selected_at_utc instanceof Date
              ? row.selected_at_utc.toISOString()
              : String(row.selected_at_utc),
        };
      });

    const playerKeys =
      rows.map(
        (row) => row.playerKey,
      );

    if (
      new Set(playerKeys).size !==
      rows.length
    ) {
      throw new Error(
        "Pick Tracker contains duplicate selected players.",
      );
    }

    const overallNumbers =
      rows.map(
        (row) => row.overallNumber,
      );

    const expectedOverall =
      Array.from(
        { length: rows.length },
        (_, index) => index + 1,
      );

    if (
      overallNumbers.join(",") !==
      expectedOverall.join(",")
    ) {
      throw new Error(
        "Pick Tracker chronological numbering is not contiguous.",
      );
    }

    const faCount =
      rows.filter(
        (row) => row.pickKind === "FA",
      ).length;

    const qoCount =
      rows.filter(
        (row) => row.pickKind === "QO",
      ).length;

    const poachCount =
      rows.filter(
        (row) => row.pickKind === "POACH",
      ).length;

    if (
      faCount !== 216 ||
      qoCount !== 24 ||
      poachCount !== 16
    ) {
      throw new Error(
        `Unexpected pick-kind distribution FA=${faCount}, QO=${qoCount}, POACH=${poachCount}.`,
      );
    }

    await client.query("ROLLBACK");

    return {
      rows,
      totalPicks: rows.length,
      faCount,
      qoCount,
      poachCount,
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