from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

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


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def collect_scalar(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = collect_scalar(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = collect_scalar(v, key)
            if found is not None:
                return found
    return None


def extract_players(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for d in walk(payload):
        if not isinstance(d, dict) or "player" not in d:
            continue

        node = d["player"]
        player_key = collect_scalar(node, "player_key")
        if not player_key:
            continue

        player_key = str(player_key)
        if player_key in seen:
            continue
        seen.add(player_key)

        name_obj = collect_scalar(node, "name")
        full_name = ""
        if isinstance(name_obj, dict):
            full_name = str(name_obj.get("full") or "")

        bye_obj = collect_scalar(node, "bye_weeks")
        bye_week = None
        if isinstance(bye_obj, dict):
            bye_week = bye_obj.get("week")

        percent_obj = collect_scalar(node, "percent_owned")
        percent_owned = None
        if isinstance(percent_obj, list):
            for item in percent_obj:
                if isinstance(item, dict) and "value" in item:
                    percent_owned = item.get("value")
        elif isinstance(percent_obj, dict):
            percent_owned = percent_obj.get("value")
        elif percent_obj is not None:
            percent_owned = percent_obj

        try:
            percent_owned_num = float(percent_owned) if percent_owned not in (None, "", "-") else None
        except Exception:
            percent_owned_num = None

        out.append(
            {
                "yahoo_player_key": player_key,
                "full_name": full_name,
                "bye_week": None if bye_week is None else str(bye_week),
                "percent_owned": percent_owned_num,
                "raw_payload": node,
            }
        )

    return out


def main() -> None:
    dsn = (os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN.")

    batch_size = int(os.environ.get("YAHOO_META_BATCH_SIZE", "25"))
    sleep_seconds = float(os.environ.get("YAHOO_SLEEP_SECONDS", "0.25"))
    raw_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo")) / "current_player_metadata_backfill"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_league_key, current_season_year
                FROM nffl.v_active_season_context
                LIMIT 1
            """)
            ctx = cur.fetchone()

            if not ctx:
                raise RuntimeError("No active season context found.")

            league_key = str(ctx["current_league_key"])
            season_year = int(ctx["current_season_year"])

            cur.execute("""
                SELECT yahoo_player_key
                FROM nffl.player_universe
                WHERE league_key=%s
                  AND season_year=%s
                ORDER BY yahoo_player_key
            """, (league_key, season_year))
            player_keys = [str(r["yahoo_player_key"]) for r in cur.fetchall()]

    print(f"ACTIVE_CONTEXT league={league_key} season={season_year} player_keys={len(player_keys)}")

    token = get_access_token()
    total_seen = 0
    total_updated = 0

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for batch_num, batch in enumerate(chunks(player_keys, batch_size), start=1):
                keys = ",".join(batch)
                url = f"{BASE}/players;player_keys={keys};out=percent_owned?format=json"

                resp = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=45,
                )

                print(f"GET batch={batch_num} count={len(batch)} HTTP={resp.status_code}")

                raw_path = raw_dir / f"batch_{batch_num:03d}.json"
                raw_path.write_text(resp.text, encoding="utf-8")

                if resp.status_code >= 400:
                    print(resp.text[:1000])
                    resp.raise_for_status()

                rows = extract_players(resp.json())
                total_seen += len(rows)

                for r in rows:
                    cur.execute(
                        """
                        UPDATE nffl.player_universe
                        SET
                            percent_owned = %s,
                            raw_payload = %s::jsonb,
                            updated_at_utc = now()
                        WHERE league_key=%s
                          AND season_year=%s
                          AND yahoo_player_key=%s
                        """,
                        (
                            r["percent_owned"],
                            json.dumps(r["raw_payload"]),
                            league_key,
                            season_year,
                            r["yahoo_player_key"],
                        ),
                    )
                    total_updated += cur.rowcount

                conn.commit()
                time.sleep(sleep_seconds)

    print(f"CURRENT_METADATA_BACKFILL seen={total_seen} updated={total_updated} raw_dir={raw_dir}")


if __name__ == "__main__":
    main()
