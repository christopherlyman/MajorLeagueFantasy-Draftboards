import Link from "next/link";
import styles from "./page.module.css";

export default function NewLeaguePage() {
  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <Link className={styles.back} href="/">
          ← Contract Keeper Commissioner
        </Link>

        <div className={styles.heading}>
          <p className={styles.step}>League setup · Step 1</p>
          <h1>Create your league</h1>
          <p>
            Start with the league structure. Contract rules and commissioner
            workflows will be stored as part of the league profile.
          </p>
        </div>

        <form className={styles.form}>
          <section className={styles.card}>
            <h2>League basics</h2>

            <label>
              League name
              <input name="leagueName" placeholder="League name" />
            </label>

            <div className={styles.grid}>
              <label>
                Sport
                <select name="sport" defaultValue="baseball">
                  <option value="baseball">Baseball</option>
                </select>
              </label>

              <label>
                Season
                <input name="season" type="number" placeholder="2027" />
              </label>

              <label>
                Number of teams
                <input name="managerCount" type="number" min="4" placeholder="12" />
              </label>

              <label>
                Scoring format
                <select name="scoringFormat" defaultValue="h2h_categories">
                  <option value="h2h_categories">Head-to-head categories</option>
                  <option value="roto">Rotisserie</option>
                  <option value="h2h_points">Head-to-head points</option>
                </select>
              </label>
            </div>
          </section>

          <section className={styles.card}>
            <h2>Player control</h2>
            <p className={styles.help}>
              Contracts are the core model. Other mechanisms are optional.
            </p>

            <label className={styles.check}>
              <input type="checkbox" checked readOnly />
              Multi-year contracts
            </label>

            <label>
              Contract lengths (years)
              <input name="contractLengths" placeholder="Enter comma-separated lengths" />
            </label>

            <div className={styles.checkGrid}>
              <label className={styles.check}><input type="checkbox" /> Qualifying Offers</label>
              <label className={styles.check}><input type="checkbox" /> Prospect Tag</label>
              <label className={styles.check}><input type="checkbox" /> Franchise Tag</label>
              <label className={styles.check}><input type="checkbox" /> Draft-pick trading</label>
            </div>

            <label>
              Qualifying Offer count
              <input name="qoCount" type="number" min="0" placeholder="Used only when QOs are enabled" />
            </label>
          </section>

          <div className={styles.actions}>
            <button className={styles.save} type="button">Save League</button>
            <span>Persistence wiring is the next build step.</span>
          </div>
        </form>
      </div>
    </main>
  );
}
