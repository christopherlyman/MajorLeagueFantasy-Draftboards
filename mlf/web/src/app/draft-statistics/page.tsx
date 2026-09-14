import { AppShell } from "../../components/AppShell";
import {
  loadDraftStatistics,
  type DailyPaceStat,
  type TeamDraftStat,
} from "../../lib/draftStatistics";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const DRAFT_KEY =
  process.env.DRAFTBOARD_DRAFT_KEY ??
  "mlf_2026_preseason";

function duration(
  seconds: number | null,
): string {
  if (seconds === null) {
    return "\u2014";
  }

  const value =
    Math.max(
      0,
      Math.round(seconds),
    );

  const hours =
    Math.floor(value / 3600);

  const minutes =
    Math.floor(
      (value % 3600) / 60,
    );

  const remaining =
    value % 60;

  return [
    hours,
    minutes,
    remaining,
  ]
    .map((part) =>
      part
        .toString()
        .padStart(2, "0"),
    )
    .join(":");
}

function rank(
  value: number | null,
): string {
  return value === null
    ? "\u2014"
    : value.toFixed(1);
}

function pace(
  value: number | null,
): string {
  return value === null
    ? "\u2014"
    : value.toFixed(2);
}

function PaceChart({
  rows,
  target,
}: {
  rows: DailyPaceStat[];
  target: number;
}) {
  if (rows.length === 0) {
    return (
      <div className={styles.emptyChart}>
        Pace history will appear after
        the first real selection.
      </div>
    );
  }

  const width = 900;
  const height = 250;
  const left = 44;
  const right = 18;
  const top = 18;
  const bottom = 34;

  const plotWidth =
    width - left - right;

  const plotHeight =
    height - top - bottom;

  const maxY =
    Math.max(
      target,
      ...rows.map(
        (row) =>
          Math.max(
            row.actualCumulative,
            row.requiredCumulative ?? 0,
            row.projectedCumulative ?? 0,
          ),
      ),
      1,
    );

  const x = (
    index: number,
  ) =>
    rows.length === 1
      ? left + plotWidth / 2
      : left +
        (
          plotWidth *
          index
        ) /
        (rows.length - 1);

  const y = (
    value: number,
  ) =>
    top +
    plotHeight -
    (
      value /
      maxY
    ) *
      plotHeight;

  const points = (
    getter:
      (row: DailyPaceStat) =>
        number | null,
  ) =>
    rows
      .map((row, index) => {
        const value =
          getter(row);

        if (value === null) {
          return null;
        }

        return (
          `${x(index)},` +
          `${y(value)}`
        );
      })
      .filter(Boolean)
      .join(" ");

  const actual =
    points(
      (row) =>
        row.actualCumulative,
    );

  const required =
    points(
      (row) =>
        row.requiredCumulative,
    );

  const projected =
    points(
      (row) =>
        row.projectedCumulative,
    );

  return (
    <div className={styles.chartWrap}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={styles.paceChart}
        role="img"
        aria-label="Cumulative real draft picks by date"
      >
        <line
          x1={left}
          x2={left}
          y1={top}
          y2={height - bottom}
          className={styles.axisLine}
        />

        <line
          x1={left}
          x2={width - right}
          y1={height - bottom}
          y2={height - bottom}
          className={styles.axisLine}
        />

        <line
          x1={left}
          x2={width - right}
          y1={y(maxY)}
          y2={y(maxY)}
          className={styles.gridLine}
        />

        <text
          x={left - 8}
          y={y(maxY) + 4}
          textAnchor="end"
          className={styles.axisText}
        >
          {Math.round(maxY)}
        </text>

        <text
          x={left - 8}
          y={height - bottom + 4}
          textAnchor="end"
          className={styles.axisText}
        >
          0
        </text>

        {required && (
          <polyline
            points={required}
            className={styles.requiredLine}
          />
        )}

        {projected && (
          <polyline
            points={projected}
            className={styles.projectedLine}
          />
        )}

        <polyline
          points={actual}
          className={styles.actualLine}
        />

        {rows.map((row, index) => (
          <circle
            key={row.date}
            cx={x(index)}
            cy={y(
              row.actualCumulative,
            )}
            r="2.7"
            className={styles.actualPoint}
          />
        ))}

        <text
          x={left}
          y={height - 10}
          textAnchor="start"
          className={styles.axisText}
        >
          {rows[0].date}
        </text>

        <text
          x={width - right}
          y={height - 10}
          textAnchor="end"
          className={styles.axisText}
        >
          {rows[rows.length - 1].date}
        </text>
      </svg>

      <div className={styles.legend}>
        <span className={styles.legendActual}>
          Actual cumulative
        </span>

        <span className={styles.legendProjected}>
          Actual pace trend
        </span>

        <span className={styles.legendRequired}>
          Required pace
        </span>
      </div>
    </div>
  );
}

function TeamTable({
  teams,
}: {
  teams: TeamDraftStat[];
}) {
  return (
    <div className={styles.desktopTableWrap}>
      <table className={styles.teamTable}>
        <thead>
          <tr>
            <th>Team</th>
            <th>Owner</th>
            <th>Picks</th>
            <th>FA</th>
            <th>QO</th>
            <th>Poach</th>
            <th>Avg Rank</th>
            <th>Avg Wall</th>
            <th>Cumulative</th>
          </tr>
        </thead>

        <tbody>
          {teams.map((team) => (
            <tr
              key={team.teamKey}
              data-stats-team-key={
                team.teamKey
              }
            >
              <td className={styles.teamName}>
                {team.teamName}
              </td>

              <td className={styles.ownerName}>
                {team.ownerName || "\u2014"}
              </td>

              <td>{team.realPicks}</td>
              <td>{team.faPicks}</td>
              <td>{team.qoPicks}</td>
              <td>{team.poachPicks}</td>

              <td className={styles.rankValue}>
                {rank(team.averageRank)}
              </td>

              <td>
                {duration(
                  team.averageWallClockSeconds,
                )}
              </td>

              <td>
                {duration(
                  team.cumulativeWallClockSeconds,
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MobileTeams({
  teams,
}: {
  teams: TeamDraftStat[];
}) {
  return (
    <div className={styles.mobileTeams}>
      {teams.map((team) => (
        <article
          key={team.teamKey}
          className={styles.teamCard}
          data-stats-mobile-team-key={
            team.teamKey
          }
        >
          <div className={styles.teamCardHeader}>
            <div>
              <strong>{team.teamName}</strong>
              <span>
                {team.ownerName
                  ? `Owner: ${team.ownerName}`
                  : "Owner unavailable"}
              </span>
            </div>

            <div className={styles.teamPickCount}>
              <strong>{team.realPicks}</strong>
              <span>Picks</span>
            </div>
          </div>

          <div className={styles.teamCardMetrics}>
            <div>
              <span>FA</span>
              <strong>{team.faPicks}</strong>
            </div>

            <div>
              <span>QO</span>
              <strong>{team.qoPicks}</strong>
            </div>

            <div>
              <span>Poach</span>
              <strong>{team.poachPicks}</strong>
            </div>

            <div>
              <span>Avg Rank</span>
              <strong>
                {rank(team.averageRank)}
              </strong>
            </div>

            <div>
              <span>Avg Wall</span>
              <strong>
                {duration(
                  team.averageWallClockSeconds,
                )}
              </strong>
            </div>

            <div>
              <span>Cumulative</span>
              <strong>
                {duration(
                  team.cumulativeWallClockSeconds,
                )}
              </strong>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

export default async function DraftStatisticsPage() {
  const snapshot =
    await loadDraftStatistics(
      DRAFT_KEY,
    );

  const maxPosition =
    Math.max(
      ...snapshot.positions.map(
        (row) => row.realPicks,
      ),
      1,
    );

  return (
    <AppShell
      title="Draft Statistics"
      subtitle={
        `${snapshot.seasonYear} MLF \u00b7 Draft Analytics`
      }
      activePath="/draft-statistics"
    >
      <div className={styles.kpiGrid}>
        <div>
          <strong>
            {snapshot.realPicks}
          </strong>
          <span>Real Picks</span>
        </div>

        <div>
          <strong>
            {snapshot.realPicksRemaining}
          </strong>
          <span>Picks Remaining</span>
        </div>

        <div>
          <strong>
            {pace(
              snapshot.averagePicksPerDay,
            )}
          </strong>
          <span>Avg Picks / Day</span>
        </div>

        <div>
          <strong>
            {snapshot.projectedCompletionDate ??
              "\u2014"}
          </strong>
          <span>Projected Finish</span>
        </div>

        <div>
          <strong>
            {duration(
              snapshot.averageWallClockSeconds,
            )}
          </strong>
          <span>Avg Wall-Clock / Pick</span>
        </div>

        <div>
          <strong>
            {pace(
              snapshot.requiredPicksPerDay,
            )}
          </strong>
          <span>Required Picks / Day</span>
        </div>
      </div>

      <section className={styles.paceSection}>
        <div className={styles.panelHeading}>
          <div>
            <h2>Draft Pace</h2>
            <p>
              Real selections by calendar date,
              with observed pace and the configured
              Opening Day target.
            </p>
          </div>

          <div className={styles.targetMeta}>
            <strong>
              {snapshot.openingDayDate ??
                "Not configured"}
            </strong>
            <span>Opening Day</span>
          </div>
        </div>

        <PaceChart
          rows={snapshot.dailyPace}
          target={snapshot.draftableSlots}
        />
      </section>

      <div className={styles.topGrid}>
        <section className={styles.panel}>
          <div className={styles.panelHeading}>
            <div>
              <h2>Draft Makeup</h2>
              <p>
                Canonical slot and selection state.
              </p>
            </div>
          </div>

          <div className={styles.makeupGrid}>
            <div>
              <strong>{snapshot.totalSlots}</strong>
              <span>Total Slots</span>
            </div>

            <div>
              <strong>
                {snapshot.keeperAssignments}
              </strong>
              <span>Keeper / PT</span>
            </div>

            <div>
              <strong>
                {snapshot.draftableSlots}
              </strong>
              <span>Real-Pick Target</span>
            </div>

            <div>
              <strong>{snapshot.filledSlots}</strong>
              <span>Filled Slots</span>
            </div>
          </div>

          <div className={styles.pickMix}>
            <div className={styles.mixFa}>
              <strong>{snapshot.faCount}</strong>
              <span>FA</span>
            </div>

            <div className={styles.mixQo}>
              <strong>{snapshot.qoCount}</strong>
              <span>QO</span>
            </div>

            <div className={styles.mixPoach}>
              <strong>{snapshot.poachCount}</strong>
              <span>Poach</span>
            </div>
          </div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeading}>
            <div>
              <h2>Position Mix</h2>
              <p>
                Primary position of each real
                selection.
              </p>
            </div>
          </div>

          <div className={styles.positionList}>
            {snapshot.positions.map((row) => (
              <div
                key={row.position}
                className={styles.positionRow}
                data-stats-position={
                  row.position
                }
              >
                <span className={styles.positionLabel}>
                  {row.position}
                </span>

                <div className={styles.positionTrack}>
                  <div
                    className={styles.positionFill}
                    style={{
                      width:
                        `${(
                          row.realPicks /
                          maxPosition
                        ) * 100}%`,
                    }}
                  />
                </div>

                <strong>{row.realPicks}</strong>
              </div>
            ))}
          </div>

          <div className={styles.rankSummary}>
            <div>
              <span>Ranked Picks</span>
              <strong>
                {snapshot.rankedRealPicks}
              </strong>
            </div>

            <div>
              <span>Average Rank</span>
              <strong>
                {rank(snapshot.averageRank)}
              </strong>
            </div>

            <div>
              <span>Rank Range</span>
              <strong>
                {snapshot.bestRank === null
                  ? "\u2014"
                  : `${snapshot.bestRank.toFixed(0)}\u2013${snapshot.worstRank?.toFixed(0) ?? "\u2014"}`}
              </strong>
            </div>
          </div>
        </section>
      </div>

      <section className={styles.roundSection}>
        <div className={styles.panelHeading}>
          <div>
            <h2>Real Picks by Round</h2>
            <p>
              Real selections only; keeper and
              prospect assignments remain separate
              canonical slot state.
            </p>
          </div>
        </div>

        <div className={styles.roundGrid}>
          {snapshot.rounds.map((round) => (
            <div
              key={round.roundNumber}
              className={styles.roundCard}
              data-stats-round={
                round.roundNumber
              }
            >
              <div className={styles.roundTop}>
                <span>
                  R{round.roundNumber}
                </span>
                <strong>
                  {round.realPicks}
                </strong>
              </div>

              <div className={styles.roundTrack}>
                <div
                  className={styles.roundFill}
                  style={{
                    width:
                      `${Math.min(
                        100,
                        (
                          round.realPicks /
                          snapshot.teamCount
                        ) * 100,
                      )}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className={styles.teamSection}>
        <div className={styles.panelHeading}>
          <div>
            <h2>Team Draft Profiles</h2>
            <p>
              Selection mix, canonical player rank,
              and elapsed wall-clock time between
              consecutive real selections.
            </p>
          </div>

          <div className={styles.targetMeta}>
            <strong>
              {snapshot.teamCount}
            </strong>
            <span>Teams</span>
          </div>
        </div>

        <TeamTable teams={snapshot.teams} />
        <MobileTeams teams={snapshot.teams} />
      </section>

      {snapshot.legacyImportedPicks > 0 ? (
        <div className={styles.legacyNote}>
          Current timing includes{" "}
          {snapshot.legacyImportedPicks} migrated
          legacy selections. Those timestamps are
          retained for historical reconstruction and
          may not represent actual live-draft clock
          usage. Future application-recorded picks
          use the canonical selection timestamp
          contract.
        </div>
      ) : (
        <div className={styles.integrityNote}>
          Timing is calculated from canonical
          application-recorded selection timestamps.
          Wall-clock values intentionally include
          ordinary overnight, weekend, and pause
          time between selections.
        </div>
      )}

      {!snapshot.openingDayDate && (
        <div className={styles.configNote}>
          Required pace is unavailable until
          DRAFTBOARD_OPENING_DAY_DATE is configured
          for {snapshot.seasonYear}.
        </div>
      )}
    </AppShell>
  );
}