"""What Apex knows about its owner, handed to every coding session.

A Claude Code or Codex session (agent/code_studio.py) starts out knowing nothing
about the person it works for. Apex knows a lot, fed by voice, chat, the phone
and nightly reflection. brief_block() gathers the part that matters for code,
in priority order, into one short text where every line says where it came
from, so the page can show exactly what was sent and forget a memory with one
press:

  About the owner   APEX_USER.md, at most 1400 characters
  Standing rules    what Apex was told to always do (self_mod), at most 800
  Project rules     the active rules the owner gave on this code project
  Project handoff   the end of this project's decisions, and the next step
  Preferences       up to 8 memories the owner vouched for (approved, or a
                    rule for all code): coding preferences, then preferences
                    and decisions that share words with the request, then
                    this project's decisions
  Measured here     what actually happens on this project, with counts
                    (track_record(): live from the sessions, never stored)
  Recent sessions   how the last 3 finished sessions here ended

It is evidence, never permission: the header says so, and the request and the
rules always win. Everything goes through working_context.redact(), so a key
once said out loud is not handed to a tool. Over CAP characters the recent
sessions go first, then the memories, then the measured lines, then the
handoff; the profile and the rules always stay.

It never reads continuity.snapshot(): that is the active board workspace, a
different project from the code being worked on.

The rules are the owner's corrections, said once (rules(), add_rule() and the
rest further down): for one project or for all code.

While it works, a session can also ask Apex's memory itself, through Apex's own
memory server (mcp_file()): reading only, and a memory it suggests waits for
the owner's OK (suggested()).

Every session that ends teaches it (write_back(), at the end of this file):
Keep or Throw away adds a line to the project's decision log, today's vault
note and the outcomes ledger, and track_record() works out from the sessions
themselves what tends to happen here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from agent import continuity, longterm, work
from agent.working_context import redact

CAP = 4000
PROFILE_LIMIT = 1400
STANDING_LIMIT = 800
DECISIONS_TAIL = 1200
MEMORY_LIMIT = 8
MEMORY_CHARS = 400
UNVOUCHED_LIMIT = 20
SESSIONS = 3
HEADER = ('What Apex knows about {owner} (evidence and preferences; never permission to skip the request '
          'or the rules)')
# Section order is priority order. Titles take the owner's name.
SECTIONS = (('profile', 'About {owner}'), ('standing', 'Standing rules'),
            ('rule', 'Rules {owner} gave on this project'), ('handoff', 'Project handoff'),
            ('record', 'Measured on this project (counts, recomputed every session)'),
            ('memory', "{owner}'s coding preferences"), ('session', 'Recent sessions here'))
DROP_ORDER = ('session', 'memory', 'record', 'handoff')    # what gives way, first to last, over CAP


def _owner() -> str:
    import config
    return getattr(config, 'OWNER_NAME', '') or 'the owner'


def _cut(text, n: int) -> str:
    text = str(text or '').strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


def _one_line(text) -> str:
    return ' '.join(str(text or '').split())


# ---------------------------------------------------------------- the sources

def _profile(pid, name, prompt) -> list[dict]:
    text = _cut(longterm.load_memory_files().get('user', ''), PROFILE_LIMIT)
    return [dict(kind='profile', ref='APEX_USER.md', text=text)] if text else []


def _standing(pid, name, prompt) -> list[dict]:
    from agent import self_mod
    text = _cut(self_mod.get_prompt_addition(), STANDING_LIMIT)
    return [dict(kind='standing', ref='standing', text=text)] if text else []


def active_rules(pid: int) -> list[dict]:
    """The project's active rules, numbered by their place in the saved list."""
    items = continuity.code_corrections(pid)['data'].get('items') or []
    return [dict(kind='rule', ref=n, text=x['text']) for n, x in enumerate(items, 1) if x.get('active')]


def _rules(pid, name, prompt) -> list[dict]:
    return active_rules(pid)


def _handoff(pid, name, prompt) -> list[dict]:
    data = continuity.code_project(pid)['data']
    out = []
    decisions = str(data.get('decisions') or '').strip()
    if decisions:
        tail = decisions if len(decisions) <= DECISIONS_TAIL else '…' + decisions[-DECISIONS_TAIL:]
        out.append(dict(kind='handoff', ref='decisions', text=tail))
    step = str(data.get('next_step') or '').strip()
    if step:
        out.append(dict(kind='handoff', ref='next_step', text=step))
    return out


def _mentions(memory: dict, words: list[str]) -> bool:
    """A word (or the start of one) in the memory's content or tags."""
    hay = f"{memory.get('content') or ''} {memory.get('tags') or ''}".lower()
    return any(re.search(r'\b' + re.escape(w), hay) for w in words if w)


# Memories any channel, device or tool call can save (core's `remember`, a page
# the agent read) never reach a coding agent's system prompt: only those whose
# provenance code set (agent/longterm.init_db) as the owner's.
TRUSTED = ('approved', 'code_rule')


def _trusted(rows) -> list[dict]:
    return [m for m in rows if m.get('source') in TRUSTED]


def memory_signature(sources):
    """Stable identity of current knowledge, excluding volatile session statistics."""
    import hashlib
    items = [{k: row.get(k) for k in ('kind', 'ref', 'text')} for row in sources
             if row.get('kind') not in ('record', 'session')]
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def _memories(pid, name, prompt) -> list[dict]:
    seen, picked = set(), []

    def add(rows):
        for m in rows:
            if len(picked) >= MEMORY_LIMIT:
                return
            key = m.get('id') if m.get('id') is not None else m.get('content')
            if key in seen or not str(m.get('content') or '').strip():
                continue
            seen.add(key)
            picked.append(m)

    project = name.lower() if len(name.strip()) >= 3 else ''
    prefs = _trusted(longterm.recall('', limit=200, kind='preference'))
    add([m for m in prefs if _mentions(m, ['code', 'coding', project])])
    if prompt.strip():                       # never a personal fact that happens to share words
        add([m for m in _trusted(longterm.match_terms(prompt, 40)) if m.get('kind') in ('preference', 'decision')][:4])
    if project:
        add([m for m in _trusted(longterm.recall('', limit=200, kind='decision')) if _mentions(m, [project])])
    return [dict(kind='memory', ref=m.get('id'), text=_cut(_one_line(m['content']), MEMORY_CHARS)) for m in picked]


class CodeError(ValueError):
    """A request the page made that can't be done, in plain words."""


def project_name(pid: int) -> str:
    """The code project's name, '' when there is none (or Apex Code never ran)."""
    try:
        with longterm._conn() as db:
            row = db.execute('SELECT name FROM code_projects WHERE id=?', (pid,)).fetchone()
        return row[0] if row else ''
    except sqlite3.OperationalError:
        return ''


def unvouched(pid: int) -> list[dict]:
    """Coding memories the brief leaves out because nothing says the owner saved
    them (any channel or tool call can save a memory, and memories from before
    provenance existed have none). The owner can vouch for one from the page."""
    name = project_name(pid)
    project = name.lower() if len(name.strip()) >= 3 else ''
    rows = [m for m in longterm.recall('', limit=200, kind='preference') if _mentions(m, ['code', 'coding', project])]
    if project:
        rows += [m for m in longterm.recall('', limit=200, kind='decision') if _mentions(m, [project])]
    out, seen = [], set()
    for m in rows:
        if m.get('source') in TRUSTED or m.get('id') in seen or not str(m.get('content') or '').strip():
            continue
        seen.add(m.get('id'))
        out.append(dict(kind='memory', ref=m.get('id'), text=redact(_cut(_one_line(m['content']), MEMORY_CHARS))))
    return out[:UNVOUCHED_LIMIT]


def vouch(memory_id: int) -> dict:
    """The owner says a memory is theirs: it may reach a coding agent's brief."""
    with longterm._write_conn() as db:
        n = db.execute("UPDATE memories SET source = 'approved' WHERE id = ? AND source = ''", (int(memory_id),)).rowcount
    if not n:
        raise CodeError('No such memory, or it already counts.')
    return {'ok': True, 'id': int(memory_id)}


def _record(pid, name, prompt) -> list[dict]:
    return [dict(kind='record', ref=x['key'], text=x['text']) for x in track_record(pid)['lines'][:RECORD_LINES]]


def _sessions(pid, name, prompt) -> list[dict]:
    try:
        with longterm._conn() as db:
            rows = db.execute("SELECT s.id, s.title, s.status, s.review_rating, s.check_state, "
                              "(SELECT data FROM code_events e WHERE e.session_id = s.id AND e.kind = 'discarded' "
                              " ORDER BY e.id DESC LIMIT 1) FROM code_sessions s "
                              "WHERE s.project_id=? AND s.status IN ('kept', 'discarded') ORDER BY s.updated DESC LIMIT ?",
                              (pid, SESSIONS)).fetchall()
    except sqlite3.OperationalError:                     # Apex Code has not run on this PC yet
        return []
    out = []
    for sid, title, status, rating, checks, gone in rows:
        why = REASONS.get(_reason_of(gone), '')
        bits = [f'"{_one_line(title)}"', 'kept' if status == 'kept' else f"thrown away{f' ({why})' if why else ''}"]
        if rating is not None:
            bits.append(f'second opinion {rating}/10')
        if checks in ('passed', 'failed'):
            bits.append(f'checks {checks}')
        # Only what Apex measured: the agent's own summary is its text, not the owner's,
        # and never goes into a later session's system prompt.
        out.append(dict(kind='session', ref=sid, text=', '.join(bits)))
    return out


SOURCES = {'profile': _profile, 'standing': _standing, 'rule': _rules, 'handoff': _handoff,
           'record': _record, 'memory': _memories, 'session': _sessions}


# ---------------------------------------------------------------- the text

def _line(item: dict) -> str:
    kind, ref, text = item['kind'], item['ref'], item['text']
    if kind == 'profile':
        return f'[profile] {text}'
    if kind == 'standing':
        return f'[standing rules] {text}'
    if kind == 'handoff':
        return f"[handoff: {'next step' if ref == 'next_step' else 'decisions'}] {text}"
    if kind == 'record':
        return f'- [measured] {text}'
    tag = {'rule': f'rule {ref}', 'memory': f'memory #{ref}', 'session': f'session #{ref}'}[kind]
    return f'- [{tag}] {text}'


def _render(owner: str, found: dict) -> str:
    parts = []
    for kind, title in SECTIONS:
        if found.get(kind):
            parts.append(f"## {title.format(owner=owner)}\n" + '\n'.join(_line(x) for x in found[kind]))
    return redact('\n\n'.join([HEADER.format(owner=owner)] + parts)) if parts else ''


def brief_block(pid: int, prompt: str = '') -> dict:
    """What a new session in code project `pid` is told about the owner.

    {'text': the block, 'sources': [{'kind', 'ref', 'text'}] for every line in
    it, 'chars': len(text), 'errors': {source: why}}. A source that fails adds
    nothing and is named in errors; this never raises for a broken source.
    """
    owner = _owner()
    name = project_name(pid)
    found, errors = {}, {}
    for kind, fn in SOURCES.items():
        try:
            items = fn(pid, name, str(prompt or ''))
        except Exception as exc:
            items, errors[kind] = [], f'{type(exc).__name__}: {exc}'
            print(f'[Code] what Apex knows: the {kind} source failed: {errors[kind]}')
        # Redacted one by one too, so what the page lists is exactly what was sent.
        found[kind] = [{**x, 'text': redact(x['text'])} for x in items if str(x.get('text') or '').strip()]
    text = _render(owner, found)
    for kind in DROP_ORDER:                              # over CAP: the least important lines go first
        while len(text) > CAP and found[kind]:
            found[kind].pop(0 if kind == 'handoff' else -1)      # the next step outlasts old decisions
            text = _render(owner, found)
    sources = [x for kind, _ in SECTIONS for x in found[kind]]
    return {'text': text, 'sources': sources, 'chars': len(text), 'errors': errors}


# ---------------------------------------------------------------- the file Claude reads

def brief_path(sid: int) -> Path:
    """Beside the sessions' working copies (ApexWork/code), never inside one,
    so the brief is never committed with the work."""
    return Path(work.WORK_DIR).resolve() / 'code' / '.apex' / f'{int(sid)}-brief.md'


def brief_file(sid: int, text: str) -> Path:
    path = brief_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def forget_file(sid: int) -> None:
    """The session is over (kept or thrown away): its brief and its memory server
    file go too. Tidying up never stops a Keep or a Throw away."""
    for path in (brief_path(sid), mcp_path(sid)):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            print(f'[Code] could not remove {path.name} of session {sid}: {exc}')


# ---------------------------------------------------------------- brain on tap: the memory server
# The brief starts a session and refreshes when its knowledge changes. The plan
# can also ask Apex's memory itself, through Apex's own MCP server
# (agent/mcp_server.py, run by scripts/apex_mcp.py): context, recall, lessons and
# search_files only read, and what they return is redacted. `remember` only stages
# a memory in the approvals queue; the page shows it under "Waiting for your OK",
# and nothing is saved until the owner approves it. Every call is logged to
# ~/.apex/mcp.log.

MCP_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'apex_mcp.py'
SUGGESTED_BY = 'Apex Code'                  # how a session's suggestions are told apart in the queue


def mcp_path(sid: int) -> Path:
    """Beside the brief: outside every working copy, so it is never committed."""
    return Path(work.WORK_DIR).resolve() / 'code' / '.apex' / f'{int(sid)}-mcp.json'


def mcp_file(sid: int, pid: int) -> Path:
    """Apex's memory server for one session, in the shape Claude Code's --mcp-config
    reads (Codex gets the same server as config overrides, agent/code_engines.py).
    The server learns which code project and session it serves from its
    environment, and uses the same database as this Apex."""
    env = {'APEX_CODE_PROJECT': str(int(pid)), 'APEX_CODE_SESSION': str(int(sid)),
           'DB_PATH': os.path.abspath(str(longterm.DB_PATH))}
    config = {'mcpServers': {'apex': {'command': sys.executable, 'args': [str(MCP_SCRIPT)], 'env': env}}}
    path = mcp_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    return path


def suggested() -> list[dict]:
    """Memories Apex Code sessions suggested, waiting for the owner's OK, newest
    first. Approve and Reject are the queue's own (/api/staged-writes/{id}/...)."""
    from agent import approvals
    out = []
    for w in approvals.list_pending():
        p = w.get('payload') or {}
        if w.get('kind') != 'remember' or not str(p.get('source') or '').startswith(SUGGESTED_BY):
            continue
        out.append({'id': w['id'], 'ts': w['ts'], 'content': str(p.get('content') or ''), 'kind': p.get('kind') or 'note',
                    'why': str(p.get('why') or ''), 'tags': str(p.get('tags') or ''), 'source': p['source'],
                    'project_id': p.get('project_id'), 'session_id': p.get('session_id')})
    return out


# ---------------------------------------------------------------- rules: say it once
# When the owner corrects the agent ("no, never touch the public API"), one tap
# turns it into a rule. A project rule lives in the continuity store
# (corrections:code-<n>), with history and restore; a rule for all code is a
# memory tagged code,rule, so voice, Celine and chat know it too. Every new
# session hears the rules in its brief, the second opinion checks them, and a
# session already open hears a change ahead of its next message (pending_note):
# a resumed plan keeps the brief it started with.

MAX_RULES = 20
RULE_CHARS = 500
SCOPES = ('project', 'all')
GLOBAL_LIMIT = 50


def _rule_text(text) -> str:
    text = text.strip() if isinstance(text, str) else ''
    if not 1 <= len(text) <= RULE_CHARS:
        raise ValueError(f'A rule is 1 to {RULE_CHARS} characters.')
    return text


def _rule_items(items) -> list[dict]:
    """The list the page sends back: at most 20 rules, each with its text and an on/off flag."""
    if not isinstance(items, list):
        raise ValueError('Send the list of rules.')
    if len(items) > MAX_RULES:
        raise ValueError(f'A project keeps at most {MAX_RULES} rules. Delete one first.')
    out = []
    for item in items:
        if not isinstance(item, dict) or type(item.get('active')) is not bool:
            raise ValueError('Each rule needs its text and whether it is on.')
        out.append(dict(text=_rule_text(item.get('text')), active=item['active']))
    return out


def global_rules() -> list[dict]:
    """Rules for all of the owner's code: memories add_rule() saved (source
    'code_rule', which no tool call can set), newest kept first. A memory merely
    tagged code,rule (say, by a model's `remember`) is not a rule."""
    found = longterm.recall('code,rule', limit=GLOBAL_LIMIT * 4, semantic=False)
    out = []
    for m in found:
        tags = {t.strip().lower() for t in str(m.get('tags') or '').split(',')}
        if m.get('source') == 'code_rule' and {'code', 'rule'} <= tags and str(m.get('content') or '').strip():
            out.append(dict(id=m.get('id'), text=str(m['content']).strip()))
    return out[:GLOBAL_LIMIT]


def rules(pid: int) -> dict:
    """This project's rules (all of them, on or off), its revision for a safe save,
    and the rules for all code."""
    doc = continuity.code_corrections(pid)
    return {'items': doc['data'].get('items') or [], 'revision': doc['revision'], 'global': global_rules()}


def _tell(pid: int | None, notes: list[str]) -> None:
    """Sessions already open hear it ahead of their next message. Saving a rule
    never fails because a session could not be told."""
    if not notes:
        return
    try:
        from agent import code_studio
        code_studio.tell_sessions('\n'.join(notes), pid)
    except Exception as exc:
        print(f'[Code] could not tell the open sessions about a rule: {type(exc).__name__}: {exc}')


def _changes(before: list[dict], after: list[dict]) -> list[str]:
    """What an open session must hear after a save: rules now on, and rules now off."""
    owner = _owner()
    was = [x['text'] for x in before if x.get('active')]
    now = [x['text'] for x in after if x.get('active')]
    return ([f'New rule from {owner} for this project: {t}' for t in now if t not in was] +
            [f'{owner} turned off this rule for this project; it no longer applies: {t}' for t in was if t not in now])


def add_rule(pid: int, text, scope: str = 'project', revision=None) -> dict:
    """One rule, said once. 'project': saved with this project's rules (a stale
    revision raises board_workspaces.Conflict). 'all': a memory tagged code,rule.
    Returns rules(pid)."""
    text = _rule_text(text)
    if scope not in SCOPES:
        raise ValueError('A rule is for this project or for all your code.')
    if scope == 'all':
        if not any(g['text'].lower() == text.lower() for g in global_rules()):    # said again: already remembered
            longterm.remember(text, kind='preference', importance=8, tags='code,rule', source='code_rule')
            _tell(None, [f'New rule from {_owner()}, for every project: {text}'])
        return rules(pid)
    before = continuity.code_corrections(pid)['data'].get('items') or []
    same = next((x for x in before if x['text'].lower() == text.lower()), None)
    if same and same.get('active'):              # already a rule: nothing to save
        return rules(pid)
    if same:                                     # said again: switch the old one back on
        after = [dict(x, active=True) if x is same else x for x in before]
    elif len(before) >= MAX_RULES:
        raise ValueError(f'A project keeps at most {MAX_RULES} rules. Delete one first.')
    else:
        after = before + [dict(text=text, active=True)]
    continuity.save_code_corrections(pid, after, revision)
    _tell(pid, _changes(before, after))
    return rules(pid)


def save_rules(pid: int, items, revision) -> dict:
    """Turn rules on or off, edit or delete them: the whole list, saved at once."""
    items = _rule_items(items)
    before = continuity.code_corrections(pid)['data'].get('items') or []
    continuity.save_code_corrections(pid, items, revision)
    _tell(pid, _changes(before, items))
    return rules(pid)


def rules_history(pid: int) -> list[dict]:
    """Earlier versions of this project's rules, newest first (the current one too)."""
    return [{'revision': h['revision'], 'updated': h['updated'], 'items': h['data'].get('items') or []}
            for h in continuity.history(continuity.code_key('corrections', pid))]


def restore_rules(pid: int, revision, current_revision) -> dict:
    """Bring back an earlier version. Nothing is lost: the restore is a new version."""
    before = continuity.code_corrections(pid)['data'].get('items') or []
    continuity.restore(continuity.code_key('corrections', pid), revision, current_revision)
    _tell(pid, _changes(before, continuity.code_corrections(pid)['data'].get('items') or []))
    return rules(pid)


# ---------------------------------------------------------------- every session teaches it
# Keep or Throw away writes what happened where the rest of Apex reads it: this
# project's decision log (the continuity handoff 'project:code-<n>', which every
# later brief reads), today's note in the vault (Obsidian, the morning brief) and
# the outcomes ledger (voice, the accuracy figures). Each step on its own, so a
# broken vault never breaks a Keep. Nothing goes to trajectory or tool_events:
# lessons.for_prompt()'s six slots belong to all of Apex chat, and tool_events
# keeps only the names of a tool's inputs.

# Why a session was thrown away, as the page offers it. A change of mind says
# nothing about the work, so it is left out of every rate below.
REASONS = {'changed_mind': 'a change of mind', 'wrong': 'it was wrong', 'poor': 'poor quality',
           'superseded': 'something better came along'}
DECISIONS_CAP = continuity.PROJECT_LIMITS['decisions']


def _reason_of(data) -> str:
    """The reason on a 'discarded' event (its JSON), '' when none was given."""
    try:
        reason = json.loads(data or '{}').get('reason') or ''
    except (TypeError, ValueError):
        return ''
    return reason if reason in REASONS else ''


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _facts(s: dict, outcome: str, facts: dict) -> dict:
    from agent import work_engines as we
    return dict(title=_one_line(s.get('title')) or 'Session', project=str(s.get('project') or ''),
                files=int(facts.get('files', s.get('files_changed')) or 0), commit=str(facts.get('commit') or ''),
                checks=s.get('check_state') or 'not run', proof=facts.get('proof') or 'unverified',
                rating=s.get('review_rating'), reviewer=we.NAMES.get(s.get('review_engine'), s.get('review_engine') or ''),
                engine=we.NAMES.get(s.get('engine'), s.get('engine') or ''), reason=facts.get('reason') or '',
                kept=outcome == 'kept')


def _decision(f: dict) -> str:
    date = time.strftime('%Y-%m-%d')
    if not f['kept']:
        return f'{date} threw away "{f["title"]}" ({REASONS.get(f["reason"], "no reason given")})'
    review = f"review {f['rating']}/10 by {f['reviewer']}" if f['rating'] is not None else 'no second opinion'
    return (f'{date} kept "{f["title"]}" ({_plural(f["files"], "file")}, {f["commit"][:12] or "no commit"}, '
            f'checks {f["checks"]}, proof {f["proof"]}, {review})')


def _append(decisions: str, line: str) -> str:
    """The log with one more line; the oldest lines go to stay within its limit."""
    lines = [l for l in str(decisions or '').splitlines() if l.strip()] + [_cut(line, DECISIONS_CAP)]
    while len('\n'.join(lines)) > DECISIONS_CAP and len(lines) > 1:
        lines.pop(0)
    return '\n'.join(lines)


def log_decision(pid: int, line: str) -> dict:
    """Add a line to the project's decision log. Another save in between (the
    owner restoring an older version, say) is read again and tried once more."""
    from agent import board_workspaces
    for attempt in (1, 2):
        doc = continuity.code_project(pid)
        data = {k: str(doc['data'].get(k) or '') for k in continuity.PROJECT_LIMITS}
        data['decisions'] = _append(data['decisions'], line)
        try:
            return continuity.save_code_project(pid, data, doc['revision'])
        except board_workspaces.Conflict:
            if attempt == 2:
                raise


def write_back(s: dict, outcome: str, **facts) -> dict:
    """A session ended ('kept' or 'discarded'): tell the rest of Apex. `s` is the
    session as code_studio.session() returns it; facts: commit, files, proof,
    reason. Returns which steps worked: {'decision', 'daily', 'outcome'}. Never
    raises: what could not be written is False, and the reason is printed."""
    f = _facts(s, outcome, facts)
    # What Apex measured, never the agent's summary: the log is in every later brief.
    line = _decision(f)
    rated = f"rated {f['rating']}/10" if f['rating'] is not None else 'not rated'
    result = '; '.join(['kept' if f['kept'] else 'thrown away', f"checks {f['checks']}", f"proof {f['proof']}", rated]
                       + ([] if f['kept'] else [f"reason {REASONS.get(f['reason'], 'none given')}"]))
    success = True if f['kept'] and f['proof'] == 'proved' else False if f['reason'] in ('wrong', 'poor') else None

    def log():
        log_decision(int(s['project_id']), redact(line))

    def daily():
        from agent import vault
        where = f" in {f['project']}" if f['project'] else ''
        vault.append_daily(redact(f'Apex Code: kept "{f["title"]}"{where} ({_plural(f["files"], "file")}, checks {f["checks"]})'
                                  if f['kept'] else
                                  f'Apex Code: threw away "{f["title"]}"{where} ({REASONS.get(f["reason"], "no reason given")})'))

    def ledger():
        from agent import outcomes
        outcomes.init_db()
        outcomes.record(redact(f"Apex Code session: {f['title']}"), redact(result), success=success,
                        action_taken=f['engine'], domain=f"code:{f['project']}")

    done = {}
    for step, fn in (('decision', log), ('daily', daily), ('outcome', ledger)):
        try:
            fn()
            done[step] = True
        except Exception as exc:
            done[step] = False
            print(f'[Code] could not write the {step} of session {s.get("id")}: {type(exc).__name__}: {exc}')
    return done


# The project's decision log, for the Rules tab: read-only, with its versions.

def decision_log(pid: int) -> dict:
    doc = continuity.code_project(pid)
    return {'decisions': str(doc['data'].get('decisions') or ''), 'revision': doc['revision'], 'updated': doc['updated']}


def decision_log_history(pid: int) -> list[dict]:
    """Earlier versions of the project's handoff, newest first (the current one too)."""
    return [{'revision': h['revision'], 'updated': h['updated'], 'decisions': str(h['data'].get('decisions') or '')}
            for h in continuity.history(continuity.code_key('project', pid))]


def restore_decision_log(pid: int, revision, current_revision) -> dict:
    """Bring an earlier version back. The restore is a new version, so it can be undone too."""
    continuity.restore(continuity.code_key('project', pid), revision, current_revision)
    return decision_log(pid)


# ---------------------------------------------------------------- the track record
# What actually happens on this project, counted from the sessions themselves:
# plain SQL over code_sessions and code_events, worked out again whenever a session
# here ends and never stored, so a lesson can't outlive its evidence (agent/lessons.py's rule). A line
# shows only with lessons.py's thresholds behind it, at least MIN_OBSERVATIONS
# sessions and PROPOSE_RATE of them, and always as "X of N", never a bare
# percentage. A pattern to act on comes before how each plan does.

RECORD_DAYS = 90
RECORD_LINES = 3                     # in the brief and on the home page


def _thresholds() -> tuple[int, float]:
    from agent import lessons
    return lessons.MIN_OBSERVATIONS, lessons.PROPOSE_RATE


FINISHED = "s.project_id = ? AND s.status IN ('kept', 'discarded') AND s.updated >= ?"


def _finished(db, pid: int, since: float) -> list[dict]:
    """Every session here that was kept or thrown away since `since`, with what
    the record needs from its events."""
    cur = db.execute(f"""
        SELECT s.id, s.engine, s.status, s.review_rating,
          (SELECT json_extract(e.data, '$.reason') FROM code_events e
             WHERE e.session_id = s.id AND e.kind = 'discarded' ORDER BY e.id DESC LIMIT 1) AS reason,
          (SELECT json_extract(e.data, '$.proof') FROM code_events e
             WHERE e.session_id = s.id AND e.kind = 'kept' ORDER BY e.id DESC LIMIT 1) AS proof,
          (SELECT COALESCE(json_extract(e.data, '$.state'),
                           CASE WHEN json_extract(e.data, '$.passed') THEN 'passed' ELSE 'failed' END)
             FROM code_events e WHERE e.session_id = s.id AND e.kind = 'checks' ORDER BY e.id LIMIT 1) AS first_checks
        FROM code_sessions s WHERE {FINISHED}""", (pid, since))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _agent_runs(db, pid: int, since: float) -> list[tuple]:
    """(session, ok) for each test run the agent did in those sessions: its command,
    then the first result with the same tool id (Codex starts its ids again each turn)."""
    from agent import code_studio
    tests = [(sid, ref, eid) for eid, sid, ref, title in db.execute(f"""
        SELECT e.id, e.session_id, json_extract(e.data, '$.ref'), json_extract(e.data, '$.title') FROM code_events e
        WHERE e.kind = 'tool' AND e.session_id IN (SELECT s.id FROM code_sessions s WHERE {FINISHED})
          AND json_extract(e.data, '$.tool') = 'command'""", (pid, since))
        if ref is not None and code_studio._runs_tests(title or '')]
    results = {}
    ids = sorted({sid for sid, _, _ in tests})
    for i in range(0, len(ids), 500):                    # the results of only those sessions, in id order
        chunk = ids[i:i + 500]
        for eid, sid, ref, ok in db.execute(
                "SELECT id, session_id, json_extract(data, '$.ref'), json_extract(data, '$.ok') FROM code_events "
                f"WHERE kind = 'result' AND session_id IN ({','.join('?' * len(chunk))}) ORDER BY id", chunk):
            results.setdefault((sid, ref), []).append((eid, ok))
    out = []
    for sid, ref, eid in tests:
        ok = next((ok for rid, ok in results.get((sid, ref), ()) if rid > eid), None)
        if ok is not None:
            out.append((sid, bool(ok)))
    return out


def _folders(db, pid: int, since: float) -> dict:
    """session -> the top folders of the files its agent changed."""
    out = {}
    for sid, path in db.execute(f"""
            SELECT DISTINCT e.session_id, json_extract(e.data, '$.path') FROM code_events e
            WHERE e.kind = 'file' AND e.session_id IN (SELECT s.id FROM code_sessions s WHERE {FINISHED})""",
                                (pid, since)):
        top, _, rest = str(path or '').replace('\\', '/').partition('/')
        if rest and top not in ('', '.', '..') and ':' not in top:
            out.setdefault(sid, set()).add(top)
    return out


# The page asks for the record every few seconds. A finished session never changes,
# so what was read stays right until a session here ends or leaves the window: it
# is kept in memory against those (never written anywhere) and read again then.
_read: dict = {}


def _evidence(pid: int, days: int) -> tuple:
    """(finished sessions, agent test runs, folders, project row), read fresh whenever they could differ."""
    since = time.time() - days * 86400
    with longterm._conn() as db:
        # The 'kept'/'discarded' event (its proof, its reason) lands just after the status
        # does, so a read in between must not be what stays cached: count those too.
        moved = db.execute(f'SELECT COUNT(*), MAX(s.updated), MIN(s.updated), '
                           f"(SELECT MAX(e.id) FROM code_events e JOIN code_sessions s ON s.id = e.session_id "
                           f"WHERE e.kind IN ('kept', 'discarded') AND {FINISHED}) "
                           f'FROM code_sessions s WHERE {FINISHED}', (pid, since, pid, since)).fetchone()
        key = (str(longterm.DB_PATH), pid, days)
        if key in _read and _read[key][0] == moved:
            return _read[key][1]
        found = (_finished(db, pid, since), _agent_runs(db, pid, since), _folders(db, pid, since),
                 db.execute('SELECT name FROM code_projects WHERE id=?', (pid,)).fetchone())
    _read[key] = (moved, found)
    return found


def _measured(key: str, text: str, n: int, hits: int) -> dict:
    return {'key': key, 'text': text, 'n': n, 'hits': hits, 'rate': round(hits / n, 3) if n else 0.0}


def _patterns(quality: list[dict], runs: list[tuple], folders: dict, enough) -> list[dict]:
    """The patterns worth telling: each thrown away (or failing) often enough, with enough sessions."""
    gone = lambda rows: sum(1 for r in rows if r['status'] == 'discarded')
    out = []
    for key, when, rows in (
            ('no_checks', 'when no checks ran', [r for r in quality if r['first_checks'] is None]),
            ('first_checks_failed', 'when the first checks run failed', [r for r in quality if r['first_checks'] == 'failed']),
            ('low_review', 'when the second opinion rated it 5/10 or lower',
             [r for r in quality if r['review_rating'] is not None and r['review_rating'] <= 5])):
        if enough(len(rows), gone(rows)):
            out.append(_measured(key, f'thrown away {gone(rows)} of {len(rows)} {when}', len(rows), gone(rows)))
    counted = {r['id'] for r in quality}
    by_folder = {}
    for sid, ok in runs:
        if sid in counted:
            for top in folders.get(sid, ()):
                by_folder.setdefault(top, []).append(ok)
    for top, oks in sorted(by_folder.items()):
        failed = oks.count(False)
        if enough(len(oks), failed):
            out.append(_measured(f'tests:{top}', f"the agent's own test runs failed {failed} of {len(oks)} times "
                                                 f'in sessions that changed {top}/', len(oks), failed))
    return sorted(out, key=lambda x: (-x['rate'], -x['n'], x['key']))


def _engines(quality: list[dict], enough) -> dict:
    """How each plan does here: kept (and kept with proof), or thrown away when
    that is the larger part. Only with enough sessions behind it."""
    from agent import code_studio, work_engines as we
    out = {}
    for engine in code_studio.ENGINES:
        rows = [r for r in quality if r['engine'] == engine]
        n, kept = len(rows), sum(1 for r in rows if r['status'] == 'kept')
        proved = sum(1 for r in rows if r['status'] == 'kept' and r['proof'] == 'proved')
        name = we.NAMES.get(engine, engine)
        if enough(n, kept):
            out[engine] = _measured(f'engine:{engine}', f'{name} here: kept {kept} of {n}, {proved} with proof', n, kept)
        elif enough(n, n - kept):
            out[engine] = _measured(f'engine:{engine}', f'{name} here: thrown away {n - kept} of {n}', n, n - kept)
    return out


def track_record(pid: int, days: int = RECORD_DAYS) -> dict:
    """What tends to happen on code project `pid` over the last `days` days:
    {'lines': [{key, text, n, hits, rate}], 'engines': {engine: line},
    'decided': sessions counted, 'days', 'accuracy': the outcomes ledger's
    figure for this project, 'note': why there are no lines, or what they come from}."""
    least, rate = _thresholds()
    enough = lambda n, hits: n >= least and hits / n >= rate
    try:
        rows, runs, folders, found = _evidence(pid, max(1, int(days)))
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        rows, runs, folders, found = [], [], {}, None                  # Apex Code has not run on this PC yet
    quality = [r for r in rows if r['reason'] != 'changed_mind']      # a change of mind says nothing about the work
    engines = _engines(quality, enough)
    lines = _patterns(quality, runs, folders, enough) + sorted(engines.values(), key=lambda x: (-x['n'], x['key']))
    decided = len(quality)
    if decided < least:
        note = f'Not enough sessions yet ({decided} of {least})'
    elif not lines:
        note = (f'Nothing stands out yet in {decided} sessions: a pattern shows once {least} sessions share it '
                f'and it happens in at least {round(rate * 5)} of every 5.')
    else:
        note = f"Counted from {_plural(decided, 'finished session')} in the last {days} days."
    accuracy = None
    if found:
        try:
            from agent import outcomes
            outcomes.init_db()
            accuracy = outcomes.recommendation_accuracy(days, domain=f'code:{found[0]}')
        except sqlite3.Error as exc:                                   # the record still stands without it
            print(f'[Code] could not read the outcomes of {found[0]}: {exc}')
    return {'lines': lines, 'engines': engines, 'decided': decided, 'days': days, 'accuracy': accuracy, 'note': note}
