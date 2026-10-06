"""Coding on your plans, live: Claude Code and Codex, every step as it happens.

One turn of a coding session (agent/code_studio.py) runs one of the two
official tools, signed in with your own subscription (agent/work_engines.py
checks how, and refuses an API-key sign-in), inside the session's own git
worktree, and turns what the tool prints into one simple feed:

  claude    `claude -p --output-format stream-json --verbose`, resumed with
            `--resume <session id>` for the next message
  chatgpt   `codex exec --json`, resumed with `codex exec resume <thread id>`

Modes:
  safe    Claude Code may read, write and edit files, search the web, and run
          only git status/diff/log/show, tests and syntax checks. Codex runs in
          its workspace-write sandbox (writes stay in the worktree, no network).
  full    any command, in the worktree. For when Safe gets in the way.
  review  read-only: the second opinion on someone else's change.

As with Work tasks, the prompt goes in on stdin (never through cmd.exe), Apex's
API keys are removed from the tool's environment, and Stop or the time limit
ends the whole process tree.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable

from agent import work_engines as we

MODES = ('safe', 'full', 'review')
TURN_TIMEOUT = 3600                         # one message's work: at most an hour
SAFE_COMMANDS = ['git status', 'git diff', 'git log', 'git show', 'ls', 'python -m pytest', 'pytest',
                 'python -m py_compile', 'node --check', 'node scripts/', 'npm test', 'npm run test', 'npm run lint']
FILE_TOOLS = ['Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep', 'TodoWrite', 'WebSearch', 'WebFetch']
CLAUDE_TOOLS = {
    'safe': FILE_TOOLS + [f'Bash({c}:*)' for c in SAFE_COMMANDS],
    'full': FILE_TOOLS + ['Bash', 'PowerShell'],
    'review': ['Read', 'Glob', 'Grep'],
}
REVIEW_DENIED = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Bash', 'PowerShell']
CODEX_SANDBOX = {'safe': 'workspace-write', 'full': 'danger-full-access', 'review': 'read-only'}
SESSION_ID = re.compile(r'^[A-Za-z0-9_-]{6,80}$')   # what may be put on a command line
OUTPUT_TAIL = 2000
RETRYING = re.compile(r'^(Reconnecting|Falling back|Retrying)\b', re.I)
# The tool no longer has the conversation to resume (seen with the real tools).
RESUME_LOST = re.compile(r'no rollout found|No conversation found', re.I)


def error_text(text: str) -> str:
    """A tool's error in its own words, without a stack trace or retry chatter."""
    text = re.split(r'\n\s*Stack backtrace:', text or '', maxsplit=1)[0]
    keep = [l for l in text.splitlines() if not RETRYING.match(l.strip().removeprefix('ERROR:').strip())]
    return we._tidy('\n'.join(keep))


def command(engine: str, exe: str, folder: Path, mode: str, resume: str | None = None) -> list[str]:
    if mode not in MODES:
        raise ValueError('Mode is safe, full or review.')
    if resume and not SESSION_ID.match(resume):
        raise ValueError('That session id is not one Apex made.')
    if engine == 'claude':
        cmd = [exe, '-p', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'acceptEdits',
               '--allowedTools', *CLAUDE_TOOLS[mode], '--strict-mcp-config']
        if mode == 'review':
            cmd += ['--disallowedTools', *REVIEW_DENIED]
        if resume:
            cmd += ['--resume', resume]
        return cmd
    if engine != 'chatgpt':
        raise ValueError('Code runs on claude or chatgpt.')
    # `codex exec resume` takes neither -C nor --sandbox: the folder is the
    # process's working directory and the sandbox a config value, both ways.
    cmd = [exe, 'exec']
    if resume:
        cmd += ['resume']
    cmd += ['--json', '--skip-git-repo-check', '-c', f'sandbox_mode={CODEX_SANDBOX[mode]}']
    mode_nt = we.codex_windows_sandbox()
    if mode_nt and os.name == 'nt':
        cmd += ['-c', f'windows.sandbox={mode_nt}']
    if resume:
        cmd += [resume]
    return cmd + ['-']                         # '-': the message comes in on stdin


# ---------------------------------------------------------------- the feed

def _rel(path: str, folder: Path) -> str:
    """A path as you'd say it: relative to the project, with forward slashes."""
    if not path:
        return ''
    try:
        p = Path(path)
        if p.is_absolute():
            p = p.resolve().relative_to(Path(folder).resolve())
        return p.as_posix()
    except (ValueError, OSError):
        return str(path).replace('\\', '/')


def _lines(text) -> int:
    return len(str(text or '').splitlines()) or (1 if text else 0)


def _tail(text, n=OUTPUT_TAIL) -> str:
    text = str(text or '')
    return text if len(text) <= n else '…' + text[-n:]


def _short(text, n=140) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def _delta(old, new) -> tuple[int, int]:
    """Lines really added and removed by replacing old with new (what git will show)."""
    a, b = str(old or '').splitlines(), str(new or '').splitlines()
    plus = minus = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ('replace', 'delete'):
            minus += i2 - i1
        if tag in ('replace', 'insert'):
            plus += j2 - j1
    return plus, minus


def _claude_tool(name: str, args: dict, folder: Path) -> list[dict]:
    """A tool call as feed events: what it does, in a few words."""
    path = _rel(args.get('file_path') or args.get('notebook_path') or args.get('path') or '', folder)
    if name == 'Read':
        return [{'kind': 'tool', 'tool': 'read', 'title': f'Read {path}', 'path': path}]
    if name in ('Edit', 'MultiEdit'):
        edits = args.get('edits') if name == 'MultiEdit' else [args]
        deltas = [_delta(e.get('old_string'), e.get('new_string')) for e in edits or []]
        return [{'kind': 'file', 'path': path, 'change': 'update',
                 'plus': sum(d[0] for d in deltas), 'minus': sum(d[1] for d in deltas)}]
    if name == 'Write':
        return [{'kind': 'file', 'path': path, 'change': 'write', 'plus': _lines(args.get('content')), 'minus': 0}]
    if name == 'NotebookEdit':
        return [{'kind': 'file', 'path': path, 'change': 'update', 'plus': _lines(args.get('new_source')), 'minus': 0}]
    if name in ('Glob', 'Grep'):
        what = args.get('pattern') or ''
        where = f" in {path}" if path else ''
        return [{'kind': 'tool', 'tool': 'search', 'title': f'Searched for {_short(what, 80)}{where}'}]
    if name in ('Bash', 'PowerShell'):
        return [{'kind': 'tool', 'tool': 'command', 'title': _short(args.get('command'), 200),
                 'detail': _short(args.get('description'), 160)}]
    if name == 'WebSearch':
        return [{'kind': 'tool', 'tool': 'web', 'title': f"Searched the web: {_short(args.get('query'), 120)}"}]
    if name == 'WebFetch':
        return [{'kind': 'tool', 'tool': 'web', 'title': f"Read {_short(args.get('url'), 120)}"}]
    if name == 'TodoWrite':
        items = [{'text': _short(t.get('content'), 160), 'done': t.get('status') == 'completed',
                  'active': t.get('status') == 'in_progress'} for t in args.get('todos') or []]
        return [{'kind': 'todo', 'items': items}]
    if name in ('Task', 'Agent'):
        return [{'kind': 'tool', 'tool': 'other', 'title': f"Helper: {_short(args.get('description'), 120)}"}]
    return [{'kind': 'tool', 'tool': 'other', 'title': name}]


def parse(engine: str, line: str, folder: Path, state: dict) -> list[dict]:
    """One line the tool printed -> feed events. Unknown lines are ignored."""
    line = line.strip()
    if not line.startswith('{'):
        return []
    try:
        data = json.loads(line)
    except ValueError:
        return []
    return _parse_claude(data, folder, state) if engine == 'claude' else _parse_codex(data, folder, state)


def _parse_claude(d: dict, folder: Path, state: dict) -> list[dict]:
    kind = d.get('type')
    out = []
    if kind == 'system':
        if d.get('subtype') == 'init' and d.get('session_id'):
            out.append({'kind': 'session', 'id': d['session_id'], 'model': d.get('model') or ''})
        elif d.get('subtype') == 'api_retry':
            out.append({'kind': 'note', 'text': 'The plan is busy; retrying…'})
        return out
    if kind == 'assistant':
        for block in (d.get('message') or {}).get('content') or []:
            t = block.get('type')
            if t == 'text' and block.get('text', '').strip():
                state['last_text'] = block['text']
                out.append({'kind': 'text', 'text': block['text']})
            elif t == 'thinking' and block.get('thinking', '').strip():
                out.append({'kind': 'thinking', 'text': _tail(block['thinking'], 4000)})
            elif t == 'tool_use':
                state.setdefault('tools', {})[block.get('id')] = block.get('name')
                for e in _claude_tool(block.get('name') or '', block.get('input') or {}, folder):
                    out.append({**e, 'id': block.get('id')})
        return out
    if kind == 'user':
        content = (d.get('message') or {}).get('content')
        for block in content if isinstance(content, list) else []:
            if block.get('type') != 'tool_result':
                continue
            name = state.get('tools', {}).get(block.get('tool_use_id'))
            text = block.get('content')
            if isinstance(text, list):
                text = '\n'.join(str(x.get('text', '')) for x in text if isinstance(x, dict))
            ok = not block.get('is_error')
            # Command output is worth showing; a file's contents are not (they're in the diff).
            show = name in ('Bash', 'PowerShell') or not ok
            out.append({'kind': 'result', 'id': block.get('tool_use_id'), 'ok': ok,
                        'output': _tail(text) if show else ''})
        return out
    if kind == 'result':
        for denied in d.get('permission_denials') or []:
            args = denied.get('tool_input') or {}
            what = args.get('command') or args.get('file_path') or args.get('url') or ''
            out.append({'kind': 'blocked', 'title': f"{denied.get('tool_name')}: {_short(what, 160)}"})
        failed = bool(d.get('is_error')) or d.get('subtype') not in (None, 'success')
        detail = '\n'.join([str(d.get('result') or '')] + [str(e) for e in d.get('errors') or []])
        status = we._classify(detail, failed)
        usage = d.get('usage') or {}
        out.append({'kind': 'done', 'status': status,
                    'summary': str(d.get('result') or '') if not failed else error_text(detail) or 'It stopped with an error.',
                    'reset_at': we.reset_time(detail) if status == 'limited' else None,
                    'tokens': (usage.get('input_tokens') or 0) + (usage.get('output_tokens') or 0)
                              + (usage.get('cache_read_input_tokens') or 0) + (usage.get('cache_creation_input_tokens') or 0)})
        if d.get('session_id'):
            state['session'] = d['session_id']
        return out
    return out


def _parse_codex(d: dict, folder: Path, state: dict) -> list[dict]:
    kind = d.get('type')
    if kind == 'thread.started' and d.get('thread_id'):
        return [{'kind': 'session', 'id': d['thread_id'], 'model': ''}]
    if kind == 'turn.completed':
        u = d.get('usage') or {}
        return [{'kind': 'done', 'status': 'done', 'summary': state.get('last_text', ''), 'reset_at': None,
                 'tokens': (u.get('input_tokens') or 0) + (u.get('output_tokens') or 0)}]
    if kind in ('turn.failed', 'error'):
        msg = (d.get('error') or {}).get('message') if kind == 'turn.failed' else d.get('message')
        state['error'] = str(msg or '')
        if kind == 'error':
            if RETRYING.match(str(msg or '')):
                return [{'kind': 'note', 'text': 'The plan is slow to answer; retrying…'}]
            return [{'kind': 'error', 'text': error_text(str(msg or 'Codex reported an error.'))}]
        status = we._classify(state['error'], True)
        return [{'kind': 'done', 'status': status, 'summary': error_text(state['error']) or 'Codex stopped with an error.',
                 'reset_at': we.reset_time(state['error']) if status == 'limited' else None, 'tokens': 0}]
    if kind not in ('item.started', 'item.updated', 'item.completed'):
        return []
    item = d.get('item') or {}
    t, iid, done = item.get('type'), item.get('id'), kind == 'item.completed'
    if t == 'agent_message' and done and str(item.get('text', '')).strip():
        state['last_text'] = item['text']
        return [{'kind': 'text', 'text': item['text']}]
    if t == 'reasoning' and done and str(item.get('text', '')).strip():
        return [{'kind': 'thinking', 'text': _tail(item['text'], 4000)}]
    if t == 'command_execution':
        if kind == 'item.started':
            return [{'kind': 'tool', 'tool': 'command', 'title': _short(item.get('command'), 200), 'id': iid}]
        if done:
            return [{'kind': 'result', 'id': iid, 'ok': item.get('exit_code') == 0 and item.get('status') != 'failed',
                     'exit_code': item.get('exit_code'), 'output': _tail(item.get('aggregated_output'))}]
    if t == 'file_change' and done:
        return [{'kind': 'file', 'path': _rel(c.get('path', ''), folder), 'id': iid,
                 'change': {'add': 'add', 'delete': 'delete'}.get(c.get('kind'), 'update'), 'plus': None, 'minus': None}
                for c in item.get('changes') or []]
    if t == 'web_search' and kind == 'item.started':
        return [{'kind': 'tool', 'tool': 'web', 'title': f"Searched the web: {_short(item.get('query'), 120)}", 'id': iid}]
    if t == 'todo_list':
        return [{'kind': 'todo', 'items': [{'text': _short(x.get('text'), 160), 'done': bool(x.get('completed')), 'active': False}
                                            for x in item.get('items') or []]}]
    if t == 'mcp_tool_call' and kind == 'item.started':
        return [{'kind': 'tool', 'tool': 'other', 'title': f"{item.get('server', '')} {item.get('tool', '')}".strip(), 'id': iid}]
    if t == 'error' and done:
        return [{'kind': 'error', 'text': _short(item.get('message'), 400)}]
    return []


# ---------------------------------------------------------------- one turn

def turn(engine: str, prompt: str, folder: Path, mode: str = 'safe', resume: str | None = None,
         on_event: Callable[[dict], None] = lambda e: None, run_id: str | None = None,
         timeout: int = TURN_TIMEOUT, env_extra: dict | None = None) -> dict:
    """Run one message's work, calling on_event for each step. Never raises
    for a failed run. Returns the final 'done' event plus the session id."""
    def finish(status, summary, **more):
        event = {'kind': 'done', 'status': status, 'summary': summary, 'reset_at': None, 'tokens': 0, **more}
        on_event(event)
        return {**event, 'session': state.get('session')}

    state: dict = {}
    exe = we.binary(engine)
    if not exe:
        return finish('missing', f'{we.NAMES[engine]} is not set up on this PC. {we.HOW[engine]}')
    signed = we.check(engine)
    if not signed['ok']:
        return finish('signed_out', f"Your {we.NAMES[engine]} {signed['why']}. {signed['how']}")
    try:
        cmd = command(engine, exe, Path(folder), mode, resume)
    except ValueError as exc:
        return finish('failed', str(exc))
    env = we._env()
    env.update(env_extra or {})
    try:
        proc = subprocess.Popen(cmd, cwd=str(folder), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', bufsize=1,
                                **we._hidden())
    except OSError as exc:
        return finish('failed', f'{we.NAMES[engine]} could not start: {exc}')
    run_id = run_id or f'code-{id(proc)}'
    we.track(run_id, proc)
    err_lines: list[str] = []

    def feed_stdin():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    def drain_stderr():
        for line in proc.stderr:
            err_lines.append(line)
            del err_lines[:-200]

    timed_out = threading.Event()
    threading.Thread(target=feed_stdin, daemon=True).start()
    threading.Thread(target=drain_stderr, daemon=True).start()
    timer = threading.Timer(timeout, lambda: (timed_out.set(), we._kill_tree(proc)))
    timer.daemon = True
    timer.start()
    final = None
    try:
        for line in proc.stdout:
            for event in parse(engine, line, Path(folder), state):
                if event['kind'] == 'session':
                    state['session'] = event['id']
                if event['kind'] == 'done':
                    final = event                    # sent last, after the process has ended
                    continue
                on_event(event)
        proc.wait()
    finally:
        timer.cancel()
        was_stopped = we.untrack(run_id)
    if was_stopped:
        return finish('stopped', 'You stopped it. Everything it changed so far is still in the session.')
    if timed_out.is_set():
        return finish('failed', f'It worked for {timeout // 60} minutes without finishing, so Apex stopped it.')
    err = ''.join(err_lines)
    if final is None:
        detail = error_text(state.get('error') or '') or error_text(err) or \
            f'{we.NAMES[engine]} ended without finishing (exit {proc.returncode}).'
        status = we._classify(detail + '\n' + err[-3000:], True)
        final = {'kind': 'done', 'status': status, 'summary': detail,
                 'reset_at': we.reset_time(detail + '\n' + err) if status == 'limited' else None, 'tokens': 0}
    if final['status'] == 'signed_out':
        we.forget_checks()
    on_event(final)
    return {**final, 'session': state.get('session')}
