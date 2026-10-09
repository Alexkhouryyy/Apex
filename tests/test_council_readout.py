"""Known-answer cases exercise supply, recovery, damage, and abstention."""
from copy import deepcopy
import json

import pytest

from agent import council, council_readout as readout, council_stats, consensus
from scripts.council_readout import evaluate_files

MEMBERS = [("model-1", "One"), ("model-2", "Two"), ("model-3", "Three")]


def case(initial, final, *, gold="A", extra_round=None):
    transcript = [{"round": 0, "label": label, "text": "independent reasoning"}
                  for _, label in MEMBERS]
    if extra_round is not None:
        transcript += [{"round": 1, "label": label, "text": "revised reasoning"}
                       for _, label in MEMBERS]
    trace = readout.make_trace("closed-answer question", MEMBERS, transcript, "final reasoning")
    reference = {"example_id": trace["example_id"], "gold": gold,
                 "reference_source": "locked-hand-checked-fixture/v1",
                 "round_answers": {"0": dict(zip([m for m, _ in MEMBERS], initial))},
                 "final_answer": final}
    if extra_round is not None:
        reference["round_answers"]["1"] = dict(zip([m for m, _ in MEMBERS], extra_round))
    return trace, reference


def test_correct_minority_recovered_and_majority_damaged():
    recovered = readout.score_trace(*case(["B", "B", "A"], "A"))
    damaged = readout.score_trace(*case(["A", "A", "B"], "B"))
    new = readout.score_trace(*case(["B", "B", "B"], "A"))
    retained = readout.score_trace(*case(["A", "A", "A"], "A"))
    metrics = readout.summarize([recovered, damaged, new, retained])
    assert metrics["proposal_hit"] == .75
    assert metrics["recoverable_headroom"] == .25
    assert metrics["recovery_rate"] == 1
    assert metrics["headroom_recovered"] == 1
    assert metrics["vote_correct_damage"] == 1
    assert metrics["damage_rate"] == .5
    assert metrics["gain"] == .25 == metrics["decomposition_gain"]


def test_zero_denominators_are_unknown_not_zero():
    record = readout.score_trace(*case([None, None, None], None))
    metrics = readout.summarize([record])
    assert metrics["recovery_rate"] is None
    assert metrics["damage_rate"] is None
    assert record["initial_vote"] is None


def test_tied_votes_and_round_metrics_keep_opening_baseline():
    record = readout.score_trace(*case(["A", "B", None], "B", extra_round=["B", "B", "B"]))
    metrics = readout.summarize([record])
    assert record["initial_vote"] is None
    assert record["rounds"][1]["vote"] == "B"
    assert metrics["proposal_hit"] == 1
    assert metrics["rounds"][0]["valid_vote_rate"] == 0
    assert metrics["rounds"][1]["valid_vote_rate"] == 1


def test_scoring_never_mutates_live_trace_or_reference():
    trace, reference = case(["A", "B", "B"], "A")
    original_trace, original_reference = deepcopy(trace), deepcopy(reference)
    readout.score_trace(trace, reference)
    assert trace == original_trace and reference == original_reference
    assert trace["initial_correct"] is None
    assert trace["final_correct"] is None


def test_unscored_live_agreement_cannot_be_correctness():
    trace, _ = case(["A", "A", "A"], "A")
    with pytest.raises(ValueError, match="Unscored"):
        readout.summarize([trace])


@pytest.mark.parametrize("change", [
    lambda ref: ref.update(example_id="wrong"),
    lambda ref: ref.update(gold=None),
    lambda ref: ref.update(reference_source=""),
    lambda ref: ref.pop("final_answer"),
    lambda ref: ref["round_answers"]["0"].pop("model-1"),
    lambda ref: ref["round_answers"]["0"].update(unknown="A"),
    lambda ref: ref["round_answers"].update({"1": {}}),
    lambda ref: ref.update(final_answer=True),
])
def test_incomplete_or_invalid_independent_labels_rejected(change):
    trace, reference = case(["A", "B", "B"], "A")
    change(reference)
    with pytest.raises(ValueError):
        readout.score_trace(trace, reference)


@pytest.mark.parametrize("bad", ["false", 0, 1, None])
def test_loose_bool_coercion_cannot_inflate_scores(bad):
    record = readout.score_trace(*case(["B", "B", "B"], "B"))
    record["final_correct"] = bad
    with pytest.raises(ValueError, match="booleans"):
        readout.summarize([record])


def test_tampered_correctness_rejected():
    record = readout.score_trace(*case(["B", "B", "B"], "B"))
    record["final_correct"] = True
    with pytest.raises(ValueError, match="inconsistent"):
        readout.summarize([record])


def test_duplicate_examples_cannot_be_counted_twice():
    record = readout.score_trace(*case(["A", "B", "B"], "A"))
    with pytest.raises(ValueError, match="Duplicate"):
        readout.summarize([record, record])


def test_failures_cannot_cast_votes_or_become_correct():
    trace, reference = case(["A", "B", None], None)
    trace["rounds"][0]["agents"][2]["status"] = "error"
    trace["final_status"] = "error"
    record = readout.score_trace(trace, reference)
    assert record["initial_correct"] == [True, False, False]
    assert record["final_correct"] is False
    reference["round_answers"]["0"]["model-3"] = "A"
    with pytest.raises(ValueError, match="failed candidate"):
        readout.score_trace(trace, reference)
    reference["round_answers"]["0"]["model-3"] = None
    reference["final_answer"] = "A"
    with pytest.raises(ValueError, match="failed finalizer"):
        readout.score_trace(trace, reference)


def test_cli_evaluation_requires_exact_reference_coverage(tmp_path):
    trace, reference = case(["B", "B", "A"], "A")
    traces, refs = tmp_path / "traces.jsonl", tmp_path / "refs.jsonl"
    traces.write_text(json.dumps(trace) + "\n")
    refs.write_text(json.dumps(reference) + "\n")
    result = evaluate_files(traces, refs)
    assert result["metrics"]["headroom_recovered"] == 1
    refs.write_text("")
    with pytest.raises(ValueError, match="no cases"):
        evaluate_files(traces, refs)


@pytest.fixture
def fake_council(monkeypatch):
    monkeypatch.setattr(council, "available_members", lambda: MEMBERS)
    monkeypatch.setattr(consensus, "agreement", lambda transcript: {"overlap": .5})
    monkeypatch.setattr(council_stats, "record_run", lambda *a, **kw: 1)
    calls = []

    def complete(model, system, prompt, max_tokens):
        calls.append((model, system, prompt))
        if "You are the chair" in system:
            return "Final reasoning\nConfidence: high — judgment\nWhere the council split: minority differs"
        return "opening " + model if "Give your strongest" in system else "revision " + model

    monkeypatch.setattr(council.provider, "complete", complete)
    return calls


def test_real_council_attaches_unscored_trace_and_preserves_independence(fake_council):
    result = council.convene("question", rounds=1, verify_readout=True)
    trace = result.readout_trace
    assert trace["evaluation_status"] == "unscored"
    assert trace["final_correct"] is None
    assert trace["readout_mode"] == "verify"
    assert [a["response"] for a in trace["rounds"][0]["agents"]] == ["opening " + m for m, _ in MEMBERS]
    assert [a["response"] for a in trace["rounds"][1]["agents"]] == ["revision " + m for m, _ in MEMBERS]
    assert len(fake_council) == 7  # 3 opening + 3 revision + chair; no extra judge calls
    for _, system, prompt in fake_council[:3]:
        assert prompt == "question"  # opening members cannot see peers
    assert readout.VERIFY_CHAIR in fake_council[-1][1]
    assert "opening model-3" in fake_council[-1][2]  # minority remains in chair context


def test_standard_mode_remains_a_comparable_baseline(fake_council):
    result = council.convene("question", rounds=1, verify_readout=False)
    assert result.readout_trace["readout_mode"] == "standard"
    assert all(readout.VERIFY_DEBATE not in system for _, system, _ in fake_council)
    assert readout.VERIFY_CHAIR not in fake_council[-1][1]
    assert len(fake_council) == 7


def test_provider_errors_are_metadata_not_false_agreement(fake_council, monkeypatch):
    def complete(model, system, prompt, max_tokens):
        if model == "model-3" or "You are the chair" in system:
            raise RuntimeError("mock provider down")
        return "answer"
    monkeypatch.setattr(council.provider, "complete", complete)
    result = council.convene("question", rounds=0)
    trace = result.readout_trace
    assert trace["rounds"][0]["agents"][2]["status"] == "error"
    assert trace["final_status"] == "error"
    assert trace["final_correct"] is None


def test_trace_failure_cannot_lose_completed_answer(fake_council, monkeypatch):
    monkeypatch.setattr(readout, "make_trace", lambda *a, **kw: (_ for _ in ()).throw(ValueError()))
    result = council.convene("question", rounds=0)
    assert result.final_answer == "Final reasoning"
    assert result.readout_trace is None
