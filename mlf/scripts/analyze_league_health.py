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
import math
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


# Explicit fantasy-role labels for historical two-way assets.
FANTASY_GROUP_OVERRIDES = {
    "Brendan McKay (p)": "pitching",
    "Shohei Ohtani (B)": "hitting",
    "Shohei Ohtani (bat)": "hitting",
    "Shohei Ohtani (P)": "pitching",
    "Shohei Ohtani (pitch)": "pitching",
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
                "primary_position":
                    "",
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
                "primary_position":
                    str(
                        (
                            person.get(
                                "primaryPosition"
                            )
                            or {}
                        ).get(
                            "abbreviation"
                        )
                        or ""
                    ).upper(),
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
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[int, dict[str, dict[int, dict[str, Any]]]],
]:
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

    return candidate_cache, stats_cache


def ip_to_outs(value: Any) -> int:
    if value is None:
        return 0

    text = str(value).strip()

    if not text or text == "-":
        return 0

    if "." not in text:
        return int(text) * 3

    whole, fraction = text.split(
        ".",
        1,
    )

    whole_value = int(
        whole or 0
    )

    digit = (
        fraction[:1]
        if fraction
        else "0"
    )

    if digit not in {
        "0",
        "1",
        "2",
    }:
        raise ValueError(
            f"Invalid baseball IP value: {value!r}"
        )

    return (
        whole_value * 3
        + int(digit)
    )


def infer_fantasy_group(
    contract_name: str,
    primary_position: str,
    hitting_stat: dict[str, Any] | None,
    pitching_stat: dict[str, Any] | None,
) -> str | None:
    override = FANTASY_GROUP_OVERRIDES.get(
        contract_name
    )

    if override:
        return override

    position = str(
        primary_position
        or ""
    ).upper()

    if position == "P":
        return "pitching"

    if position:
        return "hitting"

    hit_active = hitting_activity(
        hitting_stat
    )

    pitch_active = pitching_activity(
        pitching_stat
    )

    if hit_active and not pitch_active:
        return "hitting"

    if pitch_active and not hit_active:
        return "pitching"

    return None


def percentile_ranks(
    indexed_values: list[
        tuple[int, float | None]
    ],
) -> dict[int, float]:
    valid = []

    for index, value in indexed_values:
        if value is None:
            continue

        numeric = float(value)

        if not math.isfinite(
            numeric
        ):
            continue

        valid.append(
            (
                index,
                numeric,
            )
        )

    result: dict[int, float] = {}

    if not valid:
        return result

    valid.sort(
        key=lambda item: item[1]
    )

    total = len(valid)
    position = 0

    while position < total:
        end = position + 1

        while (
            end < total
            and valid[end][1]
            == valid[position][1]
        ):
            end += 1

        average_rank = (
            (position + 1)
            + end
        ) / 2.0

        percentile = (
            0.5
            if total == 1
            else (
                average_rank - 1
            ) / (
                total - 1
            )
        )

        for offset in range(
            position,
            end,
        ):
            result[
                valid[offset][0]
            ] = percentile

        position = end

    return result


def pearson(
    xs: list[float],
    ys: list[float],
) -> float | None:
    if len(xs) != len(ys):
        raise ValueError(
            "Pearson inputs differ in length."
        )

    if len(xs) < 3:
        return None

    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)

    numerator = sum(
        (x - x_mean)
        * (y - y_mean)
        for x, y in zip(
            xs,
            ys,
        )
    )

    x_denominator = math.sqrt(
        sum(
            (x - x_mean) ** 2
            for x in xs
        )
    )

    y_denominator = math.sqrt(
        sum(
            (y - y_mean) ** 2
            for y in ys
        )
    )

    if (
        x_denominator == 0
        or y_denominator == 0
    ):
        return None

    return numerator / (
        x_denominator
        * y_denominator
    )


def average_ranks(
    values: list[float],
) -> list[float]:
    indexed = list(
        enumerate(values)
    )

    indexed.sort(
        key=lambda item: item[1]
    )

    ranks: list[
        float | None
    ] = [
        None
    ] * len(values)

    position = 0

    while position < len(indexed):
        end = position + 1

        while (
            end < len(indexed)
            and indexed[end][1]
            == indexed[position][1]
        ):
            end += 1

        average_rank = (
            (position + 1)
            + end
        ) / 2.0

        for offset in range(
            position,
            end,
        ):
            original_index = (
                indexed[offset][0]
            )

            ranks[
                original_index
            ] = average_rank

        position = end

    if any(
        value is None
        for value in ranks
    ):
        raise RuntimeError(
            "Rank construction failed."
        )

    return [
        float(value)
        for value in ranks
    ]


def spearman(
    xs: list[float],
    ys: list[float],
) -> float | None:
    if len(xs) != len(ys):
        raise ValueError(
            "Spearman inputs differ in length."
        )

    if len(xs) < 3:
        return None

    return pearson(
        average_ranks(xs),
        average_ranks(ys),
    )


def derived_pitching_total_bases(
    stat: dict[str, Any],
) -> float:
    supplied = parse_number(
        stat.get(
            "totalBases"
        )
    )

    if supplied is not None:
        return supplied

    hits = (
        parse_number(
            stat.get(
                "hits"
            )
        )
        or 0.0
    )

    doubles = (
        parse_number(
            stat.get(
                "doubles"
            )
        )
        or 0.0
    )

    triples = (
        parse_number(
            stat.get(
                "triples"
            )
        )
        or 0.0
    )

    home_runs = (
        parse_number(
            stat.get(
                "homeRuns"
            )
        )
        or 0.0
    )

    singles = (
        hits
        - doubles
        - triples
        - home_runs
    )

    return (
        singles
        + 2.0 * doubles
        + 3.0 * triples
        + 4.0 * home_runs
    )


def build_player_quality_rows(
    controlled_rows: list[dict[str, Any]],
    candidate_cache: dict[
        str,
        list[dict[str, Any]],
    ],
    stats_cache: dict[
        int,
        dict[
            str,
            dict[int, dict[str, Any]],
        ],
    ],
) -> list[dict[str, Any]]:
    player_rows: list[
        dict[str, Any]
    ] = []

    for source in controlled_rows:
        contract_name = source[
            "player_name"
        ]

        season = source[
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

            cached = stats_cache[
                mlbam_id
            ]

            hitting_stat = cached[
                "hitting"
            ].get(
                season
            )

            pitching_stat = cached[
                "pitching"
            ].get(
                season
            )

            if (
                hitting_activity(
                    hitting_stat
                )
                or pitching_activity(
                    pitching_stat
                )
            ):
                active_candidates.append(
                    {
                        **candidate,
                        "hitting_stat":
                            hitting_stat,
                        "pitching_stat":
                            pitching_stat,
                    }
                )

        selected = None

        if contract_name in DIRECT_MLBAM:
            direct_id = DIRECT_MLBAM[
                contract_name
            ]

            direct_matches = [
                candidate
                for candidate
                in active_candidates
                if candidate[
                    "mlbam_id"
                ] == direct_id
            ]

            if len(direct_matches) > 1:
                raise RuntimeError(
                    "Multiple direct active matches for "
                    f"{contract_name!r}, {season}."
                )

            if len(direct_matches) == 1:
                selected = direct_matches[0]

        elif len(active_candidates) == 1:
            selected = active_candidates[0]

        elif len(active_candidates) > 1:
            raise RuntimeError(
                "Quality build reached unresolved "
                "active identity for "
                f"{contract_name!r}, {season}."
            )

        output = dict(source)

        output.update(
            {
                "mlbam_id": None,
                "mlb_activity": False,
                "fantasy_group": None,
                "role": None,
                "quality_pct": None,
                "elite": False,
            }
        )

        if selected is None:
            player_rows.append(
                output
            )
            continue

        hitting_stat = selected[
            "hitting_stat"
        ]

        pitching_stat = selected[
            "pitching_stat"
        ]

        fantasy_group = infer_fantasy_group(
            contract_name,
            selected.get(
                "primary_position",
                "",
            ),
            hitting_stat,
            pitching_stat,
        )

        if fantasy_group is None:
            raise RuntimeError(
                "Could not determine fantasy group "
                f"for {contract_name!r}, {season}, "
                f"MLBAM {selected['mlbam_id']}."
            )

        output[
            "mlbam_id"
        ] = selected[
            "mlbam_id"
        ]

        output[
            "mlb_activity"
        ] = True

        output[
            "fantasy_group"
        ] = fantasy_group

        if fantasy_group == "hitting":
            stat = hitting_stat or {}

            output[
                "role"
            ] = "B"

            output.update(
                {
                    "ab":
                        parse_number(
                            stat.get(
                                "atBats"
                            )
                        )
                        or 0.0,
                    "h":
                        parse_number(
                            stat.get(
                                "hits"
                            )
                        )
                        or 0.0,
                    "r":
                        parse_number(
                            stat.get(
                                "runs"
                            )
                        )
                        or 0.0,
                    "hr":
                        parse_number(
                            stat.get(
                                "homeRuns"
                            )
                        )
                        or 0.0,
                    "rbi":
                        parse_number(
                            stat.get(
                                "rbi"
                            )
                        )
                        or 0.0,
                    "sb":
                        parse_number(
                            stat.get(
                                "stolenBases"
                            )
                        )
                        or 0.0,
                    "bb":
                        parse_number(
                            stat.get(
                                "baseOnBalls"
                            )
                        )
                        or 0.0,
                    "k":
                        parse_number(
                            stat.get(
                                "strikeOuts"
                            )
                        )
                        or 0.0,
                }
            )

        else:
            stat = pitching_stat or {}

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

            games = games or 0.0

            starts = (
                parse_number(
                    stat.get(
                        "gamesStarted"
                    )
                )
                or 0.0
            )

            outs_value = parse_number(
                stat.get(
                    "outs"
                )
            )

            if outs_value is None:
                outs = ip_to_outs(
                    stat.get(
                        "inningsPitched"
                    )
                )
            else:
                outs = int(
                    round(
                        outs_value
                    )
                )

            innings = (
                outs / 3.0
            )

            role = (
                "SP"
                if (
                    starts >= 3
                    and games > 0
                    and (
                        starts / games
                    ) >= 0.40
                )
                else "RP"
            )

            output[
                "role"
            ] = role

            output.update(
                {
                    "ip":
                        innings,
                    "games_started":
                        starts,
                    "wins":
                        parse_number(
                            stat.get(
                                "wins"
                            )
                        )
                        or 0.0,
                    "pk":
                        parse_number(
                            stat.get(
                                "strikeOuts"
                            )
                        )
                        or 0.0,
                    "saves":
                        parse_number(
                            stat.get(
                                "saves"
                            )
                        )
                        or 0.0,
                    "holds":
                        parse_number(
                            stat.get(
                                "holds"
                            )
                        )
                        or 0.0,
                    "era":
                        parse_number(
                            stat.get(
                                "era"
                            )
                        ),
                    "whip":
                        parse_number(
                            stat.get(
                                "whip"
                            )
                        ),
                    "ph":
                        parse_number(
                            stat.get(
                                "hits"
                            )
                        )
                        or 0.0,
                    "pbb":
                        parse_number(
                            stat.get(
                                "baseOnBalls"
                            )
                        )
                        or 0.0,
                    "earned_runs":
                        parse_number(
                            stat.get(
                                "earnedRuns"
                            )
                        )
                        or 0.0,
                    "tb_allowed":
                        derived_pitching_total_bases(
                            stat
                        ),
                }
            )

        player_rows.append(
            output
        )

    return player_rows


def calculate_quality_percentiles(
    player_rows: list[dict[str, Any]],
) -> None:
    role_groups: dict[
        tuple[int, str],
        list[int],
    ] = defaultdict(list)

    for index, row in enumerate(
        player_rows
    ):
        if (
            row["mlb_activity"]
            and row["role"]
        ):
            role_groups[
                (
                    row["season_year"],
                    row["role"],
                )
            ].append(index)

    for (
        season,
        role,
    ), indices in sorted(
        role_groups.items()
    ):
        if role == "B":
            total_h = sum(
                player_rows[index]["h"]
                for index in indices
            )

            total_ab = sum(
                player_rows[index]["ab"]
                for index in indices
            )

            baseline_avg = (
                total_h / total_ab
                if total_ab > 0
                else 0.0
            )

            for index in indices:
                row = player_rows[
                    index
                ]

                row[
                    "avg_impact"
                ] = (
                    row["h"]
                    - baseline_avg
                    * row["ab"]
                )

                row[
                    "neg_k"
                ] = -row["k"]

            components = [
                "r",
                "hr",
                "rbi",
                "sb",
                "bb",
                "neg_k",
                "avg_impact",
            ]

        else:
            total_ip = sum(
                player_rows[index][
                    "ip"
                ]
                for index in indices
            )

            total_er = sum(
                player_rows[index][
                    "earned_runs"
                ]
                for index in indices
            )

            total_hits = sum(
                player_rows[index][
                    "ph"
                ]
                for index in indices
            )

            total_walks = sum(
                player_rows[index][
                    "pbb"
                ]
                for index in indices
            )

            total_tb = sum(
                player_rows[index][
                    "tb_allowed"
                ]
                for index in indices
            )

            baseline_era = (
                total_er * 9.0
                / total_ip
                if total_ip > 0
                else 0.0
            )

            baseline_whip = (
                (
                    total_hits
                    + total_walks
                )
                / total_ip
                if total_ip > 0
                else 0.0
            )

            baseline_tb_per_ip = (
                total_tb / total_ip
                if total_ip > 0
                else 0.0
            )

            for index in indices:
                row = player_rows[
                    index
                ]

                row[
                    "svh"
                ] = (
                    row["saves"]
                    + row["holds"]
                )

                row[
                    "era_impact"
                ] = (
                    (
                        baseline_era
                        - row["era"]
                    )
                    * row["ip"]
                    / 9.0
                    if row["era"]
                    is not None
                    else None
                )

                row[
                    "whip_impact"
                ] = (
                    (
                        baseline_whip
                        - row["whip"]
                    )
                    * row["ip"]
                    if row["whip"]
                    is not None
                    else None
                )

                row[
                    "tb_impact"
                ] = (
                    baseline_tb_per_ip
                    * row["ip"]
                    - row[
                        "tb_allowed"
                    ]
                )

            components = [
                "wins",
                "pk",
                "ip",
                "svh",
                "era_impact",
                "whip_impact",
                "tb_impact",
            ]

        percentile_columns = []

        for component in components:
            column = (
                component
                + "_pct"
            )

            percentile_columns.append(
                column
            )

            ranks = percentile_ranks(
                [
                    (
                        index,
                        player_rows[
                            index
                        ].get(
                            component
                        ),
                    )
                    for index in indices
                ]
            )

            for index in indices:
                player_rows[index][
                    column
                ] = ranks.get(
                    index
                )

        for index in indices:
            available = [
                player_rows[
                    index
                ].get(
                    column
                )
                for column
                in percentile_columns
                if player_rows[
                    index
                ].get(
                    column
                )
                is not None
            ]

            if not available:
                raise RuntimeError(
                    "No quality components for "
                    f"{season} {role}."
                )

            player_rows[index][
                "quality_composite_raw"
            ] = (
                sum(available)
                / len(available)
            )

        final_ranks = percentile_ranks(
            [
                (
                    index,
                    player_rows[
                        index
                    ].get(
                        "quality_composite_raw"
                    ),
                )
                for index in indices
            ]
        )

        for index in indices:
            quality = final_ranks.get(
                index
            )

            player_rows[index][
                "quality_pct"
            ] = quality

            player_rows[index][
                "elite"
            ] = (
                quality is not None
                and quality >= 0.75
            )


def build_franchise_quality_rows(
    player_rows: list[dict[str, Any]],
    standings: dict[
        tuple[int, str],
        int,
    ],
) -> list[dict[str, Any]]:
    portfolios: dict[
        tuple[int, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in player_rows:
        portfolios[
            (
                row["season_year"],
                row["owner_name"],
            )
        ].append(row)

    result = []

    for (
        season,
        owner,
    ), finish_rank in sorted(
        standings.items()
    ):
        assets = portfolios.get(
            (
                season,
                owner,
            ),
            [],
        )

        active = [
            row
            for row in assets
            if row.get(
                "quality_pct"
            )
            is not None
        ]

        qualities = [
            float(
                row["quality_pct"]
            )
            for row in active
        ]

        elite = [
            row
            for row in active
            if row.get(
                "elite"
            )
        ]

        result.append(
            {
                "season_year":
                    season,
                "owner_name":
                    owner,
                "finish_rank":
                    finish_rank,
                "top6":
                    finish_rank <= 6,
                "controlled_count":
                    len(assets),
                "active_count":
                    len(active),
                "nonproducing_count":
                    (
                        len(assets)
                        - len(active)
                    ),
                "avg_active_quality":
                    (
                        sum(qualities)
                        / len(qualities)
                        if qualities
                        else None
                    ),
                "realized_quality_sum":
                    sum(qualities),
                "elite_count":
                    len(elite),
                "elite_share":
                    (
                        len(elite)
                        / len(active)
                        if active
                        else None
                    ),
            }
        )

    return result


def print_quantity_quality_results(
    player_rows: list[dict[str, Any]],
    franchise_rows: list[dict[str, Any]],
) -> None:
    print()
    print("=" * 76)
    print(
        "MLF REALIZED QUALITY VALIDATION"
    )
    print("=" * 76)

    active_count = sum(
        1
        for row in player_rows
        if row.get(
            "quality_pct"
        )
        is not None
    )

    elite_count = sum(
        1
        for row in player_rows
        if row.get(
            "elite"
        )
    )

    print(
        f"QUALITY_PLAYER_SEASONS="
        f"{active_count}"
    )

    print(
        f"ELITE_PLAYER_SEASONS="
        f"{elite_count}"
    )

    print(
        f"FRANCHISE_SEASONS="
        f"{len(franchise_rows)}"
    )

    if len(franchise_rows) != 144:
        raise RuntimeError(
            "Expected 144 quality "
            "franchise-seasons."
        )

    role_counts = Counter(
        (
            row["season_year"],
            row["role"],
        )
        for row in player_rows
        if row.get(
            "quality_pct"
        )
        is not None
    )

    print()
    print(
        "ACTIVE_ROLE_COUNTS="
    )

    for season in range(
        2017,
        2026,
    ):
        print(
            f"{season} | "
            f"B={role_counts[(season, 'B')]} | "
            f"SP={role_counts[(season, 'SP')]} | "
            f"RP={role_counts[(season, 'RP')]}"
        )

    print()
    print("=" * 76)
    print(
        "PRIMARY QUANTITY / QUALITY RESULTS"
    )
    print("=" * 76)

    metrics = [
        "controlled_count",
        "active_count",
        "avg_active_quality",
        "realized_quality_sum",
        "elite_count",
        "elite_share",
    ]

    for metric in metrics:
        valid = [
            row
            for row in franchise_rows
            if row[
                metric
            ] is not None
        ]

        values = [
            float(
                row[metric]
            )
            for row in valid
        ]

        finishes = [
            float(
                row["finish_rank"]
            )
            for row in valid
        ]

        top6_values = [
            float(
                row[metric]
            )
            for row in valid
            if row["top6"]
        ]

        other_values = [
            float(
                row[metric]
            )
            for row in valid
            if not row["top6"]
        ]

        p_value = pearson(
            values,
            finishes,
        )

        s_value = spearman(
            values,
            finishes,
        )

        top6_avg = (
            sum(top6_values)
            / len(top6_values)
        )

        other_avg = (
            sum(other_values)
            / len(other_values)
        )

        print(
            f"{metric} | "
            f"n={len(valid)} | "
            f"pearson_finish="
            f"{p_value:.4f} | "
            f"spearman_finish="
            f"{s_value:.4f} | "
            f"top6_avg="
            f"{top6_avg:.4f} | "
            f"non_top6_avg="
            f"{other_avg:.4f}"
        )

    print()
    print("=" * 76)
    print(
        "SEASON-BY-SEASON AVG QUALITY"
    )
    print("=" * 76)

    for season in range(
        2017,
        2026,
    ):
        rows = [
            row
            for row in franchise_rows
            if row[
                "season_year"
            ] == season
            and row[
                "avg_active_quality"
            ] is not None
        ]

        top6 = [
            row[
                "avg_active_quality"
            ]
            for row in rows
            if row["top6"]
        ]

        others = [
            row[
                "avg_active_quality"
            ]
            for row in rows
            if not row["top6"]
        ]

        top6_avg = (
            sum(top6)
            / len(top6)
        )

        other_avg = (
            sum(others)
            / len(others)
        )

        direction = (
            "TOP6_HIGHER"
            if top6_avg > other_avg
            else "TOP6_NOT_HIGHER"
        )

        print(
            f"{season} | "
            f"top6_avg_quality="
            f"{top6_avg:.4f} | "
            f"non_top6_avg_quality="
            f"{other_avg:.4f} | "
            f"{direction}"
        )

    print()
    print(
        "REALIZED_QUALITY_VALIDATION=PASS"
    )


def controlled_asset_key(
    row: dict[str, Any],
) -> tuple[str, str]:
    contract_name = row[
        "player_name"
    ]

    canonical_name = (
        PLAYER_NAME_ALIASES.get(
            contract_name,
            contract_name,
        )
    )

    # Ohtani batting/pitching and other explicit
    # two-way labels remain distinct fantasy assets.
    group_marker = (
        FANTASY_GROUP_OVERRIDES.get(
            contract_name,
            ""
        )
    )

    return (
        normalize_player_name(
            canonical_name
        ),
        group_marker,
    )


def build_persistence_rows(
    player_rows: list[dict[str, Any]],
    franchise_rows: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
]:
    controlled_lookup: dict[
        tuple[int, str, tuple[str, str]],
        dict[str, Any],
    ] = {}

    portfolios: dict[
        tuple[int, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in player_rows:
        season = row[
            "season_year"
        ]

        owner = row[
            "owner_name"
        ]

        asset_key = controlled_asset_key(
            row
        )

        lookup_key = (
            season,
            owner,
            asset_key,
        )

        if lookup_key in controlled_lookup:
            raise RuntimeError(
                "Duplicate normalized controlled "
                "asset key: "
                f"{lookup_key!r}"
            )

        controlled_lookup[
            lookup_key
        ] = row

        portfolios[
            (
                season,
                owner,
            )
        ].append(row)

    franchise_by_key = {
        (
            row["season_year"],
            row["owner_name"],
        ): row
        for row in franchise_rows
    }

    persistence_rows = []

    for (
        season,
        owner,
    ), franchise in sorted(
        franchise_by_key.items()
    ):
        current_assets = portfolios.get(
            (
                season,
                owner,
            ),
            [],
        )

        current_elite = [
            row
            for row in current_assets
            if row.get(
                "elite"
            )
        ]

        persistent_2 = None
        persistent_3 = None
        retained_prior_elite_count = None
        prior_elite_count = None
        retained_prior_elite_share = None
        new_elite_count = None

        if season >= 2018:
            prior_assets = portfolios.get(
                (
                    season - 1,
                    owner,
                ),
                [],
            )

            prior_elite = [
                row
                for row in prior_assets
                if row.get(
                    "elite"
                )
            ]

            prior_elite_count = len(
                prior_elite
            )

            retained_prior_elite_count = sum(
                1
                for row in prior_elite
                if (
                    season,
                    owner,
                    controlled_asset_key(
                        row
                    ),
                )
                in controlled_lookup
            )

            if prior_elite_count > 0:
                retained_prior_elite_share = (
                    retained_prior_elite_count
                    / prior_elite_count
                )

            persistent_2 = 0

            for row in current_elite:
                asset_key = (
                    controlled_asset_key(
                        row
                    )
                )

                prior_row = controlled_lookup.get(
                    (
                        season - 1,
                        owner,
                        asset_key,
                    )
                )

                if (
                    prior_row is not None
                    and prior_row.get(
                        "elite"
                    )
                ):
                    persistent_2 += 1

            new_elite_count = (
                len(current_elite)
                - persistent_2
            )

        if season >= 2019:
            persistent_3 = 0

            for row in current_elite:
                asset_key = (
                    controlled_asset_key(
                        row
                    )
                )

                prior_row = controlled_lookup.get(
                    (
                        season - 1,
                        owner,
                        asset_key,
                    )
                )

                two_year_row = (
                    controlled_lookup.get(
                        (
                            season - 2,
                            owner,
                            asset_key,
                        )
                    )
                )

                if (
                    prior_row is not None
                    and two_year_row is not None
                    and prior_row.get(
                        "elite"
                    )
                    and two_year_row.get(
                        "elite"
                    )
                ):
                    persistent_3 += 1

        persistence_rows.append(
            {
                **franchise,
                "prior_elite_count":
                    prior_elite_count,
                "retained_prior_elite_count":
                    retained_prior_elite_count,
                "retained_prior_elite_share":
                    retained_prior_elite_share,
                "persistent_elite_2":
                    persistent_2,
                "persistent_elite_3":
                    persistent_3,
                "new_elite_count":
                    new_elite_count,
            }
        )

    retention = {
        "elite_opportunities": 0,
        "elite_retained": 0,
        "nonelite_opportunities": 0,
        "nonelite_retained": 0,
    }

    for row in player_rows:
        season = row[
            "season_year"
        ]

        if season >= 2025:
            continue

        if row.get(
            "quality_pct"
        ) is None:
            continue

        owner = row[
            "owner_name"
        ]

        asset_key = controlled_asset_key(
            row
        )

        retained = (
            (
                season + 1,
                owner,
                asset_key,
            )
            in controlled_lookup
        )

        if row.get(
            "elite"
        ):
            retention[
                "elite_opportunities"
            ] += 1

            if retained:
                retention[
                    "elite_retained"
                ] += 1

        else:
            retention[
                "nonelite_opportunities"
            ] += 1

            if retained:
                retention[
                    "nonelite_retained"
                ] += 1

    return (
        persistence_rows,
        retention,
    )


def print_persistence_results(
    persistence_rows: list[dict[str, Any]],
    retention: dict[str, int],
) -> None:
    print()
    print("=" * 76)
    print(
        "PERSISTENT ELITE CONTROL"
    )
    print("=" * 76)

    elite_opportunities = retention[
        "elite_opportunities"
    ]

    elite_retained = retention[
        "elite_retained"
    ]

    nonelite_opportunities = retention[
        "nonelite_opportunities"
    ]

    nonelite_retained = retention[
        "nonelite_retained"
    ]

    elite_retention_rate = (
        elite_retained
        / elite_opportunities
        if elite_opportunities
        else None
    )

    nonelite_retention_rate = (
        nonelite_retained
        / nonelite_opportunities
        if nonelite_opportunities
        else None
    )

    print(
        f"ELITE_NEXT_YEAR_OPPORTUNITIES="
        f"{elite_opportunities}"
    )

    print(
        f"ELITE_NEXT_YEAR_SAME_OWNER="
        f"{elite_retained}"
    )

    print(
        f"ELITE_NEXT_YEAR_RETENTION_RATE="
        f"{elite_retention_rate:.4f}"
    )

    print(
        f"NONELITE_NEXT_YEAR_OPPORTUNITIES="
        f"{nonelite_opportunities}"
    )

    print(
        f"NONELITE_NEXT_YEAR_SAME_OWNER="
        f"{nonelite_retained}"
    )

    print(
        f"NONELITE_NEXT_YEAR_RETENTION_RATE="
        f"{nonelite_retention_rate:.4f}"
    )

    elite_minus_nonelite_retention_pp = (
        elite_retention_rate
        - nonelite_retention_rate
    ) * 100.0

    print(
        f"ELITE_MINUS_NONELITE_RETENTION_PP="
        f"{elite_minus_nonelite_retention_pp:.2f}"
    )

    print()
    print(
        "PERSISTENCE VS FINISH="
    )

    metrics = [
        "persistent_elite_2",
        "persistent_elite_3",
        "retained_prior_elite_count",
        "retained_prior_elite_share",
        "new_elite_count",
    ]

    for metric in metrics:
        valid = [
            row
            for row in persistence_rows
            if row[
                metric
            ] is not None
        ]

        values = [
            float(
                row[metric]
            )
            for row in valid
        ]

        finishes = [
            float(
                row["finish_rank"]
            )
            for row in valid
        ]

        top6_values = [
            float(
                row[metric]
            )
            for row in valid
            if row["top6"]
        ]

        other_values = [
            float(
                row[metric]
            )
            for row in valid
            if not row["top6"]
        ]

        p_value = pearson(
            values,
            finishes,
        )

        s_value = spearman(
            values,
            finishes,
        )

        top6_avg = (
            sum(top6_values)
            / len(top6_values)
            if top6_values
            else None
        )

        other_avg = (
            sum(other_values)
            / len(other_values)
            if other_values
            else None
        )

        p_text = (
            f"{p_value:.4f}"
            if p_value is not None
            else "NA"
        )

        s_text = (
            f"{s_value:.4f}"
            if s_value is not None
            else "NA"
        )

        top6_text = (
            f"{top6_avg:.4f}"
            if top6_avg is not None
            else "NA"
        )

        other_text = (
            f"{other_avg:.4f}"
            if other_avg is not None
            else "NA"
        )

        print(
            f"{metric} | "
            f"n={len(valid)} | "
            f"pearson_finish={p_text} | "
            f"spearman_finish={s_text} | "
            f"top6_avg={top6_text} | "
            f"non_top6_avg={other_text}"
        )

    print()
    print(
        "SEASON-BY-SEASON PERSISTENT_ELITE_2="
    )

    for season in range(
        2018,
        2026,
    ):
        rows = [
            row
            for row in persistence_rows
            if row[
                "season_year"
            ] == season
            and row[
                "persistent_elite_2"
            ] is not None
        ]

        top6 = [
            row[
                "persistent_elite_2"
            ]
            for row in rows
            if row["top6"]
        ]

        others = [
            row[
                "persistent_elite_2"
            ]
            for row in rows
            if not row["top6"]
        ]

        top6_avg = (
            sum(top6)
            / len(top6)
        )

        other_avg = (
            sum(others)
            / len(others)
        )

        direction = (
            "TOP6_HIGHER"
            if top6_avg > other_avg
            else (
                "EQUAL"
                if top6_avg
                == other_avg
                else "TOP6_LOWER"
            )
        )

        print(
            f"{season} | "
            f"top6_avg={top6_avg:.4f} | "
            f"non_top6_avg={other_avg:.4f} | "
            f"{direction}"
        )

    print()
    print(
        "PERSISTENT_ELITE_VALIDATION=PASS"
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
        standings,
    ) = validate_foundation()

    (
        candidate_cache,
        stats_cache,
    ) = validate_historical_identity(
        controlled_rows
    )

    player_rows = build_player_quality_rows(
        controlled_rows,
        candidate_cache,
        stats_cache,
    )

    calculate_quality_percentiles(
        player_rows
    )

    franchise_rows = (
        build_franchise_quality_rows(
            player_rows,
            standings,
        )
    )

    print_quantity_quality_results(
        player_rows,
        franchise_rows,
    )

    (
        persistence_rows,
        retention,
    ) = build_persistence_rows(
        player_rows,
        franchise_rows,
    )

    print_persistence_results(
        persistence_rows,
        retention,
    )


if __name__ == "__main__":
    main()
