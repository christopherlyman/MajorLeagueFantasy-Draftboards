#!/usr/bin/env python3
"""
update_mlf_file_inventory.py

Rebuilds (or refreshes) MLF_FILE_INVENTORY.csv by walking the filesystem.

Outputs columns (matching your current inventory):
- FullName
- RelativePath
- Type            (Dir | File)
- SizeBytes
- LastWriteTime   (ISO, seconds)

Typical usage (NAS host):
  python3 update_mlf_file_inventory.py \
    --root "/Volume1/Bots/fantasy/mlf" \
    --out  "/Volume1/Bots/fantasy/mlf/docs/inventory/MLF_FILE_INVENTORY.csv" \
    --unc-root "\\\\Apollo\\Bots\\fantasy\\mlf"

Or inside container (if repo is mounted similarly):
  python3 scripts/update_mlf_file_inventory.py --root /app --out /app/MLF_FILE_INVENTORY.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


COLUMNS = ["FullName", "RelativePath", "Type", "SizeBytes", "LastWriteTime"]


@dataclass(frozen=True)
class InvRow:
    full_name: str
    rel_path: str
    typ: str           # "Dir" | "File"
    size_bytes: Optional[int]
    last_write_time: str


def iso_mtime_local(ts: float) -> str:
    # Match your existing format: "YYYY-MM-DDTHH:MM:SS" (no timezone)
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def to_relpath(root: Path, p: Path) -> str:
    rel = os.path.relpath(str(p), str(root))
    # Inventory uses Windows-style backslashes
    return rel.replace("/", "\\").replace("\\\\", "\\")


def join_unc(unc_root: str, rel_path: str) -> str:
    # unc_root should look like: "\\\\Apollo\\Bots\\fantasy\\mlf"
    unc_root = unc_root.rstrip("\\")
    rel_path = rel_path.lstrip("\\")
    return f"{unc_root}\\{rel_path}" if rel_path not in (".", "") else unc_root


def walk_inventory(root: Path, unc_root: str) -> List[InvRow]:
    rows: List[InvRow] = []

    # Walk dirs first, then files, deterministic ordering
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()

        dpath = Path(dirpath)

        # Skip the root itself (your CSV appears to start at subfolders like "curated")
        if dpath != root:
            st = dpath.stat()
            rel = to_relpath(root, dpath)
            rows.append(
                InvRow(
                    full_name=join_unc(unc_root, rel),
                    rel_path=rel,
                    typ="Dir",
                    size_bytes=None,
                    last_write_time=iso_mtime_local(st.st_mtime),
                )
            )

        for fn in filenames:
            fpath = dpath / fn
            try:
                st = fpath.stat()
            except FileNotFoundError:
                # In case files change during scan
                continue
            rel = to_relpath(root, fpath)
            rows.append(
                InvRow(
                    full_name=join_unc(unc_root, rel),
                    rel_path=rel,
                    typ="File",
                    size_bytes=int(st.st_size),
                    last_write_time=iso_mtime_local(st.st_mtime),
                )
            )

    return rows


def read_existing(csv_path: Path) -> Dict[str, Dict[str, str]]:
    """
    Key by RelativePath for easy comparison.
    Returns raw string dicts (including blanks).
    """
    if not csv_path.exists():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rp = (r.get("RelativePath") or "").strip()
            if not rp:
                continue
            out[rp] = r
    return out


def write_inventory(csv_path: Path, rows: List[InvRow]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "FullName": r.full_name,
                    "RelativePath": r.rel_path,
                    "Type": r.typ,
                    "SizeBytes": "" if r.size_bytes is None else str(r.size_bytes),
                    "LastWriteTime": r.last_write_time,
                }
            )


def summarize_diff(old: Dict[str, Dict[str, str]], new_rows: List[InvRow]) -> str:
    new: Dict[str, InvRow] = {r.rel_path: r for r in new_rows}

    added = sorted(set(new.keys()) - set(old.keys()))
    removed = sorted(set(old.keys()) - set(new.keys()))

    changed = []
    for k in sorted(set(new.keys()) & set(old.keys())):
        o = old[k]
        n = new[k]
        o_type = (o.get("Type") or "").strip()
        o_size = (o.get("SizeBytes") or "").strip()
        o_mtime = (o.get("LastWriteTime") or "").strip()

        n_size = "" if n.size_bytes is None else str(n.size_bytes)

        if (o_type != n.typ) or (o_size != n_size) or (o_mtime != n.last_write_time):
            changed.append(k)

    return (
        f"Diff summary:\n"
        f"- added:   {len(added)}\n"
        f"- removed: {len(removed)}\n"
        f"- changed: {len(changed)}\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Filesystem root to inventory (e.g., /Volume1/Bots/fantasy/mlf)")
    ap.add_argument("--out", required=True, help="Output CSV path")
    ap.add_argument(
        "--unc-root",
        default="\\\\Apollo\\Bots\\fantasy\\mlf",
        help=r'UNC root used for FullName column (default: "\\\\Apollo\\Bots\\fantasy\\mlf")',
    )
    ap.add_argument("--print-diff", action="store_true", help="Print added/removed/changed counts vs existing CSV")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out_csv = Path(args.out).resolve()

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"--root is not a directory: {root}")

    old = read_existing(out_csv) if args.print_diff else {}
    rows = walk_inventory(root, args.unc_root)
    write_inventory(out_csv, rows)

    print(f"Wrote {len(rows)} rows -> {out_csv}")
    if args.print_diff:
        print(summarize_diff(old, rows), end="")


if __name__ == "__main__":
    main()
