import os
import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo/")

def _numeric_keys(players_dict: dict) -> list[str]:
    keys = [k for k in players_dict.keys() if k != "count"]
    keys.sort(key=lambda x: int(x))
    return keys

def main():
    game_key = os.environ.get("YAHOO_GAME_KEY")
    if not game_key:
        raise SystemExit("Missing env var YAHOO_GAME_KEY (e.g. 469 for 2026, 458 for 2025)")

    start = int(os.environ.get("YAHOO_PLAYERS_START", "0"))
    count = int(os.environ.get("YAHOO_PLAYERS_COUNT", "25"))
    max_pages = int(os.environ.get("YAHOO_PLAYERS_MAX_PAGES", "999999"))  # safety

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pages = 0
    while True:
        url = f"{YAHOO_FANTASY_BASE}/game/{game_key}/players;start={start};count={count}?format=json"
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        out_path = OUT_DIR / f"game_{game_key}_players_start{start}_count{count}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("Wrote:", out_path.as_posix())

        # Find the players dict at the known path (best-effort; if this fails we stop loudly)
        try:
            players_node = payload["fantasy_content"]["game"][1]["players"]
        except Exception as e:
            raise SystemExit(f"Could not locate players node at expected path: {e}")

        if not isinstance(players_node, dict):
            raise SystemExit("Players node is not a dict (unexpected).")

        returned = len(_numeric_keys(players_node))
        pages += 1

        # Stop when the API returns fewer than requested => last page
        if returned < count:
            print(f"Done. Last page returned {returned} players (< requested {count}).")
            return

        if pages >= max_pages:
            print(f"Stopping due to YAHOO_PLAYERS_MAX_PAGES={max_pages}.")
            return

        start += count

if __name__ == "__main__":
    main()
