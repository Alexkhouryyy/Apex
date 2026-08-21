"""The gap analysis may not cite evidence that does not exist.

`docs/APEX_GAP_ANALYSIS.md` labels every section of the proposed CLAUDE.md, and
its central distinction is WORKS (something demonstrates this executing) versus
UNPROVEN (it exists, but nothing proves it runs). That distinction is worth
nothing if a WORKS row can cite a test that was never written, or a file that has
since been deleted.

Every audit in this repository has had to meet the same standard: it must fail on
the thing it exists to catch. Two of the three failed that on their first attempt
— the wiring audit reported itself, and the SQL audit missed the very query it
was written for. So this checks the document rather than trusting it:

  * every `path.py` or `path.py:line` citation resolves to a real file
  * every `tests/test_*.py` citation is a real test file
  * every smoke check named as evidence is registered in tools/smoke.py
  * the summary counts match the rows actually present

A gap analysis is a snapshot, and snapshots rot into lies. This is what stops it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "APEX_GAP_ANALYSIS.md"


@pytest.fixture(scope="module")
def text() -> str:
    assert DOC.exists(), f"{DOC} is missing"
    return DOC.read_text(errors="replace")


def _cited_paths(text: str) -> set[str]:
    """Backtick-quoted things that look like repository paths."""
    out = set()
    for token in re.findall(r"`([^`]+)`", text):
        token = token.strip()
        # strip a trailing :line and any prose after the path
        token = token.split()[0].rstrip(",;.")
        token = re.sub(r":\d+$", "", token)
        # Must contain a directory separator to count as a citation. A bare
        # filename in prose ("CLAUDE.md" — the proposal, which is not in the
        # repo; "me.md" — a file Apex generates at runtime) is a reference, not
        # a claim about this repository.
        if "/" in token and re.fullmatch(r"[\w./-]+\.(py|js|sh|md|service|webmanifest)", token):
            out.add(token)
    return out


def test_every_cited_file_exists(text):
    """The regression this file exists for: a citation that resolves to nothing
    is indistinguishable from evidence until someone checks."""
    missing = sorted(p for p in _cited_paths(text) if not (REPO / p).exists())
    assert not missing, f"gap analysis cites files that do not exist: {missing}"


def test_every_cited_test_file_is_real(text):
    cited = {p for p in _cited_paths(text) if p.startswith("tests/")}
    assert cited, "no test files cited — WORKS rows would have no evidence"
    missing = sorted(p for p in cited if not (REPO / p).exists())
    assert not missing, f"cites non-existent tests: {missing}"


def test_every_named_smoke_check_is_registered(text):
    """Smoke checks are the strongest evidence a row can cite, because they
    prove the feature ran in a real boot. A renamed check must not silently
    leave a WORKS row unsupported."""
    smoke = (REPO / "tools" / "smoke.py").read_text(errors="replace")
    registered = set(re.findall(r"^def ([a-z_]+)\(r: BootResult\)", smoke, re.M))
    assert registered, "could not read smoke checks — the parser has drifted"

    named = set(re.findall(r"smoke check `([a-z_]+)`", text))
    assert named, "no smoke checks cited"
    unknown = sorted(named - registered)
    assert not unknown, f"cites smoke checks that are not registered: {unknown}"


def test_summary_counts_match_the_rows(text):
    """A summary that disagrees with the table is the document lying about
    itself — the same shape as a green audit over broken code."""
    summary = {}
    block = text.split("## Summary", 1)[1].split("\n---\n", 1)[0]
    for label, count in re.findall(r"\|\s*(WORKS|UNPROVEN|PARTIAL|OFF|MISSING|NEEDS_REFACTOR)\s*\|\s*(\d+)\s*\|", block):
        summary[label] = int(count)
    assert summary, "no summary table found"

    # Count labels in the body tables only — bold markers and the legend excluded.
    body = text.split("## Core & Reasoning", 1)[1]
    actual = {}
    for row in body.splitlines():
        m = re.match(r"\|[^|]*\|[^|]*\|\s*\*{0,2}(WORKS|UNPROVEN|PARTIAL|OFF|MISSING|NEEDS_REFACTOR)\*{0,2}\s*\|", row)
        if m:
            actual[m.group(1)] = actual.get(m.group(1), 0) + 1

    mismatches = {k: (summary.get(k, 0), actual.get(k, 0))
                  for k in set(summary) | set(actual)
                  if summary.get(k, 0) != actual.get(k, 0)}
    assert not mismatches, (
        "summary disagrees with the table (label: claimed vs actual): " + str(mismatches))


def test_unproven_rows_name_what_is_missing(text):
    """UNPROVEN is only useful if it says what evidence would settle it.
    A bare label is a shrug."""
    body = text.split("## Core & Reasoning", 1)[1]
    bare = []
    for row in body.splitlines():
        if re.search(r"\|\s*\*{0,2}UNPROVEN\*{0,2}\s*\|", row):
            evidence = row.rsplit("|", 2)[-2].strip()
            if len(evidence) < 15:
                bare.append(row.strip()[:70])
    assert not bare, f"UNPROVEN rows with no explanation: {bare}"
