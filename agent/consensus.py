"""How much did the council actually agree — and what that is worth.

CLAUDE.md §41 states the problem exactly:

    Three models agreeing on outdated information is still wrong. Council Mode
    should not create false confidence from correlated model knowledge.

Apex's council runs an opening round where every member answers independently,
then debates. The opening round is the only place agreement means anything: once
members read each other, convergence is contamination, not corroboration.

## What this measures, and what it cannot

It measures **agreement**: how much the independent answers overlap, and which
specific claims — figures, dates, names — every member produced.

It does **not** measure independence, and no amount of arithmetic here could.
Three models trained on overlapping corpora will agree on a stale fact exactly as
strongly as on a true one; the texts are identical either way. That is not a
limitation to be engineered around, it is the shape of the problem.

So the output is deliberately not a confidence score. High agreement on a
verifiable specific is reported as **a reason to verify**, not as evidence. A
number is the most dangerous thing a council can agree on: it looks like
precision, it is trivially checkable, and it goes stale silently.
"""
from __future__ import annotations

import re
from typing import Iterable

# Words carrying no topical signal. Kept short deliberately — an aggressive list
# would inflate the overlap score by discarding everything the answers share.
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "for", "with", "as", "by", "at", "from", "it", "its",
    "you", "your", "i", "we", "they", "he", "she", "not", "no", "yes", "can",
    "will", "would", "should", "could", "may", "might", "must", "do", "does",
    "did", "have", "has", "had", "there", "their", "what", "which", "who",
    "when", "where", "how", "why", "so", "such", "more", "most", "some", "any",
    "all", "each", "other", "into", "about", "also", "very", "much", "many",
}

_WORD = re.compile(r"[a-z][a-z'-]{2,}")
# A figure, a year, a percentage, a version — the things that look precise.
_SPECIFIC = re.compile(
    r"\b\d{4}\b"                       # years
    r"|\b\d+(?:\.\d+)?\s?%"            # percentages
    r"|\b\d+(?:\.\d+)?\s?(?:bn|billion|m|million|k|thousand)\b"
    r"|\$\s?\d+(?:[.,]\d+)*"           # money
    r"|\bv?\d+\.\d+(?:\.\d+)?\b",      # versions
    re.IGNORECASE,
)

# Language that dates an answer. Agreement on one of these is the §41 case.
_TIME_SENSITIVE = re.compile(
    r"\b(currently|as of|latest|newest|most recent|right now|today|this year|"
    r"at present|nowadays|the current)\b", re.IGNORECASE)

HIGH_OVERLAP = 0.45      # above this, treat the answers as correlated
MIN_MEMBERS = 2


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def _specifics(text: str) -> set[str]:
    return {m.group(0).strip().lower() for m in _SPECIFIC.finditer(text or "")}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def opening_answers(transcript: Iterable[dict]) -> dict[str, str]:
    """Round 0 only. Later rounds are contaminated by definition — members have
    read each other by then, so their convergence measures persuasion."""
    return {t["label"]: t.get("text", "")
            for t in (transcript or [])
            if isinstance(t, dict) and t.get("round") == 0 and t.get("label")}


def agreement(transcript: Iterable[dict]) -> dict:
    """Measure agreement across the council's independent answers.

    Returns a dict with `overlap` (0-1), the specifics every member stated,
    whether the answers are time-sensitive, and `verify` — a list of claims that
    should be checked externally before being trusted.
    """
    answers = opening_answers(transcript)
    labels = sorted(answers)
    if len(labels) < MIN_MEMBERS:
        return {
            "members": labels, "overlap": None, "correlated": None,
            "unanimous_specifics": [], "time_sensitive": False, "verify": [],
            "note": "Fewer than two independent answers — nothing to compare.",
        }

    words = {l: _content_words(answers[l]) for l in labels}
    pairs = [(labels[i], labels[j])
             for i in range(len(labels)) for j in range(i + 1, len(labels))]
    scores = [_jaccard(words[a], words[b]) for a, b in pairs]
    overlap = sum(scores) / len(scores)

    spec_sets = [_specifics(answers[l]) for l in labels]
    unanimous = set.intersection(*spec_sets) if spec_sets else set()
    time_sensitive = any(_TIME_SENSITIVE.search(answers[l]) for l in labels)

    # What actually warrants an external check: a specific figure that every
    # member produced. Unanimity makes it *look* settled; it is checkable, so
    # check it.
    verify = sorted(unanimous)

    correlated = overlap >= HIGH_OVERLAP

    # Order matters here, and the first branch is the §41 case.
    #
    # The first version keyed the warning on lexical overlap, and so missed the
    # dangerous shape entirely: two models that phrase an answer differently but
    # produce the same stale figure scored 0.22 overlap and got a mild note. But
    # matching *conclusions* is the correlation that costs you something —
    # matching *wording* only says the models write alike. Unanimity on a
    # checkable specific outranks prose similarity.
    if unanimous and time_sensitive:
        note = ("Every member gave the same figure for a time-sensitive claim, in "
                "their own words. Independent phrasing is not independent "
                "knowledge — models share training data, so this is as consistent "
                "with one stale fact as with a correct one. Verify it.")
    elif correlated and time_sensitive:
        note = ("High agreement on time-sensitive claims. Models share training "
                "data, so this is as consistent with a shared stale fact as with "
                "a correct one. Verify before relying on it.")
    elif correlated:
        note = ("High agreement. That measures similarity of the models, not "
                "correctness — agreement is not evidence.")
    elif unanimous:
        note = ("Answers differ broadly but state the same specifics. Those "
                "figures are worth checking.")
    else:
        note = "Answers diverge. Treat the synthesis as one option, not a finding."

    return {
        "members": labels,
        "overlap": round(overlap, 3),
        "pairwise": {f"{a} vs {b}": round(s, 3) for (a, b), s in zip(pairs, scores)},
        "correlated": correlated,
        "unanimous_specifics": verify,
        "time_sensitive": time_sensitive,
        "verify": verify,
        "note": note,
    }


def format_for_user(report: dict) -> str:
    """One short paragraph. Never a confidence percentage — see the module
    docstring for why a number here would be a lie."""
    if report.get("overlap") is None:
        return report.get("note", "")
    lines = [f"Council agreement: {report['overlap']:.0%} lexical overlap across "
             f"{len(report['members'])} independent answers."]
    if report["verify"]:
        lines.append("Every member stated: " + ", ".join(report["verify"]) + ".")
    lines.append(report["note"])
    return " ".join(lines)
