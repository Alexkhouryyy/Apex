"""Offline Engram selection benchmark. Never reads or writes the live memory DB."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

import numpy as np

from agent._vendor.engram.embed import HashEmbedder, top_k

SCHEMA = "apex-memory-selection/v1"
BUDGETS = (300, 1000, 4000)
UPSTREAM = "2080752aaabb38b56a28f2e68429cfde18b6739d"


def _date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def render(unit):
    """Preserve the original text and its provenance; dates describe when it was said."""
    return f"[{unit['at']}] {unit['speaker']}: {unit['text']}"


def shortlist(case, representation, k=30):
    """Scope, revocation and time filtering happen before embedding or scoring."""
    _positive(k, "shortlist size")
    scope, query = case["scope"], case["query"]
    if not isinstance(scope, str) or not scope or not isinstance(query, str) or not query:
        raise ValueError("scope and query must be nonempty strings")
    as_of = _date(case["as_of"])
    units = case[representation]["units"]
    seen, eligible = set(), []
    for unit in units:
        ident = unit["id"]
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError("unit IDs must be unique nonempty strings")
        seen.add(ident)
        if type(unit["revoked"]) is not bool:
            raise ValueError("revoked must be an explicit boolean")
        at = _date(unit["at"])
        if unit["scope"] != scope or unit["revoked"] or at > as_of:
            continue
        if any(not isinstance(unit[key], str) or not unit[key] for key in ("text", "speaker")):
            raise ValueError("text and speaker must be nonempty strings")
        eligible.append(unit)
    ids = [u["id"] for u in eligible]
    embedding = case["embedding"]
    if embedding == "engram-hash-256/offline-only":
        embedder = HashEmbedder()
        vector, matrix = embedder.embed([query])[0], embedder.embed([render(u) for u in eligible])
    else:
        if not isinstance(embedding, str) or not embedding:
            raise ValueError("embedding model provenance is required")
        vector = np.asarray(case["query_embedding"], dtype=float)
        if (vector.ndim != 1 or not len(vector) or not np.isfinite(vector).all()
                or not np.isclose(np.linalg.norm(vector), 1, atol=1e-5)):
            raise ValueError("query embedding must be a finite nonempty vector")
        matrix = (np.asarray([u["embedding"] for u in eligible], dtype=float)
                  if eligible else np.empty((0, len(vector))))
        if matrix.shape != (len(eligible), len(vector)):
            raise ValueError("unit embeddings must have the query vector's dimension")
        if not np.isfinite(matrix).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-5):
            raise ValueError("embeddings must be finite and normalized")
        if len(matrix) and not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-5):
            raise ValueError("embeddings must be normalized")
    order = top_k(vector, ids, matrix, k)
    lookup = {u["id"]: u for u in eligible}
    return [lookup[ident] for ident, _ in order], eligible


def snapshot_id(case, representation, candidates):
    payload = {"query": case["query"], "scope": case["scope"], "as_of": case["as_of"],
               "representation": representation, "candidates": candidates,
               "embedder": case["embedding"], "query_embedding": case.get("query_embedding"),
               "upstream": UPSTREAM}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _rerank(case, representation, candidates):
    """Replay supplied scores; never invent Jev probabilities or make API calls."""
    snapshot = case[representation]["scores"]
    if snapshot["snapshot_id"] != snapshot_id(case, representation, candidates):
        raise ValueError("score snapshot does not match this exact shortlist")
    for key in ("source", "model"):
        if not isinstance(snapshot[key], str) or not snapshot[key]:
            raise ValueError(f"score {key} is required")
    probs = snapshot["probabilities"]
    if set(probs) != {u["id"] for u in candidates}:
        raise ValueError("scores must cover exactly the eligible shortlist")
    for p in probs.values():
        if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("relevance probabilities must be finite and in [0,1]")
    # Engram's registered threshold and sort for raw turns (belief defaults to 1).
    # No relation pull, graph expansion, floor, or extraction is performed here.
    return sorted((u for u in candidates if probs[u["id"]] > 0.5),
                  key=lambda u: (-probs[u["id"]], f"{u['speaker']}: {u['text']}"))


def pack(units, budget):
    """Whole lines only. UTF-8 bytes conservatively bound byte-BPE token counts."""
    _positive(budget, "budget")
    selected, used = [], 0
    for unit in units:
        cost = len((render(unit) + "\n").encode("utf-8"))
        if used + cost <= budget:
            selected.append(unit)
            used += cost
    return selected, used


def evaluate(cases, budgets=BUDGETS, k=30):
    """Compare raw/extracted representations and cosine/replayed reranking."""
    cases = list(cases)
    budgets = tuple(budgets)
    if not budgets or len(set(budgets)) != len(budgets):
        raise ValueError("budgets must be nonempty and unique")
    for budget in budgets:
        _positive(budget, "budget")
    results, seen = [], set()
    for case in cases:
        ident = case["id"]
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError("case IDs must be unique nonempty strings")
        seen.add(ident)
        if type(case["answerable"]) is not bool or not case.get("label_source"):
            raise ValueError("answerable and external label_source are required")
        for representation in ("raw", "extracted"):
            candidates, eligible = shortlist(case, representation, k)
            gold_list = case[representation]["gold_ids"]
            gold = set(gold_list)
            if len(gold) != len(gold_list) or not gold <= {u["id"] for u in eligible}:
                raise ValueError("gold IDs must be unique and eligible")
            if bool(gold) != case["answerable"]:
                raise ValueError("answerable cases require evidence; unanswerable cases require none")
            ranked = _rerank(case, representation, candidates)
            for arm, units in (("cosine", candidates), ("replayed-rerank", ranked)):
                for budget in budgets:
                    selected, used = pack(units, budget)
                    ids = [u["id"] for u in selected]
                    results.append({"case_id": ident, "representation": representation,
                                    "arm": arm, "budget": budget, "used_bytes": used,
                                    "score_source": case[representation]["scores"]["source"],
                                    "score_model": case[representation]["scores"]["model"],
                                    "selected_ids": ids, "shortlist_size": len(candidates),
                                    "evidence_recall": len(gold & set(ids)) / len(gold) if gold else None,
                                    "unanswerable_context_present": bool(ids) if not gold else None,
                                    "snapshot_id": snapshot_id(case, representation, candidates)})
    if not seen:
        raise ValueError("at least one case is required")
    return {"schema": SCHEMA, "upstream": UPSTREAM,
            "embedding_models": sorted({c["embedding"] for c in cases}),
            "budget_accounting": "UTF-8 bytes including timestamp, speaker and newline; byte-BPE upper bound",
            "answer_accuracy_measured": False, "case_count": len(seen), "results": results}
