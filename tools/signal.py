"""Signal channel via signal-cli REST API.

Uses signal-cli-rest-api (https://github.com/bbernhard/signal-cli-rest-api),
a self-hosted Docker bridge to the Signal protocol.

Quick start:
  docker run -d -p 8080:8080 -v /path/to/signal-data:/home/.local/share/signal-cli \
    -e MODE=json-rpc bbernhard/signal-cli-rest-api

Outbound: send_message(recipient, text) — POST to the REST API.
Inbound:  Configure signal-cli-rest-api to deliver webhooks to
          POST /signal/webhook, then dispatch_inbound() routes to the agent.

Config:
  SIGNAL_CLI_URL        — base URL of the REST API (e.g. http://localhost:8080)
  SIGNAL_PHONE_NUMBER   — your registered Signal number in E.164 (+15551234567)
  SIGNAL_ALLOWED_NUMBERS — comma-sep E.164 allowlist. Leave blank to allow any
                           (NOT recommended in production).

Setup:
  1. Run the Docker container above.
  2. Register / link your number via its web UI (:8080/v1/qrcodelink).
  3. Set the REST API's webhook URL to https://your-host/signal/webhook.
  4. Fill SIGNAL_CLI_URL, SIGNAL_PHONE_NUMBER, SIGNAL_ALLOWED_NUMBERS in .env.
"""
import json
import urllib.request
from typing import Callable, Optional

import config

_agent_run_fn: Optional[Callable] = None


def set_agent_run_fn(fn: Callable) -> None:
    global _agent_run_fn
    _agent_run_fn = fn


def _base_url() -> str:
    return (getattr(config, "SIGNAL_CLI_URL", "") or "").rstrip("/")


def _phone() -> str:
    return getattr(config, "SIGNAL_PHONE_NUMBER", "") or ""


def is_configured() -> bool:
    return bool(_base_url() and _phone())


def _allowed_numbers() -> set[str]:
    raw = getattr(config, "SIGNAL_ALLOWED_NUMBERS", []) or []
    return {str(n).strip() for n in raw if str(n).strip()}


def _is_allowed(number: str) -> bool:
    allowed = _allowed_numbers()
    return bool(allowed) and number.strip() in allowed  # DENY-BY-DEFAULT when unconfigured


def send_message(recipient: str, text: str) -> str:
    if not is_configured():
        return "[Signal] SIGNAL_CLI_URL or SIGNAL_PHONE_NUMBER not configured."
    url = f"{_base_url()}/v2/send"
    payload = json.dumps({
        "number": _phone(),
        "recipients": [recipient],
        "message": text,
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return f"Signal message sent to {recipient}"
    except Exception as e:
        return f"[Signal] send_message failed: {e}"


def dispatch_inbound(payload: dict) -> Optional[str]:
    """Handle one inbound message from the signal-cli-rest-api webhook."""
    envelope = payload.get("envelope") or {}
    data_message = envelope.get("dataMessage") or {}
    source = (
        envelope.get("source")
        or envelope.get("sourceNumber")
        or ""
    )
    text = (data_message.get("message") or "").strip()

    if not source or not text:
        return None

    if not _is_allowed(source):
        send_message(source, "Sorry, this number is not authorized.")
        return None

    if _agent_run_fn is None:
        send_message(source, "Agent not ready yet. Try again in a moment.")
        return None

    try:
        reply = _agent_run_fn(
            f"[Signal from {source}] {text}",
            channel_id=f"signal:{source}",
        )
    except Exception as e:
        reply = f"Agent error: {e}"

    send_message(source, reply)
    return reply
