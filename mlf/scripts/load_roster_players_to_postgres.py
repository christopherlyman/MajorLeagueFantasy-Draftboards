import os
import json
import psycopg


def flatten_player(player_node):
    # player_node looks like: [ [ {k:v}, {k:v}, ... ] ]
    inner = player_node[0]
    out = {}
    for item in inner:
        if isinstance(item, dict):
            out.update(item)
    return out


def main():
    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var: MLF_POSTGRES_DSN")

    roster_path = os.environ.get("YAHOO_ROSTER_JSON", "data/raw/yahoo/team_458_l_11506_t_1_roster.json")

    with open(roster_path, "r", encoding="utf-8") as f:
        d = json.load(f)

    players = d["fantasy_content"]["team"][1]["roster"]["0"]["players"]

    keys = sorted([k for k in players.keys() if k != "count"], key=lambda s: int(s))

    rows = []
    for k in keys:
        p = flatten_player(players[k]["player"])
        name = p.get("name") if isinstance(p.get("name"), dict) else {}
        yahoo_player_key = p.get("player_key")
        yahoo_player_id = int(p.get("player_id")) if p.get("player_id") is not None else None
        full_name = name.get("full")

        if not yahoo_player_key or yahoo_player_id is None or not full_name:
            continue

        # player_key format: "<game_key>.p.<id>"
        source_game_key = yahoo_player_key.split(".")[0] if "." in yahoo_player_key else None

        rows.append(
            (
                yahoo_player_key,
                yahoo_player_id,
                full_name,
                name.get("first"),
                name.get("last"),
                name.get("ascii_first"),
                name.get("ascii_last"),
                source_game_key,
            )
        )

    if not rows:
        raise SystemExit(f"No players found in {roster_path} (unexpected)")

    sql = """
    INSERT INTO yahoo_player (
        yahoo_player_key,
        yahoo_player_id,
        full_name,
        first_name,
        last_name,
        ascii_first,
        ascii_last,
        source_game_key,
        updated_at
    )
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
    ON CONFLICT (yahoo_player_key)
    DO UPDATE SET
        yahoo_player_id = EXCLUDED.yahoo_player_id,
        full_name       = EXCLUDED.full_name,
        first_name      = EXCLUDED.first_name,
        last_name       = EXCLUDED.last_name,
        ascii_first     = EXCLUDED.ascii_first,
        ascii_last      = EXCLUDED.ascii_last,
        source_game_key = EXCLUDED.source_game_key,
        updated_at      = now()
    ;
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()

    print(f"Upserted {len(rows)} players into yahoo_player from {roster_path}")


if __name__ == "__main__":
    main()
