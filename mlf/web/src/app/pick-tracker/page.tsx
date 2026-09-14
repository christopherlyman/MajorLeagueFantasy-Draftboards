import { AppShell } from "../../components/AppShell";
import {
  loadPickTracker,
  type PickKind,
  type PickTrackerRow,
} from "../../lib/pickTracker";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const DRAFT_KEY =
  process.env.DRAFTBOARD_DRAFT_KEY ??
  "mlf_2026_preseason";


function KindBadge({
  kind,
}: {
  kind: PickKind;
}) {
  return (
    <span
      className={`${styles.kindBadge} ${
        kind === "QO"
          ? styles.kindQo
          : kind === "POACH"
            ? styles.kindPoach
            : styles.kindFa
      }`}
    >
      {kind}
    </span>
  );
}

function PlayerIdentity({
  row,
}: {
  row: PickTrackerRow;
}) {
  const detail =
    [row.mlbTeam, row.position]
      .filter(Boolean)
      .join(" \u00b7 ");

  return (
    <div className={styles.playerIdentity}>
      <strong>{row.playerName}</strong>
      <span>{detail || "\u2014"}</span>
    </div>
  );
}

function DesktopTracker({
  rows,
}: {
  rows: PickTrackerRow[];
}) {
  return (
    <div className={styles.desktopTableWrap}>
      <table className={styles.trackerTable}>
        <thead>
          <tr>
            <th>Overall</th>
            <th>Round</th>
            <th>Pick</th>
            <th>Team</th>
            <th>Owner</th>
            <th>Player</th>
            <th>Type</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((row) => (
            <tr
              key={row.pickId}
              data-tracker-row={row.pickId}
              data-tracker-player-key={row.playerKey}
              data-tracker-pick-kind={row.pickKind}
            >
              <td className={styles.overallCell}>
                {row.overallNumber}
              </td>

              <td>
                <span className={styles.roundBadge}>
                  {row.roundLabel}
                </span>
              </td>

              <td className={styles.pickCell}>
                {row.slotNumber}
              </td>

              <td className={styles.teamCell}>
                {row.selectingTeamName}
              </td>

              <td className={styles.ownerCell}>
                {row.ownerName || "\u2014"}
              </td>

              <td>
                <PlayerIdentity row={row} />
              </td>

              <td>
                <KindBadge kind={row.pickKind} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MobileTracker({
  rows,
}: {
  rows: PickTrackerRow[];
}) {
  return (
    <div className={styles.mobileCards}>
      {rows.map((row) => (
        <article
          key={row.pickId}
          className={styles.pickCard}
          data-tracker-mobile-row={row.pickId}
          data-tracker-mobile-player-key={row.playerKey}
          data-tracker-mobile-pick-kind={row.pickKind}
        >
          <div className={styles.cardIndex}>
            <span>Overall</span>
            <strong>{row.overallNumber}</strong>
          </div>

          <div className={styles.cardMain}>
            <div className={styles.cardTop}>
              <div>
                <span className={styles.cardRound}>
                  {row.roundLabel}
                  {" \u00b7 "}
                  Pick {row.slotNumber}
                </span>

                <strong className={styles.cardPlayer}>
                  {row.playerName}
                </strong>

                <span className={styles.cardPlayerMeta}>
                  {[row.mlbTeam, row.position]
                    .filter(Boolean)
                    .join(" \u00b7 ") || "\u2014"}
                </span>
              </div>

              <KindBadge kind={row.pickKind} />
            </div>

            <div className={styles.cardTeam}>
              <strong>{row.selectingTeamName}</strong>
              <span>
                {row.ownerName
                  ? `Owner: ${row.ownerName}`
                  : "Owner unavailable"}
              </span>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

export default async function PickTrackerPage() {
  const snapshot =
    await loadPickTracker(DRAFT_KEY);

  return (
    <AppShell
      title="Pick Tracker"
      subtitle={"2026 MLF \u00b7 Draft History"}
      activePath="/pick-tracker"
    >
      <div className={styles.summaryBand}>
        <div>
          <strong>{snapshot.totalPicks}</strong>
          <span>Real picks</span>
        </div>

        <div>
          <strong>{snapshot.faCount}</strong>
          <span>FA</span>
        </div>

        <div>
          <strong>{snapshot.qoCount}</strong>
          <span>QO</span>
        </div>

        <div>
          <strong>{snapshot.poachCount}</strong>
          <span>Poach</span>
        </div>
      </div>

      <section className={styles.historySection}>
        <div className={styles.sectionHeading}>
          <div>
            <h2>Draft History</h2>
            <p>
              Real selections in chronological order.
              Keeper and prospect placeholders are excluded.
            </p>
          </div>

          <div className={styles.totalLabel}>
            {snapshot.totalPicks}
            <span>Picks</span>
          </div>
        </div>

        <DesktopTracker
          rows={snapshot.rows}
        />

        <MobileTracker
          rows={snapshot.rows}
        />
      </section>
    </AppShell>
  );
}