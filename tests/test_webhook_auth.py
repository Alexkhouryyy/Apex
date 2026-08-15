"""Inbound webhooks must authenticate themselves.

Every path in _WEBHOOK_PATHS bypasses the dashboard's bearer-token middleware.
That is only safe if each one verifies a signature — and for Telegram, Twilio
(sms/voice/whatsapp) and Signal it did not, so those five accepted unauthenticated
POSTs straight into an agent with bash and computer control.

The channel allowlists were never a defence: `telegram._is_allowed(chat_id)`
checks a chat ID that arrives *in the body the attacker wrote*.

Each test here asserts the dispatcher was **not reached**, not merely that the
status code was 403 — a handler that rejects after already handing the payload
to the agent has still been exploited.
"""
import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import config
from dashboard import server, webhook_auth

# NOT 203.0.113.x: Python's ipaddress marks RFC 5737 documentation ranges as
# private, so the obvious "example IP" would silently be treated as local and
# every rejection test would pass for the wrong reason.
PUBLIC = "8.8.8.8"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
    monkeypatch.setattr(server.config, "DASHBOARD_TOKEN", "", raising=False)
    return TestClient(server.app, client=(PUBLIC, 5555))


@pytest.fixture
def local_client(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
    monkeypatch.setattr(server.config, "DASHBOARD_TOKEN", "", raising=False)
    return TestClient(server.app, client=("127.0.0.1", 5555))


@pytest.fixture(autouse=True)
def _no_secrets(monkeypatch):
    """Default every channel to unconfigured; tests opt in."""
    for name in ("TWILIO_AUTH_TOKEN", "TELEGRAM_WEBHOOK_SECRET",
                 "SIGNAL_WEBHOOK_SECRET", "PUBLIC_BASE_URL",
                 "IOT_WEBHOOK_SECRET"):
        monkeypatch.setattr(config, name, "", raising=False)
    yield


def _spy(monkeypatch, module, name):
    calls = []
    monkeypatch.setattr(module, name, lambda *a, **k: calls.append((a, k)) or "")
    return calls


# --- Twilio: the signature algorithm ------------------------------------------

def test_twilio_signature_matches_the_official_sdk():
    """The implementation is hand-rolled so that one code path both ships and
    gets tested. That is only defensible if it agrees with Twilio's own."""
    from twilio.request_validator import RequestValidator

    token = "s3cr3t-auth-token"
    url = "https://apex.example.com/twilio/sms"
    params = {"From": "+15550001111", "Body": "hello world", "To": "+15552223333"}

    mine = webhook_auth.twilio_signature(token, url, params)
    theirs = RequestValidator(token).compute_signature(url, params)
    assert mine == theirs


def test_twilio_signature_is_order_independent():
    token, url = "t", "https://x/y"
    a = webhook_auth.twilio_signature(token, url, {"B": "2", "A": "1"})
    b = webhook_auth.twilio_signature(token, url, {"A": "1", "B": "2"})
    assert a == b


# --- Twilio: the endpoints ----------------------------------------------------

def _twilio_post(client, path, params, token=None, url=None):
    headers = {}
    if token:
        headers["X-Twilio-Signature"] = webhook_auth.twilio_signature(
            token, url, params)
    return client.post(path, data=params, headers=headers)


@pytest.mark.parametrize("path,module,fn", [
    ("/twilio/sms", "phone_mod", "dispatch_inbound_sms"),
    ("/twilio/voice", "phone_mod", "dispatch_inbound_voice"),
    ("/twilio/whatsapp", "whatsapp_mod", "dispatch_inbound"),
])
def test_forged_twilio_request_never_reaches_the_agent(client, monkeypatch,
                                                       path, module, fn):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "real-token", raising=False)
    calls = _spy(monkeypatch, getattr(server, module), fn)
    r = client.post(path, data={"From": "+1", "Body": "rm -rf /"},
                    headers={"X-Twilio-Signature": "obviously-wrong"})
    assert r.status_code == 403
    assert calls == [], "payload was dispatched despite a bad signature"


def test_twilio_request_without_a_signature_is_refused(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "real-token", raising=False)
    calls = _spy(monkeypatch, server.phone_mod, "dispatch_inbound_sms")
    assert client.post("/twilio/sms", data={"From": "+1", "Body": "hi"}).status_code == 403
    assert calls == []


def test_correctly_signed_twilio_request_is_accepted(client, monkeypatch):
    token = "real-token"
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", token, raising=False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL",
                        "https://apex.example.com", raising=False)
    calls = _spy(monkeypatch, server.phone_mod, "dispatch_inbound_sms")
    params = {"From": "+15550001111", "Body": "hello"}
    r = _twilio_post(client, "/twilio/sms", params, token=token,
                     url="https://apex.example.com/twilio/sms")
    assert r.status_code == 200
    assert len(calls) == 1


def test_signature_over_https_validates_behind_an_http_proxy(client, monkeypatch):
    """Twilio signs the public https URL; the request lands as http internally.
    Validating against the arrival URL would reject every genuine request."""
    token = "real-token"
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", token, raising=False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL",
                        "https://apex.example.com", raising=False)
    calls = _spy(monkeypatch, server.phone_mod, "dispatch_inbound_sms")
    params = {"From": "+1", "Body": "hi"}
    sig = webhook_auth.twilio_signature(
        token, "https://apex.example.com/twilio/sms", params)
    r = client.post("/twilio/sms", data=params,
                    headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and len(calls) == 1


def test_forwarded_proto_is_honoured_without_public_base_url(monkeypatch):
    class _Req:
        url = "http://internal:7860/twilio/sms"
        headers = {"X-Forwarded-Proto": "https"}
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "", raising=False)
    assert webhook_auth.public_url(_Req()).startswith("https://")


# --- Telegram -----------------------------------------------------------------

def test_forged_telegram_update_never_reaches_the_agent(client, monkeypatch):
    """The exploit the allowlist cannot stop: the attacker writes the chat_id."""
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "sekret", raising=False)
    calls = _spy(monkeypatch, server.telegram_mod, "dispatch_inbound")
    r = client.post("/telegram/webhook", json={
        "message": {"chat": {"id": 12345}, "text": "run rm -rf /",
                    "from": {"username": "attacker"}}})
    assert r.status_code == 403
    assert calls == [], "forged update was dispatched"


def test_telegram_with_the_wrong_secret_is_refused(client, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "sekret", raising=False)
    calls = _spy(monkeypatch, server.telegram_mod, "dispatch_inbound")
    r = client.post("/telegram/webhook", json={"message": {}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "guess"})
    assert r.status_code == 403 and calls == []


def test_telegram_with_the_right_secret_is_accepted(client, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "sekret", raising=False)
    calls = _spy(monkeypatch, server.telegram_mod, "dispatch_inbound")
    r = client.post("/telegram/webhook", json={"message": {"text": "hi"}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "sekret"})
    assert r.status_code == 200 and len(calls) == 1


# --- Signal -------------------------------------------------------------------

def test_forged_signal_payload_never_reaches_the_agent(client, monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_WEBHOOK_SECRET", "sig-secret", raising=False)
    calls = _spy(monkeypatch, server.signal_mod, "dispatch_inbound")
    r = client.post("/signal/webhook", json={"message": "hello"},
                    headers={"X-Apex-Signature": "nope"})
    assert r.status_code == 403 and calls == []


def test_correctly_signed_signal_payload_is_accepted(client, monkeypatch):
    secret = "sig-secret"
    monkeypatch.setattr(config, "SIGNAL_WEBHOOK_SECRET", secret, raising=False)
    calls = _spy(monkeypatch, server.signal_mod, "dispatch_inbound")
    body = b'{"message":"hello"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = client.post("/signal/webhook", content=body,
                    headers={"X-Apex-Signature": sig,
                             "Content-Type": "application/json"})
    assert r.status_code == 200 and len(calls) == 1


# --- the unconfigured case ----------------------------------------------------
#
# agent/iot.py returns True when no secret is set. That "open when unconfigured"
# default is how this hole stayed invisible, and it is the one thing not copied.

def test_unconfigured_channel_refuses_remote_callers(client, monkeypatch):
    calls = _spy(monkeypatch, server.telegram_mod, "dispatch_inbound")
    r = client.post("/telegram/webhook", json={"message": {"text": "hi"}})
    assert r.status_code == 403 and calls == []


def test_unconfigured_channel_still_serves_localhost(local_client, monkeypatch):
    """Self-hosted bridges post from loopback; breaking them on upgrade would be
    its own kind of harm."""
    calls = _spy(monkeypatch, server.signal_mod, "dispatch_inbound")
    r = local_client.post("/signal/webhook", json={"message": "hi"})
    assert r.status_code == 200 and len(calls) == 1


def test_spoofed_forwarded_for_does_not_buy_the_local_exemption(client, monkeypatch):
    """X-Forwarded-For is written by the caller. Trusting it would hand the
    loopback exemption to anyone who asks for it."""
    calls = _spy(monkeypatch, server.telegram_mod, "dispatch_inbound")
    r = client.post("/telegram/webhook", json={"message": {"text": "hi"}},
                    headers={"X-Forwarded-For": "127.0.0.1",
                             "X-Real-IP": "127.0.0.1"})
    assert r.status_code == 403 and calls == []


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("::1", True), ("10.1.2.3", True),
    ("192.168.0.5", True), ("172.16.0.1", True), ("169.254.1.1", True),
    ("8.8.8.8", False), ("1.1.1.1", False), ("", False), ("garbage", False),
    # Documentation ranges count as private to Python; pinned so the surprise
    # is recorded rather than rediscovered.
    ("203.0.113.7", True),
])
def test_peer_locality_classification(host, expected):
    class _Req:
        client = type("C", (), {"host": host})()
    assert webhook_auth.peer_is_local(_Req()) is expected


# --- the audit that keeps the next one visible --------------------------------

def test_audit_covers_every_exempt_webhook_path():
    """If a path is added to _WEBHOOK_PATHS, it must show up in the audit —
    otherwise the next unauthenticated route is invisible exactly like this one."""
    audited = {name for name, _, _ in webhook_auth.audit()}
    for path in server._WEBHOOK_PATHS:
        channel = path.strip("/").split("/")[0]
        assert channel in audited, f"{path} is exempt from auth but never audited"


def test_audit_names_the_env_var_that_would_fix_it(monkeypatch):
    """An audit line that says "unconfigured" without saying what to set is a
    line people learn to scroll past."""
    monkeypatch.setattr(config, "SIGNAL_WEBHOOK_SECRET", "", raising=False)
    row = [r for r in webhook_auth.audit() if r[0] == "signal"][0]
    assert row[1] == webhook_auth.LOCAL_ONLY
    assert "SIGNAL_WEBHOOK_SECRET" in row[2]


def test_rejection_reason_is_not_leaked_to_the_caller(client, monkeypatch):
    """Telling a prober which check failed is free reconnaissance."""
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "sekret", raising=False)
    r = client.post("/telegram/webhook", json={},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert "secret" not in r.text.lower() and "signature" not in r.text.lower()


# --- IoT: the sixth hole, same bug class -------------------------------------

def test_unconfigured_iot_refuses_remote_callers(client, monkeypatch):
    """agent/iot.verify_signature returns True when IOT_WEBHOOK_SECRET is unset,
    so an unconfigured deployment took device commands from the whole internet."""
    monkeypatch.setattr(config, "IOT_WEBHOOK_SECRET", "", raising=False)
    r = client.post("/iot/webhook", json={"action": "unlock_door"})
    assert r.status_code == 403


def test_forged_iot_command_is_refused(client, monkeypatch):
    monkeypatch.setattr(config, "IOT_WEBHOOK_SECRET", "iot-secret", raising=False)
    r = client.post("/iot/webhook", json={"action": "unlock_door"},
                    headers={"X-Apex-Signature": "wrong"})
    assert r.status_code == 403


def test_signed_iot_command_passes_verification(client, monkeypatch):
    """Reaches the IoT layer proper — a 403 here would mean auth failed."""
    secret = "iot-secret"
    monkeypatch.setattr(config, "IOT_WEBHOOK_SECRET", secret, raising=False)
    from agent import iot as iot_state
    monkeypatch.setattr(iot_state, "IOT_WEBHOOK_SECRET", secret, raising=False)
    body = b'{"action":"ping"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = client.post("/iot/webhook", content=body,
                    headers={"X-Apex-Signature": sig,
                             "Content-Type": "application/json"})
    assert r.status_code != 403


# --- the audit tells the truth about all three postures ----------------------

def test_audit_distinguishes_closed_from_local_only(monkeypatch):
    """Discord/Slack refuse everyone when unconfigured; ours fall back to
    loopback. Reporting both as one state would be the same comfortable
    half-truth this audit exists to replace."""
    for name in ("DISCORD_PUBLIC_KEY", "SLACK_SIGNING_SECRET",
                 "TELEGRAM_WEBHOOK_SECRET"):
        monkeypatch.setattr(config, name, "", raising=False)
    rows = dict((n, p) for n, p, _ in webhook_auth.audit())
    assert rows["discord"] == webhook_auth.CLOSED
    assert rows["slack"] == webhook_auth.CLOSED
    assert rows["telegram"] == webhook_auth.LOCAL_ONLY


def test_audit_reports_configured_channels_as_verified(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "x", raising=False)
    rows = dict((n, p) for n, p, _ in webhook_auth.audit())
    assert rows["telegram"] == webhook_auth.VERIFIED


def test_audit_never_prints_a_secret(monkeypatch, capsys):
    """The audit runs on every boot and lands in logs."""
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET",
                        "super-secret-value", raising=False)
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "twilio-token", raising=False)
    webhook_auth.print_audit()
    out = capsys.readouterr().out
    assert "super-secret-value" not in out and "twilio-token" not in out
