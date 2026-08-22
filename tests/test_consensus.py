"""Council agreement, measured — and explicitly not treated as evidence.

CLAUDE.md §41: "Three models agreeing on outdated information is still wrong.
Council Mode should not create false confidence from correlated model knowledge."

That row was UNPROVEN because nothing measured it. `agent/consensus.py` does now,
and the property that matters most is what it refuses to do: it never converts
agreement into a confidence score, because agreement between models trained on
overlapping corpora is exactly as strong when they are wrong.
"""
from __future__ import annotations

from agent import consensus


def _t(*answers, round_no=0):
    return [{"round": round_no, "label": f"M{i}", "text": a}
            for i, a in enumerate(answers)]


# ── it measures agreement ─────────────────────────────────────────────────────

def test_near_identical_answers_read_as_correlated():
    t = _t("Revenue grew to 4.2 billion driven by cloud services expansion",
           "Revenue grew to 4.2 billion driven by cloud services expansion")
    r = consensus.agreement(t)
    assert r["overlap"] > 0.9
    assert r["correlated"] is True


def test_genuinely_different_answers_read_as_divergent():
    t = _t("Buy the cheaper option; the warranty covers the difference.",
           "Rent instead. Ownership makes no sense below eighteen months usage.")
    r = consensus.agreement(t)
    assert r["overlap"] < consensus.HIGH_OVERLAP
    assert r["correlated"] is False
    assert "diverge" in r["note"].lower()


def test_only_the_opening_round_counts():
    """After round 0 members have read each other. Convergence then measures
    persuasion, not corroboration — including it would inflate every score."""
    t = _t("alpha beta gamma") + [
        {"round": 1, "label": "M0", "text": "identical identical identical"},
        {"round": 1, "label": "M1", "text": "identical identical identical"},
    ]
    assert set(consensus.opening_answers(t)) == {"M0"}


def test_one_member_cannot_agree_with_itself():
    r = consensus.agreement(_t("a single answer"))
    assert r["overlap"] is None
    assert "Fewer than two" in r["note"]


# ── the §41 case ──────────────────────────────────────────────────────────────

def test_unanimous_figures_are_surfaced_for_verification():
    """A number every member produced is the most dangerous kind of agreement:
    it looks like precision and it goes stale silently."""
    t = _t("As of now the figure is 42% and rising",
           "Currently it sits at 42% by most measures",
           "The latest number is 42% across the sector")
    r = consensus.agreement(t)
    assert "42%" in r["verify"], f"unanimous figure not flagged: {r['verify']}"


def test_time_sensitive_unanimity_says_verify_not_trust():
    t = _t("As of now the current leader is Acme with 42% share",
           "Currently Acme leads at 42% share as of the latest data")
    r = consensus.agreement(t)
    assert r["time_sensitive"] is True
    assert "42%" in r["verify"]
    low = r["note"].lower()
    assert "verify" in low
    assert "stale" in low or "training data" in low
    # Deliberately NOT asserting r["correlated"]: these two answers share a
    # figure while phrasing it differently, which scores low lexical overlap.
    # That is the dangerous case, and keying the warning on overlap missed it.


def test_it_never_reports_a_confidence_score():
    """The whole point. A percentage here would be read as 'how likely this is
    correct', which is precisely what the measurement cannot tell you."""
    r = consensus.agreement(_t("same thing", "same thing"))
    assert "confidence" not in r
    text = consensus.format_for_user(r).lower()
    assert "confiden" not in text, f"leaked a confidence claim: {text}"


def test_the_wording_attributes_agreement_to_the_models_not_the_truth():
    r = consensus.agreement(_t("alpha beta gamma delta epsilon",
                               "alpha beta gamma delta epsilon"))
    note = r["note"].lower()
    assert "not evidence" in note or "not correctness" in note


def test_divergent_answers_that_share_a_figure_still_flag_it():
    t = _t("Completely different framing entirely about costs, roughly 42%.",
           "An unrelated argument concerning timelines, though 42% appears.")
    r = consensus.agreement(t)
    assert r["correlated"] is False
    assert "42%" in r["verify"]
    assert "checking" in r["note"].lower() or "check" in r["note"].lower()


# ── it survives real input ────────────────────────────────────────────────────

def test_empty_and_malformed_transcripts_do_not_raise():
    for bad in ([], None, [{"round": 0}], [{"label": "x"}]):
        r = consensus.agreement(bad)
        assert isinstance(r, dict)


def test_council_attaches_the_measurement_to_its_result():
    """A measurement nobody reads is the eleventh 'built but never ran'."""
    import inspect
    from agent import council
    assert "agreement" in {f.name for f in
                           __import__("dataclasses").fields(council.CouncilResult)}
    assert "consensus.agreement" in inspect.getsource(council.convene)
