from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


_CONTEXT_KEY = "NFHL"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value


def get_postgres_dsn() -> str:
    """
    Infrastructure configuration remains environment-backed.

    Season identity is deliberately NOT read from environment variables.
    """
    return _required("POSTGRES_DSN")


def _validate_runtime_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    draft_key = str(
        row.get("draft_key") or ""
    ).strip()

    league_key = str(
        row.get("league_key") or ""
    ).strip()

    try:
        season_year = int(
            row.get("season_year")
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "NFHL runtime context has an invalid season_year."
        ) from exc

    try:
        manager_count = int(
            row.get("manager_count")
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "NFHL runtime context has an invalid manager_count."
        ) from exc

    try:
        rounds_total = int(
            row.get("rounds_total")
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "NFHL runtime context has an invalid rounds_total."
        ) from exc

    config = row.get("config_json")

    if not isinstance(
        config,
        dict,
    ):
        raise RuntimeError(
            "NFHL draft_config.config_json must be a JSON object."
        )

    league_config = config.get(
        "league"
    )

    draft_config = config.get(
        "draft"
    )

    if not isinstance(
        league_config,
        dict,
    ):
        raise RuntimeError(
            "NFHL draft configuration is missing league settings."
        )

    if not isinstance(
        draft_config,
        dict,
    ):
        raise RuntimeError(
            "NFHL draft configuration is missing draft settings."
        )

    if not draft_key:
        raise RuntimeError(
            "NFHL runtime context has no active draft_key."
        )

    if not league_key:
        raise RuntimeError(
            "NFHL runtime context has no league_key."
        )

    if (
        str(
            league_config.get(
                "league_key"
            )
            or ""
        ).strip()
        != league_key
    ):
        raise RuntimeError(
            "NFHL persisted configuration league_key does not "
            "match nfhl.draft."
        )

    try:
        config_season_year = int(
            league_config.get(
                "season_year"
            )
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "NFHL persisted configuration has an invalid "
            "league.season_year."
        ) from exc

    if config_season_year != season_year:
        raise RuntimeError(
            "NFHL persisted configuration season_year does not "
            "match nfhl.draft."
        )

    if (
        str(
            draft_config.get(
                "draft_key"
            )
            or ""
        ).strip()
        != draft_key
    ):
        raise RuntimeError(
            "NFHL persisted configuration draft_key does not "
            "match nfhl.draft."
        )

    try:
        config_manager_count = int(
            league_config.get(
                "manager_count_target"
            )
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "NFHL persisted configuration has an invalid "
            "league.manager_count_target."
        ) from exc

    if config_manager_count != manager_count:
        raise RuntimeError(
            "NFHL persisted configuration manager count does not "
            "match nfhl.draft."
        )

    try:
        config_rounds_total = int(
            draft_config.get(
                "rounds_total"
            )
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "NFHL persisted configuration has an invalid "
            "draft.rounds_total."
        ) from exc

    if config_rounds_total != rounds_total:
        raise RuntimeError(
            "NFHL persisted configuration round count does not "
            "match nfhl.draft."
        )

    database_order_mode = str(
        row.get(
            "draft_order_mode"
        )
        or ""
    ).strip()

    config_order_mode = str(
        draft_config.get(
            "order_mode"
        )
        or ""
    ).strip()

    if (
        database_order_mode
        and config_order_mode
        and database_order_mode
        != config_order_mode
    ):
        raise RuntimeError(
            "NFHL persisted configuration order mode does not "
            "match nfhl.draft."
        )

    return {
        "context_key": _CONTEXT_KEY,
        "draft_key": draft_key,
        "league_key": league_key,
        "season_year": season_year,
        "draft_label": str(
            row.get(
                "draft_label"
            )
            or ""
        ),
        "manager_count": manager_count,
        "rounds_total": rounds_total,
        "draft_order_mode": (
            database_order_mode
            or None
        ),
        "draft_status": str(
            row.get(
                "draft_status"
            )
            or ""
        ),
        "config_version": int(
            row.get(
                "config_version"
            )
        ),
        "config": deepcopy(
            config
        ),
    }


@lru_cache(maxsize=1)
def get_runtime_context() -> dict[str, Any]:
    """
    Load the single active NFHL draft/season from PostgreSQL.

    The context changes only during explicit commissioner season
    rollover, so it is cached for the running process. The supported
    rollover workflow must call clear_runtime_context_cache() after
    changing nfhl.runtime_context.
    """
    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    rc.context_key,
                    rc.active_draft_key AS draft_key,

                    d.league_key,
                    d.season_year,
                    d.draft_label,
                    d.manager_count,
                    d.rounds_total,
                    d.draft_order_mode,
                    d.status AS draft_status,

                    dc.config_version,
                    dc.config_json

                FROM nfhl.runtime_context rc

                JOIN nfhl.draft d
                  ON d.draft_key =
                     rc.active_draft_key

                JOIN nfhl.draft_config dc
                  ON dc.draft_key =
                     d.draft_key

                WHERE rc.context_key = %s
                """,
                (
                    _CONTEXT_KEY,
                ),
            )

            rows = list(
                cur.fetchall()
            )

    if len(rows) != 1:
        raise RuntimeError(
            "Expected exactly one NFHL runtime context; "
            f"found {len(rows)}."
        )

    return _validate_runtime_row(
        dict(
            rows[0]
        )
    )



_RUNTIME_SWITCHABLE_STATUSES = {
    "PREP",
    "SETUP",
    "ACTIVE",
    "COMPLETE",
}


def get_runtime_drafts() -> list[dict[str, Any]]:
    """
    Return validated persisted NFHL drafts that may be selected
    as the application runtime.

    Archived drafts are excluded.
    """
    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.draft_key,
                    d.league_key,
                    d.season_year,
                    d.draft_label,
                    d.manager_count,
                    d.rounds_total,
                    d.draft_order_mode,
                    d.status AS draft_status,

                    dc.config_version,
                    dc.config_json

                FROM nfhl.draft d

                JOIN nfhl.draft_config dc
                  ON dc.draft_key = d.draft_key

                WHERE d.status <> 'ARCHIVED'

                ORDER BY
                    d.season_year DESC,
                    d.draft_key DESC
                """
            )

            rows = list(
                cur.fetchall()
            )

    return [
        _validate_runtime_row(
            dict(row)
        )
        for row in rows
    ]


def switch_runtime_context(
    draft_key: str,
    *,
    actor: str = "commissioner",
) -> dict[str, Any]:
    """
    Explicitly switch the NFHL application runtime pointer.

    This is pointer-only:
      - nfhl.draft status is never changed.
      - draft selections are never changed.
      - draft configuration is never changed.

    Forward activation requires the current runtime draft to be
    COMPLETE.

    Rollback to an older season requires the target draft to be
    COMPLETE, allowing a future PREP/SETUP/ACTIVE season to be
    abandoned operationally while preserving its persisted state.
    """
    target_key = str(
        draft_key or ""
    ).strip()

    if not target_key:
        raise ValueError(
            "A target NFHL draft_key is required."
        )

    actor_value = (
        str(
            actor or ""
        ).strip()
        or "commissioner"
    )

    with psycopg.connect(
        get_postgres_dsn(),
        row_factory=dict_row,
    ) as conn:
        with conn.transaction():
            with conn.cursor() as cur:

                # Serialize all NFHL runtime switches.
                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (
                        _CONTEXT_KEY,
                    ),
                )

                cur.execute(
                    """
                    SELECT
                        active_draft_key
                    FROM nfhl.runtime_context
                    WHERE context_key = %s
                    FOR UPDATE
                    """,
                    (
                        _CONTEXT_KEY,
                    ),
                )

                current_row = cur.fetchone()

                if current_row is None:
                    raise RuntimeError(
                        "NFHL runtime context row does not exist."
                    )

                current_key = str(
                    current_row["active_draft_key"]
                ).strip()

                if current_key == target_key:
                    raise ValueError(
                        "The requested NFHL draft is already active."
                    )

                cur.execute(
                    """
                    SELECT
                        d.draft_key,
                        d.league_key,
                        d.season_year,
                        d.draft_label,
                        d.manager_count,
                        d.rounds_total,
                        d.draft_order_mode,
                        d.status AS draft_status,

                        dc.config_version,
                        dc.config_json

                    FROM nfhl.draft d

                    JOIN nfhl.draft_config dc
                      ON dc.draft_key = d.draft_key

                    WHERE d.draft_key = %s

                    FOR UPDATE OF d, dc
                    """,
                    (
                        target_key,
                    ),
                )

                target_row = cur.fetchone()

                if target_row is None:
                    raise ValueError(
                        "Target NFHL draft does not have a valid "
                        "draft/config pair."
                    )

                target_context = _validate_runtime_row(
                    dict(target_row)
                )

                target_status = str(
                    target_context["draft_status"]
                ).strip().upper()

                if target_status not in _RUNTIME_SWITCHABLE_STATUSES:
                    raise ValueError(
                        "Target NFHL draft status is not switchable: "
                        f"{target_status or 'UNKNOWN'}."
                    )

                cur.execute(
                    """
                    SELECT
                        season_year,
                        status
                    FROM nfhl.draft
                    WHERE draft_key = %s
                    FOR UPDATE
                    """,
                    (
                        current_key,
                    ),
                )

                current_draft = cur.fetchone()

                if current_draft is None:
                    raise RuntimeError(
                        "Current NFHL runtime draft no longer exists."
                    )

                current_year = int(
                    current_draft["season_year"]
                )

                current_status = str(
                    current_draft["status"]
                    or ""
                ).strip().upper()

                target_year = int(
                    target_context["season_year"]
                )

                if target_year > current_year:
                    if current_status != "COMPLETE":
                        raise ValueError(
                            "Forward NFHL season activation requires "
                            "the current runtime draft to be COMPLETE."
                        )

                elif target_year < current_year:
                    if target_status != "COMPLETE":
                        raise ValueError(
                            "NFHL rollback requires the target prior "
                            "season draft to be COMPLETE."
                        )

                else:
                    raise ValueError(
                        "NFHL runtime switching between drafts in the "
                        "same season is not supported."
                    )

                cur.execute(
                    """
                    UPDATE nfhl.runtime_context
                    SET
                        active_draft_key = %s,
                        updated_at_utc = now(),
                        updated_by = %s
                    WHERE context_key = %s
                    """,
                    (
                        target_key,
                        actor_value,
                        _CONTEXT_KEY,
                    ),
                )

    clear_runtime_context_cache()

    return {
        "previous_draft_key": current_key,
        "previous_season_year": current_year,
        "draft_key": target_context["draft_key"],
        "season_year": target_context["season_year"],
        "draft_label": target_context["draft_label"],
        "draft_status": target_context["draft_status"],
    }


def clear_runtime_context_cache() -> None:
    """
    Invalidate process-local season context after a supported rollover.
    """
    get_runtime_context.cache_clear()


def get_draft_key() -> str:
    return str(
        get_runtime_context()[
            "draft_key"
        ]
    )


def get_league_key() -> str:
    return str(
        get_runtime_context()[
            "league_key"
        ]
    )


def get_season_year() -> int:
    return int(
        get_runtime_context()[
            "season_year"
        ]
    )


def get_draft_config() -> dict[str, Any]:
    """
    Return a defensive copy of the persisted active-season config.
    """
    return deepcopy(
        get_runtime_context()[
            "config"
        ]
    )
