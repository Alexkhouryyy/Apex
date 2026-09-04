"""The slice of Apex's memory the cloud is allowed to read.

Step 7 of `docs/PHASE_6_7_PLAN.md`, and the place where §3a's decision — the
cloud may ANSWER, it may not be the RECORD — costs something real.

For the relay to answer you while the laptop is shut, it has to be able to read
some memory. There is no version of that which avoids the cost: a box that
cannot read cannot reason. So the question was never whether the server sees
anything. It is **how much**, and this file is the answer.

## The line, and why this one

The cloud gets the working context Apex already builds for itself at every
boot — `longterm.top_memories()` through `format_for_context()`, plus open
goals, today's schedule, and the last few turns of conversation.

That line was not invented for this document. It is the line Apex already draws
when it decides what is worth putting in front of a model, which makes it both
defensible and self-maintaining: improve what Apex considers relevant and the
cloud's view improves with it, with no second definition to keep in step.

## An allowlist of SOURCES, never a filter over everything

`build()` reads from named sources and nothing else. The alternative shape —
gather everything, then strip the dangerous parts — fails the day someone adds
a table, because the new thing is included by default and its absence from the
strip list is invisible.

Never read here at all: the full memory history, the Obsidian vault, documents,
`.env`, tool credentials, the audit tables, the MCP config. Not filtered out —
never fetched. `tests/test_working_context.py` plants a credential in each of
those and asserts none of it reaches the output.

## The part that cannot be solved by scoping, stated rather than hidden

Memories are whatever you told Apex. If you once said "my wifi password is
hunter2", that is a memory, and a memory is exactly what the working context is
made of. Source-allowlisting cannot help: the secret is *inside* the thing we
deliberately send.

So `redact()` removes what is recognisably a credential — API keys, tokens,
private key blocks, "password is ..." — and that is a **mitigation, not a
guarantee**. A secret phrased in a way no pattern matches goes up with the rest.

The only complete answer is not sending memory at all, which is the same as the
cloud not answering. That trade was made deliberately and with its cost stated;
this file is where it is paid, and pretending the redaction closes it would be
worse than the exposure.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import config

# Roughly a page of context. Bounded because this goes up on a timer: an
# unbounded push is a growing bill, a growing thing to read if the box falls,
# and a slow surprise rather than a loud one.
MAX_CHARS = 12000


# Things that are recognisably a credential wherever they appear. Anchored to
# shapes rather than to key names, because the memory that leaks a token will
# not be labelled "token".
# ORDER MATTERS. The phrase pattern goes first, deliberately.
#
# With the shape patterns first, "my token is xoxb-1234..." became
# "my token is [redacted key]", and then the phrase pattern matched
# `token is (\S+)` — capturing only "[redacted" because of the space — and
# produced "my token is [redacted] key]". The secret was gone both ways, but
# mangled output is how a redactor gets distrusted and switched off. Phrase
# first means the shape patterns never see the part already handled.
_PATTERNS: list[tuple] = [
    (re.compile(r"((?:pass(?:word|phrase)|secret|api[ _-]?key|token)\s*"
                r"(?:is|=|:)\s*)(\S+)", re.I), r"\1[redacted]"),
    (re.compile(r"\b(?:xox[baprs]|ghp|github_pat|sk|pk|rk)[-_][A-Za-z0-9_\-]{16,}"),
     "[redacted key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
     "[redacted token]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.S), "[redacted private key]"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+:[^\s:@/]{6,}@[A-Za-z0-9.\-]+\b"),
     "[redacted credential]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}"), "[redacted bearer]"),
]


def redact(text: str) -> str:
    """Remove what is recognisably a credential.

    A mitigation, not a guarantee — see the module docstring. Applied to
    everything on the way out rather than at each source, so a source added
    later is covered without anyone remembering to cover it.
    """
    out = str(text or "")
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _memories(limit: int) -> str:
    from agent import longterm
    return longterm.format_for_context(longterm.top_memories(limit=limit))


def _goals() -> str:
    from agent import goals
    try:
        return goals.active_goals_for_prompt() or ""
    except Exception:
        return ""


def _today() -> str:
    from agent import scheduler
    try:
        tasks = scheduler.list_tasks() or []
    except Exception:
        return ""
    lines = [f"- {t.get('description', '')}" for t in tasks[:10]
             if t.get("description")]
    return "[Scheduled:]\n" + "\n".join(lines) if lines else ""


def _recent_turns(limit: int) -> str:
    from agent import conversations
    try:
        threads = conversations.list_threads(limit=1) or []
        if not threads:
            return ""
        msgs = conversations.messages(threads[0]["id"], limit=limit) or []
    except Exception:
        return ""
    msgs = msgs[-limit:]
    lines = [f"{m.get('role', '?')}: {str(m.get('text', ''))[:400]}" for m in msgs]
    return "[Recent conversation:]\n" + "\n".join(lines) if lines else ""


# The allowlist. Adding a source is a deliberate act with a name; nothing is
# included by being present in the database.
SOURCES = {
    "memories": lambda: _memories(15),
    "goals": _goals,
    "schedule": _today,
    "conversation": lambda: _recent_turns(8),
}


def build(*, max_chars: int = MAX_CHARS) -> dict:
    """The readable slice, redacted and bounded.

    A source that raises contributes nothing and is NAMED in `errors`. Silently
    dropping it would make a broken source and an empty one identical, and the
    cloud would answer confidently from a context missing the half it needed.
    """
    parts, errors = {}, {}
    for name, fn in SOURCES.items():
        try:
            parts[name] = redact(fn() or "")
        except Exception as e:
            parts[name], errors[name] = "", f"{type(e).__name__}: {e}"

    text = "\n\n".join(v for v in parts.values() if v.strip())
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n[...trimmed]"
    return {"built_at": time.time(), "text": text, "chars": len(text),
            "sources": sorted(k for k, v in parts.items() if v.strip()),
            "errors": errors, "truncated": truncated}


# ── what the cloud may do with it ────────────────────────────────────────────
#
# Strictly narrower than the laptop's, and enforced the same way MCP is. The
# cloud can want something to happen; it cannot be the thing that approves it,
# which is what keeps a compromised relay a disclosure problem rather than a
# control problem.

CLOUD_MAY = ("answer", "summarise", "draft", "notify")
CLOUD_MAY_NOT = ("accounts", "files", "shell", "iot", "camera", "mcp")


def cloud_tier() -> dict:
    """The tier, as data, so the relay stores what it is allowed to do rather
    than being trusted to remember."""
    return {"may": list(CLOUD_MAY), "may_not": list(CLOUD_MAY_NOT),
            "note": ("Anything in may_not becomes a task queued for the laptop, "
                     "which runs it through safety.check, mcp_policy.enforce "
                     "and subagent_scope.check at execution time. The queue "
                     "carries a request, never an approval.")}
