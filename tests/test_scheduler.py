"""Tests for scheduler missed-fire (downtime catch-up) detection.

Skipped automatically where APScheduler isn't installed (e.g. the minimal CI
sandbox); runs fully in the real environment.
"""
from datetime import datetime, timezone, timedelta

import pytest

pytest.importorskip("apscheduler")

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from agent.scheduler import _fire_was_missed

NOW = datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc)


def test_cron_daily_missed_during_downtime():
    # Daily 08:00; last ran yesterday 09:00 → today's 08:00 fire was missed.
    cron = CronTrigger(hour=8, minute=0, timezone="UTC")
    baseline = (NOW - timedelta(days=1)).timestamp()
    assert _fire_was_missed(cron, baseline, NOW) is True


def test_cron_daily_not_missed_when_recent():
    # Daily 08:00; last ran today 08:30 → next is tomorrow 08:00, nothing missed.
    cron = CronTrigger(hour=8, minute=0, timezone="UTC")
    baseline = NOW.replace(hour=8, minute=30).timestamp()
    assert _fire_was_missed(cron, baseline, NOW) is False


def test_date_in_past_never_ran_is_missed():
    past = DateTrigger(run_date=NOW - timedelta(hours=2), timezone="UTC")
    baseline = (NOW - timedelta(days=2)).timestamp()
    assert _fire_was_missed(past, baseline, NOW) is True


def test_date_in_future_not_missed():
    fut = DateTrigger(run_date=NOW + timedelta(hours=2), timezone="UTC")
    assert _fire_was_missed(fut, None, NOW) is False


def test_interval_missed_when_overdue():
    # Every hour; last ran 3h ago → at least one fire came due during downtime.
    interval = IntervalTrigger(hours=1)
    baseline = (NOW - timedelta(hours=3)).timestamp()
    assert _fire_was_missed(interval, baseline, NOW) is True


def test_interval_not_missed_when_recent():
    """Ran 10 minutes ago on an hourly interval — nothing missed."""
    interval = IntervalTrigger(hours=1)
    baseline = (NOW - timedelta(minutes=10)).timestamp()
    assert _fire_was_missed(interval, baseline, NOW) is False


def test_interval_uses_baseline_not_trigger_construction_time():
    """Regression: IntervalTrigger defaults start_date to when it was CONSTRUCTED.

    Relying on get_next_fire_time() therefore measured from 'now' rather than
    from the last run, so an overdue hourly task never looked missed and was
    never caught up. This asserts the baseline drives the decision.
    """
    interval = IntervalTrigger(hours=1)          # start_date = real now, far from NOW
    long_ago = (NOW - timedelta(days=30)).timestamp()
    assert _fire_was_missed(interval, long_ago, NOW) is True


def test_none_baseline_does_not_crash():
    cron = CronTrigger(hour=8, minute=0, timezone="UTC")
    # No baseline → uses now as the reference; must not raise.
    assert _fire_was_missed(cron, None, NOW) in (True, False)
