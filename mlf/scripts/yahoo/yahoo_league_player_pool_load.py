import json
import math
import os
import time
from pathlib import Path

import psycopg
import requests

from auth import get_access_token
from yahoo_bulk_load import (
    _extract_percent_owned as _extract_percent_owned_meta,
    _extract_rank_fields as _extract_rank_fields_meta,
    _fetch_players_with_bisect,
    extract_player_blocks,
    extract_stats,
    fetch_league_players_meta,
    fetch_league_players_stats,
    find_player_key,
)

YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    return int(raw)


def _find_first_value(blocks, key: str):
    if not isinstance(blocks, list):
        return None
    for item in blocks:
        if isinstance(item, dict) and key in item:
            return item.get(key)
    return None


def _extract_player_key(player_blocks):
    return _find_first_value(player_blocks, "player_key")


def _extract_name(player_blocks):
    name = _find_first_value(player_blocks, "name")
    if isinstance(name, dict):
        full = name.get("full")
        if full:
            return str(full)
    if isinstance(name, str):
        return name
    return ""


def _extract_editorial_team_abbr(player_blocks):
    v = _find_first_value(player_blocks, "editorial_team_abbr")
    return "" if v is None else str(v)


def _extract_eligible_positions(player_blocks):
    ep = _find_first_value(player_blocks, "eligible_positions")
    out = []
    if isinstance(ep, list):
        for item in ep:
            if isinstance(item, dict) and "position" in item:
                pos = item.get("position")
                if pos is not None:
                    out.append(str(pos))
            elif isinstance(item, str):
                out.append(item)
    return out


def _extract_percent_owned(player_blocks):
    po = _find_first_value(player_blocks, "percent_owned")
    if isinstance(po, dict):
        val = po.get("value")
        return None if val in (None, "") else val
    return None


def _extract_rank_value(player_blocks):
    da = _find_first_value(player_blocks, "draft_analysis")
    if isinstance(da, dict):
        avg_pick = da.get("average_pick")
        return None if avg_pick in (None, "") else avg_pick
    return None


def _extract_stat_map(player_blocks):
    ps = _find_first_value(player_blocks, "player_stats")
    stats = {}
    if isinstance(ps, dict):
        coverage = ps.get("stats")
        if isinstance(coverage, list):
            for item in coverage:
                if not isinstance(item, dict):
                    continue
                stat = item.get("stat")
                if not isinstance(stat, dict):
                    continue
                sid = stat.get("stat_id")
                val = stat.get("value")
                if sid is not None:
                    stats[str(sid)] = val
    return stats


def _normalize_row(league_key: str, season_year: int, player_blocks):
    pkey = _extract_player_key(player_blocks)
    if not pkey:
        return None

    stat_map = _extract_stat_map(player_blocks)

    return {
        "league_key": league_key,
        "season_year": int(season_year),
        "yahoo_player_key": str(pkey),
        "source_game_key": str(pkey).split(".")[0],
        "full_name": _extract_name(player_blocks),
        "editorial_team_abbr": _extract_editorial_team_abbr(player_blocks) or None,
        "eligible_positions": json.dumps(_extract_eligible_positions(player_blocks)),
        "percent_owned": _extract_percent_owned(player_blocks),
        "rank_value": _extract_rank_value(player_blocks),
        "has_qo": False,
        "qo_level": None,
        "is_poachable_this_round": False,
        "h_ab": stat_map.get("60"),
        "r": stat_map.get("7"),
        "hr": stat_map.get("12"),
        "rbi": stat_map.get("13"),
        "sb": stat_map.get("16"),
        "bb": stat_map.get("18"),
        "k_hit": stat_map.get("55"),
        "avg": stat_map.get("3"),
        "ip": stat_map.get("50"),
        "w": stat_map.get("28"),
        "k_pit": stat_map.get("42"),
        "tb": stat_map.get("1000001"),
        "era": stat_map.get("26"),
        "whip": stat_map.get("27"),
        "qs": stat_map.get("83"),
        "sv_h": stat_map.get("89"),
        "raw_payload": json.dumps(player_blocks),
    }



def _seed_pool_from_shared_base(dsn: str, league_key: str, season_year: int) -> int:
    source_game_key = str(league_key).split(".")[0]

    sql = """
    INSERT INTO public.yahoo_league_player_pool (
        league_key, season_year, yahoo_player_key,
        source_game_key, full_name, editorial_team_abbr, eligible_positions,
        percent_owned, rank_value, has_qo, qo_level, is_poachable_this_round,
        h_ab, r, hr, rbi, sb, bb, k_hit, avg, ip, w, k_pit, tb, era, whip, qs, sv_h,
        raw_payload, created_at_utc, updated_at_utc
    )
    SELECT
        %s AS league_key,
        %s AS season_year,
        yp.yahoo_player_key,
        yp.source_game_key,
        yp.full_name,
        ypm.editorial_team_abbr,
        COALESCE(ypm.eligible_positions, '[]'::jsonb) AS eligible_positions,
        ypm.percent_owned,
        ypm.rank_value,
        false AS has_qo,
        NULL::integer AS qo_level,
        false AS is_poachable_this_round,
        NULL::text AS h_ab,
        NULL::integer AS r,
        NULL::integer AS hr,
        NULL::integer AS rbi,
        NULL::integer AS sb,
        NULL::integer AS bb,
        NULL::integer AS k_hit,
        NULL::numeric AS avg,
        NULL::numeric AS ip,
        NULL::integer AS w,
        NULL::integer AS k_pit,
        NULL::integer AS tb,
        NULL::numeric AS era,
        NULL::numeric AS whip,
        NULL::integer AS qs,
        NULL::integer AS sv_h,
        NULL::jsonb AS raw_payload,
        now(),
        now()
    FROM public.yahoo_player yp
    LEFT JOIN public.yahoo_player_meta ypm
      ON ypm.yahoo_player_key = yp.yahoo_player_key
     AND ypm.source_game_key = yp.source_game_key
    WHERE yp.source_game_key = %s
    ON CONFLICT (league_key, season_year, yahoo_player_key)
    DO UPDATE SET
        source_game_key = EXCLUDED.source_game_key,
        full_name = EXCLUDED.full_name,
        editorial_team_abbr = EXCLUDED.editorial_team_abbr,
        eligible_positions = EXCLUDED.eligible_positions,
        percent_owned = EXCLUDED.percent_owned,
        rank_value = EXCLUDED.rank_value,
        updated_at_utc = now();
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year, source_game_key))
            return cur.rowcount

def _parse_players(payload):
    league = payload.get("fantasy_content", {}).get("league", [])
    if not isinstance(league, list) or len(league) < 2 or not isinstance(league[1], dict):
        return [], 0

    players = league[1].get("players")
    if not isinstance(players, dict):
        return [], 0

    count = int(players.get("count") or 0)
    blocks = []
    for k, v in players.items():
        if k == "count":
            continue
        if isinstance(v, dict) and "player" in v and isinstance(v["player"], list) and v["player"]:
            player_blocks = v["player"][0]
            if isinstance(player_blocks, list):
                blocks.append(player_blocks)
    return blocks, count


def _load_pool_player_keys(dsn: str, league_key: str, season_year: int) -> list[str]:
    sql = """
    SELECT yahoo_player_key
    FROM public.yahoo_league_player_pool
    WHERE league_key = %s
      AND season_year = %s
    ORDER BY yahoo_player_key
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            return [str(r[0]) for r in cur.fetchall()]


def _stats_map_from_pairs(pairs: list[tuple[int, str]]) -> dict[str, str]:
    out = {}
    for sid, raw in pairs:
        out[str(int(sid))] = "" if raw is None else str(raw)
    return out


def _enrich_meta_batch(dsn: str, league_key: str, season_year: int, rows: list[tuple]) -> int:
    if not rows:
        return 0

    sql = """
    UPDATE public.yahoo_league_player_pool
       SET percent_owned = %s,
           rank_value = %s,
           updated_at_utc = now()
     WHERE league_key = %s
       AND season_year = %s
       AND yahoo_player_key = %s
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def _enrich_stats_batch(dsn: str, league_key: str, season_year: int, rows: list[dict]) -> int:
    if not rows:
        return 0

    sql = """
    UPDATE public.yahoo_league_player_pool
       SET h_ab = %s,
           r = %s,
           hr = %s,
           rbi = %s,
           sb = %s,
           bb = %s,
           k_hit = %s,
           avg = %s,
           ip = %s,
           w = %s,
           k_pit = %s,
           tb = %s,
           era = %s,
           whip = %s,
           qs = %s,
           sv_h = %s,
           updated_at_utc = now()
     WHERE league_key = %s
       AND season_year = %s
       AND yahoo_player_key = %s
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def _upsert_rows(dsn: str, rows):
    if not rows:
        return 0

    sql = """
    INSERT INTO public.yahoo_league_player_pool (
        league_key, season_year, yahoo_player_key,
        source_game_key, full_name, editorial_team_abbr, eligible_positions,
        percent_owned, rank_value, has_qo, qo_level, is_poachable_this_round,
        h_ab, r, hr, rbi, sb, bb, k_hit, avg, ip, w, k_pit, tb, era, whip, qs, sv_h,
        raw_payload, created_at_utc, updated_at_utc
    ) VALUES (
        %(league_key)s, %(season_year)s, %(yahoo_player_key)s,
        %(source_game_key)s, %(full_name)s, %(editorial_team_abbr)s, %(eligible_positions)s::jsonb,
        %(percent_owned)s, %(rank_value)s, %(has_qo)s, %(qo_level)s, %(is_poachable_this_round)s,
        %(h_ab)s, %(r)s, %(hr)s, %(rbi)s, %(sb)s, %(bb)s, %(k_hit)s, %(avg)s, %(ip)s, %(w)s, %(k_pit)s, %(tb)s, %(era)s, %(whip)s, %(qs)s, %(sv_h)s,
        %(raw_payload)s::jsonb, now(), now()
    )
    ON CONFLICT (league_key, season_year, yahoo_player_key)
    DO UPDATE SET
        source_game_key = EXCLUDED.source_game_key,
        full_name = EXCLUDED.full_name,
        editorial_team_abbr = EXCLUDED.editorial_team_abbr,
        eligible_positions = EXCLUDED.eligible_positions,
        percent_owned = EXCLUDED.percent_owned,
        rank_value = EXCLUDED.rank_value,
        has_qo = EXCLUDED.has_qo,
        qo_level = EXCLUDED.qo_level,
        is_poachable_this_round = EXCLUDED.is_poachable_this_round,
        h_ab = EXCLUDED.h_ab,
        r = EXCLUDED.r,
        hr = EXCLUDED.hr,
        rbi = EXCLUDED.rbi,
        sb = EXCLUDED.sb,
        bb = EXCLUDED.bb,
        k_hit = EXCLUDED.k_hit,
        avg = EXCLUDED.avg,
        ip = EXCLUDED.ip,
        w = EXCLUDED.w,
        k_pit = EXCLUDED.k_pit,
        tb = EXCLUDED.tb,
        era = EXCLUDED.era,
        whip = EXCLUDED.whip,
        qs = EXCLUDED.qs,
        sv_h = EXCLUDED.sv_h,
        raw_payload = EXCLUDED.raw_payload,
        updated_at_utc = now();
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def main():
    league_key = str(os.environ.get("YAHOO_LEAGUE_KEY", "") or "").strip()
    if not league_key:
        raise SystemExit("Missing YAHOO_LEAGUE_KEY")

    season_year = _env_int("SEASON_YEAR", 2026)
    dsn = str(os.environ.get("POSTGRES_DSN") or os.environ.get("MLF_POSTGRES_DSN") or "").strip()
    if not dsn:
        raise SystemExit("Missing POSTGRES_DSN or MLF_POSTGRES_DSN")

    page_size = _env_int("YAHOO_COUNT", 25)
    max_pages = _env_int("YAHOO_MAX_PAGES", 1)
    sleep_seconds = _env_int("YAHOO_SLEEP_SECONDS", 1)

    out_dir = Path(os.environ.get("YAHOO_RAW_OUT_DIR", "data/raw/yahoo/"))
    out_dir.mkdir(parents=True, exist_ok=True)

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    total_written = 0

    print(f"BEGIN league_key={league_key} season_year={season_year} page_size={page_size} max_pages={max_pages}", flush=True)

    total_written = _seed_pool_from_shared_base(dsn, league_key, season_year)
    print(
        f"SEEDED_FROM_SHARED_BASE league_key={league_key} season_year={season_year} upserted={total_written}",
        flush=True,
    )

    meta_batch_size = _env_int("YAHOO_META_BATCH_SIZE", 25)
    meta_write_raw = str(os.environ.get("YAHOO_WRITE_RAW_META", "") or "").strip().lower() in ("1", "true", "yes", "y")

    player_keys = _load_pool_player_keys(dsn, league_key, season_year)
    total_keys = len(player_keys)

    print(
        f"BEGIN_META league_key={league_key} season_year={season_year} meta_batch_size={meta_batch_size} total_keys={total_keys}",
        flush=True,
    )

    total_meta_upserts = 0
    for batch_num, start in enumerate(range(0, total_keys, meta_batch_size), start=1):
        batch = player_keys[start:start + meta_batch_size]
        print(
            f"meta_batch={batch_num} start={start} players={len(batch)} requesting...",
            flush=True,
        )

        payload, meta_bad = _fetch_players_with_bisect(
            fetch_league_players_meta,
            league_key,
            batch,
            token,
            label="MILF_META",
        )

        if not payload:
            payload = {"fantasy_content": {"league": [None, {"players": {"count": 0}}]}}

        if meta_write_raw:
            out_path = out_dir / f"league_{league_key.replace('.', '_')}_meta_batch{batch_num}.json"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        blocks = extract_player_blocks(payload)
        update_rows = []
        for block in blocks:
            pkey = find_player_key(block)
            if not pkey:
                continue
            percent_owned, _percent_owned_delta = _extract_percent_owned_meta(block)
            rf = _extract_rank_fields_meta(block)
            update_rows.append((
                percent_owned,
                rf.get("rank_value"),
                league_key,
                season_year,
                pkey,
            ))

        written_meta = _enrich_meta_batch(dsn, league_key, season_year, update_rows)
        total_meta_upserts += written_meta

        print(
            f"meta_batch={batch_num} blocks={len(blocks)} bad_keys={len(meta_bad)} "
            f"upserted={written_meta} cumulative_meta_upserted={total_meta_upserts}",
            flush=True,
        )

        time.sleep(sleep_seconds)

    stats_season = _env_int("YAHOO_STATS_SEASON", max(int(season_year) - 1, 0))
    stats_batch_size = _env_int("YAHOO_STATS_BATCH_SIZE", 25)
    stats_write_raw = str(os.environ.get("YAHOO_WRITE_RAW_STATS", "") or "").strip().lower() in ("1", "true", "yes", "y")

    total_stats_keys = len(player_keys)
    print(
        f"BEGIN_STATS league_key={league_key} season_year={season_year} stats_season={stats_season} "
        f"stats_batch_size={stats_batch_size} total_keys={total_stats_keys}",
        flush=True,
    )

    total_stats_upserts = 0
    for batch_num, start in enumerate(range(0, total_stats_keys, stats_batch_size), start=1):
        batch = player_keys[start:start + stats_batch_size]
        print(
            f"stats_batch={batch_num} start={start} players={len(batch)} requesting...",
            flush=True,
        )

        payload, stats_bad = _fetch_players_with_bisect(
            fetch_league_players_stats,
            league_key,
            batch,
            stats_season,
            token,
            label="MILF_STATS",
        )

        if not payload:
            payload = {"fantasy_content": {"league": [None, {"players": {"count": 0}}]}}

        if stats_write_raw:
            out_path = out_dir / f"league_{league_key.replace('.', '_')}_stats_batch{batch_num}.json"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        blocks = extract_player_blocks(payload)
        update_rows = []
        for block in blocks:
            pkey = find_player_key(block)
            if not pkey:
                continue
            sm = _stats_map_from_pairs(extract_stats(block))
            update_rows.append((
                sm.get("60") or None,
                None if sm.get("7") in (None, "", "-") else int(float(sm.get("7"))),
                None if sm.get("12") in (None, "", "-") else int(float(sm.get("12"))),
                None if sm.get("13") in (None, "", "-") else int(float(sm.get("13"))),
                None if sm.get("16") in (None, "", "-") else int(float(sm.get("16"))),
                None if sm.get("18") in (None, "", "-") else int(float(sm.get("18"))),
                None if sm.get("21") in (None, "", "-") else int(float(sm.get("21"))),
                None if sm.get("3") in (None, "", "-") else float(sm.get("3")),
                None if sm.get("50") in (None, "", "-") else float(sm.get("50")),
                None if sm.get("28") in (None, "", "-") else int(float(sm.get("28"))),
                None if sm.get("42") in (None, "", "-") else int(float(sm.get("42"))),
                None if sm.get("49") in (None, "", "-") else float(sm.get("49")),
                None if sm.get("26") in (None, "", "-") else float(sm.get("26")),
                None if sm.get("27") in (None, "", "-") else float(sm.get("27")),
                None if sm.get("83") in (None, "", "-") else int(float(sm.get("83"))),
                None if sm.get("89") in (None, "", "-") else int(float(sm.get("89"))),
                league_key,
                season_year,
                pkey,
            ))

        written_stats = _enrich_stats_batch(dsn, league_key, season_year, update_rows)
        total_stats_upserts += written_stats

        print(
            f"stats_batch={batch_num} blocks={len(blocks)} bad_keys={len(stats_bad)} "
            f"upserted={written_stats} cumulative_stats_upserted={total_stats_upserts}",
            flush=True,
        )

        time.sleep(sleep_seconds)

    print(f"END total_upserted={total_written} total_meta_upserted={total_meta_upserts} total_stats_upserted={total_stats_upserts}", flush=True)


if __name__ == "__main__":
    main()
