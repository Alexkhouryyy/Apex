"""Who does a Work task: your Claude plan, your ChatGPT plan, or the API.

Your subscriptions are used through their official command-line tools, signed
in with your own account on this PC, so a task counts against your plan's
usage, not API credits:

  claude    Claude Code (`claude -p`), signed in with your Claude Pro/Max plan
            (run `claude` once and choose your Claude account)
  chatgpt   OpenAI Codex (`codex exec`), signed in with ChatGPT
            (run `codex` once and choose "Sign in with ChatGPT")
  api       Apex's own task runner (agent/team.py), paid from API credits

Before a plan runs, `check()` asks the tool how it is signed in (`claude auth
status`, `codex login status`; neither uses any of your plan). A tool signed in
with an API key would bill credits, so Apex refuses it. Apex's own API keys
are also removed from the tool's environment.

Both tools run inside the task's own folder and may only write there:
Claude Code with file and web tools only (no shell), Codex in its
workspace-write sandbox. The task text goes in on stdin, never on the command
line: on Windows these tools are often .cmd files run through cmd.exe, where
characters like & or % in a task title would be read as commands. A timeout
or Stop ends the whole process tree, so nothing keeps running on your plan.

When a plan's usage limit is reached the run says so (`limited`) with the
reset time when the tool gives one, and the always-on agent moves to the
other plan.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ENGINES = ('claude', 'chatgpt', 'api')
NAMES = {'claude': 'Claude plan', 'chatgpt': 'ChatGPT plan', 'api': 'API credits'}
COMMANDS = {'claude': 'claude', 'chatgpt': 'codex'}
CLAUDE_TOOLS = ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'WebSearch', 'WebFetch']
TIMEOUT = 1800                       # 30 minutes per task
PLAN_METHODS = ('claude.ai', 'oauth_token')
CHECK_TTL = 600                      # how long a sign-in check is trusted
LIMIT = re.compile(r'usage limit|rate.?limit|limit (has been )?reached|hit your (usage )?limit|quota|too many requests|\b429\b', re.I)
SIGNED_OUT = re.compile(r'not (logged|signed) in|please (log|sign) ?in|/login|authenticat|unauthori[sz]ed|invalid api key|\b401\b', re.I)
HOW = {'claude': 'Run Setup-Apex-Work-Plans.cmd, or run `claude` once and choose your Claude account.',
       'chatgpt': 'Run Setup-Apex-Work-Plans.cmd, or run `codex login` and choose "Sign in with ChatGPT".'}

_procs: dict[str, subprocess.Popen] = {}     # run id -> the tool's process, for Stop
_stopped: set[str] = set()
_checks: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _usual_places() -> list[str]:
    """Where the installers put these tools, for when PATH hasn't caught up yet
    (Claude Code's installer: ~/.local/bin; npm on Windows: %APPDATA%\\npm)."""
    places = [str(Path.home() / '.local' / 'bin')]
    if os.environ.get('APPDATA'):
        places.append(str(Path(os.environ['APPDATA']) / 'npm'))
    return places


def binary(engine: str) -> str | None:
    name = COMMANDS.get(engine)
    if not name:
        return None
    return shutil.which(name) or shutil.which(name, path=os.pathsep.join(_usual_places()))


def installed() -> dict:
    return {e: (True if e == 'api' else bool(binary(e))) for e in ENGINES}


def _env() -> dict:
    env = dict(os.environ)
    for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'CODEX_API_KEY'):
        env.pop(key, None)            # so the tools use your signed-in plan, never API credits
    return env


def _hidden() -> dict:
    """No console window flashing up on Windows; a process group to end as one."""
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        proc.kill()


# ---------------------------------------------------------------- is it signed in with the plan?

def check(engine: str, fresh: bool = False) -> dict:
    """{'ok': bool, 'why': short phrase, 'how': what to do}. Uses none of your plan."""
    if engine == 'api':
        return {'ok': True, 'why': '', 'how': ''}
    now = time.time()
    with _lock:
        hit = _checks.get(engine)
    if hit and not fresh and now - hit[0] < CHECK_TTL:
        return hit[1]
    result = _check(engine)
    with _lock:
        _checks[engine] = (now, result)
    return result


def forget_checks() -> None:
    with _lock:
        _checks.clear()


def _check(engine: str) -> dict:
    exe = binary(engine)
    if not exe:
        return {'ok': False, 'why': 'is not installed on this PC', 'how': HOW[engine]}
    cmd = [exe, 'auth', 'status', '--json'] if engine == 'claude' else [exe, 'login', 'status']
    try:
        p = subprocess.run(cmd, env=_env(), capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=60, stdin=subprocess.DEVNULL, **_hidden())
    except (OSError, subprocess.SubprocessError) as exc:
        return {'ok': False, 'why': f'could not be asked how it is signed in ({type(exc).__name__})', 'how': HOW[engine]}
    out = (p.stdout or '') + '\n' + (p.stderr or '')
    if engine == 'claude':
        try:
            data = json.loads(p.stdout[p.stdout.index('{'):])
        except ValueError:
            return {'ok': False, 'why': 'gave a sign-in status Apex could not read', 'how': 'Update Claude Code (`claude update`).'}
        # Only a Claude account login (claude.ai) or a plan token from `claude setup-token`
        # (oauth_token) uses your plan. api_key, apiKeyHelper and third_party (Bedrock,
        # Vertex) bill credits; an apiKeySource means a key would be used.
        method = data.get('authMethod')
        if not data.get('loggedIn') or method == 'none':
            return {'ok': False, 'why': 'is not signed in', 'how': HOW[engine]}
        if method not in PLAN_METHODS or data.get('apiProvider', 'firstParty') != 'firstParty' or data.get('apiKeySource'):
            return {'ok': False, 'why': 'is signed in with an API key or cloud account, which bills credits, not your plan',
                    'how': 'Run `claude auth logout`, then `claude` and choose your Claude account.'}
        kind = str(data.get('subscriptionType') or '').capitalize()
        return {'ok': True, 'why': '', 'how': '', 'plan': f'Claude {kind}'.strip()}
    if p.returncode == 0 and re.search(r'^Logged in using ChatGPT\b', out, re.M):
        return {'ok': True, 'why': '', 'how': '', 'plan': 'ChatGPT'}
    m = re.search(r'^Logged in using (.+)$', out, re.M)
    if m:                                  # an API key, Bedrock, an access token...: not your ChatGPT plan
        how = re.sub(r'\s+-\s+\S+$', '', m[1].strip())       # never echo a key, even masked
        return {'ok': False, 'why': f'is signed in with {how}, which is not your ChatGPT plan',
                'how': 'Run `codex logout`, then `codex login` and choose "Sign in with ChatGPT".'}
    return {'ok': False, 'why': 'is not signed in', 'how': HOW[engine]}


# ---------------------------------------------------------------- outcomes

def _classify(text: str, failed: bool) -> str:
    if not failed:                    # a finished task may well mention limits; only a failure is one
        return 'done'
    if LIMIT.search(text or ''):
        return 'limited'
    if SIGNED_OUT.search(text or ''):
        return 'signed_out'
    return 'failed'


def reset_time(text: str, now: datetime | None = None) -> float | None:
    """When a plan's limit resets, if the tool said: 'limit reached|1760000000',
    'try again in 2 hours 5 minutes', 'resets 3pm', 'try again at 3:42 PM'."""
    now = now or datetime.now()
    text = text or ''
    m = re.search(r'limit reached\|(\d{9,11})', text)
    if m:
        return float(m[1])
    m = re.search(r'(?:try again|resets?) in ((?:\d+\s*(?:days?|hours?|hrs?|h|minutes?|mins?|m)\b[\s,and]*)+)', text, re.I)
    if m:
        secs = 0
        for n, unit in re.findall(r'(\d+)\s*(d|h|m)', m[1].lower()):
            secs += int(n) * {'d': 86400, 'h': 3600, 'm': 60}[unit]
        if secs:
            return (now + timedelta(seconds=secs)).timestamp()
    m = re.search(r'(?:try again at|resets?(?: at)?)\s+(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)', text, re.I)
    if m:
        hour = int(m[1]) % 12 + (12 if m[3].lower().startswith('p') else 0)
        at = now.replace(hour=hour, minute=int(m[2] or 0), second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        return at.timestamp()
    return None


def _tidy(text: str) -> str:
    """The tool's own words without its noise (warnings, timestamps, reconnect chatter)."""
    lines, seen = [], set()
    for line in (text or '').splitlines():
        s = re.sub(r'^\S*\d{4}-\d\d-\d\dT[\d:.]+Z\s+', '', line.strip())
        if not s or s.startswith(('WARNING:', 'warning:')) or re.match(r'ERROR: Reconnecting', s) or s in seen:
            continue
        seen.add(s); lines.append(s)
    return '\n'.join(lines[-6:])


# ---------------------------------------------------------------- one run

def run(engine: str, prompt: str, folder: Path, timeout: int = TIMEOUT, run_id: str | None = None) -> dict:
    """Run one task with a subscription tool. Never raises for a failed run:
    returns {status: done|failed|limited|signed_out|missing|stopped, summary, reset_at}."""
    exe = binary(engine)
    if not exe:
        return {'status': 'missing', 'summary': f'{NAMES[engine]} is not set up on this PC. {HOW[engine]}', 'reset_at': None}
    signed = check(engine)
    if not signed['ok']:
        return {'status': 'signed_out', 'summary': f"{NAMES[engine]} {signed['why']}. {signed['how']}", 'reset_at': None}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    last = folder / '.apex-last-message.txt'
    if engine == 'claude':
        cmd = [exe, '-p', '--output-format', 'json', '--permission-mode', 'acceptEdits', '--allowedTools', *CLAUDE_TOOLS]
    else:
        cmd = [exe, 'exec', '--skip-git-repo-check', '--ephemeral', '-C', str(folder), '--sandbox', 'workspace-write',
               '-o', str(last), '-']                # '-': read the task from stdin
    try:
        proc = subprocess.Popen(cmd, cwd=str(folder), env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', **_hidden())
    except OSError as exc:
        return {'status': 'failed', 'summary': f'{NAMES[engine]} could not start: {exc}', 'reset_at': None}
    if run_id:
        with _lock:
            _procs[run_id] = proc
    try:
        try:
            out, err = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()
            return {'status': 'failed', 'summary': f'{NAMES[engine]} took longer than {timeout // 60} minutes and was stopped.', 'reset_at': None}
    finally:
        if run_id:
            with _lock:
                _procs.pop(run_id, None)
                was_stopped = run_id in _stopped
                _stopped.discard(run_id)
    if run_id and was_stopped:
        return {'status': 'stopped', 'summary': 'You stopped it. Anything it wrote so far is in the folder.', 'reset_at': None}
    out, err = out or '', err or ''
    if engine == 'claude':
        try:
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except ValueError:
            data = {}
        failed = proc.returncode != 0 or bool(data.get('is_error')) or data.get('subtype') not in (None, 'success')
        summary = str(data.get('result') or '') or _tidy(err or out)
    else:
        failed = proc.returncode != 0
        text = last.read_text(encoding='utf-8', errors='replace') if last.exists() else ''
        summary = text.strip() or _tidy(err or out)
        try:
            last.unlink()
        except OSError:
            pass
    detail = summary + '\n' + err[-3000:]
    status = _classify(detail, failed)
    if status == 'signed_out':
        forget_checks()                   # ask again next time instead of trusting the cache
    return {'status': status, 'summary': summary.strip()[-4000:],
            'reset_at': reset_time(detail) if status == 'limited' else None}


def stop(run_id: str) -> bool:
    """End a running plan task and everything it started."""
    with _lock:
        proc = _procs.get(run_id)
        if proc:
            _stopped.add(run_id)
    if not proc:
        return False
    _kill_tree(proc)
    return True
