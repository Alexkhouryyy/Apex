"""Tests for the cited-answer engine.

These are the tests that make the feature trustworthy rather than merely
impressive. The product claim is "every claim points at a source you can open",
so the cases that matter are the ones where that claim could quietly become
false: a marker pointing at a source that does not exist, a source that failed
to fetch being cited anyway, or a retrieval failure being papered over with
confident prose.
"""
import pytest

from agent import answers


# --- citation validation: the core guarantee ---------------------------------

def test_valid_markers_survive_untouched():
    text = "Cats sleep a lot [1]. Dogs do too [2]."
    clean, cited, dropped = answers.validate_citations(text, {1, 2})
    assert clean == text
    assert cited == {1, 2} and dropped == []


def test_out_of_range_marker_is_dropped():
    """The headline failure: a model emits [7] when 2 sources exist. An
    unvalidated citation manufactures confidence, which is worse than none."""
    clean, cited, dropped = answers.validate_citations(
        "Cats sleep a lot [7].", {1, 2})
    assert "[7]" not in clean
    assert clean == "Cats sleep a lot."      # no orphaned space before the stop
    assert cited == set() and dropped == [7]


def test_partly_valid_group_is_repaired_not_discarded():
    """[2, 9] with only 2 real must keep the real half — throwing the whole
    group away would lose a citation the model got right."""
    clean, cited, dropped = answers.validate_citations(
        "Both agree on this [2, 9].", {1, 2})
    assert clean == "Both agree on this [2]."
    assert cited == {2} and dropped == [9]


def test_multi_source_group_is_preserved():
    clean, cited, _ = answers.validate_citations("Fused claim [1, 3].", {1, 2, 3})
    assert clean == "Fused claim [1, 3]." and cited == {1, 3}


def test_duplicate_numbers_in_a_group_collapse():
    clean, cited, _ = answers.validate_citations("Odd output [2, 2].", {2})
    assert clean == "Odd output [2]." and cited == {2}


def test_no_valid_sources_strips_every_marker():
    clean, cited, dropped = answers.validate_citations(
        "Claim one [1]. Claim two [2].", set())
    assert "[" not in clean
    assert cited == set() and sorted(dropped) == [1, 2]


# --- uncited claims ----------------------------------------------------------

def test_long_uncited_sentence_is_flagged():
    text = ("The company was founded in 1998 by two graduate students who met "
            "at university.")
    assert answers.find_uncited_claims(text) == [text]


def test_cited_sentence_is_not_flagged():
    text = ("The company was founded in 1998 by two graduate students who met "
            "at university [1].")
    assert answers.find_uncited_claims(text) == []


def test_headings_and_short_fragments_are_not_claims():
    text = "## Background\n\nIt varies.\n\n- Key points:\n"
    assert answers.find_uncited_claims(text) == []


def test_bullet_content_is_still_checked():
    text = "- Revenue grew by roughly forty percent over the last fiscal year."
    assert len(answers.find_uncited_claims(text)) == 1


# --- source bookkeeping ------------------------------------------------------

def test_domain_strips_www():
    assert answers.Source(n=1, url="https://www.example.com/a").domain == "example.com"


def test_malformed_url_does_not_raise():
    assert answers.Source(n=1, url="not a url").domain == ""


# --- end to end, with the network stubbed ------------------------------------

def _stub(monkeypatch, *, results, pages, reply):
    """Wire the engine to fixed search results, page bodies and model output.

    Patches at the seams the engine actually calls, so the test exercises the
    real pipeline rather than a reimplementation of it.
    """
    from tools import research as _res

    monkeypatch.setattr(_res, "search", lambda q, num_results=None: list(results))

    def _fetch(url, max_chars=None):
        if url not in pages:
            raise _res.FetchError(f"{url}: 404")
        return pages[url]

    monkeypatch.setattr(_res, "fetch", _fetch)
    monkeypatch.setattr(answers, "_synthesize", lambda q, c, model=None: reply)


def test_end_to_end_produces_cited_answer(monkeypatch):
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://a.com", "snippet": ""},
                 {"title": "B", "url": "https://b.com", "snippet": ""}],
        pages={"https://a.com": "Cats sleep sixteen hours a day. " * 20,
               "https://b.com": "Dogs sleep twelve hours a day. " * 20},
        reply="Cats sleep about sixteen hours [1]. Dogs sleep fewer [2].",
    )
    out = answers.answer("how long do pets sleep", depth="quick")

    assert out["error"] == ""
    assert "[1]" in out["answer"] and "[2]" in out["answer"]
    assert [s["n"] for s in out["sources"]] == [1, 2]
    assert all(s["cited"] for s in out["sources"])
    assert out["dropped_citations"] == []


def test_a_source_that_failed_to_fetch_is_never_cited(monkeypatch):
    """Source 2 404s, so it has no content — a citation to it would point at
    nothing the model ever read."""
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://a.com", "snippet": ""},
                 {"title": "B", "url": "https://dead.com", "snippet": ""}],
        pages={"https://a.com": "Cats sleep sixteen hours a day. " * 20},
        reply="Cats sleep a lot [1]. Dogs supposedly differ [2].",
    )
    out = answers.answer("pets", depth="quick")

    dead = [s for s in out["sources"] if s["url"] == "https://dead.com"][0]
    assert dead["status"] == answers.FAILED
    assert dead["cited"] is False
    assert "[2]" not in out["answer"]
    assert out["dropped_citations"] == [2]


def test_total_fetch_failure_is_reported_not_invented(monkeypatch):
    """Every page dead must yield an error, never fluent prose about nothing."""
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://dead1.com", "snippet": ""}],
        pages={},
        reply="This should never be reached.",
    )
    out = answers.answer("anything", depth="quick")
    assert out["answer"] == ""
    assert "could not fetch" in out["error"]


def test_no_search_results_is_an_honest_failure(monkeypatch):
    _stub(monkeypatch, results=[], pages={}, reply="x")
    out = answers.answer("obscure", depth="quick")
    assert out["answer"] == "" and "no results" in out["error"]


def test_search_backend_failure_surfaces(monkeypatch):
    from tools import research as _res

    def _boom(q, num_results=None):
        raise _res.SearchError("all backends down")

    monkeypatch.setattr(_res, "search", _boom)
    out = answers.answer("anything")
    assert out["answer"] == "" and "search failed" in out["error"]


def test_empty_query_is_refused():
    out = answers.answer("   ")
    assert out["answer"] == "" and "required" in out["error"]


def test_results_without_urls_are_not_sources(monkeypatch):
    """A row with no URL cannot be cited, so it is not a source."""
    _stub(
        monkeypatch,
        results=[{"title": "no link", "url": "", "snippet": "x"},
                 {"title": "A", "url": "https://a.com", "snippet": ""}],
        pages={"https://a.com": "Content here. " * 50},
        reply="A claim [1].",
    )
    out = answers.answer("q", depth="quick")
    assert [s["url"] for s in out["sources"]] == ["https://a.com"]


def test_engine_works_without_an_embedding_model(monkeypatch):
    """No sentence-transformers → degrade to leading chunks, still answer.

    Reranking improves precision; its absence must not take the feature down.
    """
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://a.com", "snippet": ""}],
        pages={"https://a.com": "Relevant content about the topic. " * 60},
        reply="An answer [1].",
    )
    monkeypatch.setattr(answers, "_score_pairs", lambda q, pairs: None)
    out = answers.answer("q", depth="quick")
    assert out["error"] == "" and "[1]" in out["answer"]


def test_progress_events_are_emitted_in_order(monkeypatch):
    """The dashboard renders source cards before the first answer token, so the
    roster must be emitted before synthesis starts."""
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://a.com", "snippet": ""}],
        pages={"https://a.com": "Content. " * 50},
        reply="Answer [1].",
    )
    seen = []
    answers.answer("q", depth="quick", on_event=lambda p, d: seen.append(p))
    assert seen.index("sources") < seen.index("writing")
    assert seen[0] == "search" and seen[-1] == "done"


def test_a_broken_event_callback_cannot_kill_the_run(monkeypatch):
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://a.com", "snippet": ""}],
        pages={"https://a.com": "Content. " * 50},
        reply="Answer [1].",
    )

    def _bad(phase, payload):
        raise RuntimeError("ws dead")

    # A closed dashboard socket is not a research failure. Progress reporting
    # is a courtesy; the answer is the product.
    out = answers.answer("q", depth="quick", on_event=_bad)
    assert out["error"] == "" and "[1]" in out["answer"]


def test_one_long_page_cannot_monopolise_the_context(monkeypatch):
    """Per-source chunk cap: without it a single long page crowds out every
    other source and the answer ends up single-sourced."""
    long_page = "\n".join(f"Paragraph {i} about the subject matter." * 12
                          for i in range(80))
    sources = [answers.Source(n=1, url="https://a.com"),
               answers.Source(n=2, url="https://b.com")]
    from agent import knowledge as _kb
    sources[0].chunks = _kb._chunk(long_page)
    sources[1].chunks = _kb._chunk("Short but relevant page. " * 40)

    monkeypatch.setattr(answers, "_score_pairs",
                        lambda q, pairs: [1.0] * len(pairs))  # tie: cap decides
    selected = answers._rank_chunks("subject", sources)
    per_source = {}
    for src, _ in selected:
        per_source[src.n] = per_source.get(src.n, 0) + 1
    assert per_source[1] <= answers._MAX_CHUNKS_PER_SOURCE


def test_format_markdown_marks_uncited_and_failed_sources():
    result = {
        "error": "",
        "answer": "Body [1].",
        "sources": [
            {"n": 1, "url": "https://a.com", "title": "A", "domain": "a.com",
             "status": answers.OK, "cited": True, "error": ""},
            {"n": 2, "url": "https://b.com", "title": "B", "domain": "b.com",
             "status": answers.OK, "cited": False, "error": ""},
            {"n": 3, "url": "https://c.com", "title": "C", "domain": "c.com",
             "status": answers.FAILED, "cited": False, "error": "404"},
        ],
    }
    md = answers.format_markdown(result)
    assert "[A](https://a.com)" in md
    assert "not cited" in md
    assert "https://c.com" not in md          # failed sources are not listed
    assert "1 source(s) could not be fetched" in md
