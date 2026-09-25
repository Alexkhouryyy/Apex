"""Look now: a hotkey or "Hey Celly" makes Celine look at your screen and help.

Press the hotkey (CELINE_HOTKEY, default Ctrl+Alt+C) or say the wake phrase,
and this captures the screen the mouse is on, at that moment, on the laptop.
The open companion page is waiting on /api/companion/look; it starts a turn
that refers to the capture by id, and the server attaches the image itself —
the screenshot never travels through the browser.

Words said after the wake phrase ("hey celly, why is this failing?") become
the question. With none, the question is DEFAULT_QUESTION.

The companion page speaks the answer, so it has to be open: if no page has
asked for work recently, the request says so rather than vanishing.
"""
from __future__ import annotations

import base64
import io
import threading
import time
from typing import Optional

DEFAULT_QUESTION = ("Look at my screen right now and help me with what I'm doing. "
                    "Start with what you see, then the most useful next step.")
MAX_SIDE = 1920             # the companion's screen-image limit
KEEP_SECONDS = 120          # a capture nobody used is dropped after this
PAGE_FRESH_SECONDS = 40     # a page that polled this recently is listening

_cond = threading.Condition()
_items: list[dict] = []     # {seq, ts, source, question, image}
_seq = 0
_last_poll = 0.0


def _monitor_under_cursor(monitors):
    """The mss monitor the mouse is on, or the primary. Best effort: on a
    machine without pynput, or headless, the primary monitor is right anyway."""
    try:
        from pynput.mouse import Controller
        x, y = Controller().position
        for m in monitors[1:]:
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                return m
    except Exception:
        pass
    return monitors[1]


def capture() -> str:
    """The screen the mouse is on, as a JPEG data URL the companion accepts."""
    import mss
    from PIL import Image
    with mss.mss() as sct:
        raw = sct.grab(_monitor_under_cursor(sct.monitors))
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return encode(img)


def encode(img) -> str:
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def question_from(transcript: str, phrases: list[str]) -> str:
    """What was said after the wake phrase, or '' if nothing was."""
    text = (transcript or "").strip()
    low = text.lower().lstrip(" ,.!?:;-\"'")
    offset = len(text) - len(low)
    for phrase in sorted((p.lower() for p in phrases), key=len, reverse=True):
        if phrase and low.startswith(phrase):
            rest = text[offset + len(phrase):]
            return rest.strip(" ,.!?:;-\"'").strip()
    return ""


def page_listening(now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    return now - _last_poll < PAGE_FRESH_SECONDS


def request(source: str, question: str = "", image: Optional[str] = None,
            now: Optional[float] = None) -> dict:
    """Capture the screen and queue it for the companion page. Returns the
    item without its image, plus whether a page is there to take it."""
    global _seq
    now = now if now is not None else time.time()
    image = image if image is not None else capture()
    with _cond:
        _seq += 1
        item = {"seq": _seq, "ts": now, "source": source,
                "question": (question or "").strip()[:2000], "image": image}
        _items[:] = [i for i in _items if now - i["ts"] < KEEP_SECONDS][-4:] + [item]
        _cond.notify_all()
    return {**_public(item), "page_listening": page_listening(now)}


def _public(item: dict) -> dict:
    return {k: item[k] for k in ("seq", "ts", "source", "question")}


def wait(after: int, timeout: float = 25.0) -> Optional[dict]:
    """The first request newer than `after`, waiting up to `timeout` seconds.
    Also records that a page is listening."""
    global _last_poll
    deadline = time.time() + timeout
    with _cond:
        while True:
            _last_poll = time.time()
            fresh = [i for i in _items if i["seq"] > after and _last_poll - i["ts"] < KEEP_SECONDS]
            if fresh:
                return _public(fresh[0])
            left = deadline - time.time()
            if left <= 0:
                return None
            _cond.wait(left)


def latest_seq() -> int:
    with _cond:
        return _seq


def image_for(seq: int) -> Optional[str]:
    """The captured image for a request — each can be used once."""
    with _cond:
        for i, item in enumerate(_items):
            if item["seq"] == seq:
                _items.pop(i)
                return item["image"]
    return None


def reset() -> None:
    """For tests."""
    global _seq, _last_poll
    with _cond:
        _items.clear()
        _seq = 0
        _last_poll = 0.0


# --- the two triggers ------------------------------------------------------

def _announce(result: dict) -> None:
    what = f" — \"{result['question']}\"" if result["question"] else ""
    if result["page_listening"]:
        print(f"[Celine] Looking at your screen ({result['source']}){what}", flush=True)
    else:
        print("[Celine] Captured your screen, but no companion page is open to answer. "
              "Open http://127.0.0.1:7860/companion — it will pick this up for the next "
              "two minutes.", flush=True)


def on_hotkey() -> None:
    try:
        _announce(request("hotkey"))
    except Exception as e:
        print(f"[Celine] Could not capture the screen: {type(e).__name__}: {e}", flush=True)


def make_wake_handler(phrases: list[str]):
    def on_wake(transcript: str = "") -> None:
        try:
            _announce(request("wake", question=question_from(transcript, phrases)))
        except Exception as e:
            print(f"[Celine] Could not capture the screen: {type(e).__name__}: {e}", flush=True)
    return on_wake


def start(hotkey: str = "", wake_phrases: Optional[list[str]] = None) -> list[str]:
    """Bind the hotkey and start the wake listener. Returns what started, in
    words, for the console."""
    started = []
    if hotkey:
        try:
            from app.hotkey import HotkeyManager
            mgr = HotkeyManager()
            mgr.bind(hotkey, on_hotkey)
            if mgr.start():
                started.append(f"hotkey {hotkey}")
        except Exception as e:
            print(f"[Celine] Hotkey not available: {e}", flush=True)
    if wake_phrases:
        try:
            from voice.wake import WakeWordListener
            WakeWordListener(wake_phrases=wake_phrases).start(on_wake=make_wake_handler(wake_phrases))
            started.append(f"wake phrase \"{wake_phrases[0]}\"")
        except Exception as e:
            print(f"[Celine] Wake phrase not available: {e}", flush=True)
    return started

