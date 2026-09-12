import Link from "next/link";

import { AppShell } from "../../components/AppShell";

import {
  buildTeamLineup,
  isPitcher,
  loadTeams,
  type LineupRow,
  type TeamPlayer,
} from "../../lib/teams";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const DRAFT_KEY =
  process.env.DRAFTBOARD_DRAFT_KEY ??
  "mlf_2026_preseason";

type SearchParams = {
  team?: string | string[];
};

type TeamsPageProps = {
  searchParams: Promise<SearchParams>;
};

function first(
  value: string | string[] | undefined,
): string {
  if (Array.isArray(value)) {
    return value[0] ?? "";
  }

  return value ?? "";
}

function intValue(
  value: number | null,
): string {
  if (value === null) {
    return "";
  }

  return String(
    Math.round(value),
  );
}

function decimalValue(
  value: number | null,
  decimals: number,
): string {
  if (value === null) {
    return "";
  }

  return value.toFixed(decimals);
}

function avgValue(
  value: string,
): string {
  const text = value.trim();

  if (!text) {
    return "";
  }

  const number =
    Number(text);

  if (!Number.isFinite(number)) {
    return text;
  }

  const formatted =
    number.toFixed(3);

  return formatted.startsWith("0")
    ? formatted.slice(1)
    : formatted;
}

function positions(
  player: TeamPlayer,
): string {
  const raw = [...player.positions];

  if (isPitcher(player)) {
    if (
      raw.includes("P") &&
      raw.length > 1
    ) {
      return raw
        .filter(
          (position) =>
            position !== "P",
        )
        .join("/");
    }

    return raw.join("/");
  }

  if (
    raw.includes("UTIL") &&
    raw.length > 1
  ) {
    return raw
      .filter(
        (position) =>
          position !== "UTIL",
      )
      .join("/");
  }

  return raw.join("/");
}

function hitterStats(
  player: TeamPlayer,
): Array<[string, string]> {
  return [
    ["H/AB", player.hAb],
    ["R", intValue(player.r)],
    ["HR", intValue(player.hr)],
    ["RBI", intValue(player.rbi)],
    ["SB", intValue(player.sb)],
    ["BB", intValue(player.bb)],
    ["K", intValue(player.kHit)],
    ["AVG", avgValue(player.avg)],
  ];
}

function pitcherStats(
  player: TeamPlayer,
): Array<[string, string]> {
  return [
    ["IP", decimalValue(player.ip, 1)],
    ["W", intValue(player.w)],
    ["K", intValue(player.kPit)],
    ["TB", intValue(player.tb)],
    ["ERA", decimalValue(player.era, 2)],
    ["WHIP", decimalValue(player.whip, 2)],
    ["QS", intValue(player.qs)],
    ["SV+H", intValue(player.svH)],
  ];
}

function PlayerIdentity({
  player,
}: {
  player: TeamPlayer;
}) {
  return (
    <div className={styles.playerIdentity}>
      <strong>{player.playerName}</strong>

      <span>
        {player.mlbTeam || "\u2014"}
        {" \u00b7 "}
        {positions(player) || "\u2014"}
      </span>
    </div>
  );
}

function HitterTable({
  rows,
}: {
  rows: LineupRow[];
}) {
  return (
    <div className={styles.tableScroller}>
      <table
        className={styles.rosterTable}
        data-team-hitter-table
      >
        <thead>
          <tr>
            <th>POS</th>
            <th>Player</th>
            <th>Team</th>
            <th>Position</th>
            <th>Contract</th>
            <th>Rank</th>
            <th>% Ros</th>
            <th>H/AB</th>
            <th>R</th>
            <th>HR</th>
            <th>RBI</th>
            <th>SB</th>
            <th>BB</th>
            <th>K (H)</th>
            <th>AVG</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((row, index) => {
            const p = row.player;

            return (
              <tr
                key={`${row.slot}-${index}`}
                data-lineup-row
                data-team-player-key={
                  p?.playerKey
                }
              >
                <td className={styles.slot}>
                  {row.slot}
                </td>

                <td className={styles.playerName}>
                  {p?.playerName ?? ""}
                </td>

                <td>{p?.mlbTeam ?? ""}</td>
                <td>{p ? positions(p) : ""}</td>

                <td className={styles.control}>
                  {p?.control ?? ""}
                </td>

                <td>{p ? intValue(p.rank) : ""}</td>
                <td>{p ? intValue(p.percentRostered) : ""}</td>
                <td>{p?.hAb ?? ""}</td>
                <td>{p ? intValue(p.r) : ""}</td>
                <td>{p ? intValue(p.hr) : ""}</td>
                <td>{p ? intValue(p.rbi) : ""}</td>
                <td>{p ? intValue(p.sb) : ""}</td>
                <td>{p ? intValue(p.bb) : ""}</td>
                <td>{p ? intValue(p.kHit) : ""}</td>
                <td>{p ? avgValue(p.avg) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PitcherTable({
  rows,
}: {
  rows: LineupRow[];
}) {
  return (
    <div className={styles.tableScroller}>
      <table
        className={styles.rosterTable}
        data-team-pitcher-table
      >
        <thead>
          <tr>
            <th>POS</th>
            <th>Player</th>
            <th>Team</th>
            <th>Position</th>
            <th>Contract</th>
            <th>Rank</th>
            <th>% Ros</th>
            <th>IP</th>
            <th>W</th>
            <th>K (P)</th>
            <th>TB</th>
            <th>ERA</th>
            <th>WHIP</th>
            <th>QS</th>
            <th>SV+H</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((row, index) => {
            const p = row.player;

            return (
              <tr
                key={`${row.slot}-${index}`}
                data-lineup-row
                data-team-player-key={
                  p?.playerKey
                }
              >
                <td className={styles.slot}>
                  {row.slot}
                </td>

                <td className={styles.playerName}>
                  {p?.playerName ?? ""}
                </td>

                <td>{p?.mlbTeam ?? ""}</td>
                <td>{p ? positions(p) : ""}</td>

                <td className={styles.control}>
                  {p?.control ?? ""}
                </td>

                <td>{p ? intValue(p.rank) : ""}</td>
                <td>{p ? intValue(p.percentRostered) : ""}</td>
                <td>{p ? decimalValue(p.ip, 1) : ""}</td>
                <td>{p ? intValue(p.w) : ""}</td>
                <td>{p ? intValue(p.kPit) : ""}</td>
                <td>{p ? intValue(p.tb) : ""}</td>
                <td>{p ? decimalValue(p.era, 2) : ""}</td>
                <td>{p ? decimalValue(p.whip, 2) : ""}</td>
                <td>{p ? intValue(p.qs) : ""}</td>
                <td>{p ? intValue(p.svH) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MobileSection({
  title,
  rows,
  pitcher,
}: {
  title: string;
  rows: LineupRow[];
  pitcher: boolean;
}) {
  return (
    <section className={styles.mobileSection}>
      <h2>{title}</h2>

      <div className={styles.mobileCards}>
        {rows.map((row, index) => {
          const player = row.player;

          return (
            <article
              key={`${row.slot}-${index}`}
              className={styles.mobileCard}
              data-team-mobile-row
              data-team-mobile-player-key={
                player?.playerKey
              }
            >
              <div className={styles.mobileCardHeader}>
                <div className={styles.mobileSlot}>
                  {row.slot}
                </div>

                {player ? (
                  <>
                    <PlayerIdentity player={player} />

                    <div className={styles.mobileRank}>
                      <span>Rank</span>
                      <strong>
                        {intValue(player.rank) || "\u2014"}
                      </strong>
                    </div>

                    <div className={styles.mobileRank}>
                      <span>% Ros</span>
                      <strong>
                        {intValue(
                          player.percentRostered,
                        ) || "\u2014"}
                      </strong>
                    </div>
                  </>
                ) : (
                  <div className={styles.openSlot}>
                    Open slot
                  </div>
                )}
              </div>

              {player?.control ? (
                <div className={styles.mobileControl}>
                  {player.control === "PT"
                    ? "Prospect Tag"
                    : `${player.control}-year contract`}
                </div>
              ) : null}

              {player ? (
                <div className={styles.mobileStats}>
                  {(pitcher
                    ? pitcherStats(player)
                    : hitterStats(player)
                  ).map(([label, value]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <strong>
                        {value || "\u2014"}
                      </strong>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export default async function TeamsPage({
  searchParams,
}: TeamsPageProps) {
  const params = await searchParams;
  const teams = await loadTeams(DRAFT_KEY);

  const requestedTeam =
    first(params.team);

  const selectedTeam =
    teams.find(
      (team) =>
        team.teamKey === requestedTeam,
    ) ??
    teams[0];

  if (!selectedTeam) {
    throw new Error(
      "No MLF teams are available.",
    );
  }

  const lineup =
    buildTeamLineup(
      selectedTeam.players,
    );

  const contracts =
    selectedTeam.players.filter(
      (player) =>
        player.control &&
        player.control !== "PT",
    ).length;

  const pts =
    selectedTeam.players.filter(
      (player) =>
        player.control === "PT",
    ).length;

  return (
    <AppShell
      title="Teams"
      subtitle={"2026 MLF Draft Rosters \u00b7 2025 Stats"}
      activePath="/teams"
    >
      <nav
        className={styles.teamTabs}
        aria-label="MLF teams"
      >
        {teams.map((team) => {
          const active =
            team.teamKey ===
            selectedTeam.teamKey;

          return (
            <Link
              key={team.teamKey}
              href={`/teams?team=${encodeURIComponent(team.teamKey)}`}
              className={
                active
                  ? `${styles.teamTab} ${styles.teamTabActive}`
                  : styles.teamTab
              }
              aria-current={
                active
                  ? "page"
                  : undefined
              }
            >
              {team.teamName}
            </Link>
          );
        })}
      </nav>

      <section
        className={styles.teamSummary}
        data-team-key={selectedTeam.teamKey}
      >
        <div className={styles.teamHeading}>
          <span>
            Draft slot {selectedTeam.draftSlot}
          </span>

          <strong>
            {selectedTeam.teamName}
          </strong>
        </div>

        <div className={styles.summaryMetric}>
          <strong>
            {selectedTeam.players.length}
          </strong>
          <span>Drafted / Kept</span>
        </div>

        <div className={styles.summaryMetric}>
          <strong>18</strong>
          <span>Starter slots</span>
        </div>

        <div className={styles.summaryMetric}>
          <strong>7</strong>
          <span>Bench</span>
        </div>

        <div className={styles.summaryMetric}>
          <strong>{contracts}</strong>
          <span>Contracts</span>
        </div>

        <div className={styles.summaryMetric}>
          <strong>{pts}</strong>
          <span>PT</span>
        </div>
      </section>

      {lineup.overflow > 0 ? (
        <div
          className={styles.rosterNotice}
          role="note"
        >
          <strong>
            Position eligibility mismatch:
          </strong>{" "}
          {lineup.overflow} canonical roster
          {lineup.overflow === 1
            ? " player is"
            : " players are"}{" "}
          shown as BN+ because the current
          position metadata cannot fill every
          active lineup slot without inventing
          eligibility.
        </div>
      ) : null}

      <div className={styles.desktopRoster}>
        <section className={styles.rosterSection}>
          <div className={styles.sectionHeading}>
            <h2>Hitters</h2>
            <span>
              Deterministic best-rank lineup
            </span>
          </div>

          <HitterTable rows={lineup.hitters} />
        </section>

        <section className={styles.rosterSection}>
          <div className={styles.sectionHeading}>
            <h2>Pitchers</h2>
            <span>
              Deterministic best-rank lineup
            </span>
          </div>

          <PitcherTable rows={lineup.pitchers} />
        </section>
      </div>

      <div className={styles.mobileRoster}>
        <MobileSection
          title="Hitters"
          rows={lineup.hitters}
          pitcher={false}
        />

        <MobileSection
          title="Pitchers"
          rows={lineup.pitchers}
          pitcher
        />
      </div>
    </AppShell>
  );
}