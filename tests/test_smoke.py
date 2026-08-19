"""Run the smoke suite: boot Apex for real and check its answers are true.

One boot, shared across every check, because a boot costs ~30s. Each check is a
separate test so a failure names the thing that broke rather than "smoke failed".

Marked `slow`. Run just this: pytest tests/test_smoke.py
Skip it:                      pytest -m "not slow"

Validated against the four bugs found on the day it was written — each was
reintroduced and confirmed to turn the suite red:

  clock removed from the prompt   -> prompt_carries_todays_date
  budget_tokens hardcoded         -> no_removed_parameters_are_sent
  EOF returns "" (the 129MB spin) -> headless_stdin_does_not_spin
  SELECT of a missing column      -> no_silent_failures

Two of those four were missed by the first version, and both misses were
instructive: the spin check measured log size, which a 64KB pipe buffer caps, so
a runaway loop looked identical to a healthy run; and the boot did not pass
--think, so the thinking parameter was never built and the check passed over a
code path that never ran. A green check on an unexercised path is the same lie
this suite exists to catch.
"""
from __future__ import annotations

import pytest

from tools import smoke

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def result():
    """One real boot of main.py --text --think against a scripted model."""
    return smoke.boot(
        say="remember that my name is Alex",
        script=[
            {"content": [
                {"type": "text", "text": "Noting that."},
                {"type": "tool_use", "id": "t1", "name": "remember",
                 "input": {"content": "User is Alex", "kind": "fact",
                           "importance": 9}},
            ], "stop_reason": "tool_use"},
            {"content": [{"type": "text", "text": "Saved."}],
             "stop_reason": "end_turn"},
        ],
    )


@pytest.mark.parametrize("check", smoke.CHECKS, ids=lambda c: c.__name__)
def test_smoke_check(check, result):
    finding = check(result)
    assert finding.ok, f"{finding.check}: {finding.detail}"


def test_every_check_ran(result):
    """Guard the guard: a suite whose checks silently stopped being collected
    passes forever."""
    assert len(smoke.CHECKS) >= 10, (
        f"only {len(smoke.CHECKS)} checks registered — the @check decorator has "
        f"probably stopped being applied"
    )
