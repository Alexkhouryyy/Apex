"""Whether Council Mode is earning its cost — measured, not assumed.

`convene()` runs whenever the model picks the tool or the user types
`/council`. Nothing decided whether it was worth it, and nothing recorded how
it went, so there was no evidence to decide from even in principle. The design
document is direct about this (§6.2): *"One of the strongest possible
differentiators is letting Apex learn when Council Mode is worth its latency
and cost. The router should maintain performance statistics by task type and
learn whether additional models materially improve outcomes."*

## The signal that is actually available

The honest question is not "was the council right" — that needs an outcome
nobody may ever report. It is **"did the extra models say anything different?"**
`agent/consensus.py` already measures that across the opening answers, before
the chair synthesizes anything, and `CouncilResult.agreement` carries it. Three
models that reliably produce the same answer for a class of task have added
latency and cost and no information; that is directly observable on every run,
immediately, without waiting for the world to report back.

So this module records each run's MEASURED overlap by task type, and reports:

* how often a council on this kind of question actually diverged;
* therefore whether convening one again is likely to tell you anything.

## What it deliberately does not do

**It does not refuse.** `advise()` returns a recommendation and the evidence
behind it; the decision stays with the caller. A router that silently declined
to convene would be making a correctness trade on cost grounds, invisibly —
and the design document's own warning applies in both directions: *"Council
agreement should not be treated as proof."* Agreement means the models did not
disagree, which is evidence about the models, not about the world.

**It does not score models on eloquence or on the chair's opinion.** The
chair's `confidence` is excluded on purpose: a chair that read three similar
answers reports high confidence *because* they were similar, so it cannot
also be the check on them. Only the independently measured overlap counts.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from agent import longterm

# Below this measured overlap, the members genuinely diverged and the council
# did work a single model would not have. Above it they largely restated each
# other. 0.6 is the midpoint of consensus.py's own scale and is a starting
# point to be revised from data, not a discovered constant — which is exactly
# what the stats below exist to enable.
DIVERGENCE_OVERLAP = 0.6

# How many runs of a task type before its history is worth acting on. Under
# this, "the council always agreed" is two coin flips, not a pattern.
MIN_RUNS_FOR_ADVICE = 4

_TASK_PATTERNS = (
    ("code", re.compile(r"\b(code|function|bug|refactor|api|regex|sql|traceback|"
                        r"compile|test|stack ?trace|exception)\b|```")),
    ("factual", re.compile(r"\b(what year|when did|who (is|was)|how many|"
                           r"capital of|define|meaning of)\b")),
    # `design` is checked BEFORE `decision` deliberately. Almost every design
    # question is phrased "should we ...", so with decision first the design
    # bucket was effectively unreachable — and a bucket that never fills never
    # reaches MIN_RUNS_FOR_ADVICE, so it can never produce advice at all.
    # Caught by a test that expected "how should we structure the auth layer?"
    # to be a design question, which it plainly is.
    ("design", re.compile(r"\b(architect|architecture|design|structure|"
                          r"approach|strategy|schema)\b")),
    ("decision", re.compile(r"\b(should i|should we|worth it|choose|decide|"
                            r"vs\.?|versus|better|trade[- ]?off|recommend)\b")),
    ("writing", re.compile(r"\b(write|draft|rewrite|edit|tone|wording|"
                           r"summar(y|ise|ize))\b")),
)


def task_type(question: str) -> str:
    """A coarse category for the question. Coarse on purpose: statistics need
    enough runs per bucket to mean anything, and a fine-grained taxonomy would
    give every question its own bucket of one."""
    q = (question or "").lower()
    for name, pattern in _TASK_PATTERNS:
        if pattern.search(q):
            return name
    return "general"


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS council_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                task_type TEXT NOT NULL,
                question TEXT NOT NULL,
                members TEXT NOT NULL DEFAULT '',
                member_count INTEGER NOT NULL DEFAULT 0,
                overlap REAL,
                diverged INTEGER,
                correlated INTEGER NOT NULL DEFAULT 0,
                chair_confidence TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_council_runs_type "
                  "ON council_runs(task_type, ts DESC)")


def record_run(question: str, members, agreement: Optional[dict],
               chair_confidence: Optional[str] = None) -> Optional[int]:
    """Record one convened council. Never raises — a bookkeeping failure must
    not cost the answer the council just produced.

    `overlap` may legitimately be None: a council of one, or a run where the
    transcript had fewer than two independent answers, has no overlap to
    measure. Stored as NULL rather than as a number, because a missing
    measurement and a measured zero are different facts.
    """
    try:
        overlap = None
        correlated = 0
        if isinstance(agreement, dict):
            raw = agreement.get("overlap")
            overlap = float(raw) if isinstance(raw, (int, float)) else None
            correlated = 1 if agreement.get("correlated") else 0
        diverged = None if overlap is None else int(overlap < DIVERGENCE_OVERLAP)
        names = list(members or [])
        with longterm._conn() as c:
            cur = c.execute(
                "INSERT INTO council_runs (ts, task_type, question, members, "
                "member_count, overlap, diverged, correlated, chair_confidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), task_type(question), (question or "")[:500],
                 ", ".join(str(n) for n in names), len(names), overlap,
                 diverged, correlated, (chair_confidence or "")))
            return int(cur.lastrowid)
    except Exception as e:
        print(f"[Council] could not record the run: {e}")
        return None


def stats(kind: Optional[str] = None, days: int = 180) -> dict:
    """What the record says about councils of this kind.

    `measured` counts only runs where overlap could actually be computed —
    runs without it are reported separately rather than being folded in as
    agreements, which would make a council of one look like unanimity.
    """
    cutoff = time.time() - days * 86400
    q = ("SELECT overlap, diverged, correlated FROM council_runs "
         "WHERE ts >= ?")
    args: list = [cutoff]
    if kind:
        q += " AND task_type = ?"
        args.append(kind)
    try:
        with longterm._conn() as c:
            rows = c.execute(q, args).fetchall()
    except Exception:
        rows = []

    measured = [r for r in rows if r[0] is not None]
    diverged = [r for r in measured if r[1]]
    correlated = [r for r in measured if r[2]]
    return {
        "task_type": kind or "all",
        "runs": len(rows),
        "measured": len(measured),
        "unmeasured": len(rows) - len(measured),
        "diverged": len(diverged),
        "divergence_rate": (round(len(diverged) / len(measured), 3)
                            if measured else None),
        "correlated": len(correlated),
        "mean_overlap": (round(sum(r[0] for r in measured) / len(measured), 3)
                         if measured else None),
    }


def advise(question: str, days: int = 180) -> dict:
    """Should a council be convened for this question, on the evidence so far?

    Returns `{"convene": bool|None, "confidence": str, "reason": str, ...}`.
    `convene` is None when there is not enough history to have an opinion —
    which is a real answer, and the honest one for a new task type. Guessing
    from two runs would give the recommendation an authority it has not
    earned, and the whole point of this module is to stop Council usage being
    decided by vibes.
    """
    kind = task_type(question)
    s = stats(kind, days=days)

    if s["measured"] < MIN_RUNS_FOR_ADVICE:
        return {
            "task_type": kind, "convene": None, "confidence": "none",
            "reason": (f"Only {s['measured']} measured council run(s) on "
                       f"'{kind}' questions — not enough to say whether extra "
                       f"models change the answer here. Convene if the "
                       f"question warrants it; the run will add evidence."),
            "stats": s,
        }

    rate = s["divergence_rate"] or 0.0
    # A dead-even split is NOT evidence for convening — it is the definition
    # of no signal. The first version used `>= 0.5` here and read a 50/50
    # record as a recommendation, which is how a coin flip becomes a policy.
    if rate >= 0.6:
        return {
            "task_type": kind, "convene": True, "confidence": "measured",
            "reason": (f"On '{kind}' questions the members genuinely diverged "
                       f"in {s['diverged']} of {s['measured']} runs "
                       f"({rate:.0%}). A council here has been doing real work."),
            "stats": s,
        }
    if rate <= 0.2:
        return {
            "task_type": kind, "convene": False, "confidence": "measured",
            "reason": (f"On '{kind}' questions the members restated each other "
                       f"in {s['measured'] - s['diverged']} of {s['measured']} "
                       f"runs (diverged only {rate:.0%}). A council here has "
                       f"mostly bought latency and cost, not information — but "
                       f"agreement is evidence about the models, not proof "
                       f"they were right."),
            "stats": s,
        }
    return {
        "task_type": kind, "convene": None, "confidence": "mixed",
        "reason": (f"'{kind}' questions split about evenly (diverged {rate:.0%} "
                   f"of {s['measured']} runs). No clear signal either way; "
                   f"judge this one on its stakes rather than on the history."),
        "stats": s,
    }


def summary(days: int = 180) -> str:
    """Every task type's record, for the model and the dashboard to read."""
    try:
        with longterm._conn() as c:
            kinds = [r[0] for r in c.execute(
                "SELECT DISTINCT task_type FROM council_runs "
                "WHERE ts >= ? ORDER BY task_type",
                (time.time() - days * 86400,)).fetchall()]
    except Exception:
        kinds = []
    if not kinds:
        return ("No council runs recorded yet. Convene a few and this becomes "
                "evidence about whether they are worth convening.")
    lines = [f"Council record over {days} days:"]
    for k in kinds:
        s = stats(k, days=days)
        if not s["measured"]:
            lines.append(f"  {k}: {s['runs']} run(s), none measurable")
            continue
        lines.append(
            f"  {k}: diverged {s['diverged']}/{s['measured']} "
            f"({(s['divergence_rate'] or 0):.0%}), mean overlap "
            f"{s['mean_overlap']}")
    lines.append("Divergence is the signal: members that always agree added "
                 "no information, whatever the chair's confidence said.")
    return "\n".join(lines)
