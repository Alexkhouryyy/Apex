"""Who does a Work task: your Claude plan, your ChatGPT plan, or the API.

Your subscriptions are used through their official command-line tools, signed
in with your own account on this PC, so a task counts against your plan's
usage, not API credits:

  claude    Claude Code (`claude -p`), signed in with your Claude Pro/Max plan
            (run `claude` once and choose your Claude account)
  chatgpt   OpenAI Codex (`codex exec`), signed in with ChatGPT
            (run `codex` once and choose "Sign in with ChatGPT")
  api       Apex's own task runner (agent/team.py), paid from API credits

Both tools run inside the task's own folder and may only write there:
Claude Code with file and web tools only (no shell), Codex in its
workspace-write sandbox. The task text goes in on stdin, never on the command
line: on Windows these tools are often .cmd files run through cmd.exe, where
characters like & or % in a task title would be read as commands. When a
plan's usage limit is reached, the run says so (`limited`), and the always-on
agent moves to the other plan.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ENGINES = ('claude', 'chatgpt', 'api')
NAMES = {'claude': 'Claude plan', 'chatgpt': 'ChatGPT plan', 'api': 'API credits'}
COMMANDS = {'claude': 'claude', 'chatgpt': 'codex'}
CLAUDE_TOOLS = ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'WebSearch', 'WebFetch']
TIMEOUT = 1800                       # 30 minutes per task
LIMIT = re.compile(r'usage limit|rate.?limit|limit (has been )?reached|hit your (usage )?limit|quota|too many requests|429', re.I)
SIGNED_OUT = re.compile(r'not (logged|signed) in|please (log|sign) ?in|/login|authenticat|unauthori[sz]ed|invalid api key', re.I)


def binary(engine: str) -> str | None:
    name = COMMANDS.get(engine)
    if not name:
        return None
    return shutil.which(name) or (shutil.which(name + '.cmd') if os.name == 'nt' else None)


def installed() -> dict:
    return {e: (True if e == 'api' else bool(binary(e))) for e in ENGINES}


def _classify(text: str, failed: bool) -> str:
    if not failed:                    # a finished task may well mention limits; only a failure is one
        return 'done'
    if LIMIT.search(text or ''):
        return 'limited'
    if SIGNED_OUT.search(text or ''):
        return 'signed_out'
    return 'failed'


def run(engine: str, prompt: str, folder: Path, timeout: int = TIMEOUT) -> dict:
    """Run one task with a subscription tool. Never raises for a failed run:
    returns {status: done|failed|limited|signed_out|missing, summary}."""
    exe = binary(engine)
    if not exe:
        how = 'Install Claude Code and run `claude` once to sign in.' if engine == 'claude' else \
              'Install Codex (npm install -g @openai/codex) and run `codex` once to sign in with ChatGPT.'
        return {'status': 'missing', 'summary': f'{NAMES[engine]} is not set up on this PC. {how}'}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if engine == 'claude':
        cmd = [exe, '-p', '--output-format', 'json', '--permission-mode', 'acceptEdits',
               '--allowedTools', *CLAUDE_TOOLS]
    else:
        last = folder / '.apex-last-message.txt'
        cmd = [exe, 'exec', '--skip-git-repo-check', '-C', str(folder), '--sandbox', 'workspace-write',
               '-o', str(last), '-']                # '-': read the task from stdin
    env = dict(os.environ)
    for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY'):
        env.pop(key, None)            # so the tools use your signed-in plan, never API credits
    try:
        proc = subprocess.run(cmd, cwd=str(folder), env=env, input=prompt, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'failed', 'summary': f'{NAMES[engine]} took longer than {timeout // 60} minutes and was stopped.'}
    except OSError as exc:
        return {'status': 'failed', 'summary': f'{NAMES[engine]} could not start: {exc}'}
    out, err = proc.stdout or '', proc.stderr or ''
    if engine == 'claude':
        try:
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except ValueError:
            data = {}
        summary = str(data.get('result') or err or out)[-4000:]
        failed = proc.returncode != 0 or bool(data.get('is_error')) or data.get('subtype') not in (None, 'success')
    else:
        last = folder / '.apex-last-message.txt'
        summary = (last.read_text(encoding='utf-8', errors='replace') if last.exists() else (err or out))[-4000:]
        failed = proc.returncode != 0
        try:
            last.unlink()
        except OSError:
            pass
    status = _classify(summary + '\n' + err[-2000:], failed)
    return {'status': status, 'summary': summary.strip()}
