"""What a spawned sub-agent is actually allowed to touch.

`agent/orchestrator.py`'s `ROLE_PROMPTS` already tell each sub-agent what it is
for — the researcher's prompt ends "Do NOT take actions on the user's
computer." That sentence was, until this file existed, the ENTIRE enforcement.
Nothing stopped a researcher from calling `bash` anyway; the model just had to
decide not to. A prompt is a request, not a boundary, and this codebase has
spent this whole session finding exactly that shape of bug elsewhere — a
subsystem that looks restricted and isn't.

The other thing the docstring in orchestrator.py claims and nothing enforced:
"Sub-agents cannot recursively spawn (to prevent runaways)." There was no
check anywhere. A sub-agent calling `spawn_subagent` would spawn a real
grandchild, indistinguishable from the parent doing it, up to whatever
`config.MAX_SUBAGENTS` allows concurrently — which bounds fan-out, not depth.

## How the boundary works

Each sub-agent runs on its own, never-reused `threading.Thread` (see
`orchestrator.spawn`), so a `threading.local()` naturally scopes "which role,
if any, is running on the thread asking right now" with no risk of one
sub-agent's restriction leaking onto another's thread, or onto the main
agent's. The main agent, the dashboard's request threads, and the resident
loop never call `set_active`, so `active_role()` is `None` there and `check()`
is a no-op — this file changes nothing about the assistant controlling your
own computer, only about a role-scoped worker it spawned.

`ROLE_TOOLS` is deliberately an allowlist, not a denylist. A denylist has to be
updated every time a new tool is added or it silently permits it; an allowlist
fails safe — a new tool is invisible to every role until someone decides which
ones need it. `spawn_subagent` and `wait_for_subagents` are in NO role's list,
which is what actually enforces the no-recursion promise above.
"""
from __future__ import annotations

import threading
from typing import Optional

# Every entry taken directly from what each ROLE_PROMPTS text in orchestrator.py
# says that role is for — not expanded "to be safe", because a role that can
# reach more than its own prompt claims is the same gap this file exists to
# close. `current_time` is on every list: it is stateless, has no side effect,
# and withholding it would only make every role's outputs harder to date.
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "researcher": frozenset({
        "web_search", "web_browse", "deep_research", "kb_search", "current_time",
    }),
    "coder": frozenset({
        "bash", "read_file", "write_file", "append_file", "list_dir",
        "find_files", "python_exec", "python_reset", "current_time",
    }),
    "browser": frozenset({
        "browser_goto", "browser_click", "browser_fill", "browser_press",
        "browser_get_text", "browser_evaluate", "browser_screenshot",
        "browser_url", "browser_close", "current_time",
    }),
    "analyst": frozenset({
        "python_exec", "python_reset", "read_file", "list_dir", "find_files",
        "kb_search", "current_time",
    }),
    "writer": frozenset({
        "web_search", "kb_search", "current_time",
    }),
    # The planner's prompt asks for a JSON breakdown, not action — "Return
    # JSON: [...]". No tool reaches the outside world for that, so it gets
    # none; a planner that starts calling bash has stopped planning.
    "planner": frozenset(),
}

_local = threading.local()


def set_active(role: str) -> None:
    _local.role = role


def clear_active() -> None:
    _local.role = None


def active_role() -> Optional[str]:
    return getattr(_local, "role", None)


def is_allowed(role: str, tool_name: str) -> bool:
    return tool_name in ROLE_TOOLS.get(role, frozenset())


def check(tool_name: str) -> Optional[str]:
    """None to proceed; a refusal string to return as the tool's result.

    A string, not an exception — every other gate in `_execute_tool_inner`
    (safety.check, budget, the props jail) reports a refusal as the tool's own
    result text, which the model reads and can act on. Raising here would be
    the one gate that crashes the turn instead of telling the model no.
    """
    role = active_role()
    if role is None:
        return None
    if is_allowed(role, tool_name):
        return None
    allowed = ", ".join(sorted(ROLE_TOOLS.get(role, frozenset()))) or "(none)"
    return (f"[Blocked] the '{role}' sub-agent role cannot use '{tool_name}'. "
            f"This role is limited to: {allowed}. Ask the parent agent to do "
            f"this instead, or return your result without it.")
