"""The always-on Work agent: Apex keeps an eye on your Work list and acts.

A loop (one daemon thread in the dashboard process) wakes every minute and,
when you have switched it on:

  brief       once a day at your brief time: what's overdue, due, waiting,
              and what Apex finished, sent to your phone and PC (agent/notify)
  evening     once a day at your evening time, only if something from today
              is still open
  finished    as soon as a task Apex worked on comes back: "finished, review
              it" or "stopped, and why"
  follow-up   a task waiting on someone for 3 days gets one nudge, then
              another every 3 days while it stays waiting
  pick up     if "work on its own" is on: the most urgent task you marked
              "Apex can take this" (+apex) goes to the first plan in your
              order that is free: your Claude plan, then your ChatGPT plan
              (agent/work_engines.py), so it uses your subscriptions, not API
              credits. One task at a time, at most `plan_runs` a day. When a
              plan hits its usage limit, the task goes back untried and the
              next plan takes it; that plan rests for `rest_hours`. API
              credits are only used if you add "api" to the order, and then
              only within the daily spending cap. A task Apex tried and
              couldn't finish is never retried on its own: that's your call.

Off until you turn it on. Every action is logged (work_events), and each daily
or per-task action happens once even across restarts, because what was done
is stored, not remembered in memory.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta

from agent import longterm, work, work_engines

DEFAULTS = {'enabled': False, 'auto_work': False, 'engines': ['claude', 'chatgpt'], 'plan_runs': 8,
            'rest_hours': 5, 'daily_budget': 2.0, 'task_budget': 0.5, 'brief_time': '08:30', 'evening_time': '18:00'}
WAITING_NUDGE_DAYS = 3
TICK_SECONDS = 60
_thread = None
_stop = threading.Event()


def init_db() -> None:
    work.init_db()
    with longterm._conn() as db:
        db.execute('CREATE TABLE IF NOT EXISTS work_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS work_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
            task_id INTEGER, text TEXT NOT NULL, key TEXT UNIQUE)''')


# ---------------------------------------------------------------- settings and log

def settings() -> dict:
    init_db()
    with longterm._conn() as db:
        row = db.execute("SELECT value FROM work_settings WHERE key='agent'").fetchone()
    return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def _hhmm(value, name):
    try:
        h, m = str(value).split(':')
        assert 0 <= int(h) < 24 and 0 <= int(m) < 60
        return f'{int(h):02d}:{int(m):02d}'
    except (ValueError, AssertionError):
        raise work.WorkError(f'{name} must be a time like 08:30.')


def update_settings(**changes) -> dict:
    s = settings()
    for key, value in changes.items():
        if key in ('enabled', 'auto_work'):
            if type(value) is not bool: raise work.WorkError(f'{key} is on or off.')
            s[key] = value
        elif key == 'daily_budget':
            if type(value) not in (int, float) or not 0 <= value <= 50: raise work.WorkError('The daily cap must be between $0 and $50.')
            s[key] = round(float(value), 2)
        elif key == 'task_budget':
            if type(value) not in (int, float) or not 0.05 <= value <= 5: raise work.WorkError('The per-task cap must be between $0.05 and $5.')
            s[key] = round(float(value), 2)
        elif key in ('brief_time', 'evening_time'):
            s[key] = _hhmm(value, 'The brief time' if key == 'brief_time' else 'The evening time')
        elif key == 'engines':
            if not isinstance(value, list) or not value or any(e not in work_engines.ENGINES for e in value) \
                    or len(set(value)) != len(value):
                raise work.WorkError('The order is a list of claude, chatgpt and api, each at most once.')
            s[key] = value
        elif key == 'plan_runs':
            if type(value) is not int or not 0 <= value <= 50: raise work.WorkError('Plan tasks a day must be between 0 and 50.')
            s[key] = value
        elif key == 'rest_hours':
            if type(value) not in (int, float) or not 0.5 <= value <= 168: raise work.WorkError('Rest must be between 0.5 and 168 hours.')
            s[key] = value
        else:
            raise work.WorkError(f'Unknown setting: {key}.')
    with longterm._conn() as db:
        db.execute("INSERT OR REPLACE INTO work_settings VALUES ('agent', ?)", (json.dumps(s),))
    return s


def _state(key='state') -> dict:
    init_db()
    with longterm._conn() as db:
        row = db.execute('SELECT value FROM work_settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else {}


def _save_state(state: dict, key='state') -> None:
    with longterm._conn() as db:
        db.execute('INSERT OR REPLACE INTO work_settings VALUES (?, ?)', (key, json.dumps(state)))


# ---------------------------------------------------------------- plans that need a rest

_limits_lock = threading.Lock()
WHY = {'limited': 'reached its usage limit', 'signed_out': 'is not signed in on this PC', 'missing': 'is not installed on this PC'}


def mark_unavailable(engine: str, why: str, summary: str = '', now: float | None = None) -> None:
    """A plan couldn't take a task: rest it, so the next plan in the order is used.
    Stored apart from the tick's state, because a run's thread calls this."""
    now = now or time.time()
    hours = settings()['rest_hours'] if why == 'limited' else 1
    until = now + hours * 3600
    with _limits_lock:
        limits = _state('limits')
        limits[engine] = {'until': until, 'why': why, 'detail': (summary or '')[:300]}
        _save_state(limits, 'limits')
    name = work_engines.NAMES.get(engine, engine)
    text = f"Your {name} {WHY.get(why, why)}. Apex will use the next one in your order"
    text += f" and try it again after {datetime.fromtimestamp(until).strftime('%H:%M')}." if why == 'limited' else \
            ' until you sign in (run `claude` or `codex` once on this PC).'
    if log('plan', text, key=f'plan:{engine}:{why}:{int(until // 3600)}', now=now):
        _notify('Apex · Work', text)


def clear_limits() -> None:
    with _limits_lock:
        _save_state({}, 'limits')


def available(now: float | None = None) -> dict:
    """engine -> None if Apex can use it now, else why not (a short phrase)."""
    now = now or time.time()
    have, limits = work_engines.installed(), _state('limits')
    out = {}
    for e in work_engines.ENGINES:
        lim = limits.get(e)
        if not have[e]:
            out[e] = WHY['missing']
        elif lim and lim['until'] > now:
            out[e] = f"{WHY.get(lim['why'], lim['why'])}, resting until {datetime.fromtimestamp(lim['until']).strftime('%H:%M')}"
        else:
            out[e] = None
    return out


def plan_runs_today(now: datetime) -> int:
    start = datetime(now.year, now.month, now.day).timestamp()
    with longterm._conn() as db:
        return db.execute('SELECT COUNT(*) FROM work_runs WHERE started >= ?', (start,)).fetchone()[0]


def log(kind, text, task_id=None, key=None, now=None) -> bool:
    """Record an action. With a key, only once ever: returns False if already done."""
    try:
        with longterm._conn() as db:
            db.execute('INSERT INTO work_events (ts, kind, task_id, text, key) VALUES (?,?,?,?,?)',
                       (now or time.time(), kind, task_id, text, key))
        return True
    except Exception as exc:                       # the unique key: already done
        if 'UNIQUE' in str(exc):
            return False
        raise


def events(limit=20) -> list[dict]:
    init_db()
    with longterm._conn() as db:
        rows = db.execute('SELECT ts, kind, task_id, text FROM work_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(ts=r[0], kind=r[1], task_id=r[2], text=r[3]) for r in rows]


def _notify(title, body):
    try:
        from agent import notify
        notify.notify(title, body)
    except Exception as exc:
        print(f'[Work] Could not notify: {exc}')


def spent_today(now: datetime) -> float:
    """What Apex's Work tasks cost today, plus the cap of any still running."""
    from agent import team
    start = datetime(now.year, now.month, now.day).timestamp()
    total = 0.0
    with longterm._conn() as db:
        runs = db.execute("SELECT apex_run, apex_state FROM work_tasks WHERE apex_run LIKE 'work\\_%' ESCAPE '\\'").fetchall()
    for run_id, state in runs:
        run = team.get(run_id)
        if not run or run.get('created', 0) < start:
            continue
        total += run['budget_usd'] if state in ('queued', 'running', 'verifying') else (run.get('cost_usd') or 0)
    return round(total, 4)


# ---------------------------------------------------------------- one tick

def _brief_text(view) -> str:
    lines = []
    for key, label in (('overdue', 'Overdue'), ('today', 'Due today'), ('review', 'Apex finished, review'),
                       ('apex_working', 'Apex is working on'), ('waiting', 'Waiting on others')):
        if view[key]:
            lines.append(f"{label}: " + ', '.join(t['title'] for t in view[key][:5]) + ('…' if len(view[key]) > 5 else ''))
    if view['week']:
        lines.append(f"This week: {len(view['week'])} more")
    return '\n'.join(lines)


def _due(now: datetime, hhmm: str, state: dict, key: str) -> bool:
    """True once a day, at or after hh:mm."""
    h, m = map(int, hhmm.split(':'))
    return (now.hour, now.minute) >= (h, m) and state.get(key) != now.date().isoformat()


def pick(view_tasks) -> dict | None:
    """The task Apex takes next: marked for it, never tried, most urgent first."""
    ready = [t for t in view_tasks if t.get('apex_ok') and t['status'] in ('todo', 'doing') and not t['apex_state']]
    ready.sort(key=lambda t: (t['due'] is None, t['due'] or '', t['priority'], t['created']))
    return ready[0] if ready else None


def tick(now: datetime | None = None, agent=None) -> list[str]:
    """One look at the Work list. Returns what it did (for tests and the log)."""
    init_db()
    s = settings()
    if not s['enabled']:
        return []
    now = now or datetime.now()
    done, state = [], _state()

    # Apex's work that came back since the last look.
    work.sync_apex()
    with longterm._conn() as db:
        back = db.execute("SELECT id, title, apex_run, apex_state, apex_summary FROM work_tasks "
                          "WHERE apex_run IS NOT NULL AND apex_state NOT IN ('queued','running','verifying')").fetchall()
    for tid, title, run_id, apex_state, summary in back:
        if apex_state == 'done':
            text = f'Apex finished "{title}". Review it in Work.'
        else:
            text = f'Apex stopped on "{title}" ({apex_state}): {(summary or "")[:160]}'
        if log('finished' if apex_state == 'done' else 'stopped', text, tid, key=f'back:{run_id}', now=now.timestamp()):
            _notify('Apex · Work', text); done.append(text)

    view = work.today_view(now.date())
    if _due(now, s['brief_time'], state, 'brief'):
        body = _brief_text(view) or 'Nothing overdue or due, and nothing waiting. A clear day.'
        _notify('Apex · Today', body); log('brief', 'Morning brief sent.\n' + body, now=now.timestamp())
        state['brief'] = now.date().isoformat(); done.append('brief')
    if _due(now, s['evening_time'], state, 'evening'):
        state['evening'] = now.date().isoformat()
        left = view['overdue'] + view['today']
        if left:
            body = 'Still open from today: ' + ', '.join(t['title'] for t in left[:6])
            _notify('Apex · Work', body); log('evening', body, now=now.timestamp()); done.append('evening')

    # Waiting on someone for a while: one nudge, then one every few days while it stays waiting.
    for t in view['waiting']:
        days = (now.timestamp() - t['updated']) / 86400
        if days >= WAITING_NUDGE_DAYS:
            period = int(days // WAITING_NUDGE_DAYS)
            who = f" on {t['waiting_on']}" if t['waiting_on'] else ''
            text = f'Still waiting{who} for "{t["title"]}"? It has been {int(days)} days.'
            if log('follow-up', text, t['id'], key=f"wait:{t['id']}:{int(t['updated'])}:{period}", now=now.timestamp()):
                _notify('Apex · Work', text); done.append(text)

    # Work on its own: one task at a time, on the first plan that is free.
    if s['auto_work'] and agent is not None:
        result = _auto_work(s, now, agent)
        if result:
            done.append(result)
    _save_state(state)
    return done


def choose_engine(s: dict, now: datetime) -> tuple[str | None, str]:
    """(engine, '') for the first engine in the order Apex can use now, else (None, why not)."""
    from agent import team
    free, reasons = available(now.timestamp()), []
    for e in s['engines']:
        name = work_engines.NAMES[e]
        if e == 'api':
            spent = spent_today(now)
            if team._active:
                reasons.append(f'{name}: the task runner is busy')
            elif spent + s['task_budget'] > s['daily_budget']:
                reasons.append(f"{name}: today's cap is used (${spent:.2f} of ${s['daily_budget']:.2f})")
            else:
                return e, ''
        elif free[e]:
            reasons.append(f'{name} {free[e]}')
        elif plan_runs_today(now) >= s['plan_runs']:
            reasons.append(f"{name}: {s['plan_runs']} plan tasks today already")
        else:
            return e, ''
    return None, '; '.join(reasons)


def _auto_work(s: dict, now: datetime, agent) -> str | None:
    tasks = work.list_tasks()
    if any(t['apex_state'] in ('queued', 'running', 'verifying') for t in tasks):
        return None                                  # one at a time
    choice = pick([t for t in tasks if t['status'] != 'done'])
    if not choice:
        return None
    engine, why = choose_engine(s, now)
    if not engine:
        text = f'Apex is holding "{choice["title"]}": no plan is free. {why}.'
        return 'waiting' if log('waiting', text, choice['id'], key=f'hold:{choice["id"]}:{why}', now=now.timestamp()) else None
    try:
        work.give_to_apex(choice['id'], agent, s['task_budget'], engine=engine)
    except work.WorkError as exc:
        if engine != 'api':                          # e.g. signed out since the check: rest it, try the next one
            mark_unavailable(engine, 'missing', str(exc), now.timestamp())
        log('skipped', f'Could not start "{choice["title"]}": {exc}', choice['id'],
            key=f"skip:{choice['id']}:{engine}:{now.date().isoformat()}", now=now.timestamp())
        return None
    cost = f" (cap ${s['task_budget']:.2f})" if engine == 'api' else ''
    text = f'Apex picked up "{choice["title"]}" on your {work_engines.NAMES[engine]}{cost}.'
    log('picked', text, choice['id'], now=now.timestamp())
    return text


# ---------------------------------------------------------------- the loop

def start(agent) -> None:
    """Called once when the dashboard has its agent."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def loop():
        while not _stop.wait(TICK_SECONDS):
            try:
                tick(agent=agent)
            except Exception as exc:                 # one bad tick must not end the agent
                print(f'[Work] Always-on tick failed: {exc}')
    _thread = threading.Thread(target=loop, daemon=True, name='ApexWorkAgent')
    _thread.start()


def stop() -> None:
    _stop.set()


def status(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    s = settings()
    free = available(now.timestamp())
    return {**s, 'running': bool(_thread and _thread.is_alive()), 'spent_today': spent_today(now),
            'plan_runs_today': plan_runs_today(now),
            'plans': [{'id': e, 'name': work_engines.NAMES[e], 'installed': e == 'api' or bool(work_engines.binary(e)),
                       'unavailable': free[e]} for e in work_engines.ENGINES],
            'eligible': sum(1 for t in work.list_tasks() if t.get('apex_ok') and t['status'] != 'done' and not t['apex_state']),
            'events': events()}


def describe() -> str:
    """The always-on agent in a few speakable lines (the `work` tool's action=agent)."""
    st = status()
    if not st['enabled']:
        return 'The always-on Work agent is off. Turn it on in the Work page (Always on).'
    order = ' then '.join(work_engines.NAMES[e] for e in st['engines'])
    lines = [f"Always on. Brief at {st['brief_time']}, evening check at {st['evening_time']}.",
             (f"Works on its own using {order}; {st['plan_runs_today']} of {st['plan_runs']} plan tasks used today; "
              f"{st['eligible']} task(s) marked for Apex.") if st['auto_work'] else 'Not picking up tasks on its own.']
    for p in st['plans']:
        if p['id'] in st['engines'] and p['unavailable']:
            lines.append(f"{p['name']} {p['unavailable']}.")
    if st['events']:
        lines.append('Last: ' + st['events'][0]['text'].split('\n')[0])
    return '\n'.join(lines)
