from __future__ import annotations

from typing import Any

from draftboard.state.runtime import get_league_key, get_postgres_dsn, get_season_year

import yaml


def load_league_profile_yaml_from_db(
    dsn: str,
    league_key: str,
    season_year: int,
) -> str:
    if not dsn:
        raise RuntimeError("Missing DSN for league profile load.")

    try:
        import psycopg
    except Exception as e:
        raise RuntimeError(f"psycopg import failed: {e}")

    sql = """
        SELECT profile_yaml
        FROM public.league_profile
        WHERE league_key = %s
          AND season_year = %s
          AND is_active = true
    """

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (league_key, season_year))
            row = cur.fetchone()

    if not row or not row[0]:
        raise RuntimeError(
            f"No active league_profile found for league_key={league_key} season_year={season_year}"
        )

    return str(row[0])


def parse_league_profile_yaml(yaml_text: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(yaml_text)
    except Exception as e:
        raise RuntimeError(f"League profile YAML parse failed: {e}")

    if not isinstance(raw, dict):
        raise RuntimeError("League profile YAML must parse to a top-level mapping.")

    return raw


def validate_league_profile(profile: dict[str, Any]) -> None:
    required_top = ["league", "draft", "scoring", "features", "roster", "categories", "runtime"]
    for key in required_top:
        if key not in profile:
            raise RuntimeError(f"League profile missing top-level section: {key}")

    league = profile["league"]
    draft = profile["draft"]
    scoring = profile["scoring"]
    features = profile["features"]
    roster = profile["roster"]
    categories = profile["categories"]
    runtime = profile["runtime"]

    for key in ["league_key", "name", "platform", "sport", "season_year", "manager_count"]:
        if key not in league:
            raise RuntimeError(f"League profile missing league.{key}")

    for key in ["type", "order_mode", "mode", "rounds_total", "pick_trades_allowed"]:
        if key not in draft:
            raise RuntimeError(f"League profile missing draft.{key}")

    if "format" not in scoring:
        raise RuntimeError("League profile missing scoring.format")

    for key in ["keeper", "contracts", "qualifying_offers", "franchise_tags", "prospect_tags", "commissioner_tools"]:
        if key not in features:
            raise RuntimeError(f"League profile missing features.{key}")

    if "positions" not in roster or not isinstance(roster["positions"], list) or not roster["positions"]:
        raise RuntimeError("League profile roster.positions must be a non-empty list")

    sport = str(league.get("sport", "")).lower()
    if sport == "baseball":
        for key in ["batting", "pitching"]:
            if key not in categories or not isinstance(categories[key], list) or not categories[key]:
                raise RuntimeError(f"League profile categories.{key} must be a non-empty list")
    else:
        if not isinstance(categories, dict) or not any(isinstance(v, list) and v for v in categories.values()):
            raise RuntimeError("League profile categories must contain at least one non-empty list")

    if runtime.get("db_scope_mode") != "league_key":
        raise RuntimeError("League profile runtime.db_scope_mode must equal 'league_key'")

    platform = str(league["platform"]).lower()
    if platform != "yahoo":
        raise RuntimeError(f"Unsupported v1 platform: {platform}")

    draft_type = str(draft["type"]).lower()
    if draft_type == "auction":
        raise RuntimeError("Auction draft type is recognized but unsupported in v1.")
    if draft_type != "standard":
        raise RuntimeError(f"Unsupported v1 draft.type: {draft_type}")

    order_mode = str(draft["order_mode"]).lower()
    if order_mode not in {"snake", "straight"}:
        raise RuntimeError(f"Unsupported draft.order_mode: {order_mode}")

    draft_mode = str(draft["mode"]).lower()
    if draft_mode not in {"offline", "live"}:
        raise RuntimeError(f"Unsupported draft.mode: {draft_mode}")

    scoring_format = str(scoring["format"]).lower()
    if scoring_format not in {"h2h_points", "h2h_categories", "roto"}:
        raise RuntimeError(f"Unsupported scoring.format: {scoring_format}")

    keeper = bool(features["keeper"])
    keeper_type = features.get("keeper_type")
    keepers_count = features.get("keepers_count")
    contract_lengths = features.get("contract_lengths")

    if not keeper:
        if keeper_type is not None:
            raise RuntimeError("features.keeper_type must be absent when features.keeper=false")
        if keepers_count is not None:
            raise RuntimeError("features.keepers_count must be absent when features.keeper=false")
        if contract_lengths is not None:
            raise RuntimeError("features.contract_lengths must be absent when features.keeper=false")

    if keeper:
        if keeper_type not in {"dynasty", "contract", "auction"}:
            raise RuntimeError("features.keeper_type must be one of: dynasty, contract, auction")

        if keepers_count is None:
            raise RuntimeError("features.keepers_count is required when features.keeper=true")

        if keeper_type == "auction":
            raise RuntimeError("Auction keeper type is recognized but unsupported in v1.")

        if keeper_type == "contract":
            if not isinstance(contract_lengths, list) or not contract_lengths:
                raise RuntimeError("features.contract_lengths is required when keeper_type=contract")

        if keeper_type != "contract" and contract_lengths is not None:
            raise RuntimeError("features.contract_lengths must be absent unless keeper_type=contract")

    contracts = bool(features["contracts"])
    qos = bool(features["qualifying_offers"])
    franchise_tags = bool(features["franchise_tags"])
    prospect_tags = bool(features["prospect_tags"])

    if not contracts and (qos or franchise_tags or prospect_tags):
        raise RuntimeError(
            "If features.contracts=false, then qualifying_offers, franchise_tags, and prospect_tags must all be false."
        )


def load_league_profile_from_db(
    dsn: str,
    league_key: str,
    season_year: int,
) -> dict[str, Any]:
    yaml_text = load_league_profile_yaml_from_db(dsn, league_key, season_year)
    profile = parse_league_profile_yaml(yaml_text)
    validate_league_profile(profile)
    return profile




def get_active_league_profile() -> dict[str, Any]:
    dsn = get_postgres_dsn()
    league_key = get_league_key()
    season_year = get_season_year()
    return load_league_profile_from_db(dsn, league_key, season_year)



def get_active_draft_order_mode() -> str:
    profile = get_active_league_profile()
    return str(profile["draft"]["order_mode"]).lower()


def get_active_first_standard_round() -> int:
    profile = get_active_league_profile()
    features = profile["features"]

    return get_active_qo_rounds() + 1 if bool(features.get("qualifying_offers", False)) else 1


def summarize_league_profile(profile: dict[str, Any]) -> dict[str, Any]:
    league = profile["league"]
    draft = profile["draft"]
    scoring = profile["scoring"]
    features = profile["features"]

    out: dict[str, Any] = {
        "league_key": league["league_key"],
        "name": league["name"],
        "platform": league["platform"],
        "sport": league["sport"],
        "season_year": league["season_year"],
        "manager_count": league["manager_count"],
        "draft_type": draft["type"],
        "draft_order_mode": draft["order_mode"],
        "draft_mode": draft["mode"],
        "rounds_total": draft["rounds_total"],
        "pick_trades_allowed": draft["pick_trades_allowed"],
        "scoring_format": scoring["format"],
        "keeper": features["keeper"],
        "contracts": features["contracts"],
        "qualifying_offers": features["qualifying_offers"],
        "franchise_tags": features["franchise_tags"],
        "prospect_tags": features["prospect_tags"],
        "commissioner_tools": features["commissioner_tools"],
    }

    if features.get("keeper_type") is not None:
        out["keeper_type"] = features["keeper_type"]
    if features.get("keepers_count") is not None:
        out["keepers_count"] = features["keepers_count"]
    if features.get("contract_lengths") is not None:
        out["contract_lengths"] = features["contract_lengths"]

    return out

def get_active_rounds_total() -> int:
    profile = get_active_league_profile()
    return int(profile["draft"]["rounds_total"])


def get_active_qualifying_offers_enabled() -> bool:
    profile = get_active_league_profile()
    return bool(profile["features"].get("qualifying_offers", False))

def get_active_manager_count() -> int:
    profile = get_active_league_profile()
    return int(profile["league"]["manager_count"])


def get_active_qo_rounds() -> int:
    profile = get_active_league_profile()
    if not bool(profile["features"].get("qualifying_offers", False)):
        return 0

    from draftboard.domain.rules import QO_ROUNDS
    return int(profile.get("draft", {}).get("qo_rounds", QO_ROUNDS))
