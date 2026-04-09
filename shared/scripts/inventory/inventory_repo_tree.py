import os
from pathlib import Path

ROOT = Path(os.environ.get("MLF_ROOT", "/app")).resolve()
OUT_DIR = ROOT / "curated" / "inventory"
MAX_DEPTH = int(os.environ.get("MLF_TREE_DEPTH", "4"))

SKIP_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache",
    "node_modules",
    # big/noisy
    "runtime/postgres/pgdata",
}

def is_skipped_dir(p: Path) -> bool:
    parts = set(p.parts)
    return any(sd in parts for sd in SKIP_DIRS)

def walk_tree(root: Path, max_depth: int):
    rows = []
    root_parts = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)

        if is_skipped_dir(dpath):
            dirnames[:] = []
            continue

        depth = len(dpath.parts) - root_parts
        if depth > max_depth:
            dirnames[:] = []
            continue

        dirnames[:] = sorted(dirnames)
        filenames = sorted(filenames)

        rel_dir = dpath.relative_to(root).as_posix() if dpath != root else "."
        rows.append((rel_dir, "DIR", ""))

        for fn in filenames:
            fpath = dpath / fn
            rel = fpath.relative_to(root).as_posix()
            try:
                size = fpath.stat().st_size
            except OSError:
                size = -1
            rows.append((rel, "FILE", str(size)))
    return rows

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = walk_tree(ROOT, MAX_DEPTH)

    out = OUT_DIR / "repo_tree_depth4.tsv"
    out.write_text("path\ttype\tsize_bytes\n" + "\n".join(["\t".join(r) for r in rows]) + "\n", encoding="utf-8")

    print("Wrote:", out.as_posix())
    print("Rows:", len(rows))
    print("Root:", ROOT.as_posix())
    print("Depth:", MAX_DEPTH)

if __name__ == "__main__":
    main()
