import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")
LEAGUE_KEY = "469.l.41640"  # Major League Fantasy

def main():
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"{YAHOO_FANTASY_BASE}/league/{LEAGUE_KEY}/settings?format=json"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"league_{LEAGUE_KEY.replace('.','_')}_settings.json"
    out_path.write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")

    print("Wrote:", out_path.as_posix())

if __name__ == "__main__":
    main()
