# scripts/yahoo_player_stats.py
import os
import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")

def main():
    player_key = os.environ.get("YAHOO_PLAYER_KEY")
    if not player_key:
        raise SystemExit("Missing env var YAHOO_PLAYER_KEY (e.g. 469.p.12401)")

    # Optional: request stats for a season year (Yahoo supports this on many stats endpoints)
    season = os.environ.get("YAHOO_STATS_SEASON")  # e.g. "2025" or "2026"

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Start with the simplest: player endpoint. We'll add /stats once we confirm shape.
    # Many Yahoo fantasy responses include status, editorial_team, eligible_positions, percent_owned, rank, etc.
    url = f"{YAHOO_FANTASY_BASE}/player/{player_key}?format=json"
    if season:
        # Some Yahoo endpoints accept season as a query param; if it doesn't, we'll see it in the payload/ignore.
        url += f"&season={season}"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    out_path = OUT_DIR / f"player_{player_key.replace('.','_')}_detail.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("Wrote:", out_path.as_posix())

if __name__ == "__main__":
    main()
