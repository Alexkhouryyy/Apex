"""Outcomes Apex saw for itself, as opposed to outcomes it was told about.

`agent/outcomes.py` records what happened after Apex recommended something —
and every row in it arrives because a person or the model *reported* it. That
module is honest about the limit: `coverage()` says outright that "outcomes are
recorded by you, not observed. This ratio is the ceiling on what the accuracy
figure is worth."

The design document asks for the other half (§5.2, "Automatic outcome
signals"): did the generated code run, did the tests pass, did a tool fail or
need retrying, did the user immediately undo the result. Those are things Apex
can *watch* rather than ask about — and a learning loop fed only by
self-report learns mostly about how diligently outcomes get logged.

## Where the evidence comes from

Every tool call already passes through one place — `_execute_tool` in
`agent/core.py` — holding the result text and the real inputs. That is where
this listens, because it is the only point where the evidence still exists:
`agent/trajectory.py`'s table stores a classification and the NAMES of input
keys, deliberately keeping no outputs or values, so the question "did the tests
pass" cannot be answered from it after the fact.

## The rule that keeps this honest

**An observed outcome is never invented.** Every signal here points at a
specific tool event that actually happened, and says which one. Where the
evidence is ambiguous — a command that produced no clear pass/fail — the
answer is `None` (undecided), not a guess. `outcomes.record` already treats
success as tri-state for exactly this reason, and undecided is a legitimate,
frequently-correct answer rather than a failure to classify.

Observed rows carry `source='observed'` so `coverage()` can report the split.
Collapsing the two would be the worst outcome: a number that looks like
evidence while being mostly self-report, or vice versa.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from agent import longterm

# Tools whose success or failure is a real statement about work Apex did,
# rather than about Apex looking something up. Reading a file that does not
# exist is a fact about the filesystem; running a test suite that fails is a
# fact about the work.
_WORK_TOOLS = frozenset({
    "bash", "python_exec", "write_file", "append_file", "run_skill",
    "board_create", "board_recolor", "browser_click", "browser_fill",
})

# Recognizing a test run, and its verdict. Deliberately narrow at both ends.
#
# The command must START with a runner (after the usual wrappers), not merely
# mention one: `cat pytest.ini` contains the word "pytest" and runs no tests,
# and treating it as a test run files a config file's contents as a verdict on
# the code. Found by a test, not by reasoning — the first version of this used
# a bare word-boundary match.
_RUNNER = r"(?:pytest|py\.test|unittest|npm\s+(?:run\s+)?test|yarn\s+test|go\s+test|cargo\s+test)"
_WRAPPER = r"(?:python[0-9.]*\s+-m\s+|poetry\s+run\s+|uv\s+run\s+|npx\s+|pipenv\s+run\s+)*"
_TEST_CMD = re.compile(rf"^\s*{_WRAPPER}{_RUNNER}\b")
_PYTEST_PASS = re.compile(r"\b(\d+) passed\b")
_PYTEST_FAIL = re.compile(r"\b(\d+) (failed|error(?:s|ed)?)\b")


def looks_like_tests(command: str) -> bool:
    """Is this command actually running a test suite?

    Each `&&`/`;`-separated segment is checked on its own, because
    `cd /repo && pytest -q` is a real and common shape — the runner is not at
    the start of the whole line, but it is at the start of the part that runs.
    """
    for segment in re.split(r"&&|\|\||;", (command or "").lower()):
        if _TEST_CMD.match(segment):
            return True
    return False


def test_verdict(output: str) -> tuple[Optional[bool], str]:
    """(passed?, evidence) for a test run's output.

    None when the output does not clearly say. A test command whose output was
    truncated, or that died before running anything, has not told us the tests
    passed — and recording that as a pass would be inventing evidence.
    """
    text = output or ""
    failed = _PYTEST_FAIL.search(text)
    passed = _PYTEST_PASS.search(text)
    if failed and int(failed.group(1)) > 0:
        return False, failed.group(0)
    if passed and int(passed.group(1)) > 0:
        return True, passed.group(0)
    return None, ""


def classify_event(tool: str, outcome: str, result: str,
                   inputs_preview: str = "") -> Optional[dict]:
    """One tool event -> an observed outcome signal, or None to ignore it.

    Returns a dict with `recommendation` (what Apex was doing), `result` (what
    was observed), `success` (tri-state) and `domain`. None means this event
    carries no outcome information worth storing — the common case, and the
    reason this is a filter rather than a transform.
    """
    if tool not in _WORK_TOOLS:
        return None

    # A test run is the strongest automatic signal there is: the work either
    # passed its own check or it did not, and nobody had to be asked.
    if tool == "bash" and looks_like_tests(inputs_preview):
        ok, evidence = test_verdict(result)
        if ok is None:
            return None                  # ran, but said nothing conclusive
        return {
            "recommendation": f"ran the test suite ({inputs_preview[:80]})",
            "result": f"tests {'passed' if ok else 'failed'} — {evidence}",
            "success": ok,
            "domain": "tests",
        }

    if outcome == "error":
        return {
            "recommendation": f"used {tool}",
            "result": f"the tool failed: {result[:160]}",
            "success": False,
            "domain": "tooling",
        }
    if outcome == "blocked":
        # Not a failure of the work — the safety layer doing its job. Recorded
        # as undecided so it shows up as evidence without polluting the
        # success rate in either direction.
        return {
            "recommendation": f"attempted {tool}",
            "result": "refused by the safety layer",
            "success": None,
            "domain": "safety",
        }
    return None


def init_db() -> None:
    """Add the `source` column to the outcome table if it predates this module.

    A migration rather than a new table: observed and reported outcomes are the
    same kind of fact about the same thing, and splitting them across two
    tables would mean every reader had to remember to union them — which is
    exactly how one of them ends up quietly ignored.
    """
    from agent import outcomes as _outcomes
    _outcomes.init_db()
    with longterm._conn() as c:
        cols = {r[1] for r in c.execute(
            "PRAGMA table_info(recommendation_outcomes)").fetchall()}
        if "source" not in cols:
            c.execute("ALTER TABLE recommendation_outcomes "
                      "ADD COLUMN source TEXT DEFAULT 'reported'")


def note_tool_result(tool: str, outcome: str, result: str,
                     inputs: Optional[dict] = None) -> Optional[int]:
    """Observe one finished tool call. Returns the outcome id, or None.

    Called from the one place that has everything needed — `_execute_tool`,
    where the result text and the real inputs are both still in hand.

    This deliberately does NOT read back from `tool_events`: that table stores
    a classification, a duration and the NAMES of the input keys, and no result
    text or input values at all (it redacts on purpose). Reconstructing "did
    the tests pass" from it is impossible, and widening it to store outputs
    would trade a deliberate privacy posture for a convenience this doesn't
    need — the information is right here at the call site.

    Never raises. An observation that fails must not fail the tool call it was
    watching; the whole point is to be a passive witness.
    """
    try:
        command = ""
        if isinstance(inputs, dict):
            command = str(inputs.get("command") or inputs.get("code") or "")
        signal = classify_event(tool, outcome, result or "", command)
        if signal is None:
            return None
        with longterm._conn() as c:
            cur = c.execute(
                "INSERT INTO recommendation_outcomes "
                "(ts, recommendation, action_taken, result, success, domain, source) "
                "VALUES (?,?,?,?,?,?,'observed')",
                (time.time(), signal["recommendation"], f"tool:{tool}",
                 signal["result"],
                 None if signal["success"] is None else int(signal["success"]),
                 signal["domain"]))
            return int(cur.lastrowid)
    except Exception:
        # Silent by design, and the one place in this codebase where that is
        # right: this runs inside every single tool call, so a print here on a
        # broken database would flood the console faster than it could be read.
        # The absence of observed rows is itself the visible symptom, and
        # `split()` reports it.
        return None


def split(days: int = 180) -> dict:
    """How much of the outcome record Apex saw versus was told.

    The number `coverage()` could not give: an accuracy figure built entirely
    from self-report and one built from observation are different claims, and
    a single blended percentage hides which one you have.
    """
    cutoff = time.time() - days * 86400
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT COALESCE(source, 'reported'), COUNT(*) "
                "FROM recommendation_outcomes WHERE ts >= ? GROUP BY 1",
                (cutoff,)).fetchall()
    except Exception:
        return {"observed": 0, "reported": 0, "observed_share": None}
    counts = {str(r[0]): int(r[1]) for r in rows}
    obs, rep = counts.get("observed", 0), counts.get("reported", 0)
    total = obs + rep
    return {
        "days": days,
        "observed": obs,
        "reported": rep,
        "observed_share": round(obs / total, 3) if total else None,
    }
