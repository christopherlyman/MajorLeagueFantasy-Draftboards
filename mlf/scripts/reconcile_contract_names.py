import os
import re
import psycopg


def normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def main():
    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var: MLF_POSTGRES_DSN")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:

            # 1) Identify latest snapshot
            cur.execute("""
                SELECT MAX(snapshot_id)
                FROM ingest_snapshot
            """)
            snapshot_id = cur.fetchone()[0]

            # 2) Pull distinct 2025 contracted player names ONLY
            cur.execute("""
                SELECT DISTINCT player_name
                FROM contracts_cell_raw
                WHERE snapshot_id = %s
                  AND season_year = 2025
                  AND player_name IS NOT NULL
            """, (snapshot_id,))
            contract_names = [r[0] for r in cur.fetchall()]

            # 3) Pull Yahoo players (currently roster universe)
            cur.execute("""
                SELECT yahoo_player_key, yahoo_player_id, full_name
                FROM yahoo_player
            """)
            yahoo_players = cur.fetchall()

            yahoo_by_exact = {full: (key, pid) for key, pid, full in yahoo_players}
            yahoo_by_norm = {normalize(full): (key, pid) for key, pid, full in yahoo_players}

            upserts = []

            for name in contract_names:
                exact = yahoo_by_exact.get(name)
                norm = yahoo_by_norm.get(normalize(name))

                if exact:
                    key, pid = exact
                    match_type = "exact"
                    score = 1.0
                elif norm:
                    key, pid = norm
                    match_type = "normalized"
                    score = 0.95
                else:
                    key = pid = None
                    match_type = "unmatched"
                    score = 0.0

                upserts.append((
                    "contracts",
                    name,
                    key,
                    pid,
                    match_type,
                    score,
                    False,
                    f"snapshot={snapshot_id}, season=2025",
                ))

            sql = """
            INSERT INTO player_name_match (
                source_system,
                source_player_name,
                yahoo_player_key,
                yahoo_player_id,
                match_type,
                match_score,
                is_manual_override,
                notes,
                updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (source_system, source_player_name)
            DO UPDATE SET
                yahoo_player_key   = EXCLUDED.yahoo_player_key,
                yahoo_player_id    = EXCLUDED.yahoo_player_id,
                match_type         = EXCLUDED.match_type,
                match_score        = EXCLUDED.match_score,
                is_manual_override = EXCLUDED.is_manual_override,
                notes              = EXCLUDED.notes,
                updated_at         = now();
            """

            cur.executemany(sql, upserts)
        conn.commit()

    print(f"Processed {len(contract_names)} contracted players for 2025 (snapshot {snapshot_id})")


if __name__ == "__main__":
    main()
