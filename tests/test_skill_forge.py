"""Skill Forge writes executable code. Nothing tested it.

`agent/skill_forge.py` is 397 lines wired into main.py, app/resident.py, the
autonomous cortex (`agent/awareness.py`), the constellation, and two dashboard
endpoints. It asks a model to write a Python tool, validates it, and can register
it as something Apex will later run. No test referenced this module at all — it
was the largest UNPROVEN row in docs/APEX_GAP_ANALYSIS.md, and the only one in
the autonomy path.

The load-bearing property is the one `skill_forge.py:234` describes: the
model-declared `is_read_only` flag is **not trusted**, because it was never
verified against the code, so an injected `"is_read_only": true` could otherwise
ship host-executing tools unattended. That is a prompt-injection defence with no
test behind it, which is the same as no defence at all once someone refactors.
"""
from __future__ import annotations

import json

import pytest

from agent import skill_forge


# ── A model that returns whatever proposal a test wants ───────────────────────

class _Block:
    type = "text"

    def __init__(self, t):
        self.text = t


class _Usage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Resp:
    def __init__(self, t):
        self.content = [_Block(t)]
        self.usage = _Usage()
        self.stop_reason = "end_turn"


class FakeClient:
    def __init__(self, proposal: dict | str):
        self.payload = proposal if isinstance(proposal, str) else json.dumps(proposal)
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return _Resp(self.payload)


GOOD = {
    "tool_name": "count_words",
    "description": "Counts words in a string.",
    "code": "def run(inputs: dict) -> str:\n    return str(len(inputs.get('text','').split()))",
    "test_case": {"text": "one two three"},
    "is_read_only": True,
}


@pytest.fixture
def forge_db(test_db, monkeypatch):
    skill_forge.init_db()
    from agent import approvals
    approvals.init_db()
    # Docker is not available here, and _validate_in_sandbox correctly refuses
    # without it (see test_validation_fails_closed_without_docker). Stub it so
    # the other tests can exercise the logic *after* validation.
    monkeypatch.setattr(skill_forge, "_validate_in_sandbox",
                        lambda code, inputs: (True, "ok"))
    return test_db


# ── The property that matters ─────────────────────────────────────────────────

def test_a_forged_tool_is_never_auto_approved(forge_db):
    """Even when the model declares it read-only.

    This is the injected-flag case skill_forge.py:234 exists to stop.
    """
    out = skill_forge.attempt_forge(FakeClient(GOOD), "count words")
    assert out is not None, "a valid proposal should stage"
    assert out["auto_approved"] is False, (
        "a model-declared is_read_only flag was honoured — an injected "
        "'is_read_only: true' could ship host-executing code unattended"
    )
    rows = skill_forge.list_forged()
    assert [r["status"] for r in rows] == ["pending"]


def test_forging_does_not_register_the_tool(forge_db, monkeypatch):
    """Staging must not reach self_mod. Registration happens only on approval."""
    registered = []
    from agent import self_mod
    monkeypatch.setattr(self_mod, "register_new_tool",
                        lambda **kw: registered.append(kw) or "registered")
    skill_forge.attempt_forge(FakeClient(GOOD), "count words")
    assert not registered, "a forged tool reached self_mod without approval"


def test_approval_is_what_registers_it(forge_db, monkeypatch):
    registered = []
    from agent import self_mod
    monkeypatch.setattr(self_mod, "register_new_tool",
                        lambda **kw: registered.append(kw) or "registered")

    out = skill_forge.attempt_forge(FakeClient(GOOD), "count words")
    skill_forge.approve_forged(out["id"])

    assert len(registered) == 1
    assert registered[0]["name"] == "count_words"
    assert skill_forge.list_forged()[0]["status"] == "approved"


def test_approving_twice_does_not_register_twice(forge_db, monkeypatch):
    registered = []
    from agent import self_mod
    monkeypatch.setattr(self_mod, "register_new_tool",
                        lambda **kw: registered.append(kw) or "registered")

    out = skill_forge.attempt_forge(FakeClient(GOOD), "count words")
    skill_forge.approve_forged(out["id"])
    second = skill_forge.approve_forged(out["id"])

    assert len(registered) == 1, "an approved tool was registered again"
    assert "not found or not pending" in second


def test_rejecting_leaves_it_unregistered(forge_db, monkeypatch):
    registered = []
    from agent import self_mod
    monkeypatch.setattr(self_mod, "register_new_tool",
                        lambda **kw: registered.append(kw) or "registered")

    out = skill_forge.attempt_forge(FakeClient(GOOD), "count words")
    skill_forge.reject_forged(out["id"])
    assert skill_forge.list_forged()[0]["status"] == "rejected"
    assert skill_forge.approve_forged(out["id"]).startswith("Tool #")
    assert not registered


# ── Nothing unvalidated may be staged ─────────────────────────────────────────

def test_code_that_fails_its_own_test_is_not_staged(test_db, monkeypatch):
    skill_forge.init_db()
    monkeypatch.setattr(skill_forge, "_validate_in_sandbox",
                        lambda code, inputs: (False, "boom"))
    assert skill_forge.attempt_forge(FakeClient(GOOD), "gap") is None
    assert skill_forge.list_forged() == []


def test_validation_fails_closed_without_docker(test_db, monkeypatch):
    """Forged code is model-written and untrusted, so it is validated in Docker
    or not at all. No Docker must mean refusal, never a host run."""
    from tools import sandbox

    def _no_docker():
        raise sandbox.SandboxUnavailable("no docker here")
    monkeypatch.setattr(sandbox, "autonomous_backend", _no_docker)

    ok, msg = skill_forge._validate_in_sandbox("def run(i): return 'x'", {})
    assert ok is False
    assert "Docker" in msg


@pytest.mark.parametrize("bad", [
    {**GOOD, "tool_name": "not an identifier"},
    {**GOOD, "tool_name": ""},
    {**GOOD, "code": ""},
])
def test_malformed_proposals_are_refused(forge_db, bad):
    assert skill_forge.attempt_forge(FakeClient(bad), "gap") is None


def test_non_json_reply_is_refused(forge_db):
    assert skill_forge.attempt_forge(FakeClient("I'd rather not."), "gap") is None


# ── acquire(): the other path into installed code ─────────────────────────────

def test_offline_acquire_goes_through_the_approval_gate(forge_db, monkeypatch):
    """`acquire` calls skills.create_skill with _trigger='expert', and
    agent/skills.py gates anything that is not 'manual' into approvals.stage.

    The docstring claimed offline skills install immediately. They do not.
    """
    staged = []
    from agent import approvals
    monkeypatch.setattr(approvals, "stage",
                        lambda kind, payload: staged.append((kind, payload))
                        or "[STAGED for approval #1] skill_code write held pending review")

    msg = skill_forge.acquire(FakeClient(GOOD), "count words", allow_network=False)
    assert staged, f"offline acquire bypassed the approval gate: {msg}"
    assert staged[0][0] == "skill_code"


def test_offline_acquire_reports_staging_as_pending_not_failure(forge_db, monkeypatch):
    """A gated skill is the system working. Reporting it as 'didn't install
    cleanly' told the user a success was a failure."""
    from agent import approvals
    monkeypatch.setattr(
        approvals, "stage",
        lambda kind, payload: "[STAGED for approval #1] skill_code write held "
                              "pending review (approval gate is on).")

    msg = skill_forge.acquire(FakeClient(GOOD), "count words", allow_network=False)
    low = msg.lower()
    assert "approval" in low or "approve" in low, msg
    assert "didn't install cleanly" not in low, (
        "the approval gate is reported to the user as a failure"
    )


def test_networked_acquire_stages_and_flags_network(forge_db, monkeypatch):
    monkeypatch.setattr(skill_forge, "_compile_check", lambda code: (True, "ok"))
    msg = skill_forge.acquire(FakeClient(GOOD), "fetch a feed", allow_network=True)

    rows = skill_forge.list_forged(status="pending")
    assert rows, f"networked skill was not staged: {msg}"
    assert rows[0]["needs_network"] is True
    assert "approval" in msg.lower()


def test_a_failed_proposal_installs_nothing(forge_db, monkeypatch):
    monkeypatch.setattr(skill_forge, "_propose", lambda *a, **k: None)
    msg = skill_forge.acquire(FakeClient(GOOD), "impossible", allow_network=False)
    assert "couldn't forge" in msg.lower()
    assert skill_forge.list_forged() == []
