import argparse
import hashlib
import json
import os
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

    rows_sha256 = canonical_rows_sha256(
        rows
    )

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


HISTORY_DB_COLUMNS = (
    "source_id",
    "source_kind",
    "source_sha256",
    "source_sheet_name",
    "source_row_number",
    "source_col_index",
    "source_col_label",
    "franchise_id",
    "player_name",
    "yahoo_player_key",
    "season_year",
    "raw_value",
    "years_remaining",
    "contract_label",
    "event_flags",
    "is_year_marker",
)


HISTORY_INSERT_SQL = """
INSERT INTO mlf.contract_history_cell (
    source_id,
    source_kind,
    source_sha256,
    source_sheet_name,
    source_row_number,
    source_col_index,
    source_col_label,
    franchise_id,
    player_name,
    yahoo_player_key,
    season_year,
    raw_value,
    years_remaining,
    contract_label,
    event_flags,
    is_year_marker
)
VALUES (
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s
)
"""


def canonical_rows_sha256(
    rows: list[dict[str, object]],
) -> str:
    payload = "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    ).encode("utf-8")

    return hashlib.sha256(
        payload
    ).hexdigest()


def validate_sha256(
    value: str,
    label: str,
) -> str:
    normalized = value.strip().lower()

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        normalized,
    ):
        raise ValueError(
            f"{label} must be "
            "64 lowercase hex characters."
        )

    return normalized


def load_migration_statements(
    migration_path: Path,
    expected_sha256: str,
) -> list[str]:
    actual_sha256 = sha256_file(
        migration_path
    )

    if actual_sha256 != expected_sha256:
        raise ValueError(
            "Migration SHA mismatch: "
            f"expected={expected_sha256} "
            f"actual={actual_sha256}"
        )

    sql_text = migration_path.read_text(
        encoding="utf-8"
    )

    # Migration 011 is hash-pinned above.
    # Its SQL intentionally contains no
    # procedural bodies or semicolons inside
    # string literals. This is not intended
    # to be a general SQL parser.
    sql_without_comments = "\n".join(
        line
        for line in sql_text.splitlines()
        if not line.lstrip().startswith(
            "--"
        )
    )

    statements = [
        statement.strip()
        for statement
        in sql_without_comments.split(";")
        if statement.strip()
    ]

    if len(statements) != 7:
        raise ValueError(
            "Migration 011 statement shape "
            f"changed: {len(statements)}"
        )

    if statements[0].upper() != "BEGIN":
        raise ValueError(
            "Migration 011 must begin "
            "with BEGIN."
        )

    if statements[-1].upper() != "COMMIT":
        raise ValueError(
            "Migration 011 must end "
            "with COMMIT."
        )

    body = statements[1:-1]

    for statement in body:
        if re.match(
            r"^(BEGIN|COMMIT|ROLLBACK)\b",
            statement,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Unexpected transaction "
                "control inside migration body."
            )

    return body


def fetch_database_rows(
    cursor: object,
    source_id: str,
) -> list[dict[str, object]]:
    cursor.execute(
        """
        SELECT
            source_id,
            source_kind,
            source_sha256,
            source_sheet_name,
            source_row_number,
            source_col_index,
            source_col_label,
            franchise_id,
            player_name,
            yahoo_player_key,
            season_year,
            raw_value,
            years_remaining,
            contract_label,
            event_flags,
            is_year_marker
        FROM mlf.contract_history_cell
        WHERE source_id = %s
        ORDER BY
            franchise_id,
            lower(source_sheet_name),
            source_row_number,
            season_year DESC,
            source_col_index
        """,
        (source_id,),
    )

    return [
        dict(
            zip(
                HISTORY_DB_COLUMNS,
                record,
            )
        )
        for record in cursor.fetchall()
    ]


def run_database_dry_run(
    rows: list[dict[str, object]],
    stats: dict[str, object],
    migration_path: Path,
    expected_migration_sha256: str,
    dsn_env: str,
) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required only for "
            "db-dry-run mode."
        ) from exc

    dsn = os.environ.get(
        dsn_env
    )

    if not dsn:
        raise RuntimeError(
            f"DSN environment variable "
            f"{dsn_env!r} is not set."
        )

    statements = load_migration_statements(
        migration_path,
        expected_migration_sha256,
    )

    print(
        "DB_DRY_RUN_DSN_ENV|"
        + dsn_env
    )

    print(
        "DB_DRY_RUN_MIGRATION_STATEMENTS|"
        + str(len(statements))
    )

    conn = psycopg.connect(dsn)
    rolled_back = False

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    to_regclass(
                        'mlf.contract_history_cell'
                    )
                """
            )

            if cursor.fetchone()[0] is not None:
                raise RuntimeError(
                    "contract_history_cell "
                    "already exists before dry run."
                )

            cursor.execute(
                """
                SELECT count(*)
                FROM mlf.schema_migration
                WHERE migration_version = '011'
                """
            )

            if cursor.fetchone()[0] != 0:
                raise RuntimeError(
                    "Migration 011 already exists "
                    "before dry run."
                )

            print(
                "DB_DRY_RUN_PRECONDITION=PASS"
            )

            for statement in statements:
                cursor.execute(
                    statement
                )

            cursor.execute(
                """
                SELECT
                    to_regclass(
                        'mlf.contract_history_cell'
                    ) IS NOT NULL
                """
            )

            table_exists = cursor.fetchone()[0]

            if not table_exists:
                raise RuntimeError(
                    "Migration did not create "
                    "contract_history_cell."
                )

            cursor.execute(
                """
                SELECT count(*)
                FROM mlf.schema_migration
                WHERE migration_version = '011'
                """
            )

            if cursor.fetchone()[0] != 1:
                raise RuntimeError(
                    "Migration 011 ledger row "
                    "not present inside transaction."
                )

            print(
                "DB_DRY_RUN_MIGRATION_011=PASS"
            )

            values = [
                tuple(
                    row[column]
                    for column
                    in HISTORY_DB_COLUMNS
                )
                for row in rows
            ]

            cursor.executemany(
                HISTORY_INSERT_SQL,
                values,
            )

            source_id = str(
                stats["source_id"]
            )

            cursor.execute(
                """
                SELECT count(*)
                FROM mlf.contract_history_cell
                WHERE source_id = %s
                """,
                (source_id,),
            )

            database_row_count = (
                cursor.fetchone()[0]
            )

            cursor.execute(
                """
                SELECT count(
                    DISTINCT (
                        source_sheet_name,
                        source_row_number
                    )
                )
                FROM mlf.contract_history_cell
                WHERE source_id = %s
                """,
                (source_id,),
            )

            database_episode_count = (
                cursor.fetchone()[0]
            )

            cursor.execute(
                """
                SELECT count(*)
                FROM mlf.contract_history_cell
                WHERE source_id = %s
                  AND is_year_marker
                """,
                (source_id,),
            )

            database_marker_count = (
                cursor.fetchone()[0]
            )

            if (
                database_row_count
                != int(stats["cell_count"])
            ):
                raise RuntimeError(
                    "Database row-count mismatch."
                )

            if (
                database_episode_count
                != int(stats["episode_rows"])
            ):
                raise RuntimeError(
                    "Database episode-count "
                    "mismatch."
                )

            if (
                database_marker_count
                != int(
                    stats[
                        "year_marker_count"
                    ]
                )
            ):
                raise RuntimeError(
                    "Database marker-count "
                    "mismatch."
                )

            database_rows = (
                fetch_database_rows(
                    cursor,
                    source_id,
                )
            )

            database_rows_sha256 = (
                canonical_rows_sha256(
                    database_rows
                )
            )

            expected_rows_sha256 = str(
                stats["rows_sha256"]
            )

            if (
                database_rows_sha256
                != expected_rows_sha256
            ):
                raise RuntimeError(
                    "Database round-trip row "
                    "hash mismatch: "
                    f"expected="
                    f"{expected_rows_sha256} "
                    f"actual="
                    f"{database_rows_sha256}"
                )

            print(
                "DB_DRY_RUN_HISTORY_ROWS|"
                + str(database_row_count)
            )

            print(
                "DB_DRY_RUN_EPISODES|"
                + str(database_episode_count)
            )

            print(
                "DB_DRY_RUN_YEAR_MARKERS|"
                + str(database_marker_count)
            )

            print(
                "DB_DRY_RUN_ROWS_SHA256|"
                + database_rows_sha256
            )

            print(
                "DB_DRY_RUN_IN_TRANSACTION=PASS"
            )

        conn.rollback()
        rolled_back = True

        print(
            "DB_DRY_RUN_ROLLBACK=PASS"
        )

    finally:
        if not rolled_back:
            try:
                conn.rollback()
            except Exception:
                pass

        conn.close()

    verify_conn = psycopg.connect(
        dsn,
        autocommit=True,
    )

    try:
        with verify_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COALESCE(
                        to_regclass(
                            'mlf.contract_history_cell'
                        )::text,
                        'ABSENT'
                    )
                """
            )

            post_table = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT count(*)
                FROM mlf.schema_migration
                WHERE migration_version = '011'
                """
            )

            post_migration_rows = (
                cursor.fetchone()[0]
            )

    finally:
        verify_conn.close()

    print(
        "POST_ROLLBACK_TABLE|"
        + str(post_table)
    )

    print(
        "POST_ROLLBACK_MIGRATION_011|"
        + str(post_migration_rows)
    )

    if post_table != "ABSENT":
        raise RuntimeError(
            "Dry-run table persisted "
            "after rollback."
        )

    if post_migration_rows != 0:
        raise RuntimeError(
            "Migration 011 persisted "
            "after rollback."
        )

    print(
        "DB_DRY_RUN=PASS"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mode",
        choices=(
            "fingerprint",
            "db-dry-run",
        ),
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

    parser.add_argument(
        "--migration",
    )

    parser.add_argument(
        "--expected-migration-sha256",
    )

    parser.add_argument(
        "--dsn-env",
        default="MLF_POSTGRES_DSN",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    expected_sha256 = validate_sha256(
        args.expected_sha256,
        "expected workbook SHA256",
    )

    workbook_path = Path(
        args.workbook
    )

    if not workbook_path.is_file():
        raise FileNotFoundError(
            workbook_path
        )

    rows, stats = parse_workbook(
        workbook_path,
        expected_sha256,
    )

    print_fingerprint(stats)

    if args.mode == "fingerprint":
        return

    if not args.migration:
        raise ValueError(
            "--migration is required "
            "for db-dry-run mode."
        )

    if not args.expected_migration_sha256:
        raise ValueError(
            "--expected-migration-sha256 "
            "is required for db-dry-run mode."
        )

    migration_path = Path(
        args.migration
    )

    if not migration_path.is_file():
        raise FileNotFoundError(
            migration_path
        )

    expected_migration_sha256 = (
        validate_sha256(
            args.expected_migration_sha256,
            "expected migration SHA256",
        )
    )

    run_database_dry_run(
        rows,
        stats,
        migration_path,
        expected_migration_sha256,
        args.dsn_env,
    )


if __name__ == "__main__":
    main()
