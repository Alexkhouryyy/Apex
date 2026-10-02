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
import threading
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


# --- Call Apex: talk to it on a phone call (docs/CALL_APEX.md) ---------------
#
# Made for the car: the phone pairs with the car's hands-free system, you press
# the steering-wheel phone button and say "Call Apex". Twilio gives each
# webhook about 15 seconds to answer, and Apex can think for longer, so a
# question starts the work in the background and the call checks back every
# couple of seconds ("one moment") until the answer is ready.

VOICE_INSTRUCTIONS = (
    "[Phone call. The caller may be driving and cannot look at a screen. Answer in one to "
    "three short spoken sentences. No lists, headings, links, code or emoji. Before anything "
    "that changes something (sending, booking, buying, deleting, scheduling), say exactly what "
    "you will do and ask them to say yes; only then do it.]"
)
GOODBYES = re.compile(r"^\s*(bye|goodbye|good bye|that'?s all|that is all|nothing|no thanks?|"
                      r"no that'?s it|hang up|end (the )?call|stop)\W*$", re.I)
WAIT_STEP_SECONDS = 2
WAIT_STEPS = 20                       # about 40 s of "one moment" before texting instead
_calls: dict = {}                     # CallSid -> {"thread", "reply", "error", "from"}
_calls_lock = threading.Lock()


def _voice() -> str:
    return getattr(config, "TWILIO_VOICE", "") or "Polly.Joanna-Neural"


def spoken(text: str, limit: int = 1200) -> str:
    """Model text made fit to be read aloud: no markdown, links or code."""
    t = re.sub(r"```.*?```", " (details are on screen) ", text or "", flags=re.S)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[*_#`>|]+", "", t)
    t = re.sub(r"^\s*[-•\d]+[.)]?\s+", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit]
        t = cut[:cut.rfind(". ") + 1] or cut
    return t


def _say(text: str) -> str:
    return f'<Say voice="{_voice()}">{_escape_xml(text)}</Say>'


def _listen(prompt: str = "") -> str:
    """Wait for the caller to speak; they can talk over the prompt."""
    inner = _say(prompt) if prompt else ""
    return (f'<Gather input="speech" action="/twilio/voice" method="POST" speechTimeout="auto" '
            f'language="en-US">{inner}</Gather>{_say("Goodbye.")}')


def _start(call_sid: str, from_number: str, speech: str) -> None:
    job = {"from": from_number, "reply": None, "error": None}

    def work():
        try:
            job["reply"] = _agent_run_fn(f"{VOICE_INSTRUCTIONS}\n{speech}", channel_id=f"voice:{from_number}")
        except Exception as e:
            job["error"] = str(e)[:200]
    with _calls_lock:
        _calls[call_sid] = job
        while len(_calls) > 32:
            _calls.pop(next(iter(_calls)))
    job["thread"] = threading.Thread(target=work, daemon=True, name="CallApex")
    job["thread"].start()


def dispatch_inbound_voice(from_number: str, speech_result: Optional[str] = None,
                           call_sid: str = "") -> str:
    """First webhook hit: greet and listen. With speech: start thinking and
    check back (`dispatch_voice_wait`), or end the call on a goodbye."""
    if not _is_allowed(from_number):
        return _twiml_say("This number is not authorized. Goodbye.", hangup=True)
    if not speech_result:
        return f"<Response>{_listen('Apex here. What do you need?')}</Response>"
    if GOODBYES.match(speech_result):
        return f"<Response>{_say('Okay. Drive safe.')}<Hangup/></Response>"
    if _agent_run_fn is None:
        return _twiml_say("Apex is still starting. Try again in a minute.", hangup=True)
    _start(call_sid or f"local:{from_number}", from_number, speech_result)
    return (f"<Response>{_say('One moment.')}"
            f'<Redirect method="POST">/twilio/voice/wait?n=0</Redirect></Response>')


def dispatch_voice_wait(from_number: str, call_sid: str, n: int) -> str:
    """Speak the answer if it is ready; otherwise pause and check again. After
    about 40 seconds, stop holding the caller: keep working and text the answer."""
    if not _is_allowed(from_number):
        return _twiml_say("This number is not authorized. Goodbye.", hangup=True)
    with _calls_lock:
        job = _calls.get(call_sid or f"local:{from_number}")
    if job is None or job["from"] != from_number:
        return f"<Response>{_listen('Sorry, I lost that one. What do you need?')}</Response>"
    if job["error"] is not None:
        return f"<Response>{_listen('Sorry, something went wrong on my side. Try asking again.')}</Response>"
    if job["reply"] is not None:
        with _calls_lock:
            _calls.pop(call_sid or f"local:{from_number}", None)
        return f"<Response>{_say(spoken(job['reply']) or 'Done.')}{_listen('Anything else?')}</Response>"
    if n >= WAIT_STEPS:
        def text_later():
            job["thread"].join(timeout=600)
            if job["reply"]:
                sms_send(from_number, spoken(job["reply"], 1500))
        threading.Thread(target=text_later, daemon=True, name="CallApexText").start()
        return (f"<Response>{_say('This is taking a while. I will text you the answer when it is ready.')}"
                f"{_listen('Anything else meanwhile?')}</Response>")
    return (f'<Response><Pause length="{WAIT_STEP_SECONDS}"/>'
            f'<Redirect method="POST">/twilio/voice/wait?n={n + 1}</Redirect></Response>')


def _twiml_say(text: str, hangup: bool = False) -> str:
    extra = "<Hangup/>" if hangup else ""
    return f'<Response>{_say(text)}{extra}</Response>'
