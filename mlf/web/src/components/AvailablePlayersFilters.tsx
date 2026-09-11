"use client";

import {
  useEffect,
  useRef,
  useState,
  useTransition,
} from "react";

import {
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation";

import styles from "../app/available-players/page.module.css";

type SortOption = {
  key: string;
  label: string;
};

type Props = {
  query: string;
  positions: string[];
  selectedPositions: string[];

  sortKey: string;
  sortOptions: SortOption[];
  descending: boolean;

  showAll: boolean;
  showQo: boolean;
  showPoach: boolean;
  showPt: boolean;
  showContracts: boolean;
};

export function AvailablePlayersFilters({
  query,
  positions,
  selectedPositions,
  sortKey,
  sortOptions,
  descending,
  showAll,
  showQo,
  showPoach,
  showPt,
  showContracts,
}: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [pending, startTransition] =
    useTransition();

  const [searchValue, setSearchValue] =
    useState(query);

  const searchTimer =
    useRef<ReturnType<typeof setTimeout> | null>(
      null,
    );

  useEffect(() => {
    setSearchValue(query);
  }, [query]);

  function currentParams(): URLSearchParams {
    const next =
      new URLSearchParams(
        searchParams.toString(),
      );

    const trimmed =
      searchValue.trim();

    if (trimmed) {
      next.set("q", trimmed);
    } else {
      next.delete("q");
    }

    return next;
  }

  function navigate(
    next: URLSearchParams,
  ): void {
    const qs = next.toString();

    startTransition(() => {
      router.replace(
        qs
          ? `${pathname}?${qs}`
          : pathname,
        {
          scroll: false,
        },
      );
    });
  }

  function toggleMulti(
    key: string,
    value: string,
  ): void {
    const next = currentParams();

    const existing =
      next.getAll(key);

    const currentlySelected =
      existing.includes(value);

    const updated =
      currentlySelected
        ? existing.filter(
            (item) => item !== value,
          )
        : [...existing, value];

    next.delete(key);

    for (const item of updated) {
      next.append(key, item);
    }

    navigate(next);
  }

  function toggleFlag(
    key: string,
    enabled: boolean,
  ): void {
    const next = currentParams();

    if (enabled) {
      next.set(key, "1");
    } else {
      next.delete(key);
    }

    navigate(next);
  }

  function updateSort(
    value: string,
  ): void {
    const next = currentParams();

    if (value === "rank") {
      next.delete("sort");
    } else {
      next.set("sort", value);
    }

    navigate(next);
  }

  function updateSearch(
    value: string,
  ): void {
    setSearchValue(value);

    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
    }

    searchTimer.current =
      setTimeout(() => {
        const next =
          new URLSearchParams(
            searchParams.toString(),
          );

        const trimmed =
          value.trim();

        if (trimmed) {
          next.set("q", trimmed);
        } else {
          next.delete("q");
        }

        navigate(next);
      }, 300);
  }

  function reset(): void {
    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
    }

    setSearchValue("");

    startTransition(() => {
      router.replace(
        pathname,
        {
          scroll: false,
        },
      );
    });
  }

  const selected =
    new Set(selectedPositions);

  const advancedActive =
    selected.size > 0 ||
    showAll ||
    showQo ||
    showPoach ||
    showPt ||
    showContracts;

  const statusButtons = [
    {
      key: "all",
      label: "All players",
      active: showAll,
    },
    {
      key: "qo",
      label: "QOs",
      active: showQo,
    },
    {
      key: "poach",
      label: "Poach eligible",
      active: showPoach,
    },
    {
      key: "pt",
      label: "PT",
      active: showPt,
    },
    {
      key: "contracts",
      label: "Contracts",
      active: showContracts,
    },
  ] as const;

  return (
    <>
      <div className={styles.desktopFilters}>
        <div className={styles.desktopFilterTop}>
          <label className={styles.field}>
            <span>Search</span>

            <input
              type="search"
              value={searchValue}
              onChange={(event) =>
                updateSearch(
                  event.target.value,
                )
              }
              placeholder="Type a player name..."
            />
          </label>

          <label className={styles.field}>
            <span>Sort by</span>

            <select
              value={sortKey}
              onChange={(event) =>
                updateSort(
                  event.target.value,
                )
              }
            >
              {sortOptions.map(
                (option) => (
                  <option
                    key={option.key}
                    value={option.key}
                  >
                    {option.label}
                  </option>
                ),
              )}
            </select>
          </label>

          <label className={styles.simpleCheck}>
            <input
              type="checkbox"
              checked={descending}
              onChange={(event) =>
                toggleFlag(
                  "desc",
                  event.target.checked,
                )
              }
            />

            <span>Descending</span>
          </label>

          <button
            type="button"
            className={styles.resetButton}
            onClick={reset}
          >
            Reset
          </button>
        </div>

        <div className={styles.filterGroups}>
          <div className={styles.filterGroup}>
            <div className={styles.filterLabel}>
              Position
            </div>

            <div className={styles.chipRow}>
              {positions.map(
                (position) => (
                  <button
                    key={position}
                    type="button"
                    className={
                      selected.has(position)
                        ? `${styles.filterChip} ${styles.filterChipActive}`
                        : styles.filterChip
                    }
                    aria-pressed={
                      selected.has(position)
                    }
                    onClick={() =>
                      toggleMulti(
                        "pos",
                        position,
                      )
                    }
                  >
                    {position}
                  </button>
                ),
              )}
            </div>
          </div>

          <div className={styles.filterGroup}>
            <div className={styles.filterLabel}>
              Status
            </div>

            <div className={styles.chipRow}>
              {statusButtons.map(
                (item) => (
                  <button
                    key={item.key}
                    type="button"
                    className={
                      item.active
                        ? `${styles.filterChip} ${styles.filterChipActive}`
                        : styles.filterChip
                    }
                    aria-pressed={item.active}
                    onClick={() =>
                      toggleFlag(
                        item.key,
                        !item.active,
                      )
                    }
                  >
                    {item.label}
                  </button>
                ),
              )}
            </div>
          </div>
        </div>

        {pending ? (
          <div className={styles.updating}>
            Updating...
          </div>
        ) : null}
      </div>

      <div className={styles.mobileFilters}>
        <label className={styles.field}>
          <span>Search</span>

          <input
            type="search"
            value={searchValue}
            onChange={(event) =>
              updateSearch(
                event.target.value,
              )
            }
            placeholder="Type a player name..."
          />
        </label>

        <div className={styles.mobileSortRow}>
          <label className={styles.field}>
            <span>Sort by</span>

            <select
              value={sortKey}
              onChange={(event) =>
                updateSort(
                  event.target.value,
                )
              }
            >
              {sortOptions.map(
                (option) => (
                  <option
                    key={option.key}
                    value={option.key}
                  >
                    {option.label}
                  </option>
                ),
              )}
            </select>
          </label>

          <label className={styles.simpleCheck}>
            <input
              type="checkbox"
              checked={descending}
              onChange={(event) =>
                toggleFlag(
                  "desc",
                  event.target.checked,
                )
              }
            />

            <span>Desc</span>
          </label>
        </div>

        <details
          className={styles.mobileFilterDetails}
          open={advancedActive}
        >
          <summary>
            Position &amp; status filters
          </summary>

          <div className={styles.mobileFilterBody}>
            <div>
              <div className={styles.filterLabel}>
                Position
              </div>

              <div className={styles.chipRow}>
                {positions.map(
                  (position) => (
                    <button
                      key={position}
                      type="button"
                      className={
                        selected.has(position)
                          ? `${styles.filterChip} ${styles.filterChipActive}`
                          : styles.filterChip
                      }
                      aria-pressed={
                        selected.has(position)
                      }
                      onClick={() =>
                        toggleMulti(
                          "pos",
                          position,
                        )
                      }
                    >
                      {position}
                    </button>
                  ),
                )}
              </div>
            </div>

            <div>
              <div className={styles.filterLabel}>
                Status
              </div>

              <div className={styles.chipRow}>
                {statusButtons.map(
                  (item) => (
                    <button
                      key={item.key}
                      type="button"
                      className={
                        item.active
                          ? `${styles.filterChip} ${styles.filterChipActive}`
                          : styles.filterChip
                      }
                      aria-pressed={
                        item.active
                      }
                      onClick={() =>
                        toggleFlag(
                          item.key,
                          !item.active,
                        )
                      }
                    >
                      {item.label}
                    </button>
                  ),
                )}
              </div>
            </div>
          </div>
        </details>

        <div className={styles.mobileFilterFooter}>
          <button
            type="button"
            className={styles.resetButton}
            onClick={reset}
          >
            Reset filters
          </button>

          {pending ? (
            <span className={styles.updating}>
              Updating...
            </span>
          ) : null}
        </div>
      </div>
    </>
  );
}