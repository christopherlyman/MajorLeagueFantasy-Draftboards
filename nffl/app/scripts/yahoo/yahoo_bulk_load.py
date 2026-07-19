# scripts/yahoo_bulk_load.py

import os
import time
import json
import math
import requests
from pathlib import Path

import psycopg
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from auth import get_access_token

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
OUT_DIR = Path("data/raw/yahoo")


# ----------------------------
# Helpers
# ----------------------------

def env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v else default


def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y")


def safe_player_keys(keys: list[str]) -> str:
    return ",".join(keys)


# ----------------------------
# Extraction helpers
# ----------------------------

def extract_player_blocks(payload: dict) -> list[list]:
    """
    Returns a list of player blocks, where each block is the FULL Yahoo 'player' array:
      [
        [ {player_key...}, {player_id...}, ... ],   # fields
        { "player_stats": {...}, "player_advanced_stats": {...}, ... }  # stats/extra
      ]
    """
    try:
        players_node = payload["fantasy_content"]["league"][1]["players"]
    except Exception as e:
        raise SystemExit(f"Could not locate league->players node: {e}")

    if not isinstance(players_node, dict):
        raise SystemExit("players_node is not a dict (unexpected).")

    keys = [k for k in players_node.keys() if k != "count"]
    keys.sort(key=lambda x: int(x))

    blocks: list[list] = []
    for k in keys:
        entry = players_node.get(k)
        if not isinstance(entry, dict):
            continue
        p = entry.get("player")
        if isinstance(p, list):
            blocks.append(p)

    return blocks


def find_player_key(player_block: list) -> str | None:
    # League players payload shape:
    # player_block[0] = fields list (list of dicts)
    # player_block[1] = stats dict (player_stats, player_advanced_stats, etc.)
    try:
        fields = player_block[0]
        if isinstance(fields, list) and fields:
            first = fields[0]
            if isinstance(first, dict) and "player_key" in first:
                return first["player_key"]
    except Exception:
        pass

    # Fallback: deep search (defensive)
    def walk(node):
        if isinstance(node, dict):
            if "player_key" in node:
                return node["player_key"]
            for v in node.values():
                got = walk(v)
                if got:
                    return got
        elif isinstance(node, list):
            for v in node:
                got = walk(v)
                if got:
                    return got
        return None

    return walk(player_block)


def extract_stats(player_block: list) -> list[tuple[int, str]]:
    """
    Extract (stat_id, value_raw) from Yahoo player payloads.

    Handles BOTH shapes:
      A) {"stat": {"stat_id":"50","value":"167.2"}}
      B) {"stat": [{"stat_id":"50"},{"value":"167.2"}]}
    """
    results: list[tuple[int, str]] = []

    def parse_stat_obj(stat_obj):
        sid = None
        val = None

        if isinstance(stat_obj, dict):
            sid = stat_obj.get("stat_id")
            val = stat_obj.get("value")

        elif isinstance(stat_obj, list):
            for part in stat_obj:
                if isinstance(part, dict):
                    if "stat_id" in part:
                        sid = part["stat_id"]
                    if "value" in part:
                        val = part["value"]

        if sid is None:
            return

        try:
            sid_int = int(str(sid))
        except Exception:
            return

        results.append((sid_int, "" if val is None else str(val)))

    def walk(node):
        if isinstance(node, dict):
            if "stat" in node:
                parse_stat_obj(node["stat"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(player_block)

    # de-duplicate
    seen = set()
    dedup = []
    for sid, val in results:
        if (sid, val) not in seen:
            seen.add((sid, val))
            dedup.append((sid, val))
    return dedup


def parse_numeric(value_raw: str) -> float | None:
    v = (value_raw or "").strip()
    if v == "" or v == "-":
        return None

    v = v.replace("%", "")
    try:
        return float(v)
    except Exception:
        return None


# ----------------------------
# META extraction helpers
# ----------------------------

def _find_first_value(player_block: list, key: str):
    """Walk the player_block and return the first value found for key."""
    def walk(node):
        if isinstance(node, dict):
            if key in node:
                return node[key]
            for v in node.values():
                got = walk(v)
                if got is not None:
                    return got
        elif isinstance(node, list):
            for v in node:
                got = walk(v)
                if got is not None:
                    return got
        return None

    return walk(player_block)


def _extract_eligible_positions(player_block: list) -> list[str]:
    ep = _find_first_value(player_block, "eligible_positions")
    out: list[str] = []
    if isinstance(ep, list):
        for item in ep:
            if isinstance(item, dict) and "position" in item:
                out.append(str(item["position"]))
    return out


def _extract_percent_owned(player_block: list) -> tuple[float | None, float | None]:
    po = _find_first_value(player_block, "percent_owned")
    if not isinstance(po, list):
        return (None, None)

    val = None
    delta = None
    for item in po:
        if isinstance(item, dict):
            if "value" in item:
                val = parse_numeric(str(item["value"]))
            if "delta" in item:
                delta = parse_numeric(str(item["delta"]))
    return (val, delta)


def _extract_rank_fields(player_block: list) -> dict:
    fields = {}
    for k in [
        "player_rank",
        "rank_type",
        "rank_season",
        "rank_value",
        "draft_status",
        "percent_drafted",
        "preseason_percent_drafted",
    ]:
        fields[k] = _find_first_value(player_block, k)

    # normalize numerics
    for k in ["player_rank", "rank_value", "percent_drafted", "preseason_percent_drafted"]:
        if fields.get(k) is not None:
            fields[k] = parse_numeric(str(fields[k]))

    if fields.get("rank_season") is not None:
        try:
            fields["rank_season"] = int(str(fields["rank_season"]))
        except Exception:
            fields["rank_season"] = None

    # normalize text
    for k in ["rank_type", "draft_status"]:
        if fields.get(k) is not None:
            fields[k] = str(fields[k])

    return fields


# ----------------------------
# DB setup
# ----------------------------

def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yahoo_player_league_season_stat (
            league_key text NOT NULL,
            season_year integer NOT NULL,
            yahoo_player_key text NOT NULL,
            stat_id integer NOT NULL,
            value_raw text NULL,
            value_num numeric NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (league_key, season_year, yahoo_player_key, stat_id)
        );
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_yplss_player
        ON yahoo_player_league_season_stat (league_key, season_year, yahoo_player_key);
    """)

    # This table already exists in your DB; IF NOT EXISTS makes this safe.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yahoo_player_meta (
          yahoo_player_key text PRIMARY KEY,
          source_game_key text NOT NULL,
          editorial_team_abbr text NULL,
          position_type text NULL,
          primary_position text NULL,
          eligible_positions jsonb NULL,
          percent_owned numeric NULL,
          percent_owned_delta numeric NULL,
          updated_at timestamptz NOT NULL DEFAULT now(),
          player_rank numeric NULL,
          rank_type text NULL,
          rank_season integer NULL,
          rank_value numeric NULL,
          percent_drafted numeric NULL,
          preseason_percent_drafted numeric NULL,
          draft_status text NULL
        );
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS ix_yahoo_player_meta_game_key
        ON yahoo_player_meta (source_game_key);
    """)

def _is_400(e: Exception) -> bool:
    try:
        resp = getattr(e, "response", None)
        return resp is not None and getattr(resp, "status_code", None) == 400
    except Exception:
        return False


def _merge_league_players_payload(a: dict | None, b: dict | None) -> dict:
    """
    Merge Yahoo league->players payloads (same shape as returned by /league/{league_key}/players...)
    by concatenating the players dict entries and summing count.
    """
    if not a:
        return b or {}
    if not b:
        return a

    try:
        a_players = a["fantasy_content"]["league"][1]["players"]
        b_players = b["fantasy_content"]["league"][1]["players"]
    except Exception:
        # If shape is unexpected, prefer 'a' rather than exploding.
        return a

    if not isinstance(a_players, dict) or not isinstance(b_players, dict):
        return a

    # Build merged players dict with reindexed numeric keys: "0","1",...
    merged_entries: list = []

    def collect(players_node: dict):
        keys = [k for k in players_node.keys() if k != "count"]
        keys.sort(key=lambda x: int(x))
        for k in keys:
            merged_entries.append(players_node[k])

    collect(a_players)
    collect(b_players)

    merged_players = {"count": len(merged_entries)}
    for idx, entry in enumerate(merged_entries):
        merged_players[str(idx)] = entry

    # Write back into a deep-ish copy
    out = a
    out["fantasy_content"]["league"][1]["players"] = merged_players
    return out


def _fetch_players_with_bisect(
    fetch_fn,
    league_key: str,
    player_keys: list[str],
    *args,
    label: str = "FETCH",
    **kwargs
) -> tuple[dict | None, list[str]]:
    """
    Generic bisection fetch:
      - On 200: returns (payload, [])
      - On 400: bisect keys until single bad key(s) isolated; skip them.
    Returns: (payload_or_none, bad_keys_list)
    """
    if not player_keys:
        return None, []

    try:
        return fetch_fn(league_key, player_keys, *args, **kwargs), []
    except requests.exceptions.HTTPError as e:
        if not _is_400(e):
            raise

        # Single key => it's the bad one
        if len(player_keys) == 1:
            bad = player_keys[0]
            print(f"[WARN] {label} 400 -> skipping bad player_key: {bad}", flush=True)
            return None, [bad]

        mid = len(player_keys) // 2
        left = player_keys[:mid]
        right = player_keys[mid:]

        left_payload, left_bad = _fetch_players_with_bisect(fetch_fn, league_key, left, *args, label=label, **kwargs)
        right_payload, right_bad = _fetch_players_with_bisect(fetch_fn, league_key, right, *args, label=label, **kwargs)

        merged = _merge_league_players_payload(left_payload, right_payload)
        return merged, (left_bad + right_bad)

# ----------------------------
# Yahoo call
# ----------------------------

def fetch_league_players_stats(league_key: str, player_keys: list[str], season_year: int, token: str):
    headers = {"Authorization": f"Bearer {token}"}

    url = (
        f"{YAHOO_FANTASY_BASE}/league/{league_key}/players;"
        f"player_keys={safe_player_keys(player_keys)}"
        f"/stats;type=season;season={season_year}?format=json"
    )

    r = requests.get(url, headers=headers, timeout=45)
    r.raise_for_status()
    return r.json()


def fetch_league_players_meta(league_key: str, player_keys: list[str], token: str):
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"{YAHOO_FANTASY_BASE}/league/{league_key}/players;"
        f"player_keys={safe_player_keys(player_keys)}"
        f";out=percent_owned;out=ranks;out=draft_analysis"
        f"?format=json"
    )
    r = requests.get(url, headers=headers, timeout=45)
    r.raise_for_status()
    return r.json()


# ----------------------------
# MAIN
# ----------------------------

def main():
    dsn = os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN")

    league_key = os.environ.get("YAHOO_LEAGUE_KEY")
    game_key = os.environ.get("YAHOO_GAME_KEY", "469")
    season_year = env_int("YAHOO_STATS_SEASON", 2025)
    player_input_source = os.environ.get("YAHOO_PLAYER_INPUT_SOURCE", "yahoo_player").strip().lower()
    player_universe_season = env_int("YAHOO_PLAYER_UNIVERSE_SEASON", season_year)
    player_key_limit = env_int("YAHOO_PLAYER_KEY_LIMIT", 0)
    explicit_player_keys = [
        p.strip()
        for p in os.environ.get("YAHOO_PLAYER_KEYS", "").split(",")
        if p.strip()
    ]
    allowed_stat_ids_raw = os.environ.get(
        "YAHOO_ALLOWED_STAT_IDS",
        "3,7,12,13,16,18,21,60,26,27,28,42,49,50,83,89",
    ).strip()

    batch_size = env_int("YAHOO_BATCH_SIZE", 25)
    sleep_seconds = env_int("YAHOO_SLEEP_SECONDS", 1)
    write_raw = env_bool("YAHOO_WRITE_RAW", False)

    # META flags
    fetch_meta = env_bool("YAHOO_FETCH_META", True)
    write_raw_meta = env_bool("YAHOO_WRITE_RAW_META", False)

    if not league_key:
        raise SystemExit("Missing YAHOO_LEAGUE_KEY")

    token = get_access_token()

    with psycopg.connect(dsn) as conn:
        conn.autocommit = True
        ensure_tables(conn)

        if player_input_source == "league_player_pool":
            rows = conn.execute(
                """
                SELECT yahoo_player_key
                FROM public.yahoo_league_player_pool
                WHERE league_key = %s
                  AND season_year = %s
                ORDER BY yahoo_player_key
                """,
                (league_key, int(player_universe_season)),
            ).fetchall()
            source_label = (
                f"league_player_pool league_key={league_key} "
                f"season_year={player_universe_season}"
            )
        elif player_input_source == "yahoo_player":
            rows = conn.execute(
                "SELECT yahoo_player_key FROM yahoo_player WHERE source_game_key=%s ORDER BY yahoo_player_key",
                (str(game_key),)
            ).fetchall()
            source_label = f"yahoo_player source_game_key={game_key}"
        else:
            raise SystemExit(
                "Invalid YAHOO_PLAYER_INPUT_SOURCE. "
                "Expected 'yahoo_player' or 'league_player_pool'."
            )

        player_universe = [r[0] for r in rows]

        if explicit_player_keys:
            source_keys = set(player_universe)
            missing_keys = [p for p in explicit_player_keys if p not in source_keys]
            if missing_keys:
                raise SystemExit(
                    "YAHOO_PLAYER_KEYS contains keys not found in selected input source: "
                    + ", ".join(missing_keys)
                )
            player_universe = explicit_player_keys
            print(f"YAHOO_PLAYER_KEYS applied: {len(explicit_player_keys)}")
        elif player_key_limit > 0:
            player_universe = player_universe[:player_key_limit]

        total = len(player_universe)

        print(f"Players to process ({source_label}): {total}")
        if player_key_limit > 0 and not explicit_player_keys:
            print(f"YAHOO_PLAYER_KEY_LIMIT applied: {player_key_limit}")
        if total == 0:
            return

        batches = math.ceil(total / batch_size)

        for i in range(batches):
            batch = player_universe[i * batch_size:(i + 1) * batch_size]

            print(f"\n=== BATCH {i+1}/{batches} | players={len(batch)} ===", flush=True)

            # ============================================================
            # META (positions, team, ownership, ranks)
            # ============================================================
            meta_rows = 0
            meta_bad = []

            if fetch_meta:
                meta_payload, meta_bad = _fetch_players_with_bisect(
                    fetch_league_players_meta,
                    league_key,
                    batch,
                    token,
                    label="META"
                )

                if not meta_payload:
                    meta_payload = {
                        "fantasy_content": {"league": [None, {"players": {"count": 0}}]}
                    }

                if write_raw_meta:
                    OUT_DIR.mkdir(parents=True, exist_ok=True)
                    filename = f"bulk_meta_league_{league_key.replace('.', '_')}_batch{i+1}.json"
                    (OUT_DIR / filename).write_text(
                        json.dumps(meta_payload, indent=2),
                        encoding="utf-8"
                    )

                meta_blocks = extract_player_blocks(meta_payload)

                for block in meta_blocks:
                    pkey = find_player_key(block)
                    if not pkey:
                        continue

                    source_game_key = (
                        pkey.split(".")[0] if "." in pkey else str(game_key)
                    )

                    editorial_team_abbr = _find_first_value(block, "editorial_team_abbr")
                    position_type = _find_first_value(block, "position_type")
                    primary_position = _find_first_value(block, "primary_position")
                    eligible_positions = _extract_eligible_positions(block)
                    percent_owned, percent_owned_delta = _extract_percent_owned(block)
                    rf = _extract_rank_fields(block)

                    conn.execute("""
                        INSERT INTO yahoo_player_meta (
                          yahoo_player_key,
                          source_game_key,
                          editorial_team_abbr,
                          position_type,
                          primary_position,
                          eligible_positions,
                          percent_owned,
                          percent_owned_delta,
                          player_rank,
                          rank_type,
                          rank_season,
                          rank_value,
                          percent_drafted,
                          preseason_percent_drafted,
                          draft_status
                        ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (yahoo_player_key)
                        DO UPDATE SET
                          source_game_key = EXCLUDED.source_game_key,
                          editorial_team_abbr = EXCLUDED.editorial_team_abbr,
                          position_type = EXCLUDED.position_type,
                          primary_position = EXCLUDED.primary_position,
                          eligible_positions = EXCLUDED.eligible_positions,
                          percent_owned = COALESCE(EXCLUDED.percent_owned, yahoo_player_meta.percent_owned),
                          percent_owned_delta = COALESCE(EXCLUDED.percent_owned_delta, yahoo_player_meta.percent_owned_delta),
                          player_rank = COALESCE(EXCLUDED.player_rank, yahoo_player_meta.player_rank),
                          rank_type = COALESCE(EXCLUDED.rank_type, yahoo_player_meta.rank_type),
                          rank_season = COALESCE(EXCLUDED.rank_season, yahoo_player_meta.rank_season),
                          rank_value = COALESCE(EXCLUDED.rank_value, yahoo_player_meta.rank_value),
                          percent_drafted = COALESCE(EXCLUDED.percent_drafted, yahoo_player_meta.percent_drafted),
                          preseason_percent_drafted = COALESCE(EXCLUDED.preseason_percent_drafted, yahoo_player_meta.preseason_percent_drafted),
                          draft_status = COALESCE(EXCLUDED.draft_status, yahoo_player_meta.draft_status),
                          updated_at = now();
                    """, (
                        pkey,
                        source_game_key,
                        None if editorial_team_abbr is None else str(editorial_team_abbr),
                        None if position_type is None else str(position_type),
                        None if primary_position is None else str(primary_position),
                        json.dumps(eligible_positions),
                        percent_owned,
                        percent_owned_delta,
                        rf["player_rank"],
                        rf["rank_type"],
                        rf["rank_season"],
                        rf["rank_value"],
                        rf["percent_drafted"],
                        rf["preseason_percent_drafted"],
                        rf["draft_status"],
                    ))

                    meta_rows += 1

            # Remove bad META keys before STATS
            if meta_bad:
                bad_set = set(meta_bad)
                batch = [k for k in batch if k not in bad_set]

            # ============================================================
            # STATS
            # ============================================================
            payload, stats_bad = _fetch_players_with_bisect(
                fetch_league_players_stats,
                league_key,
                batch,
                season_year,
                token,
                label="STATS"
            )

            if not payload:
                payload = {
                    "fantasy_content": {"league": [None, {"players": {"count": 0}}]}
                }

            if write_raw:
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                filename = f"league_{league_key.replace('.', '_')}_batch{i+1}.json"
                (OUT_DIR / filename).write_text(
                    json.dumps(payload, indent=2),
                    encoding="utf-8"
                )

            blocks = extract_player_blocks(payload)

            stat_rows = 0
            if allowed_stat_ids_raw.lower() in ("", "all", "*"):
                allowed = None
            else:
                allowed = {
                    int(part.strip())
                    for part in allowed_stat_ids_raw.split(",")
                    if part.strip()
                }

            for block in blocks:
                pkey = find_player_key(block)
                if not pkey:
                    continue

                stats = extract_stats(block)

                for stat_id, value_raw in stats:
                    if allowed is not None and stat_id not in allowed:
                        continue

                    value_num = parse_numeric(value_raw)

                    conn.execute("""
                        INSERT INTO yahoo_player_league_season_stat
                        (league_key, season_year, yahoo_player_key, stat_id, value_raw, value_num)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (league_key, season_year, yahoo_player_key, stat_id)
                        DO UPDATE SET
                            value_raw = EXCLUDED.value_raw,
                            value_num = EXCLUDED.value_num,
                            updated_at = now();
                    """, (
                        league_key,
                        season_year,
                        pkey,
                        stat_id,
                        value_raw,
                        value_num
                    ))

                    stat_rows += 1

            print(
                f"[{i+1}] batch={len(batch)} "
                f"meta_rows={meta_rows} "
                f"player_blocks={len(blocks)} "
                f"stat_rows={stat_rows}"
            )

            if sleep_seconds > 0 and i + 1 < batches:
                time.sleep(sleep_seconds)

    print("DONE.")


if __name__ == "__main__":
    main()
