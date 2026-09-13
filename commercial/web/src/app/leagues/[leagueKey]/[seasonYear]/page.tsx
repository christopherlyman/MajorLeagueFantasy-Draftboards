"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import {
  FormEvent,
  useEffect,
  useState,
} from "react";
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

type StoredFranchise = {
  franchise_id: number;
  franchise_name: string;
  league_key: string;
  season_year: number;
  team_key: string;
  team_name: string;
  owner_name: string | null;
  source: string;
};

type FranchiseResponse = {
  league_key: string;
  season_year: number;
  count: number;
  franchises: StoredFranchise[];
};

type FranchiseDraft = {
  team_name: string;
  owner_name: string;
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

function franchisePath(
  leagueKey: string,
  seasonYear: string,
) {
  return (
    `/api/leagues/${encodeURIComponent(leagueKey)}/` +
    `${encodeURIComponent(seasonYear)}/franchises`
  );
}

export default function LeaguePage() {
  const params = useParams<{
    leagueKey: string;
    seasonYear: string;
  }>();

  const [league, setLeague] = useState<StoredLeague | null>(
    null,
  );

  const [franchises, setFranchises] = useState<
    StoredFranchise[] | null
  >(null);

  const [drafts, setDrafts] = useState<FranchiseDraft[]>(
    [],
  );

  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadLeagueWorkspace() {
      try {
        const leagueKey = String(params.leagueKey);
        const seasonYear = String(params.seasonYear);

        const leagueUrl =
          `/api/leagues/${encodeURIComponent(leagueKey)}/` +
          `${encodeURIComponent(seasonYear)}`;

        const franchisesUrl = franchisePath(
          leagueKey,
          seasonYear,
        );

        const [leagueResponse, franchiseResponse] =
          await Promise.all([
            fetch(leagueUrl, { cache: "no-store" }),
            fetch(franchisesUrl, { cache: "no-store" }),
          ]);

        const leagueResult = await leagueResponse
          .json()
          .catch(() => null);

        if (!leagueResponse.ok) {
          throw new Error(
            leagueResult?.detail ??
              "Commissioner Tools could not load this league.",
          );
        }

        const franchiseResult = await franchiseResponse
          .json()
          .catch(() => null);

        if (!franchiseResponse.ok) {
          throw new Error(
            franchiseResult?.detail ??
              "Commissioner Tools could not load league franchises.",
          );
        }

        const storedLeague = leagueResult as StoredLeague;
        const storedFranchises =
          franchiseResult as FranchiseResponse;

        if (!cancelled) {
          setLeague(storedLeague);
          setFranchises(storedFranchises.franchises);

          if (storedFranchises.count === 0) {
            setDrafts(
              Array.from(
                {
                  length:
                    storedLeague.summary.manager_count,
                },
                () => ({
                  team_name: "",
                  owner_name: "",
                }),
              ),
            );
          }
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

    void loadLeagueWorkspace();

    return () => {
      cancelled = true;
    };
  }, [params.leagueKey, params.seasonYear]);

  function updateDraft(
    index: number,
    field: keyof FranchiseDraft,
    value: string,
  ) {
    setDrafts((current) =>
      current.map((row, rowIndex) =>
        rowIndex === index
          ? {
              ...row,
              [field]: value,
            }
          : row,
      ),
    );
  }

  async function handleFranchiseSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    if (!league || saving) {
      return;
    }

    setSaveError("");

    const normalized = drafts.map((row) => ({
      team_name: row.team_name.trim(),
      owner_name: row.owner_name.trim(),
    }));

    if (
      normalized.length !==
      league.summary.manager_count
    ) {
      setSaveError(
        "Franchise setup does not match the league team count.",
      );
      return;
    }

    const blankTeam = normalized.findIndex(
      (row) => !row.team_name,
    );

    if (blankTeam >= 0) {
      setSaveError(
        `Team ${blankTeam + 1} needs a team name.`,
      );
      return;
    }

    setSaving(true);

    try {
      const response = await fetch(
        franchisePath(
          league.league_key,
          String(league.season_year),
        ),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            franchises: normalized.map((row) => ({
              team_name: row.team_name,
              owner_name: row.owner_name || null,
            })),
          }),
        },
      );

      const result = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          result?.detail ??
            "Commissioner Tools could not save franchises.",
        );
      }

      const stored = result as FranchiseResponse;

      setFranchises(stored.franchises);
      setDrafts([]);
    } catch (submitError) {
      setSaveError(
        submitError instanceof Error
          ? submitError.message
          : "Franchise setup failed.",
      );
    } finally {
      setSaving(false);
    }
  }

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

  if (!league || franchises === null) {
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
  const franchisesInitialized = franchises.length > 0;

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
            Commissioner Tools loaded this league and its
            current franchise state from the league database.
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

          {!franchisesInitialized ? (
            <form
              className={styles.form}
              onSubmit={handleFranchiseSubmit}
            >
              <section className={styles.card}>
                <h2>Set up franchises</h2>
                <p className={styles.help}>
                  Enter all {summary.manager_count} teams.
                  Team names are required. Manager names are
                  optional and can differ from the stable
                  franchise identity.
                </p>

                <div className={styles.grid}>
                  {drafts.map((row, index) => (
                    <div key={index}>
                      <label>
                        Team {index + 1} name
                        <input
                          type="text"
                          value={row.team_name}
                          onChange={(event) =>
                            updateDraft(
                              index,
                              "team_name",
                              event.target.value,
                            )
                          }
                          placeholder={`Team ${index + 1}`}
                          autoComplete="off"
                          disabled={saving}
                        />
                      </label>

                      <label>
                        Team {index + 1} manager
                        <input
                          type="text"
                          value={row.owner_name}
                          onChange={(event) =>
                            updateDraft(
                              index,
                              "owner_name",
                              event.target.value,
                            )
                          }
                          placeholder="Optional"
                          autoComplete="off"
                          disabled={saving}
                        />
                      </label>
                    </div>
                  ))}
                </div>
              </section>

              {saveError && (
                <div
                  className={styles.notice}
                  role="alert"
                >
                  <strong>Franchise setup not saved.</strong>
                  <span>{saveError}</span>
                </div>
              )}

              <div className={styles.actions}>
                <span>
                  All franchises are created together in one
                  transaction.
                </span>

                <button
                  className={styles.save}
                  type="submit"
                  disabled={saving}
                >
                  {saving
                    ? "Saving franchises…"
                    : `Save ${summary.manager_count} franchises`}
                </button>
              </div>
            </form>
          ) : (
            <section className={styles.card}>
              <h2>Franchises</h2>
              <p className={styles.help}>
                {franchises.length} canonical franchises are
                initialized for the {league.season_year} season.
              </p>

              <div className={styles.reviewList}>
                {franchises.map((franchise, index) => (
                  <div
                    className={styles.reviewRow}
                    key={franchise.franchise_id}
                  >
                    <span>Team {index + 1}</span>
                    <strong>
                      {franchise.team_name}
                      {franchise.owner_name
                        ? ` — ${franchise.owner_name}`
                        : ""}
                    </strong>
                  </div>
                ))}
              </div>
            </section>
          )}

          <div className={styles.notice}>
            <strong>
              {franchisesInitialized
                ? "Franchise setup complete."
                : `League profile version ${league.profile_version} saved.`}
            </strong>
            <span>
              {franchisesInitialized
                ? "Commissioner Tools is now using durable franchise identities for this league."
                : "Complete franchise setup to establish the league's canonical teams and managers."}
            </span>
          </div>
        </div>
      </div>
    </main>
  );
}
