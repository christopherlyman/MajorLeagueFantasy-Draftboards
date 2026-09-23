from __future__ import annotations

import json
import os
import sys
from pathlib import Path


APP_SRC = Path(__file__).resolve().parents[2] / "app" / "src"
sys.path.insert(0, str(APP_SRC))

from draftboard.data.yahoo_client import (
    extract_yahoo_team_rows,
    fetch_yahoo_json,
    get_yahoo_access_token,
    replace_yahoo_team_map,
)


YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "data/raw/yahoo/"))


def _required_positive_int(*names: str) -> int:
    raw = next(
        (
            os.environ.get(name)
            for name in names
            if os.environ.get(name)
        ),
        None,
    )

    if raw is None:
        joined = " or ".join(names)
        raise SystemExit(f"Missing required {joined}.")

    try:
        value = int(raw)
    except ValueError as exc:
        joined = "/".join(names)
        raise SystemExit(
            f"Invalid {joined}: {raw!r}"
        ) from exc

    if value <= 0:
        raise SystemExit(
            f"Expected positive integer, found {value}."
        )

    return value


def main() -> None:
    league_key = str(
        os.environ.get("YAHOO_LEAGUE_KEY") or ""
    ).strip()

    if not league_key:
        raise SystemExit(
            "Missing env var YAHOO_LEAGUE_KEY."
        )

    token = get_yahoo_access_token()
    url = (
        f"{YAHOO_FANTASY_BASE}/league/"
        f"{league_key}/teams?format=json"
    )
    payload = fetch_yahoo_json(token, url)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = (
        OUT_DIR
        / f"league_{league_key.replace('.', '_')}_teams.json"
    )
    out_path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    print("Wrote:", out_path.as_posix())

    if os.environ.get(
        "YAHOO_UPSERT_TEAM_MAP",
        "",
    ).strip() != "1":
        return

    season_year = _required_positive_int("SEASON_YEAR")
    expected_manager_count = _required_positive_int(
        "EXPECTED_MANAGER_COUNT",
        "MANAGER_COUNT",
    )

    dsn = (
        os.environ.get("MLF_POSTGRES_DSN")
        or os.environ.get("POSTGRES_DSN")
    )
    if not dsn:
        raise SystemExit(
            "Missing env var MLF_POSTGRES_DSN or POSTGRES_DSN."
        )

    rows = extract_yahoo_team_rows(
        payload,
        league_key=league_key,
        season_year=season_year,
        expected_manager_count=expected_manager_count,
    )

    written = replace_yahoo_team_map(
        dsn,
        league_key=league_key,
        season_year=season_year,
        rows=rows,
    )

    print(
        f"Replaced {written} rows in public.yahoo_team_map "
        f"for league_key={league_key} "
        f"season_year={season_year}"
    )


if __name__ == "__main__":
    main()
