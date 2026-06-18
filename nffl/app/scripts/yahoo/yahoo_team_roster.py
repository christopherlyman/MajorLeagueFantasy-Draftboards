import os
import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")

def main():
    team_key = os.environ.get("YAHOO_TEAM_KEY")
    if not team_key:
        raise SystemExit("Missing env var YAHOO_TEAM_KEY (e.g. 458.l.11506.t.1 for 2025 end roster)")

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = f"{YAHOO_FANTASY_BASE}/team/{team_key}/roster?format=json"

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"team_{team_key.replace('.','_')}_roster.json"
    out_path.write_text(json.dumps(resp.json(), indent=2), encoding="utf-8")

    print("Wrote:", out_path.as_posix())

if __name__ == "__main__":
    main()
