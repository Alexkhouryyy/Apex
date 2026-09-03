"""Deep research: hundreds to thousands of sources, one cited report.

`agent/answers.py` is a cited-answer engine and tops out at ten sources, because
its whole pipeline ends in a single model call — search, fetch, rank passages,
synthesise. Everything has to fit one context, so "deep" meant ten pages. That is
a search with footnotes, not research.

This is a different shape, and it is not the same pipeline with a bigger number.

    answers.py     search -> fetch N -> rank -> ONE call -> answer
    deepresearch   plan -> sweep -> harvest -> extract(map) -> gaps -> reduce

The load-bearing idea: **the corpus lives in SQLite and the model never sees
it.** A thousand pages is roughly 3M characters — technically inside a 1M-token
context, but one call that size is expensive, slow, and markedly worse at
reasoning than several small ones. Instead each source is read once by a cheap
model, which emits short notes; the report is written from notes. A thousand
pages becomes a few thousand sentences, and the synthesis step reads only the
notes for the section it is writing.

Grounding is mechanical, not promised. Every note must carry a quote that occurs
**verbatim in the fetched text**. Notes whose quote cannot be found are dropped
before they can reach the report, so an invented finding is detected by string
search rather than trusted away. This is the check answers.py could not make: it
had no per-source step to attach a quote to.

Citations follow the rule established in answers.py and unchanged here: the model
emits [n] inline as it writes, and [n] is validated against sources actually
fetched. Nothing is ever retro-attached by similarity — embeddings may select
what a writer reads, they may never assign credit for what a writer said.

Everything is persisted and resumable. A thousand-source run takes minutes and
costs real money; it must survive a crash, and you must be able to look at the
corpus afterwards and ask where a claim came from.

Honest cost, measured in tokens rather than guessed: roughly $3-8 for a
1,000-source run — extraction on a cheap model dominates the source count,
planning and synthesis dominate the quality. `estimate()` prices a run before it
starts, and every call goes through telemetry so the budget cap can see it.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import config
from agent import longterm, telemetry

# ── Tunables ──────────────────────────────────────────────────────────────────

DEPTHS = {
    # depth: (sub_questions, queries_per_question, results_per_query, max_rounds)
    "quick":     (4,  2,  8,  1),
    "standard":  (8,  3, 10,  2),
    "deep":      (16, 4, 10,  3),
    "exhaustive":(28, 5, 12,  4),
}
DEFAULT_DEPTH = "deep"

FETCH_WORKERS = 16
EXTRACT_WORKERS = 8
MIN_SOURCE_CHARS = 400          # below this a page carries no findings
MAX_SOURCE_CHARS = 12000        # per source, fed to extraction
MAX_NOTES_PER_SOURCE = 6
MIN_QUOTE_CHARS = 25
NOTES_PER_SECTION = 60          # notes shown to the writer for one section
GAP_MIN_NOTES = 4               # a sub-question with fewer is under-researched

# WHICH database was initialised, not merely THAT one was. A bare boolean here
# records that some database has the tables and then skips the check for every
# other, so a DB_PATH change at runtime silently disarms the guard whose whole
# job is to make a missing table impossible. Found via agent/budget.py, which
# had the identical latch; see tests/test_schema.py::TestLazyGuardsArePerDatabase.
_ready_for: str | None = None
_lock = threading.Lock()


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS research_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                question      TEXT NOT NULL,
                depth         TEXT NOT NULL,
                target        INTEGER NOT NULL,
                status        TEXT NOT NULL DEFAULT 'planning',
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL,
                report        TEXT DEFAULT '',
                error         TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS research_questions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id   INTEGER NOT NULL,
                text     TEXT NOT NULL,
                round    INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS research_sources (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER NOT NULL,
                n          INTEGER NOT NULL,
                url        TEXT NOT NULL,
                title      TEXT DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'found',
                text       TEXT DEFAULT '',
                error      TEXT DEFAULT '',
                fetched_at REAL DEFAULT 0
            )
        """)
        # One row per URL per run. Deduping in SQL rather than in Python is what
        # keeps a sweep of 15 sub-questions x 5 queries from reading the same
        # popular page fifteen times.
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_research_src_url "
                  "ON research_sources(run_id, url)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS research_notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                source_id   INTEGER NOT NULL,
                claim       TEXT NOT NULL,
                quote       TEXT NOT NULL,
                ts          REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_research_notes_q "
                  "ON research_notes(run_id, question_id)")


def _ensure_db() -> None:
    global _ready_for
    if _ready_for == str(longterm.DB_PATH):
        return
    with _lock:
        try:
            init_db()
            _ready_for = str(longterm.DB_PATH)
        except Exception:
            pass


# ── Small helpers ─────────────────────────────────────────────────────────────

def _noop(phase: str, payload: dict) -> None:
    pass


def _text_of(resp) -> str:
    return " ".join(getattr(b, "text", "") for b in resp.content
                    if getattr(b, "type", "") == "text").strip()


def _json_list(raw: str) -> list:
    """Parse a JSON array out of a model reply, tolerating prose around it."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", raw).strip()
    try:
        out = json.loads(raw)
        return out if isinstance(out, list) else []
    except Exception:
        pass
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else []
    except Exception:
        return []


def _normalise(s: str) -> str:
    """Whitespace-insensitive form, for locating a quote in a page.

    Fetched text has arbitrary wrapping, so a quote that is genuinely present
    frequently fails an exact match. Collapsing whitespace is the difference
    between a grounding check that works and one that rejects everything.
    """
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# ── Steps ─────────────────────────────────────────────────────────────────────

_PLAN_SYS = (
    "You are planning a research project. Break the question into distinct "
    "sub-questions that together cover it: different facets, competing views, "
    "quantitative evidence, history, counter-arguments, and second-order "
    "effects. Each sub-question must be independently searchable and must not "
    "restate another. Reply with ONLY a JSON array of strings."
)


def plan(run_id: int, question: str, n: int, client) -> list[int]:
    resp = telemetry.create(
        client, call_site="deepresearch/plan",
        model=config.AGENT_MODEL, max_tokens=2000, system=_PLAN_SYS,
        messages=[{"role": "user",
                   "content": f"Question: {question}\n\nGive exactly {n} sub-questions."}],
    )
    subs = [s for s in _json_list(_text_of(resp)) if isinstance(s, str) and s.strip()]
    if not subs:
        # A planner that returns nothing must not silently produce a run with no
        # questions and therefore no sources — fall back to the question itself.
        subs = [question]
    with longterm._conn() as c:
        ids = []
        for s in subs[:n]:
            cur = c.execute(
                "INSERT INTO research_questions (run_id, text, round) VALUES (?,?,0)",
                (run_id, s.strip()))
            ids.append(int(cur.lastrowid))
    return ids


_QUERY_SYS = (
    "Write web search queries for a sub-question. Vary the angle: exact phrasing, "
    "technical terms, named entities, statistics, criticism, and primary sources. "
    "Keep each query short. Reply with ONLY a JSON array of strings."
)


def queries_for(sub_question: str, n: int, client) -> list[str]:
    try:
        resp = telemetry.create(
            client, call_site="deepresearch/queries",
            model=config.PROACTIVE_MODEL, max_tokens=600, system=_QUERY_SYS,
            messages=[{"role": "user",
                       "content": f"Sub-question: {sub_question}\n\nGive {n} queries."}],
        )
        qs = [q for q in _json_list(_text_of(resp)) if isinstance(q, str) and q.strip()]
    except Exception:
        qs = []
    return (qs or [sub_question])[:n]


def sweep(run_id: int, question_ids: list[int], per_q: int, per_query: int,
          client, search_fn: Callable, on_event=_noop) -> int:
    """Search for every sub-question and record the unique URLs found."""
    added = 0
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, text FROM research_questions WHERE id IN (%s)"
            % ",".join("?" * len(question_ids)), question_ids).fetchall()
        start_n = c.execute(
            "SELECT COALESCE(MAX(n), 0) FROM research_sources WHERE run_id = ?",
            (run_id,)).fetchone()[0]

    n = start_n
    for qid, text in rows:
        for q in queries_for(text, per_q, client):
            try:
                results = search_fn(q, per_query)
            except Exception as e:
                on_event("search_failed", {"query": q, "error": str(e)[:200]})
                continue
            for r in results:
                url = (r.get("url") or r.get("href") or "").strip()
                if not url:
                    continue
                with longterm._conn() as c:
                    try:
                        n += 1
                        c.execute(
                            "INSERT INTO research_sources (run_id, n, url, title, status) "
                            "VALUES (?,?,?,?, 'found')",
                            (run_id, n, url, (r.get("title") or "")[:300]))
                        added += 1
                    except Exception:
                        n -= 1      # UNIQUE violation: already have this URL
            on_event("searched", {"query": q, "unique_sources": added})
    return added


def harvest(run_id: int, fetch_fn: Callable, limit: int, on_event=_noop) -> int:
    """Fetch every unread source, in parallel."""
    with longterm._conn() as c:
        pending = c.execute(
            "SELECT id, url FROM research_sources WHERE run_id = ? AND status = 'found' "
            "ORDER BY n LIMIT ?", (run_id, limit)).fetchall()
    if not pending:
        return 0

    done = 0

    def _one(row):
        sid, url = row
        try:
            text = fetch_fn(url)
            return sid, text, ""
        except Exception as e:
            return sid, "", f"{type(e).__name__}: {str(e)[:200]}"

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for fut in as_completed([pool.submit(_one, r) for r in pending]):
            sid, text, err = fut.result()
            if err or len(text) < MIN_SOURCE_CHARS:
                status, err = "dead", err or "too short to carry findings"
            else:
                status = "read"
                done += 1
            with longterm._conn() as c:
                c.execute(
                    "UPDATE research_sources SET status=?, text=?, error=?, fetched_at=? "
                    "WHERE id=?",
                    (status, text[:MAX_SOURCE_CHARS], err, time.time(), sid))
            on_event("harvested", {"read": done, "of": len(pending)})
    return done


_EXTRACT_SYS = (
    "Extract findings from a document that answer a specific question.\n"
    "For each finding give a one-sentence claim and a VERBATIM quote from the "
    "document that supports it — copy the quote exactly, do not paraphrase it.\n"
    "Only include findings the document actually supports. If it contains "
    "nothing relevant, reply with an empty array. Inventing a finding is worse "
    "than returning none.\n"
    'Reply with ONLY a JSON array of {"claim": "...", "quote": "..."}.'
)


def extract(run_id: int, client, limit: int = 100000, on_event=_noop) -> dict:
    """Read each source against each sub-question and store grounded notes.

    The map step. Returns counts including how many notes were rejected for
    quoting text that is not in the source — that number is the honest measure
    of how much the extractor made up.
    """
    with longterm._conn() as c:
        subs = c.execute(
            "SELECT id, text FROM research_questions WHERE run_id = ?", (run_id,)).fetchall()
        # 'read' only — a source moves to 'extracted' below. Selecting every
        # readable source each round re-read the whole corpus every time: a
        # 3-round run paid three times for identical notes. The trade-off is
        # that sub-questions opened by a later gap round only draw on sources
        # fetched after they existed, which is the right way round — gap
        # analysis exists to go and find *new* pages for a thin question.
        sources = c.execute(
            "SELECT id, n, url, title, text FROM research_sources "
            "WHERE run_id = ? AND status = 'read' ORDER BY n LIMIT ?",
            (run_id, limit)).fetchall()
    if not subs or not sources:
        return {"notes": 0, "ungrounded": 0, "sources": 0}

    kept = ungrounded = 0
    counter = {"done": 0}

    def _one(src):
        sid, n, url, title, text = src
        # One call per source covering every sub-question: N calls, not N*M.
        sub_list = "\n".join(f"{qid}. {qt}" for qid, qt in subs)
        prompt = (f"QUESTIONS:\n{sub_list}\n\n"
                  f"DOCUMENT ({title or url}):\n{text[:MAX_SOURCE_CHARS]}\n\n"
                  f'For each finding also give "question_id" from the list above. '
                  f"At most {MAX_NOTES_PER_SOURCE} findings.")
        try:
            resp = telemetry.create(
                client, call_site="deepresearch/extract",
                model=config.PROACTIVE_MODEL, max_tokens=1500,
                system=_EXTRACT_SYS,
                messages=[{"role": "user", "content": prompt}])
            return sid, _json_list(_text_of(resp)), text
        except Exception:
            return sid, [], text

    valid_qids = {qid for qid, _ in subs}
    with ThreadPoolExecutor(max_workers=EXTRACT_WORKERS) as pool:
        for fut in as_completed([pool.submit(_one, s) for s in sources]):
            sid, findings, text = fut.result()
            hay = _normalise(text)
            rows = []
            for f in findings[:MAX_NOTES_PER_SOURCE]:
                if not isinstance(f, dict):
                    continue
                claim = str(f.get("claim", "")).strip()
                quote = str(f.get("quote", "")).strip()
                try:
                    qid = int(f.get("question_id"))
                except Exception:
                    continue
                if not claim or len(quote) < MIN_QUOTE_CHARS or qid not in valid_qids:
                    continue
                # The grounding gate. A quote that is not in the document means
                # the finding was invented, and it is dropped rather than
                # footnoted — string search, not trust.
                if _normalise(quote) not in hay:
                    ungrounded += 1
                    continue
                rows.append((run_id, qid, sid, claim, quote, time.time()))
            with longterm._conn() as c:
                if rows:
                    c.executemany(
                        "INSERT INTO research_notes "
                        "(run_id, question_id, source_id, claim, quote, ts) "
                        "VALUES (?,?,?,?,?,?)", rows)
                # Mark it read-and-mined whether or not it yielded anything, so
                # a barren page is not paid for again next round.
                c.execute("UPDATE research_sources SET status='extracted' WHERE id=?",
                          (sid,))
            kept += len(rows)
            counter["done"] += 1
            on_event("extracted", {"sources": counter["done"], "notes": kept,
                                   "ungrounded": ungrounded})
    return {"notes": kept, "ungrounded": ungrounded, "sources": len(sources)}


_GAP_SYS = (
    "You are auditing a research corpus for gaps. Given the main question and "
    "the sub-questions that returned little evidence, write NEW sub-questions "
    "that would find it — different wording, adjacent topics, primary sources, "
    "opposing views. Do not repeat an existing sub-question. "
    "Reply with ONLY a JSON array of strings."
)


def gaps(run_id: int, question: str, client, n: int, round_no: int) -> list[int]:
    """Find under-evidenced sub-questions and open new lines of enquiry."""
    with longterm._conn() as c:
        thin = c.execute(
            "SELECT q.id, q.text, COUNT(nt.id) AS c FROM research_questions q "
            "LEFT JOIN research_notes nt ON nt.question_id = q.id "
            "WHERE q.run_id = ? GROUP BY q.id HAVING c < ? ORDER BY c ASC",
            (run_id, GAP_MIN_NOTES)).fetchall()
        existing = [r[0] for r in c.execute(
            "SELECT text FROM research_questions WHERE run_id = ?", (run_id,)).fetchall()]
    if not thin:
        return []
    body = ("MAIN QUESTION: " + question + "\n\nUNDER-EVIDENCED:\n"
            + "\n".join(f"- {t} ({c} findings)" for _, t, c in thin)
            + "\n\nALREADY ASKED:\n" + "\n".join(f"- {e}" for e in existing[:40]))
    try:
        resp = telemetry.create(
            client, call_site="deepresearch/gaps",
            model=config.AGENT_MODEL, max_tokens=1200, system=_GAP_SYS,
            messages=[{"role": "user", "content": body + f"\n\nGive up to {n}."}])
        new = [s for s in _json_list(_text_of(resp)) if isinstance(s, str) and s.strip()]
    except Exception:
        return []
    seen = {e.strip().lower() for e in existing}
    ids = []
    with longterm._conn() as c:
        for s in new[:n]:
            if s.strip().lower() in seen:
                continue
            cur = c.execute(
                "INSERT INTO research_questions (run_id, text, round) VALUES (?,?,?)",
                (run_id, s.strip(), round_no))
            ids.append(int(cur.lastrowid))
    return ids


_WRITE_SYS = (
    "You are writing one section of a research report from field notes.\n"
    "Every note carries a source number. Cite it as [n] immediately after the "
    "claim it supports. Cite multiple as [3][7].\n"
    "Write only what the notes support. Do not add knowledge from memory, and "
    "do not cite a number that is not in the notes.\n"
    "Where notes disagree, say so and cite both sides. Prose, no bullet lists "
    "unless the material is genuinely a list. No preamble."
)


def synthesize(run_id: int, question: str, client, on_event=_noop) -> str:
    """The reduce step: write the report section by section from notes."""
    from agent.answers import validate_citations

    with longterm._conn() as c:
        subs = c.execute(
            "SELECT id, text FROM research_questions WHERE run_id = ? ORDER BY id",
            (run_id,)).fetchall()
        sources = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
            "SELECT id, n, url, title FROM research_sources WHERE run_id = ?",
            (run_id,)).fetchall()}

    valid = {n for (n, _u, _t) in sources.values()}
    sections, cited_all = [], set()

    for qid, qtext in subs:
        with longterm._conn() as c:
            notes = c.execute(
                "SELECT claim, quote, source_id FROM research_notes "
                "WHERE run_id = ? AND question_id = ? LIMIT ?",
                (run_id, qid, NOTES_PER_SECTION)).fetchall()
        if not notes:
            continue
        lines = []
        for claim, quote, sid in notes:
            if sid not in sources:
                continue
            n, url, title = sources[sid]
            lines.append(f'[{n}] {claim}\n    "{quote[:300]}"  — {title or url}')
        if not lines:
            continue
        resp = telemetry.create(
            client, call_site="deepresearch/write",
            model=config.AGENT_MODEL, max_tokens=4000, system=_WRITE_SYS,
            messages=[{"role": "user",
                       "content": f"REPORT QUESTION: {question}\n\n"
                                  f"SECTION: {qtext}\n\nNOTES:\n" + "\n".join(lines)}])
        body, cited, _dropped = validate_citations(_text_of(resp), valid)
        cited_all |= cited
        sections.append((qtext, body))
        on_event("wrote", {"sections": len(sections), "of": len(subs)})

    parts = [f"# {question}", ""]
    for title, body in sections:
        parts += [f"## {title}", "", body, ""]

    if cited_all:
        parts += ["## Sources", ""]
        by_n = {n: (url, title) for (n, url, title) in sources.values()}
        for n in sorted(cited_all):
            url, title = by_n.get(n, ("", ""))
            parts.append(f"{n}. {title or url} — {url}")
    return "\n".join(parts)


# ── Orchestration ─────────────────────────────────────────────────────────────

def estimate(depth: str = DEFAULT_DEPTH) -> dict:
    """Price a run before starting it. A thousand sources is not free."""
    subs, per_q, per_query, rounds = DEPTHS.get(depth, DEPTHS[DEFAULT_DEPTH])
    searches = subs * per_q * rounds
    raw = searches * per_query
    unique = int(raw * 0.55)        # overlap between queries is the norm
    cheap = config.MODEL_PRICING.get(config.PROACTIVE_MODEL, {"input": 1.0, "output": 5.0})
    smart = config.MODEL_PRICING.get(config.AGENT_MODEL, {"input": 5.0, "output": 25.0})
    extract_cost = unique * (3500 * cheap["input"] + 400 * cheap["output"]) / 1e6
    write_cost = subs * (6000 * smart["input"] + 1200 * smart["output"]) / 1e6
    plan_cost = (searches / per_q) * (800 * cheap["input"] + 200 * cheap["output"]) / 1e6
    return {
        "depth": depth, "sub_questions": subs, "searches": searches,
        "expected_sources": unique,
        "estimated_usd": round(extract_cost + write_cost + plan_cost, 2),
    }


def run(question: str, depth: str = DEFAULT_DEPTH, client=None,
        search_fn: Callable | None = None, fetch_fn: Callable | None = None,
        max_sources: int | None = None, on_event=_noop) -> dict:
    """Plan, sweep, harvest, extract, close gaps, and write the report."""
    _ensure_db()
    subs_n, per_q, per_query, rounds = DEPTHS.get(depth, DEPTHS[DEFAULT_DEPTH])

    if client is None:
        from agent.provider import get_client
        client = get_client(config.AGENT_MODEL)
    if search_fn is None or fetch_fn is None:
        from tools import research as _r
        search_fn = search_fn or (lambda q, n: _r.search(q, num_results=n))
        fetch_fn = fetch_fn or _r.fetch
    cap = max_sources or 100000

    now = time.time()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO research_runs (question, depth, target, status, created_at, updated_at) "
            "VALUES (?,?,?, 'planning', ?, ?)", (question, depth, cap, now, now))
        run_id = int(cur.lastrowid)
    on_event("started", {"run_id": run_id, "depth": depth})

    try:
        qids = plan(run_id, question, subs_n, client)
        on_event("planned", {"sub_questions": len(qids)})

        for round_no in range(rounds):
            if not qids:
                break
            _status(run_id, f"sweeping-{round_no}")
            sweep(run_id, qids, per_q, per_query, client, search_fn, on_event)
            _status(run_id, f"harvesting-{round_no}")
            harvest(run_id, fetch_fn, cap, on_event)
            _status(run_id, f"extracting-{round_no}")
            stats = extract(run_id, client, cap, on_event)
            on_event("round_done", {"round": round_no, **stats})
            if round_no + 1 < rounds:
                qids = gaps(run_id, question, client, max(2, subs_n // 2), round_no + 1)
                if not qids:
                    on_event("no_gaps", {"round": round_no})
                    break

        _status(run_id, "writing")
        report = synthesize(run_id, question, client, on_event)
        with longterm._conn() as c:
            c.execute("UPDATE research_runs SET status='done', report=?, updated_at=? "
                      "WHERE id=?", (report, time.time(), run_id))
        return {"run_id": run_id, "report": report, **stats_for(run_id)}
    except Exception as e:
        with longterm._conn() as c:
            c.execute("UPDATE research_runs SET status='failed', error=?, updated_at=? "
                      "WHERE id=?", (str(e)[:500], time.time(), run_id))
        raise


def _status(run_id: int, status: str) -> None:
    with longterm._conn() as c:
        c.execute("UPDATE research_runs SET status=?, updated_at=? WHERE id=?",
                  (status, time.time(), run_id))


def stats_for(run_id: int) -> dict:
    with longterm._conn() as c:
        found = c.execute("SELECT COUNT(*) FROM research_sources WHERE run_id=?",
                          (run_id,)).fetchone()[0]
        read = c.execute("SELECT COUNT(*) FROM research_sources WHERE run_id=? "
                         "AND status IN ('read','extracted')", (run_id,)).fetchone()[0]
        notes = c.execute("SELECT COUNT(*) FROM research_notes WHERE run_id=?",
                          (run_id,)).fetchone()[0]
        subs = c.execute("SELECT COUNT(*) FROM research_questions WHERE run_id=?",
                         (run_id,)).fetchone()[0]
        cited = c.execute(
            "SELECT COUNT(DISTINCT source_id) FROM research_notes WHERE run_id=?",
            (run_id,)).fetchone()[0]
    return {"sources_found": found, "sources_read": read, "notes": notes,
            "sub_questions": subs, "sources_cited": cited}


def get_run(run_id: int) -> Optional[dict]:
    _ensure_db()
    with longterm._conn() as c:
        row = c.execute(
            "SELECT id, question, depth, status, created_at, report, error "
            "FROM research_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    return {"id": row[0], "question": row[1], "depth": row[2], "status": row[3],
            "created_at": row[4], "report": row[5], "error": row[6],
            **stats_for(run_id)}


def list_runs(limit: int = 20) -> list[dict]:
    _ensure_db()
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, question, depth, status, created_at FROM research_runs "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "question": r[1], "depth": r[2], "status": r[3],
             "created_at": r[4]} for r in rows]


def provenance(run_id: int, claim_substring: str) -> list[dict]:
    """Which sources support a claim. The reason the corpus is kept."""
    _ensure_db()
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT nt.claim, nt.quote, s.n, s.url, s.title FROM research_notes nt "
            "JOIN research_sources s ON s.id = nt.source_id "
            "WHERE nt.run_id = ? AND nt.claim LIKE ?",
            (run_id, f"%{claim_substring}%")).fetchall()
    return [{"claim": r[0], "quote": r[1], "n": r[2], "url": r[3], "title": r[4]}
            for r in rows]
