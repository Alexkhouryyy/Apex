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
        # Whether the reply was spoken as it was written (companion setting
        # "Speak as it writes") — the before/after the report compares. Added
        # after the table first shipped, so a migration.
        cols = {r[1] for r in c.execute("PRAGMA table_info(voice_timing)")}
        if "streamed" not in cols:
            c.execute("ALTER TABLE voice_timing ADD COLUMN streamed INTEGER")


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
    streamed = turn.get("streamed")
    streamed = None if streamed is None else (1 if streamed is True else 0 if streamed is False else None)
    init_db()
    from agent import longterm
    with longterm._conn() as c:
        c.execute("INSERT INTO voice_timing (ts, mode, voice, stages, streamed)"
                  " VALUES (?,?,?,?,?)",
                  (time.time(), mode, voice, json.dumps(stages), streamed))
        c.execute("DELETE FROM voice_timing WHERE id NOT IN "
                  "(SELECT id FROM voice_timing ORDER BY id DESC LIMIT ?)", (KEEP,))
    return stages


def recent(limit: int = 20, streamed: Optional[bool] = None) -> list[dict]:
    """The last `limit` turns — of one mode only when `streamed` is given."""
    init_db()
    from agent import longterm
    where, args = "", [int(limit)]
    if streamed is not None:
        where, args = "WHERE streamed = ? ", [1 if streamed else 0, int(limit)]
    with longterm._conn() as c:
        rows = c.execute("SELECT ts, mode, voice, stages, streamed FROM voice_timing "
                         + where + "ORDER BY id DESC LIMIT ?", args).fetchall()
    return [{"ts": r[0], "mode": r[1], "voice": r[2], "stages": json.loads(r[3]),
             "streamed": None if r[4] is None else bool(r[4])} for r in rows]


def _pct(values: list, p: float) -> Optional[float]:
    """Nearest-rank percentile. None for no data — never a made-up zero."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, math.ceil(p / 100 * len(s)) - 1)
    return s[k]


def summary(limit: int = 20, streamed: Optional[bool] = None) -> dict:
    """Median and 90th percentile per stage over the last `limit` turns, and
    whether Pillar 1's check passes. `verdict` is three-state: too few turns
    to judge is `unknown`, never `pass`."""
    turns = recent(limit, streamed)
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


LABELS = {
        "stt_done": "transcript back", "first_token": "first word of reply",
        "reply_done": "whole reply written", "tts_start": "voice requested",
        "tts_ready": "first audio received", "first_sound": "FIRST SOUND",
        "stt_server": "  (server: speech-to-text)", "tts_server": "  (server: first voice section)",
}


def report(limit: int = 20) -> str:
    """The Pillar 1 table — and, once both modes have turns, before and after
    "Speak as it writes" side by side, so the change is a measured number."""
    s = summary(limit)
    lines = [f"Voice turns measured: {s['turns']} (most recent {limit})",
             "", "  stage (since you stopped talking)   median    p90    n"]
    for k in STAGES + SERVER:
        st = s["stages"][k]
        lines.append(f"  {LABELS[k]:<34}{_fmt(st['median'])} {_fmt(st['p90'])} {st['n']:>4}")
    lines += ["", f"Pillar 1: {s['verdict'].upper()} — {s['why']}"]
    before, after = summary(limit, streamed=False), summary(limit, streamed=True)
    if before["turns"] and after["turns"]:
        lines += ["", "  Before / after \"Speak as it writes\" (median, turns counted):",
                  f"  {'':<34}{'whole reply':>12}{'as it writes':>14}"]
        for k in ("reply_done", "tts_ready", "first_sound"):
            b, a = before["stages"][k], after["stages"][k]
            lines.append(f"  {LABELS[k]:<34}{_fmt(b['median']):>12}{_fmt(a['median']):>14}")
        lines.append(f"  {'turns':<34}{before['turns']:>12}{after['turns']:>14}")
        b, a = before["stages"]["first_sound"]["median"], after["stages"]["first_sound"]["median"]
        if b and a:
            lines.append(f"  First sound {'earlier' if a < b else 'LATER'} by "
                         f"{abs(b - a) / 1000:.2f}s with it on.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
