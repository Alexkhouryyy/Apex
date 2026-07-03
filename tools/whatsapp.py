"""WhatsApp channel via Twilio's WhatsApp API.

Uses the same Twilio credentials (TWILIO_SID, TWILIO_AUTH_TOKEN) already
configured for SMS/voice. Additional config:
  WHATSAPP_FROM_NUMBER  — your Twilio WhatsApp-enabled number or sandbox sender
                          (e.g. whatsapp:+14155238886 for the sandbox).
  WHATSAPP_ALLOWED_NUMBERS — comma-separated E.164 allowlist. Leave blank to
                              allow any (NOT recommended in production).

Outbound: send_message(to, text) — sends via Twilio Messages API.
Inbound:  Twilio calls POST /twilio/whatsapp; dispatch_inbound() processes it
          and returns TwiML.

Setup:
  1. Enable WhatsApp in Twilio (or use the sandbox at
     https://console.twilio.com/us1/develop/sms/settings/whatsapp-sandbox).
  2. Set WHATSAPP_FROM_NUMBER and WHATSAPP_ALLOWED_NUMBERS in .env.
  3. Point the Twilio WhatsApp webhook at https://your-host/twilio/whatsapp.
"""
import json
import urllib.parse
import urllib.request
from typing import Callable, Optional

import config

_agent_run_fn: Optional[Callable] = None


def set_agent_run_fn(fn: Callable) -> None:
    global _agent_run_fn
    _agent_run_fn = fn


def _sid() -> str:
    return getattr(config, "TWILIO_SID", "") or ""


def _auth() -> str:
    return getattr(config, "TWILIO_AUTH_TOKEN", "") or ""


def _from() -> str:
    n = getattr(config, "WHATSAPP_FROM_NUMBER", "") or ""
    if not n:
        return ""
    return n if n.startswith("whatsapp:") else f"whatsapp:{n}"


def is_configured() -> bool:
    return bool(_sid() and _auth() and _from())


def _allowed_numbers() -> set[str]:
    raw = getattr(config, "WHATSAPP_ALLOWED_NUMBERS", []) or []
    return {n.strip().replace("whatsapp:", "") for n in raw if n.strip()}


def _is_allowed(number: str) -> bool:
    allowed = _allowed_numbers()
    return not allowed or number.replace("whatsapp:", "").strip() in allowed


def send_message(to: str, text: str) -> str:
    if not is_configured():
        return "[WhatsApp] Twilio credentials or WHATSAPP_FROM_NUMBER not configured."
    to_wa = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    payload = urllib.parse.urlencode({
        "To": to_wa,
        "From": _from(),
        "Body": text[:1600],
    }).encode()
    url = f"https://api.twilio.com/2010-04-01/Accounts/{_sid()}/Messages.json"
    try:
        import base64
        creds = base64.b64encode(f"{_sid()}:{_auth()}".encode()).decode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": f"Basic {creds}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("sid"):
                return f"WhatsApp message sent to {to_wa}"
            return f"[WhatsApp] Twilio error: {result.get('message', result)}"
    except Exception as e:
        return f"[WhatsApp] send_message failed: {e}"


def dispatch_inbound(form: dict) -> str:
    """Process a Twilio WhatsApp inbound form. Returns TwiML response body."""
    from_wa = (form.get("From") or "").strip()
    body = (form.get("Body") or "").strip()

    if not from_wa or not body:
        return "<Response/>"

    if not _is_allowed(from_wa):
        return "<Response><Message>Sorry, this number is not authorized.</Message></Response>"

    if _agent_run_fn is None:
        return "<Response><Message>Agent not ready yet. Try again in a moment.</Message></Response>"

    try:
        reply = _agent_run_fn(
            f"[WhatsApp from {from_wa}] {body}",
            channel_id=f"whatsapp:{from_wa.replace('whatsapp:', '')}",
        )
    except Exception as e:
        reply = f"Agent error: {e}"

    if len(reply) > 1600:
        reply = reply[:1590] + "…"

    # Escape TwiML: an unescaped '<'/'&' (common in agent/web text) corrupts the
    # message or injects Twilio verbs. Element-content escaping via stdlib.
    from xml.sax.saxutils import escape as _xml_escape
    return f"<Response><Message>{_xml_escape(reply)}</Message></Response>"
