import { getPool } from "./db";

export type QoPlayer = {
  playerKey: string;
  playerName: string;
  mlbTeam: string;
  position: string;
};

export type QoSlot = {
  level: number;
  player: QoPlayer | null;
};

export type QoTeam = {
  teamKey: string;
  teamName: string;
  ownerName: string;
  draftSlot: number;
  current: QoSlot[];
  predraft: QoSlot[];
};

export type QoSnapshot = {
  teams: QoTeam[];
  predraftCount: number;
  currentCount: number;
};

type RawQoRow = {
  draft_slot: number | string;
  team_key: string;
  team_name: string | null;
  owner_name: string | null;

  qo_source: "CURRENT" | "PREDRAFT" | null;
  qo_level: number | string | null;
  yahoo_player_key: string | null;

  player_name: string | null;
  mlb_team: string | null;
  primary_position: string | null;
};

const QOS_SQL = `
WITH draft_meta AS (
    SELECT
        draft_key,
        league_key,
        season_year
    FROM mlf.draft
    WHERE draft_key = $1
),
team_order AS (
    SELECT
        dp.slot_number AS draft_slot,
        dp.column_team_key AS team_key,
        COALESCE(
            t.team_name,
            dp.column_team_key
        ) AS team_name
    FROM mlf.draft_pick AS dp

    JOIN draft_meta AS dm
      ON TRUE

    LEFT JOIN mlf.team AS t
      ON t.league_key = dm.league_key
     AND t.season_year = dm.season_year
     AND t.team_key = dp.column_team_key

    WHERE dp.draft_key = $1
      AND dp.round_number = 1
),
qos AS (
    SELECT
        'CURRENT'::text AS qo_source,
        q.team_key,
        q.qo_level,
        q.yahoo_player_key
    FROM mlf.draft_qo_current AS q
    WHERE q.draft_key = $1

    UNION ALL

    SELECT
        'PREDRAFT'::text AS qo_source,
        q.team_key,
        q.qo_level,
        q.yahoo_player_key
    FROM mlf.qualifying_offer AS q

    JOIN draft_meta AS dm
      ON q.league_key = dm.league_key
     AND q.season_year = dm.season_year
),
team_owner AS (
    SELECT
        ytm.team_key,
        MAX(
            NULLIF(
                BTRIM(ytm.owner_name),
                ''
            )
        ) AS owner_name
    FROM public.yahoo_team_map AS ytm

    JOIN draft_meta AS dm
      ON ytm.league_key = dm.league_key
     AND ytm.season_year = dm.season_year

    GROUP BY ytm.team_key
)
SELECT
    team_order.draft_slot,
    team_order.team_key,
    team_order.team_name,
    team_owner.owner_name,

    qos.qo_source,
    qos.qo_level,
    qos.yahoo_player_key,

    COALESCE(
        ap.full_name,
        pu.player_name,
        qos.yahoo_player_key
    ) AS player_name,

    COALESCE(
        ap.editorial_team_abbr,
        ''
    ) AS mlb_team,

    COALESCE(
        ap.primary_position,
        pu.primary_position,
        ''
    ) AS primary_position

FROM team_order

LEFT JOIN team_owner
  ON team_owner.team_key =
     team_order.team_key

LEFT JOIN qos
  ON qos.team_key =
     team_order.team_key

JOIN draft_meta AS dm
  ON TRUE

LEFT JOIN mlf.player_universe AS pu
  ON pu.league_key = dm.league_key
 AND pu.season_year = dm.season_year
 AND pu.yahoo_player_key =
     qos.yahoo_player_key

LEFT JOIN public.v_mlf_available_players_current AS ap
  ON ap.yahoo_player_key =
     qos.yahoo_player_key

ORDER BY
    team_order.draft_slot,
    CASE qos.qo_source
        WHEN 'CURRENT' THEN 1
        WHEN 'PREDRAFT' THEN 2
        ELSE 3
    END,
    qos.qo_level NULLS LAST
`;

function toNumber(
  value: number | string,
): number {
  const n = Number(value);

  if (!Number.isFinite(n)) {
    throw new Error(
      `Invalid numeric QO value: ${String(value)}`,
    );
  }

  return n;
}

function playerFromRow(
  row: RawQoRow,
): QoPlayer | null {
  if (!row.yahoo_player_key) {
    return null;
  }

  const playerName =
    row.player_name?.trim() ?? "";

  if (!playerName) {
    throw new Error(
      `Missing QO player metadata for ${row.yahoo_player_key}.`,
    );
  }

  return {
    playerKey: row.yahoo_player_key,
    playerName,
    mlbTeam:
      row.mlb_team?.trim() ?? "",
    position:
      row.primary_position?.trim() ?? "",
  };
}

function emptySlots(): QoSlot[] {
  return [1, 2, 3, 4, 5].map(
    (level) => ({
      level,
      player: null,
    }),
  );
}

export async function loadQos(
  draftKey: string,
): Promise<QoSnapshot> {
  const pool = getPool();
  const client = await pool.connect();

  try {
    await client.query(
      "BEGIN READ ONLY",
    );

    const meta =
      await client.query<{
        league_key: string;
        season_year: number;
        status: string;
        qo_rounds: number;
      }>(
        `
        SELECT
            league_key,
            season_year,
            status,
            qo_rounds
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

    if (Number(draft.qo_rounds) !== 5) {
      throw new Error(
        `Expected five QO rounds; received ${draft.qo_rounds}.`,
      );
    }

    const leagueKey =
      draft.league_key;

    const seasonYear =
      Number(draft.season_year);

    const gameKey =
      leagueKey.split(".")[0];

    const statsSeason =
      seasonYear - 1;

    await client.query(
      "SELECT set_config('mlf.league_key', $1, true)",
      [leagueKey],
    );

    await client.query(
      "SELECT set_config('mlf.game_key', $1, true)",
      [gameKey],
    );

    await client.query(
      "SELECT set_config('mlf.stats_season', $1, true)",
      [String(statsSeason)],
    );

    const result =
      await client.query<RawQoRow>(
        QOS_SQL,
        [draftKey],
      );

    await client.query("ROLLBACK");

    const teamMap =
      new Map<string, QoTeam>();

    let predraftCount = 0;
    let currentCount = 0;

    const predraftPlayerKeys =
      new Set<string>();

    const currentPlayerKeys =
      new Set<string>();

    for (const row of result.rows) {
      let team =
        teamMap.get(row.team_key);

      if (!team) {
        team = {
          teamKey: row.team_key,
          teamName:
            row.team_name?.trim() ||
            row.team_key,
          ownerName:
            row.owner_name?.trim() ?? "",
          draftSlot:
            toNumber(row.draft_slot),
          current: emptySlots(),
          predraft: emptySlots(),
        };

        teamMap.set(
          row.team_key,
          team,
        );
      }

      if (
        !row.qo_source ||
        row.qo_level === null
      ) {
        continue;
      }

      const level =
        toNumber(row.qo_level);

      if (
        level < 1 ||
        level > 5
      ) {
        throw new Error(
          `Invalid QO level ${level} for ${row.team_key}.`,
        );
      }

      const player =
        playerFromRow(row);

      if (!player) {
        throw new Error(
          `Missing player for ${row.team_key} QO${level}.`,
        );
      }

      const target =
        row.qo_source === "CURRENT"
          ? team.current
          : team.predraft;

      if (target[level - 1].player) {
        throw new Error(
          `Duplicate ${row.qo_source} QO${level} for ${row.team_key}.`,
        );
      }

      target[level - 1] = {
        level,
        player,
      };

      if (row.qo_source === "CURRENT") {
        currentCount += 1;

        if (
          currentPlayerKeys.has(
            player.playerKey,
          )
        ) {
          throw new Error(
            `Duplicate current QO player ${player.playerKey}.`,
          );
        }

        currentPlayerKeys.add(
          player.playerKey,
        );
      }
      else {
        predraftCount += 1;

        if (
          predraftPlayerKeys.has(
            player.playerKey,
          )
        ) {
          throw new Error(
            `Duplicate predraft QO player ${player.playerKey}.`,
          );
        }

        predraftPlayerKeys.add(
          player.playerKey,
        );
      }
    }

    const teams =
      Array.from(
        teamMap.values(),
      ).sort(
        (a, b) =>
          a.draftSlot -
          b.draftSlot,
      );

    if (teams.length !== 16) {
      throw new Error(
        `Expected 16 MLF teams; received ${teams.length}.`,
      );
    }

    if (predraftCount !== 80) {
      throw new Error(
        `Expected 80 predraft QOs; received ${predraftCount}.`,
      );
    }

    if (currentCount !== 24) {
      throw new Error(
        `Expected 24 current QOs for completed 2026 draft; received ${currentCount}.`,
      );
    }

    for (const team of teams) {
      const predraftFilled =
        team.predraft.filter(
          (slot) =>
            slot.player !== null,
        ).length;

      if (predraftFilled !== 5) {
        throw new Error(
          `${team.teamName} has ${predraftFilled} predraft QOs; expected 5.`,
        );
      }
    }

    for (const key of currentPlayerKeys) {
      if (!predraftPlayerKeys.has(key)) {
        throw new Error(
          `Current QO player ${key} was not present in predraft QOs.`,
        );
      }
    }

    return {
      teams,
      predraftCount,
      currentCount,
    };
  }
  catch (error) {
    try {
      await client.query(
        "ROLLBACK",
      );
    }
    catch {
      // Preserve the original error.
    }

    throw error;
  }
  finally {
    client.release();
  }
}