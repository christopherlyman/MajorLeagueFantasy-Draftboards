import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("MLF_ROOT", "/app")).resolve()
SCRIPTS_DIR = ROOT / "scripts"
OUT_DIR = ROOT / "curated" / "inventory"

# crude patterns
IMPORT_SCRIPTS_RE = re.compile(r'from\s+(scripts\.)?([a-zA-Z0-9_]+)\s+import|import\s+(scripts\.)?([a-zA-Z0-9_]+)')
OPEN_RE = re.compile(r'open\(\s*[\'"]([^\'"]+)[\'"]')
PATH_WRITE_RE = re.compile(r'\.write_text\(\s*|\.write_bytes\(\s*')
RAW_HINT_RE = re.compile(r'raw/[^\s\'"]+')
RUNTIME_HINT_RE = re.compile(r'runtime/[^\s\'"]+')

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    edges = []  # (script, dep_script)
    file_reads = []  # (script, path)
    file_writes = []  # (script, hint)

    for p in sorted(SCRIPTS_DIR.glob("*.py")):
        txt = read_text(p)

        # internal deps
        for m in IMPORT_SCRIPTS_RE.findall(txt):
            mod = m[1] or m[3]
            if mod and mod != p.stem:
                edges.append((p.name, f"{mod}.py"))

        # file reads
        for fp in OPEN_RE.findall(txt):
            file_reads.append((p.name, fp))

        # file hints (raw/curated)
        for hint in RAW_HINT_RE.findall(txt):
            file_writes.append((p.name, hint))
        for hint in CURATED_HINT_RE.findall(txt):
            file_writes.append((p.name, hint))

        # add a generic write marker if present
        if PATH_WRITE_RE.search(txt):
            file_writes.append((p.name, "[write_text/write_bytes used]"))

    out_edges = OUT_DIR / "dependency_edges.tsv"
    out_edges.write_text(
        "script\tdepends_on\n" + "\n".join(["\t".join(e) for e in sorted(set(edges))]) + "\n",
        encoding="utf-8"
    )

    out_reads = OUT_DIR / "file_reads.tsv"
    out_reads.write_text(
        "script\treads_path_literal\n" + "\n".join(["\t".join(r) for r in file_reads]) + "\n",
        encoding="utf-8"
    )

    out_writes = OUT_DIR / "file_hints.tsv"
    out_writes.write_text(
        "script\twrites_or_mentions\n" + "\n".join(["\t".join(w) for w in file_writes]) + "\n",
        encoding="utf-8"
    )

    print("Wrote:", out_edges.as_posix())
    print("Wrote:", out_reads.as_posix())
    print("Wrote:", out_writes.as_posix())

if __name__ == "__main__":
    main()
