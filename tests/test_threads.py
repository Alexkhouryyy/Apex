"""Tests for the Threads serendipity engine (deterministic cosine core)."""
import numpy as np
import time

from agent import threads, longterm


def _emb(vec):
    v = np.array(vec, dtype=np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


def _add(kind, content, vec, importance=5):
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO memories (ts, kind, content, importance, tags, embedding) VALUES (?,?,?,?,?,?)",
            (time.time(), kind, content, importance, "", _emb(vec)),
        )
        return cur.lastrowid


def _seed(test_db):
    longterm.init_db()
    threads.init_db()
    a = _add("project", "ship the launch by June", [1, 0, 0])
    c = _add("note", "momentum comes from small wins", [0.6, 0.8, 0])   # cos(a,c)=0.6 → in band, cross-domain
    d = _add("fact", "the capital of France is Paris", [0, 0, 1])       # cos(a,d)=0 → below band
    return a, c, d


def test_discover_finds_cross_domain_band_pair(test_db):
    a, c, d = _seed(test_db)
    found = threads.discover()
    # The A–C pair (score 0.6, different kinds) is the only one in the band.
    assert len(found) == 1
    ids = {found[0]["a"]["id"], found[0]["b"]["id"]}
    assert ids == {a, c}
    assert 0.35 <= found[0]["score"] <= 0.85


def test_related_ranks_by_similarity(test_db):
    a, c, d = _seed(test_db)
    rel = threads.related(a, k=2)
    # C (0.6) must rank above D (0.0).
    assert rel[0]["id"] == c
    assert rel[0]["score"] > rel[1]["score"]


def test_surface_next_records_and_does_not_repeat(test_db):
    a, c, d = _seed(test_db)
    first = threads.surface_next()
    assert first is not None and "thread_id" in first
    # Only one qualifying pair → the second surface returns nothing new.
    assert threads.surface_next() is None


def test_react_and_stats(test_db):
    _seed(test_db)
    t = threads.surface_next()
    assert threads.react(t["thread_id"], "useful") is True
    assert threads.react(t["thread_id"], "bogus") is False  # invalid reaction rejected
    s = threads.stats()
    assert s["surfaced"] == 1 and s["useful"] == 1 and s["hit_rate"] == 100


def test_recent_returns_content(test_db):
    _seed(test_db)
    threads.surface_next()
    r = threads.recent()
    assert r and "content" in r[0]["a"] and "content" in r[0]["b"]
