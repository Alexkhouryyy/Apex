"""Static detection of built-but-not-working code.

Nine times in this codebase a feature has been fully written, plausibly tested,
and simply not running: orphaned WebSocket events, a reward model nothing read,
a cortex that never ticked in resident mode, a scheduler dropping missed fires,
dormant consolidation, five unauthenticated webhooks behind a comment claiming
otherwise, and two `init_db()` functions nobody called.

Every one was found by accident, which is the real finding. Apex fails silently
*by design* — nearly everything is wrapped in fail-open error handling, which is
correct, because an agent that dies on a broken subsystem is worse than one that
degrades. The price is that "never wired up" and "working perfectly" produce
identical observable behaviour.

So each check below is derived from a defect that actually happened, and is
scoped for precision rather than recall. A noisy audit gets muted exactly the
way a noisy agent does, and a muted audit is worth nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _py_sources(include_tests: bool = False) -> dict[Path, str]:
    out = {}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((".git", "venv", ".venv", "build")):
            continue
        if not include_tests and rel.startswith("tests/"):
            continue
        try:
            out[p] = p.read_text()
        except Exception:
            pass
    return out


# --- check 1: init_db defined but never reached -------------------------------
# Caught: restraint (held nothing forever), threads (table never existed).

def _calls_init_db(src: str, mod: str) -> bool:
    """Whether `src` calls `<mod>.init_db()`, under any import alias.

    This codebase aliases pervasively — `from agent import briefing as
    _briefing` — and a naive `\\bbriefing\\b` never matches `_briefing`, because
    an underscore is a word character. That false positive would have reported
    a correctly-wired module as broken, and an audit that cries wolf is one
    people learn to skip, which is the exact failure it exists to prevent.
    """
    names = {mod}
    names |= set(re.findall(rf"import\s+{mod}\s+as\s+(\w+)", src))
    names |= set(re.findall(rf"from\s+agent\s+import\s+{mod}\s+as\s+(\w+)", src))
    return any(re.search(rf"(?<![\w.]){re.escape(n)}\.init_db\(\)", src)
               for n in names)


def orphan_init_db() -> list[str]:
    srcs = _py_sources()
    findings = []
    for path, src in srcs.items():
        if not re.search(r"^def init_db", src, re.M):
            continue
        mod = path.stem
        # Lazy self-initialisation counts as wired: the module creates its own
        # tables on first use regardless of which entry point is live.
        # Word-bounded: a substring match would let any function whose name
        # merely ENDS in _ensure_db() exempt a whole module from the check.
        if re.search(r"(?<![\w])_ensure_db\s*\(", src):
            continue
        called = any(
            _calls_init_db(other, mod)
            for other_path, other in srcs.items() if other_path != path
        )
        if not called:
            findings.append(
                f"{path.relative_to(ROOT)}: init_db() is never called and the "
                f"module has no lazy _ensure_db — its tables will not exist")
    return findings


# --- check 2: config flags nothing reads --------------------------------------
# Caught: MAX_SUBAGENTS, a documented safety cap that nothing enforced.

def dead_config_flags() -> list[str]:
    cfg = (ROOT / "config.py").read_text()
    flags = re.findall(r"^([A-Z][A-Z0-9_]{3,})\s*=", cfg, re.M)
    # Production code only. A flag referenced solely from tests is still dead
    # where it matters — and including tests made this check self-defeating: the
    # allowlist in tests/test_wiring.py quotes the finding text, which contains
    # the flag name, so naming a dead flag was enough to make it look alive.
    haystack = "\n".join(
        s for p, s in _py_sources().items() if p.name != "config.py")
    for ext in ("*.js", "*.html"):
        for p in (ROOT / "dashboard").rglob(ext):
            try:
                haystack += p.read_text()
            except Exception:
                pass
    return [f"config.{f} is defined but never read anywhere"
            for f in flags if f not in haystack]


# --- check 3: WebSocket events nobody listens for -----------------------------
# Caught: live_research broadcast search/reading/writing/done into the void.

def orphan_ws_events() -> list[str]:
    try:
        js = (ROOT / "dashboard/static/app.js").read_text()
    except Exception:
        return []
    findings = []
    for path, src in _py_sources().items():
        # Only real broadcast call sites — scanning every '"type":' in the tree
        # drowns in Anthropic tool-schema keywords like "array" and "tool_use".
        for call in re.findall(r"broadcast_threadsafe\(\s*\{(.{0,200}?)\}", src, re.S):
            for t in re.findall(r'"type":\s*"([a-z][a-z0-9_]*)"', call):
                if t not in js:
                    findings.append(
                        f"{path.relative_to(ROOT)}: broadcasts {t!r} but no "
                        f"frontend handler exists")
    return sorted(set(findings))


# --- check 4: tables written but never read -----------------------------------
# Caught: the reward model's events, threads_surfaced.

def write_only_tables() -> list[str]:
    srcs = _py_sources()
    blob = "\n".join(srcs.values())
    findings = []
    for path, src in srcs.items():
        for table in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", src):
            if not re.search(rf"(?:FROM|JOIN)\s+{table}\b", blob):
                findings.append(
                    f"{path.relative_to(ROOT)}: table {table!r} is created and "
                    f"written but never read back")
    return sorted(set(findings))


CHECKS = {
    "orphan_init_db": orphan_init_db,
    "dead_config_flags": dead_config_flags,
    "orphan_ws_events": orphan_ws_events,
    "write_only_tables": write_only_tables,
}


def run() -> dict[str, list[str]]:
    out = {}
    for name, fn in CHECKS.items():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = [f"check failed: {e}"]
    return out


if __name__ == "__main__":
    total = 0
    for name, findings in run().items():
        print(f"\n=== {name} ({len(findings)}) ===")
        for f in findings:
            print(f"  {f}")
        total += len(findings)
    print(f"\n{total} finding(s).")
