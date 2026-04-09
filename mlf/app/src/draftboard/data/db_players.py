from typing import Dict, List, Any
from decimal import Decimal

import psycopg

from draftboard.domain.models import Player, Position
from draftboard.state.runtime import get_draft_key, get_league_key, get_season_year


def _map_pos(s: str) -> Position | None:
    if not s:
        return None
    t = s.strip().upper()

    # Yahoo examples include: Util, 1B, 2B, 3B, SS, OF, C, SP, RP, P, LF, CF, RF, DH
    if t == "UTIL":
        return Position.UTIL
    if t == "C":
        return Position.C
    if t == "1B":
        return Position._1B
    if t == "2B":
        return Position._2B
    if t == "3B":
        return Position._3B
    if t == "SS":
        return Position.SS

    # Outfield sub-positions collapse to OF (we don't track LF/CF/RF separately)
    if t in ("OF", "LF", "CF", "RF"):
        return Position.OF

    # DH behaves like UTIL in our model
    if t == "DH":
        return Position.UTIL

    if t == "P":
        return Position.P
    if t == "SP":
        return Position.SP
    if t == "RP":
        return Position.RP

    # Defensive fallback: ignore unknown tokens rather than lying
    return None


def _positions_from_json(val: Any) -> List[Position]:
    # expected eligible_positions is jsonb, usually list[str] OR list[{position: ...}]
    if not isinstance(val, list):
        return [Position.UTIL]

    out: List[Position] = []
    for x in val:
        token = None

        if isinstance(x, str):
            token = x
        elif isinstance(x, dict):
            token = x.get('position') or x.get('pos') or x.get('name')
        elif hasattr(x, 'get'):
            # defensive: mapping-like objects
            token = x.get('position') or x.get('pos') or x.get('name')

        if isinstance(token, dict) and hasattr(token, 'get'):
            # defensive: nested dicts (rare)
            token = token.get('position') or token.get('pos') or token.get('name')

        if isinstance(token, str):
            p = _map_pos(token)
            if p and p not in out:
                out.append(p)

    if not out:
        return [Position.UTIL]

    # Primary position rules:
    # - UTIL always last
    # - P always second-to-last
    def _prio(pp: Position) -> int:
        if pp == Position.UTIL:
            return 99
        if pp == Position.P:
            return 98
        return 0

    return sorted(out, key=_prio)
def load_available_players(dsn: str) -> Dict[str, Player]:
    draft_key = get_draft_key()
    league_key = get_league_key()
    season_year = get_season_year()


    sql = """
  SELECT
    ap.yahoo_player_key,
    ap.full_name,
    ap.editorial_team_abbr,
    ap.eligible_positions,

    /* QO override wins if present; else predraft qualifying_offer */
    CASE
      WHEN qo.qo_group IS NOT NULL THEN true
      WHEN qop.qo_level IS NOT NULL THEN true
      ELSE false
    END AS has_qo,

    COALESCE(qo.qo_group, qop.qo_level) AS qo_level,

    /* Poach-eligible: QO level above current round (Round 1 => QO2-5 poachable) */
    CASE
      WHEN COALESCE(qo.qo_group, qop.qo_level) IS NOT NULL
       AND COALESCE(qo.qo_group, qop.qo_level) > rs.current_round
      THEN true ELSE false
    END AS is_poachable_this_round,

    ap.rank_value,
    ap.percent_owned,
    ap.h_ab,
    ap.r,
    ap.hr,
    ap.rbi,
    ap.sb,
    ap.bb,
    ap.k_hit,
    ap.avg,
    ap.ip,
    ap.w,
    ap.k_pit,
    ap.tb,
    ap.era,
    ap.whip,
    ap.qs,
    ap.sv_h
  FROM public.v_available_players_current ap

  LEFT JOIN public.qo_overrides qo
    ON qo.draft_key = %s
   AND qo.yahoo_player_key = ap.yahoo_player_key

  LEFT JOIN public.qualifying_offer qop
    ON qop.league_key = %s
   AND qop.season_year = %s
   AND qop.yahoo_player_key = ap.yahoo_player_key

  LEFT JOIN public.qo_round_state rs
    ON rs.league_key = %s
   AND rs.season_year = %s

  ORDER BY ap.full_name;

    """

    players: Dict[str, Player] = {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('mlf.league_key', %s, true);", (league_key,))
            cur.execute("SELECT set_config('mlf.game_key', %s, true);", (str(league_key).split(".")[0],))
            cur.execute("SELECT set_config('mlf.stats_season', %s, true);", (str(max(int(season_year) - 1, 0)),))
            cur.execute(sql, (draft_key, league_key, season_year, league_key, season_year))

            def _to_float(x):
                if x is None:
                    return None
                if isinstance(x, Decimal):
                    return float(x)
                return float(x) if isinstance(x, (int, float)) else x

            def _to_int(x):
                if x is None:
                    return None
                if isinstance(x, Decimal):
                    return int(x)
                return int(x) if isinstance(x, (int, float)) else x

            for row in cur.fetchall():
                (
                    pkey, full_name, team_abbr, elig_pos, has_qo, qo_level, is_poach,
                    rank_value, percent_owned,
                    h_ab, r, hr, rbi, sb, bb, k_hit, avg,
                    ip, w, k_pit, tb, era, whip, qs, sv_h,
                ) = row

                players[pkey] = Player(
                    player_key=pkey,
                    name=full_name,
                    mlb_team=team_abbr or "",
                    positions=_positions_from_json(elig_pos),
                    is_qo_eligible=bool(has_qo),
                    qo_group=int(qo_level) if qo_level is not None else None,
                    is_poach_eligible=bool(is_poach),
                    rank_value=_to_float(rank_value),
                    percent_owned=_to_float(percent_owned),
                    h_ab=h_ab,
                    r=_to_int(r),
                    hr=_to_int(hr),
                    rbi=_to_int(rbi),
                    sb=_to_int(sb),
                    bb=_to_int(bb),
                    k_hit=_to_int(k_hit),
                    avg=_to_float(avg),
                    ip=_to_float(ip),
                    w=_to_int(w),
                    k_pit=_to_int(k_pit),
                    tb=_to_int(tb),
                    era=_to_float(era),
                    whip=_to_float(whip),
                    qs=_to_int(qs),
                    sv_h=_to_int(sv_h),
                )

    return players


def load_milf_available_players(dsn: str) -> Dict[str, Player]:
    """
    MiLF-specific available-player path.
    Reads from public.yahoo_league_player_pool scoped by (league_key, season_year).
    """
    league_key = get_league_key()
    season_year = get_season_year()

    sql = """
    SELECT
        yahoo_player_key,
        full_name,
        editorial_team_abbr,
        eligible_positions,
        has_qo,
        qo_level,
        is_poachable_this_round,
        rank_value,
        percent_owned,
        h_ab,
        r,
        hr,
        rbi,
        sb,
        bb,
        k_hit,
        avg,
        ip,
        w,
        k_pit,
        tb,
        era,
        whip,
        qs,
        sv_h
    FROM public.yahoo_league_player_pool
    WHERE league_key = %s
      AND season_year = %s
    ORDER BY full_name;
    """

    players: Dict[str, Player] = {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))

            def _to_float(x):
                if x is None:
                    return None
                if isinstance(x, Decimal):
                    return float(x)
                return float(x) if isinstance(x, (int, float)) else x

            def _to_int(x):
                if x is None:
                    return None
                if isinstance(x, Decimal):
                    return int(x)
                return int(x) if isinstance(x, (int, float)) else x

            for row in cur.fetchall():
                (
                    pkey, full_name, team_abbr, elig_pos, has_qo, qo_level, is_poach,
                    rank_value, percent_owned,
                    h_ab, r, hr, rbi, sb, bb, k_hit, avg,
                    ip, w, k_pit, tb, era, whip, qs, sv_h,
                ) = row

                players[pkey] = Player(
                    player_key=pkey,
                    name=full_name,
                    mlb_team=team_abbr or "",
                    positions=_positions_from_json(elig_pos),
                    is_qo_eligible=bool(has_qo),
                    qo_group=int(qo_level) if qo_level is not None else None,
                    is_poach_eligible=bool(is_poach),
                    rank_value=_to_float(rank_value),
                    percent_owned=_to_float(percent_owned),
                    h_ab=h_ab,
                    r=_to_int(r),
                    hr=_to_int(hr),
                    rbi=_to_int(rbi),
                    sb=_to_int(sb),
                    bb=_to_int(bb),
                    k_hit=_to_int(k_hit),
                    avg=_to_float(avg),
                    ip=_to_float(ip),
                    w=_to_int(w),
                    k_pit=_to_int(k_pit),
                    tb=_to_int(tb),
                    era=_to_float(era),
                    whip=_to_float(whip),
                    qs=_to_int(qs),
                    sv_h=_to_int(sv_h),
                )

    return players


def load_shared_predraft_stats_overlay(dsn: str) -> dict[str, dict[str, object]]:
    """
    Temporary shared stats overlay for predraft boards.

    Uses the full shared 2025 stats universe already loaded in
    public.yahoo_player_league_season_stat for MLF.
    """
    stats_league_key = "469.l.41640"
    stats_season = max(int(get_season_year()) - 1, 0)

    sql = """
    SELECT
        yahoo_player_key,
        max(case when stat_id = 60 then value_raw end) as h_ab,
        max(case when stat_id = 7  then value_num end) as r,
        max(case when stat_id = 12 then value_num end) as hr,
        max(case when stat_id = 13 then value_num end) as rbi,
        max(case when stat_id = 16 then value_num end) as sb,
        max(case when stat_id = 18 then value_num end) as bb,
        max(case when stat_id = 21 then value_num end) as k_hit,
        max(case when stat_id = 3  then value_num end) as avg,
        max(case when stat_id = 50 then value_num end) as ip,
        max(case when stat_id = 28 then value_num end) as w,
        max(case when stat_id = 42 then value_num end) as k_pit,
        max(case when stat_id = 49 then value_num end) as tb,
        max(case when stat_id = 26 then value_num end) as era,
        max(case when stat_id = 27 then value_num end) as whip,
        max(case when stat_id = 83 then value_num end) as qs,
        max(case when stat_id = 89 then value_num end) as sv_h
    FROM public.yahoo_player_league_season_stat
    WHERE league_key = %s
      AND season_year = %s
    GROUP BY yahoo_player_key
    ORDER BY yahoo_player_key
    """

    def _to_float(x):
        if x is None:
            return None
        if isinstance(x, Decimal):
            return float(x)
        return float(x) if isinstance(x, (int, float)) else x

    def _to_int(x):
        if x is None:
            return None
        if isinstance(x, Decimal):
            return int(x)
        return int(x) if isinstance(x, (int, float)) else x

    out: dict[str, dict[str, object]] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (stats_league_key, stats_season))
            for row in cur.fetchall():
                (
                    pkey, h_ab, r, hr, rbi, sb, bb, k_hit, avg,
                    ip, w, k_pit, tb, era, whip, qs, sv_h,
                ) = row
                out[str(pkey)] = {
                    "h_ab": h_ab,
                    "r": _to_int(r),
                    "hr": _to_int(hr),
                    "rbi": _to_int(rbi),
                    "sb": _to_int(sb),
                    "bb": _to_int(bb),
                    "k_hit": _to_int(k_hit),
                    "avg": _to_float(avg),
                    "ip": _to_float(ip),
                    "w": _to_int(w),
                    "k_pit": _to_int(k_pit),
                    "tb": _to_int(tb),
                    "era": _to_float(era),
                    "whip": _to_float(whip),
                    "qs": _to_int(qs),
                    "sv_h": _to_int(sv_h),
                }

    return out


def load_milf_predraft_available_players(dsn: str) -> Dict[str, Player]:
    """
    Temporary MiLF next-season predraft reader.

    Base universe:
      shared game-wide available-player universe (2339-style path)

    Overlay:
      MiLF-specific meta/stats from public.yahoo_league_player_pool
    """
    base_players = load_mlf_available_players(dsn)
    shared_stats = load_shared_predraft_stats_overlay(dsn)
    milf_overlay = load_milf_available_players(dsn)

    stat_fields = [
        "h_ab", "r", "hr", "rbi", "sb", "bb", "k_hit", "avg",
        "ip", "w", "k_pit", "tb", "era", "whip", "qs", "sv_h",
    ]

    for pkey, stats_map in shared_stats.items():
        dest = base_players.get(pkey)
        if dest is None:
            continue

        for field in stat_fields:
            val = stats_map.get(field)
            if val is not None:
                setattr(dest, field, val)

    for pkey, src in milf_overlay.items():
        dest = base_players.get(pkey)
        if dest is None:
            base_players[pkey] = src
            continue

        if src.mlb_team:
            dest.mlb_team = src.mlb_team

        if src.positions:
            dest.positions = src.positions

        if src.rank_value is not None:
            dest.rank_value = src.rank_value

        if src.percent_owned is not None:
            dest.percent_owned = src.percent_owned

        for field in stat_fields:
            val = getattr(src, field)
            if val is not None:
                setattr(dest, field, val)

        dest.is_qo_eligible = bool(src.is_qo_eligible)
        dest.qo_group = src.qo_group
        dest.is_poach_eligible = bool(src.is_poach_eligible)

    return base_players


def load_active_available_players(dsn: str) -> Dict[str, Player]:
    """
    Active league-scoped available-player reader seam.

    Current behavior:
    - MiLF 2026 -> public.yahoo_league_player_pool
    - everything else -> legacy MLF/shared path
    """
    league_key = get_league_key()
    season_year = get_season_year()

    if league_key == "469.l.60688" and int(season_year) == 2026:
        return load_milf_available_players(dsn)

    return load_mlf_available_players(dsn)


def load_mlf_available_players(dsn: str) -> Dict[str, Player]:
    """
    Legacy MLF-specific available-player path.
    Backed by public.v_available_players_current / public.v_mlf_available_players_current.
    No behavior change.
    """
    return load_available_players(dsn)


def load_contracted_player_keys(dsn: str) -> set[str]:
    """
    Canonical source: public.contract scoped by (MLF_LEAGUE_KEY, MLF_SEASON_YEAR)

    Contracted keys rule:
      - years_remaining > 0 => contracted
    """
    league_key = get_league_key()
    season_year = get_season_year()

    sql = """
            SELECT yahoo_player_key
            FROM public.v_contracts_effective_current;
        """

    out: set[str] = set()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            out = {str(r[0]) for r in rows if r and r[0] is not None}

    return out

def load_pt_players(dsn: str, league_key: str, season_year: int) -> dict[str, str]:
    """
    Returns PT map: {yahoo_player_key: TEAM_XX}

    IMPORTANT (validated 2026-02-18):
      public.prospect_tag IS league + season scoped.
      All reads must filter by (league_key, season_year).
      Primary key includes: (league_key, season_year, yahoo_player_key)
    """
    sql = """
    SELECT team_key, yahoo_player_key
    FROM public.prospect_tag
    WHERE league_key=%s
      AND season_year=%s
      AND yahoo_player_key IS NOT NULL
      AND team_key IS NOT NULL;
    """

    import psycopg
    out: dict[str, str] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            for team_key, yahoo_player_key in cur.fetchall():
                out[str(yahoo_player_key)] = str(team_key)
    return out

def load_contract_overrides(dsn: str, league_key: str, season_year: int) -> dict[str, dict]:
    """
    Returns overrides keyed by yahoo_player_key:
      {
        "469.p.123": {
           "years_remaining": 0|1|2|...,
           "yahoo_team_key": "",
           "yahoo_team_name": "",
           "note": "",
        },
        ...
      }
    """
    sql = """
      SELECT yahoo_player_key, years_remaining, yahoo_team_key, yahoo_team_name, note
      FROM public.contract_override
      WHERE league_key=%s AND season_year=%s;
    """
    out: dict[str, dict] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            for pkey, yrs, tkey, tname, note in cur.fetchall():
                if not pkey:
                    continue
                out[str(pkey)] = {
                    "years_remaining": int(yrs) if yrs is not None else 0,
                    "yahoo_team_key": str(tkey or ""),
                    "yahoo_team_name": str(tname or ""),
                    "note": str(note or ""),
                }
    return out

def load_franchise_season_team_order(dsn: str, league_key: str, season_year: int) -> list[str]:
    """
    Returns canonical slot order as a list of 16 Yahoo team_keys ordered by franchise_id asc.
    Source: public.franchise_season_team (SSOT).
    """
    import psycopg

    sql = """
      SELECT team_key
      FROM public.franchise_season_team
      WHERE league_key=%s AND season_year=%s
      ORDER BY franchise_id;
    """

    out: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            out = [str(r[0]) for r in cur.fetchall() if r and r[0]]
    return out


def load_yahoo_team_map(dsn: str, league_key: str, season_year: int) -> list[dict]:
    """
    Canonical Yahoo team metadata for a league/season.
    Source: public.yahoo_team_map.
    """
    import psycopg

    sql = """
        SELECT
          league_key,
          season_year,
          team_key,
          team_name,
          owner_name,
          owner_guid,
          updated_at
        FROM public.yahoo_team_map
        WHERE league_key = %s
          AND season_year = %s
        ORDER BY team_key
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_contract_years_map(dsn: str, league_key: str, season_year: int) -> dict[str, int]:
    """
    {yahoo_player_key: years_remaining} for currently contracted players.
    Source: public.contract (canonical).
    """
    import psycopg

    sql = """
        SELECT yahoo_player_key, years_remaining
        FROM public.contract
        WHERE league_key = %s
          AND season_year = %s
          AND years_remaining > 0
    """

    out: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            for pkey, yrs in cur.fetchall():
                if not pkey:
                    continue
                out[str(pkey)] = int(yrs)
    return out


def load_contracts_current(dsn: str, league_key: str, season_year: int) -> list[dict]:
    """
    Canonical contract truth for the DraftBoard runtime.
    Source: public.contract (single source of truth; no overlays).
    """
    import psycopg

    sql = """
        SELECT
          league_key,
          season_year,
          team_key,
          yahoo_player_key,
          years_remaining,
          note,
          updated_at
        FROM public.contract
        WHERE league_key = %s
          AND season_year = %s
          AND years_remaining > 0
        ORDER BY team_key, yahoo_player_key
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

