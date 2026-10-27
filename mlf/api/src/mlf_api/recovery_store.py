from __future__ import annotations

from draftboard.data.draft_runtime import (
    reset_draft_atomic,
)
from draftboard.state.runtime import (
    get_draft_key,
    get_postgres_dsn,
)
from mlf_api.draft_tools_store import (
    get_commissioner_draft_tools_state,
)


RESET_DRAFT_CONFIRMATION = "RESET DRAFT"


def reset_commissioner_draft(
    *,
    confirmation: str,
) -> dict[str, object]:
    confirmation_text = str(
        confirmation or ""
    ).strip()

    if (
        confirmation_text
        != RESET_DRAFT_CONFIRMATION
    ):
        raise ValueError(
            "Reset Draft confirmation "
            "does not match."
        )

    before = (
        get_commissioner_draft_tools_state()
    )

    result = reset_draft_atomic(
        dsn=get_postgres_dsn(),
        draft_key=get_draft_key(),
    )

    after = (
        get_commissioner_draft_tools_state()
    )

    return {
        "action":
            "reset_draft",
        "result":
            result,
        "cleared_selection_count":
            int(
                before["selection_count"]
            ),
        "state":
            after,
    }
