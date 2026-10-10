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

Apex's own memory server (agent/mcp_server.py) is the one MCP server a session
gets: its reading tools in every mode, and `remember`, which only stages a
memory for the owner's approval, never in review. Claude Code loads it from a
file (--mcp-config, with --strict-mcp-config so nothing else loads); Codex gets
the same server as config overrides.

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

from agent import code_usage, work_engines as we
from agent.working_context import redact

MODES = ('safe', 'full', 'review')
TURN_TIMEOUT = 3600                         # one message's work: at most an hour
SAFE_COMMANDS = ['git status', 'git diff', 'git log', 'git show', 'ls', 'python -m pytest', 'pytest',
                 'python -m py_compile', 'node --check', 'node scripts/', 'npm test', 'npm run test', 'npm run lint']
FILE_TOOLS = ['Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep', 'TodoWrite', 'WebSearch', 'WebFetch']
CLAUDE_TOOLS = {
    'safe': FILE_TOOLS + [f'Bash({c}:*)' for c in SAFE_COMMANDS],
    'full': FILE_TOOLS + ['Bash', 'PowerShell'],
    # Checks can write files or run arbitrary project scripts. Apex runs them
    # separately; a Claude reviewer only gets readers.
    'review': ['Read', 'Glob', 'Grep'],
}
REVIEW_DENIED = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Bash', 'PowerShell', 'Agent', 'Task']
QUIET_TOOLS = ('ExitPlanMode',)          # Plan first stops it on purpose: not an error to show
CODEX_SANDBOX = {'safe': 'workspace-write', 'full': 'danger-full-access', 'review': 'read-only'}
SESSION_ID = re.compile(r'^[A-Za-z0-9_-]{6,80}$')   # what may be put on a command line
OUTPUT_TAIL = 2000
RETRYING = re.compile(r'^(Reconnecting|Falling back|Retrying)\b', re.I)
# The tool no longer has the conversation to resume (seen with the real tools).
RESUME_LOST = re.compile(r'no rollout found|No conversation found', re.I)
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
CODEX_EFFORT = {value: value for value in EFFORTS} | {'max': 'high'}
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}$')
ALLOWED_COMMAND = re.compile(r'^[^\n\r()]{1,300}$')     # fits inside a Bash(...) rule
PLAN_ONLY = ('Plan only, for now: read what you need, then write a short step-by-step plan for the request below '
             '(the files you would change and how, and how you would check it). Do not change any file yet.')
DIFF_CAP = 12000
# Apex's memory server: these only read (and what they return is redacted); remember stages for approval.
APEX_MCP_READ = ['mcp__apex__context', 'mcp__apex__recall', 'mcp__apex__lessons', 'mcp__apex__search_files']
APEX_MCP_WRITE = ['mcp__apex__remember']
ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')     # a bare TOML key, and a plain environment name


def error_text(text: str) -> str:
    """A tool's error in its own words, without a stack trace or retry chatter."""
    text = re.split(r'\n\s*Stack backtrace:', text or '', maxsplit=1)[0]
    keep = [l for l in text.splitlines() if not RETRYING.match(l.strip().removeprefix('ERROR:').strip())]
    return we._tidy('\n'.join(keep))


def _file_option(o: dict, key: str, what: str) -> None:
    """A file passed on the command line: one that exists, by its full path, on one line."""
    if o.get(key):
        path = str(o[key])
        if '\n' in path or '\r' in path or not os.path.isabs(path) or not os.path.isfile(path):
            raise ValueError(f'{what} is passed as a file that exists, by its full path.')
        o[key] = path
    else:
        o[key] = ''


# A request to MAKE an image, not any mention of one ("fix the logo alignment" isn't).
IMAGE_REQUEST = re.compile(r'\b(generate|create|make|draw|design|render|produce|paint)\b[^.\n]{0,60}?'
                           r'\b(images?|pictures?|illustrations?|logos?|banners?|icons?|artwork|wallpapers?)\b'
                           r'|\bimagegen\b', re.I)


def wants_images(text: str) -> bool:
    """Whose words: Apex Code passes only the owner's own message (options['images']),
    never its brief or recap, which mention images for other reasons."""
    return bool(IMAGE_REQUEST.search(text or ''))


def check_options(options: dict | None) -> dict:
    """Model, effort, plan-only, allowed commands, the brief file and the memory
    server's file for one message, checked: every value here ends up on a command line."""
    o = dict(options or {})
    _file_option(o, 'system_file', 'The brief')
    _file_option(o, 'mcp_file', "Apex's memory server")
    if o.get('model') and not MODEL.match(str(o['model'])):
        raise ValueError('That model name has characters Apex does not pass on.')
    if o.get('effort') and o['effort'] not in EFFORTS:
        raise ValueError(f"Effort is one of: {', '.join(EFFORTS)}.")
    for key in ('allow', 'always'):
        cmds = o.get(key) or []
        if not isinstance(cmds, list) or any(not isinstance(c, str) or not ALLOWED_COMMAND.match(c) for c in cmds):
            raise ValueError('An allowed command is one line, without brackets, at most 300 characters.')
        o[key] = [c.strip() for c in cmds if c.strip()]
    o['plan'] = bool(o.get('plan'))
    return o


def command(engine: str, exe: str, folder: Path, mode: str, resume: str | None = None, options: dict | None = None) -> list[str]:
    if mode not in MODES:
        raise ValueError('Mode is safe, full or review.')
    if resume and not SESSION_ID.match(resume):
        raise ValueError('That session id is not one Apex made.')
    o = check_options(options)
    if engine == 'claude':
        tools = list(CLAUDE_TOOLS[mode])
        if mode != 'review':                 # Allow once: exactly that command. Always: it, with any arguments.
            tools += [f'Bash({c})' for c in o['allow']]
            tools += [f'Bash({c}:*)' for c in o['always']]
        if o['mcp_file']:                    # Apex's memory: reading always; suggesting a memory, not in a review
            tools += APEX_MCP_READ + (APEX_MCP_WRITE if mode != 'review' else [])
        cmd = [exe, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
               '--permission-mode', 'plan' if o['plan'] or mode == 'review' else 'acceptEdits', '--allowedTools', *tools, '--strict-mcp-config']
        if o['mcp_file']:
            cmd += ['--mcp-config', o['mcp_file']]          # the only MCP server it loads
        if o['system_file']:
            # What Apex knows about you (agent/code_brain.py), as a file: multi-line text
            # on the command line would go through cmd.exe on Windows (claude.cmd).
            cmd += ['--append-system-prompt-file', o['system_file']]
        if mode == 'review':
            cmd += ['--tools', 'Read,Glob,Grep', '--disallowedTools', *REVIEW_DENIED]
        if o.get('model'):
            cmd += ['--model', o['model']]
        if o.get('effort'):
            cmd += ['--effort', o['effort']]
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
    # Codex can't allow one command: an allowed command gets that message full access.
    sandbox = 'read-only' if o['plan'] else 'danger-full-access' if o['allow'] and mode != 'review' else CODEX_SANDBOX[mode]
    cmd += ['--json', '--skip-git-repo-check', '-c', f'sandbox_mode={sandbox}']
    if o.get('model'):
        cmd += ['-m', o['model']]
    if o.get('effort'):
        cmd += ['-c', f"model_reasoning_effort={CODEX_EFFORT[o['effort']]}"]
    if o['mcp_file']:
        cmd += _codex_mcp(o['mcp_file'], mode)
    mode_nt = we.codex_windows_sandbox()
    if mode_nt and os.name == 'nt':
        cmd += ['-c', f'windows.sandbox={mode_nt}']
    if resume:
        cmd += [resume]
    return cmd + ['-']                         # '-': the message comes in on stdin


def _codex_mcp(path: str, mode: str) -> list[str]:
    """Apex's memory server for Codex, from the same file Claude Code reads, as
    config overrides. json.dumps writes a valid TOML basic string (Windows
    backslashes included); ensure_ascii=False keeps an accent in a path as it is.
    In a review the server itself refuses to suggest a memory."""
    try:
        server = json.loads(Path(path).read_text(encoding='utf-8'))['mcpServers']['apex']
        command, args, env = str(server['command']), [str(a) for a in server['args']], dict(server.get('env') or {})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Apex's memory server file can't be read: {exc}") from exc
    if mode == 'review':
        env['APEX_CODE_READ_ONLY'] = '1'
    if any(not ENV_NAME.match(str(k)) for k in env):
        raise ValueError("Apex's memory server file names an environment variable Apex does not pass on.")
    table = ', '.join(f'{k}={json.dumps(str(v), ensure_ascii=False)}' for k, v in env.items())
    return ['-c', f'mcp_servers.apex.command={json.dumps(command, ensure_ascii=False)}',
            '-c', f'mcp_servers.apex.args={json.dumps(args, ensure_ascii=False)}',
            '-c', f'mcp_servers.apex.env={{{table}}}']


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


def _run_git(folder, *args) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=str(folder), capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=30, **we._hidden())
    except (OSError, subprocess.SubprocessError):
        return None


def _cap(text: str) -> str:
    return text if len(text) <= DIFF_CAP else text[:DIFF_CAP] + f'\n… (cut: {len(text):,} characters in all)'


def file_diff(folder, path: str) -> str:
    """What this step did to a file so far: git's diff since the last checkpoint,
    or the whole file if it is new."""
    p = _run_git(folder, 'diff', '--no-renames', 'HEAD', '--', path)
    text = p.stdout if p and p.returncode == 0 else ''
    if text.strip():
        return _cap('\n'.join(l for l in text.splitlines() if not l.startswith(('diff --git', 'index ', '--- ', '+++ '))))
    tracked = _run_git(folder, 'ls-files', '--error-unmatch', '--', path)
    if tracked is not None and tracked.returncode == 0:
        return ''                                          # unchanged since the last checkpoint
    try:
        lines = (Path(folder) / path).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return ''
    return _cap(f'@@ -0,0 +1,{len(lines)} @@\n' + '\n'.join('+' + l for l in lines))


def edit_hunk(folder, path: str, old, new) -> str:
    """One edit as a diff hunk with real line numbers: found in the file as it is now."""
    a, b = str(old or '').splitlines(), str(new or '').splitlines()
    try:
        text = (Path(folder) / path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        text = ''
    idx = text.find(str(new)) if new else -1
    body = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            body += [' ' + l for l in a[i1:i2]]
        else:
            body += ['-' + l for l in a[i1:i2]] + ['+' + l for l in b[j1:j2]]
    if idx < 0:
        return _cap('\n'.join(['@@ @@'] + body))
    lines = text.splitlines()
    start = text[:idx].count('\n')
    before, after = lines[max(0, start - 3):start], lines[start + len(b):start + len(b) + 3]
    first = start - len(before) + 1
    head = f'@@ -{first},{len(before) + len(a) + len(after)} +{first},{len(before) + len(b) + len(after)} @@'
    return _cap('\n'.join([head] + [' ' + l for l in before] + body + [' ' + l for l in after]))


def diff_counts(diff: str) -> tuple[int, int]:
    lines = diff.splitlines()
    return (sum(1 for l in lines if l.startswith('+')), sum(1 for l in lines if l.startswith('-')))


MEMORY_TOOLS = {'context': 'what it knows about you and this project', 'lessons': 'what works and what fails',
                'skills': 'its skills'}


def _memory_tool(tool: str, args: dict) -> dict:
    """A call to Apex's memory server, in words: what it asked, or what it suggested."""
    args = args if isinstance(args, dict) else {}
    if tool == 'remember':
        return {'kind': 'tool', 'tool': 'memory', 'title': f"Suggested a memory for your OK: {_short(args.get('content'), 160)}"}
    what = args.get('query') or args.get('name') or MEMORY_TOOLS.get(tool) or tool
    return {'kind': 'tool', 'tool': 'memory', 'title': f"Checked Apex's memory: {_short(what, 120)}"}


def _claude_tool(name: str, args: dict, folder: Path) -> list[dict]:
    """A tool call as feed events: what it does, in a few words."""
    if name.startswith('mcp__apex__'):
        return [_memory_tool(name.removeprefix('mcp__apex__'), args)]
    path = _rel(args.get('file_path') or args.get('notebook_path') or args.get('path') or '', folder)
    if name in ('Write', 'Edit', 'MultiEdit') and (path.startswith(('/', '../')) or re.match(r'^[A-Za-z]:/', path)):
        # Outside the project: Plan first saves its plan in Claude's own folder.
        if '/.claude/plans/' in path:
            return [{'kind': 'note', 'text': 'Wrote down its plan.'}]
        return [{'kind': 'tool', 'tool': 'other', 'title': f'Wrote {path} (outside the project)'}]
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
        return [{'kind': 'tool', 'tool': 'command', 'title': _short(redact(str(args.get('command') or '')), 200),
                 'command': _cap(redact(str(args.get('command') or ''))),
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
            state['model'] = d.get('model') or None
            out.append({'kind': 'session', 'id': d['session_id'], 'model': d.get('model') or ''})
        elif d.get('subtype') == 'api_retry':
            out.append({'kind': 'note', 'text': 'The plan is busy; retrying…'})
        return out
    if kind == 'stream_event':                            # live chunks, as Claude writes
        ev = d.get('event') or {}
        delta = ev.get('delta') or {} if ev.get('type') == 'content_block_delta' else {}
        if delta.get('type') == 'text_delta' and delta.get('text'):
            return [{'kind': 'delta', 'text': delta['text']}]
        if delta.get('type') == 'thinking_delta' and delta.get('thinking'):
            return [{'kind': 'delta', 'thinking': delta['thinking']}]
        return []
    if kind == 'assistant':
        if (d.get('message') or {}).get('model'):
            state['model'] = d['message']['model']
        for block in (d.get('message') or {}).get('content') or []:
            t = block.get('type')
            if t == 'text' and block.get('text', '').strip():
                state['last_text'] = block['text']
                out.append({'kind': 'text', 'text': block['text']})
            elif t == 'thinking' and block.get('thinking', '').strip():
                out.append({'kind': 'thinking', 'text': _tail(block['thinking'], 4000)})
            elif t == 'tool_use':
                name, args = block.get('name') or '', block.get('input') or {}
                state.setdefault('tools', {})[block.get('id')] = name
                if name in QUIET_TOOLS:
                    continue
                made = [{**e, 'id': block.get('id')} for e in _claude_tool(name, args, folder)]
                if made and made[0]['kind'] == 'file':
                    # The edit happens after this message: show it, with its diff, once it's done.
                    state.setdefault('pending', {})[block.get('id')] = (name, args, made)
                else:
                    out += made
        return out
    if kind == 'user':
        content = (d.get('message') or {}).get('content')
        for block in content if isinstance(content, list) else []:
            if block.get('type') != 'tool_result':
                continue
            name = state.get('tools', {}).get(block.get('tool_use_id'))
            if name in QUIET_TOOLS:
                continue
            text = block.get('content')
            if isinstance(text, list):
                text = '\n'.join(str(x.get('text', '')) for x in text if isinstance(x, dict))
            ok = not block.get('is_error')
            pending = state.get('pending', {}).pop(block.get('tool_use_id'), None)
            if pending:
                tname, args, made = pending
                if not ok:
                    out.append({'kind': 'error', 'text': f"Couldn't change {made[0]['path']}: {_short(text, 300)}"})
                    continue
                for e in made:
                    if tname in ('Edit', 'MultiEdit'):
                        edits = args.get('edits') if tname == 'MultiEdit' else [args]
                        e['diff'] = _cap('\n'.join(edit_hunk(folder, e['path'], x.get('old_string'), x.get('new_string'))
                                                   for x in edits or []))
                    else:
                        e['diff'] = file_diff(folder, e['path'])
                        if e['diff']:
                            e['plus'], e['minus'] = diff_counts(e['diff'])
                            e['change'] = 'add' if e['diff'].startswith('@@ -0,0 ') else 'update'
                    out.append(e)
                continue
            # Command output is worth showing; a file's contents are not (they're in the diff).
            show = name in ('Bash', 'PowerShell') or not ok
            out.append({'kind': 'result', 'id': block.get('tool_use_id'), 'ok': ok,
                        'output': _tail(text) if show else ''})
        return out
    if kind == 'result':
        for denied in d.get('permission_denials') or []:
            args = denied.get('tool_input') or {}
            what = args.get('command') or args.get('file_path') or args.get('url') or ''
            out.append({'kind': 'blocked', 'title': f"{denied.get('tool_name')}: {_short(what, 160)}",
                        'command': args.get('command') if denied.get('tool_name') in ('Bash', 'PowerShell') else None})
        failed = bool(d.get('is_error')) or d.get('subtype') not in (None, 'success')
        detail = '\n'.join([str(d.get('result') or '')] + [str(e) for e in d.get('errors') or []])
        status = we._classify(detail, failed)
        usage = code_usage.turn_usage('claude', d.get('usage'), model=state.get('model'),
                                      cost=d.get('total_cost_usd'), model_usage=d.get('modelUsage'))
        out.append({'kind': 'done', 'status': status,
                    'summary': str(d.get('result') or '') if not failed else error_text(detail) or 'It stopped with an error.',
                    'reset_at': we.reset_time(detail) if status == 'limited' else None,
                    'usage': usage, 'tokens': usage['total_tokens']})
        if d.get('session_id'):
            state['session'] = d['session_id']
        return out
    return out


def _parse_codex(d: dict, folder: Path, state: dict) -> list[dict]:
    kind = d.get('type')
    if kind == 'thread.started' and d.get('thread_id'):
        return [{'kind': 'session', 'id': d['thread_id'], 'model': ''}]
    if kind == 'turn.completed':
        usage = code_usage.turn_usage('chatgpt', d.get('usage'))
        return [{'kind': 'done', 'status': 'done', 'summary': state.get('last_text', ''), 'reset_at': None,
                 'tokens': usage['total_tokens'], 'usage': usage}]
    if kind in ('turn.failed', 'error'):
        msg = (d.get('error') or {}).get('message') if kind == 'turn.failed' else d.get('message')
        state['error'] = str(msg or '')
        if kind == 'error':
            if RETRYING.match(str(msg or '')):
                return [{'kind': 'note', 'text': 'The plan is slow to answer; retrying…'}]
            return [{'kind': 'error', 'text': error_text(str(msg or 'Codex reported an error.'))}]
        status = we._classify(state['error'], True)
        return [{'kind': 'done', 'status': status, 'summary': error_text(state['error']) or 'Codex stopped with an error.',
                 'reset_at': we.reset_time(state['error']) if status == 'limited' else None, 'tokens': None,
                 'usage': code_usage.turn_usage('chatgpt')}]
    if kind not in ('item.started', 'item.updated', 'item.completed'):
        return []
    item = d.get('item') or {}
    t, iid, done = item.get('type'), item.get('id'), kind == 'item.completed'
    if t == 'agent_message' and done and str(item.get('text', '')).strip():
        state['last_text'] = item['text']
        return [{'kind': 'text', 'text': item['text']}]
    if t == 'reasoning' and done and str(item.get('text', '')).strip():
        return [{'kind': 'thinking', 'text': _tail(item['text'], 4000)}]
    if t == 'agent_message' and kind == 'item.updated' and item.get('text'):
        return [{'kind': 'delta', 'replace': item['text']}]
    if t == 'command_execution' and kind == 'item.updated' and item.get('aggregated_output'):
        return [{'kind': 'live', 'ref': iid, 'output': _tail(item['aggregated_output'])}]
    if t == 'command_execution':
        if kind == 'item.started':
            return [{'kind': 'tool', 'tool': 'command', 'title': _short(redact(str(item.get('command') or '')), 200),
                     'command': _cap(redact(str(item.get('command') or ''))), 'id': iid}]
        if done:
            return [{'kind': 'result', 'id': iid, 'ok': item.get('exit_code') == 0 and item.get('status') != 'failed',
                     'exit_code': item.get('exit_code'), 'output': _tail(item.get('aggregated_output'))}]
    if t == 'file_change' and done:
        out = []
        for c in item.get('changes') or []:
            path = _rel(c.get('path', ''), folder)
            diff = file_diff(folder, path) if c.get('kind') != 'delete' else ''
            plus, minus = diff_counts(diff) if diff else (None, None)
            out.append({'kind': 'file', 'path': path, 'id': iid, 'diff': diff, 'plus': plus, 'minus': minus,
                        'change': {'add': 'add', 'delete': 'delete'}.get(c.get('kind'), 'update')})
        return out
    if t == 'web_search' and kind == 'item.started':
        return [{'kind': 'tool', 'tool': 'web', 'title': f"Searched the web: {_short(item.get('query'), 120)}", 'id': iid}]
    if t == 'web_search' and done:
        failed = item.get('status') in ('failed', 'declined') or bool(item.get('error'))
        error = item.get('error')
        why = error.get('message') if isinstance(error, dict) else error
        return [{'kind': 'result', 'id': iid, 'ok': not failed,
                 'status': 'failed' if failed else item.get('status') or 'completed',
                 'output': _tail(redact(why)) if why else ''}]
    if t == 'todo_list':
        return [{'kind': 'todo', 'items': [{'text': _short(x.get('text'), 160), 'done': bool(x.get('completed')), 'active': False}
                                            for x in item.get('items') or []]}]
    if t == 'mcp_tool_call' and kind == 'item.started':
        if item.get('server') == 'apex':
            return [{**_memory_tool(str(item.get('tool') or ''), item.get('arguments')), 'id': iid}]
        return [{'kind': 'tool', 'tool': 'other', 'title': f"{item.get('server', '')} {item.get('tool', '')}".strip(), 'id': iid}]
    if t == 'mcp_tool_call' and done:
        result = item.get('result')
        result_failed = isinstance(result, dict) and bool(result.get('isError') or result.get('is_error'))
        failed = item.get('status') in ('failed', 'declined') or bool(item.get('error')) or result_failed
        why = (item.get('error') or {}).get('message') if isinstance(item.get('error'), dict) else item.get('error')
        # Memory values are deliberately omitted from the feed. Other tools
        # need their completion paired with the running row, with bounded text.
        output = _tail(redact(why)) if failed and why else ''
        if not why and item.get('server') != 'apex':
            if isinstance(result, dict):
                output = _tail(redact('\n'.join(str(c.get('text') or '') for c in result.get('content') or []
                                               if isinstance(c, dict) and c.get('type') == 'text')))
            elif isinstance(result, str):
                output = _tail(redact(result))
        return [{'kind': 'result', 'id': iid, 'ok': not failed,
                 'status': 'failed' if failed else item.get('status'), 'output': output}]
    if t == 'error' and done:
        return [{'kind': 'error', 'text': _short(item.get('message'), 400)}]
    return []


# ---------------------------------------------------------------- one turn

def turn(engine: str, prompt: str, folder: Path, mode: str = 'safe', resume: str | None = None,
         on_event: Callable[[dict], None] = lambda e: None, run_id: str | None = None,
         timeout: int = TURN_TIMEOUT, env_extra: dict | None = None, options: dict | None = None) -> dict:
    """Run one message's work, calling on_event for each step. Never raises
    for a failed run. Returns the final 'done' event plus the session id."""
    def finish(status, summary, **more):
        event = {'kind': 'done', 'status': status, 'summary': summary, 'reset_at': None, 'tokens': None,
                 'usage': code_usage.turn_usage(engine, model=state.get('model')), **more}
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
        cmd = command(engine, exe, Path(folder), mode, resume, options)
    except ValueError as exc:
        return finish('failed', str(exc))
    if (options or {}).get('plan'):
        prompt = f'{PLAN_ONLY}\n\n{prompt}'
    if engine == 'chatgpt' and mode != 'review':
        images = (options or {}).get('images')
        if wants_images(prompt) if images is None else images:
            prompt = ('For requested images, use your built-in image_gen/imagegen tool with this ChatGPT sign-in. '
                      'Save generated raster assets inside generated_images/ in this worktree so Apex can display them. '
                      'Never substitute a drawing for a requested generated image, or use API/Replicate credentials. '
                      'If image generation is unavailable, report that clearly.\n\n' + prompt)
        prompt = we.codex_prompt(prompt)
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
                 'reset_at': we.reset_time(detail + '\n' + err) if status == 'limited' else None, 'tokens': None,
                 'usage': code_usage.turn_usage(engine, model=state.get('model'))}
    if final['status'] == 'signed_out':
        we.forget_checks()
    on_event(final)
    return {**final, 'session': state.get('session')}
