import os
import re
import ast
from pathlib import Path

ROOT = Path(os.environ.get("MLF_ROOT", "/app")).resolve()
SCRIPTS_DIR = ROOT / "scripts"
OUT_DIR = ROOT / "curated" / "inventory"

ENV_RE = re.compile(r'os\.environ\.get\(\s*[\'"]([^\'"]+)[\'"]')
ENV_BRACKET_RE = re.compile(r'os\.environ\[\s*[\'"]([^\'"]+)[\'"]\s*\]')

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def top_docstring(tree: ast.AST):
    return ast.get_docstring(tree)

def imported_modules(tree: ast.AST):
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                mods.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    return sorted(mods)

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in sorted(SCRIPTS_DIR.glob("*.py")):
        txt = read_text(p)
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            rows.append((p.name, "SYNTAX_ERROR", "", "", ""))
            continue

        doc = (top_docstring(tree) or "").strip().replace("\n", " ")
        mods = ",".join(imported_modules(tree))

        envs = set(ENV_RE.findall(txt)) | set(ENV_BRACKET_RE.findall(txt))
        envs_s = ",".join(sorted(envs))

        # crude "entrypoint" detection
        has_main = ("if __name__" in txt) or ("def main" in txt)

        rows.append((p.name, "OK", "YES" if has_main else "NO", envs_s, mods, doc[:200]))

    out = OUT_DIR / "script_inventory.tsv"
    out.write_text(
        "script\tstatus\thas_main\tenv_vars\timports\tdocstring\n" +
        "\n".join(["\t".join(r) for r in rows]) + "\n",
        encoding="utf-8"
    )

    print("Wrote:", out.as_posix())
    print("Scripts:", len(rows))

if __name__ == "__main__":
    main()
