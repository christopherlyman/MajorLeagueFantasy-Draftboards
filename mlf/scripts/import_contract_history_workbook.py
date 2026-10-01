import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET


MAIN_NS = (
    "http://schemas.openxmlformats.org/"
    "spreadsheetml/2006/main"
)

REL_NS = (
    "http://schemas.openxmlformats.org/"
    "officeDocument/2006/relationships"
)

EXPECTED_YEARS = (
    2026,
    2025,
    2024,
    2023,
    2022,
    2021,
    2020,
    2019,
    2018,
    2017,
)

FRANCHISE_BY_SHEET = {
    "Chris L": 1,
    "Brent": 2,
    "Cole": 3,
    "Victor": 4,
    "Stephen": 5,
    "Zac": 6,
    "Dearns": 7,
    "Justin V": 8,
    "Justin C": 9,
    "Justin S": 10,
    "Nate": 11,
    "James": 12,
    "Sean": 13,
    "Waite": 14,
    "Nico": 15,
    "Chad": 16,
}

HELPER_LABELS = {
    "players",
    "past contracts",
    "total contracts",
    "years of control",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def split_cell_reference(
    reference: str,
) -> tuple[int, int, str]:
    match = re.fullmatch(
        r"([A-Z]+)(\d+)",
        reference,
    )

    if match is None:
        raise ValueError(
            f"Unsupported XLSX cell reference: "
            f"{reference!r}"
        )

    label = match.group(1)
    row_number = int(match.group(2))

    column_number = 0

    for character in label:
        column_number = (
            column_number * 26
            + ord(character)
            - 64
        )

    return (
        column_number,
        row_number,
        label,
    )


def column_label(
    column_number: int,
) -> str:
    if column_number < 1:
        raise ValueError(
            "column_number must be positive."
        )

    label = ""
    number = column_number

    while number:
        number, remainder = divmod(
            number - 1,
            26,
        )

        label = (
            chr(65 + remainder)
            + label
        )

    return label


def normalize_cell_value(
    cell_type: str | None,
    value: str,
) -> str:
    value = value.strip()

    if not value:
        return ""

    if cell_type in (
        None,
        "n",
    ):
        try:
            numeric_value = float(value)

            if numeric_value.is_integer():
                return str(
                    int(numeric_value)
                )
        except ValueError:
            pass

    return value


def parse_semantics(
    raw_value: str,
    season_year: int,
) -> tuple[
    int | None,
    str | None,
    list[str],
    bool,
]:
    value = raw_value.strip()

    is_year_marker = (
        value == str(season_year)
    )

    years_remaining = None
    contract_label = None
    event_flags: list[str] = []

    if is_year_marker:
        return (
            None,
            None,
            event_flags,
            True,
        )

    years_match = re.match(
        r"^\s*([1-5])\b",
        value,
    )

    if years_match is not None:
        years_remaining = int(
            years_match.group(1)
        )

    upper_value = value.upper()

    if re.search(
        r"\bFT\b",
        upper_value,
    ):
        contract_label = "FT"

    elif re.search(
        r"\bPT\b",
        upper_value,
    ):
        contract_label = "PT"

    lower_value = value.lower()

    if (
        " to " in lower_value
        or re.search(
            r"\(to\b",
            lower_value,
        )
        or " to)" in lower_value
    ):
        event_flags.append("to")

    if (
        " from " in lower_value
        or re.search(
            r"\(from\b",
            lower_value,
        )
        or " from)" in lower_value
    ):
        event_flags.append("from")

    if "waiver" in lower_value:
        event_flags.append("waiver")

    if (
        re.search(
            r"\bfa\b",
            lower_value,
        )
        or "free agent" in lower_value
    ):
        event_flags.append("fa")

    return (
        years_remaining,
        contract_label,
        event_flags,
        False,
    )


def load_shared_strings(
    archive: zipfile.ZipFile,
) -> list[str]:
    if (
        "xl/sharedStrings.xml"
        not in archive.namelist()
    ):
        return []

    root = ET.fromstring(
        archive.read(
            "xl/sharedStrings.xml"
        )
    )

    strings: list[str] = []

    for item in root.findall(
        f"{{{MAIN_NS}}}si"
    ):
        strings.append(
            "".join(
                element.text or ""
                for element
                in item.iter(
                    f"{{{MAIN_NS}}}t"
                )
            )
        )

    return strings


def load_sheet_paths(
    archive: zipfile.ZipFile,
) -> dict[str, str]:
    workbook = ET.fromstring(
        archive.read(
            "xl/workbook.xml"
        )
    )

    relationships = ET.fromstring(
        archive.read(
            "xl/_rels/workbook.xml.rels"
        )
    )

    relationship_map = {
        element.attrib["Id"]:
        element.attrib["Target"]
        for element in relationships
    }

    sheet_paths: dict[str, str] = {}

    sheets = workbook.find(
        f"{{{MAIN_NS}}}sheets"
    )

    if sheets is None:
        raise ValueError(
            "Workbook has no sheets collection."
        )

    for sheet in sheets:
        relationship_id = (
            sheet.attrib[
                f"{{{REL_NS}}}id"
            ]
        )

        target = relationship_map[
            relationship_id
        ]

        if target.startswith("/"):
            archive_path = (
                target.lstrip("/")
            )
        else:
            archive_path = (
                posixpath.normpath(
                    posixpath.join(
                        "xl",
                        target,
                    )
                )
            )

        sheet_paths[
            sheet.attrib["name"]
        ] = archive_path

    return sheet_paths


def read_cell_value(
    cell: ET.Element,
    shared_strings: list[str],
) -> str:
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        return "".join(
            element.text or ""
            for element
            in cell.iter(
                f"{{{MAIN_NS}}}t"
            )
        )

    value_element = cell.find(
        f"{{{MAIN_NS}}}v"
    )

    if (
        value_element is None
        or value_element.text is None
    ):
        return ""

    raw_value = value_element.text

    if cell_type == "s":
        return shared_strings[
            int(raw_value)
        ]

    return raw_value


def parse_workbook(
    workbook_path: Path,
    expected_sha256: str,
) -> tuple[
    list[dict[str, object]],
    dict[str, object],
]:
    actual_sha256 = sha256_file(
        workbook_path
    )

    if actual_sha256 != expected_sha256:
        raise ValueError(
            "Workbook SHA mismatch: "
            f"expected={expected_sha256} "
            f"actual={actual_sha256}"
        )

    source_id = (
        f"xlsx:{actual_sha256}"
    )

    rows: list[dict[str, object]] = []

    legend_cells_skipped = 0
    current_episode_rows: set[
        tuple[str, int]
    ] = set()
    past_episode_rows: set[
        tuple[str, int]
    ] = set()

    primary_keys: set[
        tuple[str, str, int, int]
    ] = set()

    with zipfile.ZipFile(
        workbook_path
    ) as archive:
        shared_strings = (
            load_shared_strings(
                archive
            )
        )

        sheet_paths = load_sheet_paths(
            archive
        )

        missing_sheets = sorted(
            set(FRANCHISE_BY_SHEET)
            - set(sheet_paths)
        )

        if missing_sheets:
            raise ValueError(
                "Missing manager sheets: "
                + ", ".join(
                    missing_sheets
                )
            )

        for (
            sheet_name,
            franchise_id,
        ) in (
            FRANCHISE_BY_SHEET.items()
        ):
            sheet_root = ET.fromstring(
                archive.read(
                    sheet_paths[
                        sheet_name
                    ]
                )
            )

            cell_values: dict[
                tuple[int, int],
                str,
            ] = {}

            formulas: dict[
                tuple[int, int],
                str | None,
            ] = {}

            for cell in sheet_root.findall(
                f".//{{{MAIN_NS}}}c"
            ):
                reference = (
                    cell.attrib["r"]
                )

                (
                    column_number,
                    row_number,
                    _,
                ) = split_cell_reference(
                    reference
                )

                if not (
                    4
                    <= column_number
                    <= 14
                ):
                    continue

                raw_value = read_cell_value(
                    cell,
                    shared_strings,
                )

                if not raw_value:
                    continue

                value = normalize_cell_value(
                    cell.attrib.get("t"),
                    raw_value,
                )

                if not value:
                    continue

                cell_values[
                    (
                        row_number,
                        column_number,
                    )
                ] = value

                formula_element = (
                    cell.find(
                        f"{{{MAIN_NS}}}f"
                    )
                )

                formulas[
                    (
                        row_number,
                        column_number,
                    )
                ] = (
                    formula_element.text
                    if formula_element
                    is not None
                    else None
                )

            candidate_headers: list[
                tuple[
                    int,
                    dict[int, int],
                ]
            ] = []

            row_numbers = sorted(
                {
                    row_number
                    for (
                        row_number,
                        _,
                    )
                    in cell_values
                }
            )

            for row_number in row_numbers:
                player_token = (
                    cell_values.get(
                        (
                            row_number,
                            4,
                        ),
                        "",
                    )
                    .strip()
                    .rstrip(":")
                    .strip()
                    .casefold()
                )

                if (
                    player_token
                    != "players"
                ):
                    continue

                year_map: dict[
                    int,
                    int,
                ] = {}

                for column_number in range(
                    5,
                    15,
                ):
                    value = (
                        cell_values.get(
                            (
                                row_number,
                                column_number,
                            ),
                            "",
                        )
                        .strip()
                    )

                    if re.fullmatch(
                        r"\d{4}",
                        value,
                    ):
                        year_map[
                            column_number
                        ] = int(value)

                if year_map:
                    candidate_headers.append(
                        (
                            row_number,
                            year_map,
                        )
                    )

            if len(candidate_headers) != 1:
                raise ValueError(
                    f"{sheet_name}: expected "
                    "exactly one history header, "
                    f"found "
                    f"{len(candidate_headers)}"
                )

            (
                header_row,
                year_map,
            ) = candidate_headers[0]

            if tuple(
                year_map.values()
            ) != EXPECTED_YEARS:
                raise ValueError(
                    f"{sheet_name}: unexpected "
                    "history year sequence "
                    f"{tuple(year_map.values())}"
                )

            past_contract_rows = [
                row_number
                for (
                    row_number,
                    column_number,
                ), value
                in cell_values.items()
                if (
                    column_number == 4
                    and value.strip().casefold()
                    == "past contracts"
                )
            ]

            if len(
                past_contract_rows
            ) != 1:
                raise ValueError(
                    f"{sheet_name}: expected "
                    "exactly one PAST CONTRACTS "
                    "separator."
                )

            past_contract_row = (
                past_contract_rows[0]
            )

            for row_number in row_numbers:
                if row_number <= header_row:
                    continue

                player_name = (
                    cell_values.get(
                        (
                            row_number,
                            4,
                        ),
                        "",
                    )
                    .strip()
                )

                if not player_name:
                    continue

                if (
                    player_name.casefold()
                    in HELPER_LABELS
                ):
                    continue

                row_has_history = False

                for (
                    column_number,
                    season_year,
                ) in year_map.items():
                    raw_value = (
                        cell_values.get(
                            (
                                row_number,
                                column_number,
                            ),
                            "",
                        )
                        .strip()
                    )

                    if (
                        not raw_value
                        or raw_value == "0"
                    ):
                        continue

                    formula = formulas.get(
                        (
                            row_number,
                            column_number,
                        )
                    )

                    if (
                        formula
                        and "color key"
                        in formula.casefold()
                    ):
                        legend_cells_skipped += 1
                        continue

                    (
                        years_remaining,
                        contract_label,
                        event_flags,
                        is_year_marker,
                    ) = parse_semantics(
                        raw_value,
                        season_year,
                    )

                    if (
                        years_remaining is None
                        and contract_label
                        is None
                        and not event_flags
                        and not is_year_marker
                    ):
                        raise ValueError(
                            f"{sheet_name} "
                            f"row {row_number} "
                            f"{player_name!r} "
                            f"{season_year}: "
                            "unclassified value "
                            f"{raw_value!r}"
                        )

                    key = (
                        source_id,
                        sheet_name,
                        row_number,
                        season_year,
                    )

                    if key in primary_keys:
                        raise ValueError(
                            "Duplicate canonical "
                            f"cell key: {key!r}"
                        )

                    primary_keys.add(key)

                    row_has_history = True

                    rows.append(
                        {
                            "source_id":
                                source_id,
                            "source_kind":
                                "xlsx",
                            "source_sha256":
                                actual_sha256,
                            "source_sheet_name":
                                sheet_name,
                            "source_row_number":
                                row_number,
                            "source_col_index":
                                column_number,
                            "source_col_label":
                                column_label(
                                    column_number
                                ),
                            "franchise_id":
                                franchise_id,
                            "player_name":
                                player_name,
                            "yahoo_player_key":
                                None,
                            "season_year":
                                season_year,
                            "raw_value":
                                raw_value,
                            "years_remaining":
                                years_remaining,
                            "contract_label":
                                contract_label,
                            "event_flags":
                                event_flags,
                            "is_year_marker":
                                is_year_marker,
                        }
                    )

                if row_has_history:
                    episode_key = (
                        sheet_name,
                        row_number,
                    )

                    if (
                        row_number
                        < past_contract_row
                    ):
                        current_episode_rows.add(
                            episode_key
                        )
                    else:
                        past_episode_rows.add(
                            episode_key
                        )

    rows.sort(
        key=lambda row: (
            int(row["franchise_id"]),
            str(
                row[
                    "source_sheet_name"
                ]
            ).casefold(),
            int(
                row[
                    "source_row_number"
                ]
            ),
            -int(row["season_year"]),
            int(
                row[
                    "source_col_index"
                ]
            ),
        )
    )

    canonical_payload = "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    ).encode("utf-8")

    rows_sha256 = hashlib.sha256(
        canonical_payload
    ).hexdigest()

    season_counts = Counter(
        int(row["season_year"])
        for row in rows
    )

    sheet_counts = Counter(
        str(
            row[
                "source_sheet_name"
            ]
        )
        for row in rows
    )

    label_counts = Counter(
        str(row["contract_label"])
        for row in rows
        if row["contract_label"]
        is not None
    )

    flag_counts = Counter(
        flag
        for row in rows
        for flag in row[
            "event_flags"
        ]
    )

    year_marker_count = sum(
        1
        for row in rows
        if bool(
            row["is_year_marker"]
        )
    )

    years_remaining_count = sum(
        1
        for row in rows
        if row["years_remaining"]
        is not None
    )

    stats: dict[str, object] = {
        "source_sha256":
            actual_sha256,
        "source_id":
            source_id,
        "sheet_count":
            len(FRANCHISE_BY_SHEET),
        "episode_rows":
            (
                len(current_episode_rows)
                + len(past_episode_rows)
            ),
        "current_episode_rows":
            len(current_episode_rows),
        "past_episode_rows":
            len(past_episode_rows),
        "cell_count":
            len(rows),
        "semantic_cell_count":
            (
                len(rows)
                - year_marker_count
            ),
        "legend_cells_skipped":
            legend_cells_skipped,
        "year_marker_count":
            year_marker_count,
        "years_remaining_count":
            years_remaining_count,
        "season_counts":
            season_counts,
        "sheet_counts":
            sheet_counts,
        "label_counts":
            label_counts,
        "flag_counts":
            flag_counts,
        "rows_sha256":
            rows_sha256,
    }

    return rows, stats


def print_fingerprint(
    stats: dict[str, object],
) -> None:
    print(
        "HISTORY_SOURCE_SHA256|"
        f"{stats['source_sha256']}"
    )

    print(
        "HISTORY_SOURCE_ID|"
        f"{stats['source_id']}"
    )

    print(
        "HISTORY_SHEET_COUNT|"
        f"{stats['sheet_count']}"
    )

    print(
        "HISTORY_EPISODE_ROWS|"
        f"{stats['episode_rows']}"
    )

    print(
        "HISTORY_CURRENT_SECTION_ROWS|"
        f"{stats['current_episode_rows']}"
    )

    print(
        "HISTORY_PAST_SECTION_ROWS|"
        f"{stats['past_episode_rows']}"
    )

    print(
        "HISTORY_CELL_COUNT|"
        f"{stats['cell_count']}"
    )

    print(
        "HISTORY_SEMANTIC_CELL_COUNT|"
        f"{stats['semantic_cell_count']}"
    )

    print(
        "HISTORY_LEGEND_CELLS_SKIPPED|"
        f"{stats['legend_cells_skipped']}"
    )

    print(
        "HISTORY_YEAR_MARKERS|"
        f"{stats['year_marker_count']}"
    )

    print(
        "HISTORY_YEARS_REMAINING|"
        f"{stats['years_remaining_count']}"
    )

    sheet_counts = stats[
        "sheet_counts"
    ]

    for (
        sheet_name,
        franchise_id,
    ) in FRANCHISE_BY_SHEET.items():
        print(
            "HISTORY_SHEET|"
            f"{sheet_name}|"
            f"{franchise_id}|"
            f"{sheet_counts[sheet_name]}"
        )

    season_counts = stats[
        "season_counts"
    ]

    for season_year in EXPECTED_YEARS:
        print(
            "HISTORY_SEASON|"
            f"{season_year}|"
            f"{season_counts[season_year]}"
        )

    label_counts = stats[
        "label_counts"
    ]

    for label in (
        "FT",
        "PT",
    ):
        print(
            "HISTORY_LABEL|"
            f"{label}|"
            f"{label_counts[label]}"
        )

    flag_counts = stats[
        "flag_counts"
    ]

    for flag in (
        "to",
        "from",
        "waiver",
        "fa",
    ):
        print(
            "HISTORY_FLAG|"
            f"{flag}|"
            f"{flag_counts[flag]}"
        )

    print(
        "HISTORY_ROWS_SHA256|"
        f"{stats['rows_sha256']}"
    )

    print(
        "HISTORY_UNCLASSIFIED|0"
    )

    print(
        "HISTORY_FINGERPRINT=PASS"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=("fingerprint",),
        required=True,
    )

    parser.add_argument(
        "--workbook",
        required=True,
    )

    parser.add_argument(
        "--expected-sha256",
        required=True,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    expected_sha256 = (
        args.expected_sha256
        .strip()
        .lower()
    )

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        expected_sha256,
    ):
        raise ValueError(
            "expected SHA256 must be "
            "64 lowercase hex characters."
        )

    workbook_path = Path(
        args.workbook
    )

    if not workbook_path.is_file():
        raise FileNotFoundError(
            workbook_path
        )

    _, stats = parse_workbook(
        workbook_path,
        expected_sha256,
    )

    print_fingerprint(stats)


if __name__ == "__main__":
    main()
