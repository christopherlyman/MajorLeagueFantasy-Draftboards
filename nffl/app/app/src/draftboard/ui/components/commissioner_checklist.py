from __future__ import annotations

from typing import Any

import psycopg
import streamlit as st
from draftboard.ui.components.commissioner_tools import (
    _load_nffl_contract_readiness,
)


from draftboard.state.runtime import (
    get_draft_key,
    get_league_key,
    get_postgres_dsn,
    get_season_year,
)


def _default_state() -> dict[str, Any]:
    return {
        "runtime_year": get_season_year(),
        "runtime_league": get_league_key(),
        "runtime_draft": get_draft_key(),
        "runtime_context_found": False,
        "runtime_active": False,
        "contract_ready": False,
        "invalid_active_contracts": 0,
        "qo_rows": 0,
        "ft_rows": 0,
        "locked_rows": 0,
        "published_qos": 0,
        "qoft_revealed": False,
        "future_staged": None,
        "error": None,
        "next_action": {
            "title": "Read Commissioner state",
            "kind": "waiting",
            "section": "Commissioner",
            "detail": "Loading current NFFL lifecycle state.",
        },
        "checks": [],
    }


def get_commissioner_checklist_state() -> dict[str, Any]:
    """
    Return read-only NFFL Commissioner rollover/readiness state.

    This function does not:
    - call Yahoo;
    - modify contracts or QO/FT decisions;
    - create manager links;
    - stage or activate a season;
    - modify runtime configuration;
    - write to the database.
    """

    state = _default_state()

    runtime_year = int(state["runtime_year"])
    runtime_league = str(state["runtime_league"])
    runtime_draft = str(state["runtime_draft"])

    try:
        with psycopg.connect(get_postgres_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_season_year,
                        current_league_key,
                        prior_season_year,
                        prior_league_key,
                        draft_key,
                        is_active
                    FROM nffl.season_context
                    WHERE league_code='NFFL'
                    ORDER BY current_season_year;
                    """
                )
                season_rows = cur.fetchall()

                readiness = _load_nffl_contract_readiness(
                    dsn=get_postgres_dsn(),
                    league_key=runtime_league,
                    season_year=runtime_year,
                )
                readiness_counts = dict(
                    readiness.get("counts")
                    or {}
                )
                invalid_active = int(
                    readiness_counts.get(
                        "current_invalid_active_contracts:"
                        "invalid_active_contracts",
                        0,
                    )
                )

                cur.execute(
                    """
                    SELECT
                        count(*) FILTER (
                            WHERE decision_type IN (
                                'QO1',
                                'QO2',
                                'QO3',
                                'QO4'
                            )
                        )::integer AS qo_rows,
                        count(*) FILTER (
                            WHERE decision_type='FT'
                        )::integer AS ft_rows,
                        count(*) FILTER (
                            WHERE decision_status='LOCKED'
                        )::integer AS locked_rows
                    FROM nffl.offseason_keeper_decision
                    WHERE league_key=%s
                      AND season_year=%s;
                    """,
                    (
                        runtime_league,
                        runtime_year,
                    ),
                )
                qoft_counts = cur.fetchone() or (
                    0,
                    0,
                    0,
                )

                cur.execute(
                    """
                    SELECT count(*)::integer
                    FROM public.qualifying_offer
                    WHERE league_key=%s
                      AND season_year=%s;
                    """,
                    (
                        runtime_league,
                        runtime_year,
                    ),
                )
                published_qos = int(
                    (cur.fetchone() or [0])[0]
                    or 0
                )

                cur.execute(
                    """
                    SELECT
                        COALESCE(qoft_revealed,false)
                    FROM nffl.league_visibility_state
                    WHERE league_key=%s
                      AND season_year=%s;
                    """,
                    (
                        runtime_league,
                        runtime_year,
                    ),
                )
                visibility = cur.fetchone()

    except Exception as exc:
        state["error"] = str(exc)
        state["next_action"] = {
            "title": "Resolve Commissioner state error",
            "kind": "action",
            "section": "Commissioner",
            "detail": (
                "The live NFFL lifecycle state could not be read. "
                "Do not continue the annual rollover until this is resolved."
            ),
        }
        return state

    runtime_rows = [
        row
        for row in season_rows
        if int(row[0]) == runtime_year
        and str(row[1]) == runtime_league
        and str(row[4]) == runtime_draft
    ]

    if len(runtime_rows) != 1:
        state["error"] = (
            "Expected exactly one nffl.season_context row matching "
            "the runtime season, league key, and draft key; "
            f"found {len(runtime_rows)}."
        )
        state["next_action"] = {
            "title": "Resolve runtime season identity",
            "kind": "action",
            "section": "Commissioner",
            "detail": state["error"],
        }
        return state

    runtime_row = runtime_rows[0]
    runtime_active = bool(runtime_row[5])

    future_rows = [
        row
        for row in season_rows
        if int(row[0]) > runtime_year
        and not bool(row[5])
    ]

    if len(future_rows) > 1:
        state["error"] = (
            "More than one future inactive NFFL season context exists. "
            "Resolve the ambiguous staged-season state before continuing."
        )
        state["next_action"] = {
            "title": "Resolve staged-season ambiguity",
            "kind": "action",
            "section": "Initialize New Season",
            "detail": state["error"],
        }
        return state

    future_staged = None

    if future_rows:
        row = future_rows[0]
        future_staged = {
            "season_year": int(row[0]),
            "league_key": str(row[1]),
            "prior_season_year": (
                int(row[2])
                if row[2] is not None
                else None
            ),
            "prior_league_key": str(
                row[3]
                or ""
            ),
            "draft_key": str(row[4]),
        }

    qo_rows = int(
        qoft_counts[0]
        or 0
    )
    ft_rows = int(
        qoft_counts[1]
        or 0
    )
    locked_rows = int(
        qoft_counts[2]
        or 0
    )
    qoft_revealed = bool(
        visibility
        and visibility[0]
    )

    state.update(
        {
            "runtime_context_found": True,
            "runtime_active": runtime_active,
            "contract_ready": (
                invalid_active == 0
            ),
            "invalid_active_contracts":
                invalid_active,
            "qo_rows": qo_rows,
            "ft_rows": ft_rows,
            "locked_rows": locked_rows,
            "published_qos": published_qos,
            "qoft_revealed": qoft_revealed,
            "future_staged": future_staged,
        }
    )

    contract_ready = bool(
        state["contract_ready"]
    )

    if not runtime_active:
        next_action = {
            "title": (
                f"Activate staged NFFL {runtime_year}"
            ),
            "kind": "action",
            "section": "Activate Staged Season",
            "detail": (
                "The runtime already points at an inactive staged "
                "season. Verify the displayed runtime identity, type "
                f"ACTIVATE NFFL {runtime_year}, and activate it."
            ),
        }

    elif future_staged is not None:
        staged_year = int(
            future_staged["season_year"]
        )
        next_action = {
            "title": (
                f"Switch runtime to staged NFFL {staged_year}"
            ),
            "kind": "action",
            "section": (
                "Outside DraftBoard - NFFL runtime configuration"
            ),
            "detail": (
                f"NFFL {staged_year} is staged but inactive. "
                "Update the annual NFFL runtime configuration to "
                "the staged season, league key, and draft key; "
                "recreate only NFFL; then return to Commissioner."
            ),
        }

    elif not contract_ready:
        next_action = {
            "title": "Resolve contract integrity blockers",
            "kind": "action",
            "section": "Contract Readiness",
            "detail": (
                f"{invalid_active} active contract(s) fail "
                "same-team end-of-prior-season roster reconciliation."
            ),
        }

    elif not qoft_revealed:
        next_action = {
            "title": "Publish and reveal QO/FT",
            "kind": "action",
            "section": "QO/FT Publish and Reveal",
            "detail": (
                "Contract integrity is clear, but QO/FT is still "
                "private. Review the status and use Publish / Reveal QO-FT."
            ),
        }

    else:
        target_year = runtime_year + 1
        next_action = {
            "title": (
                f"Wait for Yahoo to expose NFFL {target_year}"
            ),
            "kind": "waiting",
            "section": "Initialize New Season",
            "detail": (
                "When Yahoo renews the league, open Initialize New "
                "Season, enter the numeric Yahoo League ID, and use "
                "Refresh Yahoo Teams. Resolve franchise mappings and "
                "stage the season only after the preview is complete."
            ),
        }

    checks: list[dict[str, str]] = []

    checks.append(
        {
            "label": "Current runtime season",
            "state": (
                "complete"
                if runtime_active
                else "action"
            ),
            "detail": (
                f"NFFL {runtime_year} / "
                f"{runtime_league} / "
                f"{runtime_draft} is "
                + (
                    "active."
                    if runtime_active
                    else "staged but inactive."
                )
            ),
        }
    )

    checks.append(
        {
            "label": "Contract integrity",
            "state": (
                "complete"
                if contract_ready
                else "action"
            ),
            "detail": (
                "No invalid active contracts."
                if contract_ready
                else (
                    f"{invalid_active} active contract(s) "
                    "require reconciliation."
                )
            ),
        }
    )

    if qoft_revealed:
        qoft_state = "complete"
        qoft_detail = (
            f"Revealed; {qo_rows} saved QO decision(s), "
            f"{ft_rows} saved FT decision(s), "
            f"{published_qos} published QO row(s), "
            f"{locked_rows} locked decision row(s)."
        )

    elif contract_ready:
        qoft_state = "action"
        qoft_detail = (
            "QO/FT is still private and requires "
            "Commissioner publish/reveal."
        )

    else:
        qoft_state = "locked"
        qoft_detail = (
            "Resolve contract integrity before QO/FT "
            "publish/reveal."
        )

    checks.append(
        {
            "label": "QO/FT publish and reveal",
            "state": qoft_state,
            "detail": qoft_detail,
        }
    )

    if not runtime_active:
        rollover_state = "complete"
        rollover_detail = (
            f"NFFL {runtime_year} is already staged and "
            "the runtime points to it; activation remains."
        )

    elif future_staged is not None:
        staged_year = int(
            future_staged["season_year"]
        )
        rollover_state = "action"
        rollover_detail = (
            f"NFFL {staged_year} is staged and inactive; "
            "switch the runtime before activation."
        )

    elif contract_ready and qoft_revealed:
        rollover_state = "waiting"
        rollover_detail = (
            f"No future season is staged. Waiting for Yahoo "
            f"to expose NFFL {runtime_year + 1}."
        )

    else:
        rollover_state = "locked"
        rollover_detail = (
            "Complete current-season contract and QO/FT "
            "prerequisites first."
        )

    checks.append(
        {
            "label": "Next-season rollover",
            "state": rollover_state,
            "detail": rollover_detail,
        }
    )

    state["next_action"] = next_action
    state["checks"] = checks

    return state


def _render_check(
    label: str,
    *,
    state: str,
    detail: str,
) -> None:
    labels = {
        "complete": "Complete",
        "waiting": "Waiting",
        "action": "Action",
        "locked": "Locked",
    }

    status = labels.get(
        state,
        "Waiting",
    )

    st.markdown(
        f"**{status}: {label}** - {detail}"
    )


def render_commissioner_checklist(
    *,
    gateway_context: dict[str, object],
) -> None:
    role = str(
        gateway_context.get("role")
        or "public"
    ).strip().lower()

    if role != "commissioner":
        return

    state = get_commissioner_checklist_state()

    st.markdown(
        "### Commissioner Readiness"
    )

    next_action = dict(
        state["next_action"]
    )

    message = (
        f"**Next action: {next_action['title']}**  \n"
        f"{next_action['detail']}  \n"
        f"Section: {next_action['section']}"
    )

    if next_action["kind"] == "waiting":
        st.info(message)
    else:
        st.warning(message)

    if state.get("error"):
        st.error(
            str(state["error"])
        )
        return

    completed = sum(
        1
        for check in state["checks"]
        if check["state"] == "complete"
    )
    total = len(
        state["checks"]
    )

    with st.expander(
        (
            "Annual Rollover Readiness - "
            f"{completed}/{total} complete"
        ),
        expanded=True,
    ):
        for check in state["checks"]:
            _render_check(
                str(check["label"]),
                state=str(check["state"]),
                detail=str(check["detail"]),
            )

        st.caption(
            "This readiness panel is read-only. It does not call "
            "Yahoo or modify league data merely by being displayed."
        )
