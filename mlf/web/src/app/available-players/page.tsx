import { AppShell } from "../../components/AppShell";
import { AvailablePlayersFilters } from "../../components/AvailablePlayersFilters";
import { DraftActionController } from "../../components/DraftActionController";
import { loadDraftRuntimeContext } from "../../lib/draftRuntime";
import {
  loadAvailablePlayers,
  type AvailablePlayerRow,
} from "../../lib/availablePlayers";

import styles from "./page.module.css";

type Params = Record<
  string,
  string | string[] | undefined
>;

type SortKey =
  | "rank"
  | "rostered"
  | "draft"
  | "status"
  | "contractYears"
  | "name"
  | "team"
  | "position"
  | "r"
  | "hr"
  | "rbi"
  | "sb"
  | "bb"
  | "kHit"
  | "avg"
  | "ip"
  | "w"
  | "kPit"
  | "tb"
  | "era"
  | "whip"
  | "qs"
  | "svH";

const SORT_OPTIONS: Array<{
  key: SortKey;
  label: string;
}> = [
  { key: "rank", label: "Current Rank" },
  { key: "rostered", label: "% Ros" },
  { key: "draft", label: "Draft Pick" },
  { key: "status", label: "Contract/PT/QO" },
  { key: "contractYears", label: "Contract Years" },
  { key: "name", label: "Player Name" },
  { key: "team", label: "Team" },
  { key: "position", label: "Position" },
  { key: "r", label: "R" },
  { key: "hr", label: "HR" },
  { key: "rbi", label: "RBI" },
  { key: "sb", label: "SB" },
  { key: "bb", label: "BB" },
  { key: "kHit", label: "K (H)" },
  { key: "avg", label: "AVG" },
  { key: "ip", label: "IP" },
  { key: "w", label: "W" },
  { key: "kPit", label: "K (P)" },
  { key: "tb", label: "TB" },
  { key: "era", label: "ERA" },
  { key: "whip", label: "WHIP" },
  { key: "qs", label: "QS" },
  { key: "svH", label: "SV+H" },
];

const POSITION_ORDER = [
  "C",
  "1B",
  "2B",
  "3B",
  "SS",
  "OF",
  "SP",
  "RP",
  "P",
  "UTIL",
];

function first(
  value: string | string[] | undefined,
): string {
  if (Array.isArray(value)) {
    return value[0] ?? "";
  }

  return value ?? "";
}

function many(
  value: string | string[] | undefined,
): string[] {
  if (Array.isArray(value)) {
    return value;
  }

  return value
    ? [value]
    : [];
}

function enabled(
  value: string | string[] | undefined,
): boolean {
  return first(value) === "1";
}

function sortValue(
  row: AvailablePlayerRow,
  key: SortKey,
): string | number | null {
  switch (key) {
    case "rank":
      return row.currentRank;
    case "rostered":
      return row.percentRostered;
    case "draft":
      return row.draftPick || null;
    case "status":
      return row.status || null;
    case "contractYears":
      return row.contractYears;
    case "name":
      return row.name;
    case "team":
      return row.mlbTeam;
    case "position":
      return row.positions.join("/");
    case "r":
      return row.r;
    case "hr":
      return row.hr;
    case "rbi":
      return row.rbi;
    case "sb":
      return row.sb;
    case "bb":
      return row.bb;
    case "kHit":
      return row.kHit;
    case "avg":
      return row.avg;
    case "ip":
      return row.ip;
    case "w":
      return row.w;
    case "kPit":
      return row.kPit;
    case "tb":
      return row.tb;
    case "era":
      return row.era;
    case "whip":
      return row.whip;
    case "qs":
      return row.qs;
    case "svH":
      return row.svH;
  }
}

function compareValues(
  left: string | number | null,
  right: string | number | null,
  descending: boolean,
): number {
  const leftBlank =
    left === null ||
    left === "";

  const rightBlank =
    right === null ||
    right === "";

  if (leftBlank && rightBlank) {
    return 0;
  }

  if (leftBlank) {
    return 1;
  }

  if (rightBlank) {
    return -1;
  }

  let result: number;

  if (
    typeof left === "number" &&
    typeof right === "number"
  ) {
    result = left - right;
  } else {
    result = String(left).localeCompare(
      String(right),
      undefined,
      {
        numeric: true,
        sensitivity: "base",
      },
    );
  }

  return descending
    ? -result
    : result;
}

function formatInt(
  value: number | null,
): string {
  return value === null
    ? ""
    : String(Math.round(value));
}

function formatAverage(
  value: number | null,
): string {
  if (value === null) {
    return "";
  }

  const formatted =
    value.toFixed(3);

  return formatted.startsWith("0")
    ? formatted.slice(1)
    : formatted;
}

function formatTwo(
  value: number | null,
): string {
  return value === null
    ? ""
    : value.toFixed(2);
}

function formatOne(
  value: number | null,
): string {
  return value === null
    ? ""
    : value.toFixed(1);
}

function statusClass(
  row: AvailablePlayerRow,
): string {
  if (row.isPt) {
    return styles.statusPt;
  }

  if (row.isContract) {
    return styles.statusContract;
  }

  if (row.qoLevel !== null) {
    return styles.statusQo;
  }

  return styles.statusEmpty;
}

function isPitcherRow(
  row: AvailablePlayerRow,
): boolean {
  const pitcherPositions =
    new Set(["P", "SP", "RP"]);

  const hitterPositions =
    new Set([
      "C",
      "1B",
      "2B",
      "3B",
      "SS",
      "OF",
      "UTIL",
    ]);

  const hasPitcher =
    row.positions.some((position) =>
      pitcherPositions.has(position),
    );

  const hasHitter =
    row.positions.some((position) =>
      hitterPositions.has(position),
    );

  return hasPitcher && !hasHitter;
}

function mobileStats(
  row: AvailablePlayerRow,
): Array<[string, string]> {
  if (isPitcherRow(row)) {
    return [
      ["IP", formatOne(row.ip)],
      ["W", formatInt(row.w)],
      ["K", formatInt(row.kPit)],
      ["ERA", formatTwo(row.era)],
      ["WHIP", formatTwo(row.whip)],
      ["QS", formatInt(row.qs)],
      ["SV+H", formatInt(row.svH)],
    ];
  }

  return [
    ["H/AB", row.hAb],
    ["R", formatInt(row.r)],
    ["HR", formatInt(row.hr)],
    ["RBI", formatInt(row.rbi)],
    ["SB", formatInt(row.sb)],
    ["BB", formatInt(row.bb)],
    ["AVG", formatAverage(row.avg)],
  ];
}
export default async function AvailablePlayersPage({
  searchParams,
}: {
  searchParams: Promise<Params>;
}) {
  const params =
    await searchParams;

  const draftKey =
    process.env.DRAFTBOARD_DRAFT_KEY ??
    "mlf_2026_preseason";

  const rows =
    await loadAvailablePlayers(draftKey);

  const draftRuntime =
    await loadDraftRuntimeContext(draftKey);

  const query =
    first(params.q)
      .trim()
      .toLocaleLowerCase();

  const selectedPositions =
    new Set(
      many(params.pos)
        .map((value) =>
          value.toUpperCase(),
        ),
    );

  const showAll =
    enabled(params.all);

  const showQo =
    enabled(params.qo);

  const showPoach =
    enabled(params.poach);

  const showPt =
    enabled(params.pt);

  const showContracts =
    enabled(params.contracts);

  const requestedSort =
    first(params.sort) as SortKey;

  const sortKey =
    SORT_OPTIONS.some(
      (option) =>
        option.key === requestedSort,
    )
      ? requestedSort
      : "rank";

  const descending =
    enabled(params.desc);

  const filtered =
    rows.filter((row) => {
      const qoMatch =
        row.qoLevel !== null;

      const specialVisibility =
        (showQo && qoMatch) ||
        (showPt && row.isPt) ||
        (showContracts && row.isContract);

      if (
        !showAll &&
        !specialVisibility &&
        (
          row.isDrafted ||
          row.isPt ||
          row.isContract
        )
      ) {
        return false;
      }

      if (
        query &&
        !row.name
          .toLocaleLowerCase()
          .includes(query)
      ) {
        return false;
      }

      if (
        selectedPositions.size > 0 &&
        !row.positions.some(
          (position) =>
            selectedPositions.has(position),
        )
      ) {
        return false;
      }

      if (
        showQo &&
        !qoMatch
      ) {
        return false;
      }

      if (
        showPoach &&
        !row.isPoachable
      ) {
        return false;
      }

      if (
        showPt &&
        !row.isPt
      ) {
        return false;
      }

      if (
        showContracts &&
        !row.isContract
      ) {
        return false;
      }

      return true;
    });

  filtered.sort((left, right) => {
    const result =
      compareValues(
        sortValue(left, sortKey),
        sortValue(right, sortKey),
        descending,
      );

    if (result !== 0) {
      return result;
    }

    return left.name.localeCompare(
      right.name,
      undefined,
      {
        sensitivity: "base",
      },
    );
  });

  const positions =
    Array.from(
      new Set(
        rows.flatMap(
          (row) => row.positions,
        ),
      ),
    ).sort((left, right) => {
      const li =
        POSITION_ORDER.indexOf(left);

      const ri =
        POSITION_ORDER.indexOf(right);

      const lv =
        li === -1
          ? 999
          : li;

      const rv =
        ri === -1
          ? 999
          : ri;

      return lv !== rv
        ? lv - rv
        : left.localeCompare(right);
    });

  const draftedCount =
    rows.filter(
      (row) => row.isDrafted,
    ).length;

  const qoCount =
    rows.filter(
      (row) => row.qoLevel !== null,
    ).length;

  const ptCount =
    rows.filter(
      (row) => row.isPt,
    ).length;

  const contractCount =
    rows.filter(
      (row) => row.isContract,
    ).length;


  return (
    <AppShell
      activePath="/available-players"
      title="Available Players"
      subtitle={
        "2026 MLF \u00b7 read-only player pool"
      }
    >
      <section
        className={styles.summary}
        aria-label="Player pool summary"
      >
        <div className={styles.summaryHeading}>
          Player Pool
        </div>

        <div className={styles.summaryGrid}>
          <div className={styles.summaryMetric}>
            <strong>{filtered.length}</strong>
            <span>Shown</span>
          </div>

          <div className={styles.summaryMetric}>
            <strong>{rows.length}</strong>
            <span>Pool</span>
          </div>

          <div className={styles.summaryMetric}>
            <strong>{draftedCount}</strong>
            <span>Drafted</span>
          </div>

          <div className={styles.summaryMetric}>
            <strong>{qoCount}</strong>
            <span>QOs</span>
          </div>

          <div className={styles.summaryMetric}>
            <strong>{ptCount}</strong>
            <span>PT</span>
          </div>

          <div className={styles.summaryMetric}>
            <strong>{contractCount}</strong>
            <span>Contracts</span>
          </div>
        </div>
      </section>

      <AvailablePlayersFilters
        query={first(params.q)}
        positions={positions}
        selectedPositions={
          Array.from(selectedPositions)
        }
        sortKey={sortKey}
        sortOptions={SORT_OPTIONS}
        descending={descending}
        showAll={showAll}
        showQo={showQo}
        showPoach={showPoach}
        showPt={showPt}
        showContracts={showContracts}
      />

      {draftRuntime.currentPickId
      && draftRuntime.currentOwnerTeamKey ? (
        <DraftActionController
          draftKey={draftKey}
          currentPickId={
            draftRuntime.currentPickId
          }
          currentOwnerTeamKey={
            draftRuntime.currentOwnerTeamKey
          }
        />
      ) : null}
      <div className={`${styles.tableScroller} ${styles.desktopTable}`}>
        <table className={styles.playerTable}>
          <thead>
            <tr>
              <th>Player Name</th>
              <th>Team</th>
              <th>Position</th>
              <th>Contract/PT/QO</th>
              <th>Team Name</th>
              <th>Draft Pick</th>
              {draftRuntime.currentPickId ? (
                <th>Action</th>
              ) : null}
              <th>Current Rank</th>
              <th>% Ros</th>
              <th>H/AB</th>
              <th>R</th>
              <th>HR</th>
              <th>RBI</th>
              <th>SB</th>
              <th>BB</th>
              <th>K (H)</th>
              <th>AVG</th>
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
            {filtered.map((row) => (
              <tr
                key={row.playerKey}
                data-player-key={row.playerKey}
              >
                <td className={styles.playerName}>
                  {row.name}
                </td>

                <td>{row.mlbTeam}</td>

                <td>
                  {row.positions.join("/")}
                </td>

                <td>
                  {row.status ? (
                    <span
                      className={
                        `${styles.status} ${statusClass(row)}`
                      }
                    >
                      {row.status}
                    </span>
                  ) : null}
                </td>

                <td>{row.teamName}</td>
                <td>{row.draftPick}</td>
                {draftRuntime.currentPickId ? (
                  <td>
                    {!row.draftPick
                    && !row.isPt
                    && !row.isContract ? (
                      <button
                        type="button"
                        className={styles.draftActionButton}
                        data-mlf-draft-player={row.playerKey}
                        data-mlf-draft-player-name={row.name}
                        disabled
                      >
                        Draft
                      </button>
                    ) : null}
                  </td>
                ) : null}

                <td>
                  {formatInt(row.currentRank)}
                </td>

                <td>
                  {formatInt(row.percentRostered)}
                </td>

                <td>{row.hAb}</td>
                <td>{formatInt(row.r)}</td>
                <td>{formatInt(row.hr)}</td>
                <td>{formatInt(row.rbi)}</td>
                <td>{formatInt(row.sb)}</td>
                <td>{formatInt(row.bb)}</td>
                <td>{formatInt(row.kHit)}</td>
                <td>{formatAverage(row.avg)}</td>
                <td>{formatOne(row.ip)}</td>
                <td>{formatInt(row.w)}</td>
                <td>{formatInt(row.kPit)}</td>
                <td>{formatInt(row.tb)}</td>
                <td>{formatTwo(row.era)}</td>
                <td>{formatTwo(row.whip)}</td>
                <td>{formatInt(row.qs)}</td>
                <td>{formatInt(row.svH)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={styles.mobilePlayerList}>
        {filtered.map((row) => (
          <article
            key={row.playerKey}
            className={styles.mobilePlayerCard}
            data-mobile-player-key={row.playerKey}
          >
            <div className={styles.mobileCompactHeader}>
              <div className={styles.mobileIdentity}>
                <div className={styles.mobilePlayerName}>
                  {row.name}
                </div>

                <div className={styles.mobileMeta}>
                  <span>
                    {row.mlbTeam || "\u2014"}
                  </span>

                  <span className={styles.metaDot}>
                    {"\u00b7"}
                  </span>

                  <span>
                    {row.positions.join("/") || "\u2014"}
                  </span>

                  {row.status ? (
                    <span
                      className={
                        `${styles.status} ${statusClass(row)}`
                      }
                    >
                      {row.status}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className={styles.mobileTopMetric}>
                <span>Rank</span>
                <strong>
                  {formatInt(row.currentRank) || "\u2014"}
                </strong>
              </div>

              <div className={styles.mobileTopMetric}>
                <span>% Ros</span>
                <strong>
                  {formatInt(row.percentRostered) || "\u2014"}
                </strong>
              </div>
            </div>

            {draftRuntime.currentPickId
            && !row.draftPick
            && !row.isPt
            && !row.isContract ? (
              <button
                type="button"
                className={
                  `${styles.draftActionButton} ${styles.mobileDraftAction}`
                }
                data-mlf-draft-player={row.playerKey}
                data-mlf-draft-player-name={row.name}
                disabled
              >
                Draft {row.name}
              </button>
            ) : null}

            {row.teamName || row.draftPick ? (
              <div className={styles.mobileOwnershipStrip}>
                {row.teamName ? (
                  <span>
                    <small>MLF Team</small>
                    <strong>{row.teamName}</strong>
                  </span>
                ) : null}

                {row.draftPick ? (
                  <span>
                    <small>Draft Pick</small>
                    <strong>{row.draftPick}</strong>
                  </span>
                ) : null}
              </div>
            ) : null}

            <div className={styles.mobileStatGrid}>
              {mobileStats(row).map(
                ([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>
                      {value || "\u2014"}
                    </strong>
                  </div>
                ),
              )}
            </div>
          </article>
        ))}
      </div>
    </AppShell>
  );
}