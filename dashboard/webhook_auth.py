"""Signature verification for inbound webhooks.

Every path in `_WEBHOOK_PATHS` is exempt from the dashboard's bearer-token
middleware, on the premise that each carries its own per-service auth. That was
true for Discord, Slack and IoT and false for Telegram, Twilio and Signal, which
accepted unauthenticated POSTs straight into an agent with bash and computer
control. The channel allowlists are not a substitute: they check a chat ID or
phone number that arrives *in the request body*, which is the attacker's to
choose.

One module rather than five copy-pastes, so the next channel added has an obvious
place to plug into and the startup audit can see all of them at once.

Two deliberate departures from `agent/iot.py:112`, whose HMAC construction this
otherwise mirrors:

1. **Unconfigured does not mean open.** IoT returns True when no secret is set.
   That default is precisely how a hole stays invisible for months.
2. **Loopback is the escape hatch, not absence of config.** Self-hosted bridges
   (signal-cli et al) usually post from localhost, so those keep working while
   the internet does not get in. The peer address is read from the real socket
   (`request.client.host`) and never from `X-Forwarded-For`, which is a header
   the caller writes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
from urllib.parse import urlparse

import config


class WebhookRejected(Exception):
    """Raised with a reason suitable for logging, never for the response body —
    telling a prober *why* a signature failed is free reconnaissance."""


# --- peer trust ---------------------------------------------------------------

def peer_is_local(request) -> bool:
    """True when the socket peer is loopback or a private address.

    Deliberately ignores X-Forwarded-For: a remote attacker controls that header
    completely, so trusting it would hand out the exemption to anyone who asks.
    A real proxy in front of Apex terminates the connection itself, which is why
    proxied traffic is treated as local only if the proxy is.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") or ""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _require(configured: bool, request, channel: str) -> None:
    """Gate for channels with no secret configured."""
    if configured:
        return
    if peer_is_local(request):
        return
    raise WebhookRejected(
        f"{channel}: no secret configured and request came from "
        f"{getattr(getattr(request, 'client', None), 'host', '?')}"
    )


# --- Twilio -------------------------------------------------------------------
#
# Implemented from Twilio's published algorithm rather than calling the SDK's
# RequestValidator. One code path means the thing under test is the thing that
# ships; a test that exercises a local implementation while production imports
# an optional package is testing the wrong code. Cross-checked against the real
# SDK in tests/test_webhook_auth.py.

def twilio_signature(auth_token: str, url: str, params: dict) -> str:
    """HMAC-SHA1 over the full URL with POST params appended in key order."""
    payload = url
    for key in sorted(params):
        payload += key + str(params[key])
    digest = hmac.new(auth_token.encode("utf-8"),
                      payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def public_url(request) -> str:
    """The URL Twilio signed, which is not necessarily the one that arrived.

    Behind a tunnel or reverse proxy the request lands as http://internal-host
    while Twilio computed its signature over https://public-host. Validating
    against the wrong one rejects every legitimate request, so PUBLIC_BASE_URL
    wins when set, and X-Forwarded-Proto is honoured otherwise.
    """
    base = (getattr(config, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    url = str(request.url)
    if base:
        parsed = urlparse(url)
        tail = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        return base + tail
    proto = request.headers.get("X-Forwarded-Proto", "")
    if proto == "https" and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def verify_twilio(request, params: dict) -> None:
    """Raise WebhookRejected unless X-Twilio-Signature checks out."""
    token = getattr(config, "TWILIO_AUTH_TOKEN", "") or ""
    _require(bool(token), request, "twilio")
    if not token:
        return                                    # local peer, unconfigured
    provided = request.headers.get("X-Twilio-Signature", "")
    if not provided:
        raise WebhookRejected("twilio: missing X-Twilio-Signature")
    expected = twilio_signature(token, public_url(request), params)
    if not hmac.compare_digest(provided, expected):
        raise WebhookRejected("twilio: signature mismatch")


# --- Telegram -----------------------------------------------------------------

def verify_telegram(request) -> None:
    """Raise unless the secret registered with setWebhook is echoed back.

    Telegram sends the value given to setWebhook's `secret_token` in
    X-Telegram-Bot-Api-Secret-Token on every delivery.
    """
    secret = (getattr(config, "TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
    _require(bool(secret), request, "telegram")
    if not secret:
        return
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not provided or not hmac.compare_digest(provided, secret):
        raise WebhookRejected("telegram: bad or missing secret token")


# --- Signal -------------------------------------------------------------------

def verify_signal(request, body: bytes) -> None:
    """Raise unless X-Apex-Signature is HMAC-SHA256(body, SIGNAL_WEBHOOK_SECRET).

    signal-cli bridges have no signing standard of their own, so this is a shared
    secret the user configures on whatever posts to the webhook.
    """
    secret = (getattr(config, "SIGNAL_WEBHOOK_SECRET", "") or "").strip()
    _require(bool(secret), request, "signal")
    if not secret:
        return
    provided = request.headers.get("X-Apex-Signature", "")
    if not provided:
        raise WebhookRejected("signal: missing X-Apex-Signature")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise WebhookRejected("signal: signature mismatch")


# --- IoT ----------------------------------------------------------------------

def verify_iot(request, body: bytes) -> None:
    """Same HMAC as agent/iot.verify_signature, minus its fail-open default.

    That function returns True when IOT_WEBHOOK_SECRET is unset, so an
    unconfigured deployment accepted device commands from anyone on the
    internet. It is left alone for other callers; the endpoint gets the loopback
    rule so a LAN sensor still works and the internet does not.
    """
    from agent import iot as iot_state

    secret = (getattr(config, "IOT_WEBHOOK_SECRET", "") or "").strip()
    _require(bool(secret), request, "iot")
    if not secret:
        return
    if not iot_state.verify_signature(
            request.headers.get("X-Apex-Signature", ""), body):
        raise WebhookRejected("iot: signature mismatch")


# --- startup audit ------------------------------------------------------------

VERIFIED = "verified"          # a signature is checked on every request
LOCAL_ONLY = "local-only"      # unconfigured; remote callers refused
CLOSED = "closed"              # unconfigured; refuses everyone, including local


def audit() -> list[tuple[str, str, str]]:
    """(channel, posture, note) for every inbound webhook.

    This exists because the hole survived by being invisible: nothing ever said
    an endpoint was open, and a stale comment claimed the opposite. Printing the
    state on every boot means the next one announces itself.

    Three postures, not two, because the channels genuinely differ when no
    secret is set — Discord and Slack refuse everyone, ours fall back to
    loopback. Collapsing that distinction would make this report the same kind
    of comfortable half-truth it exists to replace.
    """
    def row(name, secret, how, fix, unconfigured):
        return (name, VERIFIED if secret else unconfigured,
                how if secret else fix)

    return [
        row("discord", getattr(config, "DISCORD_PUBLIC_KEY", ""),
            "ed25519", "set DISCORD_PUBLIC_KEY", CLOSED),
        row("slack", getattr(config, "SLACK_SIGNING_SECRET", ""),
            "HMAC-SHA256", "set SLACK_SIGNING_SECRET", CLOSED),
        row("iot", getattr(config, "IOT_WEBHOOK_SECRET", ""),
            "HMAC-SHA256", "set IOT_WEBHOOK_SECRET", LOCAL_ONLY),
        row("telegram", getattr(config, "TELEGRAM_WEBHOOK_SECRET", ""),
            "secret token", "set TELEGRAM_WEBHOOK_SECRET + re-register webhook",
            LOCAL_ONLY),
        row("twilio", getattr(config, "TWILIO_AUTH_TOKEN", ""),
            "X-Twilio-Signature", "set TWILIO_AUTH_TOKEN", LOCAL_ONLY),
        row("signal", getattr(config, "SIGNAL_WEBHOOK_SECRET", ""),
            "HMAC-SHA256", "set SIGNAL_WEBHOOK_SECRET", LOCAL_ONLY),
    ]


def print_audit() -> None:
    rows = audit()
    for name, posture, note in rows:
        print(f"[Webhooks] {name:9s} {posture:12s} ({note})")
    local = [r for r in rows if r[1] == LOCAL_ONLY]
    if local:
        print(f"[Webhooks] {len(local)} unconfigured: reachable from localhost "
              f"and your LAN only — remote callers get 403.")
