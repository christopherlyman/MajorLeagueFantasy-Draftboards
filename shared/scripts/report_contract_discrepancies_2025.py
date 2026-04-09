import os
import csv
from datetime import datetime, timezone

import psycopg


def main() -> None:
    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var: MLF_POSTGRES_DSN")

    snapshot_id = os.environ.get("SNAPSHOT_ID", "2026-02-02__post-renewal")
    league_key = os.environ.get("LEAGUE_KEY", "458.l.11506")
    season_year = int(os.environ.get("SEASON_YEAR", "2025"))

    # Output path (inside container FS; persist if /app/raw is bind-mounted)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.environ.get("OUT_DIR", "data/raw/contracts")
    out_path = f"{out_dir}/contract_discrepancies_{season_year}_{ts}.csv"

    sql = """
    select *
    from public.v_contract_discrepancies_2025
    where snapshot_id = %s
      and league_key = %s
      and season_year = %s
    order by discrepancy_type, sheet_owner_name, player_name
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (snapshot_id, league_key, season_year))
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]

    # Ensure output directory exists
    os.makedirs(out_dir, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {out_path}")


if __name__ == "__main__":
    main()
