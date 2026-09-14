import { getPool } from "./db";

export type TeamDraftStat = {
  teamKey: string;
  teamName: string;
  ownerName: string;
  realPicks: number;
  faPicks: number;
  qoPicks: number;
  poachPicks: number;
  rankedPicks: number;
  averageRank: number | null;
  averageWallClockSeconds: number | null;
  cumulativeWallClockSeconds: number;
};

export type RoundDraftStat = {
  roundNumber: number;
  realPicks: number;
};

export type PositionDraftStat = {
  position: string;
  realPicks: number;
};

export type DailyPaceStat = {
  date: string;
  dailyRealPicks: number;
  actualCumulative: number;
  requiredCumulative: number | null;
  projectedCumulative: number | null;
};

export type DraftStatisticsSnapshot = {
  leagueKey: string;
  seasonYear: number;
  status: string;
  timeZone: string;

  teamCount: number;
  roundsTotal: number;
  totalSlots: number;
  keeperAssignments: number;
  draftableSlots: number;
  filledSlots: number;

  realPicks: number;
  realPicksRemaining: number;

  faCount: number;
  qoCount: number;
  poachCount: number;

  rankedRealPicks: number;
  averageRank: number | null;
  bestRank: number | null;
  worstRank: number | null;

  firstPickDate: string | null;
  lastPickDate: string | null;
  openingDayDate: string | null;

  averagePicksPerDay: number | null;
  averageWallClockSeconds: number | null;
  projectedCompletionDate: string | null;
  requiredPicksPerDay: number | null;

  legacyImportedPicks: number;
  applicationRecordedPicks: number;

  teams: TeamDraftStat[];
  rounds: RoundDraftStat[];
  positions: PositionDraftStat[];
  dailyPace: DailyPaceStat[];
};

type Numeric =
  | number
  | string;

type NullableNumeric =
  | Numeric
  | null;

type RawTeamRow = {
  team_key: string;
  team_name: string | null;
  owner_name: string | null;
  real_picks: Numeric;
  fa_picks: Numeric;
  qo_picks: Numeric;
  poach_picks: Numeric;
  ranked_picks: Numeric;
  average_rank: NullableNumeric;
  cumulative_seconds: NullableNumeric;
};

type RawRoundRow = {
  round_number: Numeric;
  real_picks: Numeric;
};

type RawPositionRow = {
  position: string;
  real_picks: Numeric;
};

type RawDailyRow = {
  pick_date: string;
  daily_real_picks: Numeric;
};

function numberValue(
  value: Numeric,
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

function nullableNumber(
  value: NullableNumeric,
  label: string,
): number | null {
  if (value === null) {
    return null;
  }

  return numberValue(value, label);
}

function parseDateOnly(
  value: string,
): Date {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(
      `Invalid YYYY-MM-DD date: ${value}`,
    );
  }

  const [year, month, day] =
    value.split("-").map(Number);

  const parsed =
    new Date(
      Date.UTC(
        year,
        month - 1,
        day,
      ),
    );

  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    throw new Error(
      `Invalid calendar date: ${value}`,
    );
  }

  return parsed;
}

function dateOnly(
  value: Date,
): string {
  return value
    .toISOString()
    .slice(0, 10);
}

function addDays(
  value: string,
  days: number,
): string {
  const parsed = parseDateOnly(value);

  parsed.setUTCDate(
    parsed.getUTCDate() + days,
  );

  return dateOnly(parsed);
}

function daysInclusive(
  start: string,
  end: string,
): number {
  const startDate =
    parseDateOnly(start);

  const endDate =
    parseDateOnly(end);

  return (
    Math.floor(
      (
        endDate.getTime() -
        startDate.getTime()
      ) /
      86_400_000,
    ) + 1
  );
}

function todayInTimeZone(
  timeZone: string,
): string {
  const parts =
    new Intl.DateTimeFormat(
      "en-US",
      {
        timeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      },
    ).formatToParts(new Date());

  const values =
    Object.fromEntries(
      parts
        .filter((part) =>
          part.type !== "literal",
        )
        .map((part) => [
          part.type,
          part.value,
        ]),
    );

  return (
    `${values.year}-` +
    `${values.month}-` +
    `${values.day}`
  );
}

function openingDayFromEnvironment():
  string | null {
  const raw =
    process.env
      .DRAFTBOARD_OPENING_DAY_DATE
      ?.trim();

  if (!raw) {
    return null;
  }

  parseDateOnly(raw);

  return raw;
}

export async function loadDraftStatistics(
  draftKey: string,
): Promise<DraftStatisticsSnapshot> {
  const pool = getPool();
  const client = await pool.connect();

  const timeZone =
    process.env.DRAFTBOARD_TIME_ZONE
      ?.trim() ||
    "America/New_York";

  const openingDayDate =
    openingDayFromEnvironment();

  try {
    await client.query("BEGIN READ ONLY");

    const meta =
      await client.query<{
        league_key: string;
        season_year: number;
        status: string;
        manager_count: Numeric;
        rounds_total: Numeric;
      }>(
        `
        SELECT
            league_key,
            season_year,
            status,
            manager_count,
            rounds_total
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

    const teamCount =
      numberValue(
        draft.manager_count,
        "manager count",
      );

    const roundsTotal =
      numberValue(
        draft.rounds_total,
        "round count",
      );

    if (
      teamCount <= 0 ||
      roundsTotal <= 0
    ) {
      throw new Error(
        `Invalid draft dimensions teams=${teamCount}, rounds=${roundsTotal}.`,
      );
    }

    const totals =
      await client.query<{
        total_slots: Numeric;
        keeper_assignments: Numeric;
        real_picks: Numeric;
        fa_count: Numeric;
        qo_count: Numeric;
        poach_count: Numeric;
        legacy_imported_picks: Numeric;
        first_pick_date: string | null;
        last_pick_date: string | null;
      }>(
        `
        SELECT
            (
                SELECT count(*)
                FROM mlf.draft_pick
                WHERE draft_key = $1
            ) AS total_slots,

            (
                SELECT count(*)
                FROM mlf.draft_keeper_assignment
                WHERE draft_key = $1
            ) AS keeper_assignments,

            count(*) AS real_picks,

            count(*) FILTER (
                WHERE ds.pick_kind = 'FA'
            ) AS fa_count,

            count(*) FILTER (
                WHERE ds.pick_kind = 'QO'
            ) AS qo_count,

            count(*) FILTER (
                WHERE ds.pick_kind = 'POACH'
            ) AS poach_count,

            count(*) FILTER (
                WHERE ds.selected_by =
                      'legacy_pick_log'
            ) AS legacy_imported_picks,

            to_char(
                (
                    min(ds.selected_at_utc)
                    AT TIME ZONE $2
                )::date,
                'YYYY-MM-DD'
            ) AS first_pick_date,

            to_char(
                (
                    max(ds.selected_at_utc)
                    AT TIME ZONE $2
                )::date,
                'YYYY-MM-DD'
            ) AS last_pick_date

        FROM mlf.draft_selection AS ds
        WHERE ds.draft_key = $1
        `,
        [
          draftKey,
          timeZone,
        ],
      );

    const totalRow = totals.rows[0];

    const totalSlots =
      numberValue(
        totalRow.total_slots,
        "total slots",
      );

    const keeperAssignments =
      numberValue(
        totalRow.keeper_assignments,
        "keeper assignments",
      );

    const realPicks =
      numberValue(
        totalRow.real_picks,
        "real picks",
      );

    const faCount =
      numberValue(
        totalRow.fa_count,
        "FA count",
      );

    const qoCount =
      numberValue(
        totalRow.qo_count,
        "QO count",
      );

    const poachCount =
      numberValue(
        totalRow.poach_count,
        "poach count",
      );

    const legacyImportedPicks =
      numberValue(
        totalRow.legacy_imported_picks,
        "legacy imported picks",
      );

    const expectedSlots =
      teamCount * roundsTotal;

    if (totalSlots !== expectedSlots) {
      throw new Error(
        `Draft slot count ${totalSlots} does not match teams × rounds ${expectedSlots}.`,
      );
    }

    const filledSlots =
      keeperAssignments + realPicks;

    if (filledSlots > totalSlots) {
      throw new Error(
        `Filled slots ${filledSlots} exceed total slots ${totalSlots}.`,
      );
    }

    const draftableSlots =
      totalSlots -
      keeperAssignments;

    if (realPicks > draftableSlots) {
      throw new Error(
        `Real picks ${realPicks} exceed draftable slots ${draftableSlots}.`,
      );
    }

    if (
      faCount +
      qoCount +
      poachCount !==
      realPicks
    ) {
      throw new Error(
        "FA + QO + POACH counts do not equal real selections.",
      );
    }

    const realPicksRemaining =
      draftableSlots -
      realPicks;

    const applicationRecordedPicks =
      realPicks -
      legacyImportedPicks;

    const rankResult =
      await client.query<{
        ranked_real_picks: Numeric;
        average_rank: NullableNumeric;
        best_rank: NullableNumeric;
        worst_rank: NullableNumeric;
      }>(
        `
        SELECT
            count(pu.rank_value)
                AS ranked_real_picks,

            avg(pu.rank_value)
                AS average_rank,

            min(pu.rank_value)
                AS best_rank,

            max(pu.rank_value)
                AS worst_rank

        FROM mlf.draft_selection AS ds

        LEFT JOIN mlf.player_universe AS pu
          ON pu.league_key = $2
         AND pu.season_year = $3
         AND pu.yahoo_player_key =
             ds.yahoo_player_key

        WHERE ds.draft_key = $1
        `,
        [
          draftKey,
          draft.league_key,
          draft.season_year,
        ],
      );

    const rankRow =
      rankResult.rows[0];

    const rankedRealPicks =
      numberValue(
        rankRow.ranked_real_picks,
        "rank coverage",
      );

    const averageRank =
      nullableNumber(
        rankRow.average_rank,
        "average rank",
      );

    const bestRank =
      nullableNumber(
        rankRow.best_rank,
        "best rank",
      );

    const worstRank =
      nullableNumber(
        rankRow.worst_rank,
        "worst rank",
      );

    const timingResult =
      await client.query<{
        cumulative_seconds: NullableNumeric;
      }>(
        `
        WITH ordered AS (
            SELECT
                ds.selected_at_utc,

                lag(ds.selected_at_utc)
                OVER (
                    ORDER BY
                        ds.selected_at_utc
                            NULLS LAST,
                        dp.round_number,
                        dp.slot_number
                ) AS previous_selected_at

            FROM mlf.draft_selection AS ds

            JOIN mlf.draft_pick AS dp
              ON dp.draft_key =
                 ds.draft_key
             AND dp.pick_id =
                 ds.pick_id

            WHERE ds.draft_key = $1
        )

        SELECT
            sum(
                CASE
                    WHEN selected_at_utc IS NULL
                      OR previous_selected_at IS NULL
                    THEN 0

                    ELSE greatest(
                        extract(
                            epoch FROM (
                                selected_at_utc -
                                previous_selected_at
                            )
                        ),
                        0
                    )
                END
            ) AS cumulative_seconds

        FROM ordered
        `,
        [draftKey],
      );

    const cumulativeWallClockSeconds =
      nullableNumber(
        timingResult.rows[0]
          .cumulative_seconds,
        "cumulative wall clock",
      ) ?? 0;

    const averageWallClockSeconds =
      realPicks > 0
        ? (
            cumulativeWallClockSeconds /
            realPicks
          )
        : null;

    const teamResult =
      await client.query<RawTeamRow>(
        `
        WITH ordered AS (
            SELECT
                ds.selecting_team_key,
                ds.pick_kind,
                pu.rank_value,
                ds.selected_at_utc,

                lag(ds.selected_at_utc)
                OVER (
                    ORDER BY
                        ds.selected_at_utc
                            NULLS LAST,
                        dp.round_number,
                        dp.slot_number
                ) AS previous_selected_at

            FROM mlf.draft_selection AS ds

            JOIN mlf.draft_pick AS dp
              ON dp.draft_key =
                 ds.draft_key
             AND dp.pick_id =
                 ds.pick_id

            LEFT JOIN mlf.player_universe AS pu
              ON pu.league_key = $2
             AND pu.season_year = $3
             AND pu.yahoo_player_key =
                 ds.yahoo_player_key

            WHERE ds.draft_key = $1
        ),

        aggregated AS (
            SELECT
                selecting_team_key
                    AS team_key,

                count(*)
                    AS real_picks,

                count(*) FILTER (
                    WHERE pick_kind = 'FA'
                ) AS fa_picks,

                count(*) FILTER (
                    WHERE pick_kind = 'QO'
                ) AS qo_picks,

                count(*) FILTER (
                    WHERE pick_kind = 'POACH'
                ) AS poach_picks,

                count(rank_value)
                    AS ranked_picks,

                avg(rank_value)
                    AS average_rank,

                sum(
                    CASE
                        WHEN selected_at_utc IS NULL
                          OR previous_selected_at IS NULL
                        THEN 0

                        ELSE greatest(
                            extract(
                                epoch FROM (
                                    selected_at_utc -
                                    previous_selected_at
                                )
                            ),
                            0
                        )
                    END
                ) AS cumulative_seconds

            FROM ordered
            GROUP BY selecting_team_key
        )

        SELECT
            t.team_key,

            COALESCE(
                ytm.team_name,
                t.team_name,
                t.team_key
            ) AS team_name,

            COALESCE(
                ytm.owner_name,
                ''
            ) AS owner_name,

            COALESCE(
                a.real_picks,
                0
            ) AS real_picks,

            COALESCE(
                a.fa_picks,
                0
            ) AS fa_picks,

            COALESCE(
                a.qo_picks,
                0
            ) AS qo_picks,

            COALESCE(
                a.poach_picks,
                0
            ) AS poach_picks,

            COALESCE(
                a.ranked_picks,
                0
            ) AS ranked_picks,

            a.average_rank,

            COALESCE(
                a.cumulative_seconds,
                0
            ) AS cumulative_seconds

        FROM mlf.team AS t

        LEFT JOIN aggregated AS a
          ON a.team_key = t.team_key

        LEFT JOIN public.yahoo_team_map AS ytm
          ON ytm.league_key = $2
         AND ytm.season_year = $3
         AND ytm.team_key = t.team_key

        WHERE t.league_key = $2
          AND t.season_year = $3

        ORDER BY
            COALESCE(
                ytm.team_name,
                t.team_name,
                t.team_key
            )
        `,
        [
          draftKey,
          draft.league_key,
          draft.season_year,
        ],
      );

    if (
      teamResult.rows.length !==
      teamCount
    ) {
      throw new Error(
        `Expected ${teamCount} teams; received ${teamResult.rows.length}.`,
      );
    }

    const teams: TeamDraftStat[] =
      teamResult.rows.map((row) => {
        const teamRealPicks =
          numberValue(
            row.real_picks,
            "team real picks",
          );

        const cumulativeSeconds =
          nullableNumber(
            row.cumulative_seconds,
            "team cumulative wall clock",
          ) ?? 0;

        return {
          teamKey: row.team_key,
          teamName:
            row.team_name?.trim() ||
            row.team_key,
          ownerName:
            row.owner_name?.trim() ||
            "",
          realPicks: teamRealPicks,
          faPicks:
            numberValue(
              row.fa_picks,
              "team FA picks",
            ),
          qoPicks:
            numberValue(
              row.qo_picks,
              "team QO picks",
            ),
          poachPicks:
            numberValue(
              row.poach_picks,
              "team poach picks",
            ),
          rankedPicks:
            numberValue(
              row.ranked_picks,
              "team ranked picks",
            ),
          averageRank:
            nullableNumber(
              row.average_rank,
              "team average rank",
            ),
          cumulativeWallClockSeconds:
            cumulativeSeconds,
          averageWallClockSeconds:
            teamRealPicks > 0
              ? (
                  cumulativeSeconds /
                  teamRealPicks
                )
              : null,
        };
      });

    const teamPickTotal =
      teams.reduce(
        (sum, team) =>
          sum + team.realPicks,
        0,
      );

    if (
      teamPickTotal !==
      realPicks
    ) {
      throw new Error(
        `Team pick total ${teamPickTotal}/${realPicks}.`,
      );
    }

    const roundResult =
      await client.query<RawRoundRow>(
        `
        WITH round_counts AS (
            SELECT
                dp.round_number,
                count(*) AS real_picks

            FROM mlf.draft_selection AS ds

            JOIN mlf.draft_pick AS dp
              ON dp.draft_key =
                 ds.draft_key
             AND dp.pick_id =
                 ds.pick_id

            WHERE ds.draft_key = $1

            GROUP BY dp.round_number
        )

        SELECT
            rounds.round_number,

            COALESCE(
                rc.real_picks,
                0
            ) AS real_picks

        FROM generate_series(
            1,
            $2::integer
        ) AS rounds(round_number)

        LEFT JOIN round_counts AS rc
          ON rc.round_number =
             rounds.round_number

        ORDER BY rounds.round_number
        `,
        [
          draftKey,
          roundsTotal,
        ],
      );

    const rounds =
      roundResult.rows.map((row) => ({
        roundNumber:
          numberValue(
            row.round_number,
            "round number",
          ),
        realPicks:
          numberValue(
            row.real_picks,
            "round real picks",
          ),
      }));

    const roundPickTotal =
      rounds.reduce(
        (sum, round) =>
          sum + round.realPicks,
        0,
      );

    if (
      roundPickTotal !==
      realPicks
    ) {
      throw new Error(
        `Round pick total ${roundPickTotal}/${realPicks}.`,
      );
    }

    const positionResult =
      await client.query<RawPositionRow>(
        `
        SELECT
            COALESCE(
                pu.primary_position,
                'UNKNOWN'
            ) AS position,

            count(*) AS real_picks

        FROM mlf.draft_selection AS ds

        LEFT JOIN mlf.player_universe AS pu
          ON pu.league_key = $2
         AND pu.season_year = $3
         AND pu.yahoo_player_key =
             ds.yahoo_player_key

        WHERE ds.draft_key = $1

        GROUP BY 1
        ORDER BY
            real_picks DESC,
            position
        `,
        [
          draftKey,
          draft.league_key,
          draft.season_year,
        ],
      );

    const positions =
      positionResult.rows.map((row) => ({
        position: row.position,
        realPicks:
          numberValue(
            row.real_picks,
            "position real picks",
          ),
      }));

    const positionPickTotal =
      positions.reduce(
        (sum, position) =>
          sum + position.realPicks,
        0,
      );

    if (
      positionPickTotal !==
      realPicks
    ) {
      throw new Error(
        `Position pick total ${positionPickTotal}/${realPicks}.`,
      );
    }

    const dailyResult =
      await client.query<RawDailyRow>(
        `
        SELECT
            to_char(
                (
                    ds.selected_at_utc
                    AT TIME ZONE $2
                )::date,
                'YYYY-MM-DD'
            ) AS pick_date,

            count(*)
                AS daily_real_picks

        FROM mlf.draft_selection AS ds

        WHERE ds.draft_key = $1
          AND ds.selected_at_utc
              IS NOT NULL

        GROUP BY 1
        ORDER BY 1
        `,
        [
          draftKey,
          timeZone,
        ],
      );

    const dailyCounts =
      new Map<string, number>(
        dailyResult.rows.map((row) => [
          row.pick_date,
          numberValue(
            row.daily_real_picks,
            "daily pick count",
          ),
        ]),
      );

    const firstPickDate =
      totalRow.first_pick_date;

    const lastPickDate =
      totalRow.last_pick_date;

    const today =
      todayInTimeZone(timeZone);

    const paceReferenceDate =
      draft.status === "complete" &&
      lastPickDate
        ? lastPickDate
        : today;

    let averagePicksPerDay:
      number | null = null;

    if (
      firstPickDate &&
      realPicks > 0
    ) {
      const elapsed =
        Math.max(
          1,
          daysInclusive(
            firstPickDate,
            paceReferenceDate,
          ),
        );

      averagePicksPerDay =
        realPicks / elapsed;
    }

    let projectedCompletionDate:
      string | null = null;

    if (
      realPicksRemaining === 0
    ) {
      projectedCompletionDate =
        lastPickDate;
    }
    else if (
      averagePicksPerDay &&
      averagePicksPerDay > 0
    ) {
      const projectedDays =
        Math.max(
          0,
          Math.ceil(
            realPicksRemaining /
            averagePicksPerDay,
          ) - 1,
        );

      projectedCompletionDate =
        addDays(
          paceReferenceDate,
          projectedDays,
        );
    }

    let requiredPicksPerDay:
      number | null = null;

    if (
      openingDayDate &&
      parseDateOnly(openingDayDate)
        .getTime() >=
      parseDateOnly(paceReferenceDate)
        .getTime()
    ) {
      const daysRemaining =
        daysInclusive(
          paceReferenceDate,
          openingDayDate,
        );

      requiredPicksPerDay =
        realPicksRemaining /
        Math.max(
          1,
          daysRemaining,
        );
    }

    const dailyPace:
      DailyPaceStat[] = [];

    if (firstPickDate) {
      let chartEndDate =
        paceReferenceDate;

      if (
        openingDayDate &&
        parseDateOnly(openingDayDate)
          .getTime() >
        parseDateOnly(chartEndDate)
          .getTime()
      ) {
        chartEndDate =
          openingDayDate;
      }

      const totalChartDays =
        Math.max(
          1,
          daysInclusive(
            firstPickDate,
            chartEndDate,
          ),
        );

      const requiredDays =
        openingDayDate &&
        parseDateOnly(openingDayDate)
          .getTime() >=
        parseDateOnly(firstPickDate)
          .getTime()
          ? daysInclusive(
              firstPickDate,
              openingDayDate,
            )
          : null;

      let cumulative = 0;

      for (
        let index = 0;
        index < totalChartDays;
        index += 1
      ) {
        const date =
          addDays(
            firstPickDate,
            index,
          );

        const daily =
          dailyCounts.get(date) ?? 0;

        cumulative += daily;

        const requiredCumulative =
          requiredDays
            ? Math.min(
                draftableSlots,
                (
                  draftableSlots *
                  (index + 1)
                ) /
                requiredDays,
              )
            : null;

        const projectedCumulative =
          averagePicksPerDay
            ? Math.min(
                draftableSlots,
                averagePicksPerDay *
                (index + 1),
              )
            : null;

        dailyPace.push({
          date,
          dailyRealPicks: daily,
          actualCumulative:
            cumulative,
          requiredCumulative,
          projectedCumulative,
        });
      }
    }

    await client.query("ROLLBACK");

    return {
      leagueKey:
        draft.league_key,
      seasonYear:
        draft.season_year,
      status:
        draft.status,
      timeZone,

      teamCount,
      roundsTotal,
      totalSlots,
      keeperAssignments,
      draftableSlots,
      filledSlots,

      realPicks,
      realPicksRemaining,

      faCount,
      qoCount,
      poachCount,

      rankedRealPicks,
      averageRank,
      bestRank,
      worstRank,

      firstPickDate,
      lastPickDate,
      openingDayDate,

      averagePicksPerDay,
      averageWallClockSeconds,
      projectedCompletionDate,
      requiredPicksPerDay,

      legacyImportedPicks,
      applicationRecordedPicks,

      teams,
      rounds,
      positions,
      dailyPace,
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