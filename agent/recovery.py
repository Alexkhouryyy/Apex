"""Tool self-recovery — turn dead-end failures into actionable next steps.

Apex's tool failures are terminal strings. `read('~/notes/plan.md')` on a typo
returns `Error reading ...: [Errno 2] No such file or directory` and stops. The
model then burns a turn (or three) guessing at the right path. The information
needed to recover — that `plans.md` sits right next to it — was available at the
moment of failure and simply wasn't offered.

This layer post-processes tool results and appends a concrete hint:

  file not found   -> near-miss filenames in the nearest existing directory
  no search hits   -> what IS there, plus a looser pattern to try
  unknown tool     -> the closest real tool name
  huge output      -> spill the full text to a file, return a head + the path

Rules:
  - ADDITIVE ONLY. The original result text is never altered, only appended to,
    so nothing that parses tool output can break.
  - Never raises. A failure in the recovery layer must not fail the tool call.
  - Cheap: filesystem listings and difflib, no model calls.
"""
from __future__ import annotations

import difflib
import os
import time
from typing import Optional

# Results longer than this are spilled to a file instead of flooding the context.
SPILL_THRESHOLD = 8000
SPILL_HEAD = 2000
_SPILL_DIR = os.path.expanduser("~/.apex/tool_output")

_NOT_FOUND_MARKERS = (
    "no such file or directory",
    "error reading",
    "errno 2",
    "filenotfounderror",
)
_NO_MATCH_MARKERS = ("no matches found", "(empty directory)")


def _nearest_existing_dir(path: str) -> Optional[str]:
    """Walk up until we find a directory that exists."""
    p = os.path.dirname(os.path.abspath(os.path.expanduser(path))) or "/"
    for _ in range(6):
        if os.path.isdir(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def suggest_paths(path: str, limit: int = 5) -> list[str]:
    """Near-miss filenames for a path that doesn't exist."""
    try:
        target = os.path.expanduser(path or "")
        base = os.path.basename(target)
        d = _nearest_existing_dir(target)
        if not d or not base:
            return []
        entries = os.listdir(d)
        close = difflib.get_close_matches(base, entries, n=limit, cutoff=0.6)
        # Also catch substring matches difflib's ratio misses (plan -> my_plan_v2.md)
        stem = os.path.splitext(base)[0].lower()
        if len(stem) >= 3:
            for e in entries:
                if stem in e.lower() and e not in close:
                    close.append(e)
        return [os.path.join(d, c) for c in close[:limit]]
    except Exception:
        return []


def suggest_tool(name: str, known: list[str], limit: int = 3) -> list[str]:
    try:
        return difflib.get_close_matches(name or "", known or [], n=limit, cutoff=0.5)
    except Exception:
        return []


def _known_tools() -> list[str]:
    try:
        from agent import core
        names = [t.get("name", "") for t in getattr(core, "TOOLS", []) if t.get("name")]
        return [n for n in names if n]
    except Exception:
        return []


def spill(text: str, tool: str = "tool") -> Optional[str]:
    """Write oversized output to a file; return its path (or None)."""
    try:
        os.makedirs(_SPILL_DIR, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tool)[:40]
        path = os.path.join(_SPILL_DIR, f"{int(time.time()*1000)}-{safe}.txt")
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
        return path
    except Exception:
        return None


def _first_path_input(inputs: dict) -> str:
    for k in ("path", "file", "filename", "filepath", "target"):
        v = (inputs or {}).get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def enrich(tool: str, inputs: dict, result: str) -> str:
    """Append a recovery hint to a failed/oversized result. Never raises."""
    try:
        if not isinstance(result, str):
            return result
        low = result[:400].lower()

        # 1. Hallucinated tool -> closest real name.
        if result.startswith("Unknown tool:"):
            near = suggest_tool(tool, _known_tools())
            if near:
                return result + f"\n[recovery] Closest available tools: {', '.join(near)}."
            return result

        # 2. File not found -> near-miss filenames.
        if any(m in low for m in _NOT_FOUND_MARKERS):
            p = _first_path_input(inputs)
            near = suggest_paths(p)
            if near:
                return result + "\n[recovery] Did you mean:\n  " + "\n  ".join(near)
            d = _nearest_existing_dir(p) if p else None
            if d:
                return result + (f"\n[recovery] No similar name in {d}. "
                                 f"List it to see what's actually there.")
            return result

        # 3. Empty search -> show what IS there and a looser pattern.
        if any(m in low for m in _NO_MATCH_MARKERS):
            base = (inputs or {}).get("base") or (inputs or {}).get("path") or "."
            pattern = str((inputs or {}).get("pattern") or "")
            hint = []
            try:
                d = os.path.expanduser(str(base))
                if os.path.isdir(d):
                    entries = sorted(os.listdir(d))[:12]
                    if entries:
                        hint.append("Present in " + d + ": " + ", ".join(entries))
            except Exception:
                pass
            if pattern and not pattern.startswith("*"):
                hint.append(f"Try a looser pattern: *{pattern.strip('*')}*")
            if hint:
                return result + "\n[recovery] " + " | ".join(hint)
            return result

        # 4. Oversized output -> spill to a file, keep a head in context.
        if len(result) > SPILL_THRESHOLD:
            path = spill(result, tool)
            if path:
                return (result[:SPILL_HEAD]
                        + f"\n\n…[truncated {len(result) - SPILL_HEAD} chars]\n"
                        + f"[recovery] Full output saved to {path} — read that file "
                          f"(or grep it) instead of re-running this tool.")
        return result
    except Exception:
        return result
