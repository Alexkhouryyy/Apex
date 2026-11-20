"""Resident mode (the owner's launchers) runs the same start-up as main.py.

It used to skip most of it: sub-agents answered "Orchestrator not
initialised", the weekly review, nightly reflection and morning briefing were
never scheduled, the skill curator never ran, Python skills never loaded, and
the Guardian and Time Capsule panels stayed empty. The morning briefing also
could never be scheduled anywhere: it passed a time zone and the scheduler
added timezone="UTC" again.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("apscheduler")

from agent import briefing, orchestrator, scheduler, startup

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def live_scheduler(test_db, monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    scheduler.init(agent_run_fn=lambda *a, **k: "", speak_fn=lambda text: None)
    yield scheduler
    scheduler._scheduler.shutdown(wait=False)


def test_both_launch_modes_run_the_same_start_up():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    resident = (ROOT / "app" / "resident.py").read_text(encoding="utf-8")
    for source in (main, resident):
        assert "_startup.run_common(agent, " in source
        assert "_startup.attach_monitor_panels(dash, " in source
    assert "set_agent_factory" not in main and "install_briefing_task" not in main, "one copy, in agent/startup.py"


def test_sub_agents_are_switched_on(monkeypatch):
    monkeypatch.setattr(orchestrator, "_agent_factory", None)
    startup.wire_orchestrator()
    assert orchestrator._agent_factory is not None


def test_the_weekly_review_and_nightly_reflection_are_scheduled_once(live_scheduler, monkeypatch):
    monkeypatch.setattr(briefing, "install_briefing_task", lambda: "Morning briefing disabled.")
    startup.install_standing_tasks(print)
    startup.install_standing_tasks(print)
    descriptions = [t["description"] for t in live_scheduler.list_tasks()]
    assert sum(startup.WEEKLY_REVIEW in d for d in descriptions) == 1
    assert sum(startup.NIGHTLY_REFLECTION in d for d in descriptions) == 1


def test_the_morning_briefing_can_be_scheduled(live_scheduler):
    briefing.set_config({"enabled": "true", "time": "07:30", "timezone": "Asia/Beirut"})
    result = briefing.install_briefing_task()
    assert "scheduled daily at 07:30 Asia/Beirut" in result
    task = next(t for t in live_scheduler.list_tasks() if t["description"].startswith("[BRIEFING]"))
    job = live_scheduler._scheduler.get_job(task["id"])
    assert str(job.trigger.timezone) == "Asia/Beirut"


def test_turning_the_briefing_off_removes_the_scheduled_one(live_scheduler):
    briefing.set_config({"enabled": "true"})
    briefing.install_briefing_task()
    briefing.set_config({"enabled": "false"})
    assert briefing.install_briefing_task() == "Morning briefing disabled."
    assert not [t for t in live_scheduler.list_tasks() if t["description"].startswith("[BRIEFING]")]


def test_schedules_use_the_pc_s_local_time(monkeypatch):
    monkeypatch.setattr(scheduler, "local_zone", lambda: "Asia/Beirut")
    trigger = scheduler._make_trigger("cron", {"hour": 8, "minute": 0})
    assert str(trigger.timezone) == "Asia/Beirut", "'every day at 8' means 8 on the owner's clock, not UTC"
    assert str(scheduler._make_trigger("cron", {"hour": 8, "timezone": "UTC"}).timezone) == "UTC"


def test_the_briefing_defaults_to_the_pc_s_zone(test_db, monkeypatch):
    monkeypatch.setattr(scheduler, "local_zone", lambda: "Asia/Beirut")
    assert briefing.get_config()["timezone"] == "Asia/Beirut"


def test_one_failing_step_does_not_stop_the_others(monkeypatch):
    done, logged = [], []
    monkeypatch.setattr(startup, "wire_orchestrator", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(startup, "load_skills", lambda log: done.append("skills"))
    monkeypatch.setattr(startup, "init_vault_and_constellation", lambda log: done.append("vault"))
    monkeypatch.setattr(startup, "install_standing_tasks", lambda log: done.append("tasks"))
    monkeypatch.setattr(startup, "start_curator", lambda agent, log: done.append("curator"))
    startup.run_common(SimpleNamespace(), logged.append)
    assert done == ["skills", "vault", "tasks", "curator"]
    assert logged == ["[Startup] Sub-agents skipped: boom"]


def test_guardian_and_time_capsule_panels_get_the_monitor():
    seen = {}
    dash = SimpleNamespace(set_guardian=lambda g: seen.update(guardian=g),
                           set_timecapsule=lambda t: seen.update(capsule=t))
    startup.attach_monitor_panels(dash, SimpleNamespace(guardian="G", timecapsule="T"))
    assert seen == {"guardian": "G", "capsule": "T"}
    startup.attach_monitor_panels(dash, None)


def test_python_skills_are_loaded(monkeypatch):
    from agent import skill_md, skills
    calls = []
    monkeypatch.setattr(skill_md, "install_bundled", lambda: calls.append("bundled") or 0)
    monkeypatch.setattr(skills, "load_all", lambda: calls.append("python") or 3)
    logged = []
    startup.load_skills(logged.append)
    assert calls == ["bundled", "python"] and logged == ["[Skills] Loaded 3 skill(s)."]
