from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


NULL_NUMERIC_STRINGS = {
    "",
    "-",
    "--",
    "N/A",
}


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def direct_value(blocks: list[Any], key: str) -> Any:
    """
    Return a key only when it exists directly in one dictionary in
    the supplied Yahoo metadata list.

    Deliberately does not recurse into nested dictionaries.
    """
    for item in blocks:
        if isinstance(item, dict) and key in item:
            return item[key]

    return None


def outer_segment_value(outer: list[Any], key: str) -> Any:
    """
    Find a named top-level supplemental Yahoo player segment.

    Example:
        {"percent_owned": [...]}
        {"player_ranks": [...]}
        {"draft_analysis": [...]}

    Does not search inside the metadata list.
    """
    for segment in outer:
        if isinstance(segment, dict) and key in segment:
            return segment[key]

    return None


def object_list_to_dict(value: Any) -> dict[str, Any]:
    """
    Convert Yahoo's common object-list representation:

        [{"foo": 1}, {"bar": 2}]

    into:

        {"foo": 1, "bar": 2}
    """
    if isinstance(value, dict):
        return dict(value)

    result: dict[str, Any] = {}

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                result.update(item)

    return result


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError(
            f"Boolean value is not valid numeric Yahoo data: {value!r}"
        )

    raw = str(value).strip()

    if raw.upper() in NULL_NUMERIC_STRINGS:
        return None

    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid Yahoo numeric value: {value!r}"
        ) from exc
