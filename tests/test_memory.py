"""Unit tests for agent/memory.py — in-memory, no API required."""
import json
import pytest
from agent.memory import Memory, _SUMMARY_THRESHOLD, _KEEP_MESSAGES, _parse_compression_response


class TestAddAndRetrieve:
    def test_add_user_str_normalises_to_list(self):
        m = Memory()
        m.add_user("hello")
        msgs = m.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == [{"type": "text", "text": "hello"}]

    def test_add_user_list_stored_verbatim(self):
        m = Memory()
        block = {"type": "text", "text": "hi"}
        m.add_user([block])
        assert m.get_messages()[0]["content"] == [block]

    def test_add_assistant(self):
        m = Memory()
        m.add_assistant([{"type": "text", "text": "hey"}])
        msgs = m.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"

    def test_alternating_roles_preserved(self):
        m = Memory()
        m.add_user("q1")
        m.add_assistant([{"type": "text", "text": "a1"}])
        m.add_user("q2")
        roles = [msg["role"] for msg in m.get_messages()]
        assert roles == ["user", "assistant", "user"]

    def test_get_messages_returns_same_list(self):
        m = Memory()
        m.add_user("x")
        assert m.get_messages() is m.messages


class TestContextPrefix:
    def test_empty_when_no_summary(self):
        m = Memory()
        assert m.context_prefix() == ""

    def test_contains_summary(self):
        m = Memory()
        m.summary = "discussed project goals"
        prefix = m.context_prefix()
        assert "discussed project goals" in prefix

    def test_prefix_is_string(self):
        m = Memory()
        m.summary = "some summary"
        assert isinstance(m.context_prefix(), str)


class TestMaybeSummarize:
    def test_no_op_under_threshold(self):
        m = Memory()
        for i in range(_SUMMARY_THRESHOLD - 1):
            m.add_user(f"msg {i}")
        original_count = len(m.get_messages())
        m.maybe_summarize(None)  # client never called — threshold not reached
        assert len(m.get_messages()) == original_count
        assert m.summary == ""

    def test_no_op_exactly_at_threshold_minus_one(self):
        m = Memory()
        for _ in range(_SUMMARY_THRESHOLD - 1):
            m.add_user("x")
        m.maybe_summarize(None)
        assert len(m.get_messages()) == _SUMMARY_THRESHOLD - 1

    def test_keeps_correct_window_after_compression(self, monkeypatch):
        """After a successful compression, exactly _KEEP_MESSAGES messages remain."""
        from agent import telemetry
        payload = json.dumps({"summary": "All good.", "save_to_memory": []})

        class FakeResp:
            content = [type("B", (), {"text": payload})()]

        monkeypatch.setattr(telemetry, "create", lambda *a, **k: FakeResp())

        m = Memory()
        for i in range(_SUMMARY_THRESHOLD):
            m.add_user(f"msg {i}")
        m.maybe_summarize(object())

        assert len(m.get_messages()) == _KEEP_MESSAGES

    def test_summary_updated_after_compression(self, monkeypatch):
        from agent import telemetry
        payload = json.dumps({"summary": "Compressed context.", "save_to_memory": []})

        class FakeResp:
            content = [type("B", (), {"text": payload})()]

        monkeypatch.setattr(telemetry, "create", lambda *a, **k: FakeResp())

        m = Memory()
        for _ in range(_SUMMARY_THRESHOLD):
            m.add_user("msg")
        m.maybe_summarize(object())

        assert m.summary == "Compressed context."

    def test_facts_saved_to_longterm(self, monkeypatch, test_db):
        """Facts in save_to_memory are persisted to longterm.memories."""
        from agent import telemetry, longterm
        payload = json.dumps({
            "summary": "Working session.",
            "save_to_memory": ["Prefers Python", "Project is called Apex"],
        })

        class FakeResp:
            content = [type("B", (), {"text": payload})()]

        monkeypatch.setattr(telemetry, "create", lambda *a, **k: FakeResp())

        m = Memory()
        for _ in range(_SUMMARY_THRESHOLD):
            m.add_user("msg")
        m.maybe_summarize(object())

        # Both facts should now be in longterm
        recalled = longterm.recall("", semantic=False)
        contents = [r["content"] for r in recalled]
        assert any("Python" in c for c in contents)
        assert any("Apex" in c for c in contents)

    def test_existing_summary_not_lost_on_recompression(self, monkeypatch):
        """The old summary is included in the compression prompt so it survives."""
        from agent import telemetry
        captured_prompt = {}

        def fake_create(client, *, call_site, **kwargs):
            captured_prompt["content"] = kwargs["messages"][0]["content"]
            payload = json.dumps({"summary": "Extended.", "save_to_memory": []})

            class FakeResp:
                content = [type("B", (), {"text": payload})()]

            return FakeResp()

        monkeypatch.setattr(telemetry, "create", fake_create)

        m = Memory()
        m.summary = "Previous session context."
        for _ in range(_SUMMARY_THRESHOLD):
            m.add_user("msg")
        m.maybe_summarize(object())

        assert "Previous session context." in captured_prompt["content"]

    def test_skips_gracefully_on_api_error(self, monkeypatch):
        """When the summarization API call fails, history is preserved intact."""
        import anthropic, httpx
        from agent import telemetry

        def boom(*a, **k):
            raise anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com")
            )

        monkeypatch.setattr(telemetry, "create", boom)

        m = Memory()
        for i in range(_SUMMARY_THRESHOLD):
            m.add_user(f"msg {i}")
        original_count = len(m.get_messages())

        m.maybe_summarize(object())

        assert len(m.get_messages()) == original_count
        assert m.summary == ""


class TestParseCompressionResponse:
    def test_valid_json_extracts_summary_and_facts(self):
        text = json.dumps({"summary": "The session.", "save_to_memory": ["fact A", "fact B"]})
        summary, facts = _parse_compression_response(text)
        assert summary == "The session."
        assert facts == ["fact A", "fact B"]

    def test_json_wrapped_in_prose_still_parsed(self):
        text = 'Sure! Here you go: {"summary": "Done.", "save_to_memory": []} Hope that helps.'
        summary, facts = _parse_compression_response(text)
        assert summary == "Done."
        assert facts == []

    def test_empty_save_to_memory_list(self):
        text = json.dumps({"summary": "No facts.", "save_to_memory": []})
        summary, facts = _parse_compression_response(text)
        assert summary == "No facts."
        assert facts == []

    def test_non_string_facts_skipped(self):
        text = json.dumps({"summary": "S.", "save_to_memory": [42, None, "valid", True]})
        _, facts = _parse_compression_response(text)
        # Only the string "valid" survives
        assert "valid" in facts
        assert len(facts) == 1

    def test_malformed_json_falls_back_to_raw_text(self):
        text = "This is just a plain sentence, not JSON."
        summary, facts = _parse_compression_response(text)
        assert summary == text
        assert facts == []

    def test_invalid_json_falls_back(self):
        text = '{"summary": "broken", "save_to_memory": [unclosed'
        summary, facts = _parse_compression_response(text)
        assert summary == text  # raw fallback
        assert facts == []

    def test_empty_summary_in_json_falls_back(self):
        text = json.dumps({"summary": "", "save_to_memory": []})
        summary, facts = _parse_compression_response(text)
        # Empty summary → fallback to raw text
        assert summary == text


def test_saved_replies_go_back_to_the_api_as_the_sdk_would_send_them():
    """History must not gain fields the API never sent (a full model_dump adds
    "caller": null to tool_use and "citations": null to text)."""
    from anthropic.types import Message
    from agent.memory import Memory as ConversationMemory
    reply = Message.model_validate({
        'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'm', 'stop_reason': 'tool_use',
        'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1},
        'content': [{'type': 'text', 'text': 'Checking.'},
                    {'type': 'tool_use', 'id': 'toolu_1', 'name': 'bash', 'input': {'cmd': 'ls'}}]})
    mem = ConversationMemory()
    mem.add_assistant(reply.content)
    assert mem.get_messages()[-1]['content'] == [
        {'type': 'text', 'text': 'Checking.'},
        {'type': 'tool_use', 'id': 'toolu_1', 'name': 'bash', 'input': {'cmd': 'ls'}}]
