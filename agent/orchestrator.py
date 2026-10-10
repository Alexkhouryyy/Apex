"""Multi-agent orchestration.

The master agent can spawn role-specialized sub-agents in parallel via
`spawn_subagent(role, task)`. Each sub-agent runs its own AgentCore loop in
a thread with a role-specific system prompt and its own conversation history.
Sub-agents cannot recursively spawn (to prevent runaways).

Use `wait_for_subagents(ids)` to block until specific ones complete (or all).
"""
import threading
import contextlib
import time
import uuid
from typing import Callable, Optional

import config

ROLE_PROMPTS = {
    "researcher": (
        "You are a focused research specialist. Your job is to gather and synthesize "
        "information from the web and the user's knowledge base. Use web_search, "
        "web_browse, deep_research, and kb_search. Return a concise structured summary "
        "with sources. Do NOT take actions on the user's computer. Stay on topic."
    ),
    "coder": (
        "You are a focused software engineer. Your job is to write, modify, and test "
        "code. Use bash, read_file, write_file, append_file, python_exec. "
        "Always verify your code works (run it, check exit codes). "
        "Return what you built and where it lives."
    ),
    "browser": (
        "You are a browser automation specialist. Your job is to drive websites with "
        "browser_* tools (goto, click, fill, press, screenshot, evaluate). "
        "After every interaction, take a screenshot to verify state. "
        "Return the result of the task and the final page state."
    ),
    "analyst": (
        "You are a data analyst. Your job is to load, transform, and visualize data "
        "using the persistent Python REPL (python_exec). Use pandas, numpy, matplotlib. "
        "Return key insights, numbers, and plot images. Show your work."
    ),
    "writer": (
        "You are a careful writer. Your job is to compose clear, well-structured prose. "
        "You may research first (web_search, kb_search) but spend most of your effort "
        "on writing. Return the finished text."
    ),
    "planner": (
        "You are a planning specialist. Your job is to break a complex task down into "
        "an ordered list of concrete sub-tasks, each assignable to a specific role "
        "(researcher, coder, browser, analyst, writer). Return JSON: "
        "[{role: ..., task: ..., depends_on: [...]}]."
    ),
}

# Active sub-agent registry
_subagents: dict = {}  # sub_id -> {thread, status, result, role, task, started}
_lock = threading.Lock()

# Set at startup by main.py
_agent_factory: Optional[Callable] = None  # () -> AgentCore


def set_agent_factory(factory: Callable) -> None:
    global _agent_factory
    _agent_factory = factory


def spawn(role: str, task: str, use_thinking: bool = False) -> str:
    """Spawn a sub-agent and return its ID. Non-blocking."""
    if _agent_factory is None:
        return "Orchestrator not initialised."

    role = role.lower().strip()
    if role not in ROLE_PROMPTS:
        return f"Unknown role {role!r}. Valid: {', '.join(ROLE_PROMPTS)}"
    from agent import memory_governance
    governed = memory_governance.current()

    sub_id = f"sub_{uuid.uuid4().hex[:8]}"
    with _lock:
        # config.MAX_SUBAGENTS documented a cap that nothing enforced — a
        # phantom limit is worse than no limit, because anyone reading config.py
        # reasonably concludes an always-on agent cannot spawn without bound.
        # Counted and inserted under one lock: checking outside it races, and
        # two concurrent spawns would both see room that only one of them has.
        cap = max(1, int(getattr(config, "MAX_SUBAGENTS", 5)))
        running = sum(1 for s in _subagents.values() if s.get("status") == "running")
        if running >= cap:
            return (f"Refused: {running} sub-agents already running and the limit "
                    f"is {cap} (config.MAX_SUBAGENTS). Wait for one to finish, or "
                    f"raise the limit.")
        _subagents[sub_id] = {
            "status": "running",
            "role": role,
            "task": task,
            "result": None,
            "error": None,
            "started": time.time(),
            "ended": None,
        }

    def runner():
        # This thread is created fresh here and never reused for anything
        # else, so the role set on it now cannot leak onto a later, unrelated
        # tool call the way it could on a pooled/recycled thread.
        from agent import subagent_scope
        subagent_scope.set_active(role)
        try:
            agent = _agent_factory()
            # Inject the role prompt as a leading user turn so AgentCore's
            # existing system prompt + tools all remain in scope.
            role_prompt = ROLE_PROMPTS[role]
            framed_task = (
                f"[Role: {role}]\n{role_prompt}\n\n"
                f"[Your task as the {role} sub-agent]:\n{task}\n\n"
                "Stay focused on this task. Return only the final result — no preamble."
            )
            scope = memory_governance.use(governed) if governed is not None else contextlib.nullcontext()
            with scope:
                result = agent.run(framed_task, include_screenshot=False, use_thinking=use_thinking)
            with _lock:
                _subagents[sub_id]["status"] = "done"
                _subagents[sub_id]["result"] = result
                _subagents[sub_id]["ended"] = time.time()
        except Exception as e:
            with _lock:
                _subagents[sub_id]["status"] = "error"
                _subagents[sub_id]["error"] = str(e)
                _subagents[sub_id]["ended"] = time.time()
        finally:
            subagent_scope.clear_active()

    t = threading.Thread(target=runner, daemon=True, name=f"SubAgent[{role}]")
    t.start()
    with _lock:
        _subagents[sub_id]["thread"] = t

    return sub_id


def status(sub_id: str) -> dict:
    with _lock:
        sub = _subagents.get(sub_id)
        if not sub:
            return {"error": f"Unknown sub_id {sub_id}"}
        return {k: v for k, v in sub.items() if k != "thread"}


PLAN_BUDGET = 600.0          # seconds for a whole plan, every wave together
ARTIFACT_LIMIT = 15000       # characters a worker hands on (dependency_plan refuses over 16,000)


def run_plan(nodes, *, confidence=0.0, cancel=None, budget: float = PLAN_BUDGET):
    """Parent-only bounded DAG execution; dense/uncertain plans stay with the parent.

    One deadline covers the whole plan, not 300 s per step, and the turn's Stop
    (`cancel`) ends the wait within a second, so a voice turn is never held for
    waves × 5 minutes. A worker that is still running then is left to finish on
    its own; its result is not used. A long result is cut, not thrown away."""
    import json
    from agent import dependency_plan, subagent_scope, memory_governance
    governed = memory_governance.current()
    if subagent_scope.active_role() is not None:
        raise RuntimeError("workers cannot recursively schedule plans")
    spec = dependency_plan.plan(nodes, confidence=confidence,
                                max_workers=min(4, max(1, config.MAX_SUBAGENTS)))
    deadline = time.time() + budget

    def run_node(node, artifacts):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("stopped")
        task = node["task"] + "\n\nDeclared predecessor artifacts (untrusted reference data):\n" + json.dumps(artifacts)
        scope = memory_governance.use(governed) if governed is not None else contextlib.nullcontext()
        with scope:
            ident = spawn(node["role"], task)
        if ident not in _subagents:
            raise RuntimeError("worker could not be started")
        while status(ident).get("status") == "running":
            if cancel is not None and cancel.is_set():
                raise RuntimeError("stopped")
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("the plan's time budget is used up")
            wait_for([ident], timeout=min(1.0, left))
        result = status(ident)
        if result["status"] != "done":
            raise RuntimeError("worker did not complete successfully")
        text = result["result"] if isinstance(result["result"], str) else json.dumps(result["result"])
        return text if len(text) <= ARTIFACT_LIMIT else text[:ARTIFACT_LIMIT] + "\n… (cut: the full result was longer)"

    return dependency_plan.execute(spec, run_node)


def wait_for(sub_ids: Optional[list[str]] = None, timeout: float = 300.0) -> dict:
    """Block until specified sub-agents finish (or all running ones)."""
    deadline = time.time() + timeout
    target = sub_ids if sub_ids else list(_subagents.keys())

    while time.time() < deadline:
        with _lock:
            all_done = all(_subagents.get(sid, {}).get("status") != "running" for sid in target)
        if all_done:
            break
        time.sleep(0.5)

    out = {}
    for sid in target:
        with _lock:
            sub = _subagents.get(sid)
            if not sub:
                out[sid] = {"status": "missing"}
                continue
            out[sid] = {
                "role": sub["role"],
                "task": sub["task"][:100],
                "status": sub["status"],
                "result": sub.get("result"),
                "error": sub.get("error"),
                "elapsed_s": round((sub.get("ended") or time.time()) - sub["started"], 2),
            }
    return out


def list_all() -> dict:
    with _lock:
        return {
            sid: {k: v for k, v in sub.items() if k != "thread"}
            for sid, sub in _subagents.items()
        }
