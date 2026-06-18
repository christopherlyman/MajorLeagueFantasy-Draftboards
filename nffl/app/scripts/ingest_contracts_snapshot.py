import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg


EXCLUDE_FILENAMES = {
    "Home.csv",
    "Color key.csv",
    "Proposed Schedule.csv",
    "Proposed rule votes.csv",
    "Past rule votes.csv",
    "All contracts.csv",
    "Keepers 2025.csv",
    "Draft 2026.csv",
    "Draft 2025.csv",
    "Draft 2024.csv",
    "QOs 2025.csv",
    "QOs 2024.csv",
    "Contracts 2025.csv",
    "Contracts 2024.csv",
    "_QOs 2026.csv",
    "_Contracts 2026.csv",
    "_player_eligibilities_import.csv",
    "manifest.json",  # if present
}

YEAR_RE = re.compile(r"^\d{4}$")


def col_label(idx: int) -> str:
    # 0 -> A, 1 -> B ... 25 -> Z, 26 -> AA...
    label = ""
    n = idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        label = chr(65 + r) + label
    return label


def normalize_players_token(s: str) -> str:
    """
    Normalize header token variations:
      "Players", "Players:", " players:  " -> "players"
    """
    return (s or "").strip().rstrip(":").strip().lower()


def is_header_row(row: List[str]) -> bool:
    if not row:
        return False

    # Accept "Players" and "Players:" (and variants)
    if normalize_players_token(row[0]) != "players":
        return False

    # header should contain at least one 4-digit year somewhere
    return any(YEAR_RE.match((c or "").strip()) for c in row[1:])


def extract_year_map_from_header(row: List[str]) -> Dict[int, int]:
    """
    Returns mapping: col_index -> season_year
    Uses any cell matching YYYY in the header row.
    """
    m: Dict[int, int] = {}
    for i, c in enumerate(row):
        s = (c or "").strip()
        if YEAR_RE.match(s):
            m[i] = int(s)
    return m


def parse_cell(raw: str) -> Tuple[Optional[int], Optional[str], List[str]]:
    """
    Conservative parsing:
    - years_remaining: leading integer, if present
    - contract_label: 'FT' or 'PT' if present as standalone token
    - event_flags: detect keywords only
    """
    t = raw.strip()
    if not t:
        return None, None, []

    # label detection (standalone tokens FT/PT)
    upper = t.upper()
    label = None
    if re.search(r"\bFT\b", upper):
        label = "FT"
    elif re.search(r"\bPT\b", upper):
        label = "PT"

    # years: leading integer only (e.g., "4 (to Brent)", "2 / FA")
    years = None
    m = re.match(r"^\s*(\d+)\b", t)
    if m:
        years = int(m.group(1))

    flags = []
    low = t.lower()
    if " to " in low or re.search(r"\(to\b", low) or " to)" in low:
        flags.append("to")
    if " from " in low or re.search(r"\(from\b", low) or " from)" in low:
        flags.append("from")
    if "waiver" in low:
        flags.append("waiver")
    if re.search(r"\bfa\b", low) or "free agent" in low:
        flags.append("fa")

    return years, label, flags


def ingest_managers(cur: psycopg.Cursor, snapshot_id: str, managers_csv: Path) -> None:
    with managers_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row_num, row in enumerate(reader, start=1):
            # store as text[] raw row
            cur.execute(
                """
                INSERT INTO managers_raw_row (snapshot_id, row_number, row_values)
                VALUES (%s, %s, %s)
                ON CONFLICT (snapshot_id, row_number) DO NOTHING
                """,
                (snapshot_id, row_num, row),
            )


def ingest_owner_sheet(cur: psycopg.Cursor, snapshot_id: str, owner_name: str, csv_path: Path) -> None:
    current_year_map: Dict[int, int] = {}
    sheet_name = owner_name

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row_num, row in enumerate(reader, start=1):
            if not row:
                continue

            # detect repeated header blocks (Players / Players:)
            if is_header_row(row):
                current_year_map = extract_year_map_from_header(row)
                continue

            # if we have not yet seen any header row, skip until we do
            if not current_year_map:
                continue

            player = (row[0] or "").strip()
            if not player:
                continue

            # Skip if this is another header-like row
            if normalize_players_token(player) == "players":
                continue

            # For each year-mapped column, store non-empty raw values
            for col_idx, year in current_year_map.items():
                if col_idx >= len(row):
                    continue
                raw = (row[col_idx] or "").strip()
                if not raw:
                    continue

                cur.execute(
                    """
                    INSERT INTO contracts_cell_raw
                      (snapshot_id, owner_name, sheet_name, player_name, season_year,
                       raw_value, row_number, col_index, col_label)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        snapshot_id,
                        owner_name,
                        sheet_name,
                        player,
                        year,
                        raw,
                        row_num,
                        col_idx,
                        col_label(col_idx),
                    ),
                )

                years, label, flags = parse_cell(raw)
                cur.execute(
                    """
                    INSERT INTO contracts_cell_parsed
                      (snapshot_id, owner_name, sheet_name, player_name, season_year,
                       years_remaining, contract_label, event_flags)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (snapshot_id, owner_name, sheet_name, player_name, season_year)
                    DO UPDATE SET
                      years_remaining = EXCLUDED.years_remaining,
                      contract_label  = EXCLUDED.contract_label,
                      event_flags     = EXCLUDED.event_flags
                    """,
                    (
                        snapshot_id,
                        owner_name,
                        sheet_name,
                        player,
                        year,
                        years,
                        label,
                        flags if flags else None,
                    ),
                )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-id", required=True, help="e.g. 2026-02-02__post-renewal")
    ap.add_argument("--snapshot-label", required=True, help="e.g. post-renewal")
    ap.add_argument("--snapshot-path", required=True, help="path to folder containing CSVs")
    ap.add_argument("--pg-dsn", required=True, help="Postgres DSN, e.g. postgresql://mlf:pw@apollo:5432/mlf")
    args = ap.parse_args()

    snapshot_id = args.snapshot_id
    snapshot_label = args.snapshot_label
    snapshot_path = Path(args.snapshot_path)

    managers_csv = snapshot_path / "Managers.csv"
    if not managers_csv.exists():
        raise FileNotFoundError(f"Managers.csv not found at {managers_csv}")

    # Collect owner CSVs: all .csv not in exclude list and not Managers.csv
    csv_files = [p for p in snapshot_path.glob("*.csv")]
    owner_csvs = []
    for p in csv_files:
        if p.name == "Managers.csv":
            continue
        if p.name in EXCLUDE_FILENAMES:
            continue
        owner_csvs.append(p)

    if not owner_csvs:
        raise RuntimeError("No owner CSVs found to ingest (check exclude list / folder).")

    with psycopg.connect(args.pg_dsn) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        with conn.cursor() as cur:
            # register snapshot
            cur.execute(
                """
                INSERT INTO ingest_snapshot (snapshot_id, snapshot_type, snapshot_label, snapshot_path, source_system)
                VALUES (%s, 'contracts', %s, %s, 'google_sheets_apps_script')
                ON CONFLICT (snapshot_id) DO NOTHING
                """,
                (snapshot_id, snapshot_label, str(snapshot_path)),
            )

            ingest_managers(cur, snapshot_id, managers_csv)

            for p in sorted(owner_csvs, key=lambda x: x.name.lower()):
                owner_name = p.stem  # filename without .csv
                ingest_owner_sheet(cur, snapshot_id, owner_name, p)

        conn.commit()

    print(f"OK: ingested snapshot_id={snapshot_id} from {snapshot_path}")
    print(f"Owners ingested: {len(owner_csvs)}")
    print("Managers rows ingested from Managers.csv")


if __name__ == "__main__":
    main()
