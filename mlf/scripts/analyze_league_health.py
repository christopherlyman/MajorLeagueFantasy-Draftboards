#!/usr/bin/env python3

"""
MLF historical League Health analysis.

Methodology:
    ../docs/11_MLF_League_Health_Analysis.md

The analysis validates the frozen historical contract and standings
foundation, then resolves historical MLB player identity and season
activity. It performs no database writes. Historical identity retrieval
uses the public MLB Stats API.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
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


MLB_BASE = "https://statsapi.mlb.com/api/v1"

EXPECTED_DISTINCT_CONTRACT_NAMES = 473


# Historical contract-name aliases proven during discovery.
PLAYER_NAME_ALIASES = {
    "Brendan McKay (p)": "Brendan McKay",
    "Dee Gordon": "Dee Strange-Gordon",
    "Hyun-Jin Ryu": "Hyun Jin Ryu",
    "JD Martinez": "J.D. Martinez",
    "Jose Miranda": "José Miranda",
    "JT Realmuto": "J.T. Realmuto",
    "Mike Soroka": "Michael Soroka",
    "Nicholas Castellanos": "Nick Castellanos",
    "Ronald Acuna": "Ronald Acuña Jr.",
    "Shohei Ohtani (B)": "Shohei Ohtani",
    "Shohei Ohtani (bat)": "Shohei Ohtani",
    "Shohei Ohtani (P)": "Shohei Ohtani",
    "Shohei Ohtani (pitch)": "Shohei Ohtani",
    "Vincent Velasquez": "Vince Velasquez",
    "Wilson Contreras": "Willson Contreras",
}


# Explicit identity closures for historical same-name/search exceptions.
DIRECT_MLBAM = {
    "José Ramírez": 608070,
    "Jose Ramirez": 608070,
    "Will Smith": 669257,
    "Jose Miranda": 669304,
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


def normalize_player_name(value: str) -> str:
    text = str(value or "").strip()

    text = unicodedata.normalize(
        "NFKD",
        text,
    )

    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text.lower(),
    )

    return " ".join(
        text.split()
    )


def parse_number(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).strip()

    if text in {
        "",
        "-",
        "--",
    }:
        return None

    text = text.replace(
        ",",
        "",
    )

    try:
        return float(text)
    except ValueError:
        return None


def get_mlb_json(
    path: str,
    params: dict[str, Any] | None = None,
    attempts: int = 5,
) -> dict[str, Any]:
    url = MLB_BASE + path

    if params:
        url += (
            "?"
            + urllib.parse.urlencode(
                params
            )
        )

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent":
                "MLF-League-Health-Analysis/1.0",
        },
    )

    last_error: Exception | None = None

    for attempt in range(
        1,
        attempts + 1,
    ):
        try:
            with urllib.request.urlopen(
                request,
                timeout=45,
            ) as response:
                return json.load(
                    response
                )

        except Exception as exc:
            last_error = exc

            if attempt < attempts:
                time.sleep(
                    min(
                        2 ** attempt,
                        10,
                    )
                )

    raise RuntimeError(
        f"MLB API request failed: {url}"
    ) from last_error


def exact_name_candidates(
    contract_name: str,
) -> list[dict[str, Any]]:
    if contract_name in DIRECT_MLBAM:
        return [
            {
                "mlbam_id":
                    DIRECT_MLBAM[
                        contract_name
                    ],
                "full_name":
                    contract_name,
                "direct_override":
                    True,
            }
        ]

    search_name = PLAYER_NAME_ALIASES.get(
        contract_name,
        contract_name,
    )

    payload = get_mlb_json(
        "/people/search",
        {
            "names": search_name,
        },
    )

    target = normalize_player_name(
        search_name
    )

    candidates = []

    for person in payload.get(
        "people",
        [],
    ):
        person_id = person.get(
            "id"
        )

        full_name = str(
            person.get(
                "fullName"
            )
            or ""
        ).strip()

        if not person_id:
            continue

        if (
            normalize_player_name(
                full_name
            )
            != target
        ):
            continue

        candidates.append(
            {
                "mlbam_id":
                    int(person_id),
                "full_name":
                    full_name,
                "direct_override":
                    False,
            }
        )

    return candidates


def load_year_by_year(
    mlbam_id: int,
    group: str,
) -> dict[int, dict[str, Any]]:
    payload = get_mlb_json(
        f"/people/{mlbam_id}/stats",
        {
            "stats":
                "yearByYear",
            "group":
                group,
        },
    )

    by_season: dict[
        int,
        dict[str, Any],
    ] = {}

    for block in payload.get(
        "stats",
        [],
    ):
        for split in block.get(
            "splits",
            [],
        ):
            season_text = str(
                split.get(
                    "season"
                )
                or ""
            )

            if not season_text.isdigit():
                continue

            season = int(
                season_text
            )

            stat = (
                split.get(
                    "stat"
                )
                or {}
            )

            if season not in by_season:
                by_season[
                    season
                ] = stat
                continue

            old_games = (
                parse_number(
                    by_season[
                        season
                    ].get(
                        "gamesPlayed"
                    )
                )
                or 0
            )

            new_games = (
                parse_number(
                    stat.get(
                        "gamesPlayed"
                    )
                )
                or 0
            )

            if new_games > old_games:
                by_season[
                    season
                ] = stat

    return by_season


def hitting_activity(
    stat: dict[str, Any] | None,
) -> bool:
    if not stat:
        return False

    games = (
        parse_number(
            stat.get(
                "gamesPlayed"
            )
        )
        or 0
    )

    plate_appearances = (
        parse_number(
            stat.get(
                "plateAppearances"
            )
        )
        or 0
    )

    at_bats = (
        parse_number(
            stat.get(
                "atBats"
            )
        )
        or 0
    )

    return (
        games > 0
        or plate_appearances > 0
        or at_bats > 0
    )


def pitching_activity(
    stat: dict[str, Any] | None,
) -> bool:
    if not stat:
        return False

    games = parse_number(
        stat.get(
            "gamesPitched"
        )
    )

    if games is None:
        games = parse_number(
            stat.get(
                "gamesPlayed"
            )
        )

    innings = (
        parse_number(
            stat.get(
                "inningsPitched"
            )
        )
        or 0
    )

    return (
        (games or 0) > 0
        or innings > 0
    )


def validate_historical_identity(
    controlled_rows: list[dict[str, Any]],
) -> None:
    print()
    print("=" * 76)
    print(
        "MLF HISTORICAL IDENTITY "
        "VALIDATION"
    )
    print("=" * 76)

    names = sorted(
        {
            row["player_name"]
            for row in controlled_rows
        },
        key=normalize_player_name,
    )

    print(
        f"DISTINCT_CONTRACT_NAMES="
        f"{len(names)}"
    )

    if (
        len(names)
        != EXPECTED_DISTINCT_CONTRACT_NAMES
    ):
        raise RuntimeError(
            "Distinct contract-name invariant "
            "failed. Expected "
            f"{EXPECTED_DISTINCT_CONTRACT_NAMES}, "
            f"found {len(names)}."
        )

    candidate_cache: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    for index, name in enumerate(
        names,
        start=1,
    ):
        candidate_cache[
            name
        ] = exact_name_candidates(
            name
        )

        if (
            index % 100 == 0
            or index == len(names)
        ):
            print(
                f"IDENTITY_SEARCH_PROGRESS="
                f"{index}/{len(names)}"
            )

        time.sleep(0.02)

    mlbam_ids = sorted(
        {
            candidate["mlbam_id"]
            for candidates
            in candidate_cache.values()
            for candidate in candidates
        }
    )

    print(
        f"DISTINCT_CANDIDATE_MLBAM_IDS="
        f"{len(mlbam_ids)}"
    )

    stats_cache: dict[
        int,
        dict[str, dict[int, dict[str, Any]]],
    ] = {}

    for index, mlbam_id in enumerate(
        mlbam_ids,
        start=1,
    ):
        hitting = load_year_by_year(
            mlbam_id,
            "hitting",
        )

        pitching = load_year_by_year(
            mlbam_id,
            "pitching",
        )

        stats_cache[
            mlbam_id
        ] = {
            "hitting":
                hitting,
            "pitching":
                pitching,
        }

        if (
            index % 50 == 0
            or index == len(mlbam_ids)
        ):
            print(
                f"MLB_STAT_PROGRESS="
                f"{index}/{len(mlbam_ids)}"
            )

        time.sleep(0.02)

    statuses = Counter()
    ambiguous_rows = []
    exception_rows = []

    for row in controlled_rows:
        contract_name = row[
            "player_name"
        ]

        season = row[
            "season_year"
        ]

        candidates = candidate_cache.get(
            contract_name,
            [],
        )

        active_candidates = []

        for candidate in candidates:
            mlbam_id = candidate[
                "mlbam_id"
            ]

            cache = stats_cache[
                mlbam_id
            ]

            hit_stat = cache[
                "hitting"
            ].get(
                season
            )

            pitch_stat = cache[
                "pitching"
            ].get(
                season
            )

            if (
                hitting_activity(
                    hit_stat
                )
                or pitching_activity(
                    pitch_stat
                )
            ):
                active_candidates.append(
                    candidate
                )

        if contract_name in DIRECT_MLBAM:
            selected_id = DIRECT_MLBAM[
                contract_name
            ]

            direct_active = any(
                candidate[
                    "mlbam_id"
                ] == selected_id
                for candidate
                in active_candidates
            )

            if direct_active:
                status = (
                    "RESOLVED_ACTIVE_DIRECT"
                )
            else:
                status = (
                    "RESOLVED_NO_ACTIVITY_DIRECT"
                )

        elif len(active_candidates) == 1:
            status = "RESOLVED_ACTIVE"

        elif len(active_candidates) > 1:
            status = "AMBIGUOUS_ACTIVE"

        elif len(candidates) == 1:
            status = "RESOLVED_NO_ACTIVITY"

        elif len(candidates) == 0:
            status = "NO_IDENTITY_MATCH"

        else:
            status = (
                "MULTIPLE_IDS_NO_ACTIVITY"
            )

        statuses[
            status
        ] += 1

        if status == "AMBIGUOUS_ACTIVE":
            ambiguous_rows.append(
                {
                    "season_year":
                        season,
                    "owner_name":
                        row["owner_name"],
                    "player_name":
                        contract_name,
                    "candidates":
                        candidates,
                }
            )

        if status not in {
            "RESOLVED_ACTIVE",
            "RESOLVED_ACTIVE_DIRECT",
        }:
            exception_rows.append(
                {
                    "season_year":
                        season,
                    "owner_name":
                        row["owner_name"],
                    "player_name":
                        contract_name,
                    "identity_status":
                        status,
                    "candidate_count":
                        len(candidates),
                }
            )

    print()
    print(
        "IDENTITY_STATUS_COUNTS="
    )

    for status in sorted(
        statuses
    ):
        print(
            f"{status}="
            f"{statuses[status]}"
        )

    active_count = (
        statuses[
            "RESOLVED_ACTIVE"
        ]
        + statuses[
            "RESOLVED_ACTIVE_DIRECT"
        ]
    )

    print(
        f"RESOLVED_ACTIVE_PLAYER_SEASONS="
        f"{active_count}"
    )

    print(
        f"NON_ACTIVE_OR_UNRESOLVED_PLAYER_SEASONS="
        f"{len(controlled_rows) - active_count}"
    )

    print()
    print(
        "IDENTITY_EXCEPTION_SAMPLE="
    )

    for row in exception_rows[:30]:
        print(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    print()
    print(
        f"AMBIGUOUS_ACTIVE_COUNT="
        f"{len(ambiguous_rows)}"
    )

    if ambiguous_rows:
        for row in ambiguous_rows:
            print(
                "AMBIGUOUS_ACTIVE="
                + json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        raise RuntimeError(
            "Historical identity validation "
            "found active ambiguities."
        )

    print()
    print(
        "HISTORICAL_IDENTITY_VALIDATION=PASS"
    )


def validate_foundation() -> tuple[
    list[dict[str, Any]],
    dict[tuple[int, str], int],
]:
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

    return controlled_rows, standings


def main() -> None:
    (
        controlled_rows,
        _standings,
    ) = validate_foundation()

    validate_historical_identity(
        controlled_rows
    )


if __name__ == "__main__":
    main()
