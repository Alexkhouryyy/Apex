"""Unit tests for agent/resilience.py — pure functions, no API calls."""
import httpx
import anthropic
import pytest
from agent import resilience


def _rate_limit():
    req = httpx.Request("POST", "https://api.anthropic.com")
    return anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)


def _status(code: int, body=None):
    req = httpx.Request("POST", "https://api.anthropic.com")
    return anthropic.APIStatusError("err", response=httpx.Response(code, request=req), body=body)


def _conn():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))


def _timeout():
    return anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))


class TestClassify:
    def test_rate_limit(self):
        assert resilience.classify(_rate_limit()) == "rate_limit"

    def test_overloaded_529(self):
        assert resilience.classify(_status(529)) == "overloaded"

    def test_server_error_500(self):
        assert resilience.classify(_status(500)) == "server_error"

    def test_server_error_503(self):
        assert resilience.classify(_status(503)) == "server_error"

    def test_auth_401(self):
        assert resilience.classify(_status(401)) == "auth"

    def test_auth_403(self):
        assert resilience.classify(_status(403)) == "auth"

    def test_client_error_400(self):
        assert resilience.classify(_status(400)) == "client_error"

    def test_client_error_422(self):
        assert resilience.classify(_status(422)) == "client_error"

    @pytest.mark.parametrize("nested", [False, True])
    def test_insufficient_credit_400_is_billing(self, nested):
        error = {"type": "invalid_request_error", "message": (
            "Your credit balance is too low to access the Anthropic API. "
            "Please go to Plans & Billing to upgrade or purchase credits.")}
        body = {"type": "error", "error": error} if nested else error
        assert resilience.classify(_status(400, body)) == "billing"

    def test_payment_required_402_is_billing(self):
        assert resilience.classify(_status(402)) == "billing"

    def test_structured_billing_type_400_is_billing(self):
        assert resilience.classify(_status(400, {"error": {
            "type": "billing_error", "message": "Payment details need attention."}})) == "billing"

    @pytest.mark.parametrize("body", [None, "invalid", [], {"error": None},
                                     {"error": "invalid"}, {"message": None}])
    def test_unstructured_400_remains_client_error(self, body):
        assert resilience.classify(_status(400, body)) == "client_error"

    def test_quoted_billing_words_do_not_reclassify_validation_failure(self):
        body = {"error": {"type": "invalid_request_error", "message": (
            "Invalid messages.0: received 'Your credit balance is too low'.")}}
        assert resilience.classify(_status(400, body)) == "client_error"

    def test_server_error_is_not_reclassified_by_billing_text(self):
        assert resilience.classify(_status(503, {"error": {
            "message": "Your credit balance is too low"}})) == "server_error"

    def test_network_connection(self):
        assert resilience.classify(_conn()) == "network"

    def test_network_timeout(self):
        assert resilience.classify(_timeout()) == "network"

    def test_non_api_exception(self):
        assert resilience.classify(ValueError("boom")) == "unexpected"

    def test_runtime_error(self):
        assert resilience.classify(RuntimeError("oops")) == "unexpected"


class TestFriendlyMessage:
    def test_all_known_categories_have_nonempty_message(self):
        for cat, msg in resilience._MESSAGES.items():
            assert isinstance(msg, str) and msg.strip(), f"empty message for {cat!r}"

    def test_rate_limit_message_mentions_limit_or_rate(self):
        msg = resilience.friendly_message(_rate_limit())
        assert any(kw in msg.lower() for kw in ("rate", "limit"))

    def test_network_message_mentions_network_or_reach(self):
        msg = resilience.friendly_message(_conn())
        assert any(kw in msg.lower() for kw in ("network", "reach", "api"))

    def test_overloaded_message_mentions_overloaded_or_try(self):
        msg = resilience.friendly_message(_status(529))
        assert any(kw in msg.lower() for kw in ("overload", "try"))

    def test_unknown_exception_returns_fallback(self):
        msg = resilience.friendly_message(KeyboardInterrupt())
        assert isinstance(msg, str) and msg.strip()

    def test_credit_message_is_actionable_without_echoing_response_body(self):
        body = {"error": {"type": "invalid_request_error", "message": (
            "Your credit balance is too low. private-provider-detail")}}
        msg = resilience.friendly_message(_status(400, body))
        assert "credit" in msg and "billing" in msg
        assert "another configured model" in msg
        assert "malformed" not in msg and "private-provider-detail" not in msg
        assert len(msg) <= 300  # scheduler notifications truncate at 300


class TestShouldFallback:
    def test_rate_limit_triggers_fallback(self):
        assert resilience.should_fallback("rate_limit")

    def test_overloaded_triggers_fallback(self):
        assert resilience.should_fallback("overloaded")

    def test_server_error_triggers_fallback(self):
        assert resilience.should_fallback("server_error")

    def test_network_triggers_fallback(self):
        assert resilience.should_fallback("network")

    def test_auth_does_not_trigger_fallback(self):
        assert not resilience.should_fallback("auth")

    def test_client_error_does_not_trigger_fallback(self):
        assert not resilience.should_fallback("client_error")

    def test_billing_does_not_automatically_switch_paid_providers(self):
        assert not resilience.should_fallback("billing")


class TestFallbackCreate:
    def test_raises_when_no_key_configured(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            resilience.fallback_create([{"role": "user", "content": "hi"}])

    def test_converts_list_content_to_text(self, monkeypatch, test_db):
        """Verifies message conversion and call path without a real API call or real openai package."""
        import sys
        import config
        monkeypatch.setattr(config, "OPENROUTER_API_KEY", "fake-key")
        monkeypatch.setattr(config, "FALLBACK_MODEL", "test/model")
        monkeypatch.setitem(config.MODEL_PRICING, "test/model",
                            {"input": 1, "output": 1, "cache_read": 0, "cache_create": 0})

        captured = {}

        class FakeMessage:
            content = "hello back"

        class FakeChoice:
            message = FakeMessage()

        class FakeResp:
            choices = [FakeChoice()]

        class FakeCompletions:
            def create(self, **kwargs):
                captured["messages"] = kwargs["messages"]
                return FakeResp()

        class FakeChat:
            completions = FakeCompletions()

        class FakeOpenAI:
            def __init__(self, **_):
                self.chat = FakeChat()

        # Inject a fake openai module so resilience.py's `from openai import OpenAI` succeeds
        fake_openai = type(sys)("openai")
        fake_openai.OpenAI = FakeOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake_openai)

        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "hello"},
                {"type": "image", "source": {}},
            ]}
        ]
        result = resilience.fallback_create(messages, system="Be brief.")
        assert result == "hello back"
        user_msg = next(m for m in captured["messages"] if m["role"] == "user")
        assert "hello" in user_msg["content"]
        assert "image" not in user_msg["content"].lower()
