"""Running work the Core delegated — through the node's own gates, not around them.

Step 6 of `docs/PHASE_6_7_PLAN.md`, and the step every earlier one was
protecting. `agent/node_tasks.py` carries a request and deliberately has no
`approved` column; this is where that decision is cashed in.

## The design decision, and why it is the whole file

Delegated work does **not** get its own execution path. It goes through
`agent.core._execute_tool`, the same function a locally-decided tool call uses,
which already runs `subagent_scope.check` and then `safety.check` before
dispatching anything.

Writing a second executor here would have been easy and slightly tidier, and it
is exactly how two paths drift: one gets a new gate, the other does not, and
nobody notices until the one without it is the one that ran. There is one door,
and delegation queues up at it like everything else.

The consequence worth stating plainly: **a task can be refused, and a refusal is
a normal outcome.** `rm -rf /` arriving from the Core is blocked by
`agent/safety.py` on the machine that would have run it, and the task finishes
as `dead` carrying the refusal — visible, attributable, and not a crash.

## Nobody is at the keyboard

`safety.check` falls back to `input("Proceed? (y/N): ")` when no confirm
function is installed. On a worker thread that is a hang, and on a headless node
it is a permanent one. So the worker installs its own: **no**, announced.

This is the same answer `app/resident.py`'s `resident_confirm` gives, for the
same reason. An action risky enough to need confirming must not happen because
the person it would have asked was not there — and delegation is, by
definition, work arriving while nobody is watching.

## Two gates, not one

`safety.check` is Apex's judgement about the ACTION. `NODE_TASK_TOOLS` is the
operator's judgement about what may arrive over the network at all. They are
different questions: `read_file` is never dangerous enough for safety to stop,
and may still be something you do not want a queue able to trigger.

Deny-by-default, empty by default. A node with the worker enabled and no
allowlist runs nothing and says so at boot, rather than accepting everything
because nobody got round to narrowing it.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

import config
from agent import capabilities, node_tasks

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def allowed_tools() -> set:
    return {t.strip() for t in getattr(config, "NODE_TASK_TOOLS", []) if t.strip()}


def enabled() -> bool:
    return bool(getattr(config, "NODE_WORKER_ENABLED", False))


def _confirm(reason: str) -> bool:
    """Answer safety.check when there is nobody to ask. The answer is no.

    Returned rather than left to `safety`'s `input()` fallback, which would
    block this thread for ever on a machine with no console — and which,
    if some future stdin did answer it, would let an unattended queue talk its
    way past the gate.
    """
    line = (f"[Node] Refused delegated work — nobody is here to approve it: "
            f"{reason}")
    print(line)
    try:
        from agent import notify
        notify.notify(title="Apex refused delegated work",
                      body=str(reason)[:300], kind="safety", priority="high")
    except Exception:
        pass
    return False


def run_one(task: dict) -> tuple:
    """Execute one claimed task. Returns (ok, result_or_error, permanent).

    `permanent` says whether retrying could ever come out differently. A tool
    that is not on the allowlist, a kind nothing knows how to run, a malformed
    payload, an action safety refused — none of those change by being tried
    again in ten seconds. Retrying them spends max_attempts in a tight loop and
    leaves the task recorded as "out of attempts" when the truth was "not
    allowed".

    Every path out of here has gone through `_execute_tool`, or has not run.
    """
    kind = str(task.get("kind") or "")
    payload = task.get("payload") or {}

    if kind != "tool":
        return False, (f"this node does not know how to run '{kind}'. It "
                       f"executes Apex tools; anything else is refused rather "
                       f"than improvised."), True

    name = str(payload.get("name") or "").strip()
    inputs = payload.get("inputs") or {}
    if not name:
        return False, "the task named no tool", True
    if not isinstance(inputs, dict):
        return False, f"inputs must be an object, got {type(inputs).__name__}", True

    allow = allowed_tools()
    if name not in allow:
        return False, (
            f"'{name}' is not in NODE_TASK_TOOLS, so it cannot be triggered "
            f"remotely on this node. This is separate from whether the tool is "
            f"safe: it is about what may arrive over the network at all."
            + (f" Currently allowed: {', '.join(sorted(allow))}." if allow else
               " Nothing is currently allowed.")), True

    # The one door. subagent_scope.check and safety.check both run inside this,
    # before anything is dispatched — which is the entire point of routing
    # delegated work through it rather than executing here.
    from agent import core, safety
    if safety._confirm_fn is None:
        safety.set_confirm_fn(_confirm)
    try:
        result = core._execute_tool(name, inputs)
    except Exception as e:
        # Not permanent: a tool that threw once may work next time.
        return False, f"{type(e).__name__}: {e}", False

    # A refusal comes back as a string, not an exception. Recording it as a
    # failure rather than a success is what makes "it was blocked" visible
    # instead of looking like a tool that returned some text.
    if isinstance(result, str) and result.startswith("[BLOCKED by safety layer]"):
        return False, result, True
    if isinstance(result, str) and result.startswith("[MCP blocked]"):
        return False, result, True
    return True, result, False


def drain_once(node_id: Optional[str] = None, *, lease_seconds: int = 300) -> dict:
    """Claim and run whatever this node can, until the queue has nothing for it."""
    node_id = node_id or capabilities.this_node()
    done = failed = 0
    # A task failed here goes back to `queued`, and the loop would claim it
    # again immediately — and every claim spends an attempt, so a transient
    # failure would exhaust its retry budget in microseconds on retries that had
    # no time to become different. Excluded at the claim, not after it: skipping
    # it afterwards still burns the attempt.
    seen: set = set()
    while True:
        task = node_tasks.claim(node_id, lease_seconds=lease_seconds,
                                exclude=seen)
        if not task:
            break
        seen.add(task["id"])
        ok, out, permanent = run_one(task)
        if ok:
            node_tasks.complete(task["id"], node_id, str(out)[:4000])
            done += 1
            continue
        failed += 1
        if permanent:
            node_tasks.abandon(task["id"], node_id, str(out)[:2000])
        else:
            node_tasks.fail(task["id"], node_id, str(out)[:2000])
        print(f"[Node] Task {task['id']} ({task['kind']}) did not run: {out}")
    return {"node": node_id, "completed": done, "failed": failed}


def start_background() -> str:
    """Start claiming work. Returns the line to print at boot.

    Says what it decided in every case, including the two ways of doing nothing.
    A worker that is enabled with an empty allowlist and a worker that is
    disabled both run zero tasks, and they need different fixes.
    """
    global _thread
    if not enabled():
        return ("[Node] Not accepting delegated work "
                "(NODE_WORKER_ENABLED=false).")
    allow = allowed_tools()
    if not allow:
        return ("[Node] Enabled but NODE_TASK_TOOLS is empty, so every "
                "delegated task will be refused. Name the tools this node may "
                "run remotely.")
    if _thread is not None and _thread.is_alive():
        return "[Node] Already accepting delegated work."

    every = max(2, int(getattr(config, "NODE_WORKER_POLL_SECONDS", 10)))

    def loop():
        while True:
            try:
                drain_once()
            except Exception as e:
                print(f"[Node] worker error: {e}")
            if _stop.wait(timeout=every):
                return

    _stop.clear()
    _thread = threading.Thread(target=loop, daemon=True, name="NodeWorker")
    _thread.start()
    return (f"[Node] Accepting delegated work every {every}s — may run: "
            f"{', '.join(sorted(allow))}.")


def stop_background() -> None:
    _stop.set()
