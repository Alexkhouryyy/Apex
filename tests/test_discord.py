"""Unit tests for tools/discord.py — the Discord channel.

Covers Ed25519 signature verification and interaction dispatch (PING/PONG,
deferred slash-command handling, the user allowlist). No network calls.
"""
import threading
import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config
from tools import discord


@pytest.fixture(autouse=True)
def reset_discord(monkeypatch):
    """Reset module + config state so tests don't leak into each other."""
    monkeypatch.setattr(discord, "_agent_run_fn", None, raising=False)
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "DISCORD_PUBLIC_KEY", "", raising=False)
    monkeypatch.setattr(config, "DISCORD_ALLOWED_USER_IDS", [], raising=False)
    yield


def _keypair(monkeypatch):
    """Generate an Ed25519 keypair and register the public key in config."""
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    monkeypatch.setattr(config, "DISCORD_PUBLIC_KEY", pub_hex, raising=False)
    return priv


def _allow(monkeypatch, *user_ids):
    """Allowlist these Discord user ids.

    Channels are deny-by-default when the allowlist is empty (see
    tools/discord._is_allowed), so any test that expects a command to get past
    authorization has to say who is allowed. Without this the test passes only
    on a machine whose .env happens to list the id.
    """
    monkeypatch.setattr(
        config, "DISCORD_ALLOWED_USER_IDS", list(user_ids), raising=False
    )


def _command(value="hi", user_id="1"):
    return {
        "type": 2,
        "application_id": "app1",
        "token": "tok1",
        "data": {"options": [{"type": 3, "name": "prompt", "value": value}]},
        "member": {"user": {"id": user_id, "username": "alex"}},
    }


class TestVerifySignature:
    def test_valid_signature_accepted(self, monkeypatch):
        priv = _keypair(monkeypatch)
        ts, body = "1700000000", b'{"type":1}'
        sig = priv.sign(ts.encode() + body).hex()
        assert discord.verify_signature(sig, ts, body) is True

    def test_tampered_body_rejected(self, monkeypatch):
        priv = _keypair(monkeypatch)
        ts, body = "1700000000", b'{"type":1}'
        sig = priv.sign(ts.encode() + body).hex()
        assert discord.verify_signature(sig, ts, b'{"type":2}') is False

    def test_bad_hex_signature_rejected(self, monkeypatch):
        _keypair(monkeypatch)
        assert discord.verify_signature("not-hex", "1700000000", b"{}") is False

    def test_missing_public_key_rejected(self):
        assert discord.verify_signature("00" * 64, "1700000000", b"{}") is False

    def test_missing_signature_pieces_rejected(self, monkeypatch):
        _keypair(monkeypatch)
        assert discord.verify_signature("", "", b"{}") is False


class TestDispatchInteraction:
    def test_ping_returns_pong(self):
        assert discord.dispatch_interaction({"type": 1}) == {"type": 1}

    def test_unknown_type_returns_message(self):
        resp = discord.dispatch_interaction({"type": 99})
        assert resp["type"] == 4

    def test_command_without_agent_says_not_ready(self, monkeypatch):
        _allow(monkeypatch, "1")
        resp = discord.dispatch_interaction(_command())
        assert resp["type"] == 4
        assert "not ready" in resp["data"]["content"]

    def test_command_without_text_rejected(self, monkeypatch):
        _allow(monkeypatch, "1")
        monkeypatch.setattr(discord, "_agent_run_fn", lambda *a, **k: "x")
        bad = _command()
        bad["data"]["options"] = []
        resp = discord.dispatch_interaction(bad)
        assert resp["type"] == 4
        assert "No message" in resp["data"]["content"]

    def test_disallowed_user_rejected(self, monkeypatch):
        _allow(monkeypatch, "999")
        monkeypatch.setattr(discord, "_agent_run_fn", lambda *a, **k: "x")
        resp = discord.dispatch_interaction(_command(user_id="1"))
        assert resp["type"] == 4
        assert "not authorized" in resp["data"]["content"]

    def test_empty_allowlist_rejects_everyone(self, monkeypatch):
        """Deny-by-default: an unconfigured allowlist is not an open door."""
        monkeypatch.setattr(discord, "_agent_run_fn", lambda *a, **k: "x")
        resp = discord.dispatch_interaction(_command(user_id="1"))
        assert "not authorized" in resp["data"]["content"]

    def test_authorization_is_checked_before_anything_else(self, monkeypatch):
        """An unauthorized caller learns nothing about Apex's internal state.

        The auth check has to come first. If readiness or argument validation
        ran ahead of it, a stranger's probe would tell them whether the agent
        is running and whether their payload parsed — free reconnaissance,
        and each is a different reply, so probes are distinguishable.
        """
        bad = _command(user_id="1")
        bad["data"]["options"] = []          # would trip "No message provided."
        assert discord._agent_run_fn is None  # would trip "Agent not ready yet."
        resp = discord.dispatch_interaction(bad)
        assert "not authorized" in resp["data"]["content"]

    def test_happy_path_defers_and_runs_agent(self, monkeypatch):
        captured = {}
        done = threading.Event()

        def fake_run(text, *, channel_id=None):
            captured["text"] = text
            captured["channel_id"] = channel_id
            return "agent reply"

        def fake_edit(app_id, token, content):
            captured["edit"] = (app_id, token, content)
            done.set()

        _allow(monkeypatch, "1")
        monkeypatch.setattr(discord, "_agent_run_fn", fake_run)
        monkeypatch.setattr(discord, "_edit_original", fake_edit)

        resp = discord.dispatch_interaction(_command(value="do a thing"))
        assert resp == {"type": 5}

        assert done.wait(timeout=5), "background worker did not finish"
        assert "do a thing" in captured["text"]
        assert captured["channel_id"] == "discord:1"
        assert captured["edit"] == ("app1", "tok1", "agent reply")


class TestSendMessage:
    def test_returns_error_when_not_configured(self):
        result = discord.send_message("123", "hello")
        assert "not configured" in result
