"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import {
  NEW_LEAGUE_STORAGE_KEY,
  type LeagueSetupDraft,
} from "../../../lib/leagueSetup";
import styles from "./page.module.css";

const sports = ["Baseball", "Football", "Hockey"];
const platforms = ["Yahoo", "ESPN", "Sleeper", "Fantrax", "Fleaflicker", "Manual / Other"];
const models = ["Redraft", "Keeper", "Dynasty", "Contract Keeper"];
const drafts = ["Snake", "Straight / Linear", "Auction", "Custom / Commissioner-defined"];

export default function NewLeaguePage() {
  const router = useRouter();

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

  function handleContinue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const form = new FormData(event.currentTarget);
    const isKeeper = model === "Keeper";
    const isDynasty = model === "Dynasty";
    const isContractKeeper = model === "Contract Keeper";
    const isAuction = draft === "Auction";

    const setup: LeagueSetupDraft = {
      leagueName: String(form.get("leagueName") ?? "").trim(),
      sport,
      platform,
      seasonYear: Number(form.get("seasonYear")),
      managerCount: Number(form.get("managerCount")),

      leagueModel: model,

      keeperCount: isKeeper
        ? Number(form.get("keeperCount") ?? 0)
        : 0,
      keeperCostMode: isKeeper
        ? String(form.get("keeperCostMode") ?? "none")
        : "none",

      contractDurations: isContractKeeper
        ? durations.slice(0, contractCount)
        : [],
      restrictedRights: isContractKeeper && restricted,
      restrictedRightsLabel:
        isContractKeeper && restricted ? rightsName.trim() : "",
      prospectDesignation:
        isContractKeeper && form.has("prospectDesignation"),
      franchiseDesignation:
        isContractKeeper && form.has("franchiseDesignation"),
      futurePickTrading:
        (isDynasty || isContractKeeper) &&
        form.has("futurePickTrading"),
      annualDraft:
        isDynasty && form.has("annualDraft"),

      draftMethod: draft,
      executionMode: "offline",
      startingBudget: isAuction
        ? Number(form.get("startingBudget") ?? 260)
        : 0,
      minimumBid: isAuction
        ? Number(form.get("minimumBid") ?? 1)
        : 0,
    };

    sessionStorage.setItem(
      NEW_LEAGUE_STORAGE_KEY,
      JSON.stringify(setup),
    );

    router.push("/leagues/new/review");
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

        <form className={styles.form} onSubmit={handleContinue}>
          <section className={styles.card}>
            <h2>League identity</h2>
            <label>
              League name
              <input
                name="leagueName"
                placeholder="e.g. Sunday Night Baseball"
                required
              />
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
                <input
                  name="seasonYear"
                  type="number"
                  min="2000"
                  defaultValue="2027"
                  required
                />
              </label>

              <label>
                Number of teams
                <input
                  name="managerCount"
                  type="number"
                  min="4"
                  defaultValue="12"
                  required
                />
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
                <label>
                  Keeper count
                  <input
                    name="keeperCount"
                    type="number"
                    min="1"
                    defaultValue="4"
                    required
                  />
                </label>
                <label>
                  Keeper cost
                  <select name="keeperCostMode" defaultValue="none">
                    <option value="none">No draft cost</option>
                    <option value="round">Draft-round cost</option>
                    <option value="custom">Custom rule</option>
                  </select>
                </label>
              </div>
            )}

            {model === "Dynasty" && (
              <div className={styles.checkGrid}>
                <label className={styles.check}>
                  <input
                    name="futurePickTrading"
                    type="checkbox"
                    defaultChecked
                  />
                  Future-pick trading
                </label>
                <label className={styles.check}>
                  <input
                    name="annualDraft"
                    type="checkbox"
                    defaultChecked
                  />
                  Annual rookie/player draft
                </label>
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
                  <label className={styles.check}>
                    <input
                      name="prospectDesignation"
                      type="checkbox"
                    />
                    Prospect designation
                  </label>
                  <label className={styles.check}>
                    <input
                      name="franchiseDesignation"
                      type="checkbox"
                    />
                    Franchise designation
                  </label>
                  <label className={styles.check}>
                    <input
                      name="futurePickTrading"
                      type="checkbox"
                      defaultChecked
                    />
                    Future-pick trading
                  </label>
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
                <label>
                  Starting budget
                  <input
                    name="startingBudget"
                    type="number"
                    min="1"
                    defaultValue="260"
                    required
                  />
                </label>
                <label>
                  Minimum bid
                  <input
                    name="minimumBid"
                    type="number"
                    min="1"
                    defaultValue="1"
                    required
                  />
                </label>
              </div>
            )}
          </section>

          <div className={styles.actions}>
            <button className={styles.save} type="submit">
              Continue to review
            </button>
            <span>{sport} · {platform} · {model} · {draft}</span>
          </div>
        </form>
      </div>
    </main>
  );
}
