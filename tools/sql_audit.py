"""Check that SQL in the source only names columns the schema actually has.

`agent/reflection.py` ran

    SELECT name, kind, properties FROM entities

for as long as it existed. The column is `properties_json`. Every call raised
OperationalError, the caller caught it, printed one line, and moved on — so the
profile digest never produced a file on any machine, and nothing said so louder
than a log line among fifty others.

That is the ninth thing in this codebase built and never run, and unlike the
others this class is mechanically detectable: build the schema, read the SQL,
compare. The wiring audit finds code nothing calls; this finds code that runs
and cannot work.

Deliberately conservative. Only single-table SELECTs with a plain identifier
list are checked; anything with a join, alias, subquery, function call or `*` is
counted as **unchecked** and reported as such. A tool that quietly skipped the
hard queries while implying full coverage would be its own version of this bug.
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Fixed at import and never reassigned. REPO is monkeypatched by tests to point
# the scanner at planted files; the schema probe must still find the real
# codebase.
_REPO_ROOT = REPO
SEARCH_DIRS = ("agent", "tools", "dashboard", "app", "skills")

# The probe spawns a subprocess, so cache it — audit() may be called repeatedly
# in a single test run.
_SCHEMA_CACHE: dict[str, set[str]] | None = None

# SELECT <cols> FROM <table>, stopping at the table name. Anything more
# elaborate than that is handled by being rejected below, not parsed.
_SELECT_RE = re.compile(
    r"SELECT\s+(?P<cols>.+?)\s+FROM\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.DOTALL,
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Reasons a query is left unchecked rather than guessed at.
_TOO_COMPLEX = ("*", "(", ")", " as ", " join ", "||", " case ", " select ",
                "distinct ", "{", "}")


def build_schema() -> dict[str, set[str]]:
    """Create every table Apex knows how to create, then read the columns back.

    Uses a throwaway database file so this never touches a real one.
    """
    import json
    import subprocess
    import sys

    # In a subprocess, deliberately.
    #
    # Building the schema means calling every module's init_db() against a
    # throwaway database. Several of those modules latch a module-level "already
    # initialised" flag on first call — so doing it in-process marked them
    # initialised against the temp file, and they then skipped creating their
    # tables in the real one. In the test suite that surfaced as an unrelated
    # TUI test dying on "no such table: budget_config", and only when the two
    # ran together.
    #
    # Tracking down and restoring each flag would be a list that goes stale the
    # moment someone adds a module. A separate process cannot leak state at all.
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE

    tmp = Path(tempfile.mkdtemp()) / "schema_probe.db"
    probe = subprocess.run(
        # _REPO_ROOT, not REPO: tests point REPO at a temp directory to scan
        # planted files, and the probe still has to find the real agent package.
        [sys.executable, "-c", _PROBE_SRC, str(tmp)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=180,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            f"schema probe failed: {probe.stderr.strip()[-500:] or 'no output'}")
    _SCHEMA_CACHE = {t: set(cols) for t, cols in json.loads(probe.stdout).items()}
    return _SCHEMA_CACHE


# Runs in the subprocess above. Keep it self-contained.
_PROBE_SRC = r"""
import importlib, json, os, pathlib, sqlite3, sys
db = sys.argv[1]
os.environ["DB_PATH"] = db
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-schema-probe")
sys.path.insert(0, os.getcwd())
from agent import longterm
longterm.DB_PATH = db
longterm.init_db()
for path in sorted(pathlib.Path("agent").glob("*.py")):
    if path.stem.startswith("_"):
        continue
    text = path.read_text(errors="replace")
    if "def init_db" not in text and "def init_push_table" not in text:
        continue
    try:
        mod = importlib.import_module("agent." + path.stem)
        fn = getattr(mod, "init_db", None) or getattr(mod, "init_push_table", None)
        if callable(fn):
            fn()
    except Exception:
        # A module that cannot initialise here (missing optional dependency)
        # contributes no tables; its queries are simply left unchecked.
        pass
out = {}
with sqlite3.connect(db) as c:
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        out[t] = [r[1] for r in c.execute('PRAGMA table_info("%s")' % t)]
print(json.dumps(out))
"""


def _modules_with_init_db() -> list[str]:
    out = []
    for path in (REPO / "agent").glob("*.py"):
        if path.stem.startswith("_"):
            continue
        text = path.read_text(errors="replace")
        if "def init_db" in text or "def init_push_table" in text:
            out.append(f"agent.{path.stem}")
    return out


_SELF = Path(__file__).resolve()


def _sources():
    for d in SEARCH_DIRS:
        base = REPO / d
        if base.is_dir():
            for path in base.rglob("*.py"):
                # This file quotes the broken query it exists to catch. The
                # wiring audit learned the same lesson: a scanner that reads its
                # own explanation reports itself.
                if path.resolve() != _SELF:
                    yield path


def _sql_strings(path: Path):
    """Yield (source_line, string) for every string literal in the file.

    Scanning raw file text instead cost this tool the very bug it was written
    for: a code comment reading "so select what is actually used" matched the
    SELECT pattern, and because finditer does not produce overlapping matches,
    that prose swallowed the real query on the next line. Only string literals
    can be SQL, so only string literals are read.
    """
    import ast
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, UnicodeDecodeError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def audit() -> dict:
    """Return {'bad': [...], 'checked': n, 'unchecked': n}."""
    schema = build_schema()
    bad, checked, unchecked = [], 0, 0

    for path in _sources():
        for lineno, text in _sql_strings(path):
            if "select" not in text.lower():
                continue
            for m in _SELECT_RE.finditer(text):
                table = m.group("table")
                if table not in schema:
                    unchecked += 1      # a temp table, a CTE, or a table we
                    continue            # could not create here
                cols_src = m.group("cols")
                low = cols_src.lower()
                if any(tok in low for tok in _TOO_COMPLEX):
                    unchecked += 1
                    continue
                names = [c.strip() for c in cols_src.split(",")]
                if not all(_IDENT_RE.match(n) for n in names):
                    unchecked += 1
                    continue
                checked += 1
                line = lineno + text[:m.start()].count("\n")
                for col in names:
                    if col not in schema[table]:
                        bad.append({
                            "file": str(path.relative_to(REPO)),
                            "line": line,
                            "table": table,
                            "column": col,
                            "did_you_mean": _closest(col, schema[table]),
                        })
    return {"bad": bad, "checked": checked, "unchecked": unchecked,
            "tables": len(schema)}


def _closest(col: str, options: set[str]) -> str:
    """Best guess at the intended column, for the error message."""
    import difflib
    hit = difflib.get_close_matches(col, sorted(options), n=1, cutoff=0.6)
    return hit[0] if hit else ""


def print_audit() -> None:
    r = audit()
    print(f"[SQL] {r['checked']} simple SELECTs checked against {r['tables']} "
          f"tables; {r['unchecked']} too complex to check")
    for b in r["bad"]:
        hint = f" (did you mean {b['did_you_mean']}?)" if b["did_you_mean"] else ""
        print(f"  ✗ {b['file']}:{b['line']} — {b['table']}.{b['column']} "
              f"does not exist{hint}")
    if not r["bad"]:
        print("  ✓ no query names a column its table does not have")


if __name__ == "__main__":
    print_audit()
