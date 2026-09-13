"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  NEW_LEAGUE_STORAGE_KEY,
  type LeagueSetupDraft,
} from "../../../../lib/leagueSetup";
import styles from "../page.module.css";

function yesNo(value: boolean) {
  return value ? "Yes" : "No";
}

export default function ReviewLeaguePage() {
  const router = useRouter();
  const [setup, setSetup] = useState<LeagueSetupDraft | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [validationState, setValidationState] = useState<
    "idle" | "validating" | "valid" | "error"
  >("idle");
  const [validationMessage, setValidationMessage] = useState("");
  const [createState, setCreateState] = useState<
    "idle" | "creating" | "error"
  >("idle");
  const [createMessage, setCreateMessage] = useState("");

  useEffect(() => {
    const raw = sessionStorage.getItem(NEW_LEAGUE_STORAGE_KEY);

    if (raw) {
      try {
        setSetup(JSON.parse(raw) as LeagueSetupDraft);
      } catch {
        sessionStorage.removeItem(NEW_LEAGUE_STORAGE_KEY);
      }
    }

    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!setup) {
      return;
    }

    let cancelled = false;

    async function validateSetup() {
      setValidationState("validating");
      setValidationMessage(
        "Checking this setup against the Commissioner Tools league rules.",
      );

      try {
        const response = await fetch("/api/leagues/validate", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(setup),
        });

        const result = await response.json().catch(() => null);

        if (!response.ok) {
          throw new Error(
            result?.detail ??
              "The league setup did not pass validation.",
          );
        }

        if (result?.valid !== true) {
          throw new Error(
            "The validation service did not confirm this setup.",
          );
        }

        if (!cancelled) {
          setValidationState("valid");
          setValidationMessage(
            "This league setup passed Profile v2 validation.",
          );
        }
      } catch (error) {
        if (!cancelled) {
          setValidationState("error");
          setValidationMessage(
            error instanceof Error
              ? error.message
              : "League setup validation failed.",
          );
        }
      }
    }

    void validateSetup();

    return () => {
      cancelled = true;
    };
  }, [setup]);

  async function createLeague() {
    if (!setup || validationState !== "valid") {
      return;
    }

    setCreateState("creating");
    setCreateMessage("");

    try {
      const response = await fetch("/api/leagues", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(setup),
      });

      const result = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          result?.detail ?? "Commissioner Tools could not create this league.",
        );
      }

      if (
        result?.created !== true ||
        !result?.league_key ||
        !result?.season_year
      ) {
        throw new Error(
          "The league service did not return a complete created league.",
        );
      }

      sessionStorage.removeItem(NEW_LEAGUE_STORAGE_KEY);

      router.push(
        `/leagues/${encodeURIComponent(result.league_key)}/` +
          `${result.season_year}`,
      );
    } catch (error) {
      setCreateState("error");
      setCreateMessage(
        error instanceof Error
          ? error.message
          : "League creation failed.",
      );
    }
  }

  if (!loaded) {
    return null;
  }

  if (!setup) {
    return (
      <main className={styles.page}>
        <div className={styles.container}>
          <div className={styles.heading}>
            <p className={styles.step}>League setup</p>
            <h1>Nothing to review yet</h1>
            <p>
              Start with your league settings, then return here to
              review them before creating the league.
            </p>
          </div>

          <button
            className={styles.save}
            type="button"
            onClick={() => router.push("/leagues/new")}
          >
            Create a league
          </button>
        </div>
      </main>
    );
  }

  const contractKeeper =
    setup.leagueModel === "Contract Keeper";
  const keeper = setup.leagueModel === "Keeper";
  const dynasty = setup.leagueModel === "Dynasty";
  const auction = setup.draftMethod === "Auction";

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <button
          className={styles.back}
          type="button"
          onClick={() => router.back()}
        >
          ← Back to edit
        </button>

        <div className={styles.heading}>
          <p className={styles.step}>League setup · Review</p>
          <h1>Review your league</h1>
          <p>
            Check the setup below before Commissioner Tools creates
            the league.
          </p>
        </div>

        <div className={styles.form}>
          <section className={styles.card}>
            <h2>League identity</h2>

            <div className={styles.reviewList}>
              <div className={styles.reviewRow}>
                <span>League name</span>
                <strong>{setup.leagueName}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Sport</span>
                <strong>{setup.sport}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Platform</span>
                <strong>{setup.platform}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Season</span>
                <strong>{setup.seasonYear}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Teams</span>
                <strong>{setup.managerCount}</strong>
              </div>
            </div>
          </section>

          <section className={styles.card}>
            <h2>League model</h2>

            <div className={styles.reviewList}>
              <div className={styles.reviewRow}>
                <span>Model</span>
                <strong>{setup.leagueModel}</strong>
              </div>

              {keeper && (
                <>
                  <div className={styles.reviewRow}>
                    <span>Keeper count</span>
                    <strong>{setup.keeperCount}</strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Keeper cost</span>
                    <strong>{setup.keeperCostMode}</strong>
                  </div>
                </>
              )}

              {dynasty && (
                <>
                  <div className={styles.reviewRow}>
                    <span>Future-pick trading</span>
                    <strong>{yesNo(setup.futurePickTrading)}</strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Annual rookie/player draft</span>
                    <strong>{yesNo(setup.annualDraft)}</strong>
                  </div>
                </>
              )}

              {contractKeeper && (
                <>
                  <div className={styles.reviewRow}>
                    <span>Contract slots</span>
                    <strong>{setup.contractDurations.length}</strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Contract lengths</span>
                    <strong>
                      {setup.contractDurations.join(", ")} years
                    </strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Restricted rights</span>
                    <strong>
                      {setup.restrictedRights
                        ? setup.restrictedRightsLabel
                        : "Disabled"}
                    </strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Prospect designation</span>
                    <strong>
                      {yesNo(setup.prospectDesignation)}
                    </strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Franchise designation</span>
                    <strong>
                      {yesNo(setup.franchiseDesignation)}
                    </strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Future-pick trading</span>
                    <strong>
                      {yesNo(setup.futurePickTrading)}
                    </strong>
                  </div>
                </>
              )}
            </div>
          </section>

          <section className={styles.card}>
            <h2>Draft</h2>

            <div className={styles.reviewList}>
              <div className={styles.reviewRow}>
                <span>Draft method</span>
                <strong>{setup.draftMethod}</strong>
              </div>
              <div className={styles.reviewRow}>
                <span>Operation</span>
                <strong>Commissioner-operated</strong>
              </div>

              {auction && (
                <>
                  <div className={styles.reviewRow}>
                    <span>Starting budget</span>
                    <strong>${setup.startingBudget}</strong>
                  </div>
                  <div className={styles.reviewRow}>
                    <span>Minimum bid</span>
                    <strong>${setup.minimumBid}</strong>
                  </div>
                </>
              )}
            </div>
          </section>

          <div
            className={styles.notice}
            aria-live="polite"
          >
            <strong>
              {validationState === "validating" &&
                "Validating league setup…"}
              {validationState === "valid" &&
                "League setup validated."}
              {validationState === "error" &&
                "Validation needs attention."}
              {validationState === "idle" &&
                "Ready for validation."}
            </strong>
            <span>{validationMessage}</span>
          </div>

          {createState === "error" && (
            <div className={styles.notice} aria-live="polite">
              <strong>League creation failed.</strong>
              <span>{createMessage}</span>
            </div>
          )}

          <div className={styles.actions}>
            <button
              className={styles.secondary}
              type="button"
              onClick={() => router.back()}
              disabled={createState === "creating"}
            >
              Back to edit
            </button>

            <button
              className={styles.save}
              type="button"
              disabled={
                validationState !== "valid" ||
                createState === "creating"
              }
              onClick={createLeague}
            >
              {createState === "creating"
                ? "Creating league…"
                : "Create League"}
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
