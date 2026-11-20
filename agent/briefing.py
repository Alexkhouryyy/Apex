"""Morning briefing — daily scheduled agent turn that speaks a personalised digest.

Usage:
  from agent import briefing
  briefing.init_db()
  briefing.install_briefing_task()   # call after scheduler.init()
"""
from __future__ import annotations

import json
import time

from agent import longterm

_BRIEFING_MARKER = "[BRIEFING]"

_DEFAULTS: dict[str, str] = {
    "enabled":     "false",
    "time":        "08:00",
    "timezone":    "",          # empty: the PC's own zone (scheduler.local_zone)
    "location":    "",
    "news_topics": "",
}


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS briefing_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)


def get_config() -> dict:
    init_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT key, value FROM briefing_config").fetchall()
    cfg = dict(_DEFAULTS)
    for k, v in rows:
        cfg[k] = v
    if not cfg.get("timezone"):
        from agent import scheduler
        cfg["timezone"] = str(scheduler.local_zone())
    return cfg


def set_config(updates: dict) -> None:
    init_db()
    with longterm._conn() as c:
        for k, v in updates.items():
            c.execute(
                "INSERT OR REPLACE INTO briefing_config (key, value) VALUES (?, ?)",
                (k, str(v)),
            )


def _build_prompt(cfg: dict) -> str:
    location = (cfg.get("location") or "").strip()
    topics   = (cfg.get("news_topics") or "").strip()

    lines = [
        _BRIEFING_MARKER,
        "Deliver my morning briefing. Be warm and concise — the whole thing should "
        "take under 90 seconds to speak aloud.",
        "",
        "Cover these sections in order:",
    ]

    if location:
        lines.append(
            f"1. WEATHER — search 'weather {location} today' or fetch "
            f"https://wttr.in/{location.replace(' ', '+')}?format=3 "
            "and give me a one-sentence summary."
        )
    else:
        lines.append("1. WEATHER — skip (no location configured).")

    if topics:
        lines.append(
            f"2. NEWS — search for today's top headlines about: {topics}. "
            "Give me exactly 3 headlines with one sentence of context each."
        )
    else:
        lines.append(
            "2. NEWS — search for today's top 3 general news headlines. "
            "Give me one sentence of context per headline."
        )

    lines += [
        "3. FOLLOW-UPS — check my memory for any pending reminders or things "
        "I asked you to follow up on. Mention anything due today or overdue.",
        "",
        "Speak it naturally, as if you're talking to me, not reading a report. "
        "Start with 'Good morning' and my name if you know it.",
    ]
    return "\n".join(lines)


def install_briefing_task() -> str:
    """Register (or re-register) the morning briefing cron job.

    Safe to call on every startup — cancels any existing briefing task first.
    Returns a status string.
    """
    from agent import scheduler

    cfg = get_config()
    # Cancel any existing briefing task first, so turning it off really stops it.
    for task in scheduler.list_tasks():
        if task.get("description", "").startswith(_BRIEFING_MARKER):
            scheduler.cancel(task["id"])
    if cfg.get("enabled", "false").lower() not in {"1", "true", "yes"}:
        return "Morning briefing disabled."

    time_str = cfg.get("time", "08:00")
    tz       = cfg.get("timezone") or str(scheduler.local_zone())
    try:
        hour, minute = (int(x) for x in time_str.split(":"))
    except ValueError:
        hour, minute = 8, 0

    prompt = _build_prompt(cfg)
    result = scheduler.schedule(
        description=prompt,
        trigger_type="cron",
        trigger_params={"hour": hour, "minute": minute, "timezone": tz},
    )
    return f"Morning briefing scheduled daily at {time_str} {tz}. ({result})"


def reinstall(updates: dict | None = None) -> str:
    """Apply config updates (if any) then reinstall the cron task."""
    if updates:
        set_config(updates)
    return install_briefing_task()
