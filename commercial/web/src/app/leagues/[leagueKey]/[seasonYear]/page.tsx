"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import styles from "../../new/page.module.css";

type StoredLeague = {
  league_key: string;
  season_year: number;
  profile_version: number;
  is_active: boolean;
  summary: {
    name: string;
    platform: string;
    sport: string;
    league_model: string;
    manager_count: number;
    draft_method: string;
    contract_slots: number;
    contract_durations: number[];
    restricted_rights_enabled: boolean;
    restricted_rights_label: string;
    future_pick_trading: boolean;
    annual_draft: boolean;
  };
};

function label(value: string) {
  return value
    .split("_")
    .map(
      (part) =>
        part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(" ");
}

function yesNo(value: boolean) {
  return value ? "Yes" : "No";
}

export default function LeaguePage() {
  const params = useParams<{
    leagueKey: string;
    seasonYear: string;
  }>();

  const [league, setLeague] = useState<StoredLeague | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadLeague() {
      try {
        const leagueKey = String(params.leagueKey);
        const seasonYear = String(params.seasonYear);

        const response = await fetch(
          `/api/leagues/${encodeURIComponent(leagueKey)}/` +
            `${encodeURIComponent(seasonYear)}`,
          { cache: "no-store" },
        );

        const result = await response.json().catch(() => null);

        if (!response.ok) {
          throw new Error(
            result?.detail ?? "Commissioner Tools could not load this league.",
          );
        }

        if (!cancelled) {
          setLeague(result as StoredLeague);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "League loading failed.",
          );
        }
      }
    }

    void loadLeague();

    return () => {
      cancelled = true;
    };
  }, [params.leagueKey, params.seasonYear]);

  if (error) {
    return (
      <main className={styles.page}>
        <div className={styles.container}>
          <Link className={styles.back} href="/">
            ← Commissioner Tools
          </Link>

          <div className={styles.heading}>
            <p className={styles.step}>League</p>
            <h1>Unable to load league</h1>
            <p>{error}</p>
          </div>
        </div>
      </main>
    );
  }

  if (!league) {
    return (
      <main className={styles.page}>
        <div className={styles.container}>
          <div className={styles.heading}>
            <p className={styles.step}>League</p>
            <h1>Loading league…</h1>
          </div>
        </div>
      </main>
    );
  }

  const summary = league.summary;

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <Link className={styles.back} href="/">
          ← Commissioner Tools
        </Link>

        <div className={styles.heading}>
          <p className={styles.step}>
            {label(summary.sport)} · {league.season_year}
          </p>
          <h1>{summary.name}</h1>
          <p>
            League created successfully. Commissioner Tools loaded
            this profile back from the league database.
          </p>
        </div>

        <div className={styles.form}>
          <section className={styles.card}>
            <h2>League</h2>

            <div className={styles.reviewList}>
              <div className={styles.reviewRow}>
                <span>League key</span>
                <strong>{league.league_key}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Profile version</span>
                <strong>{league.profile_version}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Platform</span>
                <strong>{label(summary.platform)}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>League model</span>
                <strong>{label(summary.league_model)}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Teams</span>
                <strong>{summary.manager_count}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Draft method</span>
                <strong>{label(summary.draft_method)}</strong>
              </div>
            </div>
          </section>

          {summary.league_model === "contract_keeper" && (
            <section className={styles.card}>
              <h2>Player control</h2>

              <div className={styles.reviewList}>
                <div className={styles.reviewRow}>
                  <span>Contract slots</span>
                  <strong>{summary.contract_slots}</strong>
                </div>
                <div className={styles.reviewRow}>
                  <span>Contract lengths</span>
                  <strong>
                    {summary.contract_durations.join(", ")} years
                  </strong>
                </div>
                <div className={styles.reviewRow}>
                  <span>Restricted rights</span>
                  <strong>
                    {summary.restricted_rights_enabled
                      ? summary.restricted_rights_label
                      : "Disabled"}
                  </strong>
                </div>
                <div className={styles.reviewRow}>
                  <span>Future-pick trading</span>
                  <strong>
                    {yesNo(summary.future_pick_trading)}
                  </strong>
                </div>
              </div>
            </section>
          )}

          <div className={styles.notice}>
            <strong>League profile version 1 saved.</strong>
            <span>
              Franchise setup and commissioner workflow are the next
              lifecycle steps.
            </span>
          </div>
        </div>
      </div>
    </main>
  );
}
