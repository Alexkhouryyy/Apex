"""Missions: keep working, round after round, until the finish line is proven.

A team task (agent/team.py) runs research → code → review once and stops,
done or not. A mission wraps it in a loop:

    plan → work → check → (failed? feed back what failed) → next round …

and ends only when one of these is true:

  complete       the mission's completion checks pass (agent/verification.py,
                 which fails closed: an unrunnable check is a failure)
  stuck          every round used, or the same checks failed the same way for
                 STALL_ROUNDS rounds in a row (no progress, so more money won't help)
  out_of_budget  the mission's spending cap is reached
  needs_you      a step needs a human: a tool was blocked (sending, deleting,
                 publishing or anything Apex's safety gates hold), a round was
                 cut off mid-action, or Apex's daily spend cap was hit
  paused/stopped you said so

**A finish line you can check is required.** A mission must have at least one
command, file or file-contains check. Without one it could only ever claim it
was done, and a loop that keeps going until it *says* so is worse than none.

**Never sleeps, within honest limits.** While a mission runs, Windows is asked
not to sleep (closing a laptop lid can still override that). When Apex
restarts, a mission that was running carries on by itself, but only if its
interrupted round left no action with an unknown outcome. If one did (say, a
command cut off halfway), the mission waits for you, because repeating an
action whose result is unknown is how work gets done twice.
"""
from __future__ import annotations

import json
import math
import re
import sys
import threading
import time
import uuid

from agent import longterm

MAX_BUDGET = 20.0
DEFAULT_BUDGET = 5.0
ROUND_BUDGET = 1.5            # most one round may spend (a team task allows up to $5)
MAX_ROUNDS = 20
DEFAULT_ROUNDS = 8
STALL_ROUNDS = 3
CHECKABLE = ('command', 'file_exists', 'contains')
FINISHED = ('complete', 'stuck', 'out_of_budget', 'stopped')
_POLL = 2.0                   # seconds between looks at a running round

_lock = threading.RLock()
_threads: dict[str, threading.Thread] = {}
_ready = None
_agent = None


def ensure_db():
    global _ready
    with _lock:
        if _ready == str(longterm.DB_PATH):
            return
        with longterm._conn() as db:
            db.execute('CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL)')
        _ready = str(longterm.DB_PATH)


def get(mid: str) -> dict | None:
    ensure_db()
    with longterm._conn() as db:
        row = db.execute('SELECT data FROM missions WHERE id=?', (mid,)).fetchone()
    return json.loads(row[0]) if row else None


def recent(limit: int = 30) -> list[dict]:
    ensure_db()
    with longterm._conn() as db:
        rows = db.execute('SELECT data FROM missions ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def _save(m: dict) -> dict:
    m['updated'] = time.time()
    with longterm._conn() as db:
        db.execute('INSERT OR REPLACE INTO missions VALUES (?,?,?)', (m['id'], json.dumps(m), m['created']))
    return m


def _update(mid: str, **changes) -> dict:
    with _lock:
        m = get(mid)
        m.update(changes)
        return _save(m)


# --- creating one ------------------------------------------------------------------------

def validate(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError('Expected a mission.')
    title = ' '.join(str(body.get('title') or '').split())
    task = str(body.get('task') or '').strip()
    context = str(body.get('context') or '').strip()
    if not 3 <= len(title) <= 120:
        raise ValueError('Give the mission a short title (3–120 characters).')
    if not 10 <= len(task) <= 8000:
        raise ValueError('Describe the mission in 10–8,000 characters.')
    if len(context) > 8000:
        raise ValueError('Project context must be under 8,000 characters.')
    checks = body.get('checks')
    if not isinstance(checks, list) or not 1 <= len(checks) <= 10:
        raise ValueError('Add 1–10 completion checks.')
    clean = []
    for c in checks:
        if not isinstance(c, dict):
            raise ValueError('Each check needs a kind and a spec.')
        kind, spec, detail = c.get('kind'), str(c.get('spec') or '').strip(), str(c.get('detail') or '').strip()
        if kind not in CHECKABLE + ('manual',):
            raise ValueError('Checks are: command (exits 0), file_exists, contains (file includes text), or manual.')
        if not spec or len(spec) > 1000 or len(detail) > 1000:
            raise ValueError('Each check needs a spec of at most 1,000 characters.')
        if kind == 'contains' and not detail:
            raise ValueError('A "contains" check needs the text the file must include.')
        clean.append(dict(kind=kind, spec=spec, detail=detail))
    if not any(c['kind'] in CHECKABLE for c in clean):
        raise ValueError("Add at least one check Apex can run itself (a command, a file, or text in a file). "
                         "Without one it could only claim it finished.")
    if any(c['kind'] == 'manual' for c in clean):
        raise ValueError('A manual check can never pass on its own, so the mission could never finish. '
                         'Leave it out and confirm the result yourself at the end.')
    cap = body.get('budget_usd', DEFAULT_BUDGET)
    if type(cap) not in (int, float) or not math.isfinite(cap) or not 0.1 <= cap <= MAX_BUDGET:
        raise ValueError(f'The spending cap must be between $0.10 and ${MAX_BUDGET:.0f}.')
    rounds = body.get('max_rounds', DEFAULT_ROUNDS)
    if type(rounds) is not int or not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError(f'Rounds must be between 1 and {MAX_ROUNDS}.')
    roles = body.get('roles', ['researcher', 'coder', 'reviewer'])
    models = body.get('models', {})
    if not isinstance(models, dict):
        raise ValueError('Expected model choices.')
    return dict(title=title, task=task, context=context, checks=clean, budget_usd=float(cap),
                max_rounds=rounds, roles=roles, models=models)


def create(body: dict, agent=None) -> dict:
    """Validate, record the goal and its checks, and start working."""
    from agent import goals, team, verification
    spec = validate(body)
    if any(c['kind'] == 'command' for c in spec['checks']):
        from tools import sandbox
        try:
            sandbox.autonomous_backend()
        except sandbox.SandboxUnavailable:
            raise ValueError('Command checks run in Apex\'s Docker sandbox, and Docker is not running, so this '
                             'mission could never pass. Start Docker Desktop, or use file / contains checks.')
    # Let team.validate refuse a bad role/model choice now, not in round 1.
    team.validate(dict(id='m' * 16, task=spec['task'], roles=spec['roles'], models=spec['models'], budget_usd=0.5))
    ensure_db()
    goals.init_db()
    verification.init_db()
    made = goals.set_goal(spec['title'], spec['task'][:2000], horizon='week')
    found = re.search(r'Goal #(\d+)', made)
    if not found:
        raise ValueError(made)
    gid = int(found.group(1))
    for c in spec['checks']:
        verification.add_contract(gid, c['kind'], c['spec'], c['detail'])
    mid = 'mission-' + uuid.uuid4().hex[:12]
    m = dict(spec, id=mid, goal_id=gid, status='running', reason='Starting round 1.', spent_usd=0.0,
             rounds=[], created=time.time(), updated=time.time())
    with _lock:
        _save(m)
    start(mid, agent)
    return get(mid)


# --- control -----------------------------------------------------------------------------

def _current_run(m):
    return m['rounds'][-1]['run_id'] if m['rounds'] else None


def pause(mid: str) -> dict:
    m = _require(mid)
    if m['status'] != 'running':
        raise ValueError('Only a running mission can be paused.')
    _update(mid, status='paused', reason='Paused by you. The current round stops at its next safe point.')
    _stop_round(m)
    return get(mid)


def stop(mid: str) -> dict:
    m = _require(mid)
    if m['status'] in FINISHED:
        raise ValueError('That mission has already finished.')
    _update(mid, status='stopped', reason='Stopped by you. Work already done stays done.')
    _stop_round(m)
    _notify(get(mid))
    return get(mid)


def resume(mid: str, agent=None, extra_rounds: int = 0, extra_budget: float = 0.0) -> dict:
    """Carry on: after a pause, after it needed you, or with more rounds or money."""
    m = _require(mid)
    if m['status'] in ('running', 'complete', 'stopped'):
        raise ValueError(f"A {m['status']} mission can't be resumed.")
    if type(extra_rounds) is not int or not 0 <= extra_rounds <= MAX_ROUNDS:
        raise ValueError('Extra rounds must be 0–20.')
    if type(extra_budget) not in (int, float) or not 0 <= extra_budget <= MAX_BUDGET:
        raise ValueError('Extra budget must be $0–20.')
    rounds = min(MAX_ROUNDS * 2, m['max_rounds'] + extra_rounds)
    cap = min(MAX_BUDGET * 2, m['budget_usd'] + extra_budget)
    if len(m['rounds']) >= rounds:
        raise ValueError('Every round is used. Resume with extra rounds.')
    if cap - m['spent_usd'] < 0.05:
        raise ValueError('The budget is spent. Resume with extra budget.')
    _update(mid, status='running', max_rounds=rounds, budget_usd=cap, stall_from=len(m['rounds']),
            reason='Resumed by you.')
    start(mid, agent)
    return get(mid)


def _require(mid):
    m = get(mid)
    if not m:
        raise LookupError('Mission not found.')
    return m


def _stop_round(m):
    from agent import team
    rid = _current_run(m)
    if rid:
        team.stop(rid)


# --- the loop ---------------------------------------------------------------------------

def start(mid: str, agent=None) -> None:
    agent = agent or _agent
    with _lock:
        if mid in _threads and _threads[mid].is_alive():
            return
        t = threading.Thread(target=run, args=(mid, agent), daemon=True, name='ApexMission')
        _threads[mid] = t
        t.start()


def _keep_awake(on: bool) -> None:
    """Ask Windows not to sleep while this thread works (ES_CONTINUOUS|ES_SYSTEM_REQUIRED)."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
    except Exception:
        pass


def _round_task(m: dict) -> str:
    n = len(m['rounds']) + 1
    lines = [f"MISSION: {m['task']}", '',
             f"This is round {n} of at most {m['max_rounds']}. The mission is finished only when ALL of these "
             'checks pass, and they are run automatically after this round:']
    for c in m['checks']:
        what = {'command': 'command exits 0', 'file_exists': 'file exists and is not empty',
                'contains': f"file contains {c['detail']!r}"}[c['kind']]
        lines.append(f"  - [{c['kind']}] {c['spec']}  ({what})")
    done = m['rounds'][-3:]
    if done:
        lines += ['', 'What happened in the last rounds (work already done stays done; inspect the current state '
                  'before redoing anything):']
        for r in done:
            lines.append(f"Round {r['n']}: {r['status']}" + (f" ({r['error']})" if r.get('error') else ''))
            if r.get('summary'):
                lines.append('  Summary: ' + r['summary'][:1500])
            for c in r.get('checks', []):
                lines.append(f"  Check {'PASSED' if c['passed'] else 'FAILED'}: [{c['kind']}] {c['spec']} → {c['evidence'][:400]}")
        lines += ['', 'Fix what the failed checks show. Do not repeat an approach that already failed the same way.']
    return '\n'.join(lines)


def _signature(r: dict):
    return tuple((c['spec'], c['passed'], c['evidence'][:200]) for c in r.get('checks', []))


def _finish(mid: str, status: str, reason: str):
    m = _update(mid, status=status, reason=reason)
    _notify(m)


def _notify(m: dict):
    try:
        from agent import notify
        notify.notify(f"Mission {m['status'].replace('_', ' ')}: {m['title']}", m['reason'][:300])
    except Exception:
        pass


def run(mid: str, agent) -> None:
    """Rounds until the checks pass or a limit says stop. One mission thread at a time per mission."""
    from agent import team
    _keep_awake(True)
    try:
        while True:
            m = get(mid)
            if not m or m['status'] != 'running':
                return
            n = len(m['rounds']) + 1
            if n > m['max_rounds']:
                return _finish(mid, 'stuck', f"Used all {m['max_rounds']} rounds without passing the checks. "
                               'Resume with more rounds, or change the plan.')
            left = m['budget_usd'] - m['spent_usd']
            if left < 0.05:
                return _finish(mid, 'out_of_budget', f"Spent ${m['spent_usd']:.2f} of ${m['budget_usd']:.2f}. "
                               'Resume with more budget to continue.')
            rid = f"{mid}-r{n}"
            body = dict(id=rid, task=_round_task(m), context=m['context'], roles=m['roles'], models=m['models'],
                        budget_usd=round(max(0.01, min(ROUND_BUDGET, left)), 2), goal_id=m['goal_id'])
            try:
                team.submit(body, agent)
            except RuntimeError:          # another team task is using the team; wait for it
                _update(mid, reason='Waiting for another team task to finish.')
                time.sleep(max(_POLL, 0.01) * 5)
                continue
            except ValueError as exc:
                return _finish(mid, 'stuck', f'Round {n} could not start: {exc}')
            _update(mid, reason=f'Round {n} running.')
            run_data = _wait(rid, mid)
            r = _record(mid, n, run_data)
            m = get(mid)
            if m['status'] != 'running':      # paused or stopped while the round ran
                return
            if r['verified']:
                return _finish(mid, 'complete', f"All checks passed in round {n}. Spent ${m['spent_usd']:.2f}.")
            if r['status'] != 'done':
                why = _needs_you(r)
                if why:
                    return _finish(mid, 'needs_you', why)
            recent_rounds = m['rounds'][m.get('stall_from', 0):][-STALL_ROUNDS:]
            if (len(recent_rounds) == STALL_ROUNDS and all(x.get('checks') for x in recent_rounds)
                    and len({_signature(x) for x in recent_rounds}) == 1):
                return _finish(mid, 'stuck', f'No progress in {STALL_ROUNDS} rounds: the same checks failed the same way. '
                               'More rounds would only spend money. Look at the last round, adjust the mission, and resume.')
            _update(mid, reason=f'Round {n} did not pass the checks yet; starting round {n + 1}.')
    except Exception as exc:              # never leave a mission silently "running"
        try:
            _finish(mid, 'needs_you', f'The mission loop hit an error: {exc}')
        except Exception:
            pass
    finally:
        _keep_awake(False)


def _wait(rid: str, mid: str) -> dict:
    from agent import team
    asked_stop = False
    while True:
        data = team.get(rid)
        if data and data['status'] not in ('queued', 'running', 'stopping', 'verifying'):
            return data
        m = get(mid)
        if m and m['status'] != 'running' and not asked_stop:
            team.stop(rid)
            asked_stop = True
        time.sleep(_POLL)


def _record(mid: str, n: int, data: dict) -> dict:
    ver = data.get('verification') or {}
    unknown = any(e.get('status') == 'outcome_unknown' for s in data.get('steps', []) for e in s.get('evidence', []))
    apex = next((s for s in data.get('steps', []) if s.get('role') == 'apex'), {})
    r = dict(n=n, run_id=data['id'], status=data['status'], error=data.get('error', '')[:600],
             verified=data['status'] == 'done' and ver.get('status') == 'verified',
             checks=[dict(kind=c['kind'], spec=c['spec'], passed=c['passed'], evidence=str(c.get('evidence', ''))[:600])
                     for c in ver.get('results', [])],
             summary=(apex.get('result') or '')[-2000:], cost_usd=round(data.get('cost_usd', 0.0), 4),
             unknown_outcome=unknown, ended=time.time())
    with _lock:
        m = get(mid)
        m['rounds'].append(r)
        m['spent_usd'] = round(m['spent_usd'] + r['cost_usd'], 4)
        _save(m)
    return r


def _needs_you(r: dict) -> str | None:
    """Why a round that did not finish needs a human, or None to just try again."""
    err = r.get('error', '')
    if r.get('unknown_outcome'):
        return (f"Round {r['n']} was cut off in the middle of an action, so its result is unknown. "
                'Check what it did (Team tasks → this round), then resume.')
    if 'tool was blocked' in err.lower():
        return f"Round {r['n']} needs your approval: {err[:400]}"
    if '[safety]' in err.lower() or 'daily spend cap' in err.lower():
        return f"Apex's own spending cap stopped round {r['n']}: {err[:300]}"
    if r['status'] == 'interrupted':
        return f"Round {r['n']} was interrupted. Resume when you're ready."
    if r['status'] == 'failed':           # a crash or an outage (bad key, no network): another round won't fix it
        return f"Round {r['n']} hit an error, so the mission stopped instead of using up its rounds: {err[:400]}"
    return None                           # a specialist ran out of calls, an incomplete answer: next round


# --- after a restart ---------------------------------------------------------------------

def start_supervisor(agent) -> None:
    """Called once when the dashboard gets its agent: missions that were running carry on."""
    global _agent
    _agent = agent
    from agent import team
    try:
        team.ensure_db()                  # marks rounds cut off by the restart as interrupted
        for m in recent(100):
            if m['status'] != 'running':
                continue
            last = m['rounds'][-1] if m['rounds'] else None
            rid = f"{m['id']}-r{len(m['rounds']) + 1}"
            cut = team.get(rid)           # the round that was running when Apex stopped
            if cut and cut['status'] not in ('queued', 'running', 'stopping', 'verifying'):
                r = _record(m['id'], len(m['rounds']) + 1, cut)
                why = _needs_you(r) if not r['verified'] else None
                if why and r.get('unknown_outcome'):
                    _finish(m['id'], 'needs_you', 'Apex restarted mid-round. ' + why)
                    continue
                if r['verified']:
                    _finish(m['id'], 'complete', f"All checks passed in round {r['n']}.")
                    continue
            elif last is None and cut is None:
                pass
            _update(m['id'], reason='Apex restarted; carrying on.')
            start(m['id'], agent)
    except Exception as exc:
        print(f'[Missions] Could not resume missions after restart: {exc}')
