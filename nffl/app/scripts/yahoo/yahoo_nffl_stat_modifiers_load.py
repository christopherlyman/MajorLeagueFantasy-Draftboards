from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
import requests

from auth import get_access_token

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def flatten_scalar_fields(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                nested = flatten_scalar_fields(v)
                for nk, nv in nested.items():
                    out.setdefault(nk, nv)
            else:
                out[k] = v

    elif isinstance(obj, list):
        for item in obj:
            nested = flatten_scalar_fields(item)
            for nk, nv in nested.items():
                out.setdefault(nk, nv)

    return out


def extract_stat_modifiers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for d in walk(payload):
        candidates: list[Any] = []

        if isinstance(d, dict) and "stat_modifier" in d:
            candidates.append(d["stat_modifier"])

        if isinstance(d, dict) and "stat_id" in d and "value" in d:
            candidates.append(d)

        for c in candidates:
            if isinstance(c, list):
                items = c
            else:
                items = [c]

            for item in items:
                flat = flatten_scalar_fields(item)
                stat_id = flat.get("stat_id")
                value = flat.get("value")

                if stat_id is None or value is None:
                    continue

                sid = str(stat_id)
                if sid in seen:
                    continue

                try:
                    modifier = float(str(value))
                except Exception:
                    continue

                seen.add(sid)
                rows.append(
                    {
                        "stat_id": sid,
                        "modifier_value": modifier,
                        "raw": item,
                    }
                )

    rows.sort(key=lambda r: int(r["stat_id"]) if str(r["stat_id"]).isdigit() else 9999)
    return rows


def main() -> None:
    dsn = (os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN.")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT prior_league_key, prior_season_year
                FROM nffl.v_active_season_context
                LIMIT 1
            """)
            row = cur.fetchone()

    if not row:
        raise RuntimeError("No active season context found.")

    league_key = str(row[0])
    season_year = int(row[1])

    raw_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo")) / "stat_modifiers"
    raw_dir.mkdir(parents=True, exist_ok=True)

    token = get_access_token()
    url = f"{BASE}/league/{league_key}/settings?format=json"

    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=45,
    )

    print(f"GET {url}")
    print(f"HTTP {resp.status_code}")

    raw_path = raw_dir / f"league_{league_key.replace('.', '_')}_settings.json"
    raw_path.write_text(resp.text, encoding="utf-8")
    print(f"RAW={raw_path}")

    if resp.status_code >= 400:
        print(resp.text[:1200])
        resp.raise_for_status()

    rows = extract_stat_modifiers(resp.json())

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM nffl.yahoo_stat_modifier WHERE league_key=%s AND season_year=%s",
                (league_key, season_year),
            )

            for r in rows:
                cur.execute(
                    """
                    INSERT INTO nffl.yahoo_stat_modifier (
                        league_key,
                        season_year,
                        stat_id,
                        modifier_value,
                        raw_modifier_json,
                        updated_at_utc
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, now())
                    ON CONFLICT (league_key, season_year, stat_id)
                    DO UPDATE SET
                        modifier_value=EXCLUDED.modifier_value,
                        raw_modifier_json=EXCLUDED.raw_modifier_json,
                        updated_at_utc=now()
                    """,
                    (
                        league_key,
                        season_year,
                        r["stat_id"],
                        r["modifier_value"],
                        json.dumps(r["raw"]),
                    ),
                )

        conn.commit()

    print(f"STAT_MODIFIER_LOAD league={league_key} season={season_year} rows={len(rows)}")

    for r in rows:
        print(f"{r['stat_id']:>4} | {r['modifier_value']}")


if __name__ == "__main__":
    main()
