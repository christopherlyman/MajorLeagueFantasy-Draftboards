from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


YAHOO_FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


class YahooFantasyAdapterError(RuntimeError):
    """Raised when Yahoo transport or payload parsing fails."""


@dataclass(frozen=True)
class YahooGame:
    game_key: str
    name: str | None
    code: str | None
    season: int | None


@dataclass(frozen=True)
class YahooLeague:
    league_key: str
    league_id: str | None
    name: str | None
    game_key: str
    season: int | None
    num_teams: int | None


@dataclass(frozen=True)
class YahooTeam:
    team_key: str
    team_id: str | None
    name: str | None
    owner_name: str | None
    manager_id: str | None
    owner_guid: str | None


JsonGetter = Callable[
    [str, Mapping[str, str], float],
    Mapping[str, Any],
]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    text = _optional_text(value)

    if text is None:
        return None

    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _extract_from_blocks(
    blocks: list[Any],
    key: str,
) -> Any:
    for block in blocks:
        if isinstance(block, dict) and key in block:
            return block[key]

    return None


def _iter_metadata_blocks(
    node: Any,
    anchor_key: str,
):
    if isinstance(node, list):
        if any(
            isinstance(item, dict)
            and anchor_key in item
            for item in node
        ):
            yield node

        for item in node:
            yield from _iter_metadata_blocks(
                item,
                anchor_key,
            )

    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_metadata_blocks(
                value,
                anchor_key,
            )


def _require_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise YahooFantasyAdapterError(
            "Yahoo response must be a JSON object."
        )

    fantasy_content = payload.get(
        "fantasy_content"
    )

    if not isinstance(
        fantasy_content,
        Mapping,
    ):
        raise YahooFantasyAdapterError(
            "Yahoo response is missing fantasy_content."
        )

    return fantasy_content


def _league_game_key(
    league_key: str,
) -> str:
    marker = ".l."

    if marker not in league_key:
        raise YahooFantasyAdapterError(
            "Yahoo league_key has unexpected "
            f"format: {league_key!r}."
        )

    game_key = league_key.split(
        marker,
        1,
    )[0].strip()

    if not game_key:
        raise YahooFantasyAdapterError(
            "Yahoo league_key has no game key: "
            f"{league_key!r}."
        )

    return game_key


def _extract_owner(
    team_blocks: list[Any],
) -> tuple[
    str | None,
    str | None,
    str | None,
]:
    managers_block = _extract_from_blocks(
        team_blocks,
        "managers",
    )

    if (
        not isinstance(managers_block, list)
        or not managers_block
    ):
        return (None, None, None)

    managers: list[dict[str, Any]] = []

    for item in managers_block:
        if not isinstance(item, dict):
            continue

        manager = item.get("manager")

        if isinstance(manager, dict):
            managers.append(manager)

    if not managers:
        return (None, None, None)

    chosen = next(
        (
            manager
            for manager in managers
            if str(
                manager.get(
                    "is_commissioner",
                    "",
                )
            )
            == "1"
        ),
        managers[0],
    )

    return (
        _optional_text(
            chosen.get("nickname")
        ),
        _optional_text(
            chosen.get("manager_id")
        ),
        _optional_text(
            chosen.get("guid")
        ),
    )


def parse_yahoo_games(
    payload: Mapping[str, Any],
) -> list[YahooGame]:
    fantasy_content = _require_payload(
        payload
    )
    games: list[YahooGame] = []
    seen: set[str] = set()

    for blocks in _iter_metadata_blocks(
        fantasy_content,
        "game_key",
    ):
        game_key = _optional_text(
            _extract_from_blocks(
                blocks,
                "game_key",
            )
        )

        if (
            not game_key
            or game_key in seen
        ):
            continue

        seen.add(game_key)

        games.append(
            YahooGame(
                game_key=game_key,
                name=_optional_text(
                    _extract_from_blocks(
                        blocks,
                        "name",
                    )
                ),
                code=_optional_text(
                    _extract_from_blocks(
                        blocks,
                        "code",
                    )
                ),
                season=_optional_int(
                    _extract_from_blocks(
                        blocks,
                        "season",
                    )
                ),
            )
        )

    return games


def parse_yahoo_leagues(
    payload: Mapping[str, Any],
) -> list[YahooLeague]:
    fantasy_content = _require_payload(
        payload
    )
    leagues: list[YahooLeague] = []
    seen: set[str] = set()

    for blocks in _iter_metadata_blocks(
        fantasy_content,
        "league_key",
    ):
        league_key = _optional_text(
            _extract_from_blocks(
                blocks,
                "league_key",
            )
        )

        if (
            not league_key
            or league_key in seen
        ):
            continue

        seen.add(league_key)

        leagues.append(
            YahooLeague(
                league_key=league_key,
                league_id=_optional_text(
                    _extract_from_blocks(
                        blocks,
                        "league_id",
                    )
                ),
                name=_optional_text(
                    _extract_from_blocks(
                        blocks,
                        "name",
                    )
                ),
                game_key=_league_game_key(
                    league_key
                ),
                season=_optional_int(
                    _extract_from_blocks(
                        blocks,
                        "season",
                    )
                ),
                num_teams=_optional_int(
                    _extract_from_blocks(
                        blocks,
                        "num_teams",
                    )
                ),
            )
        )

    return leagues


def parse_yahoo_teams(
    payload: Mapping[str, Any],
) -> list[YahooTeam]:
    fantasy_content = _require_payload(
        payload
    )
    teams: list[YahooTeam] = []
    seen: set[str] = set()

    for blocks in _iter_metadata_blocks(
        fantasy_content,
        "team_key",
    ):
        team_key = _optional_text(
            _extract_from_blocks(
                blocks,
                "team_key",
            )
        )

        if (
            not team_key
            or team_key in seen
        ):
            continue

        seen.add(team_key)

        (
            owner_name,
            manager_id,
            owner_guid,
        ) = _extract_owner(blocks)

        teams.append(
            YahooTeam(
                team_key=team_key,
                team_id=_optional_text(
                    _extract_from_blocks(
                        blocks,
                        "team_id",
                    )
                ),
                name=_optional_text(
                    _extract_from_blocks(
                        blocks,
                        "name",
                    )
                ),
                owner_name=owner_name,
                manager_id=manager_id,
                owner_guid=owner_guid,
            )
        )

    return teams


def _default_get_json(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> Mapping[str, Any]:
    request = Request(
        url,
        headers=dict(headers),
        method="GET",
    )

    try:
        with urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read()

    except HTTPError as exc:
        raise YahooFantasyAdapterError(
            "Yahoo request failed with "
            f"HTTP {exc.code}."
        ) from exc

    except URLError as exc:
        raise YahooFantasyAdapterError(
            "Yahoo request could not be completed."
        ) from exc

    try:
        payload = json.loads(
            raw.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise YahooFantasyAdapterError(
            "Yahoo response was not valid JSON."
        ) from exc

    if not isinstance(payload, Mapping):
        raise YahooFantasyAdapterError(
            "Yahoo response must be a JSON object."
        )

    return payload


class YahooFantasyAdapter:
    def __init__(
        self,
        *,
        get_json: JsonGetter | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive."
            )

        self._get_json = (
            get_json
            or _default_get_json
        )
        self._timeout_seconds = float(
            timeout_seconds
        )

    def _fetch(
        self,
        *,
        access_token: str,
        url: str,
    ) -> Mapping[str, Any]:
        token = _optional_text(
            access_token
        )

        if token is None:
            raise YahooFantasyAdapterError(
                "Yahoo access token is required."
            )

        return self._get_json(
            url,
            {
                "Authorization":
                    f"Bearer {token}"
            },
            self._timeout_seconds,
        )

    def fetch_games(
        self,
        *,
        access_token: str,
    ) -> list[YahooGame]:
        payload = self._fetch(
            access_token=access_token,
            url=(
                f"{YAHOO_FANTASY_BASE}/"
                "users;use_login=1/"
                "games?format=json"
            ),
        )

        return parse_yahoo_games(
            payload
        )

    def fetch_leagues(
        self,
        *,
        access_token: str,
        game_key: str,
    ) -> list[YahooLeague]:
        key = _optional_text(
            game_key
        )

        if key is None:
            raise YahooFantasyAdapterError(
                "Yahoo game_key is required."
            )

        payload = self._fetch(
            access_token=access_token,
            url=(
                f"{YAHOO_FANTASY_BASE}/"
                "users;use_login=1/games;"
                f"game_keys={key}/"
                "leagues?format=json"
            ),
        )

        return parse_yahoo_leagues(
            payload
        )

    def fetch_teams(
        self,
        *,
        access_token: str,
        league_key: str,
    ) -> list[YahooTeam]:
        key = _optional_text(
            league_key
        )

        if key is None:
            raise YahooFantasyAdapterError(
                "Yahoo league_key is required."
            )

        payload = self._fetch(
            access_token=access_token,
            url=(
                f"{YAHOO_FANTASY_BASE}/"
                f"league/{key}/"
                "teams?format=json"
            ),
        )

        return parse_yahoo_teams(
            payload
        )
