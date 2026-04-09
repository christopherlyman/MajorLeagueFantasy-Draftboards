from __future__ import annotations

import re
import unicodedata


def normalize_player_search_text(value: str) -> str:
    s = str(value or "").strip().casefold()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def player_search_matches(query: str, candidate: str) -> bool:
    q = normalize_player_search_text(query)
    if not q:
        return True
    c = normalize_player_search_text(candidate)
    return q in c


def filter_player_keys_by_query(
    player_keys: list[str],
    query: str,
    label_func,
) -> list[str]:
    if not normalize_player_search_text(query):
        return list(player_keys)
    return [pk for pk in player_keys if player_search_matches(query, label_func(pk))]