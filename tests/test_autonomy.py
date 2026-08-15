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
import pytest

from tools import autonomy_audit

# What actually gates each capability when an unattended path reaches it.
# Written by a person on purpose: a tool that concluded "this one's fine" would
# be exactly the confident wrongness this file exists to prevent.
DISPOSITIONS = {
    "run_shell":
        "Gated by origin. Self-initiated execution goes through "
        "sandbox.autonomous_backend(), which forces Docker and raises "
        "SandboxUnavailable rather than falling back to the host.",
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
        "Ungated model spend. Bounded by cadence rather than permission: "
        "consolidation is 6-hourly, the cortex 5-minutely. agent/budget.py "
        "tracks the cost.",
    "set_goal":
        "Gated by approval. initiative only ever stages; approvals._apply is "
        "the sole path from a proposal to a real goal.",
    "stage":
        "This IS the gate — reaching it means the path defers to a human.",
}

# The edges that exist today. Regenerate deliberately, never reflexively:
# a diff here is a change in what Apex can do while you are asleep.
BASELINE = autonomy_audit.edges()


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
    undeclared = [
        (entry, sink) for entry, sink in autonomy_audit.edges()
        if sink not in DISPOSITIONS
    ]
    assert undeclared == [], (
        "New unattended capability with no disposition:\n  " +
        "\n  ".join(f"{e} -> {s}" for e, s in undeclared))


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
