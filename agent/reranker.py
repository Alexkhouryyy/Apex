"""The learning loop — best-of-n reranking from logged preferences.

Apex has always LOGGED preference signal and never consumed it. Thumbs up/down
sat in `turn_feedback`, blind model preferences sat in `model_preferences`, and
neither ever changed what Apex did next. This module closes that arrow.

Per the decision record (e): reranker now, fine-tune gated. Given n candidate
responses, score each against what the user has historically approved of and
return the best. No training, no GPU, no new dependency — it reuses the
sentence-transformers embeddings `longterm` already computes.

THE REWARD
    score(text) = mean_similarity(text, liked) - mean_similarity(text, disliked)

Liked/disliked response texts come from joining `turn_feedback` (the rating) to
`turn_log` (the actual assistant text), which is why this needs no new capture.
A per-model prior from `model_preferences` is blended in when the candidate's
model is known.

HONEST LIMITS — read before trusting this:
  - Cold start is real. With no feedback the reward is undefined; rerank() says
    so (`learned=False`) and preserves the original order rather than inventing
    a preference. It cannot help until you have actually rated things.
  - This learns *resemblance to approved answers*, not correctness. It will
    happily prefer a confident wrong answer that looks like your liked ones.
    It is a taste model, not a truth model.
  - Best-of-n costs n× generation. That is a real bill on an always-on agent,
    which is why it is opt-in and n is small.
  - It can degenerate (e.g. learning "longer is better"). `stats()` exists so
    that is detectable rather than invisible.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import numpy as np

from agent import longterm

# Blend weights: the learned signal dominates; the model prior only breaks ties.
_W_PREFERENCE = 1.0
_W_MODEL_PRIOR = 0.25

_MIN_SAMPLES = 3          # below this the reward is too noisy to act on
_MAX_SAMPLES = 200        # cap the index so scoring stays fast
_CACHE_TTL_S = 300.0

_index_cache: dict = {"ts": 0.0, "liked": [], "disliked": []}


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS rerank_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            REAL NOT NULL,
                session_id    INTEGER,
                turn_index    INTEGER,
                n_candidates  INTEGER NOT NULL,
                chosen_index  INTEGER NOT NULL,
                reordered     INTEGER NOT NULL DEFAULT 0,
                learned       INTEGER NOT NULL DEFAULT 0,
                scores_json   TEXT DEFAULT '',
                chosen_model  TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rerank_ts ON rerank_events(ts DESC)")


# --- the preference index ----------------------------------------------------

def _rated_responses(limit: int = _MAX_SAMPLES) -> tuple[list[str], list[str]]:
    """(liked, disliked) assistant texts, newest first.

    Joins the rating in turn_feedback to the actual text in turn_log — the text
    is not stored alongside the rating, which is why this join is the whole trick.
    """
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT f.rating, t.content_json FROM turn_feedback f "
                "JOIN turn_log t ON t.session_id = f.session_id "
                "AND t.turn_index = f.turn_index "
                "WHERE t.role = 'assistant' ORDER BY f.ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        return [], []

    liked, disliked = [], []
    for rating, content_json in rows:
        try:
            text = (json.loads(content_json) or {}).get("text", "")
        except Exception:
            text = ""
        if not text or not text.strip():
            continue
        (liked if rating > 0 else disliked).append(text)
    return liked, disliked


def _embed_all(texts: list[str]) -> list[bytes]:
    out = []
    for t in texts:
        blob = longterm._embed(t)
        if blob:
            out.append(blob)
    return out


def build_index(force: bool = False) -> dict:
    """Embed rated responses. Cached — this is the only expensive part."""
    now = time.time()
    if not force and (now - _index_cache["ts"]) < _CACHE_TTL_S and _index_cache["ts"]:
        return _index_cache
    liked_txt, disliked_txt = _rated_responses()
    _index_cache.update({
        "ts": now,
        "liked": _embed_all(liked_txt),
        "disliked": _embed_all(disliked_txt),
    })
    return _index_cache


def sample_count() -> int:
    idx = build_index()
    return len(idx["liked"]) + len(idx["disliked"])


def is_learned() -> bool:
    """True once there is enough rated history for the reward to mean anything."""
    return sample_count() >= _MIN_SAMPLES


# --- scoring -----------------------------------------------------------------

def score_text(text: str) -> tuple[float, bool]:
    """Return (score, learned). Higher = closer to what the user approves of."""
    idx = build_index()
    liked, disliked = idx["liked"], idx["disliked"]
    if (len(liked) + len(disliked)) < _MIN_SAMPLES:
        return 0.0, False
    blob = longterm._embed(text or "")
    if not blob:
        return 0.0, False
    vec = np.frombuffer(blob, dtype=np.float32)
    pos = longterm._cosine_scores(vec, liked) if liked else []
    neg = longterm._cosine_scores(vec, disliked) if disliked else []
    return (float(np.mean(pos)) if pos else 0.0) - (float(np.mean(neg)) if neg else 0.0), True


def model_prior(model: str) -> float:
    """Win rate for a model from blind comparisons, centred on 0."""
    if not model:
        return 0.0
    try:
        from agent import compare
        for row in compare.leaderboard().get("rows", []):
            if row.get("model") == model:
                return (row.get("win_rate", 0) / 100.0) - 0.5
    except Exception:
        pass
    return 0.0


# --- rerank ------------------------------------------------------------------

def rerank(candidates: list[dict]) -> dict:
    """Pick the best of n candidates.

    candidates: [{"text": str, "model": str (optional)}, ...]
    Returns {chosen_index, chosen, scores, learned, reordered}.

    With too little feedback this returns index 0 unchanged and learned=False —
    it declines to invent a preference rather than pretending to have one.
    """
    if not candidates:
        return {"chosen_index": -1, "chosen": None, "scores": [], "learned": False,
                "reordered": False}
    if len(candidates) == 1:
        return {"chosen_index": 0, "chosen": candidates[0], "scores": [0.0],
                "learned": False, "reordered": False}

    scores, learned_any = [], False
    for cand in candidates:
        s, learned = score_text(cand.get("text", ""))
        learned_any = learned_any or learned
        total = _W_PREFERENCE * s + _W_MODEL_PRIOR * model_prior(cand.get("model", ""))
        scores.append(round(total, 4))

    if not learned_any:
        # No usable history — keep the caller's order rather than guessing.
        return {"chosen_index": 0, "chosen": candidates[0], "scores": scores,
                "learned": False, "reordered": False}

    best = max(range(len(scores)), key=lambda i: scores[i])
    return {"chosen_index": best, "chosen": candidates[best], "scores": scores,
            "learned": True, "reordered": best != 0}


def record(result: dict, session_id: Optional[int] = None,
           turn_index: Optional[int] = None) -> None:
    """Log a rerank decision so its effect can be measured later. Best-effort."""
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO rerank_events (ts, session_id, turn_index, n_candidates, "
                "chosen_index, reordered, learned, scores_json, chosen_model) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), session_id, turn_index, len(result.get("scores", [])),
                 result.get("chosen_index", -1), 1 if result.get("reordered") else 0,
                 1 if result.get("learned") else 0,
                 json.dumps(result.get("scores", [])),
                 (result.get("chosen") or {}).get("model", "")),
            )
    except Exception:
        pass


# --- measurement: is this actually helping? ----------------------------------

def stats(days: int = 30) -> dict:
    """Compare feedback on turns we REORDERED vs turns we left alone.

    This is the honest scoreboard. If `approval_when_reordered` is not clearly
    above `approval_when_not_reordered`, the reranker is not earning its cost.
    """
    cutoff = time.time() - days * 86400
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT r.reordered, f.rating FROM rerank_events r "
                "JOIN turn_feedback f ON f.session_id = r.session_id "
                "AND f.turn_index = r.turn_index WHERE r.ts >= ?",
                (cutoff,),
            ).fetchall()
            total = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(reordered),0) FROM rerank_events WHERE ts >= ?",
                (cutoff,),
            ).fetchone()
    except Exception:
        return {"events": 0, "reordered": 0, "samples": sample_count(), "learned": False,
                "rated": 0, "approval_when_reordered": None,
                "approval_when_not_reordered": None}

    up_re = tot_re = up_no = tot_no = 0
    for reordered, rating in rows:
        if reordered:
            tot_re += 1; up_re += 1 if rating > 0 else 0
        else:
            tot_no += 1; up_no += 1 if rating > 0 else 0

    pct = lambda u, t: round(100 * u / t) if t else None
    return {
        "events": total[0] if total else 0,
        "reordered": total[1] if total else 0,
        "samples": sample_count(),
        "learned": is_learned(),
        "rated": len(rows),
        "approval_when_reordered": pct(up_re, tot_re),
        "approval_when_not_reordered": pct(up_no, tot_no),
    }
