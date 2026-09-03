"""Retrieval over the vault, and the promise that makes it retrieval.

Blueprint Phase 2's success check is "Apex retrieves the correct project page
WITHOUT loading the full vault." `agent/vault.py` could already write notes and
read them back by title — but `list_notes()` and `read_note(title)` both
require you to already know which page you want, which is the thing the check
says you should not need to know.

The load-bearing test here is `TestSearchNeverOpensANote`. Reading every note
and picking the best would satisfy "retrieves the correct page" and fail the
sentence's second half, and it would fail it invisibly: it works on twelve
notes and costs real money on twelve hundred, with no point at which it
visibly stops being fine. So the test makes reading a note raise, and asserts
search still answers.
"""
from pathlib import Path

import numpy as np
import pytest

from agent import longterm, vault, vault_index as vi


@pytest.fixture
def a_vault(tmp_path, monkeypatch):
    """A real vault directory and a fresh database."""
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "vi.db"))
    root = tmp_path / "vault"
    (root / "Projects").mkdir(parents=True)
    (root / "Notes").mkdir(parents=True)
    monkeypatch.setattr(vault, "VAULT_DIR", root)
    vi.init_db()
    return root


def write(root: Path, folder: str, name: str, body: str) -> Path:
    p = root / folder / f"{name}.md"
    p.write_text(f"---\ntitle: {name}\ncreated: 2026-01-01\n---\n\n{body}\n")
    return p


class _FakeModel:
    """A deterministic stand-in for sentence-transformers.

    Not installed in this container, and the point of these tests is the
    plumbing around the vectors — that the right rows are stored, scored and
    swept — not the quality of an embedding. A fake keeps the assertions about
    behaviour Apex controls. Vectors are unit bag-of-chars, so texts sharing
    words score higher, which is enough to make ranking meaningful.
    """
    def encode(self, texts, normalize_embeddings=True):
        import re as _re
        import zlib
        out = []
        for t in texts:
            v = np.zeros(1024, dtype=np.float32)
            for w in _re.split(r"\W+", str(t).lower()):
                if w:
                    # crc32, NOT hash(). Python randomises string hashing per
                    # process (PYTHONHASHSEED), so the first version of this
                    # fake produced different vectors on every run and the
                    # ranking assertions passed or failed by luck — observed,
                    # after the same test had already passed twice.
                    v[zlib.crc32(w.encode()) % 1024] += 1.0
            n = np.linalg.norm(v)
            out.append(v / n if n else v)
        return np.array(out)


@pytest.fixture
def with_model(monkeypatch):
    m = _FakeModel()
    monkeypatch.setattr(longterm, "_get_embed_model", lambda: m)
    monkeypatch.setattr(
        longterm, "_embed",
        lambda text: m.encode([text])[0].astype(np.float32).tobytes())
    return m


class TestSearchNeverOpensANote:
    """The success check, as a test.

    If search ever reads a note file, this fails — which is the only way to
    stop "retrieval" quietly becoming a linear scan over the whole vault.
    """

    def test_a_query_reads_no_files_at_all(self, a_vault, with_model, monkeypatch):
        write(a_vault, "Projects", "Phone stand", "A desk stand for a phone.")
        write(a_vault, "Projects", "Tax return", "Numbers for the accountant.")
        vi.reindex()

        def _forbidden(*a, **k):
            raise AssertionError(
                "search() opened a note file — retrieval must come from the "
                "index, or it is a full-vault scan wearing a hat")
        monkeypatch.setattr(Path, "read_text", _forbidden)

        out = vi.search("something to hold my phone up")
        assert out["results"], "search returned nothing at all"
        assert out["results"][0]["title"] == "Phone stand"

    def test_indexing_is_allowed_to_read_them(self, a_vault, with_model):
        """The other half — the restriction is on querying, not on indexing.
        A test that forbade both would be asserting the feature cannot exist."""
        write(a_vault, "Notes", "Anything", "Some words.")
        r = vi.reindex()
        assert r["added"] == 1


class TestFindingTheRightPage:
    def test_it_finds_a_note_whose_title_you_do_not_know(self, a_vault, with_model):
        write(a_vault, "Projects", "Bracket v3", "mounting bracket aluminium 40mm")
        write(a_vault, "Projects", "Holiday", "flights to lisbon in june")
        vi.reindex()
        out = vi.search("aluminium mounting bracket")
        assert out["mode"] == "semantic"
        assert out["results"][0]["title"] == "Bracket v3"

    def test_the_title_counts_even_when_the_body_never_repeats_it(
            self, a_vault, with_model):
        """A note called "Phone stand" whose body never says the phrase is
        still about a phone stand, and the filename is the strongest signal a
        person gives about what a note is for."""
        write(a_vault, "Projects", "Phone stand", "Printed it overnight. Fits.")
        write(a_vault, "Projects", "Groceries", "milk eggs bread")
        vi.reindex()
        assert vi.search("phone stand")["results"][0]["title"] == "Phone stand"

    def test_a_folder_filter_narrows_it(self, a_vault, with_model):
        write(a_vault, "Projects", "Budget", "money spreadsheet")
        write(a_vault, "Notes", "Budget notes", "money spreadsheet")
        vi.reindex()
        out = vi.search("money spreadsheet", folder="Notes")
        assert [r["folder"] for r in out["results"]] == ["Notes"]

    def test_results_carry_an_excerpt_so_the_note_need_not_be_opened(
            self, a_vault, with_model):
        write(a_vault, "Notes", "Decision", "We chose SQLite over Postgres.")
        vi.reindex()
        r = vi.search("database choice")["results"][0]
        assert "SQLite" in r["excerpt"]

    def test_frontmatter_is_not_part_of_the_note(self, a_vault, with_model):
        """Left in, every note would begin with the same six lines and the
        embeddings would all be a little bit alike."""
        write(a_vault, "Notes", "Thing", "the actual body")
        vi.reindex()
        assert "created:" not in vi.search("thing")["results"][0]["excerpt"]

    def test_a_wikilink_keeps_its_target_words(self, a_vault, with_model):
        write(a_vault, "Notes", "Log", "Finished the [[Phone Stand|thing]] today.")
        vi.reindex()
        assert "Phone Stand" in vi.search("log")["results"][0]["excerpt"]


class TestTheIndexTracksTheVault:
    def test_an_edited_note_is_re_indexed(self, a_vault, with_model):
        p = write(a_vault, "Notes", "Spec", "first version")
        vi.reindex()
        p.write_text("---\ntitle: Spec\n---\n\nsecond version entirely\n")
        r = vi.reindex()
        assert r["updated"] == 1
        assert "second version" in vi.search("spec")["results"][0]["excerpt"]

    def test_a_same_length_edit_is_still_noticed(self, a_vault, with_model):
        """Freshness is decided by hashing the bytes, not by mtime and size.

        This session already lost an hour to Python's own mtime+size bytecode
        cache serving a stale .pyc after a same-length edit inside one second.
        A vault is a folder a human edits and a sync client rewrites; same-size
        edits and reset timestamps are ordinary there.
        """
        p = write(a_vault, "Notes", "Spec", "aaaa bbbb")
        vi.reindex()
        st = p.stat()
        p.write_text(p.read_text().replace("aaaa bbbb", "cccc dddd"))
        import os
        os.utime(p, (st.st_atime, st.st_mtime))     # same mtime, same size
        assert vi.reindex()["updated"] == 1, \
            "a same-size edit with an unchanged mtime went unnoticed"

    def test_an_unchanged_note_is_not_re_embedded(self, a_vault, with_model,
                                                  monkeypatch):
        """Embedding is the expensive part. Re-running it over an unchanged
        vault would make the model the cost of starting Apex."""
        write(a_vault, "Notes", "Steady", "unchanged text")
        vi.reindex()
        calls = []
        monkeypatch.setattr(vi, "embed_text",
                            lambda t, b: calls.append(t) or b"")
        r = vi.reindex()
        assert r["unchanged"] == 1 and calls == []

    def test_force_re_embeds_anyway(self, a_vault, with_model, monkeypatch):
        write(a_vault, "Notes", "Steady", "unchanged text")
        vi.reindex()
        calls = []
        real = vi.embed_text
        monkeypatch.setattr(vi, "embed_text",
                            lambda t, b: calls.append(t) or real(t, b))
        assert vi.reindex(force=True)["updated"] == 1 and calls

    def test_a_deleted_note_leaves_the_index(self, a_vault, with_model):
        """Otherwise search keeps offering a page that is not there — a
        retrieval that "works" and points at nothing."""
        p = write(a_vault, "Notes", "Gone", "temporary thoughts")
        vi.reindex()
        p.unlink()
        assert vi.reindex()["removed"] == 1
        assert vi.search("temporary thoughts")["results"] == []

    def test_a_note_apex_writes_is_searchable_immediately(self, a_vault, with_model):
        """Not at the next full pass — Apex writing a note and then being
        unable to find it would be a strange thing to explain."""
        vault.write_note("Meeting outcome", "We agreed to ship on Friday.",
                         folder="Notes", _bypass_approval=True)
        out = vi.search("when are we shipping")
        assert out["results"] and out["results"][0]["title"] == "Meeting outcome"

    def test_an_indexing_failure_never_loses_the_note(self, a_vault, monkeypatch):
        """A note that saved but did not index leaves a stale index, which
        status() reports and reindex() repairs. A note that failed to SAVE
        because indexing raised is lost writing."""
        def boom(*a, **k):
            raise RuntimeError("index is on fire")
        monkeypatch.setattr(vi, "index_note", boom)
        msg = vault.write_note("Important", "Do not lose this.",
                               folder="Notes", _bypass_approval=True)
        assert "Created note" in msg
        assert (a_vault / "Notes" / "Important.md").exists()


class TestItSaysWhyItFoundNothing:
    """Empty is the same shape as "nothing matched", and the causes need
    completely different fixes."""

    def test_an_unindexed_vault_says_so_rather_than_returning_nothing(self, a_vault):
        write(a_vault, "Notes", "Present", "this note exists on disk")
        out = vi.search("anything")
        assert out["mode"] == "unindexed"
        assert "never been indexed" in out["note"]

    def test_a_genuine_miss_is_not_reported_as_unindexed(self, a_vault, with_model):
        write(a_vault, "Notes", "Present", "boats and rivers")
        vi.reindex()
        out = vi.search("quantum chromodynamics")
        assert out["mode"] == "semantic", \
            "a real search that found little must not read as a broken index"

    def test_an_empty_query_is_refused_rather_than_matched(self, a_vault, with_model):
        write(a_vault, "Notes", "Present", "words")
        vi.reindex()
        assert vi.search("   ")["mode"] == "none"

    def test_a_stale_index_says_so_without_reading_the_notes(
            self, a_vault, with_model, monkeypatch):
        """The warning is built from a directory listing. If it read the notes
        to check, keeping the index honest would put the vault back on the
        retrieval path — the thing this module exists to avoid."""
        write(a_vault, "Notes", "One", "first")
        vi.reindex()
        write(a_vault, "Notes", "Two", "second, never indexed")

        monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("staleness check read a note")))
        out = vi.search("first")
        assert "may be stale" in out["note"]
        assert "2 notes on disk, 1 indexed" in out["note"]


class TestWithoutAnEmbeddingModel:
    """An index that silently held no vectors would make every search return
    nothing, which reads exactly like "you have no notes about that"."""

    @pytest.fixture
    def no_model(self, monkeypatch):
        monkeypatch.setattr(longterm, "_get_embed_model", lambda: None)
        monkeypatch.setattr(longterm, "_embed", lambda text: None)

    def test_it_falls_back_to_keywords_rather_than_going_silent(
            self, a_vault, no_model):
        write(a_vault, "Projects", "Phone stand", "a stand for a phone")
        write(a_vault, "Projects", "Taxes", "receipts")
        vi.reindex()
        out = vi.search("phone stand")
        assert out["mode"] == "keyword"
        assert out["results"][0]["title"] == "Phone stand"

    def test_every_result_says_it_was_a_keyword_match(self, a_vault, no_model):
        """A degraded mode you have to infer from the quality of the answers is
        one nobody ever notices."""
        write(a_vault, "Projects", "Phone stand", "a stand for a phone")
        vi.reindex()
        assert all(r["matched"] == "keyword"
                   for r in vi.search("phone")["results"])
        assert "not meaning" in vi.search("phone")["note"]

    def test_reindex_counts_the_notes_it_could_not_embed(self, a_vault, no_model):
        write(a_vault, "Notes", "A", "x")
        write(a_vault, "Notes", "B", "y")
        assert vi.reindex()["without_embedding"] == 2

    def test_a_partly_vectorised_index_admits_it(self, a_vault, monkeypatch):
        """Indexed before the model loaded, then searched after it arrived.

        Those older notes cannot be found by meaning at all, and an index that
        stays quiet about it looks exactly like a complete one — the answer
        would simply never include them, and nothing would say why.

        Written with explicit monkeypatching rather than by stacking the
        no_model and with_model fixtures: fixture order decided which one won,
        which made the test's meaning depend on the order pytest happened to
        apply them.
        """
        model = _FakeModel()
        monkeypatch.setattr(longterm, "_get_embed_model", lambda: model)

        monkeypatch.setattr(longterm, "_embed", lambda text: None)
        write(a_vault, "Notes", "Old", "indexed before the model was available")
        vi.reindex()

        monkeypatch.setattr(
            longterm, "_embed",
            lambda text: model.encode([text])[0].astype(np.float32).tobytes())
        write(a_vault, "Notes", "New", "indexed with a model present")
        vi.reindex()

        out = vi.search("indexed")
        assert out["mode"] == "semantic"
        assert [r["title"] for r in out["results"]] == ["New"], \
            "the un-vectorised note cannot be found by meaning"
        assert "1 note(s) have no embedding" in out["note"], \
            "and the answer has to say so, or it reads as a complete search"


class TestStatus:
    def test_it_reports_disk_and_index_separately(self, a_vault, with_model):
        """They disagree in two directions and the fixes differ: more files
        than rows means the index is stale, more rows than files means notes
        were deleted and nothing swept."""
        write(a_vault, "Notes", "A", "x")
        write(a_vault, "Notes", "B", "y")
        vi.reindex()
        write(a_vault, "Notes", "C", "z")
        s = vi.status()
        assert s["notes_on_disk"] == 3 and s["notes_indexed"] == 2
        assert s["state"] == "stale"

    def test_a_never_indexed_vault_is_not_reported_as_empty(self, a_vault):
        write(a_vault, "Notes", "A", "x")
        assert vi.status()["state"] == "never_indexed"

    def test_an_actually_empty_vault_is(self, a_vault):
        assert vi.status()["state"] == "empty"


class TestTheBackgroundReindex:
    def test_it_runs_and_indexes(self, a_vault, with_model):
        write(a_vault, "Notes", "A", "x")
        vi.start_background_reindex(log=lambda *a: None).join(20)
        assert vi.status()["notes_indexed"] == 1

    def test_a_broken_vault_does_not_raise_into_boot(self, a_vault, monkeypatch):
        """A vault that cannot be indexed is a degraded search. A vault that
        stops Apex booting is worse."""
        monkeypatch.setattr(vi, "reindex", lambda **k: (_ for _ in ()).throw(
            RuntimeError("disk gone")))
        said = []
        vi.start_background_reindex(log=said.append).join(20)
        assert any("Indexing failed" in s for s in said)
