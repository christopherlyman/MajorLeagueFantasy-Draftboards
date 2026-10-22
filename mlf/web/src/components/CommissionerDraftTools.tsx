"use client";

import {
  useCallback,
  useEffect,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type DraftPick = {
  pick_id: string;
  round_number: number;
  slot_number: number;

  current_owner_team_key: string;
  current_owner_team_name: string;

  selected_player_key: string | null;
  selected_player_name: string | null;
  pick_kind: string | null;
  selected_at_utc: string | null;
};


type DraftToolsState = {
  draft_key: string;
  draft_status: string;

  current_pick_id: string | null;

  is_running: boolean;
  pick_started_at_utc: string | null;
  pick_paused_at_utc: string | null;
  elapsed_paused_seconds: number;

  seconds_per_pick: number;
  weekends_count: boolean;
  timezone_name: string;

  selection_count: number;
  open_pick_count: number;

  open_picks: DraftPick[];
  selected_picks: DraftPick[];
};


type WriteStatus = {
  write_enabled: boolean;
  user_id: number | null;
  email: string | null;
  authority: string;
  must_change_password: boolean;
};


type MutationResponse = {
  action: string;
  result: string;
  performed_by: string;
  state: DraftToolsState;
};


function pickLabel(
  pick: DraftPick,
): string {
  return (
    `R${pick.round_number}.`
    + `${pick.slot_number} — `
    + `${pick.current_owner_team_name}`
  );
}


function selectedPickLabel(
  pick: DraftPick,
): string {
  const player = (
    pick.selected_player_name
    ?? pick.selected_player_key
    ?? "Unknown player"
  );

  return (
    `${pickLabel(pick)} — ${player}`
  );
}


export function CommissionerDraftTools() {
  const [
    state,
    setState,
  ] = useState<DraftToolsState | null>(
    null,
  );

  const [
    writeStatus,
    setWriteStatus,
  ] = useState<WriteStatus | null>(
    null,
  );

  const [
    targetPick,
    setTargetPick,
  ] = useState("");

  const [
    durationHours,
    setDurationHours,
  ] = useState("24");

  const [
    weekendsCount,
    setWeekendsCount,
  ] = useState(false);

  const [
    selectedPick,
    setSelectedPick,
  ] = useState("");

  const [
    busy,
    setBusy,
  ] = useState(false);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    message,
    setMessage,
  ] = useState("");


  const applyState = useCallback(
    (
      nextState: DraftToolsState,
    ) => {
      setState(nextState);

      setTargetPick(
        nextState.current_pick_id
        ?? nextState.open_picks[0]
          ?.pick_id
        ?? "",
      );

      setDurationHours(
        String(
          Math.max(
            1,
            Math.round(
              nextState.seconds_per_pick
              / 3600,
            ),
          ),
        ),
      );

      setWeekendsCount(
        nextState.weekends_count,
      );

      setSelectedPick(
        (
          current
        ) => (
          nextState.selected_picks.some(
            (pick) => (
              pick.pick_id === current
            ),
          )
            ? current
            : (
              nextState.selected_picks[0]
                ?.pick_id
              ?? ""
            )
        ),
      );
    },
    [],
  );


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
    useCallback(
      async (): Promise<DraftToolsState> => {
        const response = await fetch(
          "/gateway/commissioner/draft-tools",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          throw new Error(
            "Draft Tools state is unavailable.",
          );
        }

        const body =
          await response.json();

        const nextState =
          body as DraftToolsState;

        applyState(
          nextState,
        );

        return nextState;
      },
      [
        applyState,
      ],
    );


  useEffect(() => {
    let active = true;

    async function initialLoad() {
      try {
        await Promise.all([
          loadState(),
          loadWriteStatus(),
        ]);

      } catch (error) {
        if (active) {
          setMessage(
            error instanceof Error
              ? error.message
              : (
                "Draft Tools state "
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


  const writeEnabled = (
    writeStatus?.write_enabled
    === true
  );


  async function mutate(
    path: string,
    payload: object,
    successMessage: string,
  ) {
    if (busy) {
      return;
    }

    const currentStatus =
      await loadWriteStatus();

    if (
      currentStatus?.write_enabled
      !== true
    ) {
      setMessage(
        "Enable Commissioner Writes "
        + "in Step 2 first.",
      );
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        path,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify(
            payload,
          ),
        },
      );

      let body: unknown = null;

      try {
        body =
          await response.json();
      } catch {
        body = null;
      }

      if (!response.ok) {
        throw new Error(
          "Draft Tool change was rejected. "
          + "Refresh the page and "
          + "check the current draft state.",
        );
      }

      const result =
        body as MutationResponse;

      applyState(
        result.state,
      );

      setMessage(
        successMessage,
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Draft Tool change failed.",
      );

      try {
        await loadState();
      } catch {
        // Preserve the mutation error.
      }

    } finally {
      setBusy(false);
    }
  }


  async function setCurrentPick() {
    if (!targetPick) {
      return;
    }

    const pick =
      state?.open_picks.find(
        (row) => (
          row.pick_id
          === targetPick
        ),
      );

    const accepted =
      window.confirm(
        "Set the live draft to "
        + `${
          pick
            ? pickLabel(pick)
            : targetPick
        }?`,
      );

    if (!accepted) {
      return;
    }

    await mutate(
      "/gateway/commissioner/"
      + "draft-tools/current-pick",
      {
        pick_id:
          targetPick,
      },
      "Current pick updated.",
    );
  }


  async function clockAction(
    action:
      | "START"
      | "PAUSE"
      | "RESUME",
  ) {
    await mutate(
      "/gateway/commissioner/"
      + "draft-tools/clock",
      {
        action,
      },
      (
        action === "START"
          ? "Draft clock started."
          : (
            action === "PAUSE"
              ? "Draft clock paused."
              : "Draft clock resumed."
          )
      ),
    );
  }


  async function saveDuration() {
    const hours =
      Number(durationHours);

    if (
      !Number.isInteger(hours)
      || hours < 1
      || hours > 72
    ) {
      setMessage(
        "Pick duration must be "
        + "1-72 whole hours.",
      );
      return;
    }

    await mutate(
      "/gateway/commissioner/"
      + "draft-tools/clock",
      {
        action:
          "SET_DURATION",
        seconds_per_pick:
          hours * 3600,
      },
      "Pick duration updated.",
    );
  }


  async function saveWeekendRule() {
    await mutate(
      "/gateway/commissioner/"
      + "draft-tools/clock",
      {
        action:
          "SET_WEEKENDS",
        weekends_count:
          weekendsCount,
      },
      "Weekend clock rule updated.",
    );
  }


  async function clearPick(
    rewindClock: boolean,
  ) {
    if (!selectedPick) {
      return;
    }

    const pick =
      state?.selected_picks.find(
        (row) => (
          row.pick_id
          === selectedPick
        ),
      );

    const accepted =
      window.confirm(
        "Clear "
        + `${
          pick
            ? selectedPickLabel(pick)
            : selectedPick
        }? `
        + (
          rewindClock
            ? (
              "The clock will rewind "
              + "to this pick and stop."
            )
            : (
              "The current clock "
              + "position will not change."
            )
        ),
      );

    if (!accepted) {
      return;
    }

    await mutate(
      "/gateway/commissioner/"
      + "draft-tools/clear-pick",
      {
        pick_id:
          selectedPick,
        rewind_clock:
          rewindClock,
      },
      (
        rewindClock
          ? (
            "Pick cleared and "
            + "clock rewound."
          )
          : "Pick cleared."
      ),
    );
  }


  if (loading) {
    return (
      <>
        <strong>
          Draft Tools
        </strong>

        <span>
          Loading live draft controls...
        </span>
      </>
    );
  }


  if (!state) {
    return (
      <>
        <strong>
          Draft Tools
        </strong>

        <span>
          Draft Tools are unavailable.
        </span>

        {message && (
          <div className={styles.tradeNotice}>
            {message}
          </div>
        )}
      </>
    );
  }


  const canStart = (
    !state.is_running
    && state.pick_paused_at_utc
      === null
  );

  const canPause =
    state.is_running;

  const canResume = (
    state.pick_paused_at_utc
    !== null
  );


  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          flexWrap: "wrap",
        }}
      >
        <strong>
          Draft Tools
        </strong>

        <span
          style={{
            opacity: 0.72,
            fontSize: "0.78rem",
            fontWeight: 700,
          }}
        >
          {
            writeEnabled
              ? "Writes enabled"
              : "Write locked"
          }
        </span>
      </div>

      <span>
        Control the live pick position,
        draft clock, and correct mistaken
        selections.
      </span>

      <div className={styles.ptCurrent}>
        <span>
          Current pick
          <strong>
            {
              state.current_pick_id
              ?? "—"
            }
          </strong>
        </span>

        <span>
          Clock
          <strong>
            {
              state.is_running
                ? "Running"
                : (
                  state.pick_paused_at_utc
                    ? "Paused"
                    : "Stopped"
                )
            }
          </strong>
        </span>

        <span>
          Selected picks
          <strong>
            {state.selection_count}
          </strong>
        </span>

        <span>
          Open picks
          <strong>
            {state.open_pick_count}
          </strong>
        </span>
      </div>

      {!writeEnabled && (
        <div className={styles.tradeNotice}>
          Enable Commissioner Writes in
          Step 2 to use Draft Tools.
        </div>
      )}

      <div
        style={{
          display: "grid",
          gap: "18px",
        }}
      >
        <section>
          <strong>
            Set Current Pick
          </strong>

          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "minmax(220px, 1fr) auto",
              gap: "10px",
              marginTop: "8px",
            }}
          >
            <label className={styles.ptField}>
              <span>
                Live pick
              </span>

              <select
                value={targetPick}
                disabled={
                  busy
                  || !writeEnabled
                }
                onChange={(event) => {
                  setTargetPick(
                    event.target.value,
                  );
                }}
              >
                {state.open_picks.map(
                  (pick) => (
                    <option
                      key={pick.pick_id}
                      value={pick.pick_id}
                    >
                      {pickLabel(pick)}
                    </option>
                  ),
                )}
              </select>
            </label>

            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={
                busy
                || !writeEnabled
                || !targetPick
                || (
                  targetPick
                  === state.current_pick_id
                )
              }
              onClick={() => {
                void setCurrentPick();
              }}
            >
              Set Current Pick
            </button>
          </div>
        </section>

        <section>
          <strong>
            Draft Clock
          </strong>

          <div
            style={{
              display: "flex",
              gap: "8px",
              flexWrap: "wrap",
              marginTop: "8px",
            }}
          >
            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={
                busy
                || !writeEnabled
                || !canStart
              }
              onClick={() => {
                void clockAction(
                  "START",
                );
              }}
            >
              Start Draft
            </button>

            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={
                busy
                || !writeEnabled
                || !canPause
              }
              onClick={() => {
                void clockAction(
                  "PAUSE",
                );
              }}
            >
              Pause Clock
            </button>

            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={
                busy
                || !writeEnabled
                || !canResume
              }
              onClick={() => {
                void clockAction(
                  "RESUME",
                );
              }}
            >
              Resume Clock
            </button>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "12px",
              marginTop: "12px",
            }}
          >
            <div>
              <label className={styles.ptField}>
                <span>
                  Hours per pick
                </span>

                <input
                  type="number"
                  min={1}
                  max={72}
                  step={1}
                  value={durationHours}
                  disabled={
                    busy
                    || !writeEnabled
                  }
                  onChange={(event) => {
                    setDurationHours(
                      event.target.value,
                    );
                  }}
                />
              </label>

              <button
                type="button"
                className={
                  styles.writePrimaryButton
                }
                disabled={
                  busy
                  || !writeEnabled
                }
                onClick={() => {
                  void saveDuration();
                }}
              >
                Save Duration
              </button>
            </div>

            <div>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  marginBottom: "8px",
                }}
              >
                <input
                  type="checkbox"
                  checked={weekendsCount}
                  disabled={
                    busy
                    || !writeEnabled
                  }
                  onChange={(event) => {
                    setWeekendsCount(
                      event.target.checked,
                    );
                  }}
                />

                Count weekends toward
                the draft clock
              </label>

              <button
                type="button"
                className={
                  styles.writePrimaryButton
                }
                disabled={
                  busy
                  || !writeEnabled
                  || (
                    weekendsCount
                    === state.weekends_count
                  )
                }
                onClick={() => {
                  void saveWeekendRule();
                }}
              >
                Save Weekend Rule
              </button>
            </div>
          </div>
        </section>

        <section>
          <strong>
            Fix a Mistake
          </strong>

          {state.selected_picks.length === 0 ? (
            <div className={styles.tradeNotice}>
              No completed draft picks
              are available to correct.
            </div>
          ) : (
            <>
              <label className={styles.ptField}>
                <span>
                  Completed pick
                </span>

                <select
                  value={selectedPick}
                  disabled={
                    busy
                    || !writeEnabled
                  }
                  onChange={(event) => {
                    setSelectedPick(
                      event.target.value,
                    );
                  }}
                >
                  {state.selected_picks.map(
                    (pick) => (
                      <option
                        key={pick.pick_id}
                        value={pick.pick_id}
                      >
                        {
                          selectedPickLabel(
                            pick,
                          )
                        }
                      </option>
                    ),
                  )}
                </select>
              </label>

              <div
                style={{
                  display: "flex",
                  gap: "8px",
                  flexWrap: "wrap",
                  marginTop: "8px",
                }}
              >
                <button
                  type="button"
                  className={
                    styles.writePrimaryButton
                  }
                  disabled={
                    busy
                    || !writeEnabled
                    || !selectedPick
                  }
                  onClick={() => {
                    void clearPick(true);
                  }}
                >
                  Clear + Rewind
                </button>

                <button
                  type="button"
                  className={
                    styles.writePrimaryButton
                  }
                  disabled={
                    busy
                    || !writeEnabled
                    || !selectedPick
                  }
                  onClick={() => {
                    void clearPick(false);
                  }}
                >
                  Clear Without Rewind
                </button>
              </div>
            </>
          )}
        </section>
      </div>

      {message && (
        <div className={styles.tradeNotice}>
          {message}
        </div>
      )}
    </>
  );
}
