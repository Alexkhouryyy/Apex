"""apex_hypothesis through the real tool dispatcher.

`tests/test_genesis.py` proves the gates. This file proves the half that has
silently failed before in this codebase: that the tool is reachable, that a
refusal arrives as text a model can read rather than an exception, and — the
one that matters — that `propose` runs the WHOLE pipeline.

A half-run pipeline is the failure to avoid. Hypotheses that were generated and
never attacked would sit in the table looking exactly like ones that had
survived something, and the table is what Apex reads back later.
"""
from __future__ import annotations

import pytest

from agent import core, genesis as g, longterm


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "genesis.db"))
    longterm.init_db()
    g.init_db()


@pytest.fixture
def models(monkeypatch):
    """Two configured models, so critique can be independent."""
    from agent import council
    monkeypatch.setattr(council, "available_members",
                        lambda: [("model-a", "A"), ("model-b", "B")])
    import config
    monkeypatch.setattr(config, "AGENT_MODEL", "model-a")


@pytest.fixture
def embeddings(monkeypatch):
    from tests.test_genesis import fake_embeddings
    fake_embeddings(monkeypatch, {})


PROPOSAL = ('[{"claim": "Lithium cells above 40C lose more than 5% capacity in 500 '
            'cycles", "prediction": "The 45C group ends below 95%", "refutation": '
            '"The 45C group finishes within 2% of the 25C control", "method": '
            '"cycle 20 cells per temperature", "evidence_for": ["s1"], '
            '"evidence_against": ["s2"], "searched_contrary": true}]')

SOURCES = [{"id": "s1", "text": "an ageing study"},
           {"id": "s2", "text": "a null result at 45C"}]


def reply_by_system(propose_reply, critique_reply):
    """Route the stub by which system prompt it was handed — the same process
    calls the model for two completely different jobs."""
    def _complete(model, system, user, max_tokens=2048):
        return critique_reply if "attacking" in system else propose_reply
    return _complete


def stub(monkeypatch, propose_reply=PROPOSAL,
         critique_reply='{"verdict": "minor", "text": "small sample"}'):
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        reply_by_system(propose_reply, critique_reply))


def run(**inputs) -> str:
    return core._execute_tool("apex_hypothesis", inputs)


class TestTheToolIsActuallyWired:

    def test_it_is_offered_to_the_model(self):
        assert any(t["name"] == "apex_hypothesis" for t in core.TOOLS)

    def test_the_schema_requires_an_action(self):
        tool = [t for t in core.TOOLS if t["name"] == "apex_hypothesis"][0]
        assert tool["input_schema"]["required"] == ["action"]

    def test_an_unknown_action_explains_itself(self, db):
        out = run(action="ponder")
        for a in ("propose", "due", "show", "list", "observe"):
            assert a in out

    def test_the_description_warns_against_inventing_a_citation(self):
        tool = [t for t in core.TOOLS if t["name"] == "apex_hypothesis"][0]
        assert "never invent one" in tool["description"]


class TestProposeRunsTheWholePipeline:

    def test_generate_critique_and_review_all_happen_in_one_call(
            self, db, models, embeddings, monkeypatch):
        stub(monkeypatch)
        out = run(action="propose", question="why do batteries die", sources=SOURCES)
        rows = g.listing()
        assert len(rows) == 1
        hid = rows[0]["id"]
        assert g.critiques_of(hid), "generated but never attacked"
        assert rows[0]["gates"], "attacked but never judged"
        assert rows[0]["status"] == g.STANDING
        assert "STANDING" in out

    def test_the_critic_is_not_the_proposer(self, db, models, embeddings, monkeypatch):
        stub(monkeypatch)
        run(action="propose", question="q", sources=SOURCES)
        hid = g.listing()[0]["id"]
        assert [c.model for c in g.critiques_of(hid)] == ["model-b"]

    def test_a_fatal_critique_rejects_it(self, db, models, embeddings, monkeypatch):
        stub(monkeypatch,
             critique_reply='{"verdict": "fatal", "text": "this is already known"}')
        out = run(action="propose", question="q", sources=SOURCES)
        assert g.listing()[0]["status"] == g.REJECTED
        assert "already known" in out

    def test_an_invented_citation_is_rejected(self, db, models, embeddings, monkeypatch):
        bad = PROPOSAL.replace('["s1"]', '["Smith2019"]')
        stub(monkeypatch, propose_reply=bad)
        out = run(action="propose", question="q", sources=SOURCES)
        assert g.listing()[0]["status"] == g.REJECTED
        assert "Smith2019" in out

    def test_the_summary_counts_survivors_honestly(self, db, models, embeddings, monkeypatch):
        stub(monkeypatch, critique_reply='{"verdict": "fatal", "text": "no"}')
        out = run(action="propose", question="q", sources=SOURCES)
        assert "0 of 1 proposals survived" in out

    def test_a_rejection_is_reported_as_a_diagnosis(self, db, models, embeddings, monkeypatch):
        stub(monkeypatch, critique_reply='{"verdict": "fatal", "text": "no"}')
        assert "objection says what to fix" in run(
            action="propose", question="q", sources=SOURCES)

    def test_without_sources_it_refuses_instead_of_rejecting_everything(self, db, models):
        out = run(action="propose", question="q")
        assert "cite something" in out
        assert g.listing() == [], "nothing should be stored"

    def test_sources_without_ids_do_not_count(self, db, models):
        out = run(action="propose", question="q", sources=[{"text": "no id here"}])
        assert "cite something" in out

    def test_a_question_is_required(self, db, models):
        assert "needs a question" in run(action="propose", sources=SOURCES)

    def test_a_model_replying_with_prose_is_a_message_not_a_traceback(
            self, db, models, monkeypatch):
        stub(monkeypatch, propose_reply="Here are some thoughts on batteries.")
        out = run(action="propose", question="q", sources=SOURCES)
        assert "[Genesis]" in out and "did not return JSON" in out

    def test_novelty_degrades_to_unverified_without_embeddings(
            self, db, models, monkeypatch):
        """On a machine with no embedding model this is the honest outcome, and
        it must not read as `standing`."""
        monkeypatch.setattr(longterm, "_embed", lambda _t: None)
        stub(monkeypatch)
        out = run(action="propose", question="q", sources=SOURCES)
        assert g.listing()[0]["status"] == g.UNVERIFIED
        assert "UNVERIFIED" in out

    def test_the_source_text_is_part_of_the_novelty_corpus(
            self, db, models, monkeypatch):
        """A claim that paraphrases a source it was just shown is extraction,
        not genesis — and the sources are the likeliest thing it copied."""
        from tests.test_genesis import fake_embeddings
        claim = ("Lithium cells above 40C lose more than 5% capacity in 500 cycles")
        source_text = "a source saying almost exactly that"
        fake_embeddings(monkeypatch, {claim: [1.0] + [0.0] * 63,
                                      source_text: [0.99, 0.141] + [0.0] * 62})
        stub(monkeypatch)
        run(action="propose", question="q",
            sources=[{"id": "s1", "text": source_text},
                     {"id": "s2", "text": "a null result"}])
        row = g.listing()[0]
        assert row["status"] == g.REJECTED
        assert "already holds this idea" in row["reason"]


class TestASingleModelInstall:
    """One configured provider is a normal way to run Apex, and it has to
    behave differently from a two-model install that skipped the critique.

    The proposal is genuinely reviewed — by the model that wrote it — so the
    honest status is `unverified`: not `standing`, because the critique is
    correlated; not `rejected`, because nothing was skipped and the user's
    configuration is not a mistake.
    """

    @pytest.fixture(autouse=True)
    def _one_model(self, monkeypatch):
        from agent import council
        monkeypatch.setattr(council, "available_members", lambda: [("model-a", "A")])
        import config
        monkeypatch.setattr(config, "AGENT_MODEL", "model-a")

    def test_self_critique_is_unverified_not_rejected(self, db, embeddings, monkeypatch):
        stub(monkeypatch)
        out = run(action="propose", question="q", sources=SOURCES)
        row = g.listing()[0]
        assert [c.model for c in g.critiques_of(row["id"])] == ["model-a"], (
            "with one provider there is nobody else to ask")
        assert row["status"] == g.UNVERIFIED
        assert "independent critique was NOT obtained" in row["reason"]
        assert "UNVERIFIED" in out

    def test_it_is_still_testable(self, db, embeddings, monkeypatch):
        stub(monkeypatch)
        run(action="propose", question="q", sources=SOURCES)
        hid = g.listing()[0]["id"]
        assert hid in [d["id"] for d in g.due()]


class TestDueShowListObserve:

    def _standing(self, monkeypatch):
        stub(monkeypatch)
        run(action="propose", question="q", sources=SOURCES)
        return g.listing()[0]["id"]

    def test_due_names_the_test_to_run(self, db, models, embeddings, monkeypatch):
        self._standing(monkeypatch)
        out = run(action="due")
        assert "cycle 20 cells per temperature" in out
        assert "refuted if" in out

    def test_due_is_empty_before_anything_stands(self, db):
        assert "Nothing is waiting" in run(action="due")

    def test_show_gives_the_whole_record(self, db, models, embeddings, monkeypatch):
        hid = self._standing(monkeypatch)
        out = run(action="show", id=hid)
        assert "refutation:" in out and "critique (model-b" in out

    def test_show_of_a_missing_id_says_so(self, db):
        assert "No hypothesis" in run(action="show", id=4242)

    def test_list_can_filter_by_status(self, db, models, embeddings, monkeypatch):
        self._standing(monkeypatch)
        assert "STANDING" in run(action="list", status="standing")
        assert "No hypotheses" in run(action="list", status="refuted")

    def test_observe_resolves_it(self, db, models, embeddings, monkeypatch):
        hid = self._standing(monkeypatch)
        out = run(action="observe", id=hid, observation="45C ended at 99%",
                  outcome="matched_refutation")
        assert "REFUTED" in out
        assert g.get(hid)["status"] == g.REFUTED

    def test_observing_something_unjudged_is_a_message_not_a_traceback(self, db):
        hid = g.record(claim="c", prediction="p", refutation="r", method="m")
        out = run(action="observe", id=hid, observation="x",
                  outcome="matched_prediction")
        assert "[Genesis]" in out and "not been reviewed" in out

    def test_a_bad_outcome_is_a_message(self, db, models, embeddings, monkeypatch):
        hid = self._standing(monkeypatch)
        out = run(action="observe", id=hid, observation="x", outcome="sort of")
        assert "[Genesis]" in out
