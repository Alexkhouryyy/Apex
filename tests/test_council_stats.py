"""Whether Council Mode is earning its cost — measured, not assumed.

Council ran whenever the model picked the tool or the user typed /council.
Nothing decided whether it was worth it, and nothing recorded how it went, so
there was no evidence to decide from even in principle.

The signal these tests defend: the honest question is not "was the council
right" (which needs an outcome nobody may ever report) but "did the extra
models say anything different", which is measurable on every single run,
immediately.
"""
import pytest

from agent import council_stats as cs


@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "council.db"))
    cs.init_db()
    return longterm


class TestTaskType:
    @pytest.mark.parametrize("question,expected", [
        ("why does this function throw a TypeError?", "code"),
        ("refactor the SQL query", "code"),
        ("should we use Postgres or SQLite?", "decision"),
        ("is it worth it to rewrite this?", "decision"),
        ("what year did the Apollo program end?", "factual"),
        ("draft an email to the landlord", "writing"),
        ("how should we structure the auth layer?", "design"),
        ("tell me about badgers", "general"),
    ])
    def test_questions_land_in_a_sensible_bucket(self, question, expected):
        assert cs.task_type(question) == expected

    def test_buckets_are_coarse_on_purpose(self):
        """Statistics need enough runs per bucket to mean anything. A
        fine-grained taxonomy gives every question its own bucket of one, and
        then nothing ever reaches MIN_RUNS_FOR_ADVICE."""
        assert len(cs._TASK_PATTERNS) <= 8

    @pytest.mark.parametrize("junk", ["", "   ", None])
    def test_nothing_still_classifies(self, junk):
        assert cs.task_type(junk) == "general"


class TestRecording:
    def test_a_run_is_recorded_with_its_measured_overlap(self, db):
        cs.record_run("fix this bug", ["Claude", "GPT"],
                      {"overlap": 0.2, "correlated": False}, "high")
        s = cs.stats("code")
        assert s["runs"] == 1 and s["measured"] == 1
        assert s["diverged"] == 1, "overlap 0.2 is genuine divergence"

    def test_high_overlap_counts_as_agreement(self, db):
        cs.record_run("fix this bug", ["Claude", "GPT"],
                      {"overlap": 0.95, "correlated": True}, "high")
        assert cs.stats("code")["diverged"] == 0

    @pytest.mark.parametrize("agreement", [
        None,                      # no measurement at all
        {},                        # a report with no overlap in it
        {"overlap": None},         # present but unmeasured
        {"overlap": "high"},       # present but not a number
        {"correlated": True},      # other fields, no overlap
    ])
    def test_a_missing_measurement_is_null_not_zero(self, db, agreement):
        """A council with nothing to compare has no overlap. Storing that as
        0.0 would make it look like MAXIMUM divergence — the single strongest
        possible argument for convening more councils, invented out of a
        council that never actually disagreed with itself.

        Parametrized across every shape a missing measurement arrives in:
        the first version only passed None, which skips the dict branch
        entirely and left the coercion inside it untested.
        """
        cs.record_run("fix this bug", ["Claude"], agreement, "low")
        s = cs.stats("code")
        assert s["runs"] == 1 and s["measured"] == 0 and s["unmeasured"] == 1
        assert s["divergence_rate"] is None

    def test_unmeasurable_runs_are_not_folded_in_as_agreements(self, db):
        """The other direction of the same error: counting them as agreements
        would argue for convening FEWER councils on evidence that does not
        exist."""
        cs.record_run("q", ["A", "B"], {"overlap": 0.1}, "low")   # diverged
        cs.record_run("q", ["A"], None, "low")                     # unmeasurable
        s = cs.stats()
        assert s["measured"] == 1 and s["divergence_rate"] == 1.0

    def test_recording_never_raises(self, db, monkeypatch):
        """A bookkeeping failure must not cost the answer the council just
        spent three model calls producing."""
        from agent import longterm
        monkeypatch.setattr(longterm, "_conn",
                            lambda: (_ for _ in ()).throw(RuntimeError("gone")))
        assert cs.record_run("q", ["A", "B"], {"overlap": 0.5}) is None

    def test_garbage_agreement_data_does_not_raise(self, db):
        for bad in ("not a dict", {"overlap": "high"}, {}, {"overlap": None}):
            assert cs.record_run("q", ["A", "B"], bad) is not None


class TestAdvice:
    def _runs(self, overlaps, question="fix this bug"):
        for o in overlaps:
            cs.record_run(question, ["Claude", "GPT"], {"overlap": o}, "high")

    def test_too_little_history_says_so_rather_than_guessing(self, db):
        """THE honesty rule. Two runs is two coin flips. An opinion built on
        it would carry an authority it has not earned — and the whole point of
        this module is to stop Council usage being decided by vibes."""
        self._runs([0.1, 0.2])
        a = cs.advise("fix this bug")
        assert a["convene"] is None and a["confidence"] == "none"
        assert "not enough" in a["reason"]

    def test_consistent_divergence_recommends_convening(self, db):
        self._runs([0.1, 0.2, 0.15, 0.3, 0.25])
        a = cs.advise("fix this bug")
        assert a["convene"] is True and a["confidence"] == "measured"
        assert "real work" in a["reason"]

    def test_consistent_agreement_recommends_against(self, db):
        self._runs([0.9, 0.95, 0.88, 0.92, 0.97])
        a = cs.advise("fix this bug")
        assert a["convene"] is False
        assert "latency and cost" in a["reason"]

    def test_even_a_negative_verdict_refuses_to_call_agreement_proof(self, db):
        """The design doc's own warning, and it cuts both ways: agreement is
        evidence about the MODELS, not proof they were right. Advice that
        forgot this would teach exactly the wrong lesson."""
        self._runs([0.9, 0.95, 0.88, 0.92, 0.97])
        assert "not proof" in cs.advise("fix this bug")["reason"]

    def test_a_mixed_record_gives_no_false_signal(self, db):
        self._runs([0.1, 0.9, 0.2, 0.95, 0.15, 0.88])
        a = cs.advise("fix this bug")
        assert a["convene"] is None and a["confidence"] == "mixed"

    def test_advice_is_per_task_type_not_global(self, db):
        """'Code questions always agree' says nothing about design questions.
        A single global number would let one busy category silence another."""
        self._runs([0.9, 0.95, 0.9, 0.92, 0.95], question="fix this bug")
        self._runs([0.1, 0.2, 0.15, 0.1, 0.2],
                   question="how should we structure the auth layer?")
        assert cs.advise("fix this bug")["convene"] is False
        assert cs.advise("how should we structure the auth layer?")["convene"] is True

    def test_the_chair_confidence_is_never_the_evidence(self, db):
        """A chair that read three similar answers reports high confidence
        BECAUSE they were similar, so it cannot also be the check on them.
        Recorded for the record; never used to decide."""
        for _ in range(5):
            cs.record_run("fix this bug", ["Claude", "GPT"],
                          {"overlap": 0.95}, "high")
        assert cs.advise("fix this bug")["convene"] is False, \
            "high chair confidence must not argue for more councils"

    def test_summary_is_readable_with_no_history(self, db):
        assert "No council runs recorded yet" in cs.summary()

    def test_summary_reports_each_task_type(self, db):
        self._runs([0.1, 0.2], question="fix this bug")
        self._runs([0.9], question="draft an email")
        text = cs.summary()
        assert "code" in text and "writing" in text
        assert "Divergence is the signal" in text
