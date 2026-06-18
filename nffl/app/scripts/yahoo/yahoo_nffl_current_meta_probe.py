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


def main() -> None:
    dsn = (os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN.")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select current_league_key
                from nffl.v_active_season_context
                limit 1
            """)
            league_key = cur.fetchone()[0]

            cur.execute("""
                select yahoo_player_key
                from nffl.player_universe
                where league_key = (select current_league_key from nffl.v_active_season_context limit 1)
                  and season_year = (select current_season_year from nffl.v_active_season_context limit 1)
                  and full_name='Josh Allen'
                limit 1
            """)
            player_key = cur.fetchone()[0]

    token = get_access_token()
    raw_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "/league_runtime/data/raw/yahoo")) / "current_meta_probe"
    raw_dir.mkdir(parents=True, exist_ok=True)

    urls = [
        f"{BASE}/player/{player_key}?format=json",
        f"{BASE}/players;player_keys={player_key};out=percent_owned?format=json",
        f"{BASE}/league/{league_key}/players;player_keys={player_key};out=percent_owned;out=draft_analysis?format=json",
    ]

    for i, url in enumerate(urls, start=1):
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=45,
        )
        print()
        print(f"PROBE_{i}: {url}")
        print(f"HTTP {resp.status_code}")

        out = raw_dir / f"probe_{i}.json"
        out.write_text(resp.text, encoding="utf-8")
        print(f"RAW={out}")

        if resp.status_code >= 400:
            print(resp.text[:1000])
            continue

        payload = resp.json()
        hits = []
        for d in walk(payload):
            if isinstance(d, dict) and any(k in d for k in ("bye_weeks", "percent_owned", "ownership", "player_key", "name")):
                hits.append(d)
            if len(hits) >= 12:
                break

        for h in hits[:12]:
            print(json.dumps(h, indent=2)[:1200])
            print("---")


if __name__ == "__main__":
    main()
