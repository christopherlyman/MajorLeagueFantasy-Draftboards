import Link from "next/link";

import {
  cookies,
} from "next/headers";

import {
  CopyManagerLinkButton,
} from "../../components/CopyManagerLinkButton";

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
      <main className={styles.page}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>
              MLF Commissioner
            </p>

            <h1>
              Manager Access
            </h1>

            <p className={styles.subtitle}>
              Commissioner authorization is
              required to view private manager
              credentials.
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
      <main className={styles.page}>
        <section className={styles.notice}>
          <strong>
            Manager links are temporarily
            unavailable.
          </strong>
        </section>
      </main>
    );
  }

  const links = linksResult.body;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>
            MLF Commissioner
          </p>

          <h1>
            Manager Access
          </h1>

          <p className={styles.subtitle}>
            Private current-season manager
            gateway links. Give each manager
            only their own link.
          </p>
        </div>

        <div className={styles.headerActions}>
          <Link
            href="/"
            className={styles.secondaryButton}
          >
            Draft Board
          </Link>

          <a
            href="/gateway/commissioner/clear?next=%2F"
            className={styles.secondaryButton}
          >
            Clear Commissioner Access
          </a>
        </div>
      </header>

      <section className={styles.summary}>
        <div>
          <span>
            League
          </span>

          <strong>
            {auth.body?.league_key}
          </strong>
        </div>

        <div>
          <span>
            Season
          </span>

          <strong>
            {auth.body?.season_year}
          </strong>
        </div>

        <div>
          <span>
            Manager Links
          </span>

          <strong>
            {links.length}
          </strong>
        </div>
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div>
            <h2>
              Manager Team Links
            </h2>

            <p>
              Each URL is a private bearer
              credential for one MLF team.
            </p>
          </div>
        </div>

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
      </section>
    </main>
  );
}
