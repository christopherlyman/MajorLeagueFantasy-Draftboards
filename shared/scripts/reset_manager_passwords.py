import argparse
import csv
import os
from datetime import datetime, timezone

import bcrypt
import psycopg


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password(length: int = 12) -> str:
    import secrets
    return "".join(secrets.choice(ALPHABET) for _ in range(int(length)))


def sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def read_targets(path: str) -> list[str]:
    out = []
    seen = set()
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            v = raw.strip()
            if not v:
                continue
            if v in seen:
                raise SystemExit(f"Duplicate target in input file: {v}")
            seen.add(v)
            out.append(v)
    if not out:
        raise SystemExit("Target file is empty.")
    return out


def load_candidates(dsn: str, league_key: str, season_year: int) -> list[dict]:
    sql = """
    select
      u.user_id,
      u.email_normalized,
      u.active as user_active,
      u.must_change_password,
      u.is_site_admin,
      r.league_key,
      r.franchise_id,
      r.role_code,
      r.active as league_role_active,
      fst.team_key,
      fst.team_name,
      u.password_hash
    from public.auth_user u
    left join public.auth_user_league_role r
      on r.user_id = u.user_id
     and r.league_key = %s
    left join public.franchise_season_team fst
      on fst.league_key = r.league_key
     and fst.season_year = %s
     and fst.franchise_id = r.franchise_id
    order by coalesce(fst.team_name, ''), u.email_normalized;
    """
    out = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(league_key), int(season_year)))
            for row in cur.fetchall():
                out.append(
                    {
                        "user_id": int(row[0]),
                        "email_normalized": str(row[1]),
                        "user_active": bool(row[2]),
                        "must_change_password_before": bool(row[3]),
                        "is_site_admin": bool(row[4]),
                        "league_key": str(row[5] or ""),
                        "franchise_id": int(row[6]) if row[6] is not None else None,
                        "role_code": str(row[7] or ""),
                        "league_role_active": bool(row[8]) if row[8] is not None else False,
                        "team_key": str(row[9] or ""),
                        "team_name": str(row[10] or ""),
                        "password_hash_before": str(row[11] or ""),
                    }
                )
    return out


def build_match_index(candidates: list[dict], selector: str) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for c in candidates:
        if not c["user_active"]:
            continue
        if c["league_key"] == "":
            continue
        if not c["league_role_active"]:
            continue

        if selector == "email":
            key = c["email_normalized"]
        elif selector == "team_key":
            key = c["team_key"]
        elif selector == "team_name":
            key = c["team_name"]
        else:
            raise SystemExit(f"Unsupported selector: {selector}")

        if not key:
            continue
        idx.setdefault(key, []).append(c)
    return idx


def resolve_targets(candidates: list[dict], selector: str, targets: list[str]) -> list[dict]:
    idx = build_match_index(candidates, selector)
    resolved = []

    for target in targets:
        matches = idx.get(target, [])
        if len(matches) == 0:
            raise SystemExit(f"No active league-mapped match for {selector}='{target}'")
        if len(matches) > 1:
            raise SystemExit(f"Ambiguous match for {selector}='{target}'")
        resolved.append(matches[0])

    # De-dup by user_id after resolution
    dedup = {}
    for r in resolved:
        dedup[r["user_id"]] = r
    return list(dedup.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-key", required=True)
    parser.add_argument("--season-year", required=True, type=int)
    parser.add_argument("--selector", required=True, choices=["email", "team_key", "team_name"])
    parser.add_argument("--targets-file", required=True)
    parser.add_argument("--output-dir", default="/app/outputs/reset_outputs")
    parser.add_argument("--password-length", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("MLF_POSTGRES_DSN", "").strip()
    if not dsn:
        raise SystemExit("Missing MLF_POSTGRES_DSN")

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    targets = read_targets(args.targets_file)
    candidates = load_candidates(dsn, args.league_key, args.season_year)
    resolved = resolve_targets(candidates, args.selector, targets)

    rollback_path = os.path.join(args.output_dir, f"reset_passwords_rollback_{stamp}.sql")
    passwords_path = os.path.join(args.output_dir, f"reset_passwords_temp_passwords_{stamp}.csv")
    receipt_path = os.path.join(args.output_dir, f"reset_passwords_receipt_{stamp}.txt")

    if args.dry_run:
        print("DRY_RUN_OK")
        print("SELECTOR =", args.selector)
        print("TARGET_COUNT =", len(resolved))
        for r in sorted(resolved, key=lambda x: (x["team_name"], x["email_normalized"])):
            print(
                f"{r['team_name']} | {r['team_key']} | "
                f"{r['email_normalized']} | user_id={r['user_id']} | "
                f"must_change_password_before={r['must_change_password_before']}"
            )
        return

    for r in resolved:
        r["temporary_password"] = generate_temp_password(args.password_length)

    with open(rollback_path, "w", encoding="utf-8") as f:
        f.write("-- rollback generated by reset_manager_passwords.py\n")
        f.write(f"-- generated_utc={stamp}\n\n")
        for r in resolved:
            f.write("UPDATE public.auth_user\n")
            f.write("SET password_hash = " + sql_quote(r["password_hash_before"]) + ",\n")
            f.write(
                "    must_change_password = "
                + ("true" if r["must_change_password_before"] else "false")
                + "\n"
            )
            f.write(f"WHERE user_id = {int(r['user_id'])};\n\n")

    with open(passwords_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "team_name",
                "team_key",
                "email_normalized",
                "user_id",
                "temporary_password",
            ]
        )
        for r in sorted(resolved, key=lambda x: (x["team_name"], x["email_normalized"])):
            writer.writerow(
                [
                    r["team_name"],
                    r["team_key"],
                    r["email_normalized"],
                    r["user_id"],
                    r["temporary_password"],
                ]
            )

    update_user_sql = """
    update public.auth_user
    set password_hash = %s,
        must_change_password = true
    where user_id = %s
      and active = true;
    """

    revoke_sessions_sql = """
    update public.auth_session
    set revoked_at_utc = now()
    where user_id = any(%s)
      and revoked_at_utc is null;
    """

    user_ids = [int(r["user_id"]) for r in resolved]

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(revoke_sessions_sql, (user_ids,))
            revoked_sessions = int(cur.rowcount or 0)

            updated_users = 0
            for r in resolved:
                password_hash = bcrypt.hashpw(
                    r["temporary_password"].encode("utf-8"),
                    bcrypt.gensalt(rounds=12),
                ).decode("utf-8")
                cur.execute(update_user_sql, (password_hash, int(r["user_id"])))
                updated_users += int(cur.rowcount or 0)

        conn.commit()

    with open(receipt_path, "w", encoding="utf-8") as f:
        f.write(f"generated_utc={stamp}\n")
        f.write(f"league_key={args.league_key}\n")
        f.write(f"season_year={args.season_year}\n")
        f.write(f"selector={args.selector}\n")
        f.write(f"targets_file={args.targets_file}\n")
        f.write(f"updated_users={updated_users}\n")
        f.write(f"revoked_sessions={revoked_sessions}\n")
        f.write(f"rollback_path={rollback_path}\n")
        f.write(f"passwords_path={passwords_path}\n")
        f.write("resolved_targets:\n")
        for r in sorted(resolved, key=lambda x: (x["team_name"], x["email_normalized"])):
            f.write(
                f"  {r['team_name']} | {r['team_key']} | "
                f"{r['email_normalized']} | user_id={r['user_id']}\n"
            )

    print("RESET_COMPLETE")
    print("SELECTOR =", args.selector)
    print("UPDATED_USERS =", updated_users)
    print("REVOKED_ACTIVE_SESSIONS =", revoked_sessions)
    print("ROLLBACK_FILE =", rollback_path)
    print("PASSWORDS_FILE =", passwords_path)
    print("RECEIPT_FILE =", receipt_path)


if __name__ == "__main__":
    main()
