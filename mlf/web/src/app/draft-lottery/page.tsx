import { AppShell } from "../../components/AppShell";
import {
  loadDraftLottery,
  type DraftOrderRow,
} from "../../lib/draftLottery";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const DRAFT_KEY =
  process.env.DRAFTBOARD_DRAFT_KEY ??
  "mlf_2026_preseason";

function DesktopRevealHero() {
  return (
    <section className={styles.revealHero}>
      <div className={styles.revealMark}>
        MLF
      </div>

      <div className={styles.revealCopy}>
        <span className={styles.revealEyebrow}>
          Official Draft Order Reveal
        </span>

        <h2>
          Draft Lottery <strong>2026</strong>
        </h2>
      </div>
    </section>
  );
}

function DesktopOrder({
  order,
}: {
  order: DraftOrderRow[];
}) {
  const columns = [
    order.slice(0, 8),
    order.slice(8, 16),
  ];

  return (
    <div className={styles.desktopOrder}>
      {columns.map((rows, columnIndex) => (
        <div
          key={columnIndex}
          className={styles.orderColumn}
        >
          {rows.map((row) => (
            <article
              key={row.teamKey}
              className={styles.desktopOrderCard}
              data-lottery-team-key={row.teamKey}
            >
              <div className={styles.desktopPickRail}>
                <span>Pick</span>
                <strong>{row.pickNumber}</strong>
              </div>

              <div className={styles.desktopTeamIdentity}>
                <strong>{row.teamName}</strong>

                <span>
                  {row.ownerName || "Owner unavailable"}
                </span>
              </div>
            </article>
          ))}
        </div>
      ))}
    </div>
  );
}

function MobileOrder({
  order,
}: {
  order: DraftOrderRow[];
}) {
  return (
    <div className={styles.mobileOrder}>
      {order.map((row) => (
        <article
          key={row.teamKey}
          className={styles.orderCard}
          data-lottery-mobile-team-key={
            row.teamKey
          }
        >
          <div className={styles.mobilePick}>
            <span>Pick</span>
            <strong>{row.pickNumber}</strong>
          </div>

          <div className={styles.mobileIdentity}>
            <strong>{row.teamName}</strong>

            <span>
              {row.ownerName
                ? `Owner: ${row.ownerName}`
                : "Owner unavailable"}
            </span>
          </div>
        </article>
      ))}
    </div>
  );
}

export default async function DraftLotteryPage() {
  const snapshot =
    await loadDraftLottery(DRAFT_KEY);

  return (
    <AppShell
      title="Draft Lottery"
      subtitle={"2026 MLF \u00b7 Final Draft Order"}
      activePath="/draft-lottery"
    >
      <DesktopRevealHero />

      <div className={styles.summaryBand}>
        <div>
          <strong>{snapshot.managerCount}</strong>
          <span>Teams</span>
        </div>

        <div>
          <strong>{snapshot.roundsTotal}</strong>
          <span>Rounds</span>
        </div>

        <div>
          <strong>
            Round {snapshot.firstStandardRound}
          </strong>
          <span>Standard draft begins</span>
        </div>

        <div>
          <strong>Straight</strong>
          <span>Draft order</span>
        </div>
      </div>

      <section className={styles.orderSection}>
        <div className={styles.sectionHeading}>
          <div>
            <h2>Final Draft Order</h2>
            <p>
              Canonical slot order used throughout the
              standard portion of the completed 2026 draft.
            </p>
          </div>

          <div className={styles.statusBadge}>
            Final
          </div>
        </div>

        <DesktopOrder
          order={snapshot.order}
        />

        <MobileOrder
          order={snapshot.order}
        />
      </section>

      <div className={styles.note}>
        Draft-order changes are commissioner-only and are
        intentionally excluded from this read-only preview.
      </div>
    </AppShell>
  );
}