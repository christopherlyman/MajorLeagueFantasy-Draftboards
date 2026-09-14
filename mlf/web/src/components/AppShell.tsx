import type { ReactNode } from "react";
import Link from "next/link";

import styles from "../app/page.module.css";

type AppShellProps = {
  activePath: string;
  title: string;
  subtitle: ReactNode;
  badge?: string;
  children: ReactNode;
};

const NAV_ITEMS = [
  {
    label: "Draft Board",
    href: "/",
    enabled: true,
  },
  {
    label: "Available Players",
    href: "/available-players",
    enabled: true,
  },
  {
    label: "Teams",
    href: "/teams",
    enabled: true,
  },
  {
    label: "QOs",
    href: "/qos",
    enabled: true,
  },
  {
    label: "Draft Lottery",
    href: "/draft-lottery",
    enabled: true,
  },
  {
    label: "Pick Tracker",
    href: "/pick-tracker",
    enabled: true,
  },
  {
    label: "Draft Statistics",
    href: "/draft-statistics",
    enabled: true,
  },
] as const;

export function AppShell({
  activePath,
  title,
  subtitle,
  badge = "Read-only preview",
  children,
}: AppShellProps) {
  return (
    <main className={styles.appShell}>
      <header className={styles.appHeader}>
        <div className={styles.brand}>
          <div className={styles.brandMark}>
            MLF
          </div>

          <div>
            <div className={styles.brandTitle}>
              Major League Fantasy
            </div>

            <div className={styles.brandSubtitle}>
              Draft Board
            </div>
          </div>
        </div>

        <div className={styles.previewBadge}>
          Next.js Preview
        </div>
      </header>

      <nav
        className={styles.tabs}
        aria-label="MLF sections"
      >
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === activePath;

          if (!item.enabled) {
            return (
              <span
                key={item.href}
                className={styles.tabMuted}
                aria-disabled="true"
              >
                {item.label}
              </span>
            );
          }

          return (
            <Link
              key={item.href}
              href={item.href}
              className={
                active
                  ? `${styles.tab} ${styles.activeTab}`
                  : styles.tabMuted
              }
              aria-current={
                active
                  ? "page"
                  : undefined
              }
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      <section className={styles.content}>
        <div className={styles.titleRow}>
          <div>
            <h1 className={styles.title}>
              {title}
            </h1>

            <p className={styles.subtitle}>
              {subtitle}
            </p>
          </div>

          <div className={styles.readOnly}>
            {badge}
          </div>
        </div>

        {children}
      </section>
    </main>
  );
}