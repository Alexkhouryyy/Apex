"""Work: projects and tasks across your job, studies, business and software,
one Today view, and tasks you can hand to Apex.

Storage is the long-term memory database (two tables), so the same tasks
reach the dashboard, voice and chat. A task Apex works on runs through the
team task runner (agent/team.py): it writes its deliverable into a folder of
its own, a reviewer pass checks it, and the task moves to "review": your
call whether it is done, never Apex's.

Quick add understands a little shorthand so a task is one line:

    send invoice to Karim fri #job !high
    study chapter 4 tomorrow #studies @Signals
    fix login bug 2026-10-20 #software !low
    call the accountant next week waiting

  #area      job, studies, business, software (or other)
  @project   an existing project's name (start of it is enough)
  !high/!low priority (!med is the default)
  dates      today, tonight, tomorrow, a weekday, next week, 12/10, 2026-10-12
  waiting    starts it as "waiting on someone"
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from agent import longterm

AREAS = ('job', 'studies', 'business', 'software', 'other')
AREA_NAMES = {'job': 'Job & clients', 'studies': 'Studies', 'business': 'Business', 'software': 'Software', 'other': 'Other'}
STATUSES = ('todo', 'doing', 'waiting', 'review', 'done')
PRIORITIES = {'high': 1, 'med': 2, 'low': 3}
WORK_DIR = Path.home() / 'ApexWork'
APEX_BUDGET = 0.50
MAX_TITLE, MAX_NOTES = 200, 8000
_WEEKDAYS = {name: i for i, names in enumerate([('mon', 'monday'), ('tue', 'tues', 'tuesday'), ('wed', 'weds', 'wednesday'),
             ('thu', 'thur', 'thurs', 'thursday'), ('fri', 'friday'), ('sat', 'saturday'), ('sun', 'sunday')]) for name in names}


class WorkError(ValueError):
    pass


def init_db() -> None:
    with longterm._conn() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS work_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, area TEXT NOT NULL, name TEXT NOT NULL,
            client TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
            created REAL NOT NULL, updated REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS work_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, area TEXT NOT NULL,
            title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', due TEXT, priority INTEGER NOT NULL DEFAULT 2,
            status TEXT NOT NULL DEFAULT 'todo', waiting_on TEXT NOT NULL DEFAULT '',
            apex_run TEXT, apex_state TEXT, apex_summary TEXT NOT NULL DEFAULT '', apex_folder TEXT NOT NULL DEFAULT '',
            apex_cost REAL NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL, done_at REAL)''')
        # Runs on your Claude or ChatGPT plan (agent/work_engines.py), outside the team runner.
        db.execute('''CREATE TABLE IF NOT EXISTS work_runs (
            id TEXT PRIMARY KEY, task_id INTEGER NOT NULL, engine TEXT NOT NULL, status TEXT NOT NULL,
            started REAL NOT NULL, ended REAL, summary TEXT NOT NULL DEFAULT '')''')
        cols = {r[1] for r in db.execute('PRAGMA table_info(work_tasks)')}
        if 'apex_engine' not in cols:
            db.execute("ALTER TABLE work_tasks ADD COLUMN apex_engine TEXT NOT NULL DEFAULT ''")
        if 'apex_ok' not in cols:                      # "Apex can take this on its own" (agent/work_agent.py)
            db.execute('ALTER TABLE work_tasks ADD COLUMN apex_ok INTEGER NOT NULL DEFAULT 0')
        # The Apex Code project a software project's code lives in: its +apex tasks become
        # coding sessions on the night shift (agent/work_agent.py, agent/code_studio.py).
        if 'code_project_id' not in {r[1] for r in db.execute('PRAGMA table_info(work_projects)')}:
            db.execute('ALTER TABLE work_projects ADD COLUMN code_project_id INTEGER')


# ---------------------------------------------------------------- quick add

def _date_word(word: str, today: date):
    w = word.lower().strip(',.')
    if w in ('today', 'tonight', 'tdy'):
        return today
    if w in ('tomorrow', 'tmrw', 'tmr'):
        return today + timedelta(days=1)
    if w in _WEEKDAYS:
        ahead = (_WEEKDAYS[w] - today.weekday()) % 7 or 7     # "fri" on a Friday means next Friday
        return today + timedelta(days=ahead)
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', w)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?', w)
    if m:                                                      # day/month, the way it's written in Lebanon
        year = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else today.year
        d = date(year, int(m[2]), int(m[1]))
        return d if m[3] or d >= today else date(year + 1, d.month, d.day)
    return None


def parse_quick(text: str, today: date | None = None, projects=()) -> dict:
    """One line of shorthand -> a task's fields. Unknown words stay in the title."""
    today = today or date.today()
    words, kept = text.split(), []
    out = {'priority': 2, 'status': 'todo'}
    i = 0
    while i < len(words):
        w, low = words[i], words[i].lower()
        nxt = words[i + 1].lower() if i + 1 < len(words) else ''
        if low.startswith('#') and low[1:] in AREAS:
            out['area'] = low[1:]
        elif low.startswith('!') and low[1:] in PRIORITIES:
            out['priority'] = PRIORITIES[low[1:]]
        elif low.startswith('@') and len(low) > 1:
            name = w[1:].lower()
            match = next((p for p in projects if p['name'].lower().startswith(name)), None)
            if match:
                out['project_id'] = match['id']
                out.setdefault('area', match['area'])
            else:
                kept.append(w)
        elif low == 'next' and nxt == 'week':
            out['due'] = (today + timedelta(days=7 - today.weekday())).isoformat(); i += 1
        elif low in ('by', 'on', 'due') and _date_word(nxt, today):
            out['due'] = _date_word(nxt, today).isoformat(); i += 1
        elif _date_word(low, today) and not (w.isupper() and w.isalpha()):   # "SAT" is an exam, not Saturday
            out['due'] = _date_word(low, today).isoformat()
        elif low == 'waiting':
            out['status'] = 'waiting'
        elif low == '+apex':                         # Apex may take this on its own (agent/work_agent.py)
            out['apex_ok'] = True
        else:
            kept.append(w)
        i += 1
    out['title'] = ' '.join(kept).strip()
    if not out['title']:
        raise WorkError('A task needs a title.')
    return out


# ---------------------------------------------------------------- projects and tasks

def _row(cursor, row):
    return {d[0]: v for d, v in zip(cursor.description, row)}


def _clean_text(value, limit, name):
    if not isinstance(value, str):
        raise WorkError(f'{name} must be text.')
    value = value.strip()
    if len(value) > limit:
        raise WorkError(f'{name} is longer than {limit} characters.')
    return value


def _check_due(value):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise WorkError('A due date must look like 2026-10-12.')


def list_projects(include_archived=False):
    init_db()
    with longterm._conn() as db:
        cur = db.execute('SELECT * FROM work_projects' + ('' if include_archived else " WHERE status='active'") + ' ORDER BY area, name')
        return [_row(cur, r) for r in cur.fetchall()]


def add_project(name, area='other', client='', notes=''):
    name = _clean_text(name, 120, 'A project name')
    if not name:
        raise WorkError('A project needs a name.')
    if area not in AREAS:
        raise WorkError(f'Area must be one of: {", ".join(AREAS)}.')
    init_db()
    now = time.time()
    with longterm._conn() as db:
        cur = db.execute('INSERT INTO work_projects (area, name, client, notes, created, updated) VALUES (?,?,?,?,?,?)',
                         (area, name, _clean_text(client, 120, 'Client'), _clean_text(notes, MAX_NOTES, 'Notes'), now, now))
        pid = cur.lastrowid
    return get_project(pid)                     # read back after the insert is committed


def get_project(pid):
    with longterm._conn() as db:
        cur = db.execute('SELECT * FROM work_projects WHERE id=?', (pid,))
        row = cur.fetchone()
        return _row(cur, row) if row else None


def update_project(pid, **changes):
    project = get_project(pid)
    if not project:
        raise WorkError('No such project.')
    fields = {}
    for key, value in changes.items():
        if key == 'name':
            fields['name'] = _clean_text(value, 120, 'A project name') or project['name']
        elif key == 'area':
            if value not in AREAS: raise WorkError('Unknown area.')
            fields['area'] = value
        elif key in ('client', 'notes'):
            fields[key] = _clean_text(value, MAX_NOTES if key == 'notes' else 120, key.title())
        elif key == 'status':
            if value not in ('active', 'archived'): raise WorkError('A project is active or archived.')
            fields['status'] = value
        elif key == 'code_project_id':
            fields['code_project_id'] = _code_project(value)
    if fields:
        fields['updated'] = time.time()
        with longterm._conn() as db:
            db.execute(f"UPDATE work_projects SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
    return get_project(pid)


def _code_project(value):
    """None (no link), or the id of a project in Apex Code."""
    if value is None:
        return None
    if type(value) is not int:
        raise WorkError('The Apex Code project is a project id, or none.')
    from agent import code_studio
    try:
        code_studio.project(value)
    except code_studio.CodeError:
        raise WorkError('No such project in Apex Code.') from None
    return value


def get_task(tid):
    init_db()
    with longterm._conn() as db:
        cur = db.execute('SELECT * FROM work_tasks WHERE id=?', (tid,))
        row = cur.fetchone()
        return _row(cur, row) if row else None


def add_task(title=None, quick=None, area=None, project_id=None, due=None, priority=None, notes='', status=None, today=None,
             apex_ok=None):
    """Either a `quick` line of shorthand, or the fields themselves (fields win)."""
    init_db()
    fields = parse_quick(quick, today, list_projects()) if quick else {}
    if title is not None:
        fields['title'] = _clean_text(title, MAX_TITLE, 'A title')
    for key, value in (('area', area), ('project_id', project_id), ('due', due), ('priority', priority), ('status', status),
                       ('apex_ok', apex_ok)):
        if value is not None:
            fields[key] = value
    if not fields.get('title'):
        raise WorkError('A task needs a title.')
    if len(fields['title']) > MAX_TITLE:
        raise WorkError(f'A title is at most {MAX_TITLE} characters.')
    if fields.get('project_id') is not None:
        project = get_project(int(fields['project_id']))
        if not project:
            raise WorkError('No such project.')
        fields.setdefault('area', project['area'])
    area = fields.get('area') or 'other'
    if area not in AREAS:
        raise WorkError(f'Area must be one of: {", ".join(AREAS)}.')
    priority = int(fields.get('priority', 2))
    if priority not in (1, 2, 3):
        raise WorkError('Priority is 1 (high), 2 or 3 (low).')
    status = fields.get('status', 'todo')
    if status not in STATUSES:
        raise WorkError('Unknown status.')
    now = time.time()
    with longterm._conn() as db:
        cur = db.execute('''INSERT INTO work_tasks (project_id, area, title, notes, due, priority, status, apex_ok, created, updated)
                            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                         (fields.get('project_id'), area, fields['title'], _clean_text(notes or '', MAX_NOTES, 'Notes'),
                          _check_due(fields.get('due')), priority, status, int(bool(fields.get('apex_ok'))), now, now))
        tid = cur.lastrowid
    return get_task(tid)                        # read back after the insert is committed


def update_task(tid, **changes):
    task = get_task(tid)
    if not task:
        raise WorkError('No such task.')
    fields = {}
    for key, value in changes.items():
        if key == 'title':
            fields['title'] = _clean_text(value, MAX_TITLE, 'A title') or task['title']
        elif key in ('notes', 'waiting_on'):
            fields[key] = _clean_text(value, MAX_NOTES, key)
        elif key == 'due':
            fields['due'] = _check_due(value)
        elif key == 'priority':
            if int(value) not in (1, 2, 3): raise WorkError('Priority is 1, 2 or 3.')
            fields['priority'] = int(value)
        elif key == 'area':
            if value not in AREAS: raise WorkError('Unknown area.')
            fields['area'] = value
        elif key == 'project_id':
            if value is not None and not get_project(int(value)): raise WorkError('No such project.')
            fields['project_id'] = value
        elif key == 'status':
            if value not in STATUSES: raise WorkError('Unknown status.')
            fields['status'] = value
            fields['done_at'] = time.time() if value == 'done' else None
        elif key == 'apex_ok':
            if type(value) is not bool: raise WorkError('"Apex can take this" is on or off.')
            fields['apex_ok'] = int(value)
    if fields:
        fields['updated'] = time.time()
        with longterm._conn() as db:
            db.execute(f"UPDATE work_tasks SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), tid))
    return get_task(tid)


def delete_task(tid):
    init_db()
    with longterm._conn() as db:
        return db.execute('DELETE FROM work_tasks WHERE id=?', (tid,)).rowcount == 1


def list_tasks(area=None, project_id=None, include_done=False):
    init_db()
    sync_apex()
    where, args = [], []
    if area: where.append('area=?'); args.append(area)
    if project_id is not None: where.append('project_id=?'); args.append(project_id)
    if not include_done: where.append("(status!='done' OR done_at > ?)"); args.append(time.time() - 3 * 86400)
    sql = 'SELECT * FROM work_tasks' + (' WHERE ' + ' AND '.join(where) if where else '') + \
          " ORDER BY status='done', due IS NULL, due, priority, created"
    with longterm._conn() as db:
        cur = db.execute(sql, args)
        return [_row(cur, r) for r in cur.fetchall()]


def today_view(today: date | None = None):
    """The Today page: what's late, what's due, what's waiting, what Apex finished."""
    today = today or date.today()
    week = (today + timedelta(days=7)).isoformat()
    open_tasks = [t for t in list_tasks() if t['status'] != 'done']
    t0 = today.isoformat()
    view = {
        'date': t0,
        'overdue': [t for t in open_tasks if t['due'] and t['due'] < t0 and t['status'] not in ('waiting', 'review')],
        'today': [t for t in open_tasks if t['due'] == t0 and t['status'] not in ('waiting', 'review')],
        'week': [t for t in open_tasks if t['due'] and t0 < t['due'] <= week and t['status'] not in ('waiting', 'review')],
        'review': [t for t in open_tasks if t['status'] == 'review'],
        'apex_working': [t for t in open_tasks if t['apex_state'] in ('queued', 'running', 'verifying')],
        'waiting': [t for t in open_tasks if t['status'] == 'waiting'],
        'doing': [t for t in open_tasks if t['status'] == 'doing' and t['apex_state'] not in ('queued', 'running', 'verifying')],
        'counts': {a: sum(1 for t in open_tasks if t['area'] == a) for a in AREAS},
    }
    return view


# ---------------------------------------------------------------- Apex does the work

def _slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40] or 'task'


def _task_lines(task) -> list[str]:
    """What the owner wrote about a task: its notes, its project and when it is due."""
    project = get_project(task['project_id']) if task['project_id'] else None
    lines = []
    if task['notes']:
        lines.append(f"Details from the owner: {task['notes']}")
    if project:
        lines.append(f"It belongs to the project '{project['name']}'" + (f" for {project['client']}" if project['client'] else '') + '.')
        if project['notes']:
            lines.append(f"Project notes: {project['notes'][:1500]}")
    if task['due']:
        lines.append(f"It is due {task['due']}.")
    return lines


def apex_brief(task, folder: Path) -> str:
    lines = [f"Do this work task end to end: {task['title']}.", *_task_lines(task)]
    lines += [f"Write the finished deliverable into the folder `{folder}` (create the files there, for example result.md),",
              "and start your final answer with a two-sentence summary of what you produced and anything the owner must check or decide.",
              "Do not send, publish, pay or contact anyone: prepare drafts for the owner instead."]
    return ' '.join(lines)


def code_brief(task) -> str:
    """The first message of a night-shift coding session (agent/work_agent.py). Its
    first line is the task's title, which becomes the session's title. Apex Code adds
    its own framing (work on the branch, don't commit), so no folder line here."""
    import config
    owner = getattr(config, 'OWNER_NAME', '') or 'the owner'
    return '\n'.join([task['title'], *_task_lines(task),
                      'You are working unattended overnight: if a command is blocked, stop and explain; '
                      f'{owner} answers in the morning.'])


UNAVAILABLE = ('limited', 'signed_out', 'missing')   # the plan couldn't take it: the task itself was not tried
_live_runs: set[str] = set()          # subscription runs this process is carrying out


def give_to_apex(tid, agent, budget_usd=APEX_BUDGET, roles=('researcher', 'coder', 'reviewer'), engine='api'):
    """Hand a task to Apex: on your Claude or ChatGPT plan (engine='claude' or
    'chatgpt', no API credits), or the API task runner. Returns the updated task."""
    from agent import work_engines
    task = get_task(tid)
    if not task:
        raise WorkError('No such task.')
    if engine not in work_engines.ENGINES:
        raise WorkError('Choose claude, chatgpt or api.')
    if task['apex_state'] in ('queued', 'running', 'verifying'):
        raise WorkError('Apex is already working on this task.')
    folder = WORK_DIR / f"{task['id']}-{_slug(task['title'])}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    if engine == 'api':
        import config
        from agent import team
        run_id = f"work_{task['id']}_{stamp}"
        try:
            team.submit(dict(id=run_id, task=apex_brief(task, folder), roles=list(roles),
                             models={r: config.AGENT_MODEL for r in (*roles, 'apex')}, budget_usd=budget_usd), agent)
        except (ValueError, RuntimeError) as exc:
            raise WorkError(str(exc)) from exc
    else:
        signed = work_engines.check(engine)
        if not signed['ok']:
            raise WorkError(f"Your {work_engines.NAMES[engine]} {signed['why']}. {signed['how']}")
        run_id = f"cli-{engine}-{task['id']}-{stamp}"
        with longterm._conn() as db:
            db.execute("INSERT INTO work_runs (id, task_id, engine, status, started) VALUES (?,?,?,'running',?)",
                       (run_id, task['id'], engine, time.time()))
        _live_runs.add(run_id)
        prompt = apex_brief(task, folder)

        def carry_out():
            try:
                result = work_engines.run(engine, prompt, folder, run_id=run_id)
            except Exception as exc:                     # never leave a run marked running
                result = {'status': 'failed', 'summary': f'{type(exc).__name__}: {exc}'}
            with longterm._conn() as db:
                db.execute('UPDATE work_runs SET status=?, ended=?, summary=? WHERE id=?',
                           (result['status'], time.time(), result['summary'][:4000], run_id))
            _live_runs.discard(run_id)
            if result['status'] in UNAVAILABLE:
                from agent import work_agent
                work_agent.mark_unavailable(engine, result['status'], result['summary'], until=result.get('reset_at'))
            sync_apex()
    with longterm._conn() as db:
        db.execute("UPDATE work_tasks SET apex_run=?, apex_state='queued', apex_folder=?, apex_summary='', apex_engine=?, "
                   "status='doing', updated=? WHERE id=?", (run_id, str(folder), engine, time.time(), tid))
    if engine != 'api':
        threading.Thread(target=carry_out, daemon=True, name=f'ApexWork-{engine}').start()
    return get_task(tid)


def stop_apex(tid):
    """Stop Apex working on a task. What it wrote so far stays in the folder."""
    task = get_task(tid)
    if not task:
        raise WorkError('No such task.')
    run_id = task['apex_run'] or ''
    if task['apex_state'] not in ('queued', 'running', 'verifying'):
        raise WorkError('Apex is not working on this task.')
    if run_id.startswith('cli-'):
        from agent import work_engines
        if not work_engines.stop(run_id):          # finished a moment ago, or Apex restarted
            sync_apex()
    elif run_id.startswith('code-'):               # the night shift: stop the session, and its checks or review
        from agent import code_studio
        try:
            code_studio.stop(int(run_id[len('code-'):]))
        except (ValueError, code_studio.CodeError):
            pass
        with longterm._conn() as db:
            db.execute("UPDATE work_tasks SET apex_state='stopped', status='todo', updated=?, apex_summary=? WHERE id=?",
                       (time.time(), 'You stopped the night shift. The session stays in Apex Code: keep it, carry on or throw it away there.', tid))
    else:
        from agent import team
        team.stop(run_id)
    return get_task(tid)


def _run_outcome(run_id):
    """(state, summary, cost) for a run, or None while it is still going."""
    if run_id.startswith('code-'):                 # a night-shift session in Apex Code
        return _code_outcome(run_id)
    if run_id.startswith('cli-'):
        with longterm._conn() as db:
            row = db.execute('SELECT status, summary FROM work_runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            return ('failed', 'The run record is missing.', 0)
        status, summary = row
        if status == 'running':
            if run_id in _live_runs:
                return None
            # Apex restarted mid-run: the plan's tool was stopped with it.
            with longterm._conn() as db:
                db.execute("UPDATE work_runs SET status='interrupted', ended=? WHERE id=?", (time.time(), run_id))
            return ('interrupted', 'Apex restarted while this was running. Check the folder for anything it wrote, then hand it over again.', 0)
        return (status, summary, 0)
    from agent import team
    run = team.get(run_id)
    if not run or run['status'] in ('queued', 'running', 'verifying', 'stopping'):
        return None if run else ('failed', 'The run record is missing.', 0)
    apex_step = next((s for s in reversed(run['steps']) if s['result']), None)
    return (run['status'], (apex_step['result'] if apex_step else run.get('error') or ''), run.get('cost_usd') or 0)


def _code_outcome(run_id):
    """A night-shift coding session (run id 'code-<session>', agent/code_studio.py):
    None while its turn, checks or second opinion are still to come; 'done' with
    what Apex saw once all three are settled; the plan's own end otherwise. A plan
    at its limit (or not signed in) puts the task back untried, like any other run."""
    from agent import code_studio
    try:
        sid = int(run_id[len('code-'):])
        s = code_studio.session(sid)
    except (ValueError, code_studio.CodeError):
        return ('failed', 'The Apex Code session is missing.', 0)
    if s['status'] == 'discarded':
        return ('stopped', 'You threw the session away in Apex Code.', 0)
    if s['status'] == 'ready' and (s['working'] or s['side'] or s['turn_state'] != 'idle'):
        return None
    if s['status'] == 'ready' and s['last_status'] != 'done':
        if not s['last_status']:
            return None                                # its first turn hasn't ended yet
        said = code_studio.events(sid)
        end = next((e for e in reversed(said) if e['kind'] == 'done'), {})
        state = s['last_status'] if s['last_status'] in (*UNAVAILABLE, 'stopped', 'interrupted') else 'failed'
        return (state, end.get('summary') or s['summary'] or f'The session ended: {s["last_status"]}.', 0)
    if s['status'] == 'ready' and not code_studio.settled(s):
        return None                                    # Apex's checks or the second opinion are still to come
    return ('done', code_studio.outcome_text(sid), 0)


def sync_apex():
    """Bring each run's outcome onto its task. Finished work waits for the
    owner's review; anything else goes back to them. A plan that hit its usage
    limit (or isn't signed in) puts the task back untried, so it can go to the
    other plan."""
    with longterm._conn() as db:
        rows = db.execute("SELECT id, apex_run FROM work_tasks WHERE apex_state IN ('queued','running','verifying')").fetchall()
    for tid, run_id in rows:
        outcome = _run_outcome(run_id)
        if outcome is None:
            changes = {'apex_state': 'running'}
        else:
            state, summary, cost = outcome
            from agent.working_context import redact      # a task is readable by any signed-in device
            changes = {'apex_summary': redact(summary or '')[:2000], 'apex_cost': round(cost, 4),
                       'apex_state': None if state in UNAVAILABLE else state,
                       'status': 'review' if state == 'done' else 'todo'}
        changes['updated'] = time.time()
        with longterm._conn() as db:
            db.execute(f"UPDATE work_tasks SET {', '.join(k + '=?' for k in changes)} WHERE id=?", (*changes.values(), tid))


def files_of(task) -> list[str]:
    folder = Path(task.get('apex_folder') or '')
    if not task.get('apex_folder') or not folder.is_dir():
        return []
    return sorted(str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file())[:50]


# ---------------------------------------------------------------- chat and voice

def _line(t):
    bits = [f"#{t['id']} {t['title']}"]
    if t['due']: bits.append(f"due {t['due']}")
    if t['priority'] == 1: bits.append('high priority')
    if t['status'] not in ('todo',): bits.append(t['status'])
    project = get_project(t['project_id']) if t['project_id'] else None
    if project: bits.append(f"project {project['name']}")
    return ' · '.join(bits)


def tool(inputs: dict) -> str:
    """The `work` tool: short, speakable answers."""
    action = inputs.get('action')
    try:
        if action == 'add':
            t = add_task(quick=inputs.get('quick') or '', area=inputs.get('area'), notes=inputs.get('notes') or '')
            return f"Added task {_line(t)} ({AREA_NAMES[t['area']]})."
        if action == 'today':
            v = today_view()
            parts = []
            for key, label in (('overdue', 'Overdue'), ('today', 'Due today'), ('review', 'Apex finished, for your review'),
                               ('apex_working', 'Apex is working on'), ('waiting', 'Waiting on others'), ('week', 'Next 7 days')):
                if v[key]:
                    parts.append(f"{label}: " + '; '.join(_line(t) for t in v[key][:8]))
            return '\n'.join(parts) or 'Nothing overdue or due this week, and nothing waiting.'
        if action == 'list':
            tasks = [t for t in list_tasks(area=inputs.get('area')) if t['status'] != 'done']
            return '\n'.join(_line(t) for t in tasks[:30]) or 'No open tasks.'
        if action in ('update', 'done'):
            tid = inputs.get('id')
            if not isinstance(tid, int):
                return 'Give the task id (from today or list).'
            changes = {'status': 'done'} if action == 'done' else {k: inputs[k] for k in ('due', 'status', 'notes', 'area') if k in inputs}
            t = update_task(tid, **changes)
            return f"Updated {_line(t)}."
        if action == 'agent':
            from agent import work_agent
            return work_agent.describe()
        return 'Unknown work action. Use add, today, list, update, done or agent.'
    except WorkError as exc:
        return f'Could not do that: {exc}'
