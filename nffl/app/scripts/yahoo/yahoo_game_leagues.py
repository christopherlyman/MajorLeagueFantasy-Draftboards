import os
import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")

def main():
    game_key = os.environ.get("YAHOO_GAME_KEY")
    if not game_key:
        raise SystemExit("Missing env var YAHOO_GAME_KEY (e.g. 469 for 2026, 458 for 2025)")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"{YAHOO_FANTASY_BASE}/users;use_login=1/games;game_keys={game_key}/leagues?format=json"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"game_{game_key}_leagues.json"
    out_path.write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")

    print("Wrote:", out_path.as_posix())

if __name__ == "__main__":
    main()
