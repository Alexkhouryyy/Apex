"""Start-up steps that every way of running Apex needs.

main.py (interactive) and app/resident.py (the always-on mode the owner's
launchers run) each used to carry their own copy of start-up. Resident mode's
copy fell behind: sub-agents were never switched on, the weekly review,
nightly reflection and morning briefing were never scheduled, the skill
curator never ran, Python skills were never loaded, and the Guardian and Time
Capsule panels stayed empty. Both now call these, so they can't drift apart.

Each step is independent: one failing is logged and the rest still run.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

Log = Callable[[str], None]

WEEKLY_REVIEW = "Weekly self-evaluation"
NIGHTLY_REFLECTION = "Nightly reflection"


def _step(name: str, log: Log, fn: Callable[[], object]):
    try:
        return fn()
    except Exception as exc:
        log(f"[Startup] {name} skipped: {exc}")
        return None


def wire_orchestrator() -> None:
    """Sub-agents (spawn_subagent, run_plan, run_dependency_plan) get a fresh
    agent each, with no shared memory."""
    from agent import orchestrator

    def factory():
        from agent.core import AgentCore
        return AgentCore()
    orchestrator.set_agent_factory(factory)


def load_skills(log: Log) -> None:
    from agent import skill_md, skills
    bundled = skill_md.install_bundled()
    if bundled:
        log(f"[Skills] Installed {bundled} bundled procedural skill(s).")
    loaded = skills.load_all()
    if loaded:
        log(f"[Skills] Loaded {loaded} skill(s).")


def init_vault_and_constellation(log: Log) -> None:
    from agent import constellation, vault
    log(f"[Vault] {vault.init_vault()}")
    log(f"[Constellation] {constellation.init()}")


def install_standing_tasks(log: Log) -> None:
    """The weekly review, nightly reflection and morning briefing. Each is
    scheduled once; the briefing is re-registered from its saved settings."""
    from agent import briefing, scheduler
    existing = [t.get("description", "") for t in scheduler.list_tasks()]
    if not any(WEEKLY_REVIEW in d for d in existing):
        scheduler.schedule(
            description=(f"{WEEKLY_REVIEW}. Call evaluate_recent_work(days=7) and speak the result. "
                         "Then suggest one concrete focus for the coming week."),
            trigger_type="cron", trigger_params={"day_of_week": "mon", "hour": 8, "minute": 0})
    if not any(NIGHTLY_REFLECTION in d for d in existing):
        scheduler.schedule(
            description=(f"{NIGHTLY_REFLECTION}. Call reflect_now(hours=24). Then call "
                         "list_reflections(status='pending') and briefly mention how many insights "
                         "are awaiting review on the dashboard."),
            trigger_type="cron", trigger_params={"hour": 3, "minute": 0})
    result = briefing.install_briefing_task()
    if "scheduled" in result:
        log(f"[Briefing] {result}")


def start_curator(agent, log: Log, *, interval: float = 3600) -> threading.Thread | None:
    """Hourly check; the curator itself decides when a weekly run is due."""
    import config
    if not getattr(config, "CURATOR_ENABLED", True):
        return None
    last_activity = {"ts": time.time()}

    def tick():
        while True:
            time.sleep(interval)
            try:
                from agent import curator
                if curator.should_run(last_active_ts=last_activity["ts"]):
                    log("[Curator] Running scheduled curation...")
                    report = curator.run(dry_run=False, client=agent.anthropic)
                    log(f"[Curator] Done. {len(report.splitlines())} report lines.")
            except Exception as exc:
                log(f"[Curator] Error: {exc}")
    thread = threading.Thread(target=tick, daemon=True, name="SkillCurator")
    thread.start()
    return thread


def attach_monitor_panels(dash, monitor) -> None:
    """The Guardian and Time Capsule dashboard panels read from the awareness
    monitor; without this they stay empty."""
    if monitor is None:
        return
    if getattr(monitor, "guardian", None) is not None:
        dash.set_guardian(monitor.guardian)
    if getattr(monitor, "timecapsule", None) is not None:
        dash.set_timecapsule(monitor.timecapsule)


def run_common(agent, log: Log) -> None:
    """Every step above that doesn't need the dashboard, each on its own."""
    _step("Sub-agents", log, wire_orchestrator)
    _step("Skills", log, lambda: load_skills(log))
    _step("Vault and Constellation", log, lambda: init_vault_and_constellation(log))
    _step("Standing tasks", log, lambda: install_standing_tasks(log))
    _step("Skill curator", log, lambda: start_curator(agent, log))
