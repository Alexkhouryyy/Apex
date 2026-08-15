"""What can Apex do while nobody is watching?

tools/wiring_audit.py finds code that never runs. Nothing found code that starts
running *more* than it used to — and enabling a dormant path is functionally
identical to writing new code. That gap cost something real: switching on the
consolidation heartbeat also switched on reflection.refine_skills(), which had a
model rewrite Apex's executable skills and installed them unreviewed. Nobody
asked what the new cadence could reach, because nothing made anyone ask.

So this maps reachability from AUTONOMOUS_ENTRIES — code that runs with no human
present — to SINKS, the capabilities worth knowing about. The output is the
answer to "what can this thing do while I'm asleep", in one place.

Two deliberate design choices:

- **Coarse and over-reporting.** The call graph is name-based, so two different
  `run` functions collapse into one node and reachability ignores every guard
  and conditional. It answers "what CAN be reached", never "what will run". For
  a safety inventory that is the correct direction of error: a false alarm costs
  a sentence of explanation, a miss costs what the heartbeat bug cost.
- **It never decides whether a path is safe.** Each edge carries a human-written
  disposition in tests/test_autonomy.py. A tool that inferred "this one's fine"
  would be exactly the confident wrongness it exists to prevent.
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

from tools.wiring_audit import ROOT, _py_sources

# Code that runs with no human in the loop. Adding one here without adding its
# edges to the disposition table is what should fail the build.
AUTONOMOUS_ENTRIES = {
    "_review_loop": "awareness monitor, every 15s",
    "tick": "autonomous cortex, every 5min",
    "check": "guardian angel, every 15s",
    "consolidate_if_due": "memory consolidation, every 6h",
    "refine_skills": "model rewrites failing skills, on the consolidation pass",
    "propose": "initiative proposes goals, on the consolidation pass",
    "run_skill": "skill execution — reachable from every autonomous path above",
}

# Capabilities worth knowing an unattended path can reach.
SINKS = {
    "run_shell": "executes shell commands",
    "create_skill": "writes executable code Apex will later run",
    "write_text": "writes files",
    "notify": "interrupts the user",
    "complete": "spends money on model calls",
    "set_goal": "creates goals that direct future work",
    "stage": "stages something for human approval",
}

_MAX_DEPTH = 8


def build_graph() -> dict[str, set[str]]:
    """Coarse name-based call graph over agent/ and tools/."""
    graph: dict[str, set[str]] = collections.defaultdict(set)
    for path, src in _py_sources().items():
        # Never raise on a path outside ROOT. A crashing auditor is its own
        # instance of the silent-failure problem this exists to surface.
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if not (rel.startswith("agent/") or rel.startswith("tools/")):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                fn = call.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name:
                    graph[node.name].add(name)
    return graph


def _reachable(graph, start: str, seen=None, depth: int = 0) -> set[str]:
    if seen is None:
        seen = set()
    if start in seen or depth > _MAX_DEPTH:
        return set()
    seen.add(start)
    hits = set()
    for callee in graph.get(start, ()):
        if callee in SINKS:
            hits.add(callee)
        hits |= _reachable(graph, callee, seen, depth + 1)
    return hits


def edges() -> set[tuple[str, str]]:
    """Every (autonomous entry, capability) pair currently reachable."""
    graph = build_graph()
    out = set()
    for entry in AUTONOMOUS_ENTRIES:
        if entry not in graph:
            continue
        for sink in _reachable(graph, entry):
            out.add((entry, sink))
    return out


def report() -> str:
    graph = build_graph()
    lines = ["What Apex can reach with no human present", ""]
    for entry, when in sorted(AUTONOMOUS_ENTRIES.items()):
        if entry not in graph:
            lines.append(f"{entry:20s} — not found in the call graph")
            continue
        hits = sorted(_reachable(graph, entry))
        lines.append(f"{entry} ({when})")
        for h in hits:
            lines.append(f"    -> {h:14s} {SINKS[h]}")
        if not hits:
            lines.append("    -> (nothing notable)")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
    print(f"{len(edges())} (entry -> capability) edge(s).")
