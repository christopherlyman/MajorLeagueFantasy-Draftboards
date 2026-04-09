import os
import re
import psycopg


def normalize(name: str) -> str:
    """
    Normalize player names for loose matching:
    - lowercase
    - remove punctuation
    - collapse whitespace
    """
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

            # 1) Pull distinct contract player names
            cur.execute("""
                SELECT DISTINCT player_name
                FROM contracts_cell_raw
                WHERE player_name IS NOT NULL
            """)
            contract_names = [r[0] for r in cur.fetchall()]

            # 2) Pull Yahoo players (local roster universe)
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
                    upserts.append((
                        "contracts",
                        name,
                        key,
                        pid,
                        "exact",
                        1.0,
                        False,
                        None,
                    ))
                elif norm:
                    key, pid = norm
                    upserts.append((
                        "contracts",
                        name,
                        key,
                        pid,
                        "normalized",
                        0.95,
                        False,
                        None,
                    ))
                else:
                    upserts.append((
                        "contracts",
                        name,
                        None,
                        None,
                        "unmatched",
                        0.0,
                        False,
                        None,
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
                updated_at         = now()
            ;
            """

            cur.executemany(sql, upserts)
        conn.commit()

    print(f"Processed {len(contract_names)} contract names")


if __name__ == "__main__":
    main()
