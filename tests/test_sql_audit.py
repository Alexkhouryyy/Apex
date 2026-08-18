"""No query may name a column its table does not have.

`agent/reflection.py` ran `SELECT name, kind, properties FROM entities` for as
long as it existed. The column is `properties_json`. Every call raised
OperationalError; the caller caught it, printed one line, and carried on — so
the profile digest never once produced a file, on any machine. It surfaced only
because a user read their own boot log:

    [Reflection] Profile digest failed: no such column: properties

That is the ninth thing in this codebase built and never run. Unlike the others,
this class is mechanically detectable, which is what tools/sql_audit.py does.
"""
from __future__ import annotations

import pytest

from tools import sql_audit


@pytest.fixture(scope="module")
def result():
    return sql_audit.audit()


def test_no_query_names_a_missing_column(result):
    if result["bad"]:
        lines = "\n".join(
            f"  {b['file']}:{b['line']} — {b['table']}.{b['column']} does not exist"
            + (f" (did you mean {b['did_you_mean']}?)" if b["did_you_mean"] else "")
            for b in result["bad"])
        pytest.fail(f"{len(result['bad'])} query/queries name missing columns:\n{lines}")


def test_the_audit_actually_reads_something(result):
    """A scanner that silently matches nothing passes forever. Guard the guard."""
    assert result["tables"] > 20, "schema probe built almost no tables"
    assert result["checked"] > 50, (
        f"only {result['checked']} queries checked — the scanner has probably "
        f"stopped matching, so a green result means nothing"
    )


def test_coverage_is_reported_not_hidden(result):
    """Queries too complex to check are counted and surfaced. A tool that
    skipped the hard ones while implying full coverage would be its own version
    of the bug it hunts."""
    assert "unchecked" in result
    assert result["unchecked"] > 0, "expected some joins/aliases to be unparsed"


def test_it_catches_a_planted_bad_column(tmp_path, monkeypatch):
    """The audit must fail on the shape it exists to find — including when the
    file also contains prose using the word 'select', which is what defeated the
    first version of this scanner."""
    bad = tmp_path / "planted.py"
    bad.write_text(
        '# we select the rows we want here\n'
        'def go(c):\n'
        '    return c.execute("SELECT name, kind, properties FROM entities").fetchall()\n'
    )
    monkeypatch.setattr(sql_audit, "_sources", lambda: iter([bad]))
    monkeypatch.setattr(sql_audit, "REPO", tmp_path)
    r = sql_audit.audit()
    cols = {b["column"] for b in r["bad"]}
    assert "properties" in cols, "planted bad column was not caught"
    assert any(b["did_you_mean"] == "properties_json" for b in r["bad"])


def test_a_correct_query_is_not_flagged(tmp_path, monkeypatch):
    good = tmp_path / "fine.py"
    good.write_text(
        'def go(c):\n'
        '    return c.execute("SELECT name, kind FROM entities").fetchall()\n'
    )
    monkeypatch.setattr(sql_audit, "_sources", lambda: iter([good]))
    monkeypatch.setattr(sql_audit, "REPO", tmp_path)
    r = sql_audit.audit()
    assert r["bad"] == []
    assert r["checked"] >= 1


def test_prose_is_not_mistaken_for_sql(tmp_path, monkeypatch):
    """Scanning raw file text made a comment reading 'so select what is actually
    used' swallow the real query on the next line — finditer does not overlap,
    so the genuine bug went unseen. Only string literals are read now."""
    f = tmp_path / "prosey.py"
    f.write_text(
        '"""We select from entities and read the columns we need."""\n'
        '# select name, kind, nonexistent_column from entities\n'
        'X = 1\n'
    )
    monkeypatch.setattr(sql_audit, "_sources", lambda: iter([f]))
    monkeypatch.setattr(sql_audit, "REPO", tmp_path)
    r = sql_audit.audit()
    assert r["bad"] == [], "prose in a comment was parsed as SQL"
