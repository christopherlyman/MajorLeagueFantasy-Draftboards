import { getPool } from "./db";

export type TeamPlayer = {
  teamKey: string;
  teamName: string;
  draftSlot: number;

  playerKey: string;
  playerName: string;
  mlbTeam: string;
  positions: string[];

  rank: number | null;
  percentRostered: number | null;

  control: string;

  hAb: string;
  r: number | null;
  hr: number | null;
  rbi: number | null;
  sb: number | null;
  bb: number | null;
  kHit: number | null;
  avg: string;

  ip: number | null;
  w: number | null;
  kPit: number | null;
  tb: number | null;
  era: number | null;
  whip: number | null;
  qs: number | null;
  svH: number | null;

  roundNumber: number;
  slotNumber: number;
  rosterSource: "DRAFT" | "KEEPER";
};

export type LineupRow = {
  slot: string;
  player: TeamPlayer | null;
};

export type TeamRoster = {
  teamKey: string;
  teamName: string;
  draftSlot: number;
  players: TeamPlayer[];
};

export type TeamLineup = {
  hitters: LineupRow[];
  pitchers: LineupRow[];
  overflow: number;
};

type RawRosterRow = {
  draft_slot: number | string;
  team_key: string;
  team_name: string;

  yahoo_player_key: string | null;
  player_name: string | null;
  mlb_team: string | null;
  eligible_positions: string | null;

  rank_value: number | string | null;
  percent_owned: number | string | null;

  control_display: string | null;

  h_ab: string | null;
  r: number | string | null;
  hr: number | string | null;
  rbi: number | string | null;
  sb: number | string | null;
  bb: number | string | null;
  k_hit: number | string | null;
  avg: string | number | null;

  ip: number | string | null;
  w: number | string | null;
  k_pit: number | string | null;
  tb: number | string | null;
  era: number | string | null;
  whip: number | string | null;
  qs: number | string | null;
  sv_h: number | string | null;

  round_number: number | string | null;
  slot_number: number | string | null;
  roster_source: "DRAFT" | "KEEPER" | null;
};

const HITTER_POSITIONS = new Set([
  "C",
  "1B",
  "2B",
  "3B",
  "SS",
  "OF",
  "UTIL",
]);

const PITCHER_POSITIONS = new Set([
  "SP",
  "RP",
  "P",
]);

const HITTER_SLOTS = [
  "C",
  "1B",
  "2B",
  "3B",
  "SS",
  "OF",
  "OF",
  "OF",
  "UTIL",
] as const;

const PITCHER_SLOTS = [
  "SP",
  "SP",
  "SP",
  "RP",
  "RP",
  "RP",
  "P",
  "P",
  "P",
] as const;

const BENCH_CAP = 7;

const TEAMS_SQL = `
WITH draft_meta AS (
    SELECT
        d.league_key,
        d.season_year
    FROM mlf.draft AS d
    WHERE d.draft_key = $1
),
team_order AS (
    SELECT
        dp.column_team_key AS team_key,
        COALESCE(t.team_name, dp.column_team_key)
            AS team_name,
        dp.slot_number AS draft_slot
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
roster AS (
    SELECT
        ds.selecting_team_key AS team_key,
        ds.yahoo_player_key,
        dp.round_number,
        dp.slot_number,
        'DRAFT'::text AS roster_source
    FROM mlf.draft_selection AS ds
    JOIN mlf.draft_pick AS dp
      ON dp.draft_key = ds.draft_key
     AND dp.pick_id = ds.pick_id
    WHERE ds.draft_key = $1

    UNION ALL

    SELECT
        dka.team_key,
        dka.yahoo_player_key,
        dp.round_number,
        dp.slot_number,
        'KEEPER'::text AS roster_source
    FROM mlf.draft_keeper_assignment AS dka
    JOIN mlf.draft_pick AS dp
      ON dp.draft_key = dka.draft_key
     AND dp.pick_id = dka.pick_id
    WHERE dka.draft_key = $1
)
SELECT
    team_order.draft_slot,
    team_order.team_key,
    team_order.team_name,

    roster.yahoo_player_key,

    COALESCE(
        ap.full_name,
        pu.player_name,
        roster.yahoo_player_key
    ) AS player_name,

    COALESCE(
        NULLIF(ap.editorial_team_abbr, ''),
        ''
    ) AS mlb_team,

    COALESCE(
        NULLIF(ap.eligible_positions::text, ''),
        NULLIF(pu.primary_position, ''),
        ''
    ) AS eligible_positions,

    COALESCE(
        ap.rank_value,
        pu.rank_value
    ) AS rank_value,

    ap.percent_owned,

    CASE
        WHEN pt.yahoo_player_key IS NOT NULL
            THEN 'PT'
        WHEN active_contract.years_remaining IS NOT NULL
            THEN active_contract.years_remaining::text
        ELSE ''
    END AS control_display,

    ap.h_ab,
    ap.r,
    ap.hr,
    ap.rbi,
    ap.sb,
    ap.bb,
    ap.k_hit,
    ap.avg,

    ap.ip,
    ap.w,
    ap.k_pit,
    ap.tb,
    ap.era,
    ap.whip,
    ap.qs,
    ap.sv_h,

    roster.round_number,
    roster.slot_number,
    roster.roster_source

FROM team_order

LEFT JOIN roster
  ON roster.team_key = team_order.team_key

JOIN draft_meta AS dm
  ON TRUE

LEFT JOIN mlf.player_universe AS pu
  ON pu.league_key = dm.league_key
 AND pu.season_year = dm.season_year
 AND pu.yahoo_player_key = roster.yahoo_player_key

LEFT JOIN public.v_mlf_available_players_current AS ap
  ON ap.yahoo_player_key = roster.yahoo_player_key

LEFT JOIN mlf.prospect_tag AS pt
  ON pt.league_key = dm.league_key
 AND pt.season_year = dm.season_year
 AND pt.team_key = team_order.team_key
 AND pt.yahoo_player_key = roster.yahoo_player_key

LEFT JOIN mlf.v_active_contract AS active_contract
  ON active_contract.league_key = dm.league_key
 AND active_contract.season_year = dm.season_year
 AND active_contract.team_key = team_order.team_key
 AND active_contract.yahoo_player_key = roster.yahoo_player_key

ORDER BY
    team_order.draft_slot,
    roster.round_number NULLS LAST,
    roster.slot_number NULLS LAST
`;

function toNumber(
  value: number | string | null,
): number | null {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return null;
  }

  const parsed = Number(value);

  return Number.isFinite(parsed)
    ? parsed
    : null;
}

function toText(
  value: unknown,
): string {
  if (
    value === null ||
    value === undefined
  ) {
    return "";
  }

  return String(value);
}

function parsePositions(
  value: string | null,
): string[] {
  const text = toText(value)
    .replace(/[\[\]{}"]/g, "")
    .trim();

  if (!text) {
    return [];
  }

  return Array.from(
    new Set(
      text
        .split(/[\/,]/)
        .map((item) =>
          item.trim().toUpperCase(),
        )
        .filter(Boolean),
    ),
  );
}

export function isPitcher(
  player: TeamPlayer,
): boolean {
  const positions =
    new Set(player.positions);

  const hasPitcher =
    [...PITCHER_POSITIONS].some(
      (position) =>
        positions.has(position),
    );

  const hasHitter =
    [...HITTER_POSITIONS]
      .filter(
        (position) =>
          position !== "UTIL",
      )
      .some(
        (position) =>
          positions.has(position),
      );

  return hasPitcher && !hasHitter;
}

function isGenericPitcher(
  player: TeamPlayer,
): boolean {
  const positions =
    new Set(player.positions);

  return (
    isPitcher(player) &&
    positions.has("P") &&
    !positions.has("SP") &&
    !positions.has("RP")
  );
}

function eligibleForSlot(
  player: TeamPlayer,
  slot: string,
  hitter: boolean,
): boolean {
  const positions =
    new Set(player.positions);

  if (hitter) {
    if (slot === "UTIL") {
      return !isPitcher(player);
    }

    return positions.has(slot);
  }

  if (slot === "P") {
    return isPitcher(player);
  }

  return positions.has(slot);
}

function comparePlayers(
  a: TeamPlayer,
  b: TeamPlayer,
): number {
  const ar =
    a.rank ?? Number.MAX_SAFE_INTEGER;

  const br =
    b.rank ?? Number.MAX_SAFE_INTEGER;

  if (ar !== br) {
    return ar - br;
  }

  return a.playerName.localeCompare(
    b.playerName,
  );
}

function takeBest(
  pool: TeamPlayer[],
  slot: string,
  hitter: boolean,
): TeamPlayer | null {
  let index = -1;

  if (
    !hitter &&
    (slot === "SP" || slot === "RP")
  ) {
    index =
      pool.findIndex(
        (player) =>
          player.positions.includes(slot),
      );

    if (index < 0) {
      index =
        pool.findIndex(
          isGenericPitcher,
        );
    }
  } else {
    index =
      pool.findIndex(
        (player) =>
          eligibleForSlot(
            player,
            slot,
            hitter,
          ),
      );
  }

  if (index < 0) {
    return null;
  }

  return pool.splice(index, 1)[0];
}

export function buildTeamLineup(
  players: TeamPlayer[],
): TeamLineup {
  const hitters =
    players
      .filter(
        (player) =>
          !isPitcher(player),
      )
      .sort(comparePlayers);

  const pitchers =
    players
      .filter(isPitcher)
      .sort(comparePlayers);

  const hitterPool = [...hitters];
  const pitcherPool = [...pitchers];

  const hitterRows: LineupRow[] =
    HITTER_SLOTS.map(
      (slot) => ({
        slot,
        player:
          takeBest(
            hitterPool,
            slot,
            true,
          ),
      }),
    );

  const pitcherRows: LineupRow[] =
    PITCHER_SLOTS.map(
      (slot) => ({
        slot,
        player:
          takeBest(
            pitcherPool,
            slot,
            false,
          ),
      }),
    );

  let benchUsed = 0;

  while (
    benchUsed < BENCH_CAP &&
    hitterPool.length > 0
  ) {
    hitterRows.push({
      slot: "BN",
      player:
        hitterPool.shift() ?? null,
    });

    benchUsed += 1;
  }

  while (
    benchUsed < BENCH_CAP &&
    pitcherPool.length > 0
  ) {
    pitcherRows.push({
      slot: "BN",
      player:
        pitcherPool.shift() ?? null,
    });

    benchUsed += 1;
  }

  while (benchUsed < BENCH_CAP) {
    hitterRows.push({
      slot: "BN",
      player: null,
    });

    benchUsed += 1;
  }

  const overflow =
    hitterPool.length +
    pitcherPool.length;

  while (hitterPool.length > 0) {
    hitterRows.push({
      slot: "BN+",
      player:
        hitterPool.shift() ?? null,
    });
  }

  while (pitcherPool.length > 0) {
    pitcherRows.push({
      slot: "BN+",
      player:
        pitcherPool.shift() ?? null,
    });
  }

  const assignedPlayers = [
    ...hitterRows,
    ...pitcherRows,
  ].filter(
    (row) =>
      row.player !== null,
  ).length;

  if (assignedPlayers !== players.length) {
    throw new Error(
      `Lineup assignment accounted for ${assignedPlayers} of ${players.length} roster players.`,
    );
  }

  return {
    hitters: hitterRows,
    pitchers: pitcherRows,
    overflow,
  };
}

export async function loadTeams(
  draftKey: string,
): Promise<TeamRoster[]> {
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
      }>(
        `
        SELECT
            league_key,
            season_year
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

    const {
      league_key: leagueKey,
      season_year: seasonYear,
    } = meta.rows[0];

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
      await client.query<RawRosterRow>(
        TEAMS_SQL,
        [draftKey],
      );

    await client.query("ROLLBACK");

    const teamMap =
      new Map<string, TeamRoster>();

    for (const row of result.rows) {
      let team =
        teamMap.get(row.team_key);

      if (!team) {
        team = {
          teamKey: row.team_key,
          teamName: row.team_name,
          draftSlot:
            Number(row.draft_slot),
          players: [],
        };

        teamMap.set(
          row.team_key,
          team,
        );
      }

      if (!row.yahoo_player_key) {
        continue;
      }

      team.players.push({
        teamKey: row.team_key,
        teamName: row.team_name,
        draftSlot:
          Number(row.draft_slot),

        playerKey:
          row.yahoo_player_key,

        playerName:
          row.player_name ??
          row.yahoo_player_key,

        mlbTeam:
          toText(row.mlb_team),

        positions:
          parsePositions(
            row.eligible_positions,
          ),

        rank:
          toNumber(row.rank_value),

        percentRostered:
          toNumber(
            row.percent_owned,
          ),

        control:
          toText(
            row.control_display,
          ),

        hAb: toText(row.h_ab),
        r: toNumber(row.r),
        hr: toNumber(row.hr),
        rbi: toNumber(row.rbi),
        sb: toNumber(row.sb),
        bb: toNumber(row.bb),
        kHit: toNumber(row.k_hit),
        avg: toText(row.avg),

        ip: toNumber(row.ip),
        w: toNumber(row.w),
        kPit: toNumber(row.k_pit),
        tb: toNumber(row.tb),
        era: toNumber(row.era),
        whip: toNumber(row.whip),
        qs: toNumber(row.qs),
        svH: toNumber(row.sv_h),

        roundNumber:
          Number(
            row.round_number ?? 0,
          ),

        slotNumber:
          Number(
            row.slot_number ?? 0,
          ),

        rosterSource:
          row.roster_source ??
          "DRAFT",
      });
    }

    const teams =
      [...teamMap.values()]
        .sort(
          (a, b) =>
            a.draftSlot -
            b.draftSlot,
        );

    if (teams.length !== 16) {
      throw new Error(
        `Expected 16 MLF teams; received ${teams.length}.`,
      );
    }

    const playerKeys =
      teams.flatMap(
        (team) =>
          team.players.map(
            (player) =>
              player.playerKey,
          ),
      );

    if (
      new Set(playerKeys).size !==
      playerKeys.length
    ) {
      throw new Error(
        "Teams roster projection contains duplicate players.",
      );
    }

    for (const team of teams) {
      if (team.players.length > 25) {
        throw new Error(
          `${team.teamName} has ${team.players.length} projected roster players; maximum is 25.`,
        );
      }
    }

    return teams;
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // Preserve the original failure.
    }

    throw error;
  } finally {
    client.release();
  }
}