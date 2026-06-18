import os
import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")

def main():
    league_key = os.environ.get("YAHOO_LEAGUE_KEY")
    if not league_key:
        raise SystemExit("Missing env var YAHOO_LEAGUE_KEY (e.g. 458.l.11506)")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Note: Yahoo responses are paginated via 'start' for large collections
    start = int(os.environ.get("YAHOO_START", "0"))
    count = int(os.environ.get("YAHOO_COUNT", "25"))  # keep small at first

    url = f"{YAHOO_FANTASY_BASE}/league/{league_key}/transactions;start={start};count={count}?format=json"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"league_{league_key.replace('.','_')}_transactions_start{start}_count{count}.json"
    out_path.write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")

    print("Wrote:", out_path.as_posix())

if __name__ == "__main__":
    main()
