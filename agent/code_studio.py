"""Apex Code: coding sessions on your Claude and ChatGPT plans, each on its own branch.

A project is a folder with git; Apex itself is always one. A session is one
piece of work. Apex gives it a branch (`apex/<n>-<slug>`) and its own working
copy of the project (a git worktree, under ApexWork/code), so the agent edits
that copy. The project you run, Apex included, is untouched until you press
Keep, which merges the branch in. Throw away deletes the copy and the branch.

Each message you send is one turn on your plan (agent/code_engines.py): Claude
Code or Codex, streamed step by step into the session's feed. Follow-ups
resume the same conversation. After every turn Apex commits what changed on
the session's branch (a checkpoint), which is what makes "Undo last step"
possible and keeps Keep a plain merge.

Around that:
  second opinion  the other plan reviews the change, read-only, and rates it
                  out of 10, without the flattery
  checks          the project's test command, run in the session's copy
  catch up        merge your latest work into the session; if that conflicts,
                  Apex can be asked to resolve it
  switch plan     continue on the other plan (say, at a usage limit) with a
                  recap, because one tool can't resume the other's chat

Coding never uses API credits: only the two plans, signed in on this PC.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from agent import code_engines, longterm, work, work_engines as we

APEX_ROOT = Path(__file__).resolve().parents[1]
ENGINES = ('claude', 'chatgpt')
MODES = ('safe', 'full')
MAX_PROMPT = 20000
MAX_PARALLEL = 3                      # sessions working at once
CHECK_TIMEOUT = 1800
REVIEW_DIFF_LIMIT = 60000             # characters of diff the second opinion gets
DIFF_LIMIT = 400_000                  # characters of one file's diff shown
APEX_CHECKS = 'python -m pytest -q -x -p no:cacheprovider'
CONFLICT_MARK = re.compile(r'^(<{7}|>{7})( |$)', re.M)
RATING = re.compile(r'rating\s*[:\-]?\s*\**\s*(\d{1,2})(?:\.\d+)?\s*/\s*10', re.I)
ANY_RATING = re.compile(r'\b(\d{1,2})(?:\.\d+)?\s*/\s*10\b')


class CodeError(ValueError):
    pass


_lock = threading.Lock()
_turns: dict[int, str] = {}           # session id -> run id of its running turn
_side: dict[int, str] = {}            # session id -> run id of a running review or checks
_recovered = False


# ---------------------------------------------------------------- storage

def init_db() -> None:
    global _recovered
    with longterm._conn() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS code_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
            checks TEXT NOT NULL DEFAULT '', created REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS code_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, title TEXT NOT NULL,
            engine TEXT NOT NULL, mode TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', worktree TEXT NOT NULL DEFAULT '',
            base_ref TEXT NOT NULL, base_commit TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready',
            turn_state TEXT NOT NULL DEFAULT 'idle', engine_session TEXT, last_status TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '', files_changed INTEGER NOT NULL DEFAULT 0,
            review_state TEXT NOT NULL DEFAULT '', review_engine TEXT NOT NULL DEFAULT '',
            review_rating INTEGER, review_text TEXT NOT NULL DEFAULT '',
            check_state TEXT NOT NULL DEFAULT '', check_output TEXT NOT NULL DEFAULT '',
            conflict INTEGER NOT NULL DEFAULT 0, pending_note TEXT NOT NULL DEFAULT '',
            kept_commit TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS code_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, ts REAL NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS code_events_session ON code_events (session_id, id)')
    if not _recovered:
        _recovered = True
        _recover()


def _recover() -> None:
    """Apex restarted: nothing can still be running from before."""
    with longterm._conn() as db:
        stuck = [r[0] for r in db.execute("SELECT id FROM code_sessions WHERE turn_state != 'idle'")]
        db.execute("UPDATE code_sessions SET turn_state='idle' WHERE turn_state != 'idle'")
        db.execute("UPDATE code_sessions SET review_state='failed', review_text='Apex restarted during the review.' "
                   "WHERE review_state='working'")
        db.execute("UPDATE code_sessions SET check_state='' WHERE check_state='running'")
    for sid in stuck:
        event(sid, 'done', status='interrupted', summary='Apex restarted while this was working. '
              'What it had changed is still in the session; send a message to carry on.', seconds=0, files=0)


def _rows(sql, args=()) -> list[dict]:
    init_db()
    with longterm._conn() as db:
        cur = db.execute(sql, args)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _set(sid: int, **fields) -> None:
    fields.setdefault('updated', time.time())
    with longterm._conn() as db:
        db.execute(f"UPDATE code_sessions SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), sid))


def event(sid: int, kind: str, **data) -> int:
    with longterm._conn() as db:
        cur = db.execute('INSERT INTO code_events (session_id, ts, kind, data) VALUES (?,?,?,?)',
                         (sid, time.time(), kind, json.dumps(data)))
        return cur.lastrowid


def events(sid: int, after: int = 0, limit: int = 500) -> list[dict]:
    rows = _rows('SELECT id, ts, kind, data FROM code_events WHERE session_id=? AND id>? ORDER BY id LIMIT ?',
                 (sid, after, limit))
    # The row's own fields always win: an event's sequence number is never a tool's id.
    return [{**json.loads(r['data']), 'id': r['id'], 'ts': r['ts'], 'kind': r['kind']} for r in rows]


# ---------------------------------------------------------------- git

def _git_env() -> dict:
    env = dict(os.environ)
    env.update(GIT_TERMINAL_PROMPT='0', GIT_EDITOR='true', GIT_MERGE_AUTOEDIT='no')
    return env


def _git(cwd, *args, input=None, timeout=120, check=True):
    """git in a folder. With check, a failure raises CodeError in git's words."""
    try:
        p = subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=str(cwd), input=input,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                           env=_git_env(), **we._hidden())
    except FileNotFoundError:
        raise CodeError('Git is not installed. Install Git for Windows from git-scm.com, then try again.')
    except subprocess.TimeoutExpired:
        raise CodeError(f'git {args[0]} took longer than {timeout} s.')
    if check and p.returncode != 0:
        raise CodeError((p.stderr or p.stdout).strip()[-800:] or f'git {args[0]} failed.')
    return p if not check else p.stdout


def _ident(repo) -> list[str]:
    """Commit as the owner when git knows who that is; otherwise as Apex Code."""
    if _git(repo, 'config', 'user.name', check=False).stdout.strip() and \
            _git(repo, 'config', 'user.email', check=False).stdout.strip():
        return []
    return ['-c', 'user.name=Apex Code', '-c', 'user.email=apex-code@localhost']


def _slug(text: str, n: int = 24) -> str:
    return re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')[:n].strip('-') or 'session'


# ---------------------------------------------------------------- projects

def _default_checks(root: Path) -> str:
    if (root / 'pytest.ini').exists() or (root / 'tests').is_dir() and any(root.glob('*.py')):
        return APEX_CHECKS
    pkg = root / 'package.json'
    if pkg.exists():
        try:
            if json.loads(pkg.read_text(encoding='utf-8')).get('scripts', {}).get('test'):
                return 'npm test'
        except (ValueError, OSError):
            pass
    return ''


def projects() -> list[dict]:
    init_db()
    if not _rows('SELECT id FROM code_projects WHERE path=?', (str(APEX_ROOT),)):
        with longterm._conn() as db:
            db.execute('INSERT OR IGNORE INTO code_projects (name, path, checks, created) VALUES (?,?,?,?)',
                       ('Apex', str(APEX_ROOT), APEX_CHECKS, time.time()))
    out = []
    for p in _rows('SELECT * FROM code_projects ORDER BY path = ? DESC, id', (str(APEX_ROOT),)):
        r = _git(p['path'], 'rev-parse', '--abbrev-ref', 'HEAD', check=False) if Path(p['path']).is_dir() else None
        p['branch'] = r.stdout.strip() if r is not None and r.returncode == 0 else ''
        p['missing'] = r is None
        out.append(p)
    return out


def project(pid: int) -> dict:
    found = [p for p in projects() if p['id'] == pid]
    if not found:
        raise CodeError('No such project.')
    return found[0]


def add_project(path: str, name: str = '') -> dict:
    folder = Path(str(path or '').strip().strip('"')).expanduser()
    if not str(path or '').strip() or not folder.is_dir():
        raise CodeError('That folder does not exist on this PC.')
    r = _git(folder, 'rev-parse', '--show-toplevel', check=False)
    if r.returncode != 0:
        raise CodeError('That folder is not a git project. Run `git init` in it (and make a first commit) first.')
    root = Path(r.stdout.strip()).resolve()
    if _rows('SELECT id FROM code_projects WHERE path=?', (str(root),)):
        raise CodeError('That project is already here.')
    name = (name or root.name).strip()[:60] or root.name
    with longterm._conn() as db:
        cur = db.execute('INSERT INTO code_projects (name, path, checks, created) VALUES (?,?,?,?)',
                         (name, str(root), _default_checks(root), time.time()))
        pid = cur.lastrowid
    return project(pid)


def update_project(pid: int, name=None, checks=None) -> dict:
    project(pid)
    fields = {}
    if name is not None:
        if not str(name).strip() or len(str(name)) > 60:
            raise CodeError('A project name is 1 to 60 characters.')
        fields['name'] = str(name).strip()
    if checks is not None:
        if len(str(checks)) > 500 or '\n' in str(checks):
            raise CodeError('The checks command is one line, at most 500 characters.')
        fields['checks'] = str(checks).strip()
    if fields:
        with longterm._conn() as db:
            db.execute(f"UPDATE code_projects SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
    return project(pid)


def _venv(project_path: str) -> dict:
    """The project's own virtualenv first on PATH, so `python` is the right one."""
    venv = Path(project_path) / '.venv'
    bindir = venv / ('Scripts' if os.name == 'nt' else 'bin')
    if not bindir.is_dir():
        return {}
    return {'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', ''), 'VIRTUAL_ENV': str(venv)}


# ---------------------------------------------------------------- sessions

def session(sid: int) -> dict:
    rows = _rows('SELECT s.*, p.name AS project, p.path AS project_path FROM code_sessions s '
                 'JOIN code_projects p ON p.id = s.project_id WHERE s.id=?', (sid,))
    if not rows:
        raise CodeError('No such session.')
    s = rows[0]
    with _lock:
        s['working'] = sid in _turns
        s['side'] = 'review' if s['review_state'] == 'working' else ('checks' if s['check_state'] == 'running' else '')
    s['engine_name'] = we.NAMES.get(s['engine'], s['engine'])
    s['since'] = None                                     # when the work now running began, for the page's clock
    if s['working'] or s['side']:
        kinds = ('you',) if s['working'] else ('review_started', 'checks_started')
        row = _rows(f"SELECT ts FROM code_events WHERE session_id=? AND kind IN ({','.join('?' * len(kinds))}) "
                    'ORDER BY id DESC LIMIT 1', (sid, *kinds))
        s['since'] = row[0]['ts'] if row else None
    return s


def sessions(project_id: int | None = None, limit: int = 100) -> list[dict]:
    where, args = ('WHERE s.project_id=?', (project_id,)) if project_id else ('', ())
    rows = _rows('SELECT s.id, s.project_id, p.name AS project, s.title, s.engine, s.mode, s.status, s.turn_state, '
                 's.last_status, s.files_changed, s.review_rating, s.review_engine, s.created, s.updated '
                 f'FROM code_sessions s JOIN code_projects p ON p.id = s.project_id {where} '
                 'ORDER BY s.updated DESC LIMIT ?', (*args, limit))
    with _lock:
        for r in rows:
            r['working'] = r['id'] in _turns
    return rows


def _clean_prompt(prompt) -> str:
    prompt = str(prompt or '').strip()
    if not prompt:
        raise CodeError('Tell Apex what to do.')
    if len(prompt) > MAX_PROMPT:
        raise CodeError(f'A message is at most {MAX_PROMPT} characters.')
    return prompt


def _title(prompt: str) -> str:
    first = next((l.strip() for l in prompt.splitlines() if l.strip()), 'Session')
    return first if len(first) <= 70 else first[:69].rstrip() + '…'


def start(project_id: int, prompt: str, engine: str = 'claude', mode: str = 'safe') -> dict:
    """A new session: its own branch and working copy, then the first message."""
    prompt = _clean_prompt(prompt)
    if engine not in ENGINES:
        raise CodeError('Choose your Claude plan or your ChatGPT plan.')
    if mode not in MODES:
        raise CodeError('Mode is safe or full.')
    proj = project(project_id)
    if proj['missing']:
        raise CodeError(f"The folder {proj['path']} is gone.")
    repo = Path(proj['path'])
    head = _git(repo, 'rev-parse', '--verify', 'HEAD', check=False)
    if head.returncode != 0:
        raise CodeError('This project has no commits yet. Make a first commit, then start a session.')
    base_commit = head.stdout.strip()
    base_ref = proj['branch'] or base_commit
    dirty = [l for l in _git(repo, 'status', '--porcelain').splitlines() if l.strip()]
    now = time.time()
    with longterm._conn() as db:
        cur = db.execute('INSERT INTO code_sessions (project_id, title, engine, mode, base_ref, base_commit, created, updated) '
                         'VALUES (?,?,?,?,?,?,?,?)', (project_id, _title(prompt), engine, mode, base_ref, base_commit, now, now))
        sid = cur.lastrowid
    slug = _slug(_title(prompt))
    branch = f'apex/{sid}-{slug}'
    folder = Path(work.WORK_DIR) / 'code' / f"{_slug(proj['name'], 16)}-{sid}-{slug}"
    # Left over from an older install with the same numbers: never reuse, never overwrite.
    if _git(repo, 'rev-parse', '-q', '--verify', f'refs/heads/{branch}', check=False).returncode == 0 or folder.exists():
        tag = format(int(time.time() * 1000) % 1_000_000, 'x')
        branch, folder = f'{branch}-{tag}', folder.with_name(f'{folder.name}-{tag}')
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(repo, 'worktree', 'add', '-b', branch, str(folder), base_commit, timeout=600)
    except CodeError:
        with longterm._conn() as db:
            db.execute('DELETE FROM code_sessions WHERE id=?', (sid,))
        raise
    _set(sid, branch=branch, worktree=str(folder))
    notes = [f"Your {len(dirty)} unsaved change{'s' if len(dirty) != 1 else ''} in {proj['name']} "
             f"{'are' if len(dirty) != 1 else 'is'} not in this session: it starts from your last commit."] if dirty else []
    return send(sid, prompt, engine, mode, _notes=notes)


def _brief(s: dict, prompt: str) -> str:
    import config
    owner = getattr(config, 'OWNER_NAME', '') or 'the owner'
    return (f"You are Apex's coding agent, working for {owner} on the project '{s['project']}'. You are in a git "
            f"worktree made for this session (branch {s['branch']}); {owner}'s own copy stays untouched until they "
            "keep your work.\n"
            "- Do what is asked below, and keep the change focused on it.\n"
            "- Run the relevant tests if the project has them, and fix what you break.\n"
            "- Do not commit, push, switch branches or change git settings: Apex records your work after each message.\n"
            "- Finish with a short summary: what you changed, how you checked it, and anything "
            f"{owner} must decide.\n\n"
            f"{owner}'s request:\n{prompt}")


def _recap(s: dict, new_prompt: str) -> str:
    asks = [e['text'] for e in events(s['id']) if e['kind'] == 'you'][-6:]
    stat = ''
    if s['worktree'] and Path(s['worktree']).is_dir():
        stat = _git(s['worktree'], 'diff', '--stat', '--no-renames', _base(s), check=False).stdout.strip()[-3000:]
    lines = [f"You are taking over a coding session in this git worktree (branch {s['branch']}).",
             'What has been asked so far:'] + [f'- {a[:600]}' for a in asks] + \
            [f"Last summary: {s['summary'][:1500] or '(none)'}",
             'Changes so far (git diff --stat against where the session started):', stat or '(none yet)',
             '', 'Do not commit, push or switch branches: Apex records your work after each message.',
             '', 'New request:', new_prompt]
    return '\n'.join(lines)


def send(sid: int, prompt: str, engine: str | None = None, mode: str | None = None, _notes=()) -> dict:
    """One message: Apex works on it in the background; the feed shows each step."""
    prompt = _clean_prompt(prompt)
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away). Start a new one.')
    engine = engine or s['engine']
    mode = mode or s['mode']
    if engine not in ENGINES:
        raise CodeError('Choose your Claude plan or your ChatGPT plan.')
    if mode not in MODES:
        raise CodeError('Mode is safe or full.')
    with _lock:
        if sid in _turns:
            raise CodeError('Apex is still working on your last message. Wait, or press Stop.')
        if sid in _side:
            raise CodeError('Wait for the review or the checks to finish, or press Stop.')
        if len(_turns) >= MAX_PARALLEL:
            raise CodeError(f'{MAX_PARALLEL} sessions are already working. Wait for one to finish.')
        run_id = f'code-{sid}-{int(time.time() * 1000)}'
        _turns[sid] = run_id
    try:
        first = not any(e['kind'] == 'you' for e in events(sid))
        switched = engine != s['engine']
        if first:
            text = _brief(s, prompt)
        elif switched or not s['engine_session']:
            text = _recap(s, prompt)
        else:
            text = prompt
        if s['pending_note'] and not first:
            text = s['pending_note'] + '\n\n' + text
        event(sid, 'you', text=prompt, engine=engine, mode=mode)
        for note in _notes:
            event(sid, 'note', text=note)
        if switched and not first:
            event(sid, 'note', text=f'Switched to your {we.NAMES[engine]}. It gets a recap of the session so far.')
        resume = None if (switched or first) else s['engine_session']
        _set(sid, engine=engine, mode=mode, turn_state='working', pending_note='',
             **({'engine_session': None} if switched else {}))
        threading.Thread(target=_run_turn, args=(sid, run_id, engine, mode, text, resume, prompt),
                         daemon=True, name=f'ApexCode-{sid}').start()
    except Exception:
        with _lock:
            _turns.pop(sid, None)
        raise
    return session(sid)


def _run_turn(sid, run_id, engine, mode, text, resume, prompt) -> None:
    started = time.time()
    s = session(sid)
    folder = Path(s['worktree'])
    result = {'status': 'failed', 'summary': 'It did not start.'}
    files = 0
    try:
        start_sha = _git(folder, 'rev-parse', 'HEAD').strip()

        def on_event(e):
            if e['kind'] == 'session':
                _set(sid, engine_session=e['id'])
            elif e['kind'] != 'done':                    # the turn's end is recorded below, with its numbers
                # A tool's own id (to pair a command with its result) is kept as `ref`.
                event(sid, e['kind'], **{('ref' if k == 'id' else k): v for k, v in e.items() if k != 'kind'})

        extra = _venv(s['project_path'])
        result = code_engines.turn(engine, text, folder, mode, resume, on_event, run_id, env_extra=extra)
        if result['status'] == 'failed' and resume and code_engines.RESUME_LOST.search(result.get('summary') or ''):
            event(sid, 'note', text='The plan had lost this conversation, so Apex started it fresh with a recap.')
            result = code_engines.turn(engine, _recap(session(sid), prompt), folder, mode, None, on_event, run_id,
                                       env_extra=extra)
        if result.get('session'):
            _set(sid, engine_session=result['session'])
        files = _checkpoint(sid, start_sha, f'turn on your {we.NAMES[engine]}')
    except Exception as exc:                              # never leave a session marked working
        result = {'status': 'failed', 'summary': f'{type(exc).__name__}: {exc}'}
    finally:
        took = round(time.time() - started)
        total = _count_changed(sid)
        event(sid, 'done', status=result['status'], summary=(result.get('summary') or '')[:6000], seconds=took,
              files=files, total=total, engine=engine, tokens=result.get('tokens') or 0,
              reset_at=result.get('reset_at'))
        fields = {'turn_state': 'idle', 'last_status': result['status'], 'files_changed': total}
        if result['status'] == 'done':
            fields['summary'] = (result.get('summary') or '')[:6000]
        _set(sid, **fields)
        if result['status'] in ('limited', 'signed_out', 'missing'):
            try:
                from agent import work_agent
                work_agent.mark_unavailable(engine, result['status'], result.get('summary') or '',
                                            until=result.get('reset_at'))
            except Exception as exc:
                print(f'[Code] could not rest the plan: {exc}')
        if took >= 60 and result['status'] != 'stopped':
            _notify(s['title'], result['status'], total)
        with _lock:                                       # last: "not working" means everything above is done
            _turns.pop(sid, None)


def _notify(title, status, files) -> None:
    body = (f'"{title}" is ready: {files} file{"s" if files != 1 else ""} changed. Review it in Apex Code.'
            if status == 'done' else f'"{title}" stopped ({status}). Open Apex Code to see why.')
    try:
        from agent import notify
        notify.notify('Apex · Code', body)
    except Exception as exc:
        print(f'[Code] could not notify: {exc}')


def _merging(folder) -> bool:
    return _git(folder, 'rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False).returncode == 0


def _checkpoint(sid: int, start_sha: str, why: str) -> int:
    """Commit what changed on the session's branch. Returns how many files. During
    a catch-up merge, only once no conflict markers remain (that ends the merge)."""
    s = session(sid)
    folder = s['worktree']
    if not folder or not Path(folder).is_dir():
        return 0
    if _merging(folder):
        unmerged = [f for f in _git(folder, 'diff', '--name-only', '--diff-filter=U').splitlines() if f]
        still = [f for f in unmerged if _has_markers(Path(folder) / f)]
        if still:
            _set(sid, conflict=1)
            event(sid, 'conflict', files=still[:50])
            return 0
    _git(folder, 'add', '-A')
    staged = [f for f in _git(folder, 'diff', '--cached', '--name-only', '--no-renames').splitlines() if f]
    if not staged and not _merging(folder):
        return 0
    n = sum(1 for e in events(sid) if e['kind'] == 'checkpoint') + 1
    message = f"Apex Code: {s['title']}\n\nCheckpoint {n}: {why}."
    _git(folder, *_ident(folder), 'commit', '-q', '--no-verify', '-F', '-', input=message)
    sha = _git(folder, 'rev-parse', 'HEAD').strip()
    if s['conflict']:
        _set(sid, conflict=0)
        event(sid, 'note', text='Conflicts resolved: your latest work is in this session now.')
    event(sid, 'checkpoint', sha=sha, prev=start_sha, files=len(staged))
    return len(staged)


def _has_markers(path: Path) -> bool:
    try:
        return bool(CONFLICT_MARK.search(path.read_text(encoding='utf-8', errors='replace')))
    except OSError:
        return False


# ---------------------------------------------------------------- what changed

def _count_changed(sid: int) -> int:
    try:
        return len(changes(sid)['files'])
    except CodeError:
        return 0


def _on_branch(s: dict) -> bool:
    return bool(re.fullmatch(r'[\w./-]+', s['base_ref'])) and not re.fullmatch(r'[0-9a-f]{40}', s['base_ref'])


def _base(s: dict) -> str:
    """Where the session's own work starts: where it meets your branch. After a
    catch-up that's your latest, so your commits never count as the session's."""
    if _on_branch(s) and s['worktree'] and Path(s['worktree']).is_dir():
        r = _git(s['worktree'], 'merge-base', 'HEAD', s['base_ref'], check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return s['base_commit']


def _refs(s: dict):
    """(folder, from, to) to diff: the working copy while it exists; after Keep, what
    the merge brought in."""
    if s['status'] == 'kept' and s['kept_commit']:
        return s['project_path'], s['kept_commit'] + '^1', s['kept_commit']
    if s['worktree'] and Path(s['worktree']).is_dir():
        return s['worktree'], _base(s), None
    raise CodeError('This session was thrown away, so its changes are gone.')


def changes(sid: int) -> dict:
    s = session(sid)
    folder, a, b = _refs(s)
    span = [a] + ([b] if b else [])
    stat = _git(folder, 'diff', '--numstat', '--no-renames', *span)
    kinds = dict(reversed(l.split('\t', 1)) for l in _git(folder, 'diff', '--name-status', '--no-renames', *span).splitlines()
                 if '\t' in l)
    files = []
    for line in stat.splitlines():
        parts = line.split('\t', 2)
        if len(parts) == 3:
            plus, minus, path = parts
            files.append({'path': path, 'plus': None if plus == '-' else int(plus), 'minus': None if minus == '-' else int(minus),
                          'change': {'A': 'add', 'D': 'delete'}.get(kinds.get(path, 'M')[:1], 'update')})
    if b is None:                                          # new files not checkpointed yet
        for path in _git(folder, 'ls-files', '--others', '--exclude-standard').splitlines():
            if path and not any(f['path'] == path for f in files):
                try:
                    text = (Path(folder) / path).read_text(encoding='utf-8')
                    plus = len(text.splitlines())
                except (UnicodeDecodeError, OSError):
                    plus = None
                files.append({'path': path, 'plus': plus, 'minus': 0, 'change': 'add'})
    files.sort(key=lambda f: f['path'])
    return {'files': files, 'plus': sum(f['plus'] or 0 for f in files), 'minus': sum(f['minus'] or 0 for f in files)}


def diff(sid: int, path: str) -> str:
    """One changed file's diff. Only files in the session's change list."""
    s = session(sid)
    listed = {f['path']: f for f in changes(sid)['files']}
    if path not in listed:
        raise CodeError('That file has no changes in this session.')
    folder, a, b = _refs(s)
    tracked = _git(folder, 'ls-files', '--error-unmatch', '--', path, check=False).returncode == 0 or b is not None
    if tracked or listed[path]['change'] == 'delete':
        text = _git(folder, 'diff', '--no-renames', a, *([b] if b else []), '--', path)
    else:
        try:
            body = (Path(folder) / path).read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return f'Binary file {path} (new)'
        lines = body.splitlines()
        text = f'new file {path}\n@@ -0,0 +1,{len(lines)} @@\n' + '\n'.join('+' + l for l in lines)
    if len(text) > DIFF_LIMIT:
        text = text[:DIFF_LIMIT] + f'\n… (cut: the full diff is {len(text):,} characters)'
    return text


# ---------------------------------------------------------------- stop, undo, catch up, keep, throw away

def stop(sid: int) -> bool:
    """Stop whatever is running in the session, even if it is only just starting."""
    with _lock:
        ids = [x for x in (_turns.get(sid), _side.get(sid)) if x]
    return any([we.stop(r, before_start=True) for r in ids])


def _idle(s: dict) -> None:
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away).')
    with _lock:
        if s['id'] in _turns or s['id'] in _side:
            raise CodeError('Apex is still working in this session. Wait, or press Stop.')


def undo(sid: int) -> dict:
    """Put the session's files back to before its last step."""
    s = session(sid)
    _idle(s)
    done = {e['sha'] for e in events(sid) if e['kind'] == 'undo'}
    points = [e for e in events(sid) if e['kind'] == 'checkpoint' and e['sha'] not in done]
    if not points:
        raise CodeError('There is no step to undo yet.')
    last = points[-1]
    if _merging(s['worktree']):
        _git(s['worktree'], 'merge', '--abort', check=False)
    _git(s['worktree'], 'reset', '-q', '--hard', last['prev'])
    event(sid, 'undo', sha=last['sha'], to=last['prev'], files=last['files'])
    _set(sid, conflict=0, files_changed=_count_changed(sid),
         pending_note='Note: the owner undid your last step, so the files are back to how they were before it.')
    return session(sid)


def catch_up(sid: int) -> dict:
    """Bring the project's latest work into this session (a merge on its branch)."""
    s = session(sid)
    _idle(s)
    if not _on_branch(s):
        raise CodeError('The project was not on a branch when this session started, so there is nothing to catch up with.')
    folder = s['worktree']
    _checkpoint(sid, _git(folder, 'rev-parse', 'HEAD').strip(), 'before catching up')
    tip = _git(folder, 'rev-parse', s['base_ref'], check=False)
    if tip.returncode != 0:
        raise CodeError(f"The branch {s['base_ref']} is gone from the project.")
    if _git(folder, 'merge-base', '--is-ancestor', tip.stdout.strip(), 'HEAD', check=False).returncode == 0:
        return {**session(sid), 'caught_up': 'already'}
    before = _git(folder, 'rev-parse', 'HEAD').strip()
    p = _git(folder, *_ident(folder), 'merge', '--no-edit', s['base_ref'], check=False)
    if p.returncode != 0:
        unmerged = [f for f in _git(folder, 'diff', '--name-only', '--diff-filter=U').splitlines() if f]
        if not unmerged:
            _git(folder, 'merge', '--abort', check=False)
            raise CodeError((p.stderr or p.stdout).strip()[-600:] or 'Catching up failed.')
        _set(sid, conflict=1)
        event(sid, 'conflict', files=unmerged[:50])
        return {**session(sid), 'caught_up': 'conflict', 'conflicts': unmerged}
    count = _git(folder, 'rev-list', '--count', f'{before}..{tip.stdout.strip()}').strip()
    event(sid, 'checkpoint', sha=_git(folder, 'rev-parse', 'HEAD').strip(), prev=before, files=0, catch_up=True)
    event(sid, 'note', text=f"Caught up with {s['base_ref']}: {count} newer commit{'s' if count != '1' else ''} of yours are in this session now.")
    _set(sid, files_changed=_count_changed(sid))
    return {**session(sid), 'caught_up': 'merged'}


def keep(sid: int) -> dict:
    """Merge the session into the project, then tidy away its working copy."""
    s = session(sid)
    _idle(s)
    folder = s['worktree']
    _checkpoint(sid, _git(folder, 'rev-parse', 'HEAD').strip(), 'kept')
    s = session(sid)
    if s['conflict'] or _merging(folder):
        raise CodeError('Resolve the conflicts first (ask Apex to fix them), then Keep.')
    if not _git(folder, 'diff', '--name-only', _base(s), 'HEAD').strip():
        raise CodeError('Nothing to keep yet: no files changed.')
    repo = s['project_path']
    total = _count_changed(sid)                           # before merging: after, there's nothing left to compare
    target = _git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    if target == 'HEAD':
        raise CodeError('Your project is not on a branch right now (detached HEAD). Check out a branch, then Keep.')
    message = (f"Apex Code: {s['title']}\n\n{(s['summary'] or '').strip()[:1500]}\n\n"
               f"Session {sid}, on your {we.NAMES.get(s['engine'], s['engine'])}.").strip()
    # git merge, unlike git commit, can't read its message from stdin: a file, then.
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(message)
    try:
        p = _git(repo, *_ident(repo), 'merge', '--no-ff', '-F', f.name, s['branch'], check=False)
    finally:
        os.unlink(f.name)
    if p.returncode != 0:
        said = (p.stderr or p.stdout).strip()
        unmerged = [f for f in _git(repo, 'diff', '--name-only', '--diff-filter=U').splitlines() if f]
        if unmerged or _merging(repo):
            _git(repo, 'merge', '--abort', check=False)
            raise CodeError(f"Your {target} changed in the same places ({', '.join(unmerged[:5])}). "
                            'Press Catch up, let Apex fix the conflicts, then Keep.')
        files = re.findall(r'^\t(\S.*)$', said, re.M)
        if 'would be overwritten' in said:
            raise CodeError(f"You have unsaved changes in {', '.join(files[:5]) or 'files'} that this would overwrite. "
                            'Commit or stash them, then Keep.')
        raise CodeError(said[-600:] or 'The merge failed.')
    merged = _git(repo, 'rev-parse', 'HEAD').strip()
    _set(sid, status='kept', kept_commit=merged, files_changed=total)
    event(sid, 'kept', commit=merged[:12], into=target, files=total,
          restart=Path(repo).resolve() == APEX_ROOT)
    _remove_copy(repo, folder)
    _git(repo, 'branch', '-d', s['branch'], check=False)
    return session(sid)


def _remove_copy(repo, folder) -> None:
    """Delete a session's working copy. On Windows a file still open can make git
    refuse; then delete the folder directly and let git forget it."""
    if not folder:
        return
    _git(repo, 'worktree', 'remove', '--force', folder, check=False)
    if Path(folder).exists():
        shutil.rmtree(folder, ignore_errors=True)
        _git(repo, 'worktree', 'prune', check=False)


def discard(sid: int) -> dict:
    """Throw the session away: its working copy and branch are deleted."""
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is already finished.')
    if stop(sid):
        for _ in range(50):
            with _lock:
                if sid not in _turns and sid not in _side:
                    break
            time.sleep(0.1)
    repo = s['project_path']
    _remove_copy(repo, s['worktree'])
    if s['branch']:
        _git(repo, 'branch', '-D', s['branch'], check=False)
    _set(sid, status='discarded')
    event(sid, 'discarded')
    return session(sid)


# ---------------------------------------------------------------- second opinion and checks

REVIEW = """You are giving a second opinion on a code change another AI made in this git repository, for {owner}.
Be brutally honest: {owner} wants the truth, not flattery. Look for bugs, things that don't do what was asked,
missing or weak tests, security problems and needless complexity. Read any file you need. Do not change anything.

What {owner} asked for:
{asks}

The change (git diff against where the session started{cut}):
```diff
{patch}
```

Answer in exactly this shape:
Rating: N/10
Verdict: one sentence.
Problems:
- the most serious first, each with file:line, or "- None found"
Good:
- one or two things done well"""


def parse_rating(text: str) -> int | None:
    m = RATING.search(text or '') or ANY_RATING.search(text or '')
    return max(0, min(10, int(m.group(1)))) if m else None


def review(sid: int, engine: str | None = None) -> dict:
    """The other plan reviews the session's change, read-only, and rates it."""
    import config
    s = session(sid)
    _idle(s)
    engine = engine or ('chatgpt' if s['engine'] == 'claude' else 'claude')
    if engine not in ENGINES:
        raise CodeError('Choose your Claude plan or your ChatGPT plan.')
    signed = we.check(engine)
    if not signed['ok']:
        raise CodeError(f"Your {we.NAMES[engine]} {signed['why']}. {signed['how']}")
    folder = s['worktree']
    _checkpoint(sid, _git(folder, 'rev-parse', 'HEAD').strip(), 'before the second opinion')
    patch = _git(folder, 'diff', '--no-renames', _base(s), 'HEAD')
    if not patch.strip():
        raise CodeError('Nothing to review yet: no files changed.')
    owner = getattr(config, 'OWNER_NAME', '') or 'the owner'
    asks = '\n'.join(f'- {e["text"][:800]}' for e in events(sid) if e['kind'] == 'you') or '- (not recorded)'
    cut = f', cut to the first {REVIEW_DIFF_LIMIT:,} characters: read the files for the rest' if len(patch) > REVIEW_DIFF_LIMIT else ''
    prompt = REVIEW.format(owner=owner, asks=asks, cut=cut, patch=patch[:REVIEW_DIFF_LIMIT])
    run_id = f'review-{sid}-{int(time.time() * 1000)}'
    with _lock:
        if sid in _turns or sid in _side:
            raise CodeError('Apex is still working in this session. Wait, or press Stop.')
        _side[sid] = run_id
    _set(sid, review_state='working', review_engine=engine, review_rating=None, review_text='')
    event(sid, 'review_started', engine=engine)

    def go():
        result = {'status': 'failed', 'summary': ''}
        try:
            def on_event(e):
                if e['kind'] in ('tool', 'file') and e.get('title', e.get('path')):
                    event(sid, 'review_step', title=e.get('title') or f"Read {e.get('path')}")
            result = code_engines.turn(engine, prompt, Path(folder), 'review', None, on_event, run_id,
                                       timeout=1200, env_extra=_venv(s['project_path']))
        except Exception as exc:
            result = {'status': 'failed', 'summary': f'{type(exc).__name__}: {exc}'}
        finally:
            text = (result.get('summary') or '').strip()
            rating = parse_rating(text) if result['status'] == 'done' else None
            ok = result['status'] == 'done'
            event(sid, 'review', engine=engine, status=result['status'], rating=rating, text=text[:8000])
            _set(sid, review_state='done' if ok else 'failed', review_rating=rating, review_text=text[:8000])
            if result['status'] in ('limited', 'signed_out', 'missing'):
                try:
                    from agent import work_agent
                    work_agent.mark_unavailable(engine, result['status'], text, until=result.get('reset_at'))
                except Exception as exc:
                    print(f'[Code] could not rest the plan: {exc}')
            with _lock:
                _side.pop(sid, None)
    threading.Thread(target=go, daemon=True, name=f'ApexCodeReview-{sid}').start()
    return session(sid)


def _argv(command: str, env: dict) -> list[str]:
    argv = shlex.split(command, posix=os.name != 'nt')
    if os.name == 'nt':
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
    if not argv:
        raise CodeError('Set a checks command for this project first.')
    # Windows looks a program up on Apex's own PATH, not the one given to it.
    found = shutil.which(argv[0], path=env.get('PATH'))
    return [found or argv[0]] + argv[1:]


def run_checks(sid: int) -> dict:
    """The project's test command, in the session's copy."""
    s = session(sid)
    _idle(s)
    proj = project(s['project_id'])
    if not proj['checks']:
        raise CodeError('Set a checks command for this project first (Project settings).')
    env = we._env()
    env.update(_venv(s['project_path']))
    argv = _argv(proj['checks'], env)
    run_id = f'checks-{sid}-{int(time.time() * 1000)}'
    with _lock:
        if sid in _turns or sid in _side:
            raise CodeError('Apex is still working in this session. Wait, or press Stop.')
        _side[sid] = run_id
    _set(sid, check_state='running', check_output='')
    event(sid, 'checks_started', command=proj['checks'])

    def go():
        started, out, passed = time.time(), '', False
        try:
            proc = subprocess.Popen(argv, cwd=s['worktree'], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **we._hidden())
            we.track(run_id, proc)
            timer = threading.Timer(CHECK_TIMEOUT, lambda: we._kill_tree(proc))
            timer.daemon = True
            timer.start()
            chunks = []
            for line in proc.stdout:
                chunks.append(line)
                if len(chunks) > 4000:
                    del chunks[:2000]
            proc.wait()
            timer.cancel()
            stopped = we.untrack(run_id)
            out = ''.join(chunks)[-8000:]
            passed = proc.returncode == 0 and not stopped
            if stopped:
                out += '\n(stopped)'
        except (OSError, ValueError) as exc:
            out = f'Could not run {proj["checks"]!r}: {exc}'
        finally:
            took = round(time.time() - started)
            event(sid, 'checks', passed=passed, seconds=took, output=out[-4000:], command=proj['checks'])
            _set(sid, check_state='passed' if passed else 'failed', check_output=out)
            with _lock:
                _side.pop(sid, None)
    threading.Thread(target=go, daemon=True, name=f'ApexCodeChecks-{sid}').start()
    return session(sid)


# ---------------------------------------------------------------- the page's overview

def overview() -> dict:
    """Projects, recent sessions, your plans and this week's numbers."""
    import config
    from agent import work_agent
    free = work_agent.available()
    order = [e for e in work_agent.settings()['engines'] if e in ENGINES] + list(ENGINES)
    default = next((e for e in order if not free.get(e)), 'claude')
    week = time.time() - 7 * 86400
    recent = _rows('SELECT id, status, review_rating FROM code_sessions WHERE created >= ?', (week,))
    rated = [r['review_rating'] for r in recent if r['review_rating'] is not None]
    turns = _rows("SELECT data FROM code_events WHERE kind='done' AND ts >= ?", (week,))
    minutes = sum(json.loads(t['data']).get('seconds', 0) for t in turns) / 60
    with _lock:
        working = len(_turns)
    return {
        'owner': getattr(config, 'OWNER_NAME', '') or '',
        'projects': projects(),
        'sessions': sessions(),
        'plans': [{'id': e, 'name': we.NAMES[e], 'ready': not free.get(e), 'why': free.get(e) or '',
                   'how': we.check(e)['how'] if free.get(e) else ''} for e in ENGINES],
        'default_engine': default,
        'week': {'sessions': len(recent), 'kept': sum(1 for r in recent if r['status'] == 'kept'),
                 'rating': round(sum(rated) / len(rated), 1) if rated else None,
                 'minutes': round(minutes), 'credits': 0},
        'working': working,
    }
