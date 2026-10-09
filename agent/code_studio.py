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
  checks          the project's test command, run in the session's copy:
                  passed, failed or unknown, on a known commit
  proof           what the agent said it checked next to what Apex saw for
                  itself; Keep asks before keeping work nothing proved
  catch up        merge your latest work into the session; if that conflicts,
                  Apex can be asked to resolve it
  switch plan     continue on the other plan (say, at a usage limit) with a
                  recap, because one tool can't resume the other's chat
  knows you       every new conversation starts with what Apex knows about
                  you (agent/code_brain.py): profile, rules, preferences
  brain on tap    while it works, the plan can ask Apex's memory itself (Apex's
                  own memory server); a memory it suggests waits for your OK
  rules           a correction, said once, becomes a rule: new sessions get it
                  in their brief, open ones ahead of their next message
  write-back      Keep and Throw away (with why) go into Apex's memory: the
                  project's decision log, today's note and the outcomes ledger;
                  what tends to happen here is counted from the sessions, live
  away mode       a command Safe mode stopped reaches your phone, and one tap
                  allows it once (or says no); a finished session pings you, and
                  Apex's restraint holds a 1 a.m. finish until you're around
  Celine          ask out loud about a session: she reads what Apex saw
                  (for_voice) and may draft a message, which only you send

Coding never uses API credits: only the two plans, signed in on this PC.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from agent import code_brain, code_engines, longterm, work, work_engines as we
from agent.working_context import redact

APEX_ROOT = Path(__file__).resolve().parents[1]
ENGINES = ('claude', 'chatgpt')
MODES = ('safe', 'full')
MAX_PROMPT = 20000
MAX_PARALLEL = 3                      # sessions working at once
CHECK_TIMEOUT = 1800
REVIEW_DIFF_LIMIT = 60000             # characters of diff the second opinion gets
DIFF_LIMIT = 400_000                  # characters of one file's diff shown
ALLOW_TTL = 7200                      # seconds a blocked command can be answered from your phone
NIGHT_ALLOW_TTL = 12 * 3600           # ... from a night-shift session: you answer in the morning
MAX_ASKS = 3                          # phone asks for blocked commands, at most, per turn
APEX_CHECKS = 'python -m pytest -q -x -p no:cacheprovider'
CONFLICT_MARK = re.compile(r'^(<{7}|>{7})( |$)', re.M)
RATING = re.compile(r'rating\s*[:\-]?\s*\**\s*(\d{1,2})(?:\.\d+)?\s*/\s*10', re.I)
ANY_RATING = re.compile(r'\b(\d{1,2})(?:\.\d+)?\s*/\s*10\b')
# A follow-up that corrects the agent ("no, never touch the public API"): the page
# offers to make it a rule (agent/code_brain.py). Nothing is saved without a tap.
CORRECTION = re.compile(r"^\s*(no\b|nope\b|don[’']?t\b|do not\b|never\b|always\b|stop\b|instead\b|not like that|"
                        r"that[’']?s (wrong|not)|wrong\b)", re.I)


class CodeError(ValueError):
    pass


_lock = threading.Lock()
_turns: dict[int, str] = {}           # session id -> run id of its running turn
_side: dict[int, str] = {}            # session id -> run id of a running review or checks
_terms: dict[int, str] = {}           # session id -> run id of your terminal command
_live: dict[int, dict] = {}           # session id -> what's being written right now (not stored)
_live_seq = itertools.count(1)        # live versions only ever go up, across steps, so the page can't go back


def _live_for(sid: int) -> dict:
    with _lock:
        return _live.setdefault(sid, {'v': next(_live_seq), 'text': '', 'thinking': '', 'outputs': {}})


def live(sid: int) -> dict:
    """The text the plan is typing, its thinking, and command output as it runs."""
    lv = _live_for(sid)
    with _lock:
        return {'v': lv['v'], 'text': lv['text'][-20000:], 'thinking': lv['thinking'][-6000:],
                'outputs': {k: v[-4000:] for k, v in lv['outputs'].items()}}


def _feed_live(sid: int, e: dict) -> bool:
    """Live chunks go to the live buffer; the stored message replaces them. True if consumed."""
    lv = _live_for(sid)
    with _lock:
        if e['kind'] == 'delta':
            if 'replace' in e:
                lv['text'] = e['replace']
            else:
                lv['text'] += e.get('text', '')
                lv['thinking'] += e.get('thinking', '')
        elif e['kind'] == 'live':
            lv['outputs'][str(e.get('ref'))] = e.get('output', '')
        elif e['kind'] == 'text':
            lv['text'] = ''
        elif e['kind'] == 'thinking':
            lv['thinking'] = ''
        elif e['kind'] == 'result':
            lv['outputs'].pop(str(e.get('id', e.get('ref'))), None)
        else:
            return False
        lv['v'] = next(_live_seq)
        return e['kind'] in ('delta', 'live')
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
        # Away mode: a command Safe mode stopped, sent to your phone to answer. The id is
        # the link's secret; answered, choice and who (which device) are the audit.
        db.execute('''CREATE TABLE IF NOT EXISTS code_pending_allows (
            id TEXT PRIMARY KEY, session_id INTEGER NOT NULL, command TEXT NOT NULL, created REAL NOT NULL,
            expires REAL NOT NULL, answered REAL, choice TEXT NOT NULL DEFAULT '', who TEXT NOT NULL DEFAULT '')''')
        # check_sha: the commit the last checks ran on; check_evidence: why they count as passed, failed or unknown.
        have = {r[1] for r in db.execute('PRAGMA table_info(code_sessions)')}
        for col, ddl in (('model', "TEXT NOT NULL DEFAULT ''"), ('effort', "TEXT NOT NULL DEFAULT ''"),
                         ('tokens', 'INTEGER NOT NULL DEFAULT 0'), ('check_sha', "TEXT NOT NULL DEFAULT ''"),
                         ('check_evidence', "TEXT NOT NULL DEFAULT ''"),
                         # origin: 'you', or 'night' for a Work task Apex took on its own overnight (task_id).
                         ('origin', "TEXT NOT NULL DEFAULT 'you'"), ('task_id', 'INTEGER')):
            if col not in have:
                db.execute(f'ALTER TABLE code_sessions ADD COLUMN {col} {ddl}')
        # checks_exit_ok: this project's test runner prints no count, so exit 0 is taken as a pass.
        have = {r[1] for r in db.execute('PRAGMA table_info(code_projects)')}
        for col, ddl in (('allow', "TEXT NOT NULL DEFAULT '[]'"), ('checks_exit_ok', 'INTEGER NOT NULL DEFAULT 0')):
            if col not in have:
                db.execute(f'ALTER TABLE code_projects ADD COLUMN {col} {ddl}')
    if not _recovered:
        _recovered = True
        _recover()


def _recover() -> None:
    """Apex restarted: nothing can still be running from before."""
    with longterm._conn() as db:
        stuck = [r[0] for r in db.execute("SELECT id FROM code_sessions WHERE turn_state != 'idle'")]
        # last_status too: the night shift reads it to tell its Work task the turn ended.
        db.execute("UPDATE code_sessions SET turn_state='idle', last_status='interrupted' WHERE turn_state != 'idle'")
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


def _last_kind(sid: int) -> str:
    """The last step of the session. A message Celine drafted is a side note, not a step."""
    rows = _rows("SELECT kind FROM code_events WHERE session_id=? AND kind != 'draft' ORDER BY id DESC LIMIT 1", (sid,))
    return rows[0]['kind'] if rows else ''


def tell_sessions(note: str, project_id: int | None = None) -> int:
    """Something the owner decided outside a session (a new rule) that its plan
    must hear: added to the note sent ahead of the next message of every open
    session (of one project, or of all). How many sessions will hear it."""
    where, args = ("status='ready'", ()) if project_id is None else ("status='ready' AND project_id=?", (project_id,))
    init_db()
    return _add_note(where, args, note)


def _add_note(where: str, args: tuple, note: str) -> int:
    """Add a line to the note of the sessions `where` picks; never replace what is waiting there."""
    with longterm._conn() as db:
        cur = db.execute("UPDATE code_sessions SET pending_note = CASE WHEN pending_note = '' THEN ? "
                         f"ELSE pending_note || char(10) || ? END WHERE {where}", (note, note, *args))
        return cur.rowcount


def _note_sent(sid: int, used: str) -> None:
    """The note went with this message: clear it, keeping anything added since it was read."""
    if not used:
        return
    with longterm._conn() as db:
        db.execute('UPDATE code_sessions SET pending_note = ltrim(substr(pending_note, ?), char(10)) '
                   'WHERE id=? AND substr(pending_note, 1, ?) = ?', (len(used) + 1, sid, len(used), used))


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


def update_project(pid: int, name=None, checks=None, exit_ok=None) -> dict:
    """exit_ok: count a test run that exits 0 without printing a count as passed
    (for a runner that never prints one); otherwise that is 'unknown'."""
    project(pid)
    fields = {}
    if exit_ok is not None:
        if not isinstance(exit_ok, bool):
            raise CodeError('"Exit 0 counts as a pass" is on or off.')
        fields['checks_exit_ok'] = int(exit_ok)
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
        s['terminal'] = sid in _terms
    s['engine_name'] = we.NAMES.get(s['engine'], s['engine'])
    s['task'] = _task_of(s.get('task_id'))
    s['since'] = None                                     # when the work now running began, for the page's clock
    if s['working'] or s['side']:
        kinds = ('you',) if s['working'] else ('review_started', 'checks_started')
        row = _rows(f"SELECT ts FROM code_events WHERE session_id=? AND kind IN ({','.join('?' * len(kinds))}) "
                    'ORDER BY id DESC LIMIT 1', (sid, *kinds))
        s['since'] = row[0]['ts'] if row else None
    return s


def _task_of(tid) -> dict | None:
    """The Work task a night-shift session works on: its id and title (agent/work.py)."""
    if not tid:
        return None
    try:
        rows = _rows('SELECT id, title FROM work_tasks WHERE id=?', (tid,))
    except Exception:                                     # Work's tables not made yet
        rows = []
    return rows[0] if rows else {'id': tid, 'title': ''}


def sessions(project_id: int | None = None, limit: int = 100) -> list[dict]:
    where, args = ('WHERE s.project_id=?', (project_id,)) if project_id else ('', ())
    rows = _rows('SELECT s.id, s.project_id, p.name AS project, s.title, s.engine, s.mode, s.status, s.turn_state, '
                 's.last_status, s.files_changed, s.review_rating, s.review_engine, s.origin, s.task_id, s.created, s.updated '
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


ORIGINS = ('you', 'night')


def start(project_id: int, prompt: str, engine: str = 'claude', mode: str = 'safe', model: str = '',
          effort: str = '', plan: bool = False, origin: str = 'you', task_id: int | None = None) -> dict:
    """A new session: its own branch and working copy, then the first message.
    origin 'night' is a Work task (task_id) the night shift took (agent/work_agent.py)."""
    prompt = _clean_prompt(prompt)
    if origin not in ORIGINS:
        raise CodeError('A session comes from you or from the night shift.')
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
        cur = db.execute('INSERT INTO code_sessions (project_id, title, engine, mode, base_ref, base_commit, origin, task_id, '
                         'created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (project_id, _title(prompt), engine, mode, base_ref, base_commit, origin, task_id, now, now))
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
    try:
        return send(sid, prompt, engine, mode, _notes=notes, model=model, effort=effort, plan=plan)
    except CodeError:
        # Never started (say, every slot filled in between): leave no session, copy or
        # branch behind, or the night shift would count it as working and start another.
        _remove_copy(repo, str(folder))
        _git(repo, 'branch', '-D', branch, check=False)
        code_brain.forget_file(sid)
        with longterm._conn() as db:
            db.execute('DELETE FROM code_events WHERE session_id=?', (sid,))
            db.execute('DELETE FROM code_sessions WHERE id=?', (sid,))
        raise


def _brief(s: dict, prompt: str, about: str = '', memory: bool = False) -> str:
    """The first message of a session. `about`: what Apex knows about the owner,
    for Codex, which has no system prompt flag (Claude gets it as a file).
    `memory`: the session can ask Apex's memory server."""
    import config
    owner = getattr(config, 'OWNER_NAME', '') or 'the owner'
    ask = (f"- Apex's memory is open to you (the apex tools: context, recall, lessons, search_files): ask it what "
           f"{owner} decided or prefers rather than guessing. `remember` only suggests a memory; {owner} approves it.\n"
           if memory else '')
    return (f"You are Apex's coding agent, working for {owner} on the project '{s['project']}'. You are in a git "
            f"worktree made for this session (branch {s['branch']}); {owner}'s own copy stays untouched until they "
            "keep your work.\n"
            "- Do what is asked below, and keep the change focused on it.\n"
            "- Run the relevant tests if the project has them, and fix what you break.\n"
            "- Do not commit, push, switch branches or change git settings: Apex records your work after each message.\n"
            + ask +
            "- Finish with a short summary: what you changed, how you checked it, and anything "
            f"{owner} must decide.\n\n"
            + (f"{about}\n\n" if about else '') +
            f"{owner}'s request:\n{prompt}")


def _brain(s: dict, prompt: str) -> dict | None:
    """Current knowledge, including an empty snapshot after the last item is forgotten.
    It never stops a message: if it can't be gathered, the session starts without it."""
    try:
        block = code_brain.brief_block(s['project_id'], prompt)
    except Exception as exc:
        print(f'[Code] could not gather what Apex knows about you: {type(exc).__name__}: {exc}')
        return None
    return block


def _memory_server(s: dict) -> str:
    """Apex's memory server for this session (agent/code_brain.mcp_file), written
    afresh each time: '' when it is turned off (CODE_APEX_MCP=false) or the file
    can't be written. A session never fails for want of it."""
    import config
    if not getattr(config, 'CODE_APEX_MCP', True):
        return ''
    try:
        return str(code_brain.mcp_file(s['id'], s['project_id']))
    except OSError as exc:
        print(f"[Code] could not set up Apex's memory server for session {s['id']}: {exc}")
        return ''


def _system_file(sid: int, block: dict | None, fresh: bool) -> str:
    """The brief file Claude reads, the same one on every turn of the conversation
    (Claude may keep its earlier prompt until it compacts, so changes are also
    sent in the message). '' when there is none."""
    path = code_brain.brief_path(sid)
    try:
        if block and block['text']:
            code_brain.brief_file(sid, block['text'])
        elif fresh or block is not None:
            path.unlink(missing_ok=True)
        return str(path) if path.is_file() else ''
    except OSError as exc:
        print(f'[Code] could not write the brief file: {exc}')
        return ''


def _recap(s: dict, new_prompt: str, about: str = '') -> str:
    asks = [e['text'] for e in events(s['id']) if e['kind'] == 'you'][-6:]
    stat = ''
    if s['worktree'] and Path(s['worktree']).is_dir():
        stat = _git(s['worktree'], 'diff', '--stat', '--no-renames', _base(s), check=False).stdout.strip()[-3000:]
    lines = [f"You are taking over a coding session in this git worktree (branch {s['branch']}).",
             'What has been asked so far:'] + [f'- {a[:600]}' for a in asks] + \
            [f"Last summary: {s['summary'][:1500] or '(none)'}",
             'Changes so far (git diff --stat against where the session started):', stat or '(none yet)',
             '', 'Do not commit, push or switch branches: Apex records your work after each message.',
             *(['', about] if about else []),
             '', 'New request:', new_prompt]
    return '\n'.join(lines)


MENTION = re.compile(r'(?<![\w/])@([\w./\\-]+)')


def mentions(sid: int, prompt: str) -> list[str]:
    """Files the message points at with @path that exist in the session's copy."""
    found = MENTION.findall(prompt)
    if not found:
        return []
    files = set(tree(sid)['files'])
    return [f.replace('\\', '/') for f in dict.fromkeys(found) if f.replace('\\', '/') in files]


def send(sid: int, prompt: str, engine: str | None = None, mode: str | None = None, _notes=(),
         model: str | None = None, effort: str | None = None, plan: bool = False, allow=None, always=None) -> dict:
    """One message: Apex works on it in the background; the feed shows each step.
    model/effort stay with the session; plan and allow are for this message only."""
    prompt = _clean_prompt(prompt)
    s = session(sid)
    engine = engine or s['engine']
    mode = mode or s['mode']
    model = (s['model'] if engine == s['engine'] else '') if model is None else str(model).strip()
    effort = (s['effort'] if engine == s['engine'] else '') if effort is None else str(effort).strip()
    try:
        options = code_engines.check_options({'model': model, 'effort': effort, 'plan': plan,
                                              'allow': list(allow or []), 'always': list(always or []) + _project_allow(s)})
    except ValueError as exc:
        raise CodeError(str(exc)) from exc
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away). Start a new one.')
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
        # A reply to a finished turn that starts like a correction: the page offers to keep it as a rule.
        correction = not first and _last_kind(sid) == 'done' and bool(CORRECTION.match(prompt))
        switched = engine != s['engine']
        # A new conversation (the first message, the other plan taking over, or one
        # that never got going) starts with what Apex knows about the owner. Claude
        # gets it as a system prompt file on every turn; Codex has no such flag, so
        # it goes in the message, ahead of the request.
        fresh = first or switched or not s['engine_session']
        current_block = _brain(s, prompt)
        block = current_block if fresh and current_block and current_block['text'] else None
        if not fresh and current_block:
            prior = next((e.get('brief', {}).get('sources', []) for e in reversed(events(sid)) if e.get('brief')), [])
            if code_brain.memory_signature(prior) != code_brain.memory_signature(current_block['sources']):
                block = current_block
        about = block['text'] if block and engine == 'chatgpt' else ''
        if engine == 'claude':
            options['system_file'] = _system_file(sid, block, fresh)
        options['mcp_file'] = _memory_server(s)
        if first:
            text = _brief(s, prompt, about, memory=bool(options['mcp_file']))
        elif switched or not s['engine_session']:
            text = _recap(s, prompt, about)
        else:
            text = prompt
        if block and not fresh:
            text = ('Updated Apex memory snapshot. Check earlier memory items missing here again before reuse; '
                    'current evidence supersedes conflicting earlier copies and never grants permission.\n\n'
                    + block['text'] + '\n\nCurrent request:\n' + text)
        if s['pending_note'] and not first:
            text = s['pending_note'] + '\n\n' + text
        pointed = mentions(sid, prompt)
        if pointed:
            text = f"Files the owner pointed at (read these first): {', '.join(pointed)}\n\n{text}"
        event(sid, 'you', text=prompt, engine=engine, mode=mode, model=model, effort=effort, plan=bool(plan),
              files=pointed, allow=list(allow or []), **({'correction': True} if correction else {}),
              **({'brief': {'chars': block['chars'], 'sources': block['sources']}} if block else {}))
        for note in _notes:
            event(sid, 'note', text=note)
        if switched and not first:
            event(sid, 'note', text=f'Switched to your {we.NAMES[engine]}. It gets a recap of the session so far.')
        resume = None if (switched or first) else s['engine_session']
        _set(sid, engine=engine, mode=mode, model=model, effort=effort, turn_state='working',
             **({'engine_session': None} if switched else {}))
        _note_sent(sid, s['pending_note'])
        with _lock:
            _live[sid] = {'v': next(_live_seq), 'text': '', 'thinking': '', 'outputs': {}}
        threading.Thread(target=_run_turn, args=(sid, run_id, engine, mode, text, resume, prompt, options, about),
                         daemon=True, name=f'ApexCode-{sid}').start()
    except Exception:
        with _lock:
            _turns.pop(sid, None)
        raise
    return session(sid)


def _run_turn(sid, run_id, engine, mode, text, resume, prompt, options=None, about='') -> None:
    started = time.time()
    s = session(sid)
    folder = Path(s['worktree'])
    result = {'status': 'failed', 'summary': 'It did not start.'}
    files = 0
    asks: dict[str, str] = {}                             # blocked command -> the link your phone answers it with
    try:
        start_sha = _git(folder, 'rev-parse', 'HEAD').strip()
        before_images = image_inventory(folder)

        def on_event(e):
            if e['kind'] == 'session':
                _set(sid, engine_session=e['id'])
            elif _feed_live(sid, e):
                return
            elif e['kind'] != 'done':                    # the turn's end is recorded below, with its numbers
                if e['kind'] == 'blocked' and e.get('command'):
                    token = _allow_token(sid, e['command'], asks, night=s['origin'] == 'night')
                    e = {**e, 'allow_id': token} if token else e
                # A tool's own id (to pair a command with its result) is kept as `ref`.
                event(sid, e['kind'], **{('ref' if k == 'id' else k): v for k, v in e.items() if k != 'kind'})

        extra = _venv(s['project_path'])
        result = code_engines.turn(engine, text, folder, mode, resume, on_event, run_id, env_extra=extra, options=options)
        if result['status'] == 'failed' and resume and code_engines.RESUME_LOST.search(result.get('summary') or ''):
            event(sid, 'note', text='The plan had lost this conversation, so Apex started it fresh with a recap.')
            if engine == 'chatgpt' and not about:     # a new Codex thread: tell it about the owner again
                about = (_brain(session(sid), prompt) or {}).get('text', '')
            result = code_engines.turn(engine, _recap(session(sid), prompt, about), folder, mode, None, on_event,
                                       run_id, env_extra=extra, options=options)
        if result.get('session'):
            _set(sid, engine_session=result['session'])
        for path, stamp in image_inventory(folder).items():
            if before_images.get(path) != stamp:
                event(sid, 'image', path=path)
        locked = _wait_readable(folder)
        if locked:
            event(sid, 'error', text=f"Windows won't let Apex open what the plan wrote ({', '.join(locked[:5])}), so this "
                  'step is not saved yet. Run `.venv\\Scripts\\python.exe scripts\\work_plans_check.py --code --only '
                  f"{'chatgpt' if engine == 'chatgpt' else 'claude'}` for the fix, then send a message to carry on.")
            files = 0
        else:
            files = _checkpoint(sid, start_sha, f'turn on your {we.NAMES[engine]}')
    except Exception as exc:                              # never leave a session marked working
        result = {'status': 'failed', 'summary': f'{type(exc).__name__}: {exc}'}
    finally:
        took = round(time.time() - started)
        total = _count_changed(sid)
        event(sid, 'done', status=result['status'], summary=(result.get('summary') or '')[:6000], seconds=took,
              files=files, total=total, engine=engine, tokens=result.get('tokens') or 0,
              reset_at=result.get('reset_at'), plan=bool((options or {}).get('plan')))
        fields = {'turn_state': 'idle', 'last_status': result['status'], 'files_changed': total,
                  'tokens': (session(sid).get('tokens') or 0) + (result.get('tokens') or 0)}
        with _lock:
            _live.pop(sid, None)
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
            _notify(sid, s['title'], result['status'], total)
        if asks:
            _ask_phone(sid, s['title'], engine, asks, night=s['origin'] == 'night')
        with _lock:                                       # last: "not working" means everything above is done
            _turns.pop(sid, None)


def _notify(sid: int, title: str, status: str, files: int) -> None:
    """A turn that took a while has ended: tell your devices, with what the proof
    says. Normal priority, so Apex's restraint can hold a 1 a.m. finish until you're
    around; tapping it opens the session. One per turn (the key counts the turns)."""
    body = (f'"{title}" is ready: {files} file{"s" if files != 1 else ""} changed; {_verdict_line(sid)}. '
            'Review it in Apex Code.' if status == 'done' else f'"{title}" stopped ({status}). Open Apex Code to see why.')
    try:
        turns = _rows("SELECT COUNT(*) AS n FROM code_events WHERE session_id=? AND kind='done'", (sid,))[0]['n']
        from agent import notify
        notify.notify('Apex · Code', body, kind='code', priority='normal', url=f'/code#s={sid}',
                      dedup_key=f'code:{sid}:done:{turns}')
    except Exception as exc:
        print(f'[Code] could not notify: {exc}')


def _verdict_line(sid: int) -> str:
    """The proof in a few words, for a notification: 'checks passed: 212 passed', or 'not verified yet'."""
    try:
        p = proof(sid)
    except Exception as exc:                              # a notification never fails over the proof
        print(f'[Code] could not read the proof for the notification: {exc}')
        return 'not verified yet'
    if p['verdict'] == 'proved':
        return f"checks passed: {p['checks']['why']}" if p['checks']['why'] else 'checks passed'
    if p['verdict'] == 'contradicted':
        return 'it says it works, but Apex saw it fail'
    return 'not verified yet'


# ---------------------------------------------------------------- away mode: answer from your phone

class NoSuchAllow(CodeError):
    """No blocked command has that link."""


def _allow_token(sid: int, command: str, asks: dict, night: bool = False) -> str | None:
    """A command Safe mode stopped gets a secret link your phone can answer, once,
    for two hours (one per command, MAX_ASKS commands a turn). None for a command
    that could never be allowed (allow() would refuse it), one past the limit (the
    page still has Allow once), or if it can't be stored."""
    command = str(command).strip()
    if not code_engines.ALLOWED_COMMAND.match(command):
        return None
    if command not in asks:
        if len(asks) >= MAX_ASKS:
            return None
        token, now = secrets.token_urlsafe(16), time.time()
        try:
            with longterm._conn() as db:
                db.execute('INSERT INTO code_pending_allows (id, session_id, command, created, expires) VALUES (?,?,?,?,?)',
                           (token, sid, command, now, now + (NIGHT_ALLOW_TTL if night else ALLOW_TTL)))
        except Exception as exc:                          # the step still shows, with Allow once on the page
            print(f'[Code] could not keep the request for your phone: {exc}')
            return None
        asks[command] = token
    return asks[command]


def _ask_phone(sid: int, title: str, engine: str, asks: dict, night: bool = False) -> None:
    """Safe mode stopped a command: ask on every device (high priority, so it is never
    held), at most MAX_ASKS a turn. The link opens a one-tap Allow once / Don't allow.
    A night-shift session asks at normal priority, so restraint holds a 3 a.m. ask
    until you're up; its link lasts until the morning."""
    try:
        from agent import notify
    except Exception as exc:
        print(f'[Code] could not ask your phone: {exc}')
        return
    for command, token in list(asks.items())[:MAX_ASKS]:
        # Every device, web push and Telegram get this: never a token in the command.
        shown = redact(command)
        shown = shown if len(shown) <= 120 else shown[:119] + '…'
        try:
            notify.notify('Apex · Code needs you',
                          f'"{redact(title)}": your {we.NAMES.get(engine, engine)} wants to run `{shown}`. Allow it once?',
                          kind='code', priority='normal' if night else 'high', url=f'/code#allow={token}',
                          dedup_key=f'code:{sid}:blocked:{hashlib.sha256(command.encode()).hexdigest()[:16]}')
        except Exception as exc:
            print(f'[Code] could not ask your phone: {exc}')


def pending_allow(token: str) -> dict:
    """What a phone link asks: the command, the session's title and project, its plan,
    until when, and whether it was answered. Nothing else about the session (no copy,
    no summary): any signed-in device with the link can read this."""
    rows = _rows('SELECT a.command, a.expires, a.answered, a.choice, s.title, s.engine, p.name AS project '
                 'FROM code_pending_allows a JOIN code_sessions s ON s.id = a.session_id '
                 'JOIN code_projects p ON p.id = s.project_id WHERE a.id = ?', (str(token),))
    if not rows:
        raise NoSuchAllow("Apex doesn't know that request. It may be from before a reinstall.")
    r = rows[0]
    # Shown redacted; Allow once still runs exactly the stored command.
    return {'command': redact(r['command']), 'title': r['title'], 'project': r['project'], 'engine': r['engine'],
            'expires': r['expires'], 'answered': r['answered'], 'choice': r['choice']}


def answer_allow(token: str, choice: str, who: str = '') -> dict:
    """Your answer from your phone, once. 'once' runs the command now (Allow once);
    'no' tells the plan, ahead of your next message, to find another way. Always
    allow is never offered here: a tap on a phone can't widen Safe mode for good.
    `who` (the device's address and browser) is kept for the audit."""
    if choice not in ('once', 'no'):
        raise CodeError("Answer 'once' or 'no'. Always allow is only on the Code page at your PC.")
    asked = pending_allow(token)
    if asked['answered'] or asked['expires'] <= time.time():
        raise CodeError('Already answered or expired.')
    row = _rows('SELECT session_id, command FROM code_pending_allows WHERE id=?', (token,))[0]
    sid, command = row['session_id'], row['command']
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away): there is nothing to allow.')
    if choice == 'once' and s['engine'] != 'claude':
        # Codex can't allow one command: it would get full access for the whole message.
        raise CodeError(f"This session is on your {we.NAMES.get(s['engine'], s['engine'])} now, which can't be allowed "
                        'just one command. Answer it on the Code page at your PC.')
    now = time.time()
    with longterm._conn() as db:                          # one answer only, even from two devices at once
        claimed = db.execute('UPDATE code_pending_allows SET answered=?, choice=?, who=? '
                             'WHERE id=? AND answered IS NULL AND expires>?',
                             (now, choice, str(who or '')[:200], token, now)).rowcount
    if claimed != 1:
        raise CodeError('Already answered or expired.')
    try:
        if choice == 'once':
            allow(sid, command, phone=str(who or 'your phone'))
        else:
            event(sid, 'note', text=f'You said no to `{command}` from your phone.')
            _add_note('id=?', (sid,), f'The owner declined `{command}`; find another way, or stop and explain.')
    except Exception:
        with longterm._conn() as db:                      # it didn't happen (say, still working): the link still works
            db.execute("UPDATE code_pending_allows SET answered=NULL, choice='', who='' WHERE id=? AND answered=?",
                       (token, now))
        raise
    return pending_allow(token)


def _settle_asks(sid: int, command: str, before: float, choice: str, who: str) -> None:
    """The command was allowed: any other link for it in this session (asked before
    now; the turn just started may ask again) is answered too, so a later tap on an
    old notification can't run it a second time."""
    with longterm._conn() as db:
        db.execute('UPDATE code_pending_allows SET answered=?, choice=?, who=? '
                   'WHERE session_id=? AND command=? AND answered IS NULL AND created<=?',
                   (time.time(), choice, who[:200], sid, command, before))


def _merging(folder) -> bool:
    return _git(folder, 'rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False).returncode == 0


LOCK_WAIT = 8.0                       # seconds to wait for a file Windows holds after a step


def _wait_readable(folder, seconds: float | None = None) -> list[str]:
    """Files the step changed that this account can't open yet. On Windows, Codex's
    sandbox (or a virus scan of a new file) can hold one for a moment: wait a few
    seconds before calling it a problem. Only changed files are looked at."""
    def locked():
        out = []
        for line in _git(folder, 'status', '--porcelain', '-uall', check=False).stdout.splitlines():
            path = line[3:].split(' -> ')[-1].strip().strip('"')
            f = Path(folder) / path
            if not path or not f.is_file():
                continue
            try:
                with f.open('rb') as fh:
                    fh.read(1)
            except PermissionError:
                out.append(path)
        return out
    end = time.time() + (LOCK_WAIT if seconds is None else seconds)
    still = locked()
    while still and time.time() < end:
        time.sleep(0.5)
        still = locked()
    return still


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
        ids = [x for x in (_turns.get(sid), _side.get(sid), _terms.get(sid)) if x]
    return any([we.stop(r, before_start=True) for r in ids])


def _idle(s: dict) -> None:
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away).')
    with _lock:
        if s['id'] in _turns or s['id'] in _side:
            raise CodeError('Apex is still working in this session. Wait, or press Stop.')
        if s['id'] in _terms:                  # it may still be changing the files: no Keep, no checks
            raise CodeError('Your command is still running. Wait, or press Stop.')


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
    _set(sid, conflict=0, files_changed=_count_changed(sid))
    _add_note('id=?', (sid,), 'Note: the owner undid your last step, so the files are back to how they were before it.')
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


def keep(sid: int, push: bool = False, require_proof: bool = False) -> dict:
    """Merge the session into the project, then tidy away its working copy. With
    require_proof, only when Apex's own checks passed on what is there now
    (proof): otherwise NotProved says why, and you can keep it anyway."""
    s = session(sid)
    _idle(s)
    try:
        verdict = proof(sid)
    except CodeError:
        raise
    except Exception as exc:                              # never a crash at the last step: unproved
        verdict = {'verdict': 'unverified', 'reasons': [f'Apex could not read the proof ({type(exc).__name__}: {exc}).']}
    if require_proof and verdict['verdict'] != 'proved':
        raise NotProved(verdict)
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
    code_brain.forget_file(sid)
    # What was kept, for the rest of Apex (agent/code_brain.py); before the event, so 'kept' stays the last one.
    learned = code_brain.write_back(s, 'kept', commit=merged, files=total, proof=verdict['verdict'])
    event(sid, 'kept', commit=merged[:12], into=target, files=total,
          restart=Path(repo).resolve() == APEX_ROOT, proof=verdict['verdict'], unverified=verdict['verdict'] != 'proved',
          learned=learned)
    _remove_copy(repo, folder)
    _git(repo, 'branch', '-d', s['branch'], check=False)
    if push:
        p = _git(repo, 'push', timeout=180, check=False)
        said = (p.stderr or p.stdout).strip()
        event(sid, 'pushed', ok=p.returncode == 0, into=target,
              text=('Pushed to GitHub.' if p.returncode == 0 else f'Kept, but the push failed: {said[-400:]}'))
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


DISCARD_REASONS = ('', *code_brain.REASONS)            # '' is "no reason given"


def discard(sid: int, reason: str = '') -> dict:
    """Throw the session away: its working copy and branch are deleted. `reason`
    (changed_mind, wrong, poor, superseded, or none) goes on the 'discarded'
    event and into Apex's memory with what the proof said."""
    reason = '' if reason is None else reason
    if not isinstance(reason, str) or reason not in DISCARD_REASONS:
        raise CodeError('Why throw it away: changed my mind, it was wrong, poor quality, something better came along, or no reason.')
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is already finished.')
    if stop(sid):
        for _ in range(50):
            with _lock:
                if sid not in _turns and sid not in _side:
                    break
            time.sleep(0.1)
    try:                                                  # read before the copy goes: the proof looks at it
        verdict = proof(sid)['verdict']
    except Exception as exc:
        print(f'[Code] could not read the proof of session {sid} before throwing it away: {type(exc).__name__}: {exc}')
        verdict = 'unverified'
    repo = s['project_path']
    _remove_copy(repo, s['worktree'])
    if s['branch']:
        _git(repo, 'branch', '-D', s['branch'], check=False)
    _set(sid, status='discarded')
    code_brain.forget_file(sid)
    learned = code_brain.write_back(session(sid), 'discarded', reason=reason, proof=verdict)
    event(sid, 'discarded', reason=reason, learned=learned)
    return session(sid)


# ---------------------------------------------------------------- second opinion and checks

REVIEW = """You are giving a second opinion on a code change another AI made in this git repository, for {owner}.
Be brutally honest: {owner} wants the truth, not flattery. Look for bugs, things that don't do what was asked,
missing or weak tests, security problems and needless complexity. Read any file you need, and run the project's
tests or checks if that helps you judge it. Do not change any file.

What {owner} asked for:
{asks}
{rules}
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


def _review_rules(s: dict, owner: str) -> str:
    """The rules for the second opinion to check (this project's active ones, and
    those for all code), and nothing else Apex knows about the owner: the
    reviewer stays independent."""
    from agent.working_context import redact
    out = ''
    for title, read in (('for this project', lambda: [r['text'] for r in code_brain.active_rules(s['project_id'])]),
                        ('for all code', lambda: [r['text'] for r in code_brain.global_rules()])):
        try:
            found = read()
        except Exception as exc:
            print(f'[Code] could not read the rules {title}: {type(exc).__name__}: {exc}')
            continue
        if found:
            lines = '\n'.join(f'- {redact(t)}' for t in found)
            out += f'\nRules {owner} gave {title} (flag any rule the change breaks):\n{lines}\n'
    return out


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
    prompt = REVIEW.format(owner=owner, asks=asks, rules=_review_rules(s, owner), cut=cut,
                           patch=patch[:REVIEW_DIFF_LIMIT])
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
                                       timeout=1200, env_extra=_venv(s['project_path']),
                                       options={'always': _project_allow(s), 'mcp_file': _memory_server(s)})
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
    """The project's test command, in the session's copy. The answer has three
    states, never two (agent/observed.py's rule): passed, failed, or unknown when
    it couldn't run, was stopped, or a test runner printed nothing Apex can read
    as a pass. Each says why, and which commit it ran on, so a later change
    makes it stale (see proof)."""
    from agent import observed
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
        if sid in _terms:
            raise CodeError('Your command is still running. Wait, or press Stop.')
        _side[sid] = run_id
    folder = s['worktree']
    try:                                                  # what they run on is a commit, so a later change shows
        _checkpoint(sid, _git(folder, 'rev-parse', 'HEAD').strip(), 'before the checks')
        sha = _git(folder, 'rev-parse', 'HEAD').strip()
    except Exception:
        with _lock:
            _side.pop(sid, None)
        raise
    _set(sid, check_state='running', check_output='', check_sha=sha, check_evidence='')
    event(sid, 'checks_started', command=proj['checks'])

    def go():
        started, out, state, why = time.time(), '', 'unknown', 'The checks stopped with an error.'
        timed_out = threading.Event()
        try:
            proc = subprocess.Popen(argv, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **we._hidden())
            we.track(run_id, proc)
            timer = threading.Timer(CHECK_TIMEOUT, lambda: (timed_out.set(), we._kill_tree(proc)))
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
            if stopped:
                out += '\n(stopped)'
            state, why = _check_result(proj, proc.returncode, out, stopped, timed_out.is_set())
        except (OSError, ValueError) as exc:
            out = why = f'Could not run {proj["checks"]!r}: {exc}'
        except Exception as exc:
            out = why = f'The checks stopped with an error: {type(exc).__name__}: {exc}'
        finally:
            took = round(time.time() - started)
            event(sid, 'checks', passed=state == 'passed', state=state, why=why, sha=sha, seconds=took,
                  output=out[-4000:], command=proj['checks'], left=_left_behind(folder))
            # The domain in lower case, as outcomes.record() files it: one project, one domain.
            observed.record(f"ran the checks of Apex Code session {sid} ({proj['checks'][:80]})", f'checks {state}: {why}',
                            {'passed': True, 'failed': False}.get(state), f"code:{proj['name']}".lower())
            # Last: once the session no longer says "running", everything above is done.
            _set(sid, check_state=state, check_output=out, check_sha=sha, check_evidence=why)
            with _lock:
                _side.pop(sid, None)
    threading.Thread(target=go, daemon=True, name=f'ApexCodeChecks-{sid}').start()
    return session(sid)


def _check_result(proj: dict, code: int, out: str, stopped: bool, timed_out: bool) -> tuple[str, str]:
    """(passed, failed or unknown; the evidence in a few words). A test runner that
    exits 0 but prints no count may have run nothing: that is unknown, not a pass,
    unless the project says exit 0 counts."""
    from agent import observed
    if stopped:
        return 'unknown', 'You stopped the checks before they finished.'
    if timed_out:
        took = f'{CHECK_TIMEOUT // 60} minutes' if CHECK_TIMEOUT >= 120 else f'{CHECK_TIMEOUT} seconds'
        return 'unknown', f'Still running after {took}, so Apex stopped them.'
    verdict, evidence = observed.test_verdict(out)
    if code != 0:
        return 'failed', f'exit {code}' + (f': {evidence}' if evidence else '')
    if verdict is False:
        return 'failed', evidence
    if verdict:
        return 'passed', evidence
    if not _runs_tests(proj['checks']):
        return 'passed', 'exit 0'
    if proj.get('checks_exit_ok'):
        return 'passed', 'exit 0 (this project counts exit 0 as a pass)'
    return 'unknown', 'Exit 0, but no test count in the output: maybe no tests ran.'


# A shell wrapper and the flag after which comes the command it runs.
SHELL_FLAG = {'bash': r'-[a-z]*c[a-z]*', 'sh': r'-[a-z]*c[a-z]*', 'zsh': r'-[a-z]*c[a-z]*', 'dash': r'-[a-z]*c[a-z]*',
              'powershell': r'-c(ommand)?', 'pwsh': r'-c(ommand)?', 'cmd': r'/c'}


def _program(command: str) -> tuple[str, str]:
    """(the program's bare name, the rest): '/usr/bin/python3' is 'python3' and
    '"C:\\Program Files\\Python\\python.exe"' is 'python'."""
    command = command.strip()
    if command[:1] in ('"', "'"):
        end = command.find(command[0], 1)
        prog, rest = (command[1:end], command[end + 1:]) if end > 0 else (command[1:], '')
    else:
        prog, _, rest = command.partition(' ')
    return re.sub(r'\.exe$', '', re.split(r'[\\/]', prog)[-1], flags=re.I), rest.strip()


def _wrapped(shell: str, rest: str) -> str | None:
    """The command a shell wrapper runs: bash -lc '<it>', powershell -Command <it>, cmd /c <it>."""
    try:
        words = shlex.split(rest, posix=shell not in ('powershell', 'pwsh', 'cmd'))
    except ValueError:
        return None
    for i, w in enumerate(words):
        if re.fullmatch(SHELL_FLAG[shell], w, re.I):
            inner = ' '.join(words[i + 1:]).strip()
            return inner[1:-1] if len(inner) > 1 and inner[0] == inner[-1] and inner[0] in '"\'' else inner
    return None


def _runs_tests(command: str, depth: int = 0) -> bool:
    """Is this command a test run? agent/observed.py decides, once each program is
    called by its bare name: its rule wants 'python -m pytest', so
    '/usr/bin/python3 -m pytest' or 'C:\\…\\python.exe -m pytest' wouldn't count.
    In a shell wrapper ('bash -lc "pytest -q"', how Codex runs every command),
    the command inside is what counts."""
    from agent import observed
    name, rest = _program(str(command or ''))
    if name.lower() in SHELL_FLAG and depth < 2:
        inner = _wrapped(name.lower(), rest)
        return inner is not None and _runs_tests(inner, depth + 1)
    named = [' '.join(x for x in _program(part) if x) for part in re.split(r'&&|\|\||;', str(command or ''))]
    return observed.looks_like_tests(' && '.join(named))


LEFT_LIMIT = 200                      # files the checks may leave behind and still be told apart


def _loose(folder) -> list[str]:
    """Files changed in the copy and not committed yet, new ones one by one. Without
    git's optional index lock: a turn's checkpoint may be adding files right then."""
    out = _git(folder, '--no-optional-locks', 'status', '--porcelain', '-uall', '--no-renames', check=False).stdout
    return [line[3:].strip().strip('"') for line in out.splitlines() if line[3:].strip()]


def _fingerprint(folder, paths: list[str]) -> dict:
    """path -> the hash of what is in it now ('' when it is gone)."""
    present = [p for p in paths if (Path(folder) / p).is_file()]
    hashes = []
    if present:
        hashes = _git(folder, 'hash-object', '--stdin-paths', input='\n'.join(present) + '\n', check=False).stdout.split()
    found = dict(zip(present, hashes)) if len(hashes) == len(present) else {}
    return {p: found.get(p, '') for p in paths}


def _left_behind(folder) -> dict:
    """What the checks left changed in the copy (a cache, a report), as it was when
    they finished: their result already includes it, so it doesn't make it stale.
    Too many to tell apart: none, and every change counts."""
    try:
        loose = _loose(folder)
        return _fingerprint(folder, loose) if len(loose) <= LEFT_LIMIT else {}
    except Exception as exc:
        print(f'[Code] could not see what the checks left behind: {exc}')
        return {}


def _changed_since(folder, sha: str, left: dict | None = None) -> list[str] | None:
    """Files whose content differs from what was checked at `sha`, committed or not.
    What the checks left behind doesn't count while it is unchanged. None when
    that commit can't be read."""
    r = _git(folder, 'diff', '--name-only', '--no-renames', sha, 'HEAD', check=False)
    if r.returncode != 0:
        return None
    paths = list(dict.fromkeys([p for p in r.stdout.splitlines() if p] + _loose(folder)))
    kept = {p: h for p, h in (left or {}).items() if p in paths}
    same = {p for p, h in _fingerprint(folder, list(kept)).items() if h == kept[p]} if kept else set()
    return [p for p in paths if p not in same]


# ---------------------------------------------------------------- proof: what the agent said vs what Apex saw

# What an agent says when it claims it checked its work. Only what it says is
# matched, word for word: Apex never puts a claim in its mouth.
CLAIMS = [re.compile(p, re.I) for p in (
    r'\b(all )?(the )?tests? (now )?pass(es|ed|ing)?\b',
    r'\b\d+ (tests? )?passed\b',
    r'\bran (the )?(tests|test suite|pytest|npm test|checks)\b',
    r'\b(verified|confirmed) (it|that|this|the fix)\b',
    r'\bbuild (passes|succeeded)\b')]
PASS_CLAIMS = (0, 1, 4)               # the ones that say it passed, not only that it ran
FAILS = re.compile(r'\bfail', re.I)    # "3 passed, 1 failed" reports a failure: no pass claim
# A test file: a change that edits tests and then passes them may have moved the goalposts.
TEST_FILE = re.compile(r'(^|/)(tests?/|test_[^/]+\.py$|[^/]+_test\.(py|go)$|[^/]+\.(test|spec)\.[jt]sx?$)')
# file:line in a review. Not after :// (a web address) or inside a longer word.
CITE = re.compile(r'(?<![\w/:.\\-])((?:[A-Za-z]:[\\/])?[\w./\\-]+\.\w+):(\d+)')
MAX_EVIDENCE = 100_000
PROOF_KINDS = ('you', 'text', 'tool', 'result', 'term', 'term_done', 'file', 'undo', 'checkpoint', 'checks',
               'owner_evidence', 'kept')


class NotProved(CodeError):
    """Keep was asked for proof and there is none: the proof says why."""
    def __init__(self, proof: dict):
        super().__init__('Not proved: ' + ' '.join(proof.get('reasons') or ['Apex has no proof it works.']))
        self.proof = proof


def _proof_events(sid: int) -> list[dict]:
    """Every event the proof reads, all of them (events() stops at 500)."""
    rows = _rows(f"SELECT id, ts, kind, data FROM code_events WHERE session_id=? AND kind IN "
                 f"({','.join('?' * len(PROOF_KINDS))}) ORDER BY id", (sid, *PROOF_KINDS))
    return [{**json.loads(r['data']), 'id': r['id'], 'ts': r['ts'], 'kind': r['kind']} for r in rows]


def _sentences(text: str) -> list[str]:
    out = []
    for line in str(text or '').splitlines():
        line = re.sub(r'^\s*(?:[-*•]|\d+[.)])\s+', '', line).strip()
        out += [x.strip() for x in re.split(r'(?<=[.!?])\s+', line) if x.strip()]
    return out


def _claims(texts: list[str]) -> list[dict]:
    """The sentences where the agent says it tested or verified its work, exactly as it said them."""
    out, seen = [], set()
    for text in texts:
        for sentence in _sentences(text):
            hits = [i for i, rx in enumerate(CLAIMS) if rx.search(sentence)]
            if hits and sentence not in seen:
                seen.add(sentence)
                out.append({'sentence': sentence[:400],
                            'pass_claim': any(i in PASS_CLAIMS for i in hits) and not FAILS.search(sentence)})
    return out[:8]


def _test_runs(evs: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """The test runs Apex saw in the feed: the agent's own (a command and its result)
    and yours (the terminal). Each with what its output says, the checkpoint its
    step made, and whether files changed after it. Also checkpoint sha -> number."""
    from agent import observed
    agent, mine, waiting, numbers, engine = [], [], {}, {}, ''

    def finish(run, output, ok, code=None):
        verdict, evidence = observed.test_verdict(output or '')
        run.update(ok=ok, verdict=verdict,
                   evidence=evidence or (f'exit {code}' if code not in (None, 0) else 'no test count' if ok else 'failed'))

    for e in evs:
        k = e['kind']
        if k == 'you':
            engine = e.get('engine') or engine
        if k in ('file', 'undo') or (k == 'checkpoint' and e.get('catch_up')):
            for r in agent + mine:
                r['stale'] = True
        if k == 'checkpoint':
            numbers[e.get('sha', '')] = len(numbers) + 1
            for r in agent + mine:
                r['checkpoint'] = r['checkpoint'] or len(numbers)
        elif k == 'tool' and e.get('tool') == 'command' and _runs_tests(e.get('title') or ''):
            run = {'by': 'agent', 'engine': engine, 'command': e.get('title') or '', 'ok': None, 'verdict': None,
                   'evidence': 'no result', 'checkpoint': None, 'stale': False, 'ts': e['ts']}
            agent.append(run)
            waiting[('agent', e.get('ref'))] = run
        elif k == 'result' and ('agent', e.get('ref')) in waiting:
            finish(waiting.pop(('agent', e.get('ref'))), e.get('output'), bool(e.get('ok')), e.get('exit_code'))
        elif k == 'term' and _runs_tests(e.get('command') or ''):
            run = {'by': 'you', 'engine': '', 'command': e.get('command') or '', 'ok': None, 'verdict': None,
                   'evidence': 'still running', 'checkpoint': None, 'stale': False, 'ts': e['ts']}
            mine.append(run)
            waiting[('you', e.get('ref'))] = run
        elif k == 'term_done' and ('you', e.get('ref')) in waiting:
            finish(waiting.pop(('you', e.get('ref'))), e.get('output'), e.get('exit_code') == 0, e.get('exit_code'))
    return agent[-6:], mine[-4:], numbers


def _live_head(s: dict) -> str:
    """The session's commit now, while its working copy exists; '' after Keep or Throw away."""
    if s['status'] != 'ready' or not s['worktree'] or not Path(s['worktree']).is_dir():
        return ''
    r = _git(s['worktree'], 'rev-parse', 'HEAD', check=False)
    return r.stdout.strip() if r.returncode == 0 else ''


def _plan_ready(engine: str) -> bool:
    try:
        from agent import work_agent
        return work_agent.available().get(engine) is None
    except Exception as exc:
        print(f'[Code] could not tell whether your {we.NAMES.get(engine, engine)} is ready: {exc}')
        return False


def _citations(s: dict, text: str, changed: set) -> list[dict]:
    """Each file:line the second opinion cites, labelled: in the change, elsewhere in
    the project, or not there at all (possibly invented)."""
    found = list(CITE.finditer(text or ''))
    if not found:
        return []
    try:
        files = set(tree(s['id'])['files'])
        folder = _folder_of(s['id'], None)
    except CodeError:
        files, folder = set(), None
    root = (s['worktree'] or '').replace('\\', '/').rstrip('/').lower()
    out, seen = [], set()
    for m in found:
        path, line = m.group(1).replace('\\', '/'), int(m.group(2))
        if root and path.lower().startswith(root + '/'):
            path = path[len(root) + 1:]
        path = re.sub(r'^\./', '', path)
        if path[:2] in ('a/', 'b/') and path not in files | changed and path[2:] in files | changed:
            path = path[2:]                                # git diff's a/ and b/
        if (path, line) in seen:
            continue
        seen.add((path, line))
        kind, label = (('in', 'in the change') if path in changed else ('out', 'outside the change') if path in files
                       else ('invented', 'file not found: possibly invented'))
        f = folder / path if folder and kind != 'invented' else None
        if f is not None and f.is_file():
            try:
                lines = len(f.read_text(encoding='utf-8', errors='replace').splitlines())
            except OSError:
                lines = line
            if line > max(lines, 1):
                kind, label = 'invented', f'no line {line} there ({lines} lines): possibly invented'
        out.append({'cite': f'{path}:{line}', 'file': path, 'line': line, 'kind': kind, 'label': label})
        if len(out) >= 30:
            break
    return out


def _review_proof(s: dict, evs: list[dict], changed: set, checks: dict) -> dict | None:
    """How far to trust the second opinion. Like genesis.check_critique, a plan
    reviewing work it did itself shares its blind spots; read on its own here,
    because that gate fails on a FATAL objection whatever the independence."""
    if s['review_state'] != 'done' or not s['review_engine']:
        return None
    authors = {e['engine'] for e in evs if e['kind'] == 'you' and e.get('engine')} or {s['engine']}
    other = next(e for e in ENGINES if e != s['review_engine'])
    if s['review_engine'] not in authors:
        independence, label = 'independent', 'independent: other plan'
    elif _plan_ready(other):
        independence, label = 'correlated', 'correlated: same plan reviewed itself'
    else:
        independence, label = 'weaker', 'weaker: only one plan signed in'
    rating, disagreement = s['review_rating'], ''
    if rating is not None and rating >= 8 and checks['state'] == 'failed':
        disagreement = f"The second opinion rates it {rating}/10, but Apex's checks failed ({checks['why']})."
    elif rating is not None and rating <= 5 and checks['state'] == 'passed':
        disagreement = f"Apex's checks passed, but the second opinion rates it {rating}/10: read what it found."
    return {'engine': s['review_engine'], 'rating': rating, 'independence': independence, 'label': label,
            'citations': _citations(s, s['review_text'], changed), 'disagreement': disagreement}


def proof(sid: int) -> dict:
    """What the agent said it did to check its work, next to what Apex saw for
    itself: the agent's own test runs, yours, and Apex's checks on a known
    commit. Observed evidence beats what anyone says (agent/observed.py): the
    verdict is 'proved' only when Apex's own checks passed on what is there now,
    'contradicted' when the agent says it passes and Apex saw it fail, and
    'unverified' otherwise, with the reasons in plain words."""
    s = session(sid)
    evs = _proof_events(sid)
    texts = [e['text'] for e in evs if e['kind'] == 'text' and e.get('text')]
    claims = _claims([s['summary']] + texts[-1:])
    agent, mine, numbers = _test_runs(evs)
    head = _live_head(s)
    folder = s['worktree'] if head else None
    last = next((e for e in reversed(evs) if e['kind'] == 'checks'), None)
    try:
        proj_checks = project(s['project_id'])['checks']
    except CodeError:
        proj_checks = ''
    state, sha = s['check_state'] or 'none', s['check_sha']
    stale, since = None, []
    if state in ('passed', 'failed', 'unknown'):
        if not sha:
            stale = True                                  # run before Apex noted which commit: can't say what it covered
        elif folder:
            since = _changed_since(folder, sha, (last or {}).get('left'))
            stale, since = (True, []) if since is None else (bool(since), since)
    checks = {'state': state, 'why': s['check_evidence'], 'sha': sha, 'checkpoint': numbers.get(sha),
              'command': (last or {}).get('command') or proj_checks, 'stale': stale, 'changed_since': since,
              'seconds': (last or {}).get('seconds'), 'ts': (last or {}).get('ts')}
    reported = next((e for e in reversed(evs) if e['kind'] == 'owner_evidence'), None)
    owner = None
    if reported:
        moved = _changed_since(folder, reported['sha']) if folder and reported.get('sha') else None
        owner = {'source': 'owner-reported', 'verdict': reported.get('verdict'), 'evidence': reported.get('evidence', ''),
                 'sha': reported.get('sha', ''), 'stale': None if moved is None else bool(moved), 'ts': reported['ts']}
    try:
        changed = {f['path'] for f in changes(sid)['files']}
    except CodeError:
        changed = set()
    goalpost = sorted(p for p in changed if TEST_FILE.search(p)) if state == 'passed' else []
    review = _review_proof(s, evs, changed, checks)
    verdict, reasons = _verdict(s, claims, agent, checks, owner, evs)
    return {'verdict': verdict, 'reasons': reasons, 'claims': claims, 'saw_agent': agent, 'saw_you': mine,
            'checks': checks, 'owner_evidence': owner, 'goalpost': goalpost, 'review': review,
            'engine': s['engine'], 'head': head}


def _verdict(s, claims, agent, checks, owner, evs) -> tuple[str, list[str]]:
    said = next((c['sentence'] for c in claims if c['pass_claim']), '')
    who = f"Your {we.NAMES.get(s['engine'], s['engine'])}"
    where = f"checkpoint {checks['checkpoint']}" if checks['checkpoint'] else f"commit {checks['sha'][:8]}"
    last = agent[-1] if agent else None
    kept = next((e for e in reversed(evs) if e['kind'] == 'kept'), None)
    if kept and s['status'] == 'kept':                 # its copy is gone: what was known when it was kept
        verdict = kept.get('proof') or 'unverified'
        return verdict, ['Kept with proof: Apex\'s checks had passed on what was kept.' if verdict == 'proved'
                         else 'Kept without proof: nothing Apex saw showed it works.']
    if said and checks['state'] == 'failed' and checks['stale'] is False:
        return 'contradicted', [f'{who} said "{said}", but Apex\'s checks failed on {where}: {checks["why"]}.']
    if said and last and last['verdict'] is False and not last['stale']:
        return 'contradicted', [f'{who} said "{said}", but its own last test run failed: {last["evidence"]}.']
    if checks['state'] == 'passed' and checks['stale'] is False:
        return 'proved', [f"Apex ran {checks['command']} on {where}: {checks['why']}."]
    reasons = []
    n = len(checks['changed_since'])
    names = ', '.join(checks['changed_since'][:5]) + (' …' if n > 5 else '')
    did = {'passed': 'passed', 'failed': 'failed', 'unknown': "couldn't decide"}.get(checks['state'], '')
    if checks['state'] == 'none':
        reasons.append("Apex hasn't run the checks on this change yet." if checks['command']
                       else 'This project has no checks command yet: set one, then run the checks.')
    elif checks['state'] == 'running':
        reasons.append('The checks are still running.')
    elif checks['stale'] is None:
        reasons.append(f"The checks {did} ({checks['why'] or 'no detail'}), and this session's copy is gone, so Apex can't compare.")
    elif checks['stale'] and n:
        reasons.append(f"The checks {did} on {where}, but {n} file{'s' if n != 1 else ''} changed since: {names}. "
                       'That result is stale: run them again.')
    elif checks['stale']:
        reasons.append(f"The checks {did} on an earlier version Apex can't compare: run them again.")
    elif checks['state'] == 'failed':
        reasons.append(f"Apex's checks failed on {where}: {checks['why']}.")
    else:
        reasons.append(f"The checks couldn't decide: {checks['why']}")
    if last and last['verdict'] is not None:
        reasons.append(f"Its own test run showed {last['evidence']}"
                       + (', and it changed files after.' if last['stale'] else ": its run, not Apex's checks."))
    elif claims:
        reasons.append(f'{who} said "{said or claims[0]["sentence"]}", and Apex saw no test run that shows it.')
    if owner:
        reasons.append(f"You reported {owner['evidence'] or 'output without a test count'}: noted, but Apex didn't see it run.")
    return 'unverified', reasons


def owner_evidence(sid: int, output: str) -> dict:
    """You ran the checks yourself and pasted what they printed. Kept as
    owner-reported: never relabelled observed, and never enough for 'proved'."""
    from agent import observed
    output = str(output or '').strip()
    if not output:
        raise CodeError('Paste what the command printed.')
    if len(output) > MAX_EVIDENCE:
        raise CodeError(f'That is more than {MAX_EVIDENCE:,} characters: paste the end of it, with the summary line.')
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away).')
    verdict, evidence = observed.test_verdict(output)
    event(sid, 'owner_evidence', source='owner-reported', verdict=verdict, evidence=evidence, sha=_live_head(s),
          output=output[-4000:])
    return proof(sid)


# ---------------------------------------------------------------- Celine on the build
# Ask Celine about a session, out loud or typed (dashboard/companion.py with
# workspace 'code', and core's code_status / code_act tools). She gets what Apex
# saw, never the diff's text: everything here was written by a coding agent or a
# repository, so it is data for her to read, not instructions to follow. She can
# draft a message; only the owner sends it.

VOICE_KINDS = ('done', 'checks', 'review', 'blocked', 'kept', 'discarded')
VOICE_EVENTS = 12                     # the last steps she hears about
VOICE_CUT = 600                       # characters of any one text in them
VOICE_LIVE = 1500                     # characters of what the plan is typing right now
MAX_DRAFT = 4000


def _cut(value, n: int = VOICE_CUT):
    if isinstance(value, str):
        return value if len(value) <= n else value[:n] + '…'
    return value


def for_voice(sid: int) -> dict:
    """One session as Celine hears it: the session, the proof, which files changed
    (counts only, no hunks), its last milestones, what the plan is typing now and
    the owner's rules. Raises CodeError (a ValueError) for an unknown session."""
    s = session(sid)
    out = {'session': {k: s.get(k) for k in ('id', 'title', 'project', 'branch', 'engine', 'engine_name', 'status',
                                               'turn_state', 'working', 'files_changed', 'review_rating',
                                               'review_engine')},
           'summary': _cut(s.get('summary') or '')}
    try:
        p = proof(sid)
        c = p['checks']
        out['proof'] = {'verdict': p['verdict'], 'reasons': p['reasons'],
                        'checks': {'state': c['state'], 'why': _cut(c['why'] or ''), 'stale': c['stale'],
                                   'changed_since': (c['changed_since'] or [])[:20]},
                        'claims': [x['sentence'] for x in p['claims']],
                        'review': p['review'] and {k: p['review'][k] for k in ('engine', 'rating', 'independence',
                                                                                'label', 'disagreement')}}
    except Exception as exc:                              # a gone copy or a git hiccup: say so, never guess
        out['proof'] = {'verdict': 'unknown', 'reasons': [f'Apex could not read the proof: {type(exc).__name__}']}
    try:
        ch = changes(sid)
        out['changes'] = {'files': [{k: f[k] for k in ('path', 'plus', 'minus', 'change')} for f in ch['files'][:60]],
                          'count': len(ch['files']), 'plus': ch['plus'], 'minus': ch['minus']}
    except Exception:
        out['changes'] = {'files': [], 'count': s.get('files_changed') or 0, 'note': "the session's copy is gone"}
    rows = _rows(f"SELECT id, ts, kind, data FROM code_events WHERE session_id=? AND kind IN "
                 f"({','.join('?' * len(VOICE_KINDS))}) ORDER BY id DESC LIMIT ?", (sid, *VOICE_KINDS, VOICE_EVENTS))
    steps = []
    for r in reversed(rows):
        data = json.loads(r['data'])
        data.pop('left', None)                            # the checks' file fingerprints: noise to her
        data.pop('allow_id', None)                        # a phone link's secret: never hers
        out_tail = data.pop('output', None)
        step = {'kind': r['kind'], 'ts': round(r['ts']), **{k: _cut(v) for k, v in data.items()}}
        if isinstance(out_tail, str):                     # the end of the output says what happened
            step['output'] = '…' + out_tail[-VOICE_CUT:] if len(out_tail) > VOICE_CUT else out_tail
        steps.append(step)
    out['steps'] = steps
    out['typing_now'] = live(sid)['text'][-VOICE_LIVE:] if s.get('working') else ''
    try:
        out['rules'] = {'project': [x['text'] for x in code_brain.rules(s['project_id'])['items'] if x.get('active')],
                        'all_code': [x['text'] for x in code_brain.global_rules()]}
    except Exception:
        out['rules'] = {'project': [], 'all_code': []}
    return out


def voice_list(limit: int = 12) -> list[dict]:
    """Recent sessions, one line each, for "how's the build going?"."""
    rows = _rows('SELECT s.id, s.title, p.name AS project, s.status, s.check_state, s.review_rating, s.updated '
                 'FROM code_sessions s JOIN code_projects p ON p.id = s.project_id ORDER BY s.updated DESC LIMIT ?',
                 (limit,))
    with _lock:
        for r in rows:
            r['working'] = r['id'] in _turns
    return rows


def draft(sid: int, text: str, by: str = 'Celine') -> dict:
    """A message for the plan, written by Celine and shown to the owner with Send,
    Edit and ✕. Recorded only: it never reaches the plan unless the owner sends it."""
    text = str(text or '').strip()
    if not text:
        raise CodeError('A draft needs some text.')
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away).')
    eid = event(sid, 'draft', text=text[:MAX_DRAFT], by=str(by or 'Celine')[:40])
    return {'id': eid, 'session_id': sid, 'text': text[:MAX_DRAFT], 'sent': False}


# ---------------------------------------------------------------- allow, files, terminal, history

def _project_allow(s: dict) -> list[str]:
    try:
        return [c for c in json.loads(project(s['project_id']).get('allow') or '[]') if isinstance(c, str)]
    except (ValueError, CodeError):
        return []


def allow(sid: int, command: str, always: bool = False, phone: str = '') -> dict:
    """Safe mode blocked a command: run it now (Allow once), or let this project's
    sessions run it, with any arguments, from now on (Always allow). `phone` is the
    device that answered from a notification (answer_allow), which only allows once."""
    command = str(command or '').strip()
    if not code_engines.ALLOWED_COMMAND.match(command):
        raise CodeError('That command can\'t be allowed: one line, without brackets, at most 300 characters.')
    s = session(sid)
    if always and not phone:
        rules = _project_allow(s)
        if command not in rules:
            rules.append(command)
            with longterm._conn() as db:
                db.execute('UPDATE code_projects SET allow=? WHERE id=?', (json.dumps(rules[-50:]), s['project_id']))
    asked_before = time.time()
    done = send(sid, f'I allowed `{command}`. Run it now, then carry on with what you were doing.',
                allow=[command], _notes=[f'You allowed `{command}` once from your phone.'] if phone else ())
    _settle_asks(sid, command, asked_before, 'once' if phone else 'pc', phone or 'the Code page')
    return done


def forget_allowed(pid: int, command: str) -> dict:
    rules = [c for c in json.loads(project(pid).get('allow') or '[]') if c != command]
    with longterm._conn() as db:
        db.execute('UPDATE code_projects SET allow=? WHERE id=?', (json.dumps(rules), pid))
    return project(pid)


TREE_LIMIT = 20000
FILE_LIMIT = 512_000


def image_inventory(folder):
    """Bounded raster inventory, including Codex's generated_images artifacts."""
    root = Path(folder).resolve()
    out = {}
    directory = root / 'generated_images'
    if not directory.resolve().is_relative_to(root):
        return out
    for index, path in enumerate(directory.rglob('*')):
        if index >= 1000:
            break
        if '.git' in path.parts or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            continue
        if path.is_file() and path.resolve().is_relative_to(root):
            stat = path.stat()
            out[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return out
LANGS = {'.py': 'python', '.js': 'js', '.cjs': 'js', '.mjs': 'js', '.ts': 'js', '.tsx': 'js', '.jsx': 'js',
         '.json': 'json', '.css': 'css', '.html': 'html', '.md': 'md', '.yml': 'yaml', '.yaml': 'yaml',
         '.toml': 'toml', '.sh': 'shell', '.cmd': 'shell', '.bat': 'shell', '.ps1': 'shell', '.sql': 'sql'}


def _folder_of(sid: int | None, pid: int | None) -> Path:
    if sid is not None:
        s = session(sid)
        if s['worktree'] and Path(s['worktree']).is_dir():
            return Path(s['worktree'])
        return Path(s['project_path'])
    return Path(project(pid)['path'])


def tree(sid: int | None = None, pid: int | None = None) -> dict:
    """Every file git knows or would add (ignored files left out), for the file
    tree and @mentions."""
    folder = _folder_of(sid, pid)
    out = _git(folder, 'ls-files', '--cached', '--others', '--exclude-standard', timeout=60)
    files = sorted({l for l in out.splitlines() if l})
    files = sorted(set(files) | set(image_inventory(folder)))
    return {'files': files[:TREE_LIMIT], 'cut': len(files) > TREE_LIMIT, 'root': str(folder)}


def read_file(path: str, sid: int | None = None, pid: int | None = None) -> dict:
    """One file's text for the viewer. Only files in the tree: never anything else on the PC."""
    folder = _folder_of(sid, pid)
    path = str(path or '').replace('\\', '/')
    if path not in set(tree(sid, pid)['files']):
        raise CodeError('That file is not in this project.')
    f = (folder / path)
    if not f.resolve().is_relative_to(folder.resolve()):
        raise CodeError('That file points outside this project.')
    if not f.is_file():
        raise CodeError('That file was deleted.')
    size = f.stat().st_size
    if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') and size <= 10_000_000:
        import base64
        import io
        from PIL import Image
        raw = f.read_bytes()
        try:
            with Image.open(io.BytesIO(raw)) as image:
                mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}.get(image.format)
                image.verify()
            if mime:
                return {'path': path, 'binary': True, 'too_big': False, 'size': size,
                        'image': {'mime': mime, 'base64': base64.b64encode(raw).decode('ascii')}, 'text': '', 'lang': ''}
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    if size > FILE_LIMIT:
        return {'path': path, 'binary': False, 'too_big': True, 'size': size, 'text': '', 'lang': ''}
    raw = f.read_bytes()
    if b'\0' in raw[:4096]:
        return {'path': path, 'binary': True, 'too_big': False, 'size': size, 'text': '', 'lang': ''}
    return {'path': path, 'binary': False, 'too_big': False, 'size': size,
            'text': raw.decode('utf-8', errors='replace'), 'lang': LANGS.get(Path(path).suffix.lower(), '')}


TERMINAL_TIMEOUT = 900


def terminal(sid: int, command: str) -> dict:
    """Run your own command in the session's copy, like a terminal there. Output
    streams into the feed. One at a time per session."""
    command = str(command or '').strip()
    if not command or len(command) > 2000:
        raise CodeError('Type a command (one line, at most 2000 characters).')
    s = session(sid)
    if s['status'] != 'ready':
        raise CodeError('This session is finished (kept or thrown away).')
    run_id = f'term-{sid}-{int(time.time() * 1000)}'
    with _lock:
        if sid in _terms:
            raise CodeError('Your last command is still running. Wait, or press Stop.')
        # A change made while the checks run would count as checked, and Keep would take it.
        if sid in _turns or sid in _side:
            raise CodeError('Apex is still working in this session. Wait, or press Stop.')
        _terms[sid] = run_id
    ref = run_id
    event(sid, 'term', command=command, ref=ref)
    argv = ['cmd.exe', '/d', '/s', '/c', command] if os.name == 'nt' else ['bash', '-lc', command]
    env = we._env()
    env.update(_venv(s['project_path']))

    def go():
        started, out, code = time.time(), [], None
        try:
            proc = subprocess.Popen(argv, cwd=s['worktree'], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **we._hidden())
            we.track(run_id, proc)
            timer = threading.Timer(TERMINAL_TIMEOUT, lambda: we._kill_tree(proc))
            timer.daemon = True
            timer.start()
            for line in proc.stdout:
                out.append(line)
                if len(out) > 4000:
                    del out[:2000]
                _feed_live(sid, {'kind': 'live', 'ref': ref, 'output': ''.join(out)[-4000:]})
            proc.wait()
            timer.cancel()
            stopped = we.untrack(run_id)
            code = proc.returncode
            if stopped:
                out.append('\n(stopped)')
        except (OSError, ValueError) as exc:
            out.append(f'Could not run it: {exc}')
        finally:
            text = ''.join(out)
            _feed_live(sid, {'kind': 'result', 'id': ref})
            event(sid, 'term_done', ref=ref, exit_code=code, output=text[-6000:], seconds=round(time.time() - started))
            with _lock:
                _terms.pop(sid, None)
    threading.Thread(target=go, daemon=True, name=f'ApexCodeTerm-{sid}').start()
    return session(sid)


def history(sid: int) -> list[dict]:
    """The session's checkpoints, newest first, with what each changed."""
    s = session(sid)
    undone = {e['sha'] for e in events(sid) if e['kind'] == 'undo'}
    out = []
    for e in reversed([e for e in events(sid) if e['kind'] == 'checkpoint']):
        out.append({'sha': e['sha'], 'short': e['sha'][:8], 'files': e.get('files', 0), 'ts': e['ts'],
                    'undone': e['sha'] in undone, 'catch_up': bool(e.get('catch_up'))})
    return out


def commit_diff(sid: int, sha: str) -> str:
    """One checkpoint's changes. Only the session's own checkpoints."""
    s = session(sid)
    if sha not in {e['sha'] for e in events(sid) if e['kind'] == 'checkpoint'}:
        raise CodeError('That is not one of this session\'s checkpoints.')
    folder = s['worktree'] if s['worktree'] and Path(s['worktree']).is_dir() else s['project_path']
    text = _git(folder, 'show', '--no-renames', '--format=%s%n', sha, check=False).stdout
    return text[:DIFF_LIMIT] + (f'\n… (cut: {len(text):,} characters)' if len(text) > DIFF_LIMIT else '')


# ---------------------------------------------------------------- the night shift (agent/work_agent.py)
# A Work task marked +apex, in a software project linked to an Apex Code project,
# is taken overnight as a real session (origin 'night'). After its turn, Apex runs
# the checks and has the other plan review it, one step per Work tick, read from the
# database so a restart carries on. It never keeps anything: the morning brief says
# what Apex saw, and the owner decides.

def _checks_command(s: dict) -> str:
    rows = _rows('SELECT checks FROM code_projects WHERE id=?', (s['project_id'],))
    return rows[0]['checks'] if rows else ''


def settled(s: dict) -> bool:
    """After a finished turn: nothing left for the night shift to do (the checks ran,
    or there are none; the second opinion came back, failed or was skipped)."""
    if not s['files_changed']:
        return True                                       # nothing changed: nothing to check or review
    if s['check_state'] in ('', 'running') and _checks_command(s):
        return False
    return s['review_state'] in ('done', 'failed', 'skipped')


def _other_plan(engine: str) -> str:
    return 'chatgpt' if engine == 'claude' else 'claude'


def autopilot(sid: int) -> str:
    """One step of a night session after its turn: run the checks once, then ask
    the other plan for a second opinion if it is free (otherwise note that and skip
    it). Never Keep. What it did: 'checks', 'review', 'skipped', or '' (nothing to do now)."""
    s = session(sid)
    if s['status'] != 'ready' or s['origin'] != 'night' or s['working'] or s['side'] or s['turn_state'] != 'idle' \
            or s['last_status'] != 'done' or settled(s):
        return ''
    command = _checks_command(s)
    if s['check_state'] == '' and command:
        try:
            run_checks(sid)
        except CodeError as exc:
            with _lock:
                if sid in _turns or sid in _side:         # something else started a moment ago
                    return ''
            why = f"The night shift couldn't start the checks: {exc}"
            event(sid, 'checks', passed=False, state='unknown', why=why, sha='', seconds=0, output='', command=command)
            _set(sid, check_state='unknown', check_evidence=why)
        return 'checks'
    if s['check_state'] == 'running' or s['review_state'] not in ('', None):
        return ''
    other = _other_plan(s['engine'])
    try:
        from agent import work_agent
        why = work_agent.available().get(other)
    except Exception as exc:
        why = f'could not be checked ({type(exc).__name__})'
    if not why:
        try:
            review(sid, other)
            return 'review'
        except CodeError as exc:
            with _lock:
                if sid in _turns or sid in _side:
                    return ''
            why = str(exc)
    note = ('No independent review: the other plan is resting.' if 'resting' in why or 'limit' in why
            else f'No independent review: your {we.NAMES[other]} {why.rstrip(".")}.')
    event(sid, 'note', text=note)
    _set(sid, review_state='skipped', review_text=note)
    return 'skipped'


def outcome_text(sid: int) -> str:
    """What a night session came to, for its Work task: the plan's summary, the
    proof and the second opinion."""
    s = session(sid)
    try:
        p = proof(sid)
    except Exception as exc:
        p = {'verdict': 'unverified', 'reasons': [f'Apex could not read the proof ({type(exc).__name__}).'],
             'checks': {'state': 'none', 'why': ''}}
    c = p['checks']
    why = c['why'] if c['state'] in ('passed', 'failed', 'unknown') and c['why'] else (p['reasons'] or [''])[0]
    if s['review_rating'] is not None:
        rated = f"review {s['review_rating']}/10 by your {we.NAMES.get(s['review_engine'], s['review_engine'])}"
    else:
        rated = 'no independent review' if s['review_state'] == 'skipped' else 'no second opinion'
    if not s['files_changed']:
        rated = 'nothing changed'
    # Leaves Apex Code for a Work task any device can read, and its notification.
    return redact(f"{(s['summary'] or '').strip()[:600]}\nProof: {p['verdict']} ({why.rstrip('.')}); {rated}".strip())


def _since_last_brief(now: float) -> float:
    """When the last morning brief before today went out (agent/work_agent.py), else a day ago."""
    day = time.localtime(now)
    today = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))
    try:
        rows = _rows("SELECT MAX(ts) AS ts FROM work_events WHERE kind='brief' AND ts < ?", (today,))
    except Exception:                                     # Work's tables not made yet
        rows = []
    return rows[0]['ts'] if rows and rows[0]['ts'] else now - 86400


def _night_in_progress(s: dict) -> bool:
    """The night shift still means to finish this session (its checks and second
    opinion): its Work task is still on it. Not once you stopped it from Work."""
    try:
        rows = _rows("SELECT 1 FROM work_tasks WHERE id=? AND apex_run=? AND apex_state IN ('queued','running','verifying')",
                     (s.get('task_id'), f"code-{s['id']}"))
    except Exception:                                     # Work's tables not made yet
        return False
    return bool(rows)


def overnight(since: float | None = None, now: float | None = None) -> list[dict]:
    """What the night shift built, for the morning: every night session still
    waiting for your decision, and any other open session that moved since the
    last brief. Each with the proof's verdict and why, the second opinion, the
    files and branch, and whether it is still working."""
    now = now or time.time()
    since = _since_last_brief(now) if since is None else since
    ids = [r['id'] for r in _rows("SELECT id FROM code_sessions WHERE status='ready' AND (origin='night' OR updated >= ?) "
                                  'ORDER BY created', (since,))]
    out = []
    for sid in ids:
        s = session(sid)
        working = bool(s['working'] or s['side'] or s['turn_state'] != 'idle' or not s['last_status']
                       or (s['origin'] == 'night' and s['last_status'] == 'done' and not settled(s)
                           and _night_in_progress(s)))
        try:
            p = proof(sid)
        except Exception as exc:
            print(f'[Code] could not read the proof of session {sid} for the morning: {type(exc).__name__}: {exc}')
            p = {'verdict': 'unverified', 'reasons': [], 'claims': [], 'checks': {'state': 'none', 'why': ''}}
        out.append({'id': sid, 'title': s['title'], 'project': s['project'], 'project_id': s['project_id'],
                    'origin': s['origin'], 'task': s['task'], 'engine': s['engine'], 'engine_name': s['engine_name'],
                    'last_status': s['last_status'], 'working': working, 'verdict': p['verdict'],
                    'reasons': p['reasons'][:3], 'checks': p['checks']['state'], 'why': p['checks']['why'] or '',
                    'claimed': any(c.get('pass_claim') for c in p.get('claims', [])),
                    'rating': s['review_rating'], 'review_state': s['review_state'], 'review_engine': s['review_engine'],
                    'review_engine_name': we.NAMES.get(s['review_engine'], s['review_engine']) if s['review_engine'] else '',
                    'files_changed': s['files_changed'], 'branch': s['branch'], 'updated': s['updated']})
    return out


# ---------------------------------------------------------------- the page's overview

def _record(pid: int) -> dict:
    """The home page's lines for one project (agent/code_brain.track_record): the
    top few, each plan's own line for the composer, and the note when there are none."""
    try:
        r = code_brain.track_record(pid)
    except Exception as exc:
        print(f'[Code] could not count the sessions of project {pid}: {type(exc).__name__}: {exc}')
        return {'lines': [], 'engines': {}, 'decided': 0, 'note': "Apex couldn't count this project's sessions just now."}
    return {'lines': r['lines'][:code_brain.RECORD_LINES], 'engines': r['engines'], 'decided': r['decided'], 'note': r['note']}


def overview() -> dict:
    """Projects, recent sessions, your plans, this week's numbers and what tends
    to happen on each project."""
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
    # Kept with proof (the 'kept' event's proof), matched as event() writes it.
    proved = _rows("SELECT COUNT(*) AS n FROM code_events e JOIN code_sessions s ON s.id = e.session_id "
                   "WHERE e.kind = 'kept' AND s.created >= ? AND e.data LIKE ?", (week, '%"proof": "proved"%'))[0]['n']
    with _lock:
        working = len(_turns)
    found = projects()
    return {
        'owner': getattr(config, 'OWNER_NAME', '') or '',
        'projects': found,
        'sessions': sessions(),
        'plans': [{'id': e, 'name': we.NAMES[e], 'ready': not free.get(e), 'why': free.get(e) or '',
                   'how': we.check(e)['how'] if free.get(e) else ''} for e in ENGINES],
        'default_engine': default,
        'week': {'sessions': len(recent), 'kept': sum(1 for r in recent if r['status'] == 'kept'), 'proved': proved,
                 'rating': round(sum(rated) / len(rated), 1) if rated else None,
                 'minutes': round(minutes), 'credits': 0},
        'record': {p['id']: _record(p['id']) for p in found},
        'working': working,
    }
