"""Concept Genesis — hypotheses that could be wrong, and a record of finding out.

Phase 13's success check: **testable novel hypotheses with evidence and
critique.** Four words, each of which a fluent paragraph can counterfeit.

## The problem this module is actually solving

Generation is one API call. Ask any model for five novel testable hypotheses
about anything and it will produce five, immediately, and they will read
beautifully. They will have the cadence of science. Some of them will be
restatements of the prompt, some will be unfalsifiable, some will cite sources
that do not exist, and nothing about the text will tell you which.

That makes this the single easiest subsystem in Apex to fake, and the one where
"it works" and "it produces output" are hardest to tell apart. So this file is
not a generator with some validation attached. It is a **gate**, in the same
shape as `agent/mcp_policy.py`: the model proposes, and four checks that do not
consult the model's opinion of itself decide what survives.

The proportion is deliberate. Most of this file, and nearly all of its tests,
is about rejecting things.

## The four gates

**1. Structure** (pure; no model, no network, no database).
A hypothesis is a claim plus the observation that would kill it. Rejected here:

  * a claim hedged into unfalsifiability — "X *may sometimes* improve Y" is
    compatible with every possible observation, so no result can bear on it;
  * a recommendation or a question wearing a claim's clothes;
  * a vacuous refutation — "if the data does not support it" names no
    observation, it just restates what falsity means;
  * a refutation that is the claim negated. "X improves Y" refuted by "if X
    does not improve Y" is a tautology with a lab coat on: it introduces no
    independent thing to go and look at.

**2. Evidence** (pure; checkable against what Apex actually holds).
Every citation must resolve to a real source. An unresolvable id is a
hallucinated citation and is fatal — this is the most common and most
convincing failure of the whole category. And a hypothesis where nobody looked
for *disconfirming* evidence fails: either contrary evidence is recorded, or an
explicit "searched, found none" is. Confirmation bias, made mechanical.

**3. Novelty** — and note the asymmetry, which is the load-bearing idea here.
With embeddings, similarity can establish both "this is a duplicate" and "this
is not". Without them, lexical overlap can still *prove* a duplicate — high
overlap is high overlap — but its absence proves nothing, because a paraphrase
shares few words with its source. So the lexical mode may return `not novel`
and may NEVER return `novel`; it returns `unknown` instead.

This is the same asymmetry `agent/mcp_policy.py` applies to a server's
self-description: evidence may make a verdict stricter, never looser.

**4. Critique** — attacked, by something that is not itself.
A model critiquing its own output is the correlated-knowledge problem
`agent/consensus.py` was written about. When a second model is configured and
was not used, that is a caller error and fails. When only one model exists, the
critique is real but weaker, and the gate says `unknown` rather than pretending.

## Never `standing` by default

A gate that cannot run returns `unknown`, and any `unknown` makes the
hypothesis `unverified` — never `standing`. `agent/forge.py` and
`agent/capabilities.py` made the same choice for the same reason: collapsing
"I could not check this" into "this passed" is how a check becomes
indistinguishable from no check.

## The part that makes it testable rather than test-shaped

A hypothesis nobody ever resolves is a note with extra fields. `observe()`
closes the loop: a standing hypothesis that meets its predicted observation
becomes `supported`, one that meets its refutation becomes `refuted`, and
`due()` answers "what did I predict that I can check now".

**`refuted` is a success.** It is the only status in this file that proves the
whole apparatus worked — something was claimed, it could have been wrong, and
it was found to be wrong. `describe()` says so, because a system that treats
refutation as failure will quietly stop producing refutable claims.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from agent import longterm

# ── verdicts ────────────────────────────────────────────────────────────────

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# ── statuses ────────────────────────────────────────────────────────────────

PROPOSED = "proposed"        # generated, not yet adjudicated
REJECTED = "rejected"        # failed a gate
UNVERIFIED = "unverified"    # a gate could not run
STANDING = "standing"        # passed every gate; testable and untested
SUPPORTED = "supported"      # an observation matched the prediction
REFUTED = "refuted"          # an observation matched the refutation — a win

# Statuses that cannot be adjudicated again. Deliberately NOT including
# `rejected`: a rejection is a diagnosis, not a sentence. "never attacked" is
# cured by attacking it; a citation that does not resolve is cured by citing
# something real; a hedged claim is cured by stating it definitely. A gate
# whose objection could never be answered would push a proposer towards never
# risking a rejectable claim, which is the opposite of the point.
#
# A resolved hypothesis is different. Once an observation has been made, the
# verdict belongs to the world rather than to the gates, and re-running them
# could only overwrite it.
RESOLVED = (SUPPORTED, REFUTED)


@dataclass(frozen=True)
class GateResult:
    gate: str
    verdict: str
    reason: str
    detail: dict = field(default_factory=dict)


# ── gate 1: structure ───────────────────────────────────────────────────────

# Modal hedges that make a claim compatible with every possible observation.
# Deliberately short and deliberately strict: "X may improve Y" is not a
# hypothesis, and the remedy — say it definitely — costs the proposer nothing.
# Words that merely make a claim STATISTICAL ("often", "tends to", "on
# average") are NOT here: those are falsifiable with a threshold, and rejecting
# them would throw out most real empirical claims.
HEDGES = (
    "may ", "might ", "could ", "possibly", "potentially", "perhaps",
    "arguably", "in principle", "in some cases", "under certain conditions",
    "it is conceivable", "one could argue",
)

# Openings that mark a recommendation or a research direction rather than a
# claim about the world. You cannot refute advice.
NOT_A_CLAIM = (
    "we should", "you should", "one should", "consider ", "explore ",
    "investigate ", "it would be interesting", "research is needed",
    "more study", "further work",
)

# Refutations that restate what being false means instead of naming something
# to go and look at.
VACUOUS_REFUTATION = (
    r"\bif (?:this|the|it|that)?\s*(?:hypothesis|claim|theory|idea|assumption)?\s*"
    r"(?:is|are|were|turns? out to be|proves? to be)?\s*"
    r"(?:wrong|false|incorrect|untrue|not true|mistaken)\b",
    r"\bif (?:the )?(?:data|evidence|results?|findings?|numbers?)\s*"
    r"(?:does not|doesn'?t|do not|don'?t|fail(?:s)? to)\s*support\b",
    r"\bif (?:it|this|that) (?:does not|doesn'?t) (?:work|hold|pan out)\b",
    r"\bif no (?:evidence|support|correlation|effect|difference) (?:is|was) found\b",
    r"^\s*(?:if )?(?:disproven|falsified|refuted|contradicted|proven wrong)\b",
    r"\bif (?:the )?opposite (?:is|were) (?:true|found|observed)\b",
    r"\black of (?:evidence|support)\b",
)
_VACUOUS_RE = tuple(re.compile(p, re.I) for p in VACUOUS_REFUTATION)

# Words carrying no content for the restatement test. Negation words are in
# here on purpose: the whole question is whether the refutation says anything
# BEYOND negating the claim, so the negation itself must not count as content.
_STOPWORDS = frozenset("""
a an the this that these those it its is are was were be been being am
if when while then than as at by for from in into of on onto or and but so to
with without within over under above below not no never none nor cannot cant
does do did doesnt dont didnt would will shall should can could may might must
we you they he she i one there here which who whom whose what how why
show shows showed showing observe observed find finds found see seen
""".split())

# How many content words a refutation must add beyond the claim's own. Two,
# because one can be an artefact of phrasing; a real refutation names a
# measurement, a population, a threshold or a time — several new words at once.
MIN_NEW_TERMS = 2

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'%.\-]*")


def content_words(text: str) -> set[str]:
    """Lowercase content tokens — what a sentence is actually about."""
    return {w for w in _WORD_RE.findall((text or "").lower())
            if w not in _STOPWORDS and len(w) > 1}


def check_structure(claim: str, prediction: str, refutation: str,
                    method: str) -> GateResult:
    """Is this a hypothesis at all? Pure: no model, no network, no database."""
    claim = (claim or "").strip()
    prediction = (prediction or "").strip()
    refutation = (refutation or "").strip()
    method = (method or "").strip()

    missing = [n for n, v in (("claim", claim), ("prediction", prediction),
                              ("refutation", refutation), ("method", method))
               if not v]
    if missing:
        return GateResult("structure", FAIL,
                          f"missing {', '.join(missing)}. A claim with no stated "
                          f"refutation is an opinion.")

    low = claim.lower()
    if claim.rstrip().endswith("?"):
        return GateResult("structure", FAIL,
                          "the claim is a question. A question cannot be refuted.")
    for opener in NOT_A_CLAIM:
        if low.startswith(opener) or f" {opener}" in low:
            return GateResult("structure", FAIL,
                              f"the claim reads as a recommendation or a research "
                              f"direction ('{opener.strip()}'), not a statement about "
                              f"the world. Advice cannot be refuted.")
    for hedge in HEDGES:
        if hedge in low:
            return GateResult("structure", FAIL,
                              f"the claim is hedged with '{hedge.strip()}', which makes "
                              f"every possible observation compatible with it. State it "
                              f"definitely, or state the condition it holds under.")

    for pattern in _VACUOUS_RE:
        if pattern.search(refutation):
            return GateResult("structure", FAIL,
                              "the refutation restates what being false means instead "
                              "of naming an observation. Say what you would SEE.")

    new_terms = content_words(refutation) - content_words(claim)
    if len(new_terms) < MIN_NEW_TERMS:
        return GateResult(
            "structure", FAIL,
            "the refutation is the claim negated — it introduces nothing new to "
            "go and look at. A refutation names a measurement, a population or a "
            "threshold the claim does not.",
            {"new_terms": sorted(new_terms)})

    return GateResult("structure", PASS,
                      "claim, prediction, refutation and method all present; the "
                      "refutation names something independent to observe.",
                      {"new_terms": sorted(new_terms)})


# ── gate 2: evidence ────────────────────────────────────────────────────────

FOR, AGAINST = "for", "against"


@dataclass(frozen=True)
class Evidence:
    stance: str          # FOR | AGAINST
    source_id: str       # must resolve to something Apex actually holds
    note: str = ""


def check_evidence(evidence: Sequence[Evidence],
                   resolves: Callable[[str], bool],
                   *, searched_contrary: bool = False) -> GateResult:
    """Does every citation point at a real source, and did anyone look for the
    evidence that would hurt?

    The first half catches the most convincing failure in this whole category.
    A fabricated citation is indistinguishable from a real one in prose — it
    has an author, a year, a plausible title — and it is only ever caught by
    trying to fetch it. So every id is resolved, and one that does not is
    fatal rather than a warning.

    The second half is confirmation bias made mechanical. A hypothesis
    assembled only from what supports it is an argument, not a finding.
    Recording `searched_contrary` with nothing found is a perfectly good
    answer; recording nothing at all is not.
    """
    if not evidence:
        return GateResult("evidence", FAIL,
                          "no evidence cited at all. A hypothesis with no sources "
                          "is a guess, however well phrased.")

    unknown_stance = [e.source_id for e in evidence if e.stance not in (FOR, AGAINST)]
    if unknown_stance:
        return GateResult("evidence", FAIL,
                          f"evidence must be recorded as '{FOR}' or '{AGAINST}'; "
                          f"got {unknown_stance[:3]}")

    missing = []
    for e in evidence:
        try:
            ok = bool(resolves(e.source_id))
        except Exception as exc:                       # a broken resolver is not a pass
            return GateResult("evidence", UNKNOWN,
                              f"could not check citations: {exc}")
        if not ok:
            missing.append(e.source_id)
    if missing:
        return GateResult(
            "evidence", FAIL,
            f"{len(missing)} citation(s) do not resolve to anything Apex holds: "
            f"{', '.join(missing[:3])}. A fabricated citation reads exactly like a "
            f"real one, so the only test is to go and fetch it.",
            {"missing": missing})

    against = [e for e in evidence if e.stance == AGAINST]
    if not against and not searched_contrary:
        return GateResult(
            "evidence", FAIL,
            "only supporting evidence is recorded, and no search for contrary "
            "evidence was. Finding none is an acceptable answer; not looking is "
            "not.")

    return GateResult("evidence", PASS,
                      f"{len(evidence)} citation(s) resolve; "
                      + (f"{len(against)} contrary" if against
                         else "contrary evidence searched for, none found"))


# ── gate 3: novelty ─────────────────────────────────────────────────────────

SEMANTIC = "semantic"
LEXICAL = "lexical"

# Cosine similarity at or above this is the same idea in different words.
SEMANTIC_DUPLICATE = 0.85
# Fraction of the claim's content words already present in one corpus entry.
LEXICAL_DUPLICATE = 0.80


def _embed_vec(text: str):
    """The claim as a vector, or None when no embedding model is available.

    Goes through `longterm._embed` rather than loading a model of its own: one
    embedding model, one place it can fail, and a machine where memory search
    is degraded has hypothesis novelty degraded in exactly the same way rather
    than in some new way nobody has noticed.
    """
    import numpy as np
    blob = longterm._embed(text or "")
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def check_novelty(claim: str, corpus: Iterable[str]) -> GateResult:
    """Is this new to Apex, or does it already know it?

    `corpus` is everything the answer could have been copied from: Apex's own
    memories, its vault, hypotheses it has already recorded, and — most
    importantly — the source passages this one was generated from. A "novel
    hypothesis" that paraphrases an input passage is extraction, not genesis,
    and it is the most likely thing to come back from a model asked to be
    original about material it was just shown.

    **The asymmetry is the whole design.** Lexical overlap is evidence of
    duplication and is not evidence of novelty: a paraphrase shares almost no
    words with its source, so a low lexical score means nothing at all. The
    lexical mode may therefore return `not novel`, and may never return
    `novel` — it returns `unknown`, which makes the hypothesis `unverified`.
    An embedding model turns that `unknown` into a real answer, which is the
    concrete reason to have one installed.
    """
    entries = [t for t in (corpus or []) if (t or "").strip()]
    claim = (claim or "").strip()
    if not claim:
        return GateResult("novelty", FAIL, "no claim to compare")
    if not entries:
        return GateResult(
            "novelty", UNKNOWN,
            "nothing to compare against — no memories, notes, prior hypotheses or "
            "source passages were supplied, so novelty was not established.",
            {"mode": "none", "compared": 0})

    vec = _embed_vec(claim)
    if vec is not None:
        import numpy as np
        best, best_i = 0.0, -1
        for i, text in enumerate(entries):
            other = _embed_vec(text)
            if other is None:
                continue
            score = float(np.dot(vec, other))
            if score > best:
                best, best_i = score, i
        detail = {"mode": SEMANTIC, "compared": len(entries),
                  "closest": round(best, 4),
                  "closest_text": entries[best_i][:200] if best_i >= 0 else ""}
        if best >= SEMANTIC_DUPLICATE:
            return GateResult("novelty", FAIL,
                              f"Apex already holds this idea (similarity {best:.2f}). "
                              f"Recall is not genesis.", detail)
        return GateResult("novelty", PASS,
                          f"nothing within {SEMANTIC_DUPLICATE:.2f} similarity across "
                          f"{len(entries)} entries (closest {best:.2f})", detail)

    claim_words = content_words(claim)
    best, best_i = 0.0, -1
    for i, text in enumerate(entries):
        if not claim_words:
            break
        covered = len(claim_words & content_words(text)) / len(claim_words)
        if covered > best:
            best, best_i = covered, i
    detail = {"mode": LEXICAL, "compared": len(entries), "closest": round(best, 4),
              "closest_text": entries[best_i][:200] if best_i >= 0 else ""}
    if best >= LEXICAL_DUPLICATE:
        return GateResult("novelty", FAIL,
                          f"{best * 100:.0f}% of this claim's words already appear "
                          f"together in something Apex holds.", detail)
    return GateResult(
        "novelty", UNKNOWN,
        f"no embedding model on this machine, so only word overlap could be "
        f"checked (closest {best * 100:.0f}%). Overlap can prove a duplicate; its "
        f"absence cannot prove novelty, because a paraphrase shares almost no "
        f"words with its source. Novelty is UNCHECKED, not established.", detail)


# ── gate 4: critique ────────────────────────────────────────────────────────

FATAL, SERIOUS, MINOR, NONE = "fatal", "serious", "minor", "none"
CRITIQUE_VERDICTS = (FATAL, SERIOUS, MINOR, NONE)


@dataclass(frozen=True)
class Critique:
    model: str
    verdict: str
    text: str = ""


def check_critique(critiques: Sequence[Critique], proposer_model: str,
                   *, models_available: int = 1) -> GateResult:
    """Has anything actually attacked this, and was it something else?

    A model critiquing its own proposal shares every blind spot with it. That
    is precisely the correlated-knowledge problem `agent/consensus.py` exists
    to measure, and it is why the distinction below matters:

      * a second model was configured and was not used  -> the caller skipped
        the point of the gate, which is a failure;
      * only one model exists                           -> the critique is real
        but weaker, and the honest answer is `unknown`.

    Collapsing those two into one verdict would either punish a single-model
    install for its configuration or let a multi-model install quietly
    self-review. They are different facts and get different answers.
    """
    if not critiques:
        return GateResult("critique", FAIL,
                          "never attacked. A hypothesis nobody tried to break is "
                          "not evidence of anything.")

    bad = [c.verdict for c in critiques if c.verdict not in CRITIQUE_VERDICTS]
    if bad:
        return GateResult("critique", FAIL,
                          f"critique verdicts must be one of "
                          f"{', '.join(CRITIQUE_VERDICTS)}; got {bad[:3]}")

    fatal = [c for c in critiques if c.verdict == FATAL]
    if fatal:
        return GateResult("critique", FAIL,
                          f"a fatal objection stands: "
                          f"{(fatal[0].text or 'no detail given')[:200]}",
                          {"by": fatal[0].model})

    others = [c for c in critiques if c.model and c.model != proposer_model]
    if not others:
        if models_available > 1:
            return GateResult(
                "critique", FAIL,
                f"only {proposer_model} reviewed its own proposal, and "
                f"{models_available} models are configured. A model shares every "
                f"blind spot with itself.")
        return GateResult(
            "critique", UNKNOWN,
            "only one model is configured, so the proposal was reviewed by the "
            "model that made it. That is a real critique but a correlated one; "
            "independent critique was NOT obtained.",
            {"self_critique": True})

    serious = [c for c in critiques if c.verdict == SERIOUS]
    return GateResult(
        "critique", PASS,
        f"survived {len(critiques)} critique(s) including "
        f"{len(others)} from another model"
        + (f"; {len(serious)} serious objection(s) recorded and unresolved"
           if serious else ""),
        {"independent": sorted({c.model for c in others})})


# ── adjudication ────────────────────────────────────────────────────────────

def adjudicate(gates: Sequence[GateResult]) -> tuple[str, str]:
    """(status, reason). `unknown` outranks `pass`, always.

    Same rule as `forge.Report.verdict` and `capabilities.can`: a check that
    did not run is not a check that passed. A hypothesis whose novelty could
    not be established is `unverified` — it may be perfectly good, and Apex
    does not know that, and saying so is the only honest option.
    """
    if not gates:
        return PROPOSED, "not adjudicated"
    failed = [g for g in gates if g.verdict == FAIL]
    if failed:
        return REJECTED, "; ".join(f"{g.gate}: {g.reason}" for g in failed)
    unknown = [g for g in gates if g.verdict == UNKNOWN]
    if unknown:
        return UNVERIFIED, "; ".join(f"{g.gate}: {g.reason}" for g in unknown)
    return STANDING, "; ".join(f"{g.gate}: {g.reason}" for g in gates)


# ── storage ─────────────────────────────────────────────────────────────────

def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at     REAL NOT NULL,
                question       TEXT NOT NULL DEFAULT '',
                claim          TEXT NOT NULL,
                prediction     TEXT NOT NULL DEFAULT '',
                refutation     TEXT NOT NULL DEFAULT '',
                method         TEXT NOT NULL DEFAULT '',
                proposer_model TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT 'proposed',
                reason         TEXT NOT NULL DEFAULT '',
                gates          TEXT NOT NULL DEFAULT '[]',
                searched_contrary INTEGER NOT NULL DEFAULT 0,
                adjudicated_at REAL NOT NULL DEFAULT 0,
                resolved_at    REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hypothesis_evidence (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                stance        TEXT NOT NULL,
                source_id     TEXT NOT NULL,
                note          TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hypothesis_critiques (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                created_at    REAL NOT NULL,
                model         TEXT NOT NULL DEFAULT '',
                verdict       TEXT NOT NULL DEFAULT 'none',
                text          TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hypothesis_observations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                created_at    REAL NOT NULL,
                outcome       TEXT NOT NULL,
                observation   TEXT NOT NULL DEFAULT '',
                status_before TEXT NOT NULL DEFAULT '',
                status_after  TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_hypotheses_status "
                  "ON hypotheses(status, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_hyp_evidence "
                  "ON hypothesis_evidence(hypothesis_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_hyp_critiques "
                  "ON hypothesis_critiques(hypothesis_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_hyp_observations "
                  "ON hypothesis_observations(hypothesis_id)")


def record(*, claim: str, prediction: str, refutation: str, method: str,
           question: str = "", proposer_model: str = "",
           evidence: Sequence[Evidence] = (),
           searched_contrary: bool = False) -> int:
    """Store a proposal. Deliberately does NOT adjudicate it.

    Proposing and judging are separate calls because they answer to different
    things: a proposal is whatever a model said, and a verdict depends on a
    corpus and a resolver the model has no access to. Merging them would let a
    generator's output arrive pre-approved.
    """
    init_db()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO hypotheses (created_at, question, claim, prediction, "
            "refutation, method, proposer_model, status, searched_contrary) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), question or "", claim or "", prediction or "",
             refutation or "", method or "", proposer_model or "", PROPOSED,
             1 if searched_contrary else 0))
        hid = int(cur.lastrowid)
        for e in evidence:
            c.execute("INSERT INTO hypothesis_evidence "
                      "(hypothesis_id, stance, source_id, note) VALUES (?,?,?,?)",
                      (hid, e.stance, e.source_id, e.note or ""))
    return hid


def add_critique(hypothesis_id: int, model: str, verdict: str, text: str = "") -> None:
    init_db()
    with longterm._conn() as c:
        c.execute("INSERT INTO hypothesis_critiques "
                  "(hypothesis_id, created_at, model, verdict, text) VALUES (?,?,?,?,?)",
                  (int(hypothesis_id), time.time(), model or "",
                   verdict or NONE, text or ""))


def evidence_of(hypothesis_id: int) -> list[Evidence]:
    init_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT stance, source_id, note FROM hypothesis_evidence "
                         "WHERE hypothesis_id=? ORDER BY id",
                         (int(hypothesis_id),)).fetchall()
    return [Evidence(r[0], r[1], r[2]) for r in rows]


def critiques_of(hypothesis_id: int) -> list[Critique]:
    init_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT model, verdict, text FROM hypothesis_critiques "
                         "WHERE hypothesis_id=? ORDER BY id",
                         (int(hypothesis_id),)).fetchall()
    return [Critique(r[0], r[1], r[2]) for r in rows]


def observations_of(hypothesis_id: int) -> list[dict]:
    init_db()
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT created_at, outcome, observation, status_before, status_after "
            "FROM hypothesis_observations WHERE hypothesis_id=? ORDER BY id",
            (int(hypothesis_id),)).fetchall()
    return [{"created_at": r[0], "outcome": r[1], "observation": r[2],
             "status_before": r[3], "status_after": r[4]} for r in rows]


_FIELDS = ("id", "created_at", "question", "claim", "prediction", "refutation",
           "method", "proposer_model", "status", "reason", "gates",
           "searched_contrary", "adjudicated_at", "resolved_at")


def get(hypothesis_id: int) -> Optional[dict]:
    init_db()
    with longterm._conn() as c:
        row = c.execute(f"SELECT {', '.join(_FIELDS)} FROM hypotheses WHERE id=?",
                        (int(hypothesis_id),)).fetchone()
    if not row:
        return None
    data = dict(zip(_FIELDS, row))
    try:
        data["gates"] = json.loads(data["gates"] or "[]")
    except ValueError:
        data["gates"] = []
    return data


def listing(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    init_db()
    q = f"SELECT {', '.join(_FIELDS)} FROM hypotheses"
    args: list = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with longterm._conn() as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for row in rows:
        data = dict(zip(_FIELDS, row))
        try:
            data["gates"] = json.loads(data["gates"] or "[]")
        except ValueError:
            data["gates"] = []
        out.append(data)
    return out


# ── review: run every gate and write the verdict down ───────────────────────

def review(hypothesis_id: int, *, corpus: Iterable[str] = (),
           resolves: Optional[Callable[[str], bool]] = None,
           models_available: int = 1) -> dict:
    """Run all four gates against a stored proposal and persist the outcome.

    Returns the updated record. The gate results are stored verbatim, not just
    the final status, because "rejected" without the objection is unactionable
    and because a later run against a better corpus should be comparable with
    this one.

    A rejected hypothesis CAN be reviewed again — that is how an objection gets
    answered. A resolved one cannot: an observation has already settled it.
    """
    data = get(hypothesis_id)
    if data is None:
        raise ValueError(f"no hypothesis {hypothesis_id}")
    if data["status"] in RESOLVED:
        # An observation has already settled this. The gates decide whether a
        # claim is worth testing; they do not get to overrule the test.
        return data

    gates = [
        check_structure(data["claim"], data["prediction"],
                        data["refutation"], data["method"]),
        check_evidence(evidence_of(hypothesis_id),
                       resolves if resolves is not None else (lambda _s: False),
                       searched_contrary=bool(data["searched_contrary"])),
        # A stored hypothesis is in `listing()`, so a corpus built from Apex's
        # own records contains this claim verbatim — and reviewing it would
        # reject it for being identical to itself, at similarity 1.0. Filtered
        # here as well as in `corpus_for(exclude_id=...)` because a caller that
        # assembles its own corpus should not have to know this.
        check_novelty(data["claim"],
                      [t for t in corpus if (t or "").strip() != data["claim"].strip()]),
        check_critique(critiques_of(hypothesis_id), data["proposer_model"],
                       models_available=models_available),
    ]
    status, reason = adjudicate(gates)
    payload = json.dumps([{"gate": g.gate, "verdict": g.verdict,
                           "reason": g.reason, "detail": g.detail} for g in gates])
    with longterm._conn() as c:
        c.execute("UPDATE hypotheses SET status=?, reason=?, gates=?, "
                  "adjudicated_at=? WHERE id=?",
                  (status, reason, payload, time.time(), int(hypothesis_id)))
    return get(hypothesis_id)


# ── the loop that closes: observation ───────────────────────────────────────

MATCHED_PREDICTION = "matched_prediction"
MATCHED_REFUTATION = "matched_refutation"
INCONCLUSIVE = "inconclusive"
OUTCOMES = (MATCHED_PREDICTION, MATCHED_REFUTATION, INCONCLUSIVE)

OBSERVABLE = (STANDING, UNVERIFIED, SUPPORTED, REFUTED)


def observe(hypothesis_id: int, observation: str, outcome: str) -> dict:
    """Record what actually happened, and move the hypothesis if it should move.

    The transition rules are Popper's, and the asymmetry in them is the point:

      standing/unverified + prediction  -> supported
      standing/unverified + refutation  -> refuted
      supported           + refutation  -> refuted
      refuted             + anything    -> still refuted

    A supported hypothesis can be refuted later by a single contrary
    observation. A refuted one is NOT restored by a confirming one — you do not
    get to un-refute a claim by finding a case where it held. Accumulated
    support never outranks one clean refutation, and a system that let it would
    drift towards whatever it had looked at most.

    `proposed` and `rejected` cannot be observed at all: one has not been
    judged, and the other failed a gate — there is no vetted refutation to
    match an observation against.
    """
    data = get(hypothesis_id)
    if data is None:
        raise ValueError(f"no hypothesis {hypothesis_id}")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    before = data["status"]
    if before not in OBSERVABLE:
        raise ValueError(
            f"hypothesis {hypothesis_id} is '{before}' — "
            + ("it has not been reviewed yet, so there is no vetted refutation "
               "for an observation to match" if before == PROPOSED
               else "it failed a gate, so testing it would be testing something "
                    "Apex has already said is not a hypothesis"))

    # One rule per line, and no redundant one. An earlier version opened with
    # `if before == REFUTED: after = REFUTED`, which reads like the guard that
    # keeps a refutation standing — and was doing nothing, because the
    # prediction branch below already excludes REFUTED. A revert audit removed
    # it and every test still passed, which is how it was found.
    after = before
    if outcome == MATCHED_REFUTATION:
        after = REFUTED
    elif outcome == MATCHED_PREDICTION and before in (STANDING, UNVERIFIED):
        after = SUPPORTED

    with longterm._conn() as c:
        c.execute("INSERT INTO hypothesis_observations (hypothesis_id, created_at, "
                  "outcome, observation, status_before, status_after) "
                  "VALUES (?,?,?,?,?,?)",
                  (int(hypothesis_id), time.time(), outcome, observation or "",
                   before, after))
        if after != before:
            c.execute("UPDATE hypotheses SET status=?, resolved_at=? WHERE id=?",
                      (after, time.time(), int(hypothesis_id)))
    return get(hypothesis_id)


def due(limit: int = 10) -> list[dict]:
    """Hypotheses waiting on an observation — what Apex predicted and can check.

    Oldest first, deliberately: the point of this list is that a prediction
    made three weeks ago is the one whose answer is now available, and a
    newest-first list would bury it under today's ideas forever.
    """
    init_db()
    with longterm._conn() as c:
        rows = c.execute(
            f"SELECT {', '.join(_FIELDS)} FROM hypotheses "
            f"WHERE status IN (?, ?) ORDER BY id ASC LIMIT ?",
            (STANDING, UNVERIFIED, int(limit))).fetchall()
    return [dict(zip(_FIELDS, r)) for r in rows]


# ── describing ──────────────────────────────────────────────────────────────

_STATUS_LINE = {
    PROPOSED: "PROPOSED — not yet judged",
    REJECTED: "REJECTED",
    UNVERIFIED: "UNVERIFIED — a check could not be run",
    STANDING: "STANDING — testable, untested",
    SUPPORTED: "SUPPORTED by observation",
    REFUTED: "REFUTED — which is the system working",
}


def describe(data: dict, *, full: bool = True) -> str:
    """One hypothesis as text, for a person or a tool result."""
    if not data:
        return "No such hypothesis."
    hid = data.get("id")
    lines = [f"#{hid} {_STATUS_LINE.get(data.get('status'), data.get('status'))}",
             f"  claim:      {data.get('claim', '')}"]
    if not full:
        return "\n".join(lines)
    lines += [
        f"  prediction: {data.get('prediction', '')}",
        f"  refutation: {data.get('refutation', '')}",
        f"  method:     {data.get('method', '')}",
    ]
    if data.get("proposer_model"):
        lines.append(f"  proposed by {data['proposer_model']}")
    ev = evidence_of(hid) if hid else []
    if ev:
        lines.append("  evidence:")
        for e in ev:
            lines.append(f"    [{e.stance}] {e.source_id}"
                         + (f" — {e.note}" if e.note else ""))
    elif data.get("searched_contrary"):
        lines.append("  evidence: contrary evidence searched for, none found")
    cs = critiques_of(hid) if hid else []
    for c in cs:
        lines.append(f"  critique ({c.model}, {c.verdict}): {(c.text or '')[:200]}")
    for g in data.get("gates") or []:
        mark = {PASS: "OK  ", FAIL: "FAIL", UNKNOWN: "????"}.get(g.get("verdict"), "?")
        lines.append(f"  [{mark}] {g.get('gate')}: {g.get('reason')}")
    for o in observations_of(hid) if hid else []:
        lines.append(f"  observed ({o['outcome']}): {(o['observation'] or '')[:200]}"
                     + (f"  -> {o['status_after']}"
                        if o["status_after"] != o["status_before"] else ""))
    return "\n".join(lines)


# ── the corpus a claim has to be new against ────────────────────────────────

def corpus_for(question: str, sources: Sequence[str] = (), *,
               limit: int = 20, exclude_id: Optional[int] = None) -> list[str]:
    """Everything this claim could have been copied from.

    The source passages come FIRST and matter most. A model asked to be
    original about material it was just shown will, more often than any other
    failure, hand back a paraphrase of that material — and a paraphrase of an
    input is extraction, not genesis. Memories, vault notes and prior claims
    catch the other direction: Apex rediscovering something it already knows.

    Every lookup is wrapped, because a novelty check is not worth crashing a
    proposal over — but note what a failure costs. A smaller corpus makes the
    check WEAKER, and `check_novelty` reports how many entries it compared
    against so a silently empty corpus shows up as "compared: 0" rather than
    as a clean pass.
    """
    texts: list[str] = [s for s in sources if (s or "").strip()]

    for h in listing(limit=200):
        if exclude_id is not None and h.get("id") == exclude_id:
            continue
        if h.get("claim"):
            texts.append(h["claim"])

    try:
        texts += [m.get("content", "") for m in longterm.recall(question, limit=limit)]
    except Exception as e:
        print(f"[Genesis] memory lookup failed, novelty checked against less: {e}")
    try:
        from agent import vault_index
        res = vault_index.search(question, limit=limit) or {}
        for r in res.get("results", []):
            texts.append(f"{r.get('title', '')} {r.get('excerpt', '')}".strip())
    except Exception as e:
        print(f"[Genesis] vault lookup failed, novelty checked against less: {e}")

    return [t for t in texts if (t or "").strip()]


def source_resolver(sources: Sequence[dict]) -> Callable[[str], bool]:
    """A resolver over a supplied source list: `{"id": ..., "text": ...}`."""
    known = {str(s.get("id", "")).strip() for s in sources if s.get("id")}
    return lambda sid: str(sid).strip() in known


# ── talking to a model ──────────────────────────────────────────────────────

class GenesisError(RuntimeError):
    """A model produced something this module will not pretend to understand."""


PROPOSE_SYSTEM = """You generate falsifiable hypotheses. You are not writing an \
essay, a summary, or a research proposal.

Return ONLY a JSON array. Each element:
{"claim": "...", "prediction": "...", "refutation": "...", "method": "...",
 "evidence_for": ["source-id", ...], "evidence_against": ["source-id", ...],
 "searched_contrary": true}

Rules, each of which is enforced in code and will cause rejection:

- claim: ONE statement about the world that could turn out to be false. Not a
  question, not a recommendation. Do not hedge it with "may", "might", "could",
  "possibly" or "potentially" — a claim compatible with every observation is
  not a hypothesis.
- refutation: the OBSERVATION that would kill the claim. Name a measurement, a
  population, a threshold, a time. "If the data does not support it" is not a
  refutation; it restates what being false means. Neither is the claim negated.
- method: how to actually get that observation.
- evidence_for / evidence_against: ids from the SOURCES given to you, exactly as
  written. Never invent one. A citation that does not resolve is fatal.
- searched_contrary: true only if you actually looked for evidence against the
  claim in the sources. Finding none is fine; not looking is not.

Prefer a claim the sources do not already state. A restatement of a source is
extraction, not a hypothesis, and is checked for."""

CRITIQUE_SYSTEM = """You are attacking a hypothesis, not improving it. Find the \
reason it is wrong, unfalsifiable, already known, or not supported by its cited \
evidence.

Return ONLY JSON: {"verdict": "fatal|serious|minor|none", "text": "..."}

- fatal: the hypothesis cannot stand — it is circular, unfalsifiable as stated,
  contradicted by the cited evidence, or already established.
- serious: a real problem that must be answered before testing is worthwhile.
- minor: worth noting, does not block.
- none: you tried and found nothing. Say what you checked."""


def _json_array(raw: str) -> list:
    """Parse a JSON array, or raise. Never returns [] for unparseable text.

    `deepresearch._json_list` returns an empty list when it cannot parse, which
    is right there — a sweep that finds nothing is normal. Here it would be a
    trap: "the model replied with prose" and "the model proposed nothing"
    would become the same observable event, and a generator that had stopped
    working would report success with zero results forever.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    try:
        out = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            raise GenesisError(
                f"the model did not return JSON. First 200 characters: "
                f"{text[:200]!r}")
        try:
            out = json.loads(m.group(0))
        except ValueError as e:
            raise GenesisError(f"the model returned malformed JSON: {e}")
    if not isinstance(out, list):
        raise GenesisError(f"expected a JSON array, got {type(out).__name__}")
    return out


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    try:
        out = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise GenesisError(
                f"the model did not return JSON. First 200 characters: "
                f"{text[:200]!r}")
        try:
            out = json.loads(m.group(0))
        except ValueError as e:
            raise GenesisError(f"the model returned malformed JSON: {e}")
    if not isinstance(out, dict):
        raise GenesisError(f"expected a JSON object, got {type(out).__name__}")
    return out


def _sources_block(sources: Sequence[dict]) -> str:
    if not sources:
        return "SOURCES: none were supplied."
    parts = ["SOURCES (cite by id, exactly as written):"]
    for s in sources:
        parts.append(f"[{s.get('id')}] {str(s.get('text', ''))[:2000]}")
    return "\n\n".join(parts)


def propose(question: str, sources: Sequence[dict] = (), *,
            model: str = "", count: int = 3) -> list[int]:
    """Ask a model for hypotheses and STORE them, unjudged. Returns their ids.

    Nothing here decides whether the output is any good. That is `review`'s
    job, and keeping them apart is what stops a generator's own confidence
    from becoming a verdict.
    """
    from agent import provider
    import config
    model = model or config.AGENT_MODEL
    user = (f"QUESTION: {question}\n\n{_sources_block(sources)}\n\n"
            f"Return at most {int(count)} hypotheses as a JSON array.")
    raw = provider.complete(model, PROPOSE_SYSTEM, user, max_tokens=2000)
    items = _json_array(raw)

    ids = []
    for item in items[:int(count)]:
        if not isinstance(item, dict):
            continue
        evidence = [Evidence(FOR, str(s)) for s in (item.get("evidence_for") or [])]
        evidence += [Evidence(AGAINST, str(s)) for s in (item.get("evidence_against") or [])]
        ids.append(record(
            claim=str(item.get("claim", "")),
            prediction=str(item.get("prediction", "")),
            refutation=str(item.get("refutation", "")),
            method=str(item.get("method", "")),
            question=question, proposer_model=model, evidence=evidence,
            searched_contrary=bool(item.get("searched_contrary"))))
    if items and not ids:
        raise GenesisError(
            f"the model returned {len(items)} item(s), none of them objects with "
            f"the required fields.")
    return ids


def critique_with(hypothesis_id: int, models: Sequence[str]) -> list[Critique]:
    """Have each model attack the hypothesis, and record what it said.

    A model that fails to answer is recorded as nothing at all rather than as a
    clean review — `check_critique` then sees fewer critics, which is the
    truth. Writing a `none` verdict for a call that errored would manufacture
    an independent review out of a network failure.
    """
    from agent import provider
    data = get(hypothesis_id)
    if data is None:
        raise ValueError(f"no hypothesis {hypothesis_id}")
    prompt = (f"CLAIM: {data['claim']}\nPREDICTION: {data['prediction']}\n"
              f"REFUTATION: {data['refutation']}\nMETHOD: {data['method']}\n"
              f"CITED EVIDENCE: "
              f"{', '.join(f'{e.stance}:{e.source_id}' for e in evidence_of(hypothesis_id)) or 'none'}")
    out = []
    for m in models:
        try:
            raw = provider.complete(m, CRITIQUE_SYSTEM, prompt, max_tokens=800)
            parsed = _json_object(raw)
        except Exception as e:
            print(f"[Genesis] {m} did not critique #{hypothesis_id}: {e}")
            continue
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in CRITIQUE_VERDICTS:
            print(f"[Genesis] {m} returned verdict {verdict!r}, which is not one "
                  f"of {', '.join(CRITIQUE_VERDICTS)} — not recorded.")
            continue
        c = Critique(m, verdict, str(parsed.get("text", "")))
        add_critique(hypothesis_id, c.model, c.verdict, c.text)
        out.append(c)
    return out


def critic_models(proposer_model: str) -> list[str]:
    """Who can attack this, preferring anyone who is not the proposer.

    Reuses the council's roster rather than keeping a second list of which
    providers are configured — `agent/council.py` already answers that, and
    two answers to it would drift.
    """
    try:
        from agent import council
        available = [m for m, _label in council.available_members()]
    except Exception:
        available = []
    others = [m for m in available if m != proposer_model]
    return others or ([proposer_model] if proposer_model else [])
