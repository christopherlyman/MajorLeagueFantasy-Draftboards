import os
import json
import requests
from pathlib import Path

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo")


def main():
    league_key = os.environ.get("YAHOO_LEAGUE_KEY", "458.l.11506")
    page_size = int(os.environ.get("YAHOO_TXN_PAGE_SIZE", "25"))

    token = get_access_token()
    headers = {"Authorization": "Bearer {}".format(token)}

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    start = 0
    pages = 0

    while True:
        url = (
            "{}/league/{}/transactions;types=drop;start={};count={}?format=json"
        ).format(YAHOO_FANTASY_BASE, league_key, start, page_size)

        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        out_path = OUT_DIR / "league_{}_transactions_types_drop_start{}_count{}.json".format(
            league_key.replace(".", "_"), start, page_size
        )
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        pages += 1

        txns = data.get("fantasy_content", {}).get("league", [None, {}])[1].get("transactions", {})
        returned = int(txns.get("count", 0))

        if returned < page_size:
            print("Done. Last page returned {} (<{}). Pages written: {}".format(returned, page_size, pages))
            break

        start += page_size

    print("League:", league_key)
    print("Page size:", page_size)


if __name__ == "__main__":
    main()
