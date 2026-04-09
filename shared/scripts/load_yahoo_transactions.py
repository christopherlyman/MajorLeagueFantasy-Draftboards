import os
import json
import glob
from pathlib import Path
import psycopg


SIGNATURE = "LOAD_YAHOO_TRANSACTIONS_v3_SUPPORTS_DROP_DICT_OR_LIST"


def numeric_keys(d: dict):
    return sorted([k for k in d.keys() if k != "count"], key=lambda s: int(s))


def flatten_list_of_dicts(node):
    out = {}
    for item in node:
        if isinstance(item, dict):
            out.update(item)
    return out


def drill_to_list_of_dicts(cur):
    while isinstance(cur, list) and cur:
        if isinstance(cur[0], dict):
            return cur
        cur = cur[0]
    return []


def as_list_of_dicts(transaction_data):
    # Yahoo is inconsistent:
    # - add often: [ { ... } ]
    # - drop often: { ... }
    # Normalize to list[dict]
    if isinstance(transaction_data, list):
        return [x for x in transaction_data if isinstance(x, dict)]
    if isinstance(transaction_data, dict):
        return [transaction_data]
    return []


def load_file(path: Path, league_key: str, conn):
    payload = json.loads(path.read_text(encoding="utf-8"))
    txns = payload["fantasy_content"]["league"][1]["transactions"]

    rows = []

    for k in numeric_keys(txns):
        cur = drill_to_list_of_dicts(txns[k]["transaction"])
        txn = flatten_list_of_dicts(cur)

        txn_key = txn.get("transaction_key")
        txn_type = txn.get("type")
        txn_status = txn.get("status")
        ts_epoch = int(txn["timestamp"]) if txn.get("timestamp") else None

        players = txn.get("players")
        if not isinstance(players, dict):
            continue

        for pk in numeric_keys(players):
            player_node = players[pk].get("player")
            if not (isinstance(player_node, list) and len(player_node) >= 2):
                continue

            player_core = flatten_list_of_dicts(player_node[0])
            meta = player_node[1]
            if not isinstance(meta, dict):
                continue

            tdata = meta.get("transaction_data")
            tdata_list = as_list_of_dicts(tdata)
            if not tdata_list:
                continue

            pkey = player_core.get("player_key")
            pid = int(player_core["player_id"]) if player_core.get("player_id") else None
            name = player_core.get("name", {})
            full_name = name.get("full") if isinstance(name, dict) else None

            for td in tdata_list:
                action = td.get("type")  # add / drop / trade
                if not action:
                    continue

                dest_team_key = td.get("destination_team_key") or ""
                rows.append((
                    league_key,
                    txn_key,
                    txn_type,
                    txn_status,
                    ts_epoch,
                    pkey,
                    pid,
                    full_name,
                    action,
                    td.get("source_type"),
                    td.get("source_team_key"),
                    td.get("source_team_name"),
                    dest_team_key,
                    td.get("destination_team_name"),
                ))

    if not rows:
        return 0

    sql = """
    INSERT INTO yahoo_transaction_event (
        league_key,
        transaction_key,
        transaction_type,
        transaction_status,
        transaction_ts_epoch,
        player_key,
        player_id,
        player_name,
        action_type,
        source_type,
        source_team_key,
        source_team_name,
        destination_team_key,
        destination_team_name
    )
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (league_key, transaction_key, player_key, action_type, destination_team_key)
    DO NOTHING;
    """

    with conn.cursor() as cur:
        cur.executemany(sql, rows)

    return len(rows)


def main():
    print(SIGNATURE)

    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var: MLF_POSTGRES_DSN")

    league_key = os.environ.get("YAHOO_LEAGUE_KEY")
    if not league_key:
        raise SystemExit("Missing env var: YAHOO_LEAGUE_KEY (e.g. 458.l.11506)")

    slug = league_key.replace(".", "_")

    pattern = os.environ.get(
        "YAHOO_TXN_GLOB",
        "data/raw/yahoo/league_{}_transactions_types_drop_start*_count25.json".format(slug)
    )

    # If no override provided, we’ll load BOTH add/trade pages and drop pages.
    if "YAHOO_TXN_GLOB" not in os.environ:
        pattern_add = "data/raw/yahoo/league_{}_transactions_types_add_drop_trade_start*_count25.json".format(slug)
        pattern_drop = "data/raw/yahoo/league_{}_transactions_types_drop_start*_count25.json".format(slug)
        files = sorted(set(glob.glob(pattern_add) + glob.glob(pattern_drop)))
    else:
        files = sorted(glob.glob(pattern))

    if not files:
        raise SystemExit("No files matched. Pattern(s) used did not match any files.")

    total_rows = 0
    with psycopg.connect(dsn) as conn:
        for fp in files:
            total_rows += load_file(Path(fp), league_key, conn)
        conn.commit()

    print("Matched files:", len(files))
    print("Extracted rows (pre-dedup insert):", total_rows)


if __name__ == "__main__":
    main()
