"""Where the time goes in a voice turn — measured, one row per turn.

Pillar 1 of docs/APEX_V2_PLAN.md: from the moment you stop speaking to the
first sound of Apex's reply, median <= 1.5 s, 90th percentile <= 2.5 s. With
Celine a reply was taking minutes, and nobody could say which stage the
minutes were in. Guessing and "optimising" the wrong stage is the classic way
to spend a week and change nothing, so this measures first.

The browser owns the clock. It is the only place that sees the whole turn —
when you stopped talking, when the transcript came back, when the first word
of the reply arrived, when the first audio actually started playing — so it
timestamps each stage on one `performance.now()` timeline and posts the turn
here. The server adds nothing but storage and the summary.

Every stage is milliseconds since SPEECH END, not since the previous stage,
so a missing stage (a text-only turn has no speech end; a muted turn has no
sound) is simply absent rather than corrupting the stages after it.

    python -m agent.voice_timing          # summary of recent turns
"""
from __future__ import annotations

import json
import math
import time
from typing import Optional

# In turn order. Anything else a client sends is dropped, not stored.
STAGES = (
    "stt_done",        # transcript back from /api/companion/transcribe
    "first_token",     # first word of the reply streamed back
    "reply_done",      # the model finished the whole reply (tools included)
    "tts_start",       # first /api/speak request sent
    "tts_ready",       # first section of audio received
    "first_sound",     # first audio actually playing
)
# Server-side durations the browser read from Server-Timing headers.
SERVER = ("stt_server", "tts_server")
MODES = ("tap", "hands_free")
MAX_MS = 30 * 60 * 1000          # half an hour: anything longer is not a turn
KEEP = 500

# Pillar 1's check, restated here so the summary can say pass/fail itself.
TARGET_MEDIAN_MS = 1500
TARGET_P90_MS = 2500


def init_db() -> None:
    from agent import longterm
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS voice_timing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                mode TEXT NOT NULL,
                voice TEXT NOT NULL DEFAULT '',
                stages TEXT NOT NULL
            )""")


def _clean_ms(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    if not math.isfinite(v) or v < 0 or v > MAX_MS:
        return None
    return round(v, 1)


def record(turn: dict) -> dict:
    """Store one turn. Raises ValueError on anything that is not a turn."""
    if not isinstance(turn, dict):
        raise ValueError("A turn is an object.")
    mode = turn.get("mode")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}.")
    voice = str(turn.get("voice") or "")[:40]
    raw = turn.get("stages")
    if not isinstance(raw, dict):
        raise ValueError("stages must be an object.")
    stages = {k: _clean_ms(raw.get(k)) for k in STAGES + SERVER}
    stages = {k: v for k, v in stages.items() if v is not None}
    if not stages:
        raise ValueError("No usable stage timings.")
    init_db()
    from agent import longterm
    with longterm._conn() as c:
        c.execute("INSERT INTO voice_timing (ts, mode, voice, stages) VALUES (?,?,?,?)",
                  (time.time(), mode, voice, json.dumps(stages)))
        c.execute("DELETE FROM voice_timing WHERE id NOT IN "
                  "(SELECT id FROM voice_timing ORDER BY id DESC LIMIT ?)", (KEEP,))
    return stages


def recent(limit: int = 20) -> list[dict]:
    init_db()
    from agent import longterm
    with longterm._conn() as c:
        rows = c.execute("SELECT ts, mode, voice, stages FROM voice_timing "
                         "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [{"ts": r[0], "mode": r[1], "voice": r[2], "stages": json.loads(r[3])}
            for r in rows]


def _pct(values: list, p: float) -> Optional[float]:
    """Nearest-rank percentile. None for no data — never a made-up zero."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, math.ceil(p / 100 * len(s)) - 1)
    return s[k]


def summary(limit: int = 20) -> dict:
    """Median and 90th percentile per stage over the last `limit` turns, and
    whether Pillar 1's check passes. `verdict` is three-state: too few turns
    to judge is `unknown`, never `pass`."""
    turns = recent(limit)
    out: dict = {"turns": len(turns), "stages": {}}
    for k in STAGES + SERVER:
        vals = [t["stages"][k] for t in turns if k in t["stages"]]
        out["stages"][k] = {"n": len(vals), "median": _pct(vals, 50),
                            "p90": _pct(vals, 90)}
    sound = out["stages"]["first_sound"]
    if sound["n"] < 20:
        out["verdict"] = "unknown"
        out["why"] = (f"{sound['n']} turns reached first sound; the check needs 20 "
                      "real spoken turns.")
    elif sound["median"] <= TARGET_MEDIAN_MS and sound["p90"] <= TARGET_P90_MS:
        out["verdict"] = "pass"
        out["why"] = "median and 90th percentile are within the Pillar 1 targets."
    else:
        out["verdict"] = "fail"
        out["why"] = (f"target is median <= {TARGET_MEDIAN_MS} ms and p90 <= "
                      f"{TARGET_P90_MS} ms to first sound.")
    return out


def _fmt(ms) -> str:
    return "  —  " if ms is None else f"{ms / 1000:5.2f}s"


def report(limit: int = 20) -> str:
    s = summary(limit)
    lines = [f"Voice turns measured: {s['turns']} (most recent {limit})",
             "", "  stage (since you stopped talking)   median    p90    n"]
    labels = {
        "stt_done": "transcript back", "first_token": "first word of reply",
        "reply_done": "whole reply written", "tts_start": "voice requested",
        "tts_ready": "first audio received", "first_sound": "FIRST SOUND",
        "stt_server": "  (server: speech-to-text)", "tts_server": "  (server: first voice section)",
    }
    for k in STAGES + SERVER:
        st = s["stages"][k]
        lines.append(f"  {labels[k]:<34}{_fmt(st['median'])} {_fmt(st['p90'])} {st['n']:>4}")
    lines += ["", f"Pillar 1: {s['verdict'].upper()} — {s['why']}"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
