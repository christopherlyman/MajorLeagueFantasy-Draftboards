import os
import json
import glob
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg


RAW_DIR = Path("data/raw/yahoo")


def _numeric_keys(d: Dict[str, Any]) -> List[str]:
    ks = [k for k in d.keys() if k != "count"]
    ks.sort(key=lambda x: int(x))
    return ks


def _unwrap_player_list(player_value: Any) -> List[dict]:
    """
    Yahoo game/players payload uses:
      "player": [ [ {..}, {..}, ... ] ]
    i.e. a list containing a single list of dict blocks.
    This function normalizes that into a flat list[dict].
    """
    if not isinstance(player_value, list) or not player_value:
        return []
    # common shape: [ [ {...}, {...} ] ]
    if len(player_value) == 1 and isinstance(player_value[0], list):
        inner = player_value[0]
        return [x for x in inner if isinstance(x, dict)]
    # fallback: already flat
    return [x for x in player_value if isinstance(x, dict)]


def _safe_get_name_block(blocks: List[dict]) -> dict:
    for b in blocks:
        nb = b.get("name")
        if isinstance(nb, dict):
            return nb
    return {}


def _safe_get_scalar(blocks: List[dict], key: str) -> Optional[str]:
    for b in blocks:
        if key in b:
            return b.get(key)
    return None


def _extract_players_from_payload(payload: dict, debug: bool = False) -> List[
    Tuple[str, int, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]
]:
    """
    Returns rows of:
      (yahoo_player_key, yahoo_player_id, full_name, first_name, last_name, ascii_first, ascii_last, source_game_key)
    """
    try:
        players_node = payload["fantasy_content"]["game"][1]["players"]
    except Exception as e:
        raise SystemExit(f"Could not locate players node at expected path: {e}")

    if not isinstance(players_node, dict):
        raise SystemExit("Players node is not a dict (unexpected).")

    if debug:
        print("DEBUG players_node keys head:", list(players_node.keys())[:10])

    rows: List[Tuple[str, int, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]] = []

    for k in _numeric_keys(players_node):
        node = players_node.get(k)
        if not isinstance(node, dict):
            continue
        if "player" not in node:
            continue

        blocks = _unwrap_player_list(node["player"])
        if not blocks:
            continue

        yahoo_player_key = _safe_get_scalar(blocks, "player_key")
        yahoo_player_id_raw = _safe_get_scalar(blocks, "player_id")
        name = _safe_get_name_block(blocks)

        full_name = name.get("full")
        first_name = name.get("first")
        last_name = name.get("last")
        ascii_first = name.get("ascii_first")
        ascii_last = name.get("ascii_last")

        if not yahoo_player_key or yahoo_player_id_raw is None or not full_name:
            continue

        try:
            yahoo_player_id = int(str(yahoo_player_id_raw))
        except Exception:
            continue

        source_game_key = yahoo_player_key.split(".")[0] if "." in yahoo_player_key else None

        rows.append(
            (
                yahoo_player_key,
                yahoo_player_id,
                full_name,
                first_name,
                last_name,
                ascii_first,
                ascii_last,
                source_game_key,
            )
        )

    return rows


def _iter_payload_files(game_key: str, count: int) -> List[Path]:
    pattern = RAW_DIR / f"game_{game_key}_players_start*_count{count}.json"
    paths = [Path(p) for p in glob.glob(pattern.as_posix())]
    if not paths:
        raise SystemExit(f"No files matched: {pattern.as_posix()}")

    def _start_num(p: Path) -> int:
        # game_469_players_start2325_count25.json
        part = p.name.split("_players_start", 1)[1]
        n = part.split("_count", 1)[0]
        return int(n)

    paths.sort(key=_start_num)
    return paths


def main() -> None:
    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var MLF_POSTGRES_DSN")

    game_key = os.environ.get("YAHOO_GAME_KEY")
    if not game_key:
        raise SystemExit("Missing env var YAHOO_GAME_KEY (e.g. 469)")

    count = int(os.environ.get("YAHOO_PLAYERS_COUNT", "25"))
    debug = os.environ.get("DEBUG", "0") == "1"

    files = _iter_payload_files(game_key=game_key, count=count)
    print(f"Found {len(files)} payload files for game_key={game_key} count={count}")

    all_rows: List[Tuple[str, int, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]] = []
    for i, fp in enumerate(files):
        payload = json.loads(fp.read_text(encoding="utf-8"))
        rows = _extract_players_from_payload(payload, debug=(debug and i == 0))
        all_rows.extend(rows)

    # de-dupe by yahoo_player_key
    dedup: Dict[str, Tuple[str, int, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]] = {}
    for r in all_rows:
        dedup[r[0]] = r
    final_rows = list(dedup.values())

    if not final_rows:
        raise SystemExit("No players extracted. (Unexpected)")

    sql = """
    INSERT INTO yahoo_player (
        yahoo_player_key,
        yahoo_player_id,
        full_name,
        first_name,
        last_name,
        ascii_first,
        ascii_last,
        source_game_key
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (yahoo_player_key)
    DO UPDATE SET
        yahoo_player_id = EXCLUDED.yahoo_player_id,
        full_name       = EXCLUDED.full_name,
        first_name      = EXCLUDED.first_name,
        last_name       = EXCLUDED.last_name,
        ascii_first     = EXCLUDED.ascii_first,
        ascii_last      = EXCLUDED.ascii_last,
        source_game_key = EXCLUDED.source_game_key,
        updated_at      = now();
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, final_rows)
        conn.commit()

    print(f"Upserted {len(final_rows)} unique players into yahoo_player for game_key={game_key}")


if __name__ == "__main__":
    main()
