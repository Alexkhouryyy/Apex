"""Cited answers — research where every claim points at a source you can open.

The product here is not "an LLM that searches". It is an answer you can verify
without leaving the page. That makes citation attribution the load-bearing part,
so two rules govern this module:

1. **Citations are generated inline, never attached afterwards.** The model emits
   `[n]` as it writes, so the marker's position *is* the attachment. The tempting
   alternative — write the answer, then bolt on citations by picking the most
   similar chunk — attributes claims to whatever is lexically nearest rather than
   to what actually supported them, and cannot cite anything for a sentence that
   fuses two sources. We have the embedding machinery to do that cheaply, which
   is exactly why it is written down as forbidden. Embeddings select context;
   they never assign credit.

2. **Every marker is validated against the real source list.** Models emit `[7]`
   when five sources exist. An unvalidated citation is worse than no citation: it
   manufactures confidence, which is the one thing this feature sells.

A citation is a pointer to check, not a proof. Sources that failed to fetch carry
a status and are never handed to the model as if they were evidence.
"""
from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import config

# Sources fetched per depth. These are read counts, not search counts.
_DEPTH_SOURCES = {"quick": 3, "standard": 6, "deep": 10}
_DEFAULT_DEPTH = "standard"

_FETCH_WORKERS = 8
_MAX_CHUNKS = 28              # total passages handed to the synthesiser
_MAX_CHUNKS_PER_SOURCE = 4    # so one long page cannot crowd out the rest
_SYNTH_MAX_TOKENS = 4000

# A sentence shorter than this is a fragment, a heading or a transition — not a
# factual claim worth flagging as uncited.
_MIN_CLAIM_CHARS = 40

OK = "ok"
FAILED = "failed"

# [1] or [1, 2] — a group so the model can attribute one claim to two sources.
_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass
class Source:
    """One retrieved page. Exists as data *before* synthesis runs, because
    everything downstream — the prompt, the validator, the UI — needs a stable
    id to point at."""
    n: int
    url: str
    title: str = ""
    snippet: str = ""
    status: str = OK
    error: str = ""
    text: str = ""
    chunks: list[str] = field(default_factory=list)
    cited: bool = False

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.removeprefix("www.")
        except Exception:
            return ""

    def to_dict(self) -> dict:
        return {"n": self.n, "url": self.url, "title": self.title,
                "domain": self.domain, "status": self.status,
                "error": self.error, "cited": self.cited}


def _noop(phase: str, payload: dict) -> None:
    pass


def _shielded(fn):
    """Wrap a progress callback so a broken consumer cannot raise into the run."""
    def _emit(phase: str, payload: dict) -> None:
        try:
            fn(phase, payload)
        except Exception:
            pass
    return _emit


# --- retrieval ---------------------------------------------------------------

def _search(query: str, n: int) -> list[Source]:
    """Search and build the source registry. Rows without a URL are dropped —
    they cannot be cited, so they are not sources."""
    from tools import research as _res

    results = _res.search(query, num_results=n)
    sources: list[Source] = []
    for r in results:
        url = (r.get("url") or r.get("href") or "").strip()
        if not url:
            continue
        sources.append(Source(n=len(sources) + 1, url=url,
                              title=(r.get("title") or "").strip(),
                              snippet=(r.get("snippet") or "").strip()))
        if len(sources) >= n:
            break
    return sources


def _fetch_all(sources: list[Source], on_event=_noop) -> None:
    """Fetch every source in parallel, mutating each with text or a failure.

    Serial fetching was the dominant cost in the old path: page fetches, not
    generation, are what the user waits for.
    """
    from agent import knowledge as _kb
    from tools import research as _res

    def _one(src: Source) -> Source:
        try:
            src.text = _res.fetch(src.url)
            src.chunks = _kb._chunk(src.text)
            src.status = OK
        except Exception as e:
            # A failed fetch is a fact about the source, not a piece of content.
            src.status = FAILED
            src.error = str(e)
            src.text = ""
            src.chunks = []
        return src

    if not sources:
        return
    workers = min(_FETCH_WORKERS, len(sources))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, s) for s in sources]
        for fut in concurrent.futures.as_completed(futs):
            try:
                src = fut.result()
            except Exception:
                continue
            try:
                on_event("source_done", src.to_dict())
            except Exception:
                pass


# --- passage selection -------------------------------------------------------

def _rank_chunks(query: str, sources: list[Source]) -> list[tuple[Source, str]]:
    """Pick the passages most relevant to the query, keeping each tied to its
    source.

    Chunk-level selection is what makes a citation mean "this passage supports
    the claim" instead of "this URL was vaguely involved". It also stops nav
    junk from eating the context window.

    Degrades to leading chunks when embeddings are unavailable — a worse answer,
    never a crash.
    """
    live = [s for s in sources if s.status == OK and s.chunks]
    if not live:
        return []

    pairs: list[tuple[Source, str]] = [
        (s, c) for s in live for c in s.chunks[: _MAX_CHUNKS_PER_SOURCE * 3]
    ]

    scores = _score_pairs(query, pairs)
    if scores is None:
        # No embedding model: round-robin the head of each source so every
        # source still gets representation.
        out: list[tuple[Source, str]] = []
        for i in range(_MAX_CHUNKS_PER_SOURCE):
            for s in live:
                if i < len(s.chunks):
                    out.append((s, s.chunks[i]))
        return out[:_MAX_CHUNKS]

    order = sorted(range(len(pairs)), key=lambda i: scores[i], reverse=True)
    per_source: dict[int, int] = {}
    selected: list[tuple[Source, str]] = []
    for i in order:
        src, chunk = pairs[i]
        if per_source.get(src.n, 0) >= _MAX_CHUNKS_PER_SOURCE:
            continue
        per_source[src.n] = per_source.get(src.n, 0) + 1
        selected.append((src, chunk))
        if len(selected) >= _MAX_CHUNKS:
            break
    return selected


def _score_pairs(query: str, pairs: list[tuple[Source, str]]) -> list[float] | None:
    """Cosine similarity of each chunk against the query. None if unavailable."""
    try:
        import numpy as np
        from agent import longterm as _lt

        qblob = _lt._embed(query)
        if qblob is None:
            return None
        qvec = np.frombuffer(qblob, dtype=np.float32)
        blobs = [_lt._embed(c) for _, c in pairs]
        if not any(b is not None for b in blobs):
            return None
        return _lt._cosine_scores(qvec, blobs)
    except Exception:
        return None


# --- synthesis ---------------------------------------------------------------

_SYNTH_SYSTEM = (
    "You are a research analyst. You answer only from the numbered sources you "
    "are given.\n\n"
    "CITATION RULES — these are not style preferences:\n"
    "- Put a marker like [1] immediately after every factual claim, at the end "
    "of the sentence that makes it.\n"
    "- Use [1, 2] when two sources support the same claim.\n"
    "- Only ever use numbers that appear in the SOURCES list below. Never invent "
    "a source number.\n"
    "- If the sources do not answer part of the question, say so plainly. Do not "
    "fill the gap from memory, and never attach a citation to a claim the "
    "sources do not make.\n"
    "- If sources disagree, report the disagreement and cite both.\n\n"
    "Write clear prose with short paragraphs. Use Markdown headings only if the "
    "answer genuinely has sections. Do not add a Sources or References section — "
    "the interface renders one from your markers."
)


def _build_context(selected: list[tuple[Source, str]]) -> str:
    """Group the chosen passages under their source number.

    The number the model sees is the number the validator checks and the number
    the UI links — one identifier end to end.
    """
    by_source: dict[int, list[str]] = {}
    meta: dict[int, Source] = {}
    for src, chunk in selected:
        by_source.setdefault(src.n, []).append(chunk)
        meta[src.n] = src

    blocks = []
    for n in sorted(by_source):
        src = meta[n]
        header = f"[{n}] {src.title or src.domain}  ({src.url})"
        body = "\n\n".join(by_source[n])
        blocks.append(f"{header}\n{body}")
    return "\n\n---\n\n".join(blocks)


def _synthesize(query: str, context: str, model: str | None = None) -> str:
    from agent import provider

    model = model or config.AGENT_MODEL
    user = (
        f"QUESTION:\n{query}\n\n"
        f"SOURCES:\n{context}\n\n"
        f"Answer the question using only these sources, citing as instructed."
    )
    return provider.complete(model, _SYNTH_SYSTEM, user, max_tokens=_SYNTH_MAX_TOKENS)


# --- citation validation -----------------------------------------------------

def validate_citations(text: str, valid: set[int]) -> tuple[str, set[int], list[int]]:
    """Strip every citation the source list cannot back.

    Returns (clean_text, cited, dropped). A group like [2, 9] with only 2 valid
    becomes [2]; a group with nothing valid disappears entirely, taking its
    stray whitespace with it.
    """
    cited: set[int] = set()
    dropped: list[int] = []

    def _sub(m: re.Match) -> str:
        nums = [int(p) for p in re.split(r"\s*,\s*", m.group(1)) if p.strip().isdigit()]
        keep = []
        for n in nums:
            if n in valid:
                if n not in keep:
                    keep.append(n)
            else:
                dropped.append(n)
        if not keep:
            return ""
        cited.update(keep)
        return "[" + ", ".join(str(n) for n in keep) + "]"

    clean = _CITE_RE.sub(_sub, text)
    # Removing a marker can leave " ." or a double space behind.
    clean = re.sub(r"[ \t]+([.,;:!?])", r"\1", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip(), cited, dropped


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def find_uncited_claims(text: str) -> list[str]:
    """Sentences long enough to be factual claims that carry no citation.

    Advisory, not a gate: a transition or a summarising line legitimately has no
    source. Surfacing them lets the reader see where the ground is soft.
    """
    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        body = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", line)
        for sent in _SENT_SPLIT.split(body):
            sent = sent.strip()
            if len(sent) < _MIN_CLAIM_CHARS or sent.endswith(":"):
                continue
            if not _CITE_RE.search(sent):
                out.append(sent)
    return out


# --- the engine --------------------------------------------------------------

def answer(query: str, depth: str = _DEFAULT_DEPTH, on_event=None) -> dict:
    """Research `query` and return a cited answer.

    Returns {query, answer, sources, uncited_claims, dropped_citations, error}.
    `error` is set (and `answer` empty) only when nothing could be produced.
    """
    # A disconnected dashboard must never fail a research run: progress
    # reporting is a courtesy, the answer is the product.
    on_event = _shielded(on_event or _noop)
    query = (query or "").strip()
    if not query:
        return _fail(query, "a query is required")

    n = _DEPTH_SOURCES.get(depth, _DEPTH_SOURCES[_DEFAULT_DEPTH])

    on_event("search", {"query": query, "depth": depth})
    try:
        sources = _search(query, n)
    except Exception as e:
        return _fail(query, f"search failed: {e}")
    if not sources:
        return _fail(query, f"no results found for {query!r}")

    # Emit the roster before fetching: the user sees what is being read while
    # it is being read, which is most of the perceived speed.
    on_event("sources", {"sources": [s.to_dict() for s in sources]})

    on_event("reading", {"count": len(sources)})
    _fetch_all(sources, on_event)

    live = [s for s in sources if s.status == OK]
    if not live:
        errs = "; ".join(f"{s.domain}: {s.error}" for s in sources[:3])
        return _fail(query, f"could not fetch any sources ({errs})",
                     sources=sources)

    on_event("ranking", {"sources": len(live)})
    selected = _rank_chunks(query, sources)
    if not selected:
        return _fail(query, "sources had no readable content", sources=sources)

    on_event("writing", {"sources": len({s.n for s, _ in selected})})
    try:
        raw = _synthesize(query, _build_context(selected))
    except Exception as e:
        return _fail(query, f"synthesis failed: {e}", sources=sources)

    valid = {s.n for s in live}
    clean, cited, dropped = validate_citations(raw, valid)
    for s in sources:
        s.cited = s.n in cited

    result = {
        "query": query,
        "answer": clean,
        "sources": [s.to_dict() for s in sources],
        "uncited_claims": find_uncited_claims(clean),
        "dropped_citations": dropped,
        "error": "",
    }
    on_event("done", {"cited": sorted(cited), "dropped": dropped})
    return result


def _fail(query: str, message: str, sources: list[Source] | None = None) -> dict:
    """An honest empty answer. Never a fabricated one."""
    return {
        "query": query,
        "answer": "",
        "sources": [s.to_dict() for s in (sources or [])],
        "uncited_claims": [],
        "dropped_citations": [],
        "error": message,
    }


def format_markdown(result: dict) -> str:
    """Render a result for text surfaces (voice, chat, saved documents)."""
    if result.get("error"):
        return f"Research failed: {result['error']}"
    lines = [result["answer"], "", "## Sources"]
    for s in result["sources"]:
        if s["status"] != OK:
            continue
        mark = "" if s["cited"] else " _(not cited)_"
        lines.append(f"{s['n']}. [{s['title'] or s['domain']}]({s['url']}){mark}")
    failed = [s for s in result["sources"] if s["status"] != OK]
    if failed:
        lines.append("")
        lines.append(f"_{len(failed)} source(s) could not be fetched._")
    return "\n".join(lines)
