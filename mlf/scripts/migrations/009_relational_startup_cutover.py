from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


ROOT = Path(sys.argv[1])

INIT = ROOT / "mlf/app/src/draftboard/state/init_restore.py"
APP = ROOT / "mlf/app/src/draftboard/ui/app.py"


def read(path: Path) -> str:
    return (
        path.read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "")
    )


def write(path: Path, text: str) -> None:
    path.write_text(
        text.rstrip("\n") + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse(text: str, path: Path) -> ast.Module:
    return ast.parse(
        text,
        filename=str(path),
    )


def replace_lines(
    text: str,
    *,
    start_line: int,
    end_line: int,
    replacement: str,
) -> str:
    lines = text.splitlines(
        keepends=True
    )

    replacement_text = ""

    if replacement:
        replacement_text = (
            replacement.rstrip("\n")
            + "\n"
        )

    return (
        "".join(
            lines[: start_line - 1]
        )
        + replacement_text
        + "".join(
            lines[end_line:]
        )
    )


def find_function(
    text: str,
    path: Path,
    name: str,
):
    tree = parse(
        text,
        path,
    )

    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == name
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"{path.name}: expected one function "
            f"{name}; found {len(matches)}."
        )

    return matches[0]


def remove_function(
    text: str,
    path: Path,
    name: str,
) -> str:
    node = find_function(
        text,
        path,
        name,
    )

    return replace_lines(
        text,
        start_line=node.lineno,
        end_line=node.end_lineno,
        replacement="",
    )


def replace_function(
    text: str,
    path: Path,
    name: str,
    replacement: str,
) -> str:
    node = find_function(
        text,
        path,
        name,
    )

    return replace_lines(
        text,
        start_line=node.lineno,
        end_line=node.end_lineno,
        replacement=replacement,
    )


def call_name(
    node: ast.Call,
) -> str | None:
    if isinstance(
        node.func,
        ast.Name,
    ):
        return node.func.id

    if isinstance(
        node.func,
        ast.Attribute,
    ):
        return node.func.attr

    return None


def remove_call_statement(
    text: str,
    path: Path,
    function_name: str,
) -> str:
    tree = parse(
        text,
        path,
    )

    parents = {}

    for parent in ast.walk(
        tree
    ):
        for child in ast.iter_child_nodes(
            parent
        ):
            parents[
                child
            ] = parent

    statements = []

    for node in ast.walk(
        tree
    ):
        if not isinstance(
            node,
            ast.Call,
        ):
            continue

        if call_name(node) != function_name:
            continue

        current = node

        while (
            current in parents
            and not isinstance(
                current,
                ast.stmt,
            )
        ):
            current = parents[
                current
            ]

        if (
            isinstance(
                current,
                ast.stmt,
            )
            and current not in statements
        ):
            statements.append(
                current
            )

    if len(statements) != 1:
        raise RuntimeError(
            f"{path.name}: expected one executable "
            f"call to {function_name}; "
            f"found {len(statements)}."
        )

    statement = statements[
        0
    ]

    return replace_lines(
        text,
        start_line=statement.lineno,
        end_line=statement.end_lineno,
        replacement="",
    )


def replace_once(
    text: str,
    old: str,
    new: str,
    label: str,
) -> str:
    count = text.count(
        old
    )

    if count != 1:
        raise RuntimeError(
            f"{label}: expected one match; "
            f"found {count}."
        )

    return text.replace(
        old,
        new,
        1,
    )


def remove_global_autosave_import(
    text: str,
    path: Path,
) -> str:
    pattern = re.compile(
        r"^from draftboard\.state\.autosave "
        r"import [^\n]+\n",
        flags=re.MULTILINE,
    )

    updated, count = (
        pattern.subn(
            "",
            text,
            count=1,
        )
    )

    if count != 1:
        raise RuntimeError(
            f"{path.name}: expected one global "
            f"autosave import; found {count}."
        )

    return updated


def add_projection_import(
    text: str,
) -> str:
    projection_import = (
        "from draftboard.data.draft_projection "
        "import load_draft_state_projection\n"
    )

    if projection_import in text:
        return text

    anchor = (
        "from draftboard.data.picks_grid "
        "import build_picks_grid\n"
    )

    return replace_once(
        text,
        anchor,
        anchor + projection_import,
        "draft projection import",
    )


def add_get_draft_key(
    text: str,
    path: Path,
) -> str:
    pattern = re.compile(
        r"^from draftboard\.state\.runtime "
        r"import ([^\n]+)$",
        flags=re.MULTILINE,
    )

    matches = list(
        pattern.finditer(
            text
        )
    )

    if len(matches) != 1:
        raise RuntimeError(
            f"{path.name}: expected one state.runtime "
            f"import; found {len(matches)}."
        )

    match = matches[
        0
    ]

    names = [
        name.strip()
        for name in
        match.group(1).split(",")
        if name.strip()
    ]

    if (
        "get_draft_key"
        not in names
    ):
        names.insert(
            0,
            "get_draft_key",
        )

    preferred = [
        "get_draft_key",
        "get_league_key",
        "get_postgres_dsn",
        "get_season_year",
    ]

    ordered = [
        name
        for name in preferred
        if name in names
    ]

    ordered.extend(
        name
        for name in names
        if name not in ordered
    )

    replacement = (
        "from draftboard.state.runtime import "
        + ", ".join(
            ordered
        )
    )

    return (
        text[: match.start()]
        + replacement
        + text[match.end():]
    )


def replace_pick_guard(
    text: str,
    path: Path,
) -> str:
    function = find_function(
        text,
        path,
        "render_pick_controls",
    )

    matches = []

    for statement in function.body:
        if not isinstance(
            statement,
            ast.If,
        ):
            continue

        calls = {
            call_name(node)
            for node in ast.walk(
                statement.test
            )
            if isinstance(
                node,
                ast.Call,
            )
        }

        if (
            "_is_pick_open_for_live_draft"
            in calls
        ):
            matches.append(
                statement
            )

    if len(matches) != 1:
        raise RuntimeError(
            "render_pick_controls: expected one "
            "local current-pick healing guard; "
            f"found {len(matches)}."
        )

    node = matches[
        0
    ]

    replacement = '''    if not _is_pick_open_for_live_draft(current_pick):
        next_pick_id = _next_open_pick_id(
            state,
            after_pick_id=current_pick_id,
        )

        if next_pick_id is None:
            st.info("Draft complete.")
            return

        st.error(
            "Relational draft runtime inconsistency: "
            f"current pick {current_pick_id} is not open; "
            f"canonical next open pick is {next_pick_id}."
        )
        return
'''

    return replace_lines(
        text,
        start_line=node.lineno,
        end_line=node.end_lineno,
        replacement=replacement,
    )


def replace_footer(
    text: str,
    path: Path,
) -> str:
    function = find_function(
        text,
        path,
        "render_app",
    )

    body = list(
        function.body
    )

    import_index = None

    for index, statement in enumerate(
        body
    ):
        if (
            isinstance(
                statement,
                ast.ImportFrom,
            )
            and statement.module
            == "draftboard.state.autosave"
        ):
            import_index = index
            break

    if import_index is None:
        raise RuntimeError(
            "render_app: local AUTOSAVE_PATH "
            "import not found."
        )

    caption_index = None

    for index in range(
        import_index + 1,
        len(body),
    ):
        statement = body[
            index
        ]

        if not isinstance(
            statement,
            ast.Expr,
        ):
            continue

        value = statement.value

        if not isinstance(
            value,
            ast.Call,
        ):
            continue

        func = value.func

        if (
            isinstance(
                func,
                ast.Attribute,
            )
            and isinstance(
                func.value,
                ast.Name,
            )
            and func.value.id == "st"
            and func.attr == "caption"
        ):
            caption_index = index
            break

    if caption_index is None:
        raise RuntimeError(
            "render_app: footer caption not found."
        )

    start_node = body[
        import_index
    ]

    end_node = body[
        caption_index
    ]

    replacement = '''    st.caption(
        f"Version: {APP_VERSION} "
        f"| Last modified: {_last_modified_iso(APP_FILE_PATH)} "
        "| State: PostgreSQL relational"
    )
'''

    return replace_lines(
        text,
        start_line=start_node.lineno,
        end_line=end_node.end_lineno,
        replacement=replacement,
    )


def assert_no_qo_sync_execution(
    text: str,
    path: Path,
) -> None:
    tree = parse(
        text,
        path,
    )

    definitions = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name
        == "_sync_qo_placeholders"
    ]

    calls = [
        node
        for node in ast.walk(
            tree
        )
        if isinstance(
            node,
            ast.Call,
        )
        and call_name(node)
        == "_sync_qo_placeholders"
    ]

    if definitions or calls:
        raise RuntimeError(
            "_sync_qo_placeholders executable "
            "code remains: "
            f"definitions={len(definitions)} "
            f"calls={len(calls)}"
        )


def assert_no_local_clock_assignment(
    text: str,
    path: Path,
) -> None:
    function = find_function(
        text,
        path,
        "render_pick_controls",
    )

    problems = []

    for node in ast.walk(
        function
    ):
        targets = []

        if isinstance(
            node,
            ast.Assign,
        ):
            targets.extend(
                node.targets
            )

        elif isinstance(
            node,
            ast.AnnAssign,
        ):
            targets.append(
                node.target
            )

        elif isinstance(
            node,
            ast.AugAssign,
        ):
            targets.append(
                node.target
            )

        for target in targets:
            rendered = ast.unparse(
                target
            )

            if rendered.startswith(
                "state.clock."
            ):
                problems.append(
                    rendered
                )

    if problems:
        raise RuntimeError(
            "render_pick_controls still "
            "mutates local clock: "
            + ", ".join(
                problems
            )
        )


def assert_no_pick_assignment(
    text: str,
    path: Path,
) -> None:
    tree = parse(
        text,
        path,
    )

    problems = []

    for node in ast.walk(
        tree
    ):
        targets = []

        if isinstance(
            node,
            ast.Assign,
        ):
            targets.extend(
                node.targets
            )

        elif isinstance(
            node,
            ast.AnnAssign,
        ):
            targets.append(
                node.target
            )

        elif isinstance(
            node,
            ast.AugAssign,
        ):
            targets.append(
                node.target
            )

        for target in targets:
            for child in ast.walk(
                target
            ):
                if (
                    isinstance(
                        child,
                        ast.Attribute,
                    )
                    and child.attr
                    in {
                        "selected_player_key",
                        "selected_ts_iso",
                    }
                ):
                    problems.append(
                        child.attr
                    )

    if problems:
        raise RuntimeError(
            "app.py still locally mutates "
            "projected pick selection fields: "
            + ", ".join(
                problems
            )
        )


# ================================================================
# init_restore.py
# ================================================================

init = read(
    INIT
)

init = remove_global_autosave_import(
    init,
    INIT,
)

init = add_projection_import(
    init
)

init = add_get_draft_key(
    init,
    INIT,
)

# Remove the old restore path first because it contains one
# historical keeper-prefill call.
init = remove_function(
    init,
    INIT,
    "_restore_mlf_state_from_autosave",
)

# Exactly one fresh-build prefill call should now remain.
init = remove_call_statement(
    init,
    INIT,
    "_apply_mlf_contract_pt_prefill",
)

init = remove_function(
    init,
    INIT,
    "_apply_mlf_contract_pt_prefill",
)

init = replace_once(
    init,
    "        pt_player_team_map=dict(pt_map),\n",
    "        pt_player_team_map={},\n",
    "base PT ownership",
)

init = replace_function(
    init,
    INIT,
    "ensure_initialized",
    '''def ensure_initialized() -> None:
    """
    Initialize Streamlit state from PostgreSQL relational truth.

    Rich team/player presentation data is built first. Draft picks,
    ownership, selections, keeper placeholders, current QOs, pick log,
    draft order, and clock state are projected from the dedicated
    MLF relational model.
    """
    if has_state():
        return

    base = _build_mlf_initial_state()

    projected = load_draft_state_projection(
        dsn=get_postgres_dsn(),
        draft_key=get_draft_key(),
        base_state=base,
    )

    init_state(projected)
''',
)

for forbidden in (
    "draftboard.state.autosave",
    "try_load_autosave",
    "save_autosave",
    "_restore_mlf_state_from_autosave",
    "_apply_mlf_contract_pt_prefill",
):
    if forbidden in init:
        raise RuntimeError(
            f"init_restore.py still contains "
            f"{forbidden!r}"
        )

parse(
    init,
    INIT,
)

write(
    INIT,
    init,
)


# ================================================================
# app.py
# ================================================================

app = read(
    APP
)

app = remove_global_autosave_import(
    app,
    APP,
)

app = remove_call_statement(
    app,
    APP,
    "_sync_qo_placeholders",
)

app = remove_function(
    app,
    APP,
    "_sync_qo_placeholders",
)

app = replace_pick_guard(
    app,
    APP,
)

app = replace_footer(
    app,
    APP,
)

assert_no_qo_sync_execution(
    app,
    APP,
)

for forbidden in (
    "draftboard.state.autosave",
    "try_load_autosave",
    "save_autosave",
    "AUTOSAVE_PATH",
):
    if forbidden in app:
        raise RuntimeError(
            f"app.py still contains "
            f"{forbidden!r}"
        )

assert_no_local_clock_assignment(
    app,
    APP,
)

assert_no_pick_assignment(
    app,
    APP,
)

parse(
    app,
    APP,
)

write(
    APP,
    app,
)


print(
    "UPDATED="
    + INIT.relative_to(
        ROOT
    ).as_posix()
)

print(
    "UPDATED="
    + APP.relative_to(
        ROOT
    ).as_posix()
)

print(
    "EXECUTABLE_QO_SYNC_REFERENCES=ZERO"
)

print(
    "RELATIONAL_STARTUP_CUTOVER_PATCH=PASS"
)
