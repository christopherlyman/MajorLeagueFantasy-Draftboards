import { getPool } from "./db";

export type AvailablePlayerRow = {
  playerKey: string;
  name: string;
  mlbTeam: string;
  positions: string[];

  status: string;
  contractYears: number | null;
  qoLevel: number | null;
  currentRound: number;
  isPoachable: boolean;
  isDrafted: boolean;
  isPt: boolean;
  isContract: boolean;

  teamName: string;
  draftPick: string;

  currentRank: number | null;
  percentRostered: number | null;

  hAb: string;
  r: number | null;
  hr: number | null;
  rbi: number | null;
  sb: number | null;
  bb: number | null;
  kHit: number | null;
  avg: number | null;

  ip: number | null;
  w: number | null;
  kPit: number | null;
  tb: number | null;
  era: number | null;
  whip: number | null;
  qs: number | null;
  svH: number | null;
};

type DraftContext = {
  league_key: string;
  season_year: number;
};

type RawAvailablePlayer = {
  player_key: string;
  full_name: string | null;
  mlb_team: string | null;
  eligible_positions: unknown;

  qo_level: unknown;
  current_round: unknown;
  contract_years: unknown;

  keeper_kind: string | null;
  keeper_team_name: string | null;

  real_pick_id: string | null;
  real_pick_round: unknown;
  real_pick_slot: unknown;
  real_pick_type: string | null;
  real_pick_team_name: string | null;

  current_rank: unknown;
  percent_rostered: unknown;

  h_ab: unknown;
  r: unknown;
  hr: unknown;
  rbi: unknown;
  sb: unknown;
  bb: unknown;
  k_hit: unknown;
  avg: unknown;

  ip: unknown;
  w: unknown;
  k_pit: unknown;
  tb: unknown;
  era: unknown;
  whip: unknown;
  qs: unknown;
  sv_h: unknown;
};

const AVAILABLE_PLAYERS_SQL = `
SELECT
    ap.yahoo_player_key::text
        AS player_key,

    ap.full_name,
    COALESCE(ap.editorial_team_abbr, '')
        AS mlb_team,
    ap.eligible_positions,

    COALESCE(qo.qo_group, qop.qo_level)
        AS qo_level,
    COALESCE(rs.current_round, 0)
        AS current_round,

    contract_effective.years_remaining
        AS contract_years,

    keeper.keeper_kind,
    keeper.keeper_team_name,

    real_pick.pick_id::text
        AS real_pick_id,
    real_pick.round_number
        AS real_pick_round,
    real_pick.slot_number
        AS real_pick_slot,
    real_pick.pick_type
        AS real_pick_type,
    real_pick.team_name
        AS real_pick_team_name,

    ap.rank_value
        AS current_rank,
    ap.percent_owned
        AS percent_rostered,

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
    ap.sv_h

FROM public.v_available_players_current AS ap

JOIN mlf.draft AS d
  ON d.draft_key = $1

LEFT JOIN public.qo_overrides AS qo
  ON qo.draft_key = d.draft_key
 AND qo.yahoo_player_key = ap.yahoo_player_key

LEFT JOIN public.qualifying_offer AS qop
  ON qop.league_key = d.league_key
 AND qop.season_year = d.season_year
 AND qop.yahoo_player_key = ap.yahoo_player_key

LEFT JOIN public.qo_round_state AS rs
  ON rs.league_key = d.league_key
 AND rs.season_year = d.season_year

LEFT JOIN mlf.v_contract_effective AS contract_effective
  ON contract_effective.league_key = d.league_key
 AND contract_effective.season_year = d.season_year
 AND contract_effective.yahoo_player_key =
     ap.yahoo_player_key

LEFT JOIN LATERAL (
    SELECT
        dka.keeper_kind,
        tm.team_name AS keeper_team_name
    FROM mlf.draft_keeper_assignment AS dka
    JOIN mlf.draft_pick AS dp
      ON dp.draft_key = dka.draft_key
     AND dp.pick_id = dka.pick_id
    LEFT JOIN mlf.team AS tm
      ON tm.league_key = d.league_key
     AND tm.season_year = d.season_year
     AND tm.team_key = dp.current_owner_team_key
    WHERE dka.draft_key = d.draft_key
      AND dka.yahoo_player_key =
          ap.yahoo_player_key
    ORDER BY
        dp.round_number,
        dp.slot_number
    LIMIT 1
) AS keeper
  ON TRUE

LEFT JOIN LATERAL (
    SELECT
        dp.pick_id,
        dp.round_number,
        dp.slot_number,
        dp.pick_type,
        tm.team_name
    FROM mlf.draft_selection AS ds
    JOIN mlf.draft_pick AS dp
      ON dp.draft_key = ds.draft_key
     AND dp.pick_id = ds.pick_id
    LEFT JOIN mlf.team AS tm
      ON tm.league_key = d.league_key
     AND tm.season_year = d.season_year
     AND tm.team_key = dp.current_owner_team_key
    WHERE ds.draft_key = d.draft_key
      AND ds.yahoo_player_key =
          ap.yahoo_player_key
    ORDER BY
        dp.round_number,
        dp.slot_number
    LIMIT 1
) AS real_pick
  ON TRUE

ORDER BY
    ap.rank_value NULLS LAST,
    ap.full_name;
`;

function asNumber(
  value: unknown,
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

function parsePositions(
  raw: unknown,
): string[] {
  let values: string[];

  if (Array.isArray(raw)) {
    values = raw.map(String);
  } else {
    values = String(raw ?? "")
      .replace(/[{}"]/g, "")
      .split(/[,/]/);
  }

  const normalized = Array.from(
    new Set(
      values
        .map((value) =>
          value.trim().toUpperCase(),
        )
        .filter(Boolean),
    ),
  );

  const hitterPositions = new Set([
    "C",
    "1B",
    "2B",
    "3B",
    "SS",
    "OF",
  ]);

  const hasHitterPosition =
    normalized.some((position) =>
      hitterPositions.has(position),
    );

  const hasPitcherPosition =
    normalized.some((position) =>
      ["SP", "RP", "P"].includes(position),
    );

  const isPitcher =
    hasPitcherPosition &&
    !hasHitterPosition;

  if (isPitcher) {
    if (
      normalized.includes("P") &&
      normalized.some((position) =>
        ["SP", "RP"].includes(position),
      )
    ) {
      return normalized.filter(
        (position) => position !== "P",
      );
    }

    return normalized;
  }

  if (
    normalized.includes("UTIL") &&
    normalized.some(
      (position) => position !== "UTIL",
    )
  ) {
    return normalized.filter(
      (position) => position !== "UTIL",
    );
  }

  return normalized;
}

function contractLabel(
  years: number,
): string {
  return years === 1
    ? "1-year"
    : `${years}-years`;
}

function draftPickLabel(
  round: number | null,
  slot: number | null,
  pickType: string | null,
): string {
  if (
    round === null ||
    slot === null
  ) {
    return "";
  }

  const normalizedType =
    String(pickType ?? "").toUpperCase();

  if (
    normalizedType === "QO" ||
    normalizedType.endsWith("QO")
  ) {
    return `QO${round}.${slot}`;
  }

  return `R${String(round).padStart(2, "0")}.${slot}`;
}

function mapRow(
  raw: RawAvailablePlayer,
): AvailablePlayerRow {
  const contractYears =
    asNumber(raw.contract_years);

  const qoLevel =
    asNumber(raw.qo_level);

  const currentRound =
    asNumber(raw.current_round) ?? 0;

  const keeperKind =
    String(raw.keeper_kind ?? "")
      .trim()
      .toUpperCase();

  const isPt =
    keeperKind === "PT";

  const isContract =
    !isPt &&
    contractYears !== null &&
    contractYears > 0;

  const isDrafted =
    Boolean(raw.real_pick_id);

  let status = "";

  // Streamlit precedence:
  // PT -> Contract -> QO
  if (isPt) {
    status = "PT";
  } else if (isContract) {
    status = contractLabel(
      contractYears as number,
    );
  } else if (qoLevel !== null) {
    status = `QO${qoLevel}`;
  }

  const teamName =
    isDrafted
      ? String(
          raw.real_pick_team_name ?? "",
        )
      : (
          isPt ||
          isContract
        )
        ? String(
            raw.keeper_team_name ?? "",
          )
        : "";

  const realPickRound =
    asNumber(raw.real_pick_round);

  const realPickSlot =
    asNumber(raw.real_pick_slot);

  return {
    playerKey: raw.player_key,
    name: String(raw.full_name ?? ""),
    mlbTeam: String(raw.mlb_team ?? ""),
    positions: parsePositions(
      raw.eligible_positions,
    ),

    status,
    contractYears,
    qoLevel,
    currentRound,
    isPoachable:
      qoLevel !== null &&
      currentRound >= 1 &&
      currentRound <= 5 &&
      qoLevel > currentRound,
    isDrafted,
    isPt,
    isContract,

    // Undrafted QOs intentionally receive no
    // ownership/team label here.
    teamName,
    draftPick: isDrafted
      ? draftPickLabel(
          realPickRound,
          realPickSlot,
          raw.real_pick_type,
        )
      : "",

    currentRank:
      asNumber(raw.current_rank),
    percentRostered:
      asNumber(raw.percent_rostered),

    hAb: String(raw.h_ab ?? ""),
    r: asNumber(raw.r),
    hr: asNumber(raw.hr),
    rbi: asNumber(raw.rbi),
    sb: asNumber(raw.sb),
    bb: asNumber(raw.bb),
    kHit: asNumber(raw.k_hit),
    avg: asNumber(raw.avg),

    ip: asNumber(raw.ip),
    w: asNumber(raw.w),
    kPit: asNumber(raw.k_pit),
    tb: asNumber(raw.tb),
    era: asNumber(raw.era),
    whip: asNumber(raw.whip),
    qs: asNumber(raw.qs),
    svH: asNumber(raw.sv_h),
  };
}

export async function loadAvailablePlayers(
  draftKey: string,
): Promise<AvailablePlayerRow[]> {
  const client =
    await getPool().connect();

  try {
    await client.query("BEGIN READ ONLY");

    const contextResult =
      await client.query<DraftContext>(
        `
        SELECT
            league_key,
            season_year
        FROM mlf.draft
        WHERE draft_key = $1
        `,
        [draftKey],
      );

    const context =
      contextResult.rows[0];

    if (!context) {
      throw new Error(
        `Unknown MLF draft_key: ${draftKey}`,
      );
    }

    const leagueKey =
      String(context.league_key);

    const seasonYear =
      Number(context.season_year);

    await client.query(
      "SELECT set_config('mlf.league_key', $1, true)",
      [leagueKey],
    );

    await client.query(
      "SELECT set_config('mlf.game_key', $1, true)",
      [leagueKey.split(".")[0]],
    );

    await client.query(
      "SELECT set_config('mlf.stats_season', $1, true)",
      [
        String(
          Math.max(
            seasonYear - 1,
            0,
          ),
        ),
      ],
    );

    const result =
      await client.query<RawAvailablePlayer>(
        AVAILABLE_PLAYERS_SQL,
        [draftKey],
      );

    await client.query("ROLLBACK");

    return result.rows.map(mapRow);
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // Preserve original error.
    }

    throw error;
  } finally {
    client.release();
  }
}