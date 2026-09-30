"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type QOPlayer = {
  yahoo_player_key: string;
  name: string;
  rank_value: number | null;
  predraft_qo_team_key: string | null;
};


type QOTeam = {
  team_key: string;
  team_name: string;
  predraft: Array<string | null>;
  current: Array<string | null>;
};


type QOState = {
  draft_key: string;
  draft_status: string;
  selection_count: number;
  qo_rounds: number;
  baseline_synced: boolean;
  can_edit: boolean;
  lock_reason: string | null;
  predraft_count: number;
  current_count: number;
  teams: QOTeam[];
  players: QOPlayer[];
};


type WriteStatus = {
  write_enabled: boolean;
  user_id: number | null;
  email: string | null;
  authority: string;
  must_change_password: boolean;
};


type UpdateResponse = {
  updated_team_key: string;
  current_qo_count: number;
  performed_by: string;
  state: QOState;
};


function blankSlots(): string[] {
  return ["", "", "", "", ""];
}


export function CommissionerQualifyingOffers() {
  const [
    state,
    setState,
  ] = useState<QOState | null>(null);

  const [
    writeStatus,
    setWriteStatus,
  ] = useState<WriteStatus | null>(null);

  const [
    teamKey,
    setTeamKey,
  ] = useState("");

  const [
    slots,
    setSlots,
  ] = useState<string[]>(
    blankSlots,
  );

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    busy,
    setBusy,
  ] = useState(false);

  const [
    message,
    setMessage,
  ] = useState("");


  const loadWriteStatus =
    useCallback(async (): Promise<
      WriteStatus | null
    > => {
      try {
        const response = await fetch(
          "/gateway/commissioner/write-status",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          return null;
        }

        const body =
          await response.json();

        const nextStatus =
          body as WriteStatus;

        setWriteStatus(nextStatus);

        return nextStatus;
      } catch {
        return null;
      }
    }, []);


  const loadState =
    useCallback(async (): Promise<QOState> => {
      const response = await fetch(
        "/gateway/commissioner/qualifying-offers",
        {
          credentials: "same-origin",
          cache: "no-store",
        },
      );

      if (!response.ok) {
        throw new Error(
          "Qualifying-offer state is unavailable.",
        );
      }

      const body =
        await response.json();

      const nextState =
        body as QOState;

      setState(nextState);

      return nextState;
    }, []);


  function applyTeam(
    nextState: QOState,
    nextTeamKey: string,
  ) {
    const team =
      nextState.teams.find(
        (row) => (
          row.team_key === nextTeamKey
        ),
      );

    setTeamKey(nextTeamKey);

    setSlots(
      team
        ? team.predraft.map(
            (value) => value ?? "",
          )
        : blankSlots(),
    );

    setMessage("");
  }


  useEffect(() => {
    let active = true;

    async function initialLoad() {
      try {
        const [
          nextState,
        ] = await Promise.all([
          loadState(),
          loadWriteStatus(),
        ]);

        if (!active) {
          return;
        }

        const firstTeam =
          nextState.teams[0]
            ?.team_key
          ?? "";

        applyTeam(
          nextState,
          firstTeam,
        );
      } catch (error) {
        if (active) {
          setMessage(
            error instanceof Error
              ? error.message
              : (
                "Qualifying-offer state "
                + "is unavailable."
              ),
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void initialLoad();

    const onWriteStatusChanged = () => {
      void loadWriteStatus();
    };

    window.addEventListener(
      "mlf-commissioner-write-status-changed",
      onWriteStatusChanged,
    );

    return () => {
      active = false;

      window.removeEventListener(
        "mlf-commissioner-write-status-changed",
        onWriteStatusChanged,
      );
    };
  }, [
    loadState,
    loadWriteStatus,
  ]);


  const playersByKey = useMemo(
    () => new Map(
      (state?.players ?? []).map(
        (player) => [
          player.yahoo_player_key,
          player,
        ],
      ),
    ),
    [state],
  );


  const currentTeam = useMemo(
    () => (
      state?.teams.find(
        (team) => (
          team.team_key === teamKey
        ),
      )
      ?? null
    ),
    [
      state,
      teamKey,
    ],
  );


  function playerLabel(
    playerKey: string | null,
  ): string {
    if (!playerKey) {
      return "—";
    }

    const player =
      playersByKey.get(playerKey);

    if (!player) {
      return playerKey;
    }

    const rank = (
      player.rank_value !== null
        ? `#${player.rank_value} — `
        : ""
    );

    return (
      `${rank}${player.name}`
    );
  }


  const uniqueSelections = (
    slots.filter(Boolean).length === 5
    && new Set(slots).size === 5
  );

  const writeEnabled = (
    writeStatus?.write_enabled
    === true
  );

  const canSave = (
    state?.can_edit === true
    && writeEnabled
    && teamKey !== ""
    && uniqueSelections
    && !busy
  );


  function changeSlot(
    index: number,
    playerKey: string,
  ) {
    setSlots(
      (current) =>
        current.map(
          (value, currentIndex) => (
            currentIndex === index
              ? playerKey
              : value
          ),
        ),
    );

    setMessage("");
  }


  async function save() {
    if (!state || !canSave) {
      return;
    }

    const currentWriteStatus =
      await loadWriteStatus();

    if (
      currentWriteStatus?.write_enabled
      !== true
    ) {
      setMessage(
        "Commissioner writes are locked. "
        + "Enable the named write identity "
        + "in Step 2 first.",
      );

      return;
    }

    const teamName =
      currentTeam?.team_name
      ?? teamKey;

    const accepted = window.confirm(
      `Replace all five predraft QOs for `
      + `${teamName}?`,
    );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "qualifying-offers/"
        + encodeURIComponent(teamKey),
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            player_keys: slots,
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error(
            "Commissioner write authorization "
            + "expired. Enable writes again.",
          );
        }

        if (response.status === 409) {
          throw new Error(
            "Predraft qualifying offers are "
            + "locked or canonical QO state "
            + "changed. Reload before retrying.",
          );
        }

        if (response.status === 400) {
          throw new Error(
            "Invalid QO selection. Five unique, "
            + "eligible players are required.",
          );
        }

        throw new Error(
          "Qualifying-offer save failed.",
        );
      }

      const result =
        body as UpdateResponse;

      setState(result.state);

      applyTeam(
        result.state,
        result.updated_team_key,
      );

      setMessage(
        "Predraft qualifying offers saved "
        + "and current QO state rebuilt.",
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Qualifying-offer save failed.",
      );

      try {
        const refreshed =
          await loadState();

        applyTeam(
          refreshed,
          teamKey,
        );
      } catch {
        // Preserve the original failure message.
      }

    } finally {
      setBusy(false);
    }
  }


  if (loading) {
    return (
      <>
        <div className={styles.stepTitleRow}>
          <strong>
            Qualifying Offers
          </strong>

          <span
            className={
              styles.writeLockedBadge
            }
          >
            Loading
          </span>
        </div>

        <span>
          Loading predraft QO state…
        </span>
      </>
    );
  }


  return (
    <>
      <div className={styles.stepTitleRow}>
        <strong>
          Qualifying Offers
        </strong>

        <span
          className={
            state?.can_edit
              ? (
                  writeEnabled
                    ? styles.stepReady
                    : styles.writeLockedBadge
                )
              : styles.writeLockedBadge
          }
        >
          {state?.can_edit
            ? (
                writeEnabled
                  ? "Write enabled"
                  : "Write locked"
              )
            : "Predraft locked"}
        </span>
      </div>

      <span>
        Review the five predraft QOs for each
        team and the current QO ladder derived
        from draft history.
      </span>

      {state && (
        <div className={styles.qoPanel}>
          <div className={styles.tradeContext}>
            <span>
              Predraft rows
              <strong>
                {state.predraft_count}
              </strong>
            </span>

            <span>
              Current rows
              <strong>
                {state.current_count}
              </strong>
            </span>

            <span>
              Selections
              <strong>
                {state.selection_count}
              </strong>
            </span>

            <span>
              Mirror
              <strong>
                {state.baseline_synced
                  ? "Synced"
                  : "Mismatch"}
              </strong>
            </span>
          </div>

          {state.lock_reason && (
            <div className={styles.tradeWriteNotice}>
              {state.lock_reason}
            </div>
          )}

          <label className={styles.qoTeamSelect}>
            <span>
              Team
            </span>

            <select
              value={teamKey}
              onChange={(event) => {
                applyTeam(
                  state,
                  event.target.value,
                );
              }}
            >
              {state.teams.map(
                (team) => (
                  <option
                    key={team.team_key}
                    value={team.team_key}
                  >
                    {team.team_name}
                  </option>
                ),
              )}
            </select>
          </label>

          <div className={styles.qoGrid}>
            {slots.map(
              (playerKey, index) => {
                const usedElsewhere =
                  new Set(
                    slots.filter(
                      (_, slotIndex) =>
                        slotIndex !== index,
                    ),
                  );

                return (
                  <label
                    className={styles.qoField}
                    key={`qo-${index + 1}`}
                  >
                    <span>
                      QO{index + 1}
                    </span>

                    <select
                      value={playerKey}
                      disabled={!state.can_edit}
                      onChange={(event) => {
                        changeSlot(
                          index,
                          event.target.value,
                        );
                      }}
                    >
                      <option value="">
                        Select player
                      </option>

                      {state.players.map(
                        (player) => {
                          const ownedByOther = (
                            player
                              .predraft_qo_team_key
                            && player
                              .predraft_qo_team_key
                              !== teamKey
                          );

                          const duplicate = (
                            usedElsewhere.has(
                              player
                                .yahoo_player_key,
                            )
                          );

                          return (
                            <option
                              key={
                                player
                                  .yahoo_player_key
                              }
                              value={
                                player
                                  .yahoo_player_key
                              }
                              disabled={
                                Boolean(
                                  ownedByOther
                                )
                                || duplicate
                              }
                            >
                              {playerLabel(
                                player
                                  .yahoo_player_key,
                              )}
                            </option>
                          );
                        },
                      )}
                    </select>
                  </label>
                );
              },
            )}
          </div>

          <div className={styles.qoCurrent}>
            <strong>
              Current QO ladder after draft replay
            </strong>

            <div className={styles.qoCurrentGrid}>
              {(currentTeam?.current
                ?? [
                  null,
                  null,
                  null,
                  null,
                  null,
                ]
              ).map(
                (playerKey, index) => (
                  <div
                    key={
                      `current-${index + 1}`
                    }
                  >
                    <span>
                      QO{index + 1}
                    </span>

                    <strong>
                      {playerLabel(
                        playerKey,
                      )}
                    </strong>
                  </div>
                ),
              )}
            </div>
          </div>

          <div className={styles.qoActions}>
            <button
              type="button"
              className={
                styles.writeSecondaryButton
              }
              disabled={!currentTeam}
              onClick={() => {
                if (currentTeam) {
                  setSlots(
                    currentTeam.predraft.map(
                      (value) => (
                        value ?? ""
                      ),
                    ),
                  );

                  setMessage("");
                }
              }}
            >
              Reset
            </button>

            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={!canSave}
              onClick={() => {
                void save();
              }}
            >
              {busy
                ? "Saving…"
                : "Save Five Predraft QOs"}
            </button>
          </div>

          {message && (
            <div
              className={styles.writeMessage}
              aria-live="polite"
            >
              {message}
            </div>
          )}
        </div>
      )}
    </>
  );
}
