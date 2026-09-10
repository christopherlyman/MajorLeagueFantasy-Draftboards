import Link from "next/link";

import { AppShell } from "../../components/AppShell";
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
      <div className={styles.summary}>
        <div>
          <strong>{filtered.length}</strong>
          <span> shown</span>
        </div>

        <div>
          <strong>{rows.length}</strong>
          <span> pool</span>
        </div>

        <div>
          <strong>{draftedCount}</strong>
          <span> drafted</span>
        </div>

        <div>
          <strong>{qoCount}</strong>
          <span> QOs</span>
        </div>

        <div>
          <strong>{ptCount}</strong>
          <span> PT</span>
        </div>

        <div>
          <strong>{contractCount}</strong>
          <span> contracts</span>
        </div>
      </div>

      <form
        method="get"
        className={styles.filters}
      >
        <label className={styles.field}>
          <span>Search</span>
          <input
            type="search"
            name="q"
            defaultValue={first(params.q)}
            placeholder="Type a player name..."
          />
        </label>

        <label className={styles.field}>
          <span>Position</span>
          <select
            name="pos"
            multiple
            defaultValue={Array.from(
              selectedPositions,
            )}
          >
            {positions.map((position) => (
              <option
                key={position}
                value={position}
              >
                {position}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>Sort by</span>
          <select
            name="sort"
            defaultValue={sortKey}
          >
            {SORT_OPTIONS.map((option) => (
              <option
                key={option.key}
                value={option.key}
              >
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <div className={styles.toggles}>
          <label>
            <input
              type="checkbox"
              name="all"
              value="1"
              defaultChecked={showAll}
            />
            Show all players
          </label>

          <label>
            <input
              type="checkbox"
              name="qo"
              value="1"
              defaultChecked={showQo}
            />
            Only QOs
          </label>

          <label>
            <input
              type="checkbox"
              name="poach"
              value="1"
              defaultChecked={showPoach}
            />
            Only poach-eligible
          </label>

          <label>
            <input
              type="checkbox"
              name="pt"
              value="1"
              defaultChecked={showPt}
            />
            Only PT
          </label>

          <label>
            <input
              type="checkbox"
              name="contracts"
              value="1"
              defaultChecked={showContracts}
            />
            Only contracts
          </label>

          <label>
            <input
              type="checkbox"
              name="desc"
              value="1"
              defaultChecked={descending}
            />
            Descending
          </label>
        </div>

        <div className={styles.actions}>
          <button type="submit">
            Apply
          </button>

          <Link href="/available-players">
            Reset
          </Link>
        </div>
      </form>

      <div className={styles.tableScroller}>
        <table className={styles.playerTable}>
          <thead>
            <tr>
              <th>Player Name</th>
              <th>Team</th>
              <th>Position</th>
              <th>Contract/PT/QO</th>
              <th>Team Name</th>
              <th>Draft Pick</th>
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
    </AppShell>
  );
}