"""Chat history must survive closing the tab.

It lived in sessionStorage, so a reload erased it — while every turn was already
in SQLite. This is a separate store rather than a view over turn_log for a
concrete reason: turn_log.session_id is per PROCESS (main.py and resident.py
each call start_session() once at boot), so an always-on Apex would present one
endless conversation spanning weeks.
"""
import pytest

from agent import conversations, longterm


@pytest.fixture(autouse=True)
def _db(test_db, monkeypatch):
    monkeypatch.setattr(conversations, "_ready", False, raising=False)
    conversations.init_db()
    yield


def test_a_conversation_persists():
    tid = conversations.create()
    conversations.add_message(tid, "user", "check my disk space")
    conversations.add_message(tid, "agent", "42% used")
    msgs = conversations.messages(tid)
    assert [m["role"] for m in msgs] == ["user", "agent"]
    assert msgs[0]["text"] == "check my disk space"


def test_the_title_comes_from_what_you_said():
    """Derived, not generated: a model call per conversation is spend for
    something a truncation does as well."""
    tid = conversations.create()
    conversations.add_message(tid, "user", "what's my calendar look like tomorrow?")
    assert conversations.list_threads()[0]["title"] == "what's my calendar look like tomorrow?"


def test_a_long_first_message_is_truncated():
    tid = conversations.create()
    conversations.add_message(tid, "user", "x" * 500)
    title = conversations.list_threads()[0]["title"]
    assert len(title) <= conversations._TITLE_CHARS + 1 and title.endswith("…")


def test_the_title_is_not_rewritten_by_later_messages():
    tid = conversations.create()
    conversations.add_message(tid, "user", "first thing")
    conversations.add_message(tid, "agent", "reply")
    conversations.add_message(tid, "user", "second thing")
    assert conversations.list_threads()[0]["title"] == "first thing"


def test_empty_conversations_are_not_listed():
    """Clicking New should not litter the list with threads you never used."""
    conversations.create()
    assert conversations.list_threads() == []


def test_threads_are_ordered_by_recency():
    a = conversations.create(); conversations.add_message(a, "user", "older")
    b = conversations.create(); conversations.add_message(b, "user", "newer")
    assert [t["title"] for t in conversations.list_threads()] == ["newer", "older"]


def test_deleting_removes_the_messages_too():
    tid = conversations.create()
    conversations.add_message(tid, "user", "hello")
    assert conversations.delete(tid) is True
    assert conversations.messages(tid) == []
    with longterm._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0


def test_latest_id_reopens_where_you_left_off():
    a = conversations.create(); conversations.add_message(a, "user", "one")
    b = conversations.create(); conversations.add_message(b, "user", "two")
    assert conversations.latest_id() == b


def test_it_works_without_an_explicit_init_db(monkeypatch):
    """The restraint lesson: a module that needs a boot call it never gets
    silently does nothing forever."""
    monkeypatch.setattr(conversations, "_ready", False, raising=False)
    with longterm._conn() as c:
        c.execute("DROP TABLE IF EXISTS chat_messages")
        c.execute("DROP TABLE IF EXISTS chat_threads")
    monkeypatch.setattr(conversations, "_ready", False, raising=False)
    tid = conversations.create()
    conversations.add_message(tid, "user", "still works")
    assert conversations.messages(tid)[0]["text"] == "still works"


def test_everything_fails_open(monkeypatch):
    class _Broken:
        def __enter__(self): raise RuntimeError("db gone")
        def __exit__(self, *a): return False
    monkeypatch.setattr(longterm, "_conn", lambda: _Broken())
    assert conversations.list_threads() == []
    assert conversations.messages(1) == []
    assert conversations.delete(1) is False
    conversations.add_message(1, "user", "x")     # must not raise


def test_the_endpoints_exist():
    from dashboard import server
    paths = {r.path for r in server.app.routes if hasattr(r, "path")}
    assert "/api/chat/threads" in paths
    assert "/api/chat/threads/{thread_id}" in paths


def test_the_client_stores_the_thread_outside_the_session():
    """sessionStorage was the bug. localStorage is what makes it survive."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()
    fn = js[js.index("const _THREAD_KEY"):js.index("async function _loadThreadList")]
    assert "localStorage" in fn and "sessionStorage" not in fn


def test_the_chat_endpoint_records_both_sides():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "dashboard/server.py").read_text()
    chat = src[src.index("user_text = (body.get"):src.index("# --- Council")]
    assert 'add_message(thread_id, "user"' in chat
    assert 'add_message(thread_id, "agent"' in chat


# --- copy: the small affordance whose absence reads as unfinished ------------

def _js():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "dashboard/static/app.js").read_text()


def test_every_message_gets_a_copy_button():
    js = _js()
    fn = js[js.index("function _appendChatMsg"):]
    fn = fn[:fn.index("\nasync function sendChat")]
    assert "chat-copy-btn" in fn


def test_copying_uses_the_source_text_not_the_rendered_html():
    """Agent content is rendered markdown; copying what is on screen would
    paste mangled formatting instead of what was written."""
    js = _js()
    fn = js[js.index("function _appendChatMsg"):]
    fn = fn[:fn.index("\nasync function sendChat")]
    assert "div.dataset.raw = text" in fn
    # And the streamed answer's raw text is only final once streaming ends.
    fin = js[js.index("function _chatFinalize"):]
    fin = fin[:fin.index("\nfunction ")]
    assert "dataset.raw" in fin


def test_copy_falls_back_outside_a_secure_context():
    """navigator.clipboard does not exist over plain http on a LAN address —
    exactly where a self-hosted dashboard is most likely to be opened."""
    js = _js()
    handler = js[js.index("const btn = e.target.closest('.chat-copy-btn')"):]
    handler = handler[:handler.index("\n});")]
    assert "isSecureContext" in handler
    assert "execCommand('copy')" in handler


def test_copy_is_delegated_so_restored_messages_work():
    """Messages arrive three ways — streamed, restored from a thread, and
    re-rendered on clear. Per-button wiring would miss two of them."""
    js = _js()
    assert "getElementById('chat-messages')?.addEventListener('click'" in js
