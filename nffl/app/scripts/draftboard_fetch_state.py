from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch DraftBoard state from Postgres and write to a JSON file.")
    ap.add_argument("--draft-key", required=True, help="Draft identifier (e.g., mlf_2026_preseason).")
    ap.add_argument(
        "--out",
        default="app/draft_state_from_db.json",
        help="Output JSON path (relative to /app).",
    )
    args = ap.parse_args()

    dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("POSTGRES_DSN or MLF_POSTGRES_DSN missing in environment.")

    sql = """
    SELECT schema_version, state_json, state_sha256, updated_at_utc
    FROM public.draftboard_state
    WHERE draft_key = %(draft_key)s
    """
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"draft_key": args.draft_key})
            row = cur.fetchone()

    if not row:
        raise SystemExit(f"NOT_FOUND: {args.draft_key}")

    schema_version, state_json, state_sha256, updated_at_utc = row

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # state_json comes back as a Python object (psycopg jsonb adaptation)
    out_path.write_text(json.dumps(state_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("FETCH_OK")
    print("draft_key:", args.draft_key)
    print("schema_version:", schema_version)
    print("state_sha256:", state_sha256)
    print("updated_at_utc:", updated_at_utc.isoformat())
    print("wrote:", str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
