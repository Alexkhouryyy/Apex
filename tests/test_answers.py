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
    monkeypatch.setattr(answers, "_synthesize",
                        lambda q, c, model=None, history=None: reply)


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


# --- dashboard wiring --------------------------------------------------------

def test_research_routes_are_registered():
    from dashboard import server
    paths = {r.path for r in server.app.routes if hasattr(r, "path")}
    assert "/api/research" in paths and "/api/research/save" in paths


def test_frontend_listens_for_the_events_the_engine_emits():
    """The old research path broadcast progress events that no client ever
    handled. This pins the contract so that cannot silently happen again."""
    from pathlib import Path
    app_js = Path(__file__).resolve().parents[1] / "dashboard/static/app.js"
    js = app_js.read_text()
    for phase in ("search", "sources", "reading", "source_done", "ranking",
                  "writing", "result", "error"):
        assert f"research_{phase}" in js, f"frontend ignores research_{phase}"


def test_research_tab_exists_in_the_shell():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "dashboard/static"
    html = (root / "index.html").read_text()
    assert 'data-tab="research"' in html and 'id="tab-research"' in html
    # Cache version must move with any frontend change or clients keep the old
    # bundle and the tab silently does not exist for them.
    assert "v=omni22" in html
    assert "apex-shell-v22" in (root / "sw.js").read_text()


def test_answer_html_is_escaped_before_formatting():
    """The answer is synthesized from arbitrary web pages, so it is untrusted.
    marked.parse() emits raw HTML and must not be what renders it."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()
    fn = js[js.index("function _researchFormat"):]
    fn = fn[:fn.index("\nfunction ")]
    assert "escapeHTML" in fn
    assert "marked.parse" not in fn


# --- surfaces ----------------------------------------------------------------

def test_research_is_a_first_class_tool():
    """deep_research sat in the tool list while the better path needed a two-hop
    list_skills -> run_skill, so the model reliably picked the weaker one."""
    from agent import core
    names = {t["name"] for t in core.TOOLS}
    assert "research" in names
    tool = [t for t in core.TOOLS if t["name"] == "research"][0]
    assert "query" in tool["input_schema"]["properties"]


def test_live_research_delegates_to_the_engine(monkeypatch, tmp_path):
    """The skill must not carry a second, drifting copy of the pipeline."""
    import skills.live_research as lr

    monkeypatch.setattr(lr, "_research_dir", lambda: tmp_path)
    monkeypatch.setattr(answers, "answer", lambda q, depth="standard", on_event=None: {
        "error": "", "answer": "An answer [1].", "uncited_claims": [],
        "dropped_citations": [],
        "sources": [{"n": 1, "url": "https://a.com", "title": "A", "domain": "a.com",
                     "status": answers.OK, "cited": True, "error": ""}],
    })
    out = lr.run({"query": "anything", "depth": "quick"})
    assert "[1]" in out and "https://a.com" in out
    assert list(tmp_path.glob("*.md"))


def test_live_research_reports_engine_failure(monkeypatch):
    import skills.live_research as lr
    monkeypatch.setattr(answers, "answer", lambda q, depth="standard", on_event=None: {
        "error": "no results found", "answer": "", "sources": [],
        "uncited_claims": [], "dropped_citations": [],
    })
    assert "no results found" in lr.run({"query": "x"})


# --- the scoring path (exercised without a real embedding model) -------------

def _fake_embed_factory(vectors):
    """Return an _embed stub producing real float32 blobs, so the numpy path in
    _score_pairs runs for real even where sentence-transformers is absent."""
    import numpy as np

    def _embed(text):
        vec = vectors.get(text)
        if vec is None:
            return None
        return np.array(vec, dtype=np.float32).tobytes()
    return _embed


def test_score_pairs_ranks_by_cosine_similarity(monkeypatch):
    from agent import longterm as _lt

    vectors = {
        "query": [1.0, 0.0],
        "on topic": [1.0, 0.0],       # identical -> 1.0
        "off topic": [0.0, 1.0],      # orthogonal -> 0.0
    }
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory(vectors))
    pairs = [(answers.Source(n=1, url="u"), "on topic"),
             (answers.Source(n=2, url="u"), "off topic")]
    scores = answers._score_pairs("query", pairs)
    assert scores is not None
    assert scores[0] == pytest.approx(1.0, abs=1e-5)
    assert scores[1] == pytest.approx(0.0, abs=1e-5)


def test_score_pairs_returns_none_without_a_model(monkeypatch):
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", lambda t: None)
    pairs = [(answers.Source(n=1, url="u"), "x")]
    assert answers._score_pairs("q", pairs) is None


def test_ranking_prefers_the_relevant_passage(monkeypatch):
    """End of the retrieval story: the passage that actually answers the
    question is what reaches the model, not whatever happened to be first."""
    from agent import longterm as _lt
    vectors = {
        "capital": [1.0, 0.0],
        "Canberra is the capital.": [1.0, 0.0],
        "Unrelated boilerplate text.": [0.0, 1.0],
    }
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory(vectors))
    src = answers.Source(n=1, url="https://a.com", status=answers.OK)
    src.chunks = ["Unrelated boilerplate text.", "Canberra is the capital."]
    selected = answers._rank_chunks("capital", [src])
    assert selected[0][1] == "Canberra is the capital."


# --- follow-ups: the thing that makes it a research session ------------------

HISTORY = [{"query": "what is the capital of Australia",
            "answer": "Canberra is the capital [1]. It was chosen in 1908 [2]."}]


def test_strip_citations_removes_markers_and_tidies_spacing():
    """Prior turns re-enter the prompt; their numbers referred to a different
    turn's sources, so leaving them in invites the model to reuse a number that
    now means something else."""
    assert answers.strip_citations("Canberra is the capital [1]. Chosen in 1908 [2, 3].") \
        == "Canberra is the capital. Chosen in 1908."


def test_history_block_carries_prior_turns_without_markers():
    block = answers._history_block(HISTORY)
    assert "capital of Australia" in block and "Canberra is the capital." in block
    assert "[1]" not in block


def test_history_is_capped_to_recent_turns():
    many = [{"query": f"q{i}", "answer": f"a{i}"} for i in range(10)]
    kept = answers._recent(many)
    assert len(kept) == answers._MAX_HISTORY_TURNS
    assert kept[-1]["query"] == "q9"          # the most recent, not the oldest


def test_incomplete_turns_are_not_treated_as_history():
    assert answers._recent([{"query": "q", "answer": ""}, {"query": "", "answer": "a"}]) == []


def test_rewrite_resolves_a_reference(monkeypatch):
    """'why was it chosen?' retrieves nothing on its own — the entity lives in
    the previous turn. This is the whole mechanism behind a working thread."""
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: "why was Canberra chosen as Australia's capital")
    out = answers.rewrite_query("why was it chosen?", HISTORY)
    assert out == "why was Canberra chosen as Australia's capital"


def test_first_question_is_never_rewritten(monkeypatch):
    """No history, nothing to resolve — and no reason to pay for a model call."""
    from agent import provider
    called = []
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: called.append(1) or "rewritten")
    assert answers.rewrite_query("what is the capital of Australia", []) \
        == "what is the capital of Australia"
    assert called == []


def test_rewrite_failure_falls_back_to_the_original(monkeypatch):
    """A bad rewrite is worse than no rewrite, so it must never block an answer."""
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert answers.rewrite_query("why?", HISTORY) == "why?"


def test_rewrite_rejects_a_runaway_reply(monkeypatch):
    """A model that answers the question instead of rewriting it has
    misunderstood the job; the original query is safer than an essay."""
    from agent import provider
    monkeypatch.setattr(provider, "complete", lambda *a, **k: "Sure! " + "x" * 500)
    assert answers.rewrite_query("why?", HISTORY) == "why?"


def test_rewrite_rejects_an_empty_reply(monkeypatch):
    from agent import provider
    monkeypatch.setattr(provider, "complete", lambda *a, **k: "   ")
    assert answers.rewrite_query("why?", HISTORY) == "why?"


def test_rewrite_strips_quotes_and_extra_lines(monkeypatch):
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: '"Canberra capital history"\nsome trailing note')
    assert answers.rewrite_query("why?", HISTORY) == "Canberra capital history"


def test_followup_searches_the_rewritten_query(monkeypatch):
    """End to end: retrieval must use the resolved question, not the pronoun."""
    seen = {}

    from tools import research as _res
    from agent import provider

    def _search(q, num_results=None):
        seen["query"] = q
        return [{"title": "A", "url": "https://a.com", "snippet": ""}]

    monkeypatch.setattr(_res, "search", _search)
    monkeypatch.setattr(_res, "fetch", lambda u, max_chars=None: "Canberra content. " * 50)
    monkeypatch.setattr(provider, "complete", lambda *a, **k: "Canberra 1908 compromise")
    monkeypatch.setattr(answers, "_synthesize",
                        lambda q, c, model=None, history=None: "Because of a compromise [1].")

    out = answers.answer("why was it chosen?", depth="quick", history=HISTORY)
    assert seen["query"] == "Canberra 1908 compromise"
    assert out["search_query"] == "Canberra 1908 compromise"
    assert out["query"] == "why was it chosen?"      # the user's words are kept
    assert out["error"] == ""


def test_synthesis_gets_history_as_context_not_as_a_source(monkeypatch):
    """Prior turns must reach the writer, labelled as context and stripped of
    markers — otherwise turn two cites turn one's source numbers."""
    captured = {}
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda model, system, user, max_tokens=0: captured.update(
                            {"user": user, "system": system}) or "ok")
    answers._synthesize("why?", "[1] src (https://a.com)\nbody", history=HISTORY)
    assert "EARLIER IN THIS CONVERSATION" in captured["user"]
    assert "never cite it" in captured["user"]
    assert "Canberra is the capital." in captured["user"]
    assert "Canberra is the capital [1]" not in captured["user"]


def test_first_turn_prompt_has_no_history_section(monkeypatch):
    captured = {}
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda model, system, user, max_tokens=0: captured.update(
                            {"user": user}) or "ok")
    answers._synthesize("q", "[1] src\nbody", history=None)
    assert "EARLIER IN THIS CONVERSATION" not in captured["user"]


def test_frontend_handles_the_rewrite_event():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()
    assert "research_rewrite" in js
    assert "_researchThread" in js and "history:" in js
