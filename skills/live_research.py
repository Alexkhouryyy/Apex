"""Skill: live_research — cited research for voice and chat.

A thin surface over agent.answers: search → parallel fetch → passage rerank →
synthesis with numbered sources → citation validation. Progress is broadcast to
the dashboard so the Research tab lights up regardless of which surface started
the run.

This used to run its own pipeline, which truncated each source to 3000 chars and
then capped the concatenation at 14000 — so at depth='deep' sources 5-10 never
reached the model while the UI reported reading all ten. Passage selection
replaces blind truncation, so that class of silent loss is gone.

Trusted, hand-written skill.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

DESCRIPTION = (
    "Research a topic thoroughly: searches the web, reads top sources, and writes "
    "a cited Markdown report saved to ~/Documents/Apex/Research/. Every factual "
    "claim carries a [n] marker resolving to a source that was actually fetched. "
    "Pass {query, depth} where depth is 'quick' (3 sources), 'standard' (6), or 'deep' (10)."
)
VERSION = "2.0"
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Research topic or question.",
        },
        "depth": {
            "type": "string",
            "enum": ["quick", "standard", "deep"],
            "description": "quick=3 sources, standard=6, deep=10.",
            "default": "standard",
        },
    },
    "required": ["query"],
}

def _broadcast(phase: str, payload: dict) -> None:
    """Emit the same event shape the Research tab consumes, so a run started by
    voice or chat renders live in the dashboard too."""
    try:
        from dashboard import server as _srv
        _srv.ws_manager.broadcast_threadsafe(
            {"type": f"research_{phase}", "phase": phase, "ts": time.time(), **payload}
        )
    except Exception:
        pass


def _research_dir() -> Path:
    d = Path.home() / "Documents" / "Apex" / "Research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(query: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", query.lower().strip())[:60].strip("-") or "research"


def run(inputs: dict) -> str:
    query = (inputs.get("query") or "").strip()
    if not query:
        return "live_research: 'query' is required."
    depth = inputs.get("depth", "standard")

    from agent import answers

    result = answers.answer(query, depth=depth, on_event=_broadcast)
    if result.get("error"):
        return f"live_research: {result['error']}"

    report = answers.format_markdown(result)

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = _research_dir() / f"{ts}-{_slug(query)}.md"
    try:
        out_path.write_text(f"# Research: {query}\n_Generated {ts}_\n\n{report}", encoding="utf-8")
    except Exception as e:
        return f"live_research: report save failed: {e}\n\n{report[:600]}"

    _broadcast("saved", {"path": str(out_path)})
    return f"Research complete. Report saved to {out_path}.\n\n{report}"
