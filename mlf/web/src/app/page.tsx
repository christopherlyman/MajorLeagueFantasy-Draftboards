import type { CSSProperties } from "react";

import styles from "./page.module.css";

import {
  loadBoard,
  type BoardRow,
} from "@/lib/board";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const runtime = "nodejs";

const DRAFT_KEY =
  process.env.DRAFTBOARD_DRAFT_KEY ??
  "mlf_2026_preseason";

const TEAM_COUNT = 16;
const SCROLL_COLUMN_PX = 106;
const GRID_GAP_PX = 3;

type PositionKey =
  | "p"
  | "of"
  | "c"
  | "1b"
  | "2b"
  | "3b"
  | "ss"
  | "util"
  | "unknown";

type PositionDisplay = {
  key: PositionKey;
  label: string;
};

function splitName(
  fullName: string,
): [string, string] {
  const parts = fullName
    .trim()
    .split(/\s+/);

  if (parts.length <= 1) {
    return [fullName.trim(), ""];
  }

  return [
    parts[0],
    parts.slice(1).join(" "),
  ];
}

function baseballPosition(
  raw: string | null,
): PositionDisplay {
  const value = (raw ?? "")
    .toUpperCase()
    .trim();

  const token = value
    .replace("/", ",")
    .split(",", 1)[0]
    .trim();

  if (["P", "SP", "RP"].includes(token)) {
    return {
      key: "p",
      label: token,
    };
  }

  if (["OF", "LF", "CF", "RF"].includes(token)) {
    return {
      key: "of",
      label: token,
    };
  }

  if (token === "C") {
    return {
      key: "c",
      label: "C",
    };
  }

  if (token === "1B") {
    return {
      key: "1b",
      label: "1B",
    };
  }

  if (token === "2B") {
    return {
      key: "2b",
      label: "2B",
    };
  }

  if (token === "3B") {
    return {
      key: "3b",
      label: "3B",
    };
  }

  if (token === "SS") {
    return {
      key: "ss",
      label: "SS",
    };
  }

  if (
    ["UTIL", "DH", "MI", "CI"].includes(token)
  ) {
    return {
      key: "util",
      label: token,
    };
  }

  return {
    key: "unknown",
    label: "",
  };
}

function positionClass(
  key: PositionKey,
): string {
  switch (key) {
    case "p":
      return styles.posP;
    case "of":
      return styles.posOf;
    case "c":
      return styles.posC;
    case "1b":
      return styles.pos1b;
    case "2b":
      return styles.pos2b;
    case "3b":
      return styles.pos3b;
    case "ss":
      return styles.posSs;
    case "util":
      return styles.posUtil;
    default:
      return styles.posUnknown;
  }
}

function displayBadge(
  row: BoardRow,
): string {
  const kind = String(
    row.pick_kind ?? "",
  ).trim();

  if (kind === "CONTRACT_PLACEHOLDER") {
    if (row.contract_years_remaining == null) {
      return "C";
    }

    return `C${row.contract_years_remaining}`;
  }

  if (kind === "PT_PLACEHOLDER") {
    return "PT";
  }

  return kind;
}

function badgeToneClass(
  row: BoardRow,
): string {
  const kind = String(
    row.pick_kind ?? "",
  )
    .trim()
    .toUpperCase();

  switch (kind) {
    case "POACH":
      return styles.badgePoach;

    case "QO":
      return styles.badgeQo;

    case "FA":
      return styles.badgeFa;

    case "PT":
    case "PT_PLACEHOLDER":
      return styles.badgePt;

    case "CONTRACT":
    case "CONTRACT_PLACEHOLDER":
      return styles.badgeContract;

    default:
      return styles.badgeNeutral;
  }
}
function groupByRound(
  rows: BoardRow[],
): BoardRow[][] {
  const byRound = new Map<
    number,
    BoardRow[]
  >();

  for (const row of rows) {
    const existing =
      byRound.get(row.round_number) ?? [];

    existing.push(row);
    byRound.set(
      row.round_number,
      existing,
    );
  }

  return [...byRound.entries()]
    .sort(([a], [b]) => a - b)
    .map(([, roundRows]) =>
      [...roundRows].sort(
        (a, b) =>
          a.slot_number - b.slot_number,
      ),
    );
}

function MobileBoard({
  rounds,
}: {
  rounds: BoardRow[][];
}) {
  return (
    <div className={styles.mobileBoard}>
      {rounds.map((roundRows) => {
        const roundNumber =
          roundRows[0]?.round_number ?? 0;

        const roundLabel =
          roundRows[0]?.round_label ??
          `R${roundNumber}`;

        return (
          <section
            key={roundNumber}
            className={styles.mobileRound}
          >
            <div className={styles.mobileRoundHeader}>
              <span>{roundLabel}</span>

              <span>
                {roundRows.length} picks
              </span>
            </div>

            <div className={styles.mobilePickList}>
              {roundRows.map((row) => {
                const name =
                  row.selected_player_name ??
                  "";

                const position =
                  baseballPosition(
                    row.selected_primary_position,
                  );

                const badge =
                  displayBadge(row);

                const pickLabel =
                  `${row.round_label}.${row.slot_number}`;

                const owner =
                  row.ownership_note ??
                  row.current_owner_team_name ??
                  "";

                return (
                  <article
                    key={String(row.pick_id)}
                    data-mobile-pick-id={String(
                      row.pick_id,
                    )}
                    className={
                      `${styles.mobilePick} ` +
                      positionClass(
                        position.key,
                      )
                    }
                  >
                    <div
                      className={
                        styles.mobilePickTop
                      }
                    >
                      <span
                        className={
                          styles.mobilePickNumber
                        }
                      >
                        {pickLabel}
                      </span>

                      <span
                        className={
                          styles.mobileTeam
                        }
                      >
                        {row.column_team_name}
                      </span>
                    </div>

                    <div
                      className={
                        styles.mobilePickMain
                      }
                    >
                      <div
                        className={
                          styles.mobilePosition
                        }
                      >
                        {position.label ||
                          "—"}
                      </div>

                      <div
                        className={
                          styles.mobilePlayerName
                        }
                      >
                        {name || "Open pick"}
                      </div>

                      {badge ? (
                        <div
                          className={
                            `${styles.mobileAction} ${badgeToneClass(row)}`
                          }
                        >
                          {badge}
                        </div>
                      ) : null}
                    </div>

                    {row.traded_flag &&
                    owner ? (
                      <div
                        className={
                          styles.mobileOwner
                        }
                      >
                        Traded · {owner}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
export default async function Home() {
  const rows = await loadBoard(
    DRAFT_KEY,
  );

  if (rows.length !== 400) {
    throw new Error(
      `Expected 400 MLF draft slots; received ${rows.length}.`,
    );
  }

  const firstRound = Math.min(
    ...rows.map(
      (row) => row.round_number,
    ),
  );

  const headers = rows
    .filter(
      (row) =>
        row.round_number === firstRound,
    )
    .sort(
      (a, b) =>
        a.slot_number - b.slot_number,
    );

  if (headers.length !== TEAM_COUNT) {
    throw new Error(
      `Expected ${TEAM_COUNT} MLF teams; received ${headers.length}.`,
    );
  }

  const rounds = groupByRound(rows);

  for (const round of rounds) {
    if (round.length !== TEAM_COUNT) {
      throw new Error(
        `Expected ${TEAM_COUNT} picks in round ${round[0]?.round_number}; received ${round.length}.`,
      );
    }
  }

  const scrollBoardMinWidth =
    TEAM_COUNT * SCROLL_COLUMN_PX +
    (TEAM_COUNT - 1) * GRID_GAP_PX;

  const boardStyle = {
    "--team-count": TEAM_COUNT,
    "--scroll-board-min-width":
      `${scrollBoardMinWidth}px`,
  } as CSSProperties;

  return (
    <main className={styles.appShell}>
      <header className={styles.appHeader}>
        <div className={styles.brand}>
          <div className={styles.brandMark}>
            MLF
          </div>

          <div>
            <div className={styles.brandTitle}>
              Major League Fantasy
            </div>

            <div className={styles.brandSubtitle}>
              Draft Board
            </div>
          </div>
        </div>

        <div className={styles.previewBadge}>
          Next.js Preview
        </div>
      </header>

      <nav
        className={styles.tabs}
        aria-label="MLF sections"
      >
        <div
          className={`${styles.tab} ${styles.activeTab}`}
        >
          Draft Board
        </div>

        <div className={styles.tabMuted}>
          Available Players
        </div>

        <div className={styles.tabMuted}>
          Teams
        </div>

        <div className={styles.tabMuted}>
          QOs
        </div>

        <div className={styles.tabMuted}>
          Draft Lottery
        </div>

        <div className={styles.tabMuted}>
          Pick Tracker
        </div>

        <div className={styles.tabMuted}>
          Draft Statistics
        </div>
      </nav>

      <section className={styles.content}>
        <div className={styles.titleRow}>
          <div>
            <h1 className={styles.title}>
              Draft Board
            </h1>

            <p className={styles.subtitle}>
              {"2026 MLF \u00b7 16 teams \u00b7 25 rounds"}
            </p>
          </div>

          <div className={styles.readOnly}>
            Read-only preview
          </div>
        </div>

        <div
          className={
            `${styles.boardScroller} ${styles.desktopBoard}`
          }
        >
          <div
            className={styles.boardCanvas}
            style={boardStyle}
          >
            <div
              className={styles.teamGrid}
            >
              {headers.map((row) => (
                <div
                  key={row.slot_number}
                  className={styles.teamHeader}
                  title={row.column_team_name}
                >
                  {row.column_team_name}
                </div>
              ))}
            </div>

            <div className={styles.roundStack}>
              {rounds.map(
                (roundRows) => (
                  <div
                    key={
                      roundRows[0]
                        .round_number
                    }
                    className={styles.roundGrid}
                  >
                    {roundRows.map(
                      (row) => {
                        const name =
                          row.selected_player_name ??
                          "";

                        const [
                          firstName,
                          lastName,
                        ] = splitName(name);

                        const position =
                          baseballPosition(
                            row.selected_primary_position,
                          );

                        const badge =
                          displayBadge(row);

                        const pickLabel =
                          `${row.round_label}.${row.slot_number}`;

                        const owner =
                          row.ownership_note ??
                          row.current_owner_team_name ??
                          "";

                        const topLeft =
                          row.traded_flag
                            ? position.label
                              ? `TRADE \u00b7 ${position.label}`
                              : "TRADE"
                            : position.label;

                        return (
                          <article
                            key={String(
                              row.pick_id,
                            )}
                            data-pick-id={String(
                              row.pick_id,
                            )}
                            data-round={
                              row.round_number
                            }
                            data-slot={
                              row.slot_number
                            }
                            className={
                              `${styles.card} ` +
                              positionClass(
                                position.key,
                              )
                            }
                            title={
                              name ||
                              pickLabel
                            }
                          >
                            {topLeft ? (
                              <div
                                className={
                                  styles.topLeft
                                }
                              >
                                {topLeft}
                              </div>
                            ) : null}

                            <div
                              className={
                                styles.pickLabel
                              }
                            >
                              {pickLabel}
                            </div>

                            <div
                              className={
                                styles.player
                              }
                            >
                              <div
                                className={
                                  styles.firstName
                                }
                              >
                                {firstName}
                              </div>

                              <div
                                className={
                                  styles.lastName
                                }
                              >
                                {lastName}
                              </div>
                            </div>

                            {badge ? (
                              <div
                                className={
                                  `${styles.actionBadge} ${badgeToneClass(row)}`
                                }
                              >
                                {badge}
                              </div>
                            ) : null}

                            {row.traded_flag &&
                            owner ? (
                              <div
                                className={
                                  styles.owner
                                }
                                title={owner}
                              >
                                {owner}
                              </div>
                            ) : null}
                          </article>
                        );
                      },
                    )}
                  </div>
                ),
              )}
            </div>
          </div>
        </div>

        <MobileBoard rounds={rounds} />
      </section>
    </main>
  );
}