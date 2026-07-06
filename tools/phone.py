"""Twilio voice + SMS — proactive outbound and inbound webhook handlers.

Outbound:
  - sms_send(to, body): REST-API SMS
  - voice_call(to, message): outbound call that speaks `message`

Inbound webhooks are wired in dashboard/server.py (POST /twilio/sms,
POST /twilio/voice) which call `dispatch_inbound_*` here.

Inbound numbers are checked against `config.PHONE_ALLOWED_NUMBERS` —
if the list is non-empty and the From number isn't in it, the request is rejected.
"""
import re
from typing import Callable, Optional

import config

_agent_run_fn: Optional[Callable] = None


def set_agent_run_fn(fn: Callable) -> None:
    """Wire main.py's agent.run so inbound SMS/calls have somewhere to go."""
    global _agent_run_fn
    _agent_run_fn = fn


def _client():
    sid = getattr(config, "TWILIO_SID", "") or ""
    tok = getattr(config, "TWILIO_AUTH_TOKEN", "") or ""
    if not sid or not tok:
        return None
    try:
        from twilio.rest import Client
        return Client(sid, tok)
    except Exception as e:
        print(f"[Phone] Twilio import failed: {e}")
        return None


def _from_number() -> str:
    return getattr(config, "TWILIO_FROM_NUMBER", "") or ""


def _is_allowed(num: str) -> bool:
    allowed = getattr(config, "PHONE_ALLOWED_NUMBERS", []) or []
    if not allowed:
        return False  # DENY-BY-DEFAULT: no allowlist configured → nobody gets through
    norm = re.sub(r"\D", "", num or "")
    return any(re.sub(r"\D", "", a) == norm for a in allowed)


def sms_send(to: str, body: str) -> str:
    c = _client()
    if c is None:
        return "[phone] Twilio not configured. Set TWILIO_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER in .env."
    if not _from_number():
        return "[phone] TWILIO_FROM_NUMBER not set."
    try:
        msg = c.messages.create(to=to, from_=_from_number(), body=body[:1500])
        return f"SMS queued sid={msg.sid} to={to}"
    except Exception as e:
        return f"[phone] SMS send failed: {e}"


def voice_call(to: str, message: str) -> str:
    """Place an outbound call that reads `message` via Polly TTS, then hangs up."""
    c = _client()
    if c is None:
        return "[phone] Twilio not configured."
    if not _from_number():
        return "[phone] TWILIO_FROM_NUMBER not set."
    twiml = f'<Response><Say voice="Polly.Joanna">{_escape_xml(message[:1500])}</Say></Response>'
    try:
        call = c.calls.create(to=to, from_=_from_number(), twiml=twiml)
        return f"Call queued sid={call.sid} to={to}"
    except Exception as e:
        return f"[phone] Call failed: {e}"


def _escape_xml(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


# === Inbound dispatch (called from dashboard/server.py webhook routes) ===

def dispatch_inbound_sms(from_number: str, body: str) -> str:
    """Returns a TwiML response."""
    if not _is_allowed(from_number):
        return _twiml_say("Sorry, this number is not authorized.")
    if _agent_run_fn is None:
        return _twiml_say("Agent is not ready yet.")
    try:
        reply = _agent_run_fn(f"[Inbound SMS from {from_number}] {body}", channel_id=f"sms:{from_number}")
    except Exception as e:
        reply = f"Agent error: {e}"
    return f'<Response><Message>{_escape_xml(reply[:1500])}</Message></Response>'


def dispatch_inbound_voice(from_number: str, speech_result: Optional[str] = None) -> str:
    """
    First webhook hit (no speech_result yet): respond with TwiML <Gather> prompt.
    Second webhook hit (with speech_result): run agent, respond with TwiML <Say>.
    """
    if not _is_allowed(from_number):
        return _twiml_say("This number is not authorized. Goodbye.", hangup=True)

    if not speech_result:
        return (
            '<Response>'
            '<Gather input="speech" action="/twilio/voice" method="POST" speechTimeout="auto" language="en-US">'
            '<Say voice="Polly.Joanna">Hi. What can I help with?</Say>'
            '</Gather>'
            '<Say voice="Polly.Joanna">I did not hear anything. Goodbye.</Say>'
            '</Response>'
        )

    if _agent_run_fn is None:
        return _twiml_say("Agent is not ready. Try again later.", hangup=True)

    try:
        reply = _agent_run_fn(f"[Inbound voice call from {from_number}] {speech_result}", channel_id=f"voice:{from_number}")
    except Exception as e:
        reply = f"Agent error: {e}"
    return (
        '<Response>'
        f'<Say voice="Polly.Joanna">{_escape_xml(reply[:1500])}</Say>'
        '<Gather input="speech" action="/twilio/voice" method="POST" speechTimeout="auto" language="en-US">'
        '<Say voice="Polly.Joanna">Anything else?</Say>'
        '</Gather>'
        '<Say voice="Polly.Joanna">Goodbye.</Say>'
        '</Response>'
    )


def _twiml_say(text: str, hangup: bool = False) -> str:
    extra = "<Hangup/>" if hangup else ""
    return f'<Response><Say voice="Polly.Joanna">{_escape_xml(text)}</Say>{extra}</Response>'
