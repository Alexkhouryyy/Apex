"""Telegram bot channel — inbound/outbound messages.

Outbound:
  - send_message(chat_id, text): POST to Telegram Bot API

Inbound:
  Telegram delivers updates to POST /telegram/webhook (wired in dashboard/server.py).
  Each update is routed through dispatch_inbound(), which runs the agent
  and sends the reply back to Telegram.

Setup:
  1. Create a bot via @BotFather, get the token.
  2. Set TELEGRAM_BOT_TOKEN in .env (and optionally TELEGRAM_ALLOWED_CHAT_IDS).
  3. Register the webhook once:
       curl "https://api.telegram.org/bot{TOKEN}/setWebhook?url=https://your.host/telegram/webhook"
"""
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

import config

_agent_run_fn: Optional[Callable] = None

# Long-polling state
_poll_thread: Optional[threading.Thread] = None
_poll_stop: Optional[threading.Event] = None


def set_agent_run_fn(fn: Callable) -> None:
    global _agent_run_fn
    _agent_run_fn = fn


def _token() -> str:
    return getattr(config, "TELEGRAM_BOT_TOKEN", "") or ""


def is_configured() -> bool:
    return bool(_token())


def _allowed_ids() -> set[int]:
    raw = getattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", []) or []
    try:
        return {int(x) for x in raw if str(x).strip().lstrip("-").isdigit()}
    except Exception:
        return set()


def _is_allowed(chat_id: int) -> bool:
    allowed = _allowed_ids()
    return bool(allowed) and chat_id in allowed  # DENY-BY-DEFAULT when unconfigured


def send_message(chat_id: int | str, text: str) -> str:
    if not is_configured():
        return "[Telegram] TELEGRAM_BOT_TOKEN not configured."
    import urllib.request
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "Markdown",
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_token()}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return f"Telegram message sent to chat_id={chat_id}"
            return f"[Telegram] API error: {result}"
    except Exception as e:
        return f"[Telegram] send_message failed: {e}"


def start_polling() -> str:
    """Start long-polling getUpdates in a background thread.

    Use this when there is no public HTTPS URL for a webhook (e.g. a laptop or
    home machine behind NAT). Telegram forbids getUpdates while a webhook is
    set, so we delete any existing webhook first. Idempotent: a second call
    while already polling is a no-op.
    """
    global _poll_thread, _poll_stop
    if not is_configured():
        return "[Telegram] TELEGRAM_BOT_TOKEN not configured."
    if _poll_thread is not None and _poll_thread.is_alive():
        return "[Telegram] already polling."
    _poll_stop = threading.Event()
    _poll_thread = threading.Thread(
        target=_poll_loop, args=(_poll_stop,), daemon=True, name="TelegramPoll"
    )
    _poll_thread.start()
    return "[Telegram] long-polling started."


def stop_polling() -> None:
    if _poll_stop is not None:
        _poll_stop.set()


def _poll_loop(stop: threading.Event) -> None:
    # getUpdates only works when no webhook is registered.
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{_token()}/deleteWebhook", timeout=10
        )
    except Exception:
        pass

    offset: Optional[int] = None
    print("[Telegram] Polling for messages...")
    while not stop.is_set():
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            url = (
                f"https://api.telegram.org/bot{_token()}/getUpdates?"
                + urllib.parse.urlencode(params)
            )
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = json.loads(resp.read())
            if not data.get("ok"):
                time.sleep(3)
                continue
            for update in data.get("result", []):
                offset = update.get("update_id", 0) + 1
                try:
                    dispatch_inbound(update)
                except Exception as e:
                    print(f"[Telegram] dispatch error: {e}")
        except Exception:
            # Network hiccup / timeout — back off briefly and retry.
            if not stop.is_set():
                time.sleep(3)


def dispatch_inbound(update: dict) -> Optional[str]:
    """Process one Telegram update dict. Sends reply via API. Returns reply text or None."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None

    chat_id: int = msg.get("chat", {}).get("id")
    text: str = (msg.get("text") or "").strip()
    username: str = msg.get("from", {}).get("username", "unknown")

    if not chat_id or not text:
        return None

    if not _is_allowed(chat_id):
        send_message(chat_id, "Sorry, this chat is not authorized.")
        return None

    if _agent_run_fn is None:
        send_message(chat_id, "Agent not ready yet. Try again in a moment.")
        return None

    try:
        reply = _agent_run_fn(
            f"[Telegram from @{username}] {text}",
            channel_id=f"telegram:{chat_id}",
        )
    except Exception as e:
        reply = f"Agent error: {e}"

    if len(reply) > 4096:
        reply = reply[:4090] + "…"

    send_message(chat_id, reply)
    return reply
