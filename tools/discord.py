"""Discord bot channel — outbound messages + inbound slash-command interactions.

Outbound:
  - send_message(channel_id, text): POST a message to a channel via the Bot API.

Inbound:
  Discord delivers slash-command interactions to POST /discord/interactions
  (wired in dashboard/server.py). Each interaction is Ed25519-verified, then
  routed through dispatch_interaction(): a PING is answered with PONG, and a
  slash command is acknowledged immediately with a deferred response while the
  agent runs on a background thread. Once the agent finishes, the deferred
  reply is edited in place — this is required because Discord enforces a
  ~3-second response budget.

Setup:
  1. Create an application + bot at https://discord.com/developers/applications
  2. Bot token  -> DISCORD_BOT_TOKEN ; Public key -> DISCORD_PUBLIC_KEY
  3. Register a slash command (e.g. /ask) with a required string option.
  4. Set the Interactions Endpoint URL to https://your.host/discord/interactions
"""
import json
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

import config

_API = "https://discord.com/api/v10"

_agent_run_fn: Optional[Callable] = None


def set_agent_run_fn(fn: Callable) -> None:
    """Wire main.py's agent.run so inbound interactions have somewhere to go."""
    global _agent_run_fn
    _agent_run_fn = fn


def _token() -> str:
    return getattr(config, "DISCORD_BOT_TOKEN", "") or ""


def is_configured() -> bool:
    return bool(_token())


def _allowed_user_ids() -> set[str]:
    return {str(x) for x in (getattr(config, "DISCORD_ALLOWED_USER_IDS", []) or [])}


def _is_allowed(user_id: str) -> bool:
    allowed = _allowed_user_ids()
    return bool(allowed) and str(user_id) in allowed  # DENY-BY-DEFAULT when unconfigured


def send_message(channel_id: str, text: str) -> str:
    """Post a message to a Discord channel via the Bot API."""
    if not is_configured():
        return "[Discord] DISCORD_BOT_TOKEN not configured."
    payload = json.dumps({"content": text[:2000]}).encode()
    try:
        req = urllib.request.Request(
            f"{_API}/channels/{channel_id}/messages",
            data=payload,
            headers={
                "Authorization": f"Bot {_token()}",
                "Content-Type": "application/json",
                "User-Agent": "ApexAgent (https://localhost, 1.0)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read())
        return f"Discord message sent to channel {channel_id}"
    except urllib.error.HTTPError as e:
        return f"[Discord] API error {e.code}: {e.read().decode(errors='replace')[:200]}"
    except Exception as e:
        return f"[Discord] send_message failed: {e}"


def verify_signature(signature: str, timestamp: str, body: bytes) -> bool:
    """Verify a Discord interaction's Ed25519 signature against the app public key."""
    pub = getattr(config, "DISCORD_PUBLIC_KEY", "") or ""
    if not (pub and signature and timestamp):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub))
        key.verify(bytes.fromhex(signature), timestamp.encode() + body)
        return True
    except Exception:
        return False


def _edit_original(application_id: str, interaction_token: str, content: str) -> None:
    """Edit the deferred interaction response with the agent's final reply."""
    url = f"{_API}/webhooks/{application_id}/{interaction_token}/messages/@original"
    payload = json.dumps({"content": content[:2000]}).encode()
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[Discord] failed to edit interaction response: {e}")


def dispatch_interaction(interaction: dict) -> dict:
    """Handle one verified Discord interaction. Returns the immediate JSON response.

    PING (type 1) -> PONG. Slash command (type 2) -> deferred ack (type 5) plus a
    background agent run that edits the reply once finished.
    """
    itype = interaction.get("type")

    if itype == 1:  # PING
        return {"type": 1}

    if itype != 2:  # only APPLICATION_COMMAND is supported
        return {"type": 4, "data": {"content": "Unsupported interaction."}}

    application_id = interaction.get("application_id", "")
    token = interaction.get("token", "")
    user = (interaction.get("member") or {}).get("user") or interaction.get("user") or {}
    user_id = str(user.get("id", ""))
    username = user.get("username", "unknown")

    # Extract the first string option from the slash command (type 3 = STRING).
    options = (interaction.get("data") or {}).get("options") or []
    text = ""
    for opt in options:
        if opt.get("value"):
            text = str(opt["value"])
            break

    if not _is_allowed(user_id):
        return {"type": 4, "data": {"content": "Sorry, you are not authorized to use this bot."}}
    if not text:
        return {"type": 4, "data": {"content": "No message provided."}}
    if _agent_run_fn is None:
        return {"type": 4, "data": {"content": "Agent not ready yet. Try again in a moment."}}

    def _worker():
        try:
            reply = _agent_run_fn(
                f"[Discord from @{username}] {text}",
                channel_id=f"discord:{user_id}",
            )
        except Exception as e:
            reply = f"Agent error: {e}"
        _edit_original(application_id, token, reply or "(no response)")

    threading.Thread(target=_worker, daemon=True, name="DiscordTurn").start()

    # type 5 = DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    return {"type": 5}
