import os
import json
import requests
from pathlib import Path

from auth import get_access_token


YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")

def main():
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    # "use_login=1" scopes to the authenticated user
    url = f"{YAHOO_FANTASY_BASE}/users;use_login=1/games?format=json"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "users_games.json"
    out_path.write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")

    print("Wrote:", out_path.as_posix())

if __name__ == "__main__":
    main()
