"""Tests for the cited-answer engine.

These are the tests that make the feature trustworthy rather than merely
impressive. The product claim is "every claim points at a source you can open",
so the cases that matter are the ones where that claim could quietly become
false: a marker pointing at a source that does not exist, a source that failed
to fetch being cited anyway, or a retrieval failure being papered over with
confident prose.
"""
import pytest

import config

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
                        lambda q, c, model=None, history=None, on_token=None: reply)


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
                        lambda q, pairs: ([1.0] * len(pairs),
                                          [None] * len(pairs)))  # tie: cap decides
    selected = answers._rank_chunks("subject", sources)
    per_source = {}
    for p in selected:
        per_source[p.n] = per_source.get(p.n, 0) + 1
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
    assert "v=omni26" in html
    assert "apex-shell-v26" in (root / "sw.js").read_text()


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

def _fake_embed_factory(vectors, default=None):
    """Return an _embed stub producing real float32 blobs, so the numpy path in
    _score_pairs and audit_support runs for real even where
    sentence-transformers is absent."""
    import numpy as np

    def _embed(text):
        vec = vectors.get(text, default)
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
    scored = answers._score_pairs("query", pairs)
    assert scored is not None
    scores, blobs = scored
    # The vectors come back so the grounding audit can reuse them instead of
    # paying to embed the same chunks twice.
    assert len(blobs) == 2 and all(b is not None for b in blobs)
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
    assert selected[0].text == "Canberra is the capital."
    assert selected[0].vec is not None      # carried through for the audit


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
                        lambda q, c, model=None, history=None, on_token=None:
                        "Because of a compromise [1].")

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


# --- the live WebSocket must survive bookkeeping failures --------------------

def test_websocket_survives_device_registration_failure(monkeypatch):
    """Found by driving the real dashboard in a browser, not by any unit test.

    devices.touch() ran unguarded in the WS handler, so one DB error closed the
    socket before the first frame — silently killing the live feed, research
    streaming and council streaming, with nothing on screen to explain it.
    """
    from fastapi.testclient import TestClient
    from dashboard import server
    from agent import devices

    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
    monkeypatch.setattr(server.config, "DASHBOARD_TOKEN", "", raising=False)
    monkeypatch.setattr(devices, "touch",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no such table: devices")))
    monkeypatch.setattr(server, "_awareness_log", None, raising=False)

    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/live?device=abc&kind=web") as ws:
            # The snapshot is the first frame; if registration killed the socket
            # this raises instead.
            assert ws.receive_json()["type"] == "snapshot"


# --- grounding audit: does the cited source actually support the claim? -------
#
# Validation proves [2] is a real fetched source. It cannot prove source 2 says
# this. These pin the gap — and pin the limits, so nobody later mistakes the
# check for fact-checking.

def _passage(n, text, vec):
    import numpy as np
    src = answers.Source(n=n, url=f"https://s{n}.com", status=answers.OK)
    return answers.Passage(source=src, text=text,
                           vec=np.array(vec, dtype=np.float32).tobytes())


def test_claim_citing_an_unrelated_source_is_flagged(monkeypatch):
    """The failure this whole phase exists for: a real source, cited for a claim
    it never made. Every other check in the system passes it."""
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({
        "The tax rate rose to forty percent last year.": [0.0, 1.0],
        "Canberra is the capital of Australia.": [1.0, 0.0],
    }))
    passages = [_passage(1, "Canberra is the capital of Australia.", [1.0, 0.0])]
    weak = answers.audit_support(
        "The tax rate rose to forty percent last year [1].", passages, floor=0.25)
    assert len(weak) == 1
    assert weak[0]["cites"] == [1] and weak[0]["support"] < 0.25


def test_a_well_supported_claim_is_not_flagged(monkeypatch):
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({
        "Canberra is the capital city of Australia.": [1.0, 0.0],
        "Canberra is the capital of Australia.": [1.0, 0.0],
    }))
    passages = [_passage(1, "Canberra is the capital of Australia.", [1.0, 0.0])]
    assert answers.audit_support(
        "Canberra is the capital city of Australia [1].", passages, floor=0.25) == []


def test_a_claim_is_scored_only_against_the_source_it_cites(monkeypatch):
    """Citing [1] must be judged against source 1 alone. Scoring against every
    passage would let an unrelated source rescue a bad citation."""
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({
        "Sydney is the largest city.": [0.0, 1.0],
    }))
    passages = [_passage(1, "Griffin won the design competition.", [1.0, 0.0]),
                _passage(2, "Sydney is the largest city.", [0.0, 1.0])]
    # Cites [1] but matches source 2 — must still be flagged.
    weak = answers.audit_support("Sydney is the largest city [1].", passages, floor=0.25)
    assert len(weak) == 1 and weak[0]["cites"] == [1]
    # Cited correctly, it passes.
    assert answers.audit_support("Sydney is the largest city [2].", passages,
                                 floor=0.25) == []


def test_a_multi_source_claim_passes_if_any_cited_source_supports_it(monkeypatch):
    """[1, 2] means 'these support it' — one genuine supporter is enough."""
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({
        "Sydney is the largest city.": [0.0, 1.0],
    }))
    passages = [_passage(1, "Griffin won the design competition.", [1.0, 0.0]),
                _passage(2, "Sydney is the largest city.", [0.0, 1.0])]
    assert answers.audit_support("Sydney is the largest city [1, 2].", passages,
                                 floor=0.25) == []


def test_markers_are_stripped_before_embedding(monkeypatch):
    """'[1]' is noise in vector space; the claim is what gets scored."""
    seen = []
    from agent import longterm as _lt

    def _embed(text):
        import numpy as np
        seen.append(text)
        return np.array([1.0, 0.0], dtype=np.float32).tobytes()

    monkeypatch.setattr(_lt, "_embed", _embed)
    answers.audit_support("Canberra is the capital of Australia [1].",
                          [_passage(1, "x", [1.0, 0.0])], floor=0.25)
    assert seen and "[1]" not in seen[0]


def test_uncited_sentences_are_not_double_reported(monkeypatch):
    """They are already surfaced as uncited_claims; flagging them again is noise."""
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({}))
    passages = [_passage(1, "Anything at all here.", [1.0, 0.0])]
    assert answers.audit_support(
        "This sentence is long enough to count but carries no citation.",
        passages, floor=0.25) == []


def test_audit_fails_open_without_an_embedding_model(monkeypatch):
    """No model must mean 'no opinion', never an error — and never a false
    all-clear, which is why the caller renders nothing rather than a tick."""
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", lambda t: None)
    passages = [_passage(1, "text", [1.0, 0.0])]
    assert answers.audit_support("A claim of some length here [1].",
                                 passages, floor=0.25) == []


def test_audit_survives_a_broken_embedder(monkeypatch):
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed",
                        lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    assert answers.audit_support("A claim of some length here [1].",
                                 [_passage(1, "t", [1.0, 0.0])], floor=0.25) == []


def test_audit_declines_when_passages_carry_no_vectors():
    """The no-embedding ranking path produces vector-less passages. The audit
    must decline rather than invent a judgement."""
    src = answers.Source(n=1, url="https://a.com", status=answers.OK)
    passages = [answers.Passage(source=src, text="some text", vec=None)]
    assert answers.audit_support("A claim of some length here [1].",
                                 passages, floor=0.25) == []


def test_audit_never_mutates_the_answer(monkeypatch):
    """It is an annotation layer. It may not edit prose or remove a citation."""
    from agent import longterm as _lt
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({
        "Totally unrelated claim goes here.": [0.0, 1.0],
    }))
    text = "Totally unrelated claim goes here [1]."
    before = text
    passages = [_passage(1, "Something else entirely.", [1.0, 0.0])]
    weak = answers.audit_support(text, passages, floor=0.25)
    assert weak and text == before


def test_weak_claims_reach_the_result(monkeypatch):
    """End to end through answer(), on the real pipeline."""
    from agent import longterm as _lt
    _stub(
        monkeypatch,
        results=[{"title": "A", "url": "https://a.com", "snippet": ""}],
        pages={"https://a.com": "Canberra is the capital of Australia. " * 30},
        reply="The tax rate rose to forty percent last year [1].",
    )
    monkeypatch.setattr(_lt, "_embed", _fake_embed_factory({
        "The tax rate rose to forty percent last year.": [0.0, 1.0],
        "q": [1.0, 0.0],
    }, default=[1.0, 0.0]))
    out = answers.answer("q", depth="quick")
    assert len(out["weak_claims"]) == 1
    assert out["answer"] == "The tax rate rose to forty percent last year [1]."


def test_format_markdown_lists_weak_claims_without_certifying_the_rest():
    md = answers.format_markdown({
        "error": "", "answer": "Good claim [1]. Odd claim [2].",
        "sources": [
            {"n": 1, "url": "https://a.com", "title": "A", "domain": "a.com",
             "status": answers.OK, "cited": True, "error": ""},
            {"n": 2, "url": "https://b.com", "title": "B", "domain": "b.com",
             "status": answers.OK, "cited": True, "error": ""},
        ],
        "weak_claims": [{"sentence": "Odd claim [2].", "cites": [2], "support": 0.05}],
    })
    assert "Check these against their source" in md
    assert "Odd claim" in md
    # Asymmetry: nothing anywhere may imply the unflagged claim was verified.
    assert "verified" not in md.lower()


def test_frontend_renders_weak_claims_and_never_a_pass_badge():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()
    fn = js[js.index("function _researchResult"):]
    fn = fn[:fn.index("\nfunction ")]
    assert "weak_claims" in fn
    assert "escapeHTML(w.sentence)" in fn          # untrusted text stays escaped
    # Nothing may certify the claims that pass: clearing the floor only means
    # "topically consistent with the page cited".
    for badge in ("✓", "✔", "checks passed", "all claims verified", "supported ✓"):
        assert badge not in fn


# --- ordinal follow-ups -------------------------------------------------------
#
# "why was it chosen?" already worked. "what about the second one?" did not:
# the rewriter saw prior turns as flat prose, and *second* indexes into
# structure that had been thrown away.

HISTORY_WITH_SOURCES = [{
    "query": "biggest cities in Australia",
    "answer": "Sydney is the largest, then Melbourne, then Brisbane.",
    "sources": [
        {"n": 1, "title": "Sydney — largest city", "domain": "a.com", "status": answers.OK},
        {"n": 2, "title": "Melbourne — second city", "domain": "b.com", "status": answers.OK},
        {"n": 3, "title": "Dead link", "domain": "c.com", "status": answers.FAILED},
    ],
}]


def test_rewriter_is_shown_the_numbered_sources():
    """An ordinal needs a list to index into."""
    block = answers._history_block(HISTORY_WITH_SOURCES, with_sources=True)
    assert "1. Sydney — largest city" in block
    assert "2. Melbourne — second city" in block


def test_unfetched_sources_are_not_offered_to_the_rewriter():
    """A source that failed to fetch was never read; pointing an ordinal at it
    would resolve the question to a page we do not have."""
    block = answers._history_block(HISTORY_WITH_SOURCES, with_sources=True)
    assert "Dead link" not in block


def test_the_writer_never_sees_prior_source_numbers():
    """THE invariant of the whole threading design. Prior [n] means something
    different this turn, so showing the writer an old numbered list is an
    invitation to cite a source that no longer exists under that number."""
    block = answers._history_block(HISTORY_WITH_SOURCES)          # writer path
    assert "Sources shown" not in block
    assert "Melbourne — second city" not in block
    assert "Sydney is the largest, then Melbourne" in block       # prose survives


def test_synthesis_prompt_carries_no_prior_source_list(monkeypatch):
    """Same invariant, asserted where it actually matters — the real prompt."""
    captured = {}
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda model, system, user, max_tokens=0:
                        captured.update({"user": user}) or "ok")
    answers._synthesize("q", "[1] src\nbody", history=HISTORY_WITH_SOURCES)
    assert "Sources shown for that answer" not in captured["user"]
    assert "Melbourne — second city" not in captured["user"]


def test_history_without_sources_still_works():
    """Voice and chat callers pass {query, answer} only."""
    block = answers._history_block(HISTORY, with_sources=True)
    assert "capital of Australia" in block and "Sources shown" not in block


def test_source_list_is_capped():
    many = [{"query": "q", "answer": "a",
             "sources": [{"n": i, "title": f"Title {i}", "status": answers.OK}
                         for i in range(1, 30)]}]
    block = answers._history_block(many, with_sources=True)
    assert f"{answers._MAX_HISTORY_SOURCES}. Title {answers._MAX_HISTORY_SOURCES}" in block
    assert f"{answers._MAX_HISTORY_SOURCES + 1}. Title" not in block


def test_malformed_source_entries_do_not_break_the_rewrite():
    junk = [{"query": "q", "answer": "a",
             "sources": ["not a dict", {}, {"n": 2, "title": "  "}, None]}]
    assert answers._history_block(junk, with_sources=True)  # no raise


def test_ordinal_resolves_end_to_end(monkeypatch):
    """The behaviour this phase exists for."""
    seen = {}
    from agent import provider
    from tools import research as _res

    def _complete(model, system, user, max_tokens=0):
        seen["system"] = system
        seen["user"] = user
        return "Melbourne population size"

    monkeypatch.setattr(provider, "complete", _complete)

    def _search(q, num_results=None):
        seen["searched"] = q
        return [{"title": "M", "url": "https://m.com", "snippet": ""}]

    monkeypatch.setattr(_res, "search", _search)
    monkeypatch.setattr(_res, "fetch", lambda u, max_chars=None: "Melbourne content. " * 40)
    monkeypatch.setattr(answers, "_synthesize",
                        lambda q, c, model=None, history=None, on_token=None:
                        "Melbourne is large [1].")

    out = answers.answer("what about the second one?", depth="quick",
                         history=HISTORY_WITH_SOURCES)
    assert seen["searched"] == "Melbourne population size"
    assert out["search_query"] == "Melbourne population size"
    assert out["query"] == "what about the second one?"
    # The rewriter must have been given both the prose list and the source list.
    assert "Melbourne — second city" in seen["user"]
    assert "Sydney is the largest, then Melbourne" in seen["user"]
    assert "second one" in seen["system"] or "Ordinal" in seen["system"]


def test_frontend_sends_source_titles_in_history():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()
    fn = js[js.index("async function runResearch"):]
    fn = fn[:fn.index("\nasync function ")]
    assert "sources:" in fn and "title: s.title" in fn


# --- CitationGate: the guarantee has to hold mid-stream ----------------------
#
# Streaming puts text on screen exactly where the reader is looking. An invalid
# marker visible for one frame breaks "a citation you can see is a citation that
# resolves" just as thoroughly as one left in the final text.

def _gate(valid=(1, 2)):
    return answers.CitationGate(set(valid))


def _stream(text, valid=(1, 2), chunk=None):
    """Feed `text` through a gate. chunk=None means one shot; chunk=1 means
    character by character."""
    g = _gate(valid)
    if chunk is None:
        out = g.feed(text)
    else:
        out = "".join(g.feed(text[i:i + chunk]) for i in range(0, len(text), chunk))
    return out + g.flush(), g


def test_valid_marker_streams_through():
    out, g = _stream("Canberra is the capital [1].")
    assert out == "Canberra is the capital [1]."
    assert g.cited == {1} and g.dropped == []


def test_invalid_marker_never_appears_in_the_stream():
    out, g = _stream("A claim [9]. Another [1].")
    assert "[9]" not in out
    assert out == "A claim . Another [1]."   # spacing is tidied in the final render
    assert g.dropped == [9]


def test_partly_valid_group_is_repaired_mid_stream():
    out, _ = _stream("Both agree [2, 9].")
    assert out == "Both agree [2]."


def test_chunking_cannot_change_the_output():
    """THE invariant. Network chunk boundaries are arbitrary — a gate that only
    works when a marker arrives whole is a gate that fails in production."""
    text = ("Canberra is the capital [1]. Sydney is largest [2, 9]. "
            "A bad one [7]. See [note] and [1,2] together.")
    whole, _ = _stream(text)
    for size in (1, 2, 3, 5, 7, 13):
        piece, _ = _stream(text, chunk=size)
        assert piece == whole, f"chunk size {size} diverged"


def test_marker_split_across_chunks_is_still_validated():
    g = _gate()
    out = g.feed("Claim [") + g.feed("9") + g.feed("]. Next [1") + g.feed("].")
    assert "[9]" not in out + g.flush()
    assert "[1]" in out


def test_non_citation_brackets_pass_through_untouched():
    for text in ("See [note] here.", "A [markdown](http://x) link.",
                 "Nested [[1]] brackets.", "Empty [] bracket."):
        out, _ = _stream(text)
        assert out == text, text


def test_a_trailing_open_bracket_is_flushed_not_swallowed():
    """Stream ends mid-marker: the text must still be delivered."""
    out, _ = _stream("Ends abruptly [1")
    assert out == "Ends abruptly [1"


def test_an_overlong_run_is_released_rather_than_held():
    """A stray '[' followed by digits must not hold the rest of the stream."""
    long_run = "[" + "1234567890" * 4
    out, _ = _stream(f"Text {long_run} more text")
    assert out.endswith("more text")
    assert "1234567890" in out


def test_gate_agrees_with_the_batch_validator_on_markers():
    """The streamed text and the final text must not disagree about which
    citations are real — only about whitespace tidying."""
    text = "One [1]. Two [9]. Three [2, 9]. Four [3]."
    streamed, g = _stream(text, valid=(1, 2))
    final, cited, dropped = answers.validate_citations(text, {1, 2})
    assert answers._CITE_RE.findall(streamed) == answers._CITE_RE.findall(final)
    assert g.cited == cited
    assert sorted(g.dropped) == sorted(dropped)


# --- streaming through the engine and the provider ---------------------------

def test_answer_streams_gated_tokens(monkeypatch):
    """End to end: tokens arrive, and the invalid marker is not among them."""
    from tools import research as _res

    monkeypatch.setattr(_res, "search", lambda q, num_results=None:
                        [{"title": "A", "url": "https://a.com", "snippet": ""}])
    monkeypatch.setattr(_res, "fetch", lambda u, max_chars=None: "Content here. " * 40)

    def _synth(q, c, model=None, history=None, on_token=None):
        text = "A real claim [1]. A bogus one [9]."
        for i in range(0, len(text), 3):        # awkward boundaries on purpose
            on_token(text[i:i + 3])
        return text

    monkeypatch.setattr(answers, "_synthesize", _synth)
    seen = []
    out = answers.answer("q", depth="quick", on_token=seen.append)

    streamed = "".join(seen)
    assert "[1]" in streamed
    assert "[9]" not in streamed          # never shown, not merely removed later
    assert "[9]" not in out["answer"]
    assert out["dropped_citations"] == [9]


def test_streaming_is_optional(monkeypatch):
    """Voice and skill callers pass no on_token and must be unaffected."""
    _stub(monkeypatch,
          results=[{"title": "A", "url": "https://a.com", "snippet": ""}],
          pages={"https://a.com": "Content. " * 40},
          reply="An answer [1].")
    out = answers.answer("q", depth="quick")
    assert out["answer"] == "An answer [1]."


def test_provider_streaming_falls_back_to_blocking(monkeypatch):
    """A slow answer beats no answer."""
    from agent import provider

    class _BadClient:
        class messages:
            @staticmethod
            def stream(**kw):
                raise RuntimeError("stream unsupported")

    monkeypatch.setattr(provider, "get_client", lambda m: _BadClient())
    monkeypatch.setattr(provider, "complete",
                        lambda m, s, u, max_tokens=0: "fallback answer")
    got = provider.stream_complete("claude-x", "sys", "user", on_token=lambda t: None)
    assert got == "fallback answer"


def test_provider_streaming_yields_tokens(monkeypatch):
    from agent import provider
    import types

    class _Delta:
        type = "text_delta"
        def __init__(self, text): self.text = text

    class _Ev:
        type = "content_block_delta"
        def __init__(self, text): self.delta = _Delta(text)

    class _Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter([_Ev("Hel"), _Ev("lo")])

    client = types.SimpleNamespace(
        messages=types.SimpleNamespace(stream=lambda **kw: _Stream()))
    monkeypatch.setattr(provider, "get_client", lambda m: client)
    seen = []
    got = provider.stream_complete("claude-x", "s", "u", on_token=seen.append)
    assert got == "Hello" and seen == ["Hel", "lo"]


def test_a_broken_token_consumer_does_not_kill_generation(monkeypatch):
    from agent import provider
    import types

    class _Delta:
        type = "text_delta"
        def __init__(self, text): self.text = text

    class _Ev:
        type = "content_block_delta"
        def __init__(self, text): self.delta = _Delta(text)

    class _Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter([_Ev("Hel"), _Ev("lo")])

    client = types.SimpleNamespace(
        messages=types.SimpleNamespace(stream=lambda **kw: _Stream()))
    monkeypatch.setattr(provider, "get_client", lambda m: client)
    got = provider.stream_complete(
        "claude-x", "s", "u",
        on_token=lambda t: (_ for _ in ()).throw(RuntimeError("ws gone")))
    assert got == "Hello"


def test_frontend_streams_via_textcontent_not_innerhtml():
    """Model text is untrusted until the final escaped render."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()
    fn = js[js.index("function _researchToken"):]
    fn = fn[:fn.index("\nfunction ")]
    assert "textContent +=" in fn
    assert "innerHTML +=" not in fn
    assert "research_token" in js
