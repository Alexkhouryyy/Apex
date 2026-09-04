"""What the cloud is allowed to read, checked against a real database.

Step 7 of docs/PHASE_6_7_PLAN.md. The plan's success check is specific: "the
pushed context contains no credentials, vault or full history — asserted
against a REAL database, not a fixture."

So `TestNothingElseGetsIn` plants a distinct marker in each place the context is
not allowed to read — the vault, documents, the audit tables, .env, deep memory
history — writes them through the real modules into a real SQLite file, builds
the context, and asserts none of the markers appear. A fixture that returned
canned data would prove the assembler, not the boundary.
"""
import json
import re

import pytest

import config
from agent import longterm, working_context as wc


@pytest.fixture
def brain(tmp_path, monkeypatch):
    """A real database, populated through the real modules."""
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "brain.db"))
    from agent import schema
    schema.init_all(log=lambda *a: None)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestNothingElseGetsIn:
    """One distinct marker per forbidden source. If any appears in the output,
    the boundary leaked and the marker says which one."""

    def test_the_forbidden_sources_stay_out(self, brain, monkeypatch):
        # In-scope content, so the test is not passing on an empty context.
        longterm.remember("Alex prefers dark roast coffee", kind="preference",
                          importance=9)

        # 1. Deep memory history — only the top N are in scope.
        for i in range(60):
            longterm.remember(f"OLDMARKER{i} trivial note number {i}",
                              kind="note", importance=1)

        # 2. The Obsidian vault.
        from agent import vault
        monkeypatch.setattr(vault, "VAULT", brain / "vault", raising=False)
        try:
            vault.write_note("Secrets", "VAULTMARKER lives in the vault")
        except Exception:
            pass

        # 3. Documents.
        try:
            from agent import documents
            with longterm._conn() as c:
                c.execute("INSERT INTO documents (title, content, ts) "
                          "VALUES ('x', 'DOCMARKER inside a document', 0)")
                c.commit()
        except Exception:
            pass

        # 4. The MCP audit trail.
        from agent import mcp_policy
        mcp_policy.record(mcp_policy.decide("mcp__gmail__send_message"),
                          {"to": "AUDITMARKER@example.com"}, decision="denied")

        # 5. .env
        (brain / ".env").write_text("ENVMARKER_KEY=ENVMARKER_VALUE\n")

        out = wc.build()["text"]
        assert "dark roast" in out, \
            "the context is empty, so this test would pass for the wrong reason"

        # Never fetched at all. These are absolute.
        for marker in ("VAULTMARKER", "DOCMARKER", "AUDITMARKER", "ENVMARKER_VALUE"):
            assert marker not in out, (
                f"{marker} reached the cloud context. It is read from an "
                f"allowlist of sources; something started fetching more.")

        # Memory is a different claim and needs a different test. The context
        # carries a bounded SLICE — `top_memories(15)` — not the history, and
        # which fifteen come back is relevance ordering, not recency. So
        # asserting one particular note is absent proves nothing: the first
        # version of this test picked OLDMARKER59, and OLDMARKER56 came back
        # instead. What matters is that sixty memories do not become sixty
        # memories on a rented box.
        seen = len(re.findall(r"OLDMARKER\d+", out))
        assert seen < 20, (
            f"{seen} of 60 history notes reached the context. It is meant to be "
            f"a page of what Apex already considers relevant, not the archive.")

    def test_the_allowlist_is_the_mechanism(self):
        """Not "gather everything, then strip". That shape includes a new table
        by default and the omission from the strip list is invisible."""
        import inspect
        src = inspect.getsource(wc.build)
        assert "SOURCES" in src
        for forbidden in ("vault", "documents", "mcp_audit", "environ"):
            assert forbidden not in src

    def test_every_source_is_named(self):
        assert set(wc.SOURCES) == {"memories", "goals", "schedule", "conversation"}


class TestRedaction:
    @pytest.mark.parametrize("text,gone", [
        ("my token is xoxb-1234567890abcdef1234", "xoxb-1234567890abcdef1234"),
        ("password is hunter2", "hunter2"),
        ("api key: sk-abcdefghijklmnopqrstuv", "sk-abcdefghijklmnopqrstuv"),
        ("Authorization: Bearer abcdefghijklmnopqrs", "abcdefghijklmnopqrs"),
        ("postgres://user:supersecret@db.example.com/x", "supersecret"),
        ("ghp_abcdefghijklmnopqrstuvwxyz123456", "ghp_abcdefghijklmnop"),
    ])
    def test_recognisable_credentials_are_removed(self, text, gone):
        assert gone not in wc.redact(text)

    def test_the_output_is_not_mangled(self):
        """With the shape patterns first, "my token is xoxb-..." came out as
        "my token is [redacted] key]". The secret was gone both ways, and
        mangled output is how a redactor gets distrusted and switched off."""
        out = wc.redact("my token is xoxb-1234567890abcdef1234 ok")
        assert out == "my token is [redacted] ok"

    def test_ordinary_text_is_untouched(self):
        """A redactor that eats normal prose gets turned off, and then nothing
        is redacted."""
        text = "Alex prefers dark roast coffee and runs on Tuesdays."
        assert wc.redact(text) == text

    def test_a_private_key_block_goes_entirely(self):
        blob = ("-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n"
                "-----END RSA PRIVATE KEY-----")
        assert "AAAA" not in wc.redact(f"here it is:\n{blob}\nthanks")

    def test_it_is_applied_to_every_source_not_each_one_separately(self, brain):
        """Redaction on the way out means a source added later is covered
        without anyone remembering to cover it."""
        longterm.remember("the wifi password is correcthorsebattery",
                          kind="fact", importance=9)
        assert "correcthorsebattery" not in wc.build()["text"]

    def test_redaction_is_a_mitigation_not_a_guarantee(self, brain):
        """Stated in a test because it is the honest limit of this design, and
        a limit nobody wrote down is a limit somebody will assume away.

        A secret phrased so no pattern matches goes up with the rest. The only
        complete answer is not sending memory, which is the same as the cloud
        not answering."""
        longterm.remember("the spare key is under the third flowerpot",
                          kind="fact", importance=9)
        assert "third flowerpot" in wc.build()["text"]


class TestBounds:
    def test_a_huge_context_is_trimmed(self, brain):
        for i in range(40):
            longterm.remember("x" * 900 + f" n{i}", kind="note", importance=9)
        out = wc.build(max_chars=2000)
        assert out["chars"] <= 2000 + 20 and out["truncated"] is True

    def test_a_small_one_is_not_marked_truncated(self, brain):
        longterm.remember("short", kind="fact", importance=9)
        assert wc.build()["truncated"] is False


class TestBrokenSourcesAreNamed:
    def test_a_source_that_raises_is_reported_not_dropped(self, brain, monkeypatch):
        """Silently dropping it makes a broken source and an empty one
        identical, and the cloud answers confidently from a context missing the
        half it needed."""
        monkeypatch.setitem(wc.SOURCES, "goals",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        out = wc.build()
        assert "goals" in out["errors"] and "boom" in out["errors"]["goals"]

    def test_one_broken_source_does_not_lose_the_others(self, brain, monkeypatch):
        longterm.remember("Alex prefers dark roast", kind="preference",
                          importance=9)
        monkeypatch.setitem(wc.SOURCES, "goals",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert "dark roast" in wc.build()["text"]


class TestTheCloudTier:
    def test_it_may_answer_and_may_not_act(self):
        t = wc.cloud_tier()
        assert "answer" in t["may"]
        for forbidden in ("shell", "files", "accounts", "mcp"):
            assert forbidden in t["may_not"]

    def test_the_two_lists_do_not_overlap(self):
        assert not (set(wc.CLOUD_MAY) & set(wc.CLOUD_MAY_NOT))

    def test_it_says_where_refused_work_goes(self):
        """The cloud can want something to happen; it cannot be the thing that
        approves it."""
        note = wc.cloud_tier()["note"]
        assert "queued for the laptop" in note and "never an approval" in note
