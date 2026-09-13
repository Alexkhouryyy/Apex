"""Concept Genesis — mostly a list of things that must be rejected.

Phase 13's success check is "testable novel hypotheses with evidence and
critique", and every one of those four words can be counterfeited by a fluent
paragraph. Generation is one API call; the hard part, and nearly all of this
file, is deciding which of the generated claims actually qualify.

The novelty tests are the ones to read. `TestNoveltyIsAsymmetric` exists
because a check that could only be wrong in one direction has to be allowed to
be wrong in only that direction: word overlap proves a duplicate and proves
nothing about originality, so the lexical mode may return `not novel` and may
never return `novel`. Getting that backwards would make every paraphrase look
like an original idea, on exactly the machines that cannot tell.
"""
from __future__ import annotations

import numpy as np
import pytest

from agent import genesis as g
from agent import longterm


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "genesis.db"))
    longterm.init_db()
    g.init_db()


@pytest.fixture
def no_embeddings(monkeypatch):
    """The machine this runs on: sentence-transformers unreachable."""
    monkeypatch.setattr(longterm, "_embed", lambda _t: None)


def fake_embeddings(monkeypatch, mapping: dict[str, list[float]]):
    """Deterministic vectors for named texts, so the SEMANTIC path can be
    exercised where no embedding model can be downloaded.

    Anything not named gets a vector orthogonal to everything else, which is
    what "unrelated" has to mean for the threshold test to be about the
    threshold rather than about the stub.
    """
    unknown = {}

    def _embed(text: str):
        key = (text or "").strip()
        if key in mapping:
            v = np.asarray(mapping[key], dtype=np.float32)
        else:
            idx = unknown.setdefault(key, len(unknown))
            v = np.zeros(64, dtype=np.float32)
            v[8 + (idx % 56)] = 1.0
        n = float(np.linalg.norm(v)) or 1.0
        return (v / n).astype(np.float32).tobytes()

    monkeypatch.setattr(longterm, "_embed", _embed)


GOOD = dict(
    claim="Lithium cells stored above 40C lose more than 5% capacity in 500 cycles",
    prediction="The 45C group ends below 95% of nominal capacity",
    refutation="The 45C group finishes within 2% of the 25C control after 500 cycles",
    method="Cycle 20 cells at each temperature, measuring capacity every 50 cycles",
)


# --------------------------------------------------------------------------
# Gate 1 — is it a hypothesis at all
# --------------------------------------------------------------------------

class TestStructureGate:

    def test_a_real_hypothesis_passes(self):
        assert g.check_structure(**GOOD).verdict == g.PASS

    @pytest.mark.parametrize("field", ["claim", "prediction", "refutation", "method"])
    def test_every_field_is_required(self, field):
        args = dict(GOOD, **{field: ""})
        r = g.check_structure(**args)
        assert r.verdict == g.FAIL and field in r.reason

    @pytest.mark.parametrize("hedge", [
        "Hot storage may degrade lithium cells",
        "Hot storage might degrade lithium cells",
        "Hot storage could degrade lithium cells",
        "Hot storage potentially degrades lithium cells",
        "Hot storage possibly degrades lithium cells",
        "In some cases hot storage degrades lithium cells",
    ])
    def test_a_claim_compatible_with_every_observation_is_not_a_hypothesis(self, hedge):
        r = g.check_structure(**dict(GOOD, claim=hedge))
        assert r.verdict == g.FAIL
        assert "hedged" in r.reason

    @pytest.mark.parametrize("claim", [
        "Hot storage degrades lithium cells faster than cool storage",
        "Lithium cells often lose 5% capacity per 500 cycles above 40C",
        "On average, cells above 40C degrade twice as fast",
    ])
    def test_statistical_claims_are_not_mistaken_for_hedges(self, claim):
        """"often" and "on average" are falsifiable with a threshold. Rejecting
        them would throw out most real empirical claims, which is why the hedge
        list is short and specific rather than a general vagueness detector."""
        assert g.check_structure(**dict(GOOD, claim=claim)).verdict == g.PASS

    def test_a_question_is_not_a_claim(self):
        r = g.check_structure(**dict(GOOD, claim="Does hot storage degrade cells?"))
        assert r.verdict == g.FAIL and "question" in r.reason

    @pytest.mark.parametrize("claim", [
        "We should investigate hot storage effects",
        "Explore the relationship between temperature and capacity",
        "Further work is needed on cell degradation",
    ])
    def test_advice_cannot_be_refuted(self, claim):
        assert g.check_structure(**dict(GOOD, claim=claim)).verdict == g.FAIL

    @pytest.mark.parametrize("refutation", [
        "If the hypothesis is wrong",
        "If it turns out to be false",
        "If the data does not support it",
        "If the evidence doesn't support the claim",
        "If it does not work",
        "If no evidence is found",
        "Lack of evidence",
        "If the opposite is true",
    ])
    def test_a_vacuous_refutation_names_no_observation(self, refutation):
        r = g.check_structure(**dict(GOOD, refutation=refutation))
        assert r.verdict == g.FAIL
        assert "restates what being false means" in r.reason

    def test_the_claim_negated_is_not_a_refutation(self):
        """"X degrades Y" refuted by "if X does not degrade Y" is a tautology
        with a lab coat on: it gives you nothing independent to go and look
        at."""
        r = g.check_structure(
            claim="Hot storage degrades lithium cells",
            prediction="cells get worse",
            refutation="If hot storage does not degrade lithium cells",
            method="observe")
        assert r.verdict == g.FAIL
        assert "introduces nothing new" in r.reason

    def test_a_refutation_that_adds_a_measurement_survives(self):
        r = g.check_structure(
            claim="Hot storage degrades lithium cells",
            prediction="cells get worse",
            refutation="The 45C group finishes within 2% of the 25C control",
            method="cycle test")
        assert r.verdict == g.PASS

    def test_the_reason_says_what_to_do(self):
        """A gate that only says no trains a proposer to stop risking claims."""
        for bad in (dict(GOOD, claim="Cells may degrade when hot"),
                    dict(GOOD, refutation="If the data does not support it")):
            reason = g.check_structure(**bad).reason
            assert any(w in reason for w in ("State it", "Say what you would SEE",
                                             "names a measurement"))


# --------------------------------------------------------------------------
# Gate 2 — evidence
# --------------------------------------------------------------------------

class TestEvidenceGate:

    def _resolver(self, *known):
        return lambda sid: sid in set(known)

    def test_evidence_that_resolves_and_includes_contrary_passes(self):
        ev = [g.Evidence(g.FOR, "s1"), g.Evidence(g.AGAINST, "s2")]
        assert g.check_evidence(ev, self._resolver("s1", "s2")).verdict == g.PASS

    def test_a_citation_that_does_not_resolve_is_fatal(self):
        """The most convincing failure in the category. A fabricated citation
        has an author, a year and a plausible title; the only test is to try to
        fetch it."""
        ev = [g.Evidence(g.FOR, "Smith2019"), g.Evidence(g.AGAINST, "s2")]
        r = g.check_evidence(ev, self._resolver("s2"))
        assert r.verdict == g.FAIL
        assert "Smith2019" in r.reason
        assert r.detail["missing"] == ["Smith2019"]

    def test_no_evidence_at_all_is_a_guess(self):
        """Asserting the REASON, not just the verdict. An empty list also
        trips the contrary-evidence rule, so a test that checked only for FAIL
        passed with this guard removed — which is how it was found."""
        r = g.check_evidence([], self._resolver())
        assert r.verdict == g.FAIL
        assert "no evidence cited at all" in r.reason

    def test_no_evidence_fails_even_when_someone_searched(self):
        """The case that isolates this rule from the contrary-evidence one:
        with `searched_contrary` set, nothing else objects."""
        r = g.check_evidence([], self._resolver(), searched_contrary=True)
        assert r.verdict == g.FAIL
        assert "no evidence cited at all" in r.reason

    def test_only_supporting_evidence_fails_unless_someone_looked(self):
        ev = [g.Evidence(g.FOR, "s1")]
        assert g.check_evidence(ev, self._resolver("s1")).verdict == g.FAIL
        assert g.check_evidence(ev, self._resolver("s1"),
                                searched_contrary=True).verdict == g.PASS

    def test_finding_no_contrary_evidence_is_an_answer_and_is_recorded_as_one(self):
        r = g.check_evidence([g.Evidence(g.FOR, "s1")], self._resolver("s1"),
                             searched_contrary=True)
        assert "none found" in r.reason

    def test_an_unknown_stance_is_refused(self):
        r = g.check_evidence([g.Evidence("maybe", "s1")], self._resolver("s1"))
        assert r.verdict == g.FAIL

    def test_a_broken_resolver_is_unknown_not_a_pass(self):
        """If citations could not be checked, they were not checked."""
        def boom(_sid):
            raise OSError("index unreachable")
        r = g.check_evidence([g.Evidence(g.FOR, "s1")], boom)
        assert r.verdict == g.UNKNOWN


# --------------------------------------------------------------------------
# Gate 3 — novelty, and the asymmetry that is the whole design
# --------------------------------------------------------------------------

class TestNoveltyIsAsymmetric:

    CLAIM = "Lithium cells above 40C lose more than 5% capacity in 500 cycles"

    def test_lexical_mode_can_prove_a_duplicate(self, no_embeddings):
        r = g.check_novelty(self.CLAIM, [
            "Note: lithium cells above 40C lose more than 5% capacity in 500 cycles."])
        assert r.verdict == g.FAIL
        assert r.detail["mode"] == g.LEXICAL

    def test_lexical_mode_can_never_prove_novelty(self, no_embeddings):
        """The load-bearing assertion of this module. Without embeddings, an
        unrelated corpus and a paraphrased corpus look identical — low word
        overlap either way — so `novel` is not available as an answer."""
        r = g.check_novelty(self.CLAIM, ["the price of tea in china",
                                         "an unrelated note about bicycles"])
        assert r.verdict == g.UNKNOWN
        assert "cannot prove novelty" in r.reason
        assert r.detail["mode"] == g.LEXICAL

    def test_no_lexical_result_is_ever_a_pass(self, no_embeddings):
        for corpus in (["totally unrelated"], ["a", "b", "c"],
                       [self.CLAIM.replace("40C", "50C")]):
            assert g.check_novelty(self.CLAIM, corpus).verdict != g.PASS

    def test_a_paraphrase_is_the_reason_the_asymmetry_exists(self, monkeypatch):
        """A paraphrase shares almost no words with its source and nearly all
        of its meaning. Lexically it reads as original; semantically it does
        not. Same input, two modes, two different answers — which is exactly
        why one of them is not allowed to say `novel`."""
        source = "Heat shortens the working life of rechargeable batteries"
        paraphrase = "Elevated temperatures reduce the usable lifespan of secondary cells"

        monkeypatch.setattr(longterm, "_embed", lambda _t: None)
        lexical = g.check_novelty(paraphrase, [source])
        assert lexical.verdict == g.UNKNOWN, "lexically this looks original"

        fake_embeddings(monkeypatch, {source: [1.0] + [0.0] * 63,
                                      paraphrase: [0.95, 0.31] + [0.0] * 62})
        semantic = g.check_novelty(paraphrase, [source])
        assert semantic.verdict == g.FAIL
        assert semantic.detail["mode"] == g.SEMANTIC

    def test_semantic_mode_passes_a_genuinely_new_claim(self, monkeypatch):
        fake_embeddings(monkeypatch, {})
        r = g.check_novelty(self.CLAIM, ["tea", "bicycles", "the moon"])
        assert r.verdict == g.PASS
        assert r.detail["mode"] == g.SEMANTIC

    def test_the_threshold_is_actually_consulted(self, monkeypatch):
        """Just under and just over, so the number in the constant is doing the
        work rather than the shape of the stub."""
        other = "an existing note"
        for sim, expected in ((0.80, g.PASS), (0.90, g.FAIL)):
            fake_embeddings(monkeypatch, {
                other: [1.0] + [0.0] * 63,
                self.CLAIM: [sim, (1 - sim ** 2) ** 0.5] + [0.0] * 62})
            r = g.check_novelty(self.CLAIM, [other])
            assert r.verdict == expected, f"similarity {sim} gave {r.verdict}"
            assert r.detail["closest"] == pytest.approx(sim, abs=1e-3)

    def test_an_empty_corpus_is_unknown_not_novel(self, monkeypatch):
        """Nothing to compare against is not the same as nothing matched, and
        a silently empty corpus is the most likely way this check dies."""
        fake_embeddings(monkeypatch, {})
        r = g.check_novelty(self.CLAIM, [])
        assert r.verdict == g.UNKNOWN
        assert r.detail["compared"] == 0

    def test_the_corpus_size_is_always_reported(self, monkeypatch):
        fake_embeddings(monkeypatch, {})
        assert g.check_novelty(self.CLAIM, ["a", "b", "c"]).detail["compared"] == 3

    def test_the_closest_match_is_named_so_a_rejection_is_actionable(self, no_embeddings):
        r = g.check_novelty(self.CLAIM, [
            "unrelated",
            "Lithium cells above 40C lose more than 5% capacity in 500 cycles"])
        assert "Lithium cells above 40C" in r.detail["closest_text"]


# --------------------------------------------------------------------------
# Gate 4 — critique
# --------------------------------------------------------------------------

class TestCritiqueGate:

    def test_never_attacked_fails(self):
        r = g.check_critique([], "model-a")
        assert r.verdict == g.FAIL and "never attacked" in r.reason

    def test_a_fatal_objection_stands(self):
        cs = [g.Critique("model-b", g.FATAL, "the claim is circular")]
        r = g.check_critique(cs, "model-a", models_available=2)
        assert r.verdict == g.FAIL and "circular" in r.reason

    def test_a_cross_model_critique_passes(self):
        cs = [g.Critique("model-b", g.MINOR, "small sample")]
        r = g.check_critique(cs, "model-a", models_available=2)
        assert r.verdict == g.PASS
        assert r.detail["independent"] == ["model-b"]

    def test_self_review_with_another_model_available_is_a_caller_error(self):
        """The gate's whole point was skipped. A model shares every blind spot
        with itself — the correlated-knowledge problem agent/consensus.py was
        written to measure."""
        cs = [g.Critique("model-a", g.NONE, "looks fine to me")]
        r = g.check_critique(cs, "model-a", models_available=2)
        assert r.verdict == g.FAIL
        assert "its own proposal" in r.reason

    def test_self_review_on_a_single_model_install_is_unknown_not_failure(self):
        """A different fact, and it gets a different answer. Punishing a
        one-model install for its configuration would be wrong; calling its
        self-review independent would be a lie."""
        cs = [g.Critique("model-a", g.NONE, "looks fine to me")]
        r = g.check_critique(cs, "model-a", models_available=1)
        assert r.verdict == g.UNKNOWN
        assert r.detail["self_critique"] is True

    def test_an_unrecognised_verdict_is_refused(self):
        r = g.check_critique([g.Critique("model-b", "lgtm", "")], "model-a")
        assert r.verdict == g.FAIL

    def test_serious_objections_are_surfaced_even_when_it_passes(self):
        cs = [g.Critique("model-b", g.SERIOUS, "the control group is wrong")]
        r = g.check_critique(cs, "model-a", models_available=2)
        assert r.verdict == g.PASS
        assert "serious" in r.reason


class TestAdjudication:

    def test_unknown_outranks_pass(self):
        status, _ = g.adjudicate([g.GateResult("a", g.PASS, ""),
                                  g.GateResult("b", g.UNKNOWN, "no embeddings")])
        assert status == g.UNVERIFIED

    def test_fail_outranks_unknown(self):
        status, _ = g.adjudicate([g.GateResult("a", g.UNKNOWN, ""),
                                  g.GateResult("b", g.FAIL, "")])
        assert status == g.REJECTED

    def test_all_pass_is_standing(self):
        status, _ = g.adjudicate([g.GateResult("a", g.PASS, ""),
                                  g.GateResult("b", g.PASS, "")])
        assert status == g.STANDING

    def test_the_objection_travels_with_the_status(self):
        _, reason = g.adjudicate([g.GateResult("novelty", g.FAIL, "already known")])
        assert "novelty" in reason and "already known" in reason


# --------------------------------------------------------------------------
# Storage and review
# --------------------------------------------------------------------------

def _store(**over):
    args = dict(GOOD, question="what limits battery life", proposer_model="model-a",
                evidence=[g.Evidence(g.FOR, "s1"), g.Evidence(g.AGAINST, "s2")])
    args.update(over)
    return g.record(**args)


RESOLVES = lambda sid: sid in {"s1", "s2"}


class TestReview:

    def test_a_proposal_is_stored_unjudged(self, db):
        hid = _store()
        assert g.get(hid)["status"] == g.PROPOSED, (
            "storing and judging are separate on purpose — a generator's "
            "output must not arrive pre-approved")

    def test_review_runs_every_gate_and_keeps_the_results(self, db, monkeypatch):
        fake_embeddings(monkeypatch, {})
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "small sample")
        data = g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        assert data["status"] == g.STANDING
        assert [x["gate"] for x in data["gates"]] == [
            "structure", "evidence", "novelty", "critique"]

    def test_an_unknown_gate_makes_it_unverified_not_standing(self, db, no_embeddings):
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "small sample")
        data = g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        assert data["status"] == g.UNVERIFIED
        assert "novelty" in data["reason"]

    def test_a_missing_resolver_does_not_wave_citations_through(self, db, monkeypatch):
        """The default resolver resolves nothing, so a caller that forgets to
        pass one gets a rejection rather than a free pass."""
        fake_embeddings(monkeypatch, {})
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "x")
        data = g.review(hid, corpus=["tea"], models_available=2)
        assert data["status"] == g.REJECTED and "citation" in data["reason"]

    def test_a_hypothesis_is_not_compared_against_itself(self, db, monkeypatch):
        """A stored claim is in `listing()`, so a corpus built from Apex's own
        records contains it verbatim. Comparing it with itself scores 1.0 and
        rejects every hypothesis the moment it is saved."""
        fake_embeddings(monkeypatch, {})
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "x")
        data = g.review(hid, corpus=[GOOD["claim"], "tea"],
                        resolves=RESOLVES, models_available=2)
        assert data["status"] == g.STANDING

    def test_reviewing_a_missing_hypothesis_raises(self, db):
        with pytest.raises(ValueError):
            g.review(9999)


class TestARejectionIsADiagnosisNotASentence:

    def test_answering_the_objection_changes_the_verdict(self, db, monkeypatch):
        """"never attacked" is cured by attacking it. A gate whose objection
        could never be answered would push a proposer towards never risking a
        rejectable claim, which is the opposite of the point."""
        fake_embeddings(monkeypatch, {})
        hid = _store()
        first = g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        assert first["status"] == g.REJECTED

        g.add_critique(hid, "model-b", g.MINOR, "small sample")
        second = g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        assert second["status"] == g.STANDING

    def test_a_resolved_hypothesis_is_not_re_judged(self, db, monkeypatch):
        """Once an observation has been made the verdict belongs to the world.
        The gates decide what is worth testing; they do not overrule the test."""
        fake_embeddings(monkeypatch, {})
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "x")
        g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        g.observe(hid, "45C finished within 1% of control", g.MATCHED_REFUTATION)

        again = g.review(hid, corpus=["tea"], models_available=2)   # no resolver
        assert again["status"] == g.REFUTED


# --------------------------------------------------------------------------
# The loop that closes
# --------------------------------------------------------------------------

class TestObservation:

    def _standing(self, monkeypatch):
        fake_embeddings(monkeypatch, {})
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "x")
        g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        return hid

    def test_a_matched_prediction_supports_it(self, db, monkeypatch):
        hid = self._standing(monkeypatch)
        assert g.observe(hid, "45C ended at 92%", g.MATCHED_PREDICTION)["status"] == \
            g.SUPPORTED

    def test_a_matched_refutation_refutes_it(self, db, monkeypatch):
        hid = self._standing(monkeypatch)
        assert g.observe(hid, "45C ended at 99%", g.MATCHED_REFUTATION)["status"] == \
            g.REFUTED

    def test_an_inconclusive_observation_changes_nothing_but_is_kept(self, db, monkeypatch):
        hid = self._standing(monkeypatch)
        assert g.observe(hid, "the rig failed", g.INCONCLUSIVE)["status"] == g.STANDING
        assert len(g.observations_of(hid)) == 1

    def test_support_can_be_overturned_by_one_refutation(self, db, monkeypatch):
        hid = self._standing(monkeypatch)
        g.observe(hid, "92%", g.MATCHED_PREDICTION)
        assert g.observe(hid, "99%", g.MATCHED_REFUTATION)["status"] == g.REFUTED

    def test_a_refutation_is_not_undone_by_later_support(self, db, monkeypatch):
        """The asymmetry is Popper's and it is deliberate. You do not get to
        un-refute a claim by finding a case where it held; a system that
        allowed it would drift towards whatever it had looked at most."""
        hid = self._standing(monkeypatch)
        g.observe(hid, "99%", g.MATCHED_REFUTATION)
        after = g.observe(hid, "92% in a later run", g.MATCHED_PREDICTION)
        assert after["status"] == g.REFUTED
        assert len(g.observations_of(hid)) == 2, "the observation is still recorded"

    def test_an_unverified_hypothesis_can_still_be_tested(self, db, no_embeddings):
        """Novelty being unchecked says nothing about whether the experiment is
        worth running."""
        hid = _store()
        g.add_critique(hid, "model-b", g.MINOR, "x")
        assert g.review(hid, corpus=["tea"], resolves=RESOLVES,
                        models_available=2)["status"] == g.UNVERIFIED
        assert g.observe(hid, "99%", g.MATCHED_REFUTATION)["status"] == g.REFUTED

    def test_an_unjudged_proposal_cannot_be_observed(self, db):
        hid = _store()
        with pytest.raises(ValueError) as e:
            g.observe(hid, "anything", g.MATCHED_PREDICTION)
        assert "not been reviewed" in str(e.value)

    def test_a_rejected_hypothesis_cannot_be_observed(self, db, monkeypatch):
        fake_embeddings(monkeypatch, {})
        hid = _store()
        g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
        assert g.get(hid)["status"] == g.REJECTED
        with pytest.raises(ValueError) as e:
            g.observe(hid, "anything", g.MATCHED_PREDICTION)
        assert "failed a gate" in str(e.value)

    def test_an_unknown_outcome_is_refused(self, db, monkeypatch):
        hid = self._standing(monkeypatch)
        with pytest.raises(ValueError):
            g.observe(hid, "x", "sort of")

    def test_the_transition_is_recorded_on_the_observation(self, db, monkeypatch):
        hid = self._standing(monkeypatch)
        g.observe(hid, "99%", g.MATCHED_REFUTATION)
        o = g.observations_of(hid)[0]
        assert (o["status_before"], o["status_after"]) == (g.STANDING, g.REFUTED)

    def test_refutation_is_described_as_the_system_working(self, db, monkeypatch):
        """A system that reported refutation as failure would quietly stop
        producing refutable claims."""
        hid = self._standing(monkeypatch)
        g.observe(hid, "99%", g.MATCHED_REFUTATION)
        assert "the system working" in g.describe(g.get(hid))


class TestDue:

    def test_it_lists_what_can_be_checked_and_nothing_else(self, db, monkeypatch):
        fake_embeddings(monkeypatch, {})
        standing = _store()
        g.add_critique(standing, "model-b", g.MINOR, "x")
        g.review(standing, corpus=["tea"], resolves=RESOLVES, models_available=2)
        rejected = _store(claim="Cells may degrade when hot")
        g.review(rejected, corpus=["tea"], resolves=RESOLVES, models_available=2)
        unjudged = _store(claim="A third unrelated claim about capacity loss")

        ids = [d["id"] for d in g.due()]
        assert standing in ids
        assert rejected not in ids and unjudged not in ids

    def test_oldest_first_so_an_old_prediction_is_not_buried(self, db, monkeypatch):
        fake_embeddings(monkeypatch, {})
        ids = []
        for n in range(3):
            hid = _store(claim=f"Claim number {n} about cells losing capacity at {40 + n}C")
            g.add_critique(hid, "model-b", g.MINOR, "x")
            g.review(hid, corpus=["tea"], resolves=RESOLVES, models_available=2)
            ids.append(hid)
        assert [d["id"] for d in g.due()] == ids


# --------------------------------------------------------------------------
# Talking to a model
# --------------------------------------------------------------------------

ONE_GOOD = ('[{"claim": "Lithium cells above 40C lose more than 5% capacity in 500 '
            'cycles", "prediction": "The 45C group ends below 95%", "refutation": '
            '"The 45C group finishes within 2% of the 25C control", "method": '
            '"cycle 20 cells per temperature", "evidence_for": ["s1"], '
            '"evidence_against": ["s2"], "searched_contrary": true}]')


def stub_provider(monkeypatch, reply):
    """Replace the model call with a canned reply. `reply` may be a string or a
    callable of (model, system, user)."""
    from agent import provider
    calls = []

    def _complete(model, system, user, max_tokens=2048):
        calls.append({"model": model, "system": system, "user": user})
        return reply(model, system, user) if callable(reply) else reply

    monkeypatch.setattr(provider, "complete", _complete)
    return calls


class TestPropose:

    def test_it_stores_what_the_model_said_and_judges_none_of_it(self, db, monkeypatch):
        stub_provider(monkeypatch, ONE_GOOD)
        ids = g.propose("why do batteries die", model="model-a")
        assert len(ids) == 1
        data = g.get(ids[0])
        assert data["status"] == g.PROPOSED
        assert data["proposer_model"] == "model-a"
        assert data["searched_contrary"] == 1
        assert {(e.stance, e.source_id) for e in g.evidence_of(ids[0])} == \
            {(g.FOR, "s1"), (g.AGAINST, "s2")}

    def test_the_sources_reach_the_model_with_their_ids(self, db, monkeypatch):
        calls = stub_provider(monkeypatch, ONE_GOOD)
        g.propose("q", sources=[{"id": "s1", "text": "a paper about heat"}],
                  model="model-a")
        assert "[s1]" in calls[0]["user"] and "a paper about heat" in calls[0]["user"]

    def test_the_instructions_name_the_rules_that_are_enforced(self):
        """A prompt that asks for something different from what the gates check
        produces a generator that fails constantly for reasons it was never
        told."""
        for phrase in ("may", "might", "does not support", "does not resolve",
                       "searched_contrary"):
            assert phrase in g.PROPOSE_SYSTEM

    def test_prose_instead_of_json_raises_rather_than_returning_nothing(self, db, monkeypatch):
        """If an unparseable reply returned an empty list, "the model replied
        with an essay" and "the model proposed nothing" would be the same
        observable event, and a generator that had stopped working would report
        success with zero results forever."""
        stub_provider(monkeypatch, "Here are some fascinating hypotheses to consider!")
        with pytest.raises(g.GenesisError) as e:
            g.propose("q", model="model-a")
        assert "did not return JSON" in str(e.value)

    def test_malformed_json_raises(self, db, monkeypatch):
        stub_provider(monkeypatch, '[{"claim": "a", ')
        with pytest.raises(g.GenesisError):
            g.propose("q", model="model-a")

    def test_a_json_object_instead_of_an_array_raises(self, db, monkeypatch):
        stub_provider(monkeypatch, '{"claim": "a"}')
        with pytest.raises(g.GenesisError):
            g.propose("q", model="model-a")

    def test_fenced_json_is_accepted(self, db, monkeypatch):
        stub_provider(monkeypatch, "```json\n" + ONE_GOOD + "\n```")
        assert len(g.propose("q", model="model-a")) == 1

    def test_json_wrapped_in_chatter_is_accepted(self, db, monkeypatch):
        stub_provider(monkeypatch, "Sure! " + ONE_GOOD + " Hope that helps.")
        assert len(g.propose("q", model="model-a")) == 1

    def test_an_empty_array_is_not_an_error(self, db, monkeypatch):
        """The model looked and proposed nothing. That is a real answer and a
        different one from a broken reply."""
        stub_provider(monkeypatch, "[]")
        assert g.propose("q", model="model-a") == []

    def test_items_that_are_not_objects_are_reported_not_swallowed(self, db, monkeypatch):
        stub_provider(monkeypatch, '["a hypothesis about heat", "another one"]')
        with pytest.raises(g.GenesisError) as e:
            g.propose("q", model="model-a")
        assert "none of them objects" in str(e.value)

    def test_the_count_is_a_cap(self, db, monkeypatch):
        many = "[" + ", ".join([ONE_GOOD[1:-1]] * 5) + "]"
        stub_provider(monkeypatch, many)
        assert len(g.propose("q", model="model-a", count=2)) == 2


class TestCritiqueWith:

    def _stored(self):
        return _store()

    def test_it_records_what_each_model_said(self, db, monkeypatch):
        hid = self._stored()
        stub_provider(monkeypatch,
                      '{"verdict": "serious", "text": "the control group is wrong"}')
        g.critique_with(hid, ["model-b", "model-c"])
        cs = g.critiques_of(hid)
        assert [c.model for c in cs] == ["model-b", "model-c"]
        assert all(c.verdict == g.SERIOUS for c in cs)

    def test_the_hypothesis_reaches_the_critic_in_full(self, db, monkeypatch):
        hid = self._stored()
        calls = stub_provider(monkeypatch, '{"verdict": "none", "text": "checked"}')
        g.critique_with(hid, ["model-b"])
        sent = calls[0]["user"]
        for part in (GOOD["claim"], GOOD["refutation"], "for:s1", "against:s2"):
            assert part in sent

    def test_a_model_that_fails_is_not_recorded_as_a_clean_review(self, db, monkeypatch):
        """Writing a `none` verdict for a call that errored would manufacture
        an independent review out of a network failure — and `check_critique`
        would then pass on the strength of it."""
        from agent import provider

        def _boom(model, system, user, max_tokens=2048):
            if model == "model-b":
                raise RuntimeError("connection reset")
            return '{"verdict": "minor", "text": "fine"}'

        monkeypatch.setattr(provider, "complete", _boom)
        hid = self._stored()
        g.critique_with(hid, ["model-b", "model-c"])
        assert [c.model for c in g.critiques_of(hid)] == ["model-c"]

    def test_an_unparseable_critique_is_not_recorded(self, db, monkeypatch):
        hid = self._stored()
        stub_provider(monkeypatch, "I think this hypothesis is quite good actually")
        assert g.critique_with(hid, ["model-b"]) == []
        assert g.critiques_of(hid) == []

    def test_an_invented_verdict_is_not_recorded(self, db, monkeypatch):
        hid = self._stored()
        stub_provider(monkeypatch, '{"verdict": "looks-good", "text": "nice"}')
        assert g.critique_with(hid, ["model-b"]) == []
        assert g.critiques_of(hid) == []

    def test_the_critic_is_told_to_attack_not_to_improve(self):
        assert "attacking" in g.CRITIQUE_SYSTEM
        for verdict in g.CRITIQUE_VERDICTS:
            assert verdict in g.CRITIQUE_SYSTEM


class TestCriticSelection:

    def test_it_prefers_anyone_other_than_the_proposer(self, monkeypatch):
        from agent import council
        monkeypatch.setattr(council, "available_members",
                            lambda: [("model-a", "A"), ("model-b", "B")])
        assert g.critic_models("model-a") == ["model-b"]

    def test_a_single_model_install_falls_back_to_itself(self, monkeypatch):
        """Which check_critique then reports as `unknown` rather than as
        independent review — the two halves have to agree about this."""
        from agent import council
        monkeypatch.setattr(council, "available_members", lambda: [("model-a", "A")])
        assert g.critic_models("model-a") == ["model-a"]

    def test_it_does_not_keep_a_second_list_of_configured_providers(self, monkeypatch):
        """agent/council.py already answers "which providers have keys". Two
        answers to that question would drift."""
        from agent import council
        monkeypatch.setattr(council, "available_members", lambda: [("only-me", "X")])
        assert g.critic_models("") == ["only-me"]


class TestCorpus:

    def test_the_source_passages_are_included(self, db, monkeypatch):
        monkeypatch.setattr(longterm, "recall", lambda *a, **k: [])
        texts = g.corpus_for("q", sources=["a passage the model was shown"])
        assert "a passage the model was shown" in texts

    def test_prior_claims_are_included(self, db, monkeypatch):
        monkeypatch.setattr(longterm, "recall", lambda *a, **k: [])
        _store()
        assert GOOD["claim"] in g.corpus_for("q")

    def test_a_hypothesis_can_exclude_itself(self, db, monkeypatch):
        monkeypatch.setattr(longterm, "recall", lambda *a, **k: [])
        hid = _store()
        assert GOOD["claim"] not in g.corpus_for("q", exclude_id=hid)

    def test_memories_are_included(self, db, monkeypatch):
        monkeypatch.setattr(longterm, "recall",
                            lambda *a, **k: [{"content": "a remembered fact"}])
        assert "a remembered fact" in g.corpus_for("q")

    def test_a_failed_lookup_shrinks_the_corpus_rather_than_crashing(self, db, monkeypatch, capsys):
        """And the shrinking is visible: check_novelty reports how many entries
        it compared against, so a silently empty corpus shows up as
        `compared: 0` instead of as a clean pass."""
        def _boom(*a, **k):
            raise RuntimeError("index unreachable")
        monkeypatch.setattr(longterm, "recall", _boom)
        texts = g.corpus_for("q", sources=["still here"])
        assert texts == ["still here"]
        assert "novelty checked against less" in capsys.readouterr().out
