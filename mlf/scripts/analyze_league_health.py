#!/usr/bin/env python3

"""
MLF historical League Health analysis.

Methodology:
    ../docs/11_MLF_League_Health_Analysis.md

This first implementation increment validates the frozen historical
contract and standings foundations. It performs no database writes and
no external API calls.
"""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SNAPSHOT_DIR = Path(
    "/Volume1/Bots/fantasy/DraftBoards/"
    "mlf/data/raw/contracts/"
    "2026-02-02__post-renewal"
)

MANAGERS_CSV = SNAPSHOT_DIR / "Managers.csv"


EXPECTED_CONTROLLED_PLAYER_SEASONS = 1654

EXPECTED_SEASON_COUNTS = {
    2017: 111,
    2018: 160,
    2019: 194,
    2020: 203,
    2021: 202,
    2022: 197,
    2023: 195,
    2024: 190,
    2025: 202,
}

EXPECTED_FRANCHISE_SEASONS = 144
EXPECTED_FRANCHISE_COUNT = 16


# Explicit historical owner-name normalization used only for analysis.
# Source files are preserved unchanged.
OWNER_ALIASES = {
    "Nate": "Nate H",
}


CONTRACT_SQL = r"""
SELECT DISTINCT
    owner_name,
    sheet_name,
    row_number,
    btrim(player_name) AS player_name,
    season_year,
    raw_value,
    years_remaining,
    contract_label
FROM public.v_player_contract_timeline
WHERE season_year BETWEEN 2017 AND 2025
  AND (
        years_remaining BETWEEN 1 AND 5
        OR upper(coalesce(contract_label, '')) = 'FT'
      )
  AND btrim(coalesce(raw_value, ''))
        <> season_year::text
  AND btrim(coalesce(player_name, '')) <> ''
ORDER BY
    season_year,
    player_name,
    owner_name,
    row_number;
"""


def run_psql(sql: str) -> str:
    command = [
        "docker",
        "exec",
        "-i",
        "mlf_postgres",
        "psql",
        "-X",
        "-U",
        "mlf",
        "-d",
        "mlf",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-F",
        "\t",
        "-t",
        "-c",
        sql,
    ]

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )

    return result.stdout


def optional_int(value: str) -> int | None:
    text = str(value or "").strip()

    if not text:
        return None

    return int(text)


def load_contract_rows() -> list[dict[str, Any]]:
    output = run_psql(CONTRACT_SQL)

    rows: list[dict[str, Any]] = []

    for line in output.splitlines():
        if not line.strip():
            continue

        parts = line.split("\t")

        if len(parts) != 8:
            raise RuntimeError(
                "Unexpected contract query width: "
                f"{len(parts)} columns in {line!r}"
            )

        (
            owner_name,
            sheet_name,
            row_number,
            player_name,
            season_year,
            raw_value,
            years_remaining,
            contract_label,
        ) = parts

        rows.append(
            {
                "owner_name": OWNER_ALIASES.get(
                    owner_name.strip(),
                    owner_name.strip(),
                ),
                "sheet_name": sheet_name.strip(),
                "row_number": int(row_number),
                "player_name": player_name.strip(),
                "season_year": int(season_year),
                "raw_value": raw_value.strip(),
                "years_remaining": optional_int(
                    years_remaining
                ),
                "contract_label": contract_label.strip(),
            }
        )

    return rows


def choose_end_owner_rows(
    rows: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    grouped: dict[
        tuple[int, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in rows:
        key = (
            row["season_year"],
            row["player_name"],
        )
        grouped[key].append(row)

    selected: list[dict[str, Any]] = []
    resolution_log: list[dict[str, Any]] = []

    for key, group in sorted(grouped.items()):
        if len(group) == 1:
            selected.append(group[0])
            continue

        preferred = []

        for row in group:
            raw = row["raw_value"].lower()

            if (
                "(from " in raw
                or "waiver claim" in raw
            ):
                preferred.append(row)

        if len(preferred) == 1:
            chosen = preferred[0]

        else:
            survivors = []

            for row in group:
                raw = row["raw_value"].lower()

                if "(to " in raw:
                    continue

                if "/ fa" in raw:
                    continue

                survivors.append(row)

            if len(survivors) != 1:
                print()
                print(
                    "UNRESOLVED_END_OWNER_GROUP="
                    + json.dumps(
                        group,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )

                raise RuntimeError(
                    "Could not deterministically resolve "
                    f"end owner for {key!r}"
                )

            chosen = survivors[0]

        selected.append(chosen)

        resolution_log.append(
            {
                "season_year": key[0],
                "player_name": key[1],
                "candidate_owners": [
                    row["owner_name"]
                    for row in group
                ],
                "selected_owner": chosen["owner_name"],
                "selected_raw_value":
                    chosen["raw_value"],
            }
        )

    return selected, resolution_log


def normalize_header(value: str) -> str:
    return " ".join(
        str(value or "")
        .replace("\n", " ")
        .split()
    ).strip()


def load_standings() -> dict[
    tuple[int, str],
    int,
]:
    if not MANAGERS_CSV.exists():
        raise RuntimeError(
            f"Managers.csv not found: {MANAGERS_CSV}"
        )

    standings: dict[
        tuple[int, str],
        int,
    ] = {}

    with MANAGERS_CSV.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)

        if reader.fieldnames is None:
            raise RuntimeError(
                "Managers.csv has no header."
            )

        header_map = {
            normalize_header(name): name
            for name in reader.fieldnames
        }

        if "Owner" not in header_map:
            raise RuntimeError(
                "Managers.csv does not contain "
                "an Owner column. Headers: "
                + repr(
                    sorted(header_map)
                )
            )

        owner_column = header_map["Owner"]

        rank_columns: dict[int, str] = {}

        for season in range(2017, 2026):
            expected = f"{season} Rank"

            if expected not in header_map:
                raise RuntimeError(
                    "Managers.csv missing "
                    f"{expected!r}. Headers: "
                    + repr(
                        sorted(header_map)
                    )
                )

            rank_columns[season] = (
                header_map[expected]
            )

        for row in reader:
            owner = str(
                row.get(owner_column)
                or ""
            ).strip()

            if not owner:
                continue

            for season, column in (
                rank_columns.items()
            ):
                value = str(
                    row.get(column)
                    or ""
                ).strip()

                if not value:
                    raise RuntimeError(
                        "Missing finish rank for "
                        f"{owner!r}, {season}"
                    )

                standings[
                    (
                        season,
                        owner,
                    )
                ] = int(
                    float(value)
                )

    return standings


def validate_foundation() -> None:
    print()
    print("=" * 76)
    print(
        "MLF LEAGUE HEALTH "
        "FOUNDATION VALIDATION"
    )
    print("=" * 76)

    raw_rows = load_contract_rows()

    (
        controlled_rows,
        resolutions,
    ) = choose_end_owner_rows(
        raw_rows
    )

    print(
        f"FILTERED_TIMELINE_ROWS="
        f"{len(raw_rows)}"
    )

    print(
        f"END_OWNER_RESOLUTIONS="
        f"{len(resolutions)}"
    )

    print(
        f"CONTROLLED_PLAYER_SEASONS="
        f"{len(controlled_rows)}"
    )

    if (
        len(controlled_rows)
        != EXPECTED_CONTROLLED_PLAYER_SEASONS
    ):
        raise RuntimeError(
            "Controlled-player-season invariant "
            "failed. Expected "
            f"{EXPECTED_CONTROLLED_PLAYER_SEASONS}, "
            f"found {len(controlled_rows)}."
        )

    season_counts = Counter(
        row["season_year"]
        for row in controlled_rows
    )

    actual_counts = dict(
        sorted(
            season_counts.items()
        )
    )

    print(
        "CONTROLLED_BY_SEASON="
        + json.dumps(
            actual_counts,
            sort_keys=True,
        )
    )

    if actual_counts != EXPECTED_SEASON_COUNTS:
        raise RuntimeError(
            "Season-count invariant failed. "
            f"Expected {EXPECTED_SEASON_COUNTS!r}; "
            f"found {actual_counts!r}."
        )

    standings = load_standings()

    print(
        f"STANDINGS_ROWS="
        f"{len(standings)}"
    )

    if (
        len(standings)
        != EXPECTED_FRANCHISE_SEASONS
    ):
        raise RuntimeError(
            "Standings invariant failed. "
            f"Expected "
            f"{EXPECTED_FRANCHISE_SEASONS}, "
            f"found {len(standings)}."
        )

    contract_owners = sorted(
        {
            row["owner_name"]
            for row in controlled_rows
        }
    )

    standings_owners = sorted(
        {
            owner
            for _, owner
            in standings
        }
    )

    print(
        f"DISTINCT_CONTRACT_OWNERS="
        f"{len(contract_owners)}"
    )

    print(
        f"DISTINCT_STANDINGS_OWNERS="
        f"{len(standings_owners)}"
    )

    if (
        len(contract_owners)
        != EXPECTED_FRANCHISE_COUNT
    ):
        raise RuntimeError(
            "Expected "
            f"{EXPECTED_FRANCHISE_COUNT} "
            "contract owners; found "
            f"{len(contract_owners)}."
        )

    if (
        len(standings_owners)
        != EXPECTED_FRANCHISE_COUNT
    ):
        raise RuntimeError(
            "Expected "
            f"{EXPECTED_FRANCHISE_COUNT} "
            "standings owners; found "
            f"{len(standings_owners)}."
        )

    missing_in_standings = sorted(
        set(contract_owners)
        - set(standings_owners)
    )

    if missing_in_standings:
        raise RuntimeError(
            "Contract owners missing from "
            "standings: "
            + repr(missing_in_standings)
        )

    print()
    print(
        "END_OWNER_RESOLUTION_SAMPLE="
    )

    for row in resolutions[:10]:
        print(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    print()
    print(
        "FOUNDATION_VALIDATION=PASS"
    )


def main() -> None:
    validate_foundation()


if __name__ == "__main__":
    main()
