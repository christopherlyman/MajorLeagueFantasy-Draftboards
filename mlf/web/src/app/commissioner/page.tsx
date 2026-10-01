import Link from "next/link";

import {
  cookies,
} from "next/headers";

import {
  AppShell,
} from "../../components/AppShell";

import {
  CopyManagerLinkButton,
} from "../../components/CopyManagerLinkButton";

import {
  CommissionerYahooRefresh,
} from "../../components/CommissionerYahooRefresh";

import {
  CommissionerTradeBuilder,
} from "../../components/CommissionerTradeBuilder";

import {
  CommissionerQualifyingOffers,
} from "../../components/CommissionerQualifyingOffers";

import {
  CommissionerProspectTags,
} from "../../components/CommissionerProspectTags";

import styles from "./page.module.css";


export const dynamic = "force-dynamic";


type CommissionerPrincipal = {
  is_authenticated: boolean;
  role: "public" | "commissioner";
  league_key: string;
  season_year: number;
  display_name: string;
  acting_as: string;
};


type ManagerGatewayLink = {
  franchise_id: number;
  team_key: string;
  team_name: string;
  owner_name: string | null;
  is_active: boolean;
  claim_count: number;
  last_claimed_at_utc: string | null;
  manager_url: string;
};


type DraftOrderState = {
  draft_key: string;
  status: string;
  manager_count: number;
  draft_order_mode: string;
  first_standard_round: number;
  selection_count: number;
  can_rebase: boolean;
  lock_reason: string | null;
  slots: Array<{
    slot_number: number;
    team_key: string;
    team_name: string;
  }>;
};


type ApiResult<T> = {
  status: number;
  body: T | null;
};


function apiBase(): string {
  return (
    process.env.MLF_API_INTERNAL_URL
    ?? "http://mlf_api:8000"
  ).replace(/\/+$/, "");
}


async function apiGet<T>(
  path: string,
  cookieHeader: string,
): Promise<ApiResult<T>> {
  const response = await fetch(
    `${apiBase()}${path}`,
    {
      method: "GET",
      headers: cookieHeader
        ? {
            cookie: cookieHeader,
          }
        : undefined,
      cache: "no-store",
    },
  );

  let body: T | null = null;

  try {
    body = await response.json() as T;
  } catch {
    body = null;
  }

  return {
    status: response.status,
    body,
  };
}


function formatClaimed(
  value: string | null,
): string {
  if (!value) {
    return "Never";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      timeZone: "America/New_York",
      dateStyle: "medium",
      timeStyle: "short",
    },
  ).format(date);
}


export default async function CommissionerPage() {
  const cookieStore = await cookies();

  const cookieHeader = cookieStore
    .getAll()
    .map(
      ({ name, value }) =>
        `${name}=${value}`,
    )
    .join("; ");

  const auth = await apiGet<CommissionerPrincipal>(
    "/commissioner/auth/me",
    cookieHeader,
  );

  const authorized = (
    auth.status === 200
    && auth.body?.is_authenticated === true
    && auth.body.role === "commissioner"
  );

  if (!authorized) {
    return (
      <main className={styles.deniedPage}>
        <header className={styles.deniedHeader}>
          <div>
            <p className={styles.eyebrow}>
              MLF Commissioner
            </p>

            <h1>
              Commissioner Tools
            </h1>

            <p className={styles.deniedSubtitle}>
              Commissioner authorization is
              required to access this workspace.
            </p>
          </div>

          <Link
            href="/"
            className={styles.secondaryButton}
          >
            Draft Board
          </Link>
        </header>

        <section className={styles.notice}>
          <strong>
            Commissioner access required
          </strong>

          <p>
            Open the private commissioner link
            for this browser, then return here.
          </p>
        </section>
      </main>
    );
  }

  const linksResult =
    await apiGet<ManagerGatewayLink[]>(
      "/commissioner/manager-links",
      cookieHeader,
    );

  if (
    linksResult.status !== 200
    || !Array.isArray(linksResult.body)
  ) {
    return (
      <AppShell
        title="Commissioner Tools"
        subtitle="MLF commissioner administration"
        activePath="/commissioner"
        badge="Commissioner"
      >
        <section className={styles.notice}>
          <strong>
            Commissioner data is temporarily
            unavailable.
          </strong>
        </section>
      </AppShell>
    );
  }

  const links = linksResult.body;

  const draftOrderResult =
    await apiGet<DraftOrderState>(
      "/commissioner/draft-order",
      cookieHeader,
    );

  const draftOrder = (
    draftOrderResult.status === 200
    && draftOrderResult.body
  )
    ? draftOrderResult.body
    : null;

  const activeLinks = links.filter(
    (row) => row.is_active,
  ).length;

  const claimedLinks = links.filter(
    (row) => row.claim_count > 0,
  ).length;

  const managerAccessReady = (
    links.length === 16
    && activeLinks === 16
  );

  return (
    <AppShell
      title="Commissioner Tools"
      subtitle={
        "Commissioner readiness, annual workflow, manager access, and league operations"
      }
      activePath="/commissioner"
      badge="Commissioner"
    >
      <div className={styles.topActions}>
        <a
          href="/gateway/commissioner/clear?next=%2F"
          className={styles.secondaryButton}
        >
          Clear Commissioner Access
        </a>
      </div>

      <section className={styles.readiness}>
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>
              Commissioner Readiness
            </p>

            <h2>
              Current League State
            </h2>
          </div>

          <span
            className={
              managerAccessReady
                ? styles.readyBadge
                : styles.actionBadge
            }
          >
            {managerAccessReady
              ? "Manager Access Ready"
              : "Action Needed"}
          </span>
        </div>

        <div className={styles.readinessGrid}>
          <div className={styles.metric}>
            <span>
              League
            </span>

            <strong>
              {auth.body?.league_key}
            </strong>
          </div>

          <div className={styles.metric}>
            <span>
              Season
            </span>

            <strong>
              {auth.body?.season_year}
            </strong>
          </div>

          <div className={styles.metric}>
            <span>
              Active Links
            </span>

            <strong>
              {activeLinks} / {links.length}
            </strong>
          </div>

          <div className={styles.metric}>
            <span>
              Claimed
            </span>

            <strong>
              {claimedLinks} / {links.length}
            </strong>
          </div>
        </div>

        <div className={styles.nextAction}>
          <strong>
            Next action
          </strong>

          <p>
            {managerAccessReady
              ? "Manager access is configured. Continue through the Commissioner workflow below."
              : "Review Manager Access and resolve inactive or missing team links before continuing."}
          </p>
        </div>
      </section>

      <section className={styles.workflow}>
        <div className={styles.workflowHeading}>
          <h2>
            Annual Commissioner Workflow
          </h2>

          <p>
            Normal administration is kept
            separate from correction, recovery,
            and destructive operations.
          </p>
        </div>

        <details className={styles.tool}>
          <summary>
            <span>
              1. Manager Access
            </span>

            <span className={styles.toolStatus}>
              {activeLinks} active
            </span>
          </summary>

          <div className={styles.toolBody}>
            <p className={styles.toolIntro}>
              Private current-season manager
              gateway links. Give each manager
              only their own link.
            </p>

            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>
                      Team
                    </th>
                    <th>
                      Manager
                    </th>
                    <th>
                      Status
                    </th>
                    <th>
                      Claims
                    </th>
                    <th>
                      Last Claimed
                    </th>
                    <th>
                      Manager Link
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {links.map((row) => (
                    <tr key={row.team_key}>
                      <td>
                        <strong>
                          {row.team_name}
                        </strong>

                        <small>
                          {row.team_key}
                        </small>
                      </td>

                      <td>
                        {row.owner_name ?? "\u2014"}
                      </td>

                      <td>
                        <span
                          className={
                            row.is_active
                              ? styles.active
                              : styles.inactive
                          }
                        >
                          {row.is_active
                            ? "Active"
                            : "Inactive"}
                        </span>
                      </td>

                      <td>
                        {row.claim_count}
                      </td>

                      <td>
                        {formatClaimed(
                          row.last_claimed_at_utc,
                        )}
                      </td>

                      <td>
                        <div
                          className={
                            styles.linkCell
                          }
                        >
                          <CopyManagerLinkButton
                            url={row.manager_url}
                            className={
                              styles.copyButton
                            }
                          />

                          <code
                            className={
                              styles.managerUrl
                            }
                          >
                            {row.manager_url}
                          </code>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </details>

        <details className={styles.tool}>
          <summary>
            <span>
              2. Draft Preparation
            </span>

            <span className={styles.toolStatus}>
              Views available
            </span>
          </summary>

          <div className={styles.toolBody}>
            <p className={styles.toolIntro}>
              Review QOs and the draft lottery
              before draft operations.
            </p>

            <div className={styles.toolLinks}>
              <Link
                href="/qos"
                className={styles.secondaryButton}
              >
                QOs
              </Link>

              <Link
                href="/draft-lottery"
                className={styles.secondaryButton}
              >
                Draft Lottery
              </Link>
            </div>
          </div>
        </details>

        <details className={styles.tool}>
          <summary>
            <span>
              3. Draft Operations
            </span>

            <span className={styles.toolStatus}>
              Live views
            </span>
          </summary>

          <div className={styles.toolBody}>
            <p className={styles.toolIntro}>
              Monitor the active board, pick
              history, and draft statistics.
            </p>

            <div className={styles.toolLinks}>
              <Link
                href="/"
                className={styles.secondaryButton}
              >
                Draft Board
              </Link>

              <Link
                href="/pick-tracker"
                className={styles.secondaryButton}
              >
                Pick Tracker
              </Link>

              <Link
                href="/draft-statistics"
                className={styles.secondaryButton}
              >
                Draft Statistics
              </Link>
            </div>
          </div>
        </details>

        <section className={styles.pendingOperations}>
          <div className={styles.operationsHeading}>
            <div>
              <p className={styles.eyebrow}>
                Operational Sequence
              </p>

              <h2>
                Commissioner Operations
              </h2>

              <p>
                Work through these steps in order.
                Each step will contain its controls
                here as its authoritative API
                boundary is migrated.
              </p>
            </div>
          </div>

          <ol className={styles.operationSteps}>
            <li
              className={`${styles.operationStep} ${styles.operationStepExpanded}`}
            >
              <span className={styles.stepNumber}>
                1
              </span>

              <div className={styles.stepContent}>
                <div className={styles.stepTitleRow}>
                  <strong>
                    Set Draft Order
                  </strong>

                  {draftOrder && (
                    <span
                      className={
                        draftOrder.can_rebase
                          ? styles.stepReady
                          : styles.stepLocked
                      }
                    >
                      {draftOrder.can_rebase
                        ? "Ready"
                        : "Locked"}
                    </span>
                  )}
                </div>

                <span>
                  Establish and verify the saved
                  draft slot order.
                </span>

                {draftOrder ? (
                  <div className={styles.stepPanel}>
                    <div className={styles.stepMeta}>
                      <span>
                        Draft
                        <strong>
                          {draftOrder.draft_key}
                        </strong>
                      </span>

                      <span>
                        Status
                        <strong>
                          {draftOrder.status}
                        </strong>
                      </span>

                      <span>
                        Order
                        <strong>
                          {draftOrder.draft_order_mode}
                        </strong>
                      </span>

                      <span>
                        Selections
                        <strong>
                          {draftOrder.selection_count}
                        </strong>
                      </span>
                    </div>

                    {!draftOrder.can_rebase && (
                      <div className={styles.lockNotice}>
                        {draftOrder.lock_reason
                          ?? "Draft order is locked."}
                      </div>
                    )}

                    <ol className={styles.orderGrid}>
                      {draftOrder.slots.map(
                        (slot) => (
                          <li
                            key={slot.team_key}
                            className={styles.orderSlot}
                          >
                            <span
                              className={
                                styles.orderSlotNumber
                              }
                            >
                              {slot.slot_number}
                            </span>

                            <span
                              className={
                                styles.orderSlotName
                              }
                            >
                              {slot.team_name}
                            </span>
                          </li>
                        ),
                      )}
                    </ol>
                  </div>
                ) : (
                  <div className={styles.stepUnavailable}>
                    Draft-order state is temporarily
                    unavailable.
                  </div>
                )}
              </div>
            </li>

            <li
              className={`${styles.operationStep} ${styles.operationStepExpanded}`}
            >
              <span className={styles.stepNumber}>
                2
              </span>

              <div className={styles.stepContent}>
                <CommissionerYahooRefresh />
              </div>
            </li>

            <li
              className={`${styles.operationStep} ${styles.operationStepExpanded}`}
            >
              <span className={styles.stepNumber}>
                3
              </span>

              <div className={styles.stepContent}>
                <CommissionerTradeBuilder />
              </div>
            </li>

            <li
              className={`${styles.operationStep} ${styles.operationStepExpanded}`}
            >
              <span className={styles.stepNumber}>
                4
              </span>

              <div className={styles.stepContent}>
                <CommissionerQualifyingOffers />
              </div>
            </li>

            <li
              className={`${styles.operationStep} ${styles.operationStepExpanded}`}
            >
              <span className={styles.stepNumber}>
                5
              </span>

              <div className={styles.stepContent}>
                <CommissionerProspectTags />
              </div>
            </li>

            <li className={styles.operationStep}>
              <span className={styles.stepNumber}>
                6
              </span>

              <div className={styles.stepContent}>
                <strong>
                  Contract Overrides
                </strong>

                <span>
                  Handle Commissioner contract
                  corrections and exceptions.
                </span>
              </div>
            </li>

            <li className={styles.operationStep}>
              <span className={styles.stepNumber}>
                7
              </span>

              <div className={styles.stepContent}>
                <strong>
                  Draft Tools
                </strong>

                <span>
                  Run live-draft controls and
                  Commissioner corrections.
                </span>
              </div>
            </li>
          </ol>
        </section>

        <details
          className={`${styles.tool} ${styles.dangerTool}`}
        >
          <summary>
            <span>
              Recovery / Danger Zone
            </span>

            <span className={styles.toolStatus}>
              Protected
            </span>
          </summary>

          <div className={styles.toolBody}>
            <p className={styles.toolIntro}>
              Destructive and recovery actions
              remain unavailable here until their
              authenticated mutation boundaries
              are migrated and proven.
            </p>
          </div>
        </details>
      </section>
    </AppShell>
  );
}
