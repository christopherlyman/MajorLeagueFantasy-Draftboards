import os
import psycopg


def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def main():
    dsn = os.environ.get("MLF_POSTGRES_DSN")
    if not dsn:
        raise SystemExit("Missing env var: MLF_POSTGRES_DSN")

    league_key = os.environ.get("LEAGUE_KEY", "458.l.11506")
    season_year = int(os.environ.get("SEASON_YEAR", "2025"))
    snapshot_id = os.environ.get("SNAPSHOT_ID")  # optional; if missing we use MAX(snapshot_id)
    waiver_window_seconds = int(os.environ.get("WAIVER_WINDOW_SECONDS", "172800"))  # 2 days
    opening_day_epoch = int(os.environ.get("OPENING_DAY_EPOCH", "0"))

    contracts_travel_on_trades = _bool_env("CONTRACTS_TRAVEL_ON_TRADES", True)

    # NOTE: For now we use your existing view name v_contracts_2025_with_team_key.
    # When you backfill history, we can generalize the view and keep this script unchanged.

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if not snapshot_id:
                cur.execute("SELECT MAX(snapshot_id) FROM ingest_snapshot")
                snapshot_id = cur.fetchone()[0]

            # Output table (season-aware, re-runnable)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contract_reconcile (
                    snapshot_id             text NOT NULL,
                    league_key              text NOT NULL,
                    season_year             integer NOT NULL,
                    player_name             text NOT NULL,

                    starting_team_key       text NOT NULL,
                    starting_owner_name     text NOT NULL,
                    years_remaining_season  integer NOT NULL,
                    raw_value               text,

                    ending_team_key         text,
                    ending_owner_name       text,

                    contract_voided         boolean NOT NULL DEFAULT false,
                    void_reason             text,
                    voided_ts_epoch         bigint,

                    last_event_ts_epoch     bigint,
                    last_event_source_type  text,
                    last_event_action_type  text,
                    last_event_txn_key      text,

                    needs_review            boolean NOT NULL DEFAULT false,
                    review_reason           text,

                    years_remaining_next    integer,
                    has_contract_next       boolean NOT NULL DEFAULT false,

                    updated_at              timestamptz NOT NULL DEFAULT now(),

                    PRIMARY KEY (snapshot_id, league_key, season_year, player_name)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS ix_contract_reconcile_review
                ON contract_reconcile (league_key, season_year, needs_review, contract_voided);
            """)

            # Contract base for the season (per sheet) + authoritative team_key already attached
            cur.execute("""
                SELECT
                  player_name,
                  sheet_owner_name,
                  team_key,
                  years_remaining,
                  COALESCE(raw_value, '') AS raw_value
                FROM v_contracts_2025_with_team_key
                WHERE snapshot_id = %s
                  AND season_year = %s
            """, (snapshot_id, season_year))
            base_rows = cur.fetchall()

            # Load all events once
            cur.execute("""
                SELECT
                  player_name,
                  COALESCE(transaction_ts_epoch, 0) AS ts,
                  transaction_key,
                  action_type,
                  source_type,
                  source_team_key,
                  destination_team_key
                FROM yahoo_transaction_event
                WHERE league_key = %s
                  AND player_name IS NOT NULL
                ORDER BY player_name, ts
            """, (league_key,))
            ev_rows = cur.fetchall()

            events_by_player = {}
            for pname, ts, tkey, action, stype, src_team, dst_team in ev_rows:
                events_by_player.setdefault(pname, []).append(
                    (int(ts), tkey, action, (stype or "").lower(), src_team, dst_team)
                )

            # team_key -> owner_name
            def owner_for_team(team_key: str):
                cur.execute("""
                    SELECT owner_name
                    FROM yahoo_team_map
                    WHERE league_key=%s AND season_year=%s AND team_key=%s
                """, (league_key, season_year, team_key))
                r = cur.fetchone()
                return r[0] if r else None

            upserts = []

            for player_name, starting_owner, starting_team_key, years_remaining, raw_value in base_rows:
                if years_remaining is None:
                    continue
                years_remaining = int(years_remaining)
                raw_lower = (raw_value or "").lower()
                sheet_mentions_trade = ("from" in raw_lower) or ("to" in raw_lower)

                evs = events_by_player.get(player_name, [])
                last = evs[-1] if evs else None

                current_team_key = starting_team_key
                voided = False
                void_reason = None
                voided_ts = None

                needs_review = False
                review_reasons = []

                last_drop_ts = None  # for "cleared waivers" inference

                for ts, txn_key, action, stype, src_team, dst_team in evs:
                    # track drops
                    if action == "drop":
                        last_drop_ts = ts
                        continue

                    # cleared waivers inference:
                    # Only void if the player is later added from FREE AGENCY after the waiver window.
                    # NOTE: waiver claims (source_type='waivers') can process after the waiver time and should TRANSFER, not void.
                    if (
                        action == "add"
                        and last_drop_ts is not None
                        and stype == "freeagents"
                        and ts > last_drop_ts + waiver_window_seconds
                    ):
                        voided = True
                        void_reason = "cleared_waivers"
                        voided_ts = last_drop_ts + waiver_window_seconds
                        current_team_key = None
                        break

                    # FA add handling:
                    # - BEFORE Opening Day: treat as draft-era roster activity (do NOT void)
                    # - ON/AFTER Opening Day: treat as true free-agent acquisition (void)
                    if action == "add" and stype == "freeagents":
                        if opening_day_epoch and ts < opening_day_epoch:
                            # Draft-era acquisition; ignore for contract-void logic
                            last_drop_ts = None
                            continue
                        voided = True
                        void_reason = "added_from_freeagency"
                        voided_ts = ts
                        current_team_key = None
                        break

                    # trade handling
                    if stype == "trade":
                        if not sheet_mentions_trade:
                            needs_review = True
                            review_reasons.append("trade_in_yahoo_but_sheet_missing_from/to")
                        if contracts_travel_on_trades and dst_team:
                            current_team_key = dst_team
                        # trade implies movement; reset drop window
                        last_drop_ts = None
                        continue

                    # waiver claim handling
                    if action == "add" and stype == "waivers":
                        if dst_team:
                            current_team_key = dst_team
                        last_drop_ts = None
                        continue

                    # any other add cancels pending cleared-waivers inference
                    if action == "add":
                        last_drop_ts = None

                # End-of-season: if last event was a drop and no add within 2 days, infer cleared waivers
                if (not voided) and (last_drop_ts is not None):
                    voided = True
                    void_reason = "cleared_waivers"
                    voided_ts = last_drop_ts + waiver_window_seconds
                    current_team_key = None

                ending_owner = None
                if current_team_key:
                    ending_owner = owner_for_team(current_team_key)
                    if ending_owner is None:
                        needs_review = True
                        review_reasons.append("ending_team_key_unmapped")

                # Next season decrement
                if not voided and years_remaining > 1:
                    years_next = years_remaining - 1
                    has_next = True
                else:
                    years_next = 0
                    has_next = False

                upserts.append((
                    snapshot_id, league_key, season_year, player_name,
                    starting_team_key, starting_owner, years_remaining, raw_value,
                    current_team_key, ending_owner,
                    voided, void_reason, voided_ts,
                    (last[0] if last else None),
                    (last[3] if last else None),
                    (last[2] if last else None),
                    (last[1] if last else None),
                    needs_review,
                    (";".join(review_reasons) if review_reasons else None),
                    years_next, has_next
                ))

            cur.executemany("""
                INSERT INTO contract_reconcile (
                    snapshot_id, league_key, season_year, player_name,
                    starting_team_key, starting_owner_name, years_remaining_season, raw_value,
                    ending_team_key, ending_owner_name,
                    contract_voided, void_reason, voided_ts_epoch,
                    last_event_ts_epoch, last_event_source_type, last_event_action_type, last_event_txn_key,
                    needs_review, review_reason,
                    years_remaining_next, has_contract_next
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (snapshot_id, league_key, season_year, player_name)
                DO UPDATE SET
                    ending_team_key = EXCLUDED.ending_team_key,
                    ending_owner_name = EXCLUDED.ending_owner_name,
                    contract_voided = EXCLUDED.contract_voided,
                    void_reason = EXCLUDED.void_reason,
                    voided_ts_epoch = EXCLUDED.voided_ts_epoch,
                    last_event_ts_epoch = EXCLUDED.last_event_ts_epoch,
                    last_event_source_type = EXCLUDED.last_event_source_type,
                    last_event_action_type = EXCLUDED.last_event_action_type,
                    last_event_txn_key = EXCLUDED.last_event_txn_key,
                    needs_review = EXCLUDED.needs_review,
                    review_reason = EXCLUDED.review_reason,
                    years_remaining_next = EXCLUDED.years_remaining_next,
                    has_contract_next = EXCLUDED.has_contract_next,
                    updated_at = now();
            """, upserts)

        conn.commit()

    print(f"Wrote contract_reconcile for season {season_year} (snapshot {snapshot_id})")


if __name__ == "__main__":
    main()
