"""Persistent task scheduler — lets the agent schedule recurring and one-off autonomous actions.

Tasks are stored in SQLite and survive restarts. APScheduler fires them; each
execution runs the agent with the stored prompt and speaks the result aloud.
"""
import json
import time
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from agent import longterm

# How long after a scheduled time a fire may still run (e.g. the host was briefly
# busy). Across a full restart we additionally do explicit catch-up in _restore_tasks.
_MISFIRE_GRACE_SECONDS = 3600

_scheduler: Optional[BackgroundScheduler] = None
_agent_run_fn: Optional[Callable] = None
_speak_fn: Optional[Callable] = None


def init(agent_run_fn: Callable, speak_fn: Callable) -> None:
    """Wire up and start the scheduler. Call once at startup."""
    global _scheduler, _agent_run_fn, _speak_fn
    _agent_run_fn = agent_run_fn
    _speak_fn = speak_fn

    # Ensure the tasks table exists
    import sqlite3
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                trigger_type TEXT NOT NULL,   -- cron / interval / date
                trigger_params TEXT NOT NULL, -- JSON
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                last_run REAL,
                run_count INTEGER DEFAULT 0
            )
        """)

    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "misfire_grace_time": _MISFIRE_GRACE_SECONDS},
    )
    _scheduler.start()
    _restore_tasks()
    print(f"[Scheduler] Started. {len(list_tasks())} tasks loaded.")


def schedule(
    description: str,
    trigger_type: str,
    trigger_params: dict,
) -> str:
    """
    Schedule an autonomous agent task.

    trigger_type: 'cron' | 'interval' | 'date'
    trigger_params examples:
      cron:     {"hour": 8, "minute": 0}            — daily at 8am UTC
      cron:     {"day_of_week": "mon-fri", "hour": 9}
      interval: {"hours": 1}                         — every hour
      interval: {"minutes": 30}
      date:     {"run_date": "2026-06-01 09:00:00"}
    """
    if _scheduler is None:
        return "Scheduler not initialised."

    task_id = f"task_{int(time.time() * 1000)}"
    trigger = _make_trigger(trigger_type, trigger_params)
    if trigger is None:
        return f"Unknown trigger_type: {trigger_type!r}. Use cron, interval, or date."

    _scheduler.add_job(
        _fire_task,
        trigger=trigger,
        id=task_id,
        args=[task_id, description],
        replace_existing=True,
    )

    with longterm._conn() as c:
        c.execute(
            """INSERT INTO scheduled_tasks
               (id, description, trigger_type, trigger_params, enabled, created_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (task_id, description, trigger_type, json.dumps(trigger_params), time.time()),
        )

    return f"Scheduled task {task_id!r}: {description} [{trigger_type}: {trigger_params}]"


def cancel(task_id: str) -> str:
    if _scheduler is None:
        return "Scheduler not initialised."
    try:
        _scheduler.remove_job(task_id)
    except Exception:
        pass
    with longterm._conn() as c:
        c.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
    return f"Cancelled task {task_id!r}"


def list_tasks() -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, description, trigger_type, trigger_params, enabled, last_run, run_count, created_at FROM scheduled_tasks"
        ).fetchall()
    return [
        {
            "id": r[0], "description": r[1], "trigger_type": r[2],
            "trigger_params": json.loads(r[3]), "enabled": bool(r[4]),
            "last_run": r[5], "run_count": r[6], "created_at": r[7],
        }
        for r in rows
    ]


def _fire_task(task_id: str, description: str) -> None:
    print(f"[Scheduler] Firing task {task_id!r}: {description}")
    with longterm._conn() as c:
        c.execute(
            "UPDATE scheduled_tasks SET last_run = ?, run_count = run_count + 1 WHERE id = ?",
            (time.time(), task_id),
        )
    try:
        response = _agent_run_fn(
            description,
            include_screenshot=True,
            use_thinking=False,
            channel_id=f"scheduler:{task_id}",
        )
        if _speak_fn and response:
            _speak_fn(response)
        # Cross-device reach: a scheduled task firing while you're away from the
        # desktop should still reach your phone.
        if response:
            try:
                from agent import notify as _notify
                is_briefing = description.lstrip().startswith("[BRIEFING]")
                title = "Morning briefing" if is_briefing else "Scheduled task"
                kind = "briefing" if is_briefing else "schedule"
                url = "/?tab=briefing" if is_briefing else "/?tab=schedule"
                _notify.notify(title, response[:300], kind=kind,
                               priority="normal", url=url,
                               dedup_key=f"task:{task_id}:{int(time.time() // 60)}")
            except Exception:
                pass
    except Exception as e:
        print(f"[Scheduler] Task {task_id!r} failed: {e}")


def _make_trigger(trigger_type: str, params: dict):
    try:
        if trigger_type == "cron":
            return CronTrigger(**params, timezone="UTC")
        elif trigger_type == "interval":
            return IntervalTrigger(**params)
        elif trigger_type == "date":
            return DateTrigger(**params)
    except Exception as e:
        print(f"[Scheduler] Bad trigger params: {e}")
    return None


def _fire_was_missed(trigger, baseline_ts: Optional[float], now_dt: datetime) -> bool:
    """True if a scheduled fire came due between ``baseline_ts`` and now.

    The in-memory jobstore recomputes next_run_time fresh on restart, so a fire
    that was due while the process was down would otherwise be silently skipped.
    We detect it from the persisted ``last_run`` (or ``created_at``) baseline.
    """
    try:
        baseline = (
            datetime.fromtimestamp(baseline_ts, tz=timezone.utc)
            if baseline_ts else None
        )
        if baseline is None:
            return False  # no last_run and no created_at — nothing to catch up from

        # IntervalTrigger defaults start_date to CONSTRUCTION time, so on restart
        # get_next_fire_time() measures from "now" rather than from the last run
        # and an overdue interval task never looks missed. Compute it from the
        # baseline directly instead. (Cron/date triggers are absolute, so their
        # get_next_fire_time is already correct relative to the baseline.)
        interval = getattr(trigger, "interval", None)
        if interval is not None:
            return (baseline + interval) <= now_dt

        # Next fire strictly after the baseline; if it's already <= now, we missed it.
        nxt = trigger.get_next_fire_time(None, baseline)
        return nxt is not None and nxt <= now_dt
    except Exception:
        return False


def _restore_tasks() -> None:
    now_dt = datetime.now(timezone.utc)
    for task in list_tasks():
        if not task["enabled"]:
            continue
        trigger = _make_trigger(task["trigger_type"], task["trigger_params"])
        if not trigger:
            continue
        # Catch-up: if a fire came due while we were down, run it once now (coalesced).
        baseline = task.get("last_run") or task.get("created_at")
        did_catchup = False
        if _fire_was_missed(trigger, baseline, now_dt):
            try:
                _scheduler.add_job(
                    _fire_task,
                    trigger="date",
                    run_date=now_dt,
                    id=f"{task['id']}_catchup",
                    args=[task["id"], task["description"]],
                    replace_existing=True,
                    misfire_grace_time=_MISFIRE_GRACE_SECONDS,
                )
                did_catchup = True
                print(f"[Scheduler] Catch-up scheduled for missed task {task['id']!r}.")
            except Exception as e:
                print(f"[Scheduler] Catch-up failed for {task['id']}: {e}")
        # A one-off 'date' task that already missed is fully satisfied by the catch-up;
        # re-adding the past-date job would double-fire it.
        if task["trigger_type"] == "date" and did_catchup:
            continue
        # Normal future schedule.
        try:
            _scheduler.add_job(
                _fire_task,
                trigger=trigger,
                id=task["id"],
                args=[task["id"], task["description"]],
                replace_existing=True,
            )
        except Exception as e:
            print(f"[Scheduler] Could not restore {task['id']}: {e}")
