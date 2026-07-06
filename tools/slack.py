"""Slack bot channel — inbound Events API + outbound messages.

Outbound:
  - send_message(channel, text): POST to Slack Web API (chat.postMessage)

Inbound:
  Slack sends event payloads to POST /slack/events (wired in dashboard/server.py).
  Each payload is HMAC-SHA256 verified, URL verification challenges are answered
  immediately, and message events are dispatched through dispatch_event().

Setup:
  1. Create a Slack app at https://api.slack.com/apps
  2. Bot token (xoxb-…)  -> SLACK_BOT_TOKEN
     Signing secret      -> SLACK_SIGNING_SECRET
  3. Enable Event Subscriptions → set Request URL to https://your-host/slack/events
  4. Subscribe to bot events: app_mention and/or message.channels
  5. Optionally set SLACK_ALLOWED_CHANNEL_IDS (comma-sep) to limit which channels
     trigger the agent; leave blank to allow all.
"""
import hashlib
import hmac
import json
import time
import urllib.request
from typing import Callable, Optional

import config

_API = "https://slack.com/api"
_agent_run_fn: Optional[Callable] = None


def set_agent_run_fn(fn: Callable) -> None:
    global _agent_run_fn
    _agent_run_fn = fn


def _token() -> str:
    return getattr(config, "SLACK_BOT_TOKEN", "") or ""


def _signing_secret() -> str:
    return getattr(config, "SLACK_SIGNING_SECRET", "") or ""


def is_configured() -> bool:
    return bool(_token())


def _allowed_channel_ids() -> set[str]:
    raw = getattr(config, "SLACK_ALLOWED_CHANNEL_IDS", []) or []
    return {str(x).strip() for x in raw if str(x).strip()}


def _is_allowed(channel_id: str) -> bool:
    allowed = _allowed_channel_ids()
    return bool(allowed) and channel_id in allowed  # DENY-BY-DEFAULT when unconfigured


def verify_signature(signature: str, timestamp: str, body: bytes) -> bool:
    """Verify Slack's HMAC-SHA256 request signature."""
    secret = _signing_secret()
    if not (secret and signature and timestamp):
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def send_message(channel: str, text: str) -> str:
    if not is_configured():
        return "[Slack] SLACK_BOT_TOKEN not configured."
    payload = json.dumps({"channel": channel, "text": text[:40000]}).encode()
    try:
        req = urllib.request.Request(
            f"{_API}/chat.postMessage",
            data=payload,
            headers={
                "Authorization": f"Bearer {_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return f"Slack message sent to {channel}"
            return f"[Slack] API error: {result.get('error', result)}"
    except Exception as e:
        return f"[Slack] send_message failed: {e}"


def dispatch_event(payload: dict) -> dict | None:
    """Handle one Slack Events API payload. Returns a response dict or None."""
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event") or {}
    etype = event.get("type", "")

    if etype not in ("message", "app_mention"):
        return None
    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return None

    channel_id = event.get("channel", "")
    user_id = event.get("user", "unknown")
    text = (event.get("text") or "").strip()

    if not text or not channel_id:
        return None

    if not _is_allowed(channel_id):
        send_message(channel_id, "Sorry, this channel is not authorized.")
        return None

    if _agent_run_fn is None:
        send_message(channel_id, "Agent not ready yet.")
        return None

    try:
        reply = _agent_run_fn(
            f"[Slack from {user_id}] {text}",
            channel_id=f"slack:{channel_id}",
        )
    except Exception as e:
        reply = f"Agent error: {e}"

    if len(reply) > 40000:
        reply = reply[:39990] + "…"

    send_message(channel_id, reply)
    return None
