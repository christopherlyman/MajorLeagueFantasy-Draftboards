import { AppShell } from "../../components/AppShell";
import {
  loadQos,
  type QoPlayer,
  type QoTeam,
} from "../../lib/qos";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const DRAFT_KEY =
  process.env.DRAFTBOARD_DRAFT_KEY ??
  "mlf_2026_preseason";

function PlayerIdentity({
  player,
}: {
  player: QoPlayer;
}) {
  return (
    <div className={styles.playerIdentity}>
      <strong>{player.playerName}</strong>

      <span>
        {[
          player.mlbTeam,
          player.position,
        ]
          .filter(Boolean)
          .join(" \u00b7 ")}
      </span>
    </div>
  );
}

function DesktopMatrix({
  teams,
  mode,
}: {
  teams: QoTeam[];
  mode: "current" | "predraft";
}) {
  return (
    <div
      className={styles.tableScroller}
      data-qo-desktop-matrix={mode}
    >
      <table className={styles.qoTable}>
        <thead>
          <tr>
            <th>#</th>
            <th>Team</th>
            <th>Owner</th>
            <th>QO1</th>
            <th>QO2</th>
            <th>QO3</th>
            <th>QO4</th>
            <th>QO5</th>
          </tr>
        </thead>

        <tbody>
          {teams.map((team) => {
            const slots =
              mode === "current"
                ? team.current
                : team.predraft;

            return (
              <tr
                key={team.teamKey}
                data-qo-team-key={team.teamKey}
              >
                <td className={styles.slotNumber}>
                  {team.draftSlot}
                </td>

                <td className={styles.teamName}>
                  {team.teamName}
                </td>

                <td className={styles.ownerName}>
                  {team.ownerName || "\u2014"}
                </td>

                {slots.map((slot) => (
                  <td
                    key={slot.level}
                    className={styles.qoCell}
                    data-qo-level={slot.level}
                    data-qo-player-key={
                      slot.player?.playerKey
                    }
                  >
                    {slot.player ? (
                      <PlayerIdentity
                        player={slot.player}
                      />
                    ) : (
                      <span
                        className={styles.emptyQo}
                      >
                        {"\u2014"}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MobileCards({
  teams,
  mode,
}: {
  teams: QoTeam[];
  mode: "current" | "predraft";
}) {
  return (
    <div
      className={styles.mobileCards}
      data-qo-mobile-cards={mode}
    >
      {teams.map((team) => {
        const slots =
          mode === "current"
            ? team.current
            : team.predraft;

        return (
          <article
            key={team.teamKey}
            className={styles.mobileCard}
            data-qo-mobile-team-key={
              team.teamKey
            }
          >
            <header
              className={styles.mobileCardHeader}
            >
              <div>
                <span>
                  Draft slot {team.draftSlot}
                </span>

                <strong>
                  {team.teamName}
                </strong>
              </div>

              {team.ownerName ? (
                <div
                  className={styles.mobileOwner}
                >
                  {team.ownerName}
                </div>
              ) : null}
            </header>

            <div className={styles.mobileSlots}>
              {slots.map((slot) => (
                <div
                  key={slot.level}
                  className={styles.mobileSlot}
                  data-qo-mobile-level={
                    slot.level
                  }
                  data-qo-mobile-player-key={
                    slot.player?.playerKey
                  }
                >
                  <div
                    className={styles.mobileLevel}
                  >
                    QO{slot.level}
                  </div>

                  {slot.player ? (
                    <PlayerIdentity
                      player={slot.player}
                    />
                  ) : (
                    <span
                      className={styles.mobileEmpty}
                    >
                      No current QO
                    </span>
                  )}
                </div>
              ))}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function QoSection({
  title,
  caption,
  teams,
  mode,
  count,
}: {
  title: string;
  caption: string;
  teams: QoTeam[];
  mode: "current" | "predraft";
  count: number;
}) {
  return (
    <section className={styles.qoSection}>
      <div className={styles.sectionHeading}>
        <div>
          <h2>{title}</h2>
          <p>{caption}</p>
        </div>

        <div className={styles.sectionCount}>
          <strong>{count}</strong>
          <span>QO slots</span>
        </div>
      </div>

      <DesktopMatrix
        teams={teams}
        mode={mode}
      />

      <MobileCards
        teams={teams}
        mode={mode}
      />
    </section>
  );
}

export default async function QosPage() {
  const snapshot =
    await loadQos(DRAFT_KEY);

  return (
    <AppShell
      title="Qualifying Offers"
      subtitle={
        "2026 MLF \u00b7 Final draft QO state"
      }
      activePath="/qos"
    >
      <div className={styles.summaryBand}>
        <div>
          <strong>
            {snapshot.currentCount}
          </strong>
          <span>Current QOs</span>
        </div>

        <div>
          <strong>
            {snapshot.predraftCount}
          </strong>
          <span>Predraft QOs</span>
        </div>

        <div>
          <strong>
            {snapshot.teams.length}
          </strong>
          <span>Teams</span>
        </div>

        <div>
          <strong>5</strong>
          <span>QO levels</span>
        </div>
      </div>

      <QoSection
        title="Current QOs"
        caption="Effective QO state after the completed five QO rounds."
        teams={snapshot.teams}
        mode="current"
        count={snapshot.currentCount}
      />

      <QoSection
        title="Predraft QOs"
        caption="Original qualifying-offer selections before draft activity."
        teams={snapshot.teams}
        mode="predraft"
        count={snapshot.predraftCount}
      />
    </AppShell>
  );
}