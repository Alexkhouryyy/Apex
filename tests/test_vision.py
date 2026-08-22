"""Screen vision decides where to click. Only the camera gate was tested.

`tools/vision.py` is reached from `agent/core.py:1562-1571` — find_on_screen,
click_on, annotate_for_agent, click_mark. `tests/test_camera.py` covers the
camera *device* gate; nothing covered the screen-reading tools, which was the
UNPROVEN row.

OCR and a real screen are out of reach here. What is testable is the part where
being wrong does damage: click_on turns a text query into mouse coordinates, and
a miss that falls through to a click would press whatever happens to be under
the pointer.
"""
from __future__ import annotations

import pytest

from tools import vision


@pytest.fixture
def no_clicks(monkeypatch):
    """Record clicks instead of performing them."""
    clicks = []
    from tools import computer
    monkeypatch.setattr(computer, "click",
                        lambda x, y, button="left", double=False: clicks.append((x, y)))
    return clicks


def _match(x=100, y=200, text="Submit"):
    return {"text": text, "center_x": x, "center_y": y,
            "bbox": (x - 20, y - 5, 40, 10), "confidence": 95}


# ── the one that matters: never click on a miss ───────────────────────────────

def test_no_match_means_no_click(monkeypatch, no_clicks):
    """A click that happens anyway lands on whatever is under the pointer —
    in an agent with a mouse, that is an arbitrary action on your desktop."""
    monkeypatch.setattr(vision, "find_on_screen", lambda q, exact=False: [])
    out = vision.click_on("Submit")
    assert "Could not find" in out
    assert no_clicks == [], "clicked despite finding nothing"


def test_an_ocr_error_result_is_not_treated_as_a_match(monkeypatch, no_clicks):
    """find_on_screen reports failure as [{'error': ...}] rather than raising.
    Indexing that as a match would read center_x off an error dict."""
    monkeypatch.setattr(vision, "find_on_screen",
                        lambda q, exact=False: [{"error": "tesseract missing"}])
    out = vision.click_on("Submit")
    assert "Could not find" in out
    assert no_clicks == []


def test_an_out_of_range_occurrence_does_not_click(monkeypatch, no_clicks):
    monkeypatch.setattr(vision, "find_on_screen", lambda q, exact=False: [_match()])
    out = vision.click_on("Submit", occurrence=5)
    assert "out of range" in out
    assert no_clicks == []


# ── and it does click when it should ──────────────────────────────────────────

def test_a_match_clicks_its_centre(monkeypatch, no_clicks):
    monkeypatch.setattr(vision, "find_on_screen",
                        lambda q, exact=False: [_match(300, 400)])
    out = vision.click_on("Submit")
    assert no_clicks == [(300, 400)]
    assert "Clicked" in out and "300" in out


def test_occurrence_selects_the_nth_match(monkeypatch, no_clicks):
    monkeypatch.setattr(vision, "find_on_screen", lambda q, exact=False: [
        _match(10, 10), _match(20, 20), _match(30, 30)])
    vision.click_on("Submit", occurrence=2)
    assert no_clicks == [(30, 30)]


def test_the_reply_says_which_match_it_took(monkeypatch, no_clicks):
    """The model chooses `occurrence` on the next turn from this string."""
    monkeypatch.setattr(vision, "find_on_screen", lambda q, exact=False: [
        _match(10, 10), _match(20, 20)])
    out = vision.click_on("Submit")
    assert "1/2" in out


# ── the tools are actually offered ────────────────────────────────────────────

def test_screen_vision_tools_are_exposed_to_the_model(monkeypatch):
    import config
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    from agent.core import AgentCore
    names = {t["name"] for t in AgentCore()._all_tools()}
    for tool in ("find_on_screen", "click_on_screen", "annotate_screen"):
        assert tool in names or any(tool.split("_")[0] in n for n in names), \
            f"no screen-vision tool resembling {tool} is offered"


def test_missing_ocr_is_reported_not_raised(monkeypatch):
    """_check_ocr gates everything. Without tesseract these tools must explain
    themselves rather than raise into the turn."""
    monkeypatch.setattr(vision, "_check_ocr", lambda: False)
    out = vision.find_on_screen("anything")
    assert isinstance(out, list) and out and "error" in out[0]
