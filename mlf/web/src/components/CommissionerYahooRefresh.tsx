"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type WriteStatus = {
  write_enabled: boolean;
  user_id: number | null;
  email: string | null;
  authority: string;
  must_change_password: boolean;
};


type RefreshReceipt = {
  finished_at_utc: string;
  duration_sec: number;
  players_before: number;
  players_after: number;
  meta_updated_last_10m: number;
  stats_season: number;
  performed_by: string;
};


function errorCode(
  body: unknown,
): string {
  if (
    typeof body === "object"
    && body !== null
    && "detail" in body
  ) {
    const detail = (
      body as {
        detail?: unknown;
      }
    ).detail;

    if (
      typeof detail === "object"
      && detail !== null
      && "code" in detail
    ) {
      const code = (
        detail as {
          code?: unknown;
        }
      ).code;

      if (typeof code === "string") {
        return code;
      }
    }
  }

  return "";
}


export function CommissionerYahooRefresh() {
  const [
    status,
    setStatus,
  ] = useState<WriteStatus | null>(null);

  const [
    checking,
    setChecking,
  ] = useState(true);

  const [
    email,
    setEmail,
  ] = useState("");

  const [
    password,
    setPassword,
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
    receipt,
    setReceipt,
  ] = useState<RefreshReceipt | null>(null);

  useEffect(() => {
    let active = true;

    async function loadStatus() {
      try {
        const response = await fetch(
          "/gateway/commissioner/write-status",
          {
            method: "GET",
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        const body = await response.json();

        if (
          active
          && response.ok
        ) {
          setStatus(
            body as WriteStatus,
          );
        }
      } catch {
        if (active) {
          setMessage(
            "Write authorization status is temporarily unavailable.",
          );
        }
      } finally {
        if (active) {
          setChecking(false);
        }
      }
    }

    void loadStatus();

    return () => {
      active = false;
    };
  }, []);

  async function login(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    setBusy(true);
    setMessage("");
    setReceipt(null);

    try {
      const response = await fetch(
        "/gateway/commissioner/write-login",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({
            email,
            password,
          }),
        },
      );

      const body = await response.json();

      if (!response.ok) {
        const code = errorCode(body);

        if (code === "login_rate_limited") {
          throw new Error(
            "Too many failed attempts. Wait 10 minutes and try again.",
          );
        }

        if (
          code
          === "password_change_required"
        ) {
          throw new Error(
            "This account must change its temporary password before Commissioner writes can be enabled.",
          );
        }

        if (
          code
          === "commissioner_write_required"
        ) {
          throw new Error(
            "This account is not authorized for Commissioner writes.",
          );
        }

        throw new Error(
          "Email or password was not accepted.",
        );
      }

      setStatus(
        body as WriteStatus,
      );
      setPassword("");
      setMessage(
        "Commissioner writes enabled.",
      );

      window.dispatchEvent(
        new Event(
          "mlf-commissioner-write-status-changed",
        ),
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Unable to enable Commissioner writes.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setMessage("");
    setReceipt(null);

    try {
      const response = await fetch(
        "/gateway/commissioner/write-logout",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type": "application/json",
          },
          body: "{}",
        },
      );

      const body = await response.json();

      if (!response.ok) {
        throw new Error(
          "Unable to clear the write identity.",
        );
      }

      setStatus(
        body as WriteStatus,
      );
      setMessage(
        "Commissioner writes disabled.",
      );

      window.dispatchEvent(
        new Event(
          "mlf-commissioner-write-status-changed",
        ),
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Unable to clear the write identity.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function refreshYahoo() {
    const confirmed = window.confirm(
      "Refresh the MLF Yahoo player universe now? "
      + "This updates Yahoo-backed player metadata "
      + "and prior-year statistics in PostgreSQL. "
      + "Draft picks, contracts, QOs, and prospect "
      + "tags are not part of this operation.",
    );

    if (!confirmed) {
      return;
    }

    setBusy(true);
    setMessage(
      "Refreshing Yahoo player universe. This can take several minutes.",
    );
    setReceipt(null);

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "yahoo-player-universe/refresh",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type": "application/json",
          },
          body: "{}",
        },
      );

      const body = await response.json();

      if (!response.ok) {
        const code = errorCode(body);

        if (
          code
          === "yahoo_refresh_in_progress"
        ) {
          throw new Error(
            "A Yahoo refresh is already running.",
          );
        }

        if (
          response.status === 401
          || code
            === "commissioner_write_required"
        ) {
          setStatus(
            (current) => current
              ? {
                  ...current,
                  write_enabled: false,
                }
              : current,
          );

          throw new Error(
            "Commissioner write authorization expired. Sign in again.",
          );
        }

        throw new Error(
          "Yahoo refresh failed. Existing draft state was not intentionally changed by this control.",
        );
      }

      const nextReceipt =
        body as RefreshReceipt;

      setReceipt(nextReceipt);
      setMessage(
        "Yahoo player universe refresh completed.",
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Yahoo refresh failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  const enabled =
    status?.write_enabled === true;

  return (
    <>
      <div className={styles.stepTitleRow}>
        <strong>
          Refresh Yahoo Player Universe
        </strong>

        <span
          className={
            enabled
              ? styles.stepReady
              : styles.writeLockedBadge
          }
        >
          {enabled
            ? "Write enabled"
            : "Write locked"}
        </span>
      </div>

      <span>
        Refresh Yahoo-backed player metadata and
        prior-year statistics used by the
        DraftBoard.
      </span>

      <div className={styles.writePanel}>
        <div className={styles.writePanelHeader}>
          <div>
            <strong>
              Commissioner Write Identity
            </strong>

            <p>
              The private Commissioner link opens
              this workspace. A named MLF account
              is additionally required for
              mutations.
            </p>
          </div>
        </div>

        {checking ? (
          <p className={styles.writeMuted}>
            Checking write authorization…
          </p>
        ) : enabled ? (
          <div className={styles.writeIdentityRow}>
            <div>
              <span className={styles.writeMuted}>
                Authorized as
              </span>

              <strong>
                {status?.email}
              </strong>

              <span className={styles.writeAuthority}>
                {status?.authority === "site_admin"
                  ? "Site administrator"
                  : "League commissioner"}
              </span>
            </div>

            <button
              type="button"
              className={styles.writeSecondaryButton}
              disabled={busy}
              onClick={() => void logout()}
            >
              Disable writes
            </button>
          </div>
        ) : (
          <form
            className={styles.writeLoginForm}
            onSubmit={login}
          >
            <div className={styles.writeField}>
              <label htmlFor="commissioner-write-email">
                MLF email
              </label>

              <input
                id="commissioner-write-email"
                type="email"
                autoComplete="username"
                value={email}
                disabled={busy}
                onChange={(event) => {
                  setEmail(event.target.value);
                }}
                required
              />
            </div>

            <div className={styles.writeField}>
              <label htmlFor="commissioner-write-password">
                Password
              </label>

              <input
                id="commissioner-write-password"
                type="password"
                autoComplete="current-password"
                value={password}
                disabled={busy}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
                required
              />
            </div>

            <button
              type="submit"
              className={styles.writePrimaryButton}
              disabled={busy}
            >
              {busy
                ? "Checking…"
                : "Enable Commissioner Writes"}
            </button>
          </form>
        )}

        <div className={styles.yahooAction}>
          <div>
            <strong>
              Yahoo refresh
            </strong>

            <p>
              Uses the existing MLF Yahoo loader.
              The 2026 DraftBoard refreshes
              2025 statistical context.
            </p>
          </div>

          <button
            type="button"
            className={styles.writePrimaryButton}
            disabled={
              busy
              || !enabled
            }
            onClick={() => {
              void refreshYahoo();
            }}
          >
            {busy && enabled
              ? "Refreshing…"
              : "Run Refresh Now"}
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

        {receipt && (
          <div className={styles.refreshReceipt}>
            <div>
              <span>
                Players
              </span>

              <strong>
                {receipt.players_before}
                {" → "}
                {receipt.players_after}
              </strong>
            </div>

            <div>
              <span>
                Meta updated
              </span>

              <strong>
                {receipt.meta_updated_last_10m}
              </strong>
            </div>

            <div>
              <span>
                Stats season
              </span>

              <strong>
                {receipt.stats_season}
              </strong>
            </div>

            <div>
              <span>
                Duration
              </span>

              <strong>
                {receipt.duration_sec}s
              </strong>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
