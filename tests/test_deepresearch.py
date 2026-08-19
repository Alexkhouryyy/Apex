"""Deep research must actually be deep, and every claim must be traceable.

`agent/answers.py` capped at ten sources because its pipeline ends in one model
call. This engine keeps the corpus in SQLite and shows the model notes, so the
source count is bounded by time and money rather than context.

The tests run a synthetic web of ~1,200 pages through the real pipeline. The
search and fetch functions are fakes; everything between them — dedup, parallel
harvest, grounded extraction, gap analysis, sectioned synthesis, citation
validation — is the shipping code.

The check that matters most is grounding. Every note must carry a quote that
occurs verbatim in the page it came from, so a fabricated finding is caught by
string search rather than trusted. A test plants an extractor that invents
quotes and asserts none of them reach the report.
"""
from __future__ import annotations

import re

import pytest

import config
from agent import deepresearch as dr


# ── A synthetic web ───────────────────────────────────────────────────────────

CORPUS_SIZE = 1200


def _page(i: int) -> str:
    return (
        f"Report number {i} on distributed energy storage.\n\n"
        f"Grid-scale batteries in region {i % 40} reached {100 + i} megawatt-hours "
        f"of installed capacity during the reporting period, an increase of "
        f"{i % 90} percent year on year.\n\n"
        f"Analysts at institute {i % 17} note that lithium iron phosphate now "
        f"accounts for {40 + (i % 50)} percent of new installations, displacing "
        f"nickel manganese cobalt chemistries in stationary applications.\n\n"
        f"Critics argue the figures for region {i % 40} exclude behind-the-meter "
        f"systems and therefore understate deployment by roughly {i % 30} percent."
    ) * 2


WEB = {f"https://example.org/report/{i}": _page(i) for i in range(CORPUS_SIZE)}
URLS = list(WEB)


def fake_search(query: str, n: int) -> list[dict]:
    """Deterministic, and overlapping on purpose.

    Different queries return overlapping URLs, exactly as a real search engine
    does. If dedup is broken the same popular page is fetched many times and the
    unique-source count collapses — which is what this feeds.
    """
    seed = sum(ord(c) for c in query)
    out = []
    for k in range(n):
        idx = (seed * 7 + k * 13) % CORPUS_SIZE
        url = URLS[idx]
        out.append({"url": url, "title": f"Report {idx}"})
    return out


def fake_fetch(url: str) -> str:
    if url not in WEB:
        raise RuntimeError("404")
    return WEB[url]


# ── A scripted model ──────────────────────────────────────────────────────────

class FakeClient:
    """Plays planner, query-writer, extractor and writer.

    Quotes are copied out of the document it is shown, which is what a truthful
    extractor does — `UngroundedClient` below does the opposite.
    """

    def __init__(self):
        self.calls = {"plan": 0, "queries": 0, "extract": 0, "write": 0, "gaps": 0}
        self.messages = self

    def create(self, **kw):
        system = kw.get("system", "")
        user = kw["messages"][0]["content"]
        if "planning a research project" in system:
            self.calls["plan"] += 1
            n = int(re.search(r"exactly (\d+)", user).group(1))
            text = _json([f"Facet {i}: what is the evidence on aspect {i}?"
                          for i in range(n)])
        elif "web search queries" in system:
            self.calls["queries"] += 1
            n = int(re.search(r"Give (\d+) queries", user).group(1))
            # Keyed on the whole sub-question, not a prefix. Keying on user[:20]
            # gave every sub-question the string "Sub-question: Facet " and so
            # identical queries, identical search seeds, and 80 unique sources
            # from a corpus of 1200 — the fake, not the engine, was the ceiling.
            sub = user.split("Sub-question:", 1)[1].split("\n")[0].strip()
            text = _json([f"{sub} angle {i}" for i in range(n)])
        elif "auditing a research corpus" in system:
            self.calls["gaps"] += 1
            text = _json([f"Follow-up on the thin area {self.calls['gaps']}"])
        elif "Extract findings" in system:
            self.calls["extract"] += 1
            text = _json(self._findings(user))
        else:
            self.calls["write"] += 1
            text = self._section(user)
        return _Resp(text)

    def _findings(self, user: str) -> list:
        # Spread findings across the sub-questions the way a real extractor
        # would. Always answering the first one produced a single-section
        # report and hid whether sectioning worked at all.
        qids = [int(m) for m in re.findall(r"^(\d+)\. ", user, re.M)]
        doc = user.split("DOCUMENT", 1)[1]
        rep = int(re.search(r"Report number (\d+)", doc).group(1))
        qid = qids[rep % len(qids)]
        m = re.search(r"Grid-scale batteries in region \d+ reached [\d]+ "
                      r"megawatt-hours", doc)
        out = []
        if m:
            out.append({"claim": "Installed grid storage capacity grew.",
                        "quote": m.group(0), "question_id": qid})
        m2 = re.search(r"lithium iron phosphate now accounts for \d+ percent", doc)
        if m2:
            out.append({"claim": "LFP is displacing NMC in stationary storage.",
                        "quote": m2.group(0), "question_id": qid})
        return out

    def _section(self, user: str) -> str:
        ns = re.findall(r"^\[(\d+)\]", user, re.M)[:6]
        cites = "".join(f"[{n}]" for n in ns) or "[1]"
        return (f"Deployment grew across the reporting period{cites}. "
                f"Chemistry mix shifted toward lithium iron phosphate{cites}.")


class UngroundedClient(FakeClient):
    """An extractor that fabricates its quotes."""

    def _findings(self, user: str) -> list:
        qid = int(re.findall(r"^(\d+)\. ", user, re.M)[0])
        return [{"claim": "Capacity tripled overnight in every region.",
                 "quote": "Capacity tripled overnight in every region, say analysts.",
                 "question_id": qid}]


def _json(obj) -> str:
    import json
    return json.dumps(obj)


class _Block:
    type = "text"

    def __init__(self, t):
        self.text = t


class _Usage:
    input_tokens = 500
    output_tokens = 200
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Resp:
    def __init__(self, t):
        self.content = [_Block(t)]
        self.usage = _Usage()
        self.stop_reason = "end_turn"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "r.db"))
    longterm.init_db()
    dr._ready = False
    dr.init_db()
    return tmp_path


@pytest.fixture
def result(db, monkeypatch):
    monkeypatch.setattr(config, "AGENT_MODEL", "claude-opus-5", raising=False)
    monkeypatch.setattr(config, "PROACTIVE_MODEL", "claude-haiku-4-5", raising=False)
    client = FakeClient()
    out = dr.run("How is grid-scale storage deploying?", depth="deep",
                 client=client, search_fn=fake_search, fetch_fn=fake_fetch)
    out["client"] = client
    return out


# ── Scale ─────────────────────────────────────────────────────────────────────

def test_it_reads_hundreds_of_sources_not_ten(result):
    """The complaint that started this: depth='deep' meant ten pages."""
    assert result["sources_read"] > 200, (
        f"only {result['sources_read']} sources read — this is still a search, "
        f"not research"
    )


def test_urls_are_deduplicated_across_queries(result, db):
    from agent import longterm
    with longterm._conn() as c:
        rows = c.execute("SELECT url, COUNT(*) FROM research_sources "
                         "GROUP BY url HAVING COUNT(*) > 1").fetchall()
    assert not rows, f"{len(rows)} URLs stored more than once; harvest would refetch them"


def test_notes_far_outnumber_sources_but_stay_small(result):
    """The reason a thousand pages is tractable: pages become sentences."""
    assert result["notes"] >= result["sources_read"], "extraction produced almost nothing"


def test_many_distinct_sources_are_actually_cited(result):
    assert result["sources_cited"] > 100, (
        f"only {result['sources_cited']} sources contributed a finding"
    )


# ── Grounding ─────────────────────────────────────────────────────────────────

def test_every_note_quote_exists_in_its_source(result, db):
    """The mechanical grounding guarantee, checked independently of the code
    that enforces it."""
    from agent import longterm
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT nt.quote, s.text FROM research_notes nt "
            "JOIN research_sources s ON s.id = nt.source_id").fetchall()
    assert rows, "no notes to check"
    bad = [q for q, text in rows if dr._normalise(q) not in dr._normalise(text)]
    assert not bad, f"{len(bad)} notes quote text absent from their source"


def test_fabricated_findings_never_reach_the_corpus(db, monkeypatch):
    """An extractor that invents quotes must produce an empty corpus, not a
    plausible one. This is the difference between citations and decoration."""
    monkeypatch.setattr(config, "AGENT_MODEL", "claude-opus-5", raising=False)
    monkeypatch.setattr(config, "PROACTIVE_MODEL", "claude-haiku-4-5", raising=False)
    out = dr.run("Anything", depth="quick", client=UngroundedClient(),
                 search_fn=fake_search, fetch_fn=fake_fetch)
    assert out["notes"] == 0, "invented findings were stored as evidence"
    assert "[1]" not in out["report"], "a report was written from nothing"


def test_ungrounded_count_is_reported_not_hidden(db, monkeypatch):
    monkeypatch.setattr(config, "PROACTIVE_MODEL", "claude-haiku-4-5", raising=False)
    qids = dr.plan(1, "q", 2, FakeClient())
    with __import__("agent.longterm", fromlist=["x"])._conn() as c:
        c.execute("INSERT INTO research_sources (run_id, n, url, status, text) "
                  "VALUES (1, 1, 'u', 'read', ?)", ("some real text " * 60,))
    stats = dr.extract(1, UngroundedClient())
    assert stats["ungrounded"] > 0, "silently dropping fabrications hides the rate"
    assert stats["notes"] == 0


# ── Citations ─────────────────────────────────────────────────────────────────

def test_every_citation_resolves_to_a_real_source(result, db):
    from agent import longterm
    with longterm._conn() as c:
        valid = {r[0] for r in c.execute(
            "SELECT n FROM research_sources WHERE run_id = ?",
            (result["run_id"],)).fetchall()}
    used = {int(n) for n in re.findall(r"\[(\d+)\]", result["report"])}
    assert used, "the report cites nothing"
    assert used <= valid, f"invented citations: {sorted(used - valid)}"


def test_report_has_sections_and_a_bibliography(result):
    assert result["report"].startswith("# ")
    assert "## Sources" in result["report"]
    assert result["report"].count("## ") >= 3, "no sectioning — this is an answer, not a report"


def test_provenance_traces_a_claim_back_to_pages(result):
    hits = dr.provenance(result["run_id"], "LFP")
    assert hits, "cannot trace a claim back to its sources"
    assert all(h["url"].startswith("https://") and h["quote"] for h in hits)


# ── Process ───────────────────────────────────────────────────────────────────

def test_it_decomposes_before_searching(result):
    assert result["sub_questions"] >= 16, "no research plan — this is one query"
    assert result["client"].calls["plan"] >= 1


class ThinClient(FakeClient):
    """An extractor that only ever answers the first sub-question, leaving the
    rest under-evidenced — the situation gap analysis exists for."""

    def _findings(self, user: str) -> list:
        qid = [int(m) for m in re.findall(r"^(\d+)\. ", user, re.M)][0]
        doc = user.split("DOCUMENT", 1)[1]
        m = re.search(r"Grid-scale batteries in region \d+ reached [\d]+ "
                      r"megawatt-hours", doc)
        return [{"claim": "Capacity grew.", "quote": m.group(0),
                 "question_id": qid}] if m else []


def test_gap_analysis_opens_new_questions_when_evidence_is_thin(db, monkeypatch):
    """The regression this check is really about: under-evidenced sub-questions
    must trigger another round of searching rather than being written up thin."""
    monkeypatch.setattr(config, "AGENT_MODEL", "claude-opus-5", raising=False)
    monkeypatch.setattr(config, "PROACTIVE_MODEL", "claude-haiku-4-5", raising=False)
    client = ThinClient()
    out = dr.run("Thin question", depth="standard", client=client,
                 search_fn=fake_search, fetch_fn=fake_fetch)
    assert client.calls["gaps"] >= 1, "thin evidence did not open a second round"
    assert out["sub_questions"] > 8, "gap questions were not added to the run"


def test_no_extra_round_is_spent_when_nothing_is_thin(result):
    """The other half. Loop-until-dry must stop when it is dry — a fixed round
    count would pay for searches nobody needs."""
    assert result["client"].calls["gaps"] == 0, (
        "a gap round ran even though every sub-question had enough evidence"
    )


def test_extraction_is_one_call_per_source(result):
    """N calls, not N sub-questions x N sources — the difference between $4 and
    $60 for the same run."""
    assert result["client"].calls["extract"] <= result["sources_read"] + 5


def test_run_is_persisted_and_inspectable(result):
    got = dr.get_run(result["run_id"])
    assert got["status"] == "done"
    assert got["report"] == result["report"]
    assert got["sources_read"] == result["sources_read"]


def test_a_dead_source_does_not_kill_the_run(db, monkeypatch):
    monkeypatch.setattr(config, "PROACTIVE_MODEL", "claude-haiku-4-5", raising=False)

    def flaky(url):
        if hash(url) % 3 == 0:
            raise RuntimeError("connection reset")
        return fake_fetch(url)

    out = dr.run("Resilience", depth="standard", client=FakeClient(),
                 search_fn=fake_search, fetch_fn=flaky)
    assert out["sources_read"] > 20
    assert out["sources_found"] > out["sources_read"], "no failures were recorded"


def test_estimate_prices_a_run_before_it_starts():
    deep = dr.estimate("deep")
    quick = dr.estimate("quick")
    assert deep["expected_sources"] > quick["expected_sources"]
    assert deep["estimated_usd"] > quick["estimated_usd"] > 0
    assert deep["estimated_usd"] < 100, "an estimate this high would be a bug"
