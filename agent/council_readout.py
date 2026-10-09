"""Council traces and independently labeled evaluation using real WDH metrics.

Live conversations have no gold outcome: correctness stays null. Closed-answer
replay labels come from a separate reference file, never the chair's confidence.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from agent._vendor.when_debate_helps.metrics import (
    compute_readout_metrics, majority_answer, round_metrics,
)

SCHEMA = "apex-council-readout/v1"
VERIFY_DEBATE = (
    " Verify the decisive claims against the supplied evidence. Identify which "
    "assumptions differ and what would settle them. Agreement is not proof: "
    "retain a supported minority answer. If a claim cannot be verified from the "
    "available evidence, say so; do not invent a source or verification result."
)
VERIFY_CHAIR = (
    " Separate proposal coverage from selecting the final answer. Compare the "
    "independent opening proposals with the later revisions. Check the decisive "
    "reasoning, evidence and assumptions of minority proposals before rejecting "
    "them. Do not count repeated assertions as independent evidence or infer "
    "correctness from unanimity. State unresolved verification gaps."
)


def make_trace(question, members, transcript, final_response, *, errors=None,
               final_error=False, verify_readout=False):
    """Preserve opening proposals and revisions in upstream-compatible rounds."""
    models = {label: model for model, label in members}
    errors = errors or {}
    rounds = []
    for number in sorted({entry["round"] for entry in transcript}):
        agents = []
        for entry in transcript:
            if entry["round"] != number:
                continue
            label = entry["label"]
            agents.append({
                "candidate_id": models[label], "label": label,
                "response": entry["text"], "answer": None, "correct": None,
                "status": "error" if label in errors.get(number, {}) else "ok",
            })
        # Stable ordering even when providers finish in a different order.
        order = {model: i for i, (model, _) in enumerate(members)}
        agents.sort(key=lambda agent: order[agent["candidate_id"]])
        rounds.append({"round": number, "agents": agents, "vote": None,
                       "vote_correct": None, "proposal_hit": None})
    return {
        "schema_version": SCHEMA, "example_id": uuid4().hex,
        "question": question, "evaluation_status": "unscored",
        "readout_mode": "verify" if verify_readout else "standard",
        "initial_correct": None, "initial_vote": None,
        "initial_vote_correct": None, "rounds": rounds,
        "final_response": final_response,
        "final_status": "error" if final_error else "ok",
        "final_answer": None, "final_correct": None,
    }


def _answer(value):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("Canonical answers must be nonempty strings or null abstentions.")
    return value


def score_trace(trace, reference):
    """Apply independent exact-answer labels; reject incomplete adjudication.

    reference = {example_id, gold, reference_source, round_answers:
                 {'0': {candidate_id: canonical_answer_or_null}}, final_answer}
    Gold is never passed into generation. Successful responses require a label
    (including an explicit null abstention); failed responses cannot cast a vote.
    """
    if trace.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported Council trace schema.")
    if reference.get("example_id") != trace.get("example_id"):
        raise ValueError("Reference and trace example IDs must match.")
    source = reference.get("reference_source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("An independent reference_source is required.")
    gold = _answer(reference.get("gold"))
    if gold is None:
        raise ValueError("A verified gold answer is required.")
    rounds = trace.get("rounds")
    if not rounds or [r["round"] for r in rounds] != list(range(len(rounds))):
        raise ValueError("A trace must include contiguous rounds starting with the independent opening.")
    supplied = reference.get("round_answers")
    if not isinstance(supplied, dict) or set(supplied) != {str(r["round"]) for r in rounds}:
        raise ValueError("Every round requires an independent answer map.")
    out = deepcopy(trace)
    for item in out["rounds"]:
        labels = supplied[str(item["round"])]
        if not isinstance(labels, dict):
            raise ValueError("Round labels must map candidate IDs to answers.")
        ids = [a["candidate_id"] for a in item["agents"]]
        if not ids or len(set(ids)) != len(ids) or set(labels) != set(ids):
            raise ValueError("Each candidate requires exactly one label per round.")
        for agent in item["agents"]:
            prediction = _answer(labels[agent["candidate_id"]])
            if agent["status"] not in {"ok", "error"}:
                raise ValueError("Unknown candidate status.")
            if agent["status"] == "error" and prediction is not None:
                raise ValueError("A failed candidate cannot supply an answer.")
            agent["answer"] = prediction
            agent["correct"] = prediction is not None and prediction == gold
        item["vote"] = majority_answer(a["answer"] for a in item["agents"])
        item["vote_correct"] = item["vote"] is not None and item["vote"] == gold
        item["proposal_hit"] = any(a["correct"] for a in item["agents"])
    if "final_answer" not in reference:
        raise ValueError("Final response requires an independent answer label.")
    final = _answer(reference["final_answer"])
    if out.get("final_status") not in {"ok", "error"}:
        raise ValueError("Unknown final response status.")
    if out["final_status"] == "error" and final is not None:
        raise ValueError("A failed finalizer cannot supply an answer.")
    out.update({
        "gold": gold, "reference_source": source, "evaluation_status": "scored",
        "initial_correct": [a["correct"] for a in out["rounds"][0]["agents"]],
        "initial_vote": out["rounds"][0]["vote"],
        "initial_vote_correct": out["rounds"][0]["vote_correct"],
        "final_answer": final, "final_correct": final is not None and final == gold,
    })
    return out


def summarize(records):
    """Only fully scored adapter records may reach upstream's bool coercions."""
    if not records:
        raise ValueError("At least one scored trace is required.")
    ids = [r.get("example_id") for r in records]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Duplicate or missing example IDs would distort the evaluation.")
    for record in records:
        if record.get("evaluation_status") != "scored":
            raise ValueError("Unscored live traces cannot be used as correctness evidence.")
        flags = record.get("initial_correct")
        if (not isinstance(flags, list) or not flags or any(type(v) is not bool for v in flags)
                or type(record.get("initial_vote_correct")) is not bool
                or type(record.get("final_correct")) is not bool):
            raise ValueError("Correctness labels must be actual booleans.")
        # Recompute from their original canonical labels: don't trust prefilled
        # correctness flags (upstream would treat the string 'false' as true).
        reference = {
            "example_id": record["example_id"], "gold": record.get("gold"),
            "reference_source": record.get("reference_source"),
            "round_answers": {str(r["round"]): {a["candidate_id"]: a["answer"]
                              for a in r["agents"]} for r in record["rounds"]},
            "final_answer": record.get("final_answer"),
        }
        checked = score_trace(record, reference)
        if checked != record:
            raise ValueError("Scored labels are inconsistent with canonical answers.")
    metrics = compute_readout_metrics(records)
    metrics["rounds"] = round_metrics(records)
    metrics["headroom_recovered"] = metrics["counts"]["recovered"]
    metrics["vote_correct_damage"] = metrics["counts"]["damaged"]
    return metrics
