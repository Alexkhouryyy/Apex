"""A new thing Apex can do unattended must not ship undeclared.

tools/wiring_audit.py catches code that never runs. Nothing caught code that
starts running *more* than it used to — and enabling a dormant path is the same
as writing new code. That gap cost something real: switching on the consolidation
heartbeat also switched on refine_skills(), which had a model rewrite Apex's
executable skills and install them unreviewed. Nobody asked what the new cadence
could reach, because nothing made anyone ask.

This makes it ask. A new (autonomous entry -> capability) edge fails here until
someone writes down what gates it.

Note on precision: the call graph is name-based, so it OVER-reports — two
distinct `run` functions collapse into one node, and guards are invisible to it.
That is the correct direction of error for a safety inventory, and it is why
DISPOSITIONS describes the real gate rather than the tool inferring one.
"""
import json
from pathlib import Path

import pytest

from tools import autonomy_audit

# What actually gates each capability when an unattended path reaches it.
# Written by a person on purpose: a tool that concluded "this one's fine" would
# be exactly the confident wrongness this file exists to prevent.
DISPOSITIONS = {
    "run_shell":
        "Cortex run_python uses sandbox.autonomous_backend(), which forces "
        "Docker and refuses host fallback. Confirm-tier bash requires approval "
        "and dispatcher safety. Trusted installed skills can use the configured "
        "host backend; this name-based inventory is not a universal sandbox.",
    "create_skill":
        "Gated by approval since 0c1e5e5. Any _trigger != 'manual' is staged "
        "through approvals; the only path to disk is approvals._apply, on a "
        "human yes. Validation parses rather than exec()s.",
    "write_text":
        "Ungated, and accepted: writing reports, notes and documents is the "
        "job. Paths are Apex's own directories; destructive writes go through "
        "the safety layer's confirm tier.",
    "notify":
        "Ungated by design, but shaped by agent/restraint.py — non-urgent "
        "messages wait for a moment you respond in. Urgent always gets through.",
    "complete":
        "Shared model calls reserve budget allowance before dispatch and "
        "settle usage through agent/budget.py and agent/telemetry.py. "
        "Cadence also bounds consolidation and Cortex decisions.",
    "set_goal":
        "Gated by approval. initiative only ever stages; approvals._apply is "
        "the sole path from a proposal to a real goal.",
    "stage":
        "This IS the gate — reaching it means the path defers to a human.",
}

# Fixed, version-controlled pairs. Never derive the approval inventory from the
# code under test: a new edge must require an explicit reviewed file change.
_INVENTORY = json.loads((Path(__file__).resolve().parents[1] / "docs/autonomy_inventory.json").read_text())
REVIEWED_ENTRIES = _INVENTORY["entries"]
BASELINE = {(record["entry"], record["capability"]): record["disposition"]
            for record in _INVENTORY["edges"]}


def test_every_capability_has_a_written_disposition():
    """The inventory is only worth having if it says what holds each edge."""
    for _, sink in autonomy_audit.edges():
        assert sink in DISPOSITIONS, f"no disposition written for {sink!r}"
        assert len(DISPOSITIONS[sink]) > 40, f"disposition for {sink!r} is too thin"


def test_no_undeclared_autonomous_capability():
    """The check that would have caught the heartbeat bug.

    Turning on a dormant path, or adding an autonomous trigger, changes what
    Apex can do unattended. That must be a deliberate, written decision.
    """
    unreviewed_entries = sorted(set(autonomy_audit.AUTONOMOUS_ENTRIES) - set(REVIEWED_ENTRIES))
    assert unreviewed_entries == [], f"New unattended entries without review: {unreviewed_entries}"
    undeclared = sorted(autonomy_audit.edges() - set(BASELINE))
    assert undeclared == [], (
        "New unattended capability with no disposition:\n  " +
        "\n  ".join(f"{e} -> {s}" for e, s in undeclared))


def test_committed_inventory_has_unique_pairs_and_explicit_dispositions():
    assert len(BASELINE) == len(_INVENTORY["edges"]), "duplicate inventory pairs"
    for (entry, sink), disposition in BASELINE.items():
        assert entry in REVIEWED_ENTRIES
        assert sink in autonomy_audit.SINKS
        assert len(disposition) > 40
    assert all(len(context) > 40 for context in REVIEWED_ENTRIES.values())


def test_new_entry_to_existing_dangerous_sink_is_rejected(monkeypatch):
    actual = autonomy_audit.edges()
    monkeypatch.setattr(autonomy_audit, "edges", lambda: actual | {("unreviewed_entry", "run_shell")})
    with pytest.raises(AssertionError, match="unreviewed_entry -> run_shell"):
        test_no_undeclared_autonomous_capability()


def test_new_pair_from_existing_entry_is_rejected(monkeypatch):
    # Simulate a detected pair with no corresponding review, even though both
    # the entry and sink individually already have dispositions.
    monkeypatch.delitem(BASELINE, ("propose", "create_skill"))
    with pytest.raises(AssertionError, match="propose -> create_skill"):
        test_no_undeclared_autonomous_capability()


def test_reachability_keeps_the_short_path_when_a_long_path_hits_the_depth_limit(monkeypatch):
    monkeypatch.setattr(autonomy_audit, "_MAX_DEPTH", 2)
    graph = {"entry": ["long", "shared"], "long": ["shared"],
             "shared": ["helper"], "helper": ["run_shell"]}
    assert autonomy_audit._reachable(graph, "entry") == {"run_shell"}
    graph["entry"].reverse()
    assert autonomy_audit._reachable(graph, "entry") == {"run_shell"}


def test_new_entry_without_detected_sinks_still_requires_review(monkeypatch):
    monkeypatch.setitem(autonomy_audit.AUTONOMOUS_ENTRIES, "new_empty_entry", "new schedule")
    with pytest.raises(AssertionError, match="new_empty_entry"):
        test_no_undeclared_autonomous_capability()


def test_actual_cortex_sandbox_guard_never_falls_back_to_host(monkeypatch):
    from agent import cortex
    from tools import sandbox
    monkeypatch.setattr(sandbox.config, "EXECUTION_BACKEND", "local")
    monkeypatch.setattr(sandbox, "docker_available", lambda refresh=False: False)
    monkeypatch.setattr(sandbox.LocalBackend, "run_python",
                        lambda *a, **kw: pytest.fail("autonomous code reached the host backend"))
    result = cortex._execute_tool("run_python", {"code": "print('test')"}, raise_errors=True)
    assert "unavailable" in result and "not run on host" in result


def test_the_dangerous_sinks_are_gated_not_merely_noted():
    """Shell, code-writing and goal-creation are the three that must name a
    real mechanism, not just describe themselves."""
    for sink, must_mention in (
        ("run_shell", "autonomous_backend"),
        ("create_skill", "approvals"),
        ("set_goal", "approvals"),
    ):
        assert must_mention in DISPOSITIONS[sink], (
            f"{sink} must name the mechanism that gates it")


def test_every_autonomous_entry_is_found_in_the_code():
    """A declared entry point that no longer exists means the inventory is
    describing a system that has moved on."""
    graph = autonomy_audit.build_graph()
    missing = [e for e in autonomy_audit.AUTONOMOUS_ENTRIES if e not in graph]
    assert missing == [], f"declared autonomous entries not found: {missing}"


def test_the_audit_reports_the_heartbeat_reaching_code_execution():
    """The specific fact nobody had written down: my 6-hourly consolidation
    can reach shell execution and executable-code writing."""
    edges = autonomy_audit.edges()
    assert ("consolidate_if_due", "run_shell") in edges
    assert ("consolidate_if_due", "create_skill") in edges


def test_the_report_is_human_readable():
    """It exists to be read; a report nobody reads is another silent failure."""
    text = autonomy_audit.report()
    assert "consolidate_if_due" in text and "run_shell" in text
    assert "every 6h" in text          # says WHEN, not just what


def test_over_reporting_is_acknowledged_not_hidden():
    """The graph is name-based and collapses distinct functions. Pinned so the
    limitation stays visible rather than being mistaken for precision."""
    doc = autonomy_audit.__doc__ or ""
    assert "over-report" in doc.lower()
    assert "name-based" in doc.lower()


def test_the_audit_never_raises(monkeypatch):
    monkeypatch.setattr(autonomy_audit, "ROOT",
                        autonomy_audit.ROOT / "does_not_exist")
    assert isinstance(autonomy_audit.edges(), set)
