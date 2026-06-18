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


def extract_stat_categories(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for d in walk(payload):
        candidates = []
        if isinstance(d, dict) and "stat" in d:
            candidates.append(d["stat"])
        if isinstance(d, dict) and "stat_id" in d:
            candidates.append(d)

        for c in candidates:
            flat = flatten_scalar_fields(c)
            stat_id = flat.get("stat_id")
            if stat_id is None:
                continue

            sid = str(stat_id)
            if sid in seen:
                continue

            seen.add(sid)

            position_types = flat.get("position_types") or flat.get("position_type") or []
            if isinstance(position_types, str):
                position_types_json = [position_types]
            elif isinstance(position_types, list):
                position_types_json = position_types
            else:
                position_types_json = []

            sort_order = flat.get("sort_order")
            try:
                sort_order_int = int(sort_order) if sort_order is not None and str(sort_order).strip() != "" else None
            except Exception:
                sort_order_int = None

            rows.append(
                {
                    "stat_id": sid,
                    "name": str(flat.get("name") or ""),
                    "display_name": str(flat.get("display_name") or flat.get("abbr") or ""),
                    "sort_order": sort_order_int,
                    "position_types": position_types_json,
                    "raw": c,
                }
            )

    rows.sort(key=lambda r: (r["sort_order"] if r["sort_order"] is not None else 9999, int(r["stat_id"]) if r["stat_id"].isdigit() else 9999))
    return rows


def load_context(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                current_season_year,
                current_league_key,
                prior_season_year,
                prior_league_key
            FROM nffl.v_active_season_context
            LIMIT 1
            """
        )
        row = cur.fetchone()

    if not row:
        raise RuntimeError("No active NFFL season context found.")

    return {
        "current_season_year": row[0],
        "current_league_key": row[1],
        "prior_season_year": row[2],
        "prior_league_key": row[3],
    }


def main() -> None:
    dsn = (os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN.")

    with psycopg.connect(dsn) as conn:
        ctx = load_context(conn)

    prior_game_key = str(ctx["prior_league_key"]).split(".l.", 1)[0]
    raw_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo")) / "stat_categories"
    raw_dir.mkdir(parents=True, exist_ok=True)

    token = get_access_token()
    url = f"{BASE}/game/{prior_game_key}/stat_categories?format=json"

    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=45,
    )
    print(f"GET {url}")
    print(f"HTTP {resp.status_code}")

    raw_path = raw_dir / f"game_{prior_game_key}_stat_categories.json"
    raw_path.write_text(resp.text, encoding="utf-8")

    if resp.status_code >= 400:
        print(resp.text[:1200])
        resp.raise_for_status()

    payload = resp.json()
    rows = extract_stat_categories(payload)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO nffl.yahoo_stat_category (
                        game_key,
                        stat_id,
                        name,
                        display_name,
                        sort_order,
                        position_types,
                        raw_stat_json,
                        updated_at_utc
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
                    ON CONFLICT (game_key, stat_id)
                    DO UPDATE SET
                        name=EXCLUDED.name,
                        display_name=EXCLUDED.display_name,
                        sort_order=EXCLUDED.sort_order,
                        position_types=EXCLUDED.position_types,
                        raw_stat_json=EXCLUDED.raw_stat_json,
                        updated_at_utc=now()
                    """,
                    (
                        prior_game_key,
                        r["stat_id"],
                        r["name"],
                        r["display_name"],
                        r["sort_order"],
                        json.dumps(r["position_types"]),
                        json.dumps(r["raw"]),
                    ),
                )
        conn.commit()

    print(
        "STAT_CATEGORY_LOAD "
        f"prior_game_key={prior_game_key} "
        f"rows={len(rows)} "
        f"raw={raw_path}"
    )

    for r in rows[:80]:
        label = r["display_name"] or r["name"]
        print(f"{r['stat_id']:>4} | {label}")


if __name__ == "__main__":
    main()
