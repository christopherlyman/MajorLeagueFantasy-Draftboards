import os
import csv
from datetime import datetime, timezone

import psycopg


def main() -> None:
    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var: MLF_POSTGRES_DSN")

    snapshot_id = os.environ.get("SNAPSHOT_ID")
    if not snapshot_id:
        raise SystemExit("Missing env var: SNAPSHOT_ID")

    league_key = os.environ.get("LEAGUE_KEY")
    if not league_key:
        raise SystemExit("Missing env var: LEAGUE_KEY")

    season_year = int(os.environ.get("SEASON_YEAR", "0"))
    if not season_year:
        raise SystemExit("Missing env var: SEASON_YEAR")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.environ.get("OUT_DIR", "data/raw/contracts")
    out_path = f"{out_dir}/contract_discrepancies_{league_key.replace('.', '_')}_{season_year}_{ts}.csv"

    sql = """
    select *
    from public.v_contract_discrepancies
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

    os.makedirs(out_dir, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {out_path}")


if __name__ == "__main__":
    main()
