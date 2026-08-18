"""A dashboard that cannot bind must say so, not print a URL.

`start_in_background` ran uvicorn inside a daemon thread, so a bind failure
surfaced only as a traceback in that thread — while main.py had *already*
printed `[Dashboard] http://0.0.0.0:7860` unconditionally. Start a second Apex
while the first is running and it announced a working dashboard URL that was
never serving, and the caller's try/except could not see the failure.

The announced host was wrong a second way too: main.py printed
config.DASHBOARD_HOST, but the tokenless-bind guard can quietly move the bind to
127.0.0.1. The URL shown was the one requested, never the one in effect.

Both are the same failure shape this codebase keeps producing — something breaks
and the surface says it worked.
"""
from __future__ import annotations

import socket

import pytest

import config
from dashboard import server as dash


@pytest.fixture
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_bind_failure_raises_instead_of_printing_a_url(free_port, monkeypatch):
    """The regression: a taken port must reach the caller."""
    monkeypatch.setattr(config, "DASHBOARD_HOST", "127.0.0.1", raising=False)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", free_port))
    blocker.listen(1)
    try:
        with pytest.raises(RuntimeError) as exc:
            dash.start_in_background(port=free_port, host="127.0.0.1")
        msg = str(exc.value)
        assert str(free_port) in msg
        # The message has to be actionable, not just true.
        assert "already" in msg.lower() or "running" in msg.lower()
    finally:
        blocker.close()


def test_successful_start_reports_the_address_it_bound(free_port, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_HOST", "127.0.0.1", raising=False)
    t = dash.start_in_background(port=free_port, host="127.0.0.1")
    assert t.dashboard_port == free_port
    assert t.dashboard_host == "127.0.0.1"
    assert t.dashboard_url == f"http://127.0.0.1:{free_port}"


def test_tokenless_public_bind_is_reported_as_loopback(free_port, monkeypatch):
    """The guard downgrades 0.0.0.0 to loopback. The caller must be told the
    truth, or it prints a LAN URL for a server only reachable locally."""
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "DASHBOARD_HOST", "0.0.0.0", raising=False)
    t = dash.start_in_background(port=free_port, host="0.0.0.0")
    assert t.dashboard_host == "127.0.0.1", "tokenless public bind was not downgraded"
    assert "0.0.0.0" not in t.dashboard_url


def test_url_never_advertises_the_wildcard_address(free_port, monkeypatch):
    """0.0.0.0 is a bind directive, not somewhere you can point a browser."""
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "a-token", raising=False)
    monkeypatch.setattr(config, "DASHBOARD_HOST", "0.0.0.0", raising=False)
    t = dash.start_in_background(port=free_port, host="0.0.0.0")
    assert t.dashboard_host == "0.0.0.0", "a tokened public bind should stay public"
    assert t.dashboard_url == f"http://127.0.0.1:{free_port}"
