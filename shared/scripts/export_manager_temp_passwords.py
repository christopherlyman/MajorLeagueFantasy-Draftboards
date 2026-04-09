#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import psycopg


LEAGUE_KEY = os.environ.get("MLF_LEAGUE_KEY", "469.l.41640")
SEASON_YEAR = int(os.environ.get("MLF_SEASON_YEAR", "2026"))
DSN = os.environ.get("MLF_POSTGRES_DSN", "")

# Change this only if you want a different output location.
OUTPUT_DIR = Path("/app/scripts/output")


def generate_temp_password(length: int = 12) -> str:
    # Avoid ambiguous chars for human distribution.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def require_dsn() -> str:
    if not DSN:
        raise RuntimeError("Missing MLF_POSTGRES_DSN env var.")
    return DSN


def load_manager_accounts(conn: psycopg.Connection) -> list[dict]:
    sql = """
        SELECT
            u.user_id,
            u.email_normalized,
            u.active,
            u.is_site_admin,
            r.franchise_id,
            r.role_code,
            fst.team_key,
            fst.team_name
        FROM public.auth_user u
        JOIN public.auth_user_league_role r
          ON r.user_id = u.user_id
         AND r.league_key = %s
         AND r.active = true
        LEFT JOIN public.franchise_season_team fst
          ON fst.franchise_id = r.franchise_id
         AND fst.league_key = r.league_key
         AND fst.season_year = %s
        WHERE u.active = true
          AND coalesce(u.is_site_admin, false) = false
        ORDER BY r.franchise_id, u.email_normalized
    """
    out: list[dict] = []
    with conn.cursor() as cur:
        cur.execute(sql, (LEAGUE_KEY, SEASON_YEAR))
        for row in cur.fetchall():
            out.append(
                {
                    "user_id": int(row[0]),
                    "email_normalized": str(row[1]),
                    "active": bool(row[2]),
                    "is_site_admin": bool(row[3]),
                    "franchise_id": int(row[4]) if row[4] is not None else None,
                    "role_code": str(row[5]) if row[5] is not None else None,
                    "team_key": str(row[6]) if row[6] is not None else "",
                    "team_name": str(row[7]) if row[7] is not None else "",
                }
            )
    return out


def update_password(conn: psycopg.Connection, *, user_id: int, temp_password: str) -> int:
    password_hash = bcrypt.hashpw(
        temp_password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    sql = """
        UPDATE public.auth_user
        SET password_hash = %s,
            must_change_password = true
        WHERE user_id = %s
          AND active = true
    """
    with conn.cursor() as cur:
        cur.execute(sql, (password_hash, int(user_id)))
        return int(cur.rowcount or 0)


def main() -> int:
    dsn = require_dsn()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"manager_temp_passwords_{LEAGUE_KEY.replace('.', '_')}_{SEASON_YEAR}_{ts}.csv"

    with psycopg.connect(dsn) as conn:
        accounts = load_manager_accounts(conn)

        if not accounts:
            print("No active non-site-admin manager accounts found.", file=sys.stderr)
            return 1

        rows_for_csv: list[dict] = []

        for acct in accounts:
            temp_pw = generate_temp_password(12)
            updated = update_password(conn, user_id=acct["user_id"], temp_password=temp_pw)

            if updated != 1:
                conn.rollback()
                raise RuntimeError(
                    f"Password update did not affect exactly one row for {acct['email_normalized']}"
                )

            rows_for_csv.append(
                {
                    "email_normalized": acct["email_normalized"],
                    "team_name": acct["team_name"],
                    "team_key": acct["team_key"],
                    "franchise_id": acct["franchise_id"],
                    "temporary_password": temp_pw,
                }
            )

        conn.commit()

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "email_normalized",
                "team_name",
                "team_key",
                "franchise_id",
                "temporary_password",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_for_csv)

    print(f"WROTE_CSV={output_path}")
    print(f"ROWS={len(rows_for_csv)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())