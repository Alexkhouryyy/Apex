import copy
import json
import subprocess
import sys

import numpy as np
import pytest

from agent._vendor.engram.embed import HashEmbedder, top_k
from agent.memory_selection import evaluate, pack, shortlist, snapshot_id


def unit(ident, text="user lives in Paris", **changes):
    return dict(id=ident, text=text, at="2026-10-01T00:00:00Z", speaker="user",
                scope="owner", revoked=False, **changes)


def case():
    out = dict(id="q1", query="where does user live", scope="owner",
               as_of="2026-10-09T00:00:00Z", answerable=True,
               label_source="synthetic-test", embedding="engram-hash-256/offline-only")
    for rep in ("raw", "extracted"):
        out[rep] = dict(units=[unit("a"), unit("b", "user lives for music")], gold_ids=["a"])
    return seal(out)


def seal(out, probabilities=None, k=30):
    for rep in ("raw", "extracted"):
        candidates, _ = shortlist(out, rep, k)
        out[rep]["scores"] = dict(snapshot_id=snapshot_id(out, rep, candidates),
                                  source="synthetic-test", model="test-not-Jev",
                                  probabilities=probabilities or {u["id"]: 0.9 if u["id"] == "a" else 0.1
                                                                  for u in candidates})
    return out


def test_upstream_stable_cosine_and_hash_normalization():
    vectors = HashEmbedder().embed(["Paris", "Paris", "Berlin"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)
    assert [i for i, _ in top_k(vectors[0], ["a", "b", "c"], vectors, 2)] == ["a", "b"]


def test_four_arms_three_budgets_preserve_input():
    data = case()
    before = copy.deepcopy(data)
    report = evaluate(iter([data]))
    assert len(report["results"]) == 12
    assert report["answer_accuracy_measured"] is False
    assert all(r["used_bytes"] <= r["budget"] for r in report["results"])
    assert all(r["selected_ids"] == ["a"] for r in report["results"] if r["arm"] == "replayed-rerank")
    assert data == before


def test_excluded_records_never_enter_score_snapshot():
    data = case()
    data["raw"]["units"] += [dict(unit("private"), scope="guest"),
                              dict(unit("deleted"), revoked=True),
                              dict(unit("future"), at="2027-01-01T00:00:00Z")]
    data = seal(data)
    report = evaluate([data])
    assert all(set(r["selected_ids"]) <= {"a", "b"} for r in report["results"])
    assert data["raw"]["scores"]["probabilities"] == {"a": .9, "b": .1}


def test_exact_threshold_and_unanswerable_proxy():
    data = case()
    data["answerable"] = False
    for rep in ("raw", "extracted"):
        data[rep]["gold_ids"] = []
    data = seal(data, {"a": .5, "b": .1})
    rows = evaluate([data])["results"]
    assert all(r["evidence_recall"] is None for r in rows)
    assert all(r["unanswerable_context_present"] is False for r in rows if r["arm"] == "replayed-rerank")


@pytest.mark.parametrize("p", [True, float("nan"), float("inf"), -1, 1.1])
def test_bad_probabilities_rejected(p):
    data = case()
    data["raw"]["scores"]["probabilities"]["a"] = p
    with pytest.raises(ValueError, match="probabilities"):
        evaluate([data])


@pytest.mark.parametrize("change", ["query", "text", "scope", "as_of", "coverage", "model"])
def test_stale_or_incomplete_scores_rejected(change):
    data = case()
    if change == "text":
        data["raw"]["units"][0]["text"] = "different"
    elif change == "coverage":
        data["raw"]["scores"]["probabilities"].pop("b")
    elif change == "model":
        data["raw"]["scores"]["model"] = ""
    else:
        data[change] += "different" if change != "as_of" else "+00:00"
    with pytest.raises(ValueError):
        evaluate([data])


def test_shortlist_and_gold_recall_not_top_k_answer_claim():
    data = seal(case(), k=1)
    report = evaluate([data], k=1)
    assert all(r["shortlist_size"] == 1 for r in report["results"])


def test_whole_units_utf8_overhead_and_long_unit_skipping():
    units = [unit("long", "x" * 500), unit("short", "é")]
    selected, used = pack(units, 100)
    assert [u["id"] for u in selected] == ["short"]
    assert used == len("[2026-10-01T00:00:00Z] user: é\n".encode())


def test_precomputed_embeddings_and_fingerprint():
    data = case()
    data["embedding"], data["query_embedding"] = "fixture-normalized-v1", [1., 0.]
    for rep in ("raw", "extracted"):
        for u, vec in zip(data[rep]["units"], [[0., 1.], [1., 0.]]):
            u["embedding"] = vec
    data = seal(data)
    assert shortlist(data, "raw")[0][0]["id"] == "b"
    assert evaluate([data])["embedding_models"] == ["fixture-normalized-v1"]
    data["raw"]["units"][0]["embedding"] = [1., 0.]
    with pytest.raises(ValueError, match="snapshot"):
        evaluate([data])


@pytest.mark.parametrize("value", [[0., 0.], [float("nan"), 0.], [[1., 0.]]])
def test_bad_embeddings_rejected(value):
    data = case()
    data["embedding"], data["query_embedding"] = "fixture", value
    with pytest.raises(ValueError):
        shortlist(data, "raw")


def test_revoked_gold_and_duplicate_cases_rejected():
    data = case()
    data["raw"]["units"][0]["revoked"] = True
    with pytest.raises(ValueError, match="gold"):
        evaluate([seal(data)])
    with pytest.raises(ValueError, match="case IDs"):
        evaluate([case(), case()])


def test_cli_offline_and_no_input_overwrite(tmp_path):
    source, output = tmp_path / "cases.jsonl", tmp_path / "report.json"
    source.write_text(json.dumps(case()) + "\n")
    cmd = [sys.executable, "-m", "scripts.memory_selection", "--cases", str(source), "--output"]
    assert subprocess.run(cmd + [str(output)], capture_output=True).returncode == 0
    assert json.loads(output.read_text())["case_count"] == 1
    assert subprocess.run(cmd + [str(source)], capture_output=True).returncode != 0
