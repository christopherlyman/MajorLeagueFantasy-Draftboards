"use client";

import {
  useCallback,
  useEffect,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type DraftToolsState = {
  draft_key: string;
  draft_status: string;
  current_pick_id: string | null;
  is_running: boolean;
  selection_count: number;
  open_pick_count: number;
};


type WriteStatus = {
  write_enabled: boolean;
  user_id: number | null;
  email: string | null;
  authority: string;
  must_change_password: boolean;
};


type ResetResponse = {
  action: string;
  result: string;
  cleared_selection_count: number;
  performed_by: string;
  state: DraftToolsState;
};


const REQUIRED_CONFIRMATION =
  "RESET DRAFT";


export function CommissionerRecoveryDangerZone() {
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
    confirmation,
    setConfirmation,
  ] = useState("");

  const [
    busy,
    setBusy,
  ] = useState(false);

  const [
    message,
    setMessage,
  ] = useState("");

  const [
    loading,
    setLoading,
  ] = useState(true);


  const loadState =
    useCallback(
      async (): Promise<void> => {
        const response = await fetch(
          "/gateway/commissioner/draft-tools",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          throw new Error(
            "Draft recovery state "
            + "is unavailable.",
          );
        }

        const body =
          await response.json();

        setState(
          body as DraftToolsState,
        );
      },
      [],
    );


  const loadWriteStatus =
    useCallback(
      async (): Promise<WriteStatus | null> => {
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

          setWriteStatus(
            nextStatus,
          );

          return nextStatus;

        } catch {
          return null;
        }
      },
      [],
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
                "Draft recovery state "
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

  const confirmationMatches = (
    confirmation
    === REQUIRED_CONFIRMATION
  );


  async function resetDraft() {
    if (
      busy
      || !confirmationMatches
    ) {
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

    const selectedCount =
      state?.selection_count
      ?? 0;

    const accepted =
      window.confirm(
        "RESET THE ENTIRE DRAFT?\n\n"
        + "This will permanently clear "
        + `${selectedCount} real draft `
        + "selection"
        + (
          selectedCount === 1
            ? ""
            : "s"
        )
        + ", rebuild the QO ladder, "
        + "stop the clock, and return "
        + "the draft to the first "
        + "non-keeper pick.\n\n"
        + "This cannot be undone "
        + "from the DraftBoard.",
      );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "recovery/reset-draft",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            confirmation:
              REQUIRED_CONFIRMATION,
          }),
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
          "Draft reset was rejected. "
          + "Refresh the page and "
          + "check the current state.",
        );
      }

      const result =
        body as ResetResponse;

      setState(
        result.state,
      );

      setConfirmation("");

      setMessage(
        "Draft reset complete. "
        + `${result.cleared_selection_count} `
        + "real selections were cleared.",
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Draft reset failed.",
      );

      try {
        await loadState();
      } catch {
        // Preserve original reset error.
      }

    } finally {
      setBusy(false);
    }
  }


  if (loading) {
    return (
      <p className={styles.toolIntro}>
        Loading protected recovery state...
      </p>
    );
  }


  return (
    <>
      <p className={styles.toolIntro}>
        Use this only when the entire
        live draft must be restarted.
        Individual pick corrections belong
        in Step 7.
      </p>

      {state && (
        <div className={styles.ptCurrent}>
          <span>
            Real selections
            <strong>
              {state.selection_count}
            </strong>
          </span>

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
                  : "Stopped"
              }
            </strong>
          </span>
        </div>
      )}

      <div className={styles.tradeNotice}>
        Reset Draft clears every real draft
        selection, rebuilds the QO ladder,
        stops the draft clock, and returns
        the current pick to the first
        non-keeper slot. Contracts, Prospect
        Tags, keeper assignments, and the
        draft order are not deleted.
      </div>

      {!writeEnabled && (
        <div className={styles.tradeNotice}>
          Enable Commissioner Writes in
          Step 2 before using recovery
          operations.
        </div>
      )}

      <label className={styles.ptField}>
        <span>
          Type RESET DRAFT to confirm
        </span>

        <input
          type="text"
          value={confirmation}
          autoComplete="off"
          disabled={
            busy
            || !writeEnabled
          }
          placeholder="RESET DRAFT"
          onChange={(event) => {
            setConfirmation(
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
          || !confirmationMatches
        }
        onClick={() => {
          void resetDraft();
        }}
      >
        {
          busy
            ? "Resetting..."
            : "RESET DRAFT"
        }
      </button>

      {message && (
        <div className={styles.tradeNotice}>
          {message}
        </div>
      )}
    </>
  );
}
