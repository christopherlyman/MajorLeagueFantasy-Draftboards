"use client";

import Link from "next/link";
import { useState } from "react";
import styles from "./page.module.css";

const sports = ["Baseball", "Football", "Hockey"];
const platforms = ["Yahoo", "ESPN", "Sleeper", "Fantrax", "Fleaflicker", "Manual / Other"];
const models = ["Redraft", "Keeper", "Dynasty", "Contract Keeper"];
const drafts = ["Snake", "Straight / Linear", "Auction", "Custom / Commissioner-defined"];

export default function NewLeaguePage() {
  const [sport, setSport] = useState("Baseball");
  const [platform, setPlatform] = useState("Yahoo");
  const [model, setModel] = useState("Contract Keeper");
  const [draft, setDraft] = useState("Snake");
  const [contractCount, setContractCount] = useState(4);
  const [durations, setDurations] = useState([5, 4, 3, 2]);
  const [restricted, setRestricted] = useState(false);
  const [rightsName, setRightsName] = useState("Qualifying Offer");

  function resizeContracts(count: number) {
    const safe = Math.max(1, Math.min(12, count || 1));
    setContractCount(safe);
    setDurations((current) =>
      Array.from({ length: safe }, (_, i) => current[i] ?? 1),
    );
  }

  function setDuration(index: number, years: number) {
    const safe = Math.max(1, Math.min(10, years || 1));
    setDurations((current) =>
      current.map((value, i) => (i === index ? safe : value)),
    );
  }

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <Link className={styles.back} href="/">← Commissioner Tools</Link>

        <div className={styles.heading}>
          <p className={styles.step}>League setup</p>
          <h1>Create your league</h1>
          <p>
            Tell us how your league works. We will only show settings that
            apply to your format.
          </p>
        </div>

        <form className={styles.form}>
          <section className={styles.card}>
            <h2>League identity</h2>
            <label>
              League name
              <input placeholder="e.g. Sunday Night Baseball" />
            </label>

            <div className={styles.grid}>
              <label>
                Sport
                <select value={sport} onChange={(e) => setSport(e.target.value)}>
                  {sports.map((value) => <option key={value}>{value}</option>)}
                </select>
              </label>

              <label>
                Platform
                <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                  {platforms.map((value) => <option key={value}>{value}</option>)}
                </select>
              </label>

              <label>
                Season
                <input type="number" defaultValue="2027" />
              </label>

              <label>
                Number of teams
                <input type="number" min="4" defaultValue="12" />
              </label>
            </div>
          </section>

          <section className={styles.card}>
            <h2>League model</h2>
            <p className={styles.help}>How player ownership carries between seasons.</p>

            <label>
              League model
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {models.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>

            {model === "Keeper" && (
              <div className={styles.grid}>
                <label>Keeper count<input type="number" min="1" defaultValue="4" /></label>
                <label>
                  Keeper cost
                  <select defaultValue="none">
                    <option value="none">No draft cost</option>
                    <option value="round">Draft-round cost</option>
                    <option value="custom">Custom rule</option>
                  </select>
                </label>
              </div>
            )}

            {model === "Dynasty" && (
              <div className={styles.checkGrid}>
                <label className={styles.check}><input type="checkbox" defaultChecked /> Future-pick trading</label>
                <label className={styles.check}><input type="checkbox" defaultChecked /> Annual rookie/player draft</label>
              </div>
            )}

            {model === "Contract Keeper" && (
              <>
                <label>
                  Contract slots
                  <input
                    type="number"
                    min="1"
                    max="12"
                    value={contractCount}
                    onChange={(e) => resizeContracts(Number(e.target.value))}
                  />
                </label>

                <div className={styles.grid}>
                  {durations.map((years, index) => (
                    <label key={index}>
                      Contract {index + 1} length (years)
                      <input
                        type="number"
                        min="1"
                        max="10"
                        value={years}
                        onChange={(e) => setDuration(index, Number(e.target.value))}
                      />
                    </label>
                  ))}
                </div>

                <div className={styles.checkGrid}>
                  <label className={styles.check}>
                    <input
                      type="checkbox"
                      checked={restricted}
                      onChange={(e) => setRestricted(e.target.checked)}
                    />
                    Restricted rights on expiring players
                  </label>
                  <label className={styles.check}><input type="checkbox" /> Prospect designation</label>
                  <label className={styles.check}><input type="checkbox" /> Franchise designation</label>
                  <label className={styles.check}><input type="checkbox" defaultChecked /> Future-pick trading</label>
                </div>

                {restricted && (
                  <label>
                    What does your league call this?
                    <input
                      value={rightsName}
                      onChange={(e) => setRightsName(e.target.value)}
                      placeholder="e.g. Qualifying Offer"
                    />
                  </label>
                )}
              </>
            )}
          </section>

          <section className={styles.card}>
            <h2>Draft method</h2>
            <p className={styles.help}>Draft format is independent of league model.</p>

            <label>
              Draft method
              <select value={draft} onChange={(e) => setDraft(e.target.value)}>
                {drafts.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>

            {draft === "Auction" && (
              <div className={styles.grid}>
                <label>Starting budget<input type="number" defaultValue="260" /></label>
                <label>Minimum bid<input type="number" defaultValue="1" /></label>
              </div>
            )}
          </section>

          <div className={styles.actions}>
            <button className={styles.save} type="button">Continue to review</button>
            <span>{sport} · {platform} · {model} · {draft}</span>
          </div>
        </form>
      </div>
    </main>
  );
}
