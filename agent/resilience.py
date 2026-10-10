"""Classification, graceful handling, and fallback for Anthropic API failures.

The Anthropic SDK already retries transient errors (429, 500-range, 529, and
network failures) with exponential backoff and honors `retry-after`;
`config.API_MAX_RETRIES` tunes how many attempts it makes. This module covers
what the SDK does not:
  1. Turning an exhausted-retry error into a clear category + user-facing message.
  2. Optionally routing the request to a fallback provider (OpenRouter) when the
     primary provider is rate-limited or overloaded. Set OPENROUTER_API_KEY in
     .env to enable; set FALLBACK_MODEL to choose the model (default:
     anthropic/claude-3-5-sonnet). The fallback omits tools and thinking — it
     returns a plain text completion as a best-effort degraded response.
"""
import anthropic
import config


def _billing_failure(exc: anthropic.APIStatusError) -> bool:
    """Recognize payment errors, including Anthropic's legacy credit 400."""
    status = getattr(exc, "status_code", 0)
    if status == 402:
        return True
    if status != 400:
        return False
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    if not isinstance(error, dict):
        return False
    if error.get("type") == "billing_error":
        return True
    # invalid_request_error alone is not enough: genuine request validation
    # failures use it too. Read only the provider's structured message.
    message = error.get("message", "")
    return isinstance(message, str) and message.strip().lower().startswith(
        ("your credit balance is too low", "credit balance is too low"))


def classify(exc: Exception) -> str:
    """Map an exception to a short category string for logs and messaging."""
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit"
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return "network"
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        if _billing_failure(exc):
            return "billing"
        if status == 529:
            return "overloaded"
        if status >= 500:
            return "server_error"
        if status in (401, 403):
            return "auth"
        return "client_error"
    if isinstance(exc, anthropic.APIError):
        return "api_error"
    return "unexpected"


def should_fallback(category: str) -> bool:
    """True for transient/capacity errors where a different provider might succeed."""
    from agent import apocalypse
    if apocalypse.enabled():
        return False
    return category in {"rate_limit", "overloaded", "server_error", "network", "api_error"}


def fallback_create(messages: list[dict], system: str = "", max_tokens: int = 4000) -> str:
    """Try OpenRouter as a fallback provider.

    Converts Anthropic-format messages to OpenAI-format (strips images and
    tool blocks, keeps text). Returns the completion text or raises RuntimeError.
    """
    from agent import apocalypse
    if apocalypse.enabled():
        raise apocalypse.OfflineUnavailable('Cloud fallback is paused in Apocalypse mode.')
    key = getattr(config, "OPENROUTER_API_KEY", "") or ""
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not configured.")
    model = getattr(config, "FALLBACK_MODEL", "") or "anthropic/claude-3-5-sonnet"

    oai_messages: list[dict] = []
    if system:
        oai_messages.append({"role": "system", "content": system})

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = " ".join(parts).strip() or "[non-text content]"
        if isinstance(content, str) and content:
            oai_messages.append({"role": role, "content": content})

    try:
        from agent import provider, telemetry
        client = provider.OpenAIAdapter(api_key=key, base_url="https://openrouter.ai/api/v1")
        resp = telemetry.create(client, call_site="resilience/fallback",
            model=model,
            messages=oai_messages,
            max_tokens=max_tokens,
        )
        return " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as e:
        raise RuntimeError(f"Fallback provider also failed: {e}") from e


_MESSAGES = {
    "rate_limit": "I'm being rate-limited by the API right now. Give me a moment and try again.",
    "network": "I couldn't reach the API — looks like a network issue. Try again in a moment.",
    "overloaded": "The API is overloaded right now. Try again in a minute.",
    "server_error": "The API had a server error. Try again shortly.",
    "auth": "My API key was rejected — this needs a config fix before I can respond.",
    "billing": "The model API is blocked by billing or insufficient credit. Check your API account's billing, or choose another configured model in Apex.",
    "client_error": "The API rejected that request as malformed. Let me know how to proceed.",
    "api_error": "I hit an API error and couldn't finish that turn. Try again.",
    "unexpected": "Something went wrong on my end and I couldn't finish that turn.",
}


def friendly_message(exc: Exception) -> str:
    """A short, user-facing sentence describing a turn that failed after retries."""
    return _MESSAGES.get(classify(exc), _MESSAGES["unexpected"])
