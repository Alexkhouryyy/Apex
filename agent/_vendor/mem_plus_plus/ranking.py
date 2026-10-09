"""Mem++ weighted RRF. Changed: standalone default k=60; removed DB imports."""
def _rrf_fuse(ranked_lists, k: int = 60, weights=None) -> dict:
    """Weighted Reciprocal Rank Fusion (weights tunable) — PURE.

    Returns ``{id: rrf_score}`` where the score is ``Σ_lists weight_list · 1/(k +
    rank)`` (rank 0-based). A ``0.0`` weight drops that leg entirely (true no-op).
    With default weights and only the lexical+tag lists non-empty, the fused order
    matches the pre-vector relevance signal exactly.
    """
    scores: dict = {}
    for i, ranked in enumerate(ranked_lists):
        weight = 1.0 if weights is None else (weights[i] if i < len(weights) else 1.0)
        if weight == 0.0:
            continue
        for rank, mem_id in enumerate(ranked):
            scores[mem_id] = scores.get(mem_id, 0.0) + weight / (k + rank)
    return scores
