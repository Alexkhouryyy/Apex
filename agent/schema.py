"""Every table Apex has, created from one list that both entry points share.

`main.py` (interactive/TUI) and `app/resident.py` (the always-on daemon) each
kept their OWN hand-maintained sequence of `init_db()` calls, and they had
drifted by twelve modules — all twelve initialised interactively and none of
them in resident mode:

    board, conversations, council_stats, deepresearch, devices, initiative,
    lessons, mcp_policy, observed, outcomes, restraint, scheduler

Resident mode is the always-on one. So on a machine that had only ever run the
daemon, `outcomes`, `lessons`, `restraint` and the rest had no tables, and
several of those modules print-and-continue on a write failure by design —
which turns a missing table into a feature that quietly does nothing. The exact
shape this codebase keeps producing, in the mode that matters most.

It stayed hidden for the same reason `scheduler.init_db` did before CI found
it: any machine that had ever run interactive mode had the tables on disk
already, so the daemon inherited them and looked fine.

`tools/wiring_audit.orphan_init_db` could not catch this. It asks whether
`init_db()` is called from *anywhere*, and one caller satisfies it — "wired
into one of the two entry points" and "wired" are different claims. The fix is
therefore not twelve added lines, which would drift again the next time someone
adds a module; it is one list, plus `tests/test_schema.py` asserting that every
module in `agent/` defining `init_db` appears in it.
"""
from __future__ import annotations

# Ordered roughly by dependency: longterm owns the connection and the sessions
# table, so it goes first. The rest are independent of each other.
INIT_MODULES: tuple[str, ...] = (
    "longterm",
    "access_tokens", "approvals", "board", "briefing", "budget", "compare",
    "conversations", "cortex", "council_stats", "deepresearch", "devices",
    "documents", "feedback", "goals", "initiative", "iot", "knowledge",
    "lessons", "mcp_policy", "observed", "outcomes", "perception", "reranker",
    "restraint", "scheduler", "skill_forge", "threads", "trajectory",
    "vault_index", "verification", "world_model",
)

# Modules whose table-creating function is not called `init_db`. Listed rather
# than special-cased inside the loop so the exception is visible from the list
# itself: `notify` predates the convention and renaming it would migrate a
# table that push subscriptions depend on.
EXTRA: tuple[tuple[str, str], ...] = (
    ("notify", "init_push_table"),
)


def init_all(*, log=print) -> list[str]:
    """Create every table. Returns the names of modules that failed.

    A failure is reported and skipped rather than raised. One subsystem's
    broken migration must not stop Apex booting — but it must also not pass
    silently, which is why the names come back to the caller instead of only
    reaching a log line that scrolls away. `tools/smoke.py`'s
    `core_tables_exist` is the check that actually fails a build over it.
    """
    import importlib

    failed: list[str] = []
    for name, fn_name in [(m, "init_db") for m in INIT_MODULES] + list(EXTRA):
        try:
            mod = importlib.import_module(f"agent.{name}")
            getattr(mod, fn_name)()
        except Exception as e:
            failed.append(name)
            try:
                log(f"[Schema] {name}.{fn_name}() failed: {type(e).__name__}: {e}")
            except Exception:
                pass
    return failed
