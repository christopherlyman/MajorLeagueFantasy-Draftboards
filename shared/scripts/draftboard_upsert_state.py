from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Upsert DraftBoard JSON state into Postgres.")
    ap.add_argument(
        "--draft-key",
        required=True,
        help="Logical identifier for this draft (e.g., mlf_2026_preseason).",
    )
    ap.add_argument(
        "--json-path",
        default="app/draft_state_autosave.json",
        help="Path to DraftBoard JSON state file (relative to /app).",
    )
    args = ap.parse_args()

    dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("POSTGRES_DSN or MLF_POSTGRES_DSN missing in environment.")

    json_path = Path(args.json_path)
    if not json_path.exists():
        raise SystemExit(f"JSON not found: {json_path}")

    raw = json_path.read_bytes()
    state_sha = sha256_hex(raw)

    # Validate JSON and grab schema_version if present
    obj = json.loads(raw.decode("utf-8"))
    schema_version = obj.get("schema_version") or obj.get("schemaVersion") or "unknown"

    upsert_sql = """
    INSERT INTO public.draftboard_state (draft_key, schema_version, state_json, state_sha256, updated_at_utc)
    VALUES (%(draft_key)s, %(schema_version)s, %(state_json)s::jsonb, %(state_sha256)s, now())
    ON CONFLICT (draft_key) DO UPDATE SET
      schema_version = EXCLUDED.schema_version,
      state_json     = EXCLUDED.state_json,
      state_sha256   = EXCLUDED.state_sha256,
      updated_at_utc = now()
    RETURNING draft_key, schema_version, state_sha256, updated_at_utc;
    """

    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                upsert_sql,
                {
                    "draft_key": args.draft_key,
                    "schema_version": schema_version,
                    "state_json": json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
                    "state_sha256": state_sha,
                },
            )
            row = cur.fetchone()
        conn.commit()

    draft_key, schema_version, state_sha256, updated_at_utc = row
    print("UPSERT_OK")
    print("draft_key:", draft_key)
    print("schema_version:", schema_version)
    print("state_sha256:", state_sha256)
    print("updated_at_utc:", updated_at_utc.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
