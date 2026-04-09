#!/usr/bin/env python3
"""
probe_repo_tree.py

Print a clean, stable tree (folders + files) under a root path to a max depth.

Defaults:
  root: /app
  depth: 4

Usage:
  python scripts/probe_repo_tree.py
  python scripts/probe_repo_tree.py --root /app --depth 4
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable


DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vscode",
}
DEFAULT_EXCLUDE_FILES = {
    ".DS_Store",
    "Thumbs.db",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="/app", help="Root directory to print (default: /app)")
    p.add_argument("--depth", type=int, default=4, help="Max depth from root (default: 4)")
    p.add_argument(
        "--show-hidden",
        action="store_true",
        help="Include dotfiles/dotdirs (default: hidden excluded unless explicitly allowed)",
    )
    return p.parse_args()


def is_hidden(p: Path) -> bool:
    return p.name.startswith(".")


def should_skip_dir(p: Path, show_hidden: bool) -> bool:
    if p.name in DEFAULT_EXCLUDE_DIRS:
        return True
    if not show_hidden and is_hidden(p):
        return True
    return False


def should_skip_file(p: Path, show_hidden: bool) -> bool:
    if p.name in DEFAULT_EXCLUDE_FILES:
        return True
    if not show_hidden and is_hidden(p):
        return True
    return False


def iter_children_sorted(dir_path: Path) -> tuple[list[Path], list[Path]]:
    try:
        children = list(dir_path.iterdir())
    except PermissionError:
        return ([], [])

    dirs = [c for c in children if c.is_dir()]
    files = [c for c in children if c.is_file()]

    # Stable sort: dirs then files, alphabetical (case-insensitive, then case-sensitive)
    key = lambda x: (x.name.lower(), x.name)
    dirs.sort(key=key)
    files.sort(key=key)
    return (dirs, files)


def print_tree(root: Path, max_depth: int, show_hidden: bool) -> int:
    root = root.resolve()
    if not root.exists():
        raise SystemExit(f"ERROR: root does not exist: {root}")
    if not root.is_dir():
        raise SystemExit(f"ERROR: root is not a directory: {root}")
    if max_depth < 0:
        raise SystemExit("ERROR: --depth must be >= 0")

    print(str(root))
    printed = 0

    def _walk(dir_path: Path, depth: int, prefix: str) -> None:
        nonlocal printed
        if depth >= max_depth:
            return

        dirs, files = iter_children_sorted(dir_path)

        # Filter
        dirs = [d for d in dirs if not should_skip_dir(d, show_hidden)]
        files = [f for f in files if not should_skip_file(f, show_hidden)]

        entries: list[Path] = dirs + files
        for i, entry in enumerate(entries):
            last = i == (len(entries) - 1)
            branch = "└── " if last else "├── "
            print(prefix + branch + entry.name)
            printed += 1
            if entry.is_dir():
                extension = "    " if last else "│   "
                _walk(entry, depth + 1, prefix + extension)

    _walk(root, depth=0, prefix="")
    return printed


def main() -> None:
    args = parse_args()
    total = print_tree(Path(args.root), args.depth, args.show_hidden)
    print(f"\n(items printed: {total})")


if __name__ == "__main__":
    main()
