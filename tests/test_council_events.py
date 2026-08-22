"""The council view is two halves that must agree on the same strings.

`dashboard/server.py` broadcasts council_* WebSocket events; `app.js` handles
them. Nothing tied the two together, which made "Council visualization" an
UNPROVEN row: rename an event on one side and the other stops hearing it — the
panel simply never updates, with no error anywhere.

The wiring audit's orphan_ws_events check already covers events emitted with no
handler. This covers the opposite direction too, and pins the pairing.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = (REPO / "dashboard" / "server.py").read_text(errors="replace")
APP_JS = (REPO / "dashboard" / "static" / "app.js").read_text(errors="replace")


def _emitted() -> set[str]:
    return set(re.findall(r'"type":\s*"(council_\w+)"', SERVER))


def _handled() -> set[str]:
    return set(re.findall(r"msg\.type\s*===\s*'(council_\w+)'", APP_JS))


def test_the_council_actually_emits_events():
    """Guard the guard: if the regex stops matching, every assertion below
    becomes vacuously true."""
    assert len(_emitted()) >= 4, f"only found {_emitted()} — the scanner has drifted"


def test_every_emitted_event_has_a_handler():
    orphans = sorted(_emitted() - _handled())
    assert not orphans, (
        f"emitted with nothing listening: {orphans} — the panel silently "
        f"stops updating"
    )


def test_every_handled_event_is_emitted():
    """A handler for an event nobody sends is dead code that looks like a
    feature."""
    dead = sorted(_handled() - _emitted())
    assert not dead, f"handled but never emitted: {dead}"


def test_the_lifecycle_is_complete():
    """A view that can start but never finish leaves a spinner forever."""
    emitted = _emitted()
    for required in ("council_progress", "council_answer", "council_done"):
        assert required in emitted, f"{required} is never emitted"


def test_failure_is_reported_to_the_view():
    """Without an error event a failed council looks identical to a slow one."""
    assert "council_error" in _emitted(), "a council failure never reaches the UI"
    assert "council_error" in _handled(), "council_error is emitted but ignored"
