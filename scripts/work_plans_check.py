"""Check that Apex's Work tasks can run on your Claude and ChatGPT plans.

    python scripts/work_plans_check.py          # no usage: installed, signed in with the plan, flags
    python scripts/work_plans_check.py --live   # also one tiny real task per plan (a few seconds of usage)
    python scripts/work_plans_check.py --signed-in claude   # exit 0 if signed in with the plan
    python scripts/work_plans_check.py --code   # also Apex Code's path: a live coding step and a follow-up

For each plan:
  1. installed: where the tool is and its version;
  2. signed in with the plan, not an API key (an API key would bill credits);
  3. the tool still accepts every option Apex passes it (they change between
     versions);
  4. with --live: a real task in a scratch folder. The plan must write
     apex-check.md containing "ready" and finish as `done`, the same way Work
     tasks run.

Prints PASS / FAIL per step and exits 1 if anything a plan needs failed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import os
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import work_engines as we  # noqa: E402

FLAGS = {
    'claude': (['--help'], ['-p', '--output-format', '--permission-mode', '--allowedTools', '--tools', '--strict-mcp-config']),
    'chatgpt': (['exec', '--help'], ['--skip-git-repo-check', '--ephemeral', '--cd', '--sandbox', 'workspace-write',
                                     '--output-last-message', 'read from stdin']),
}
CODE_TASKS = ('Create a file named apex-code-check.md in this folder containing exactly the word ready. '
              'Then reply with one short sentence.',
              'Add a second line to apex-code-check.md containing exactly the word again. Then reply with one short sentence.')
LIVE_TASK = ('Create a file named apex-check.md in the current folder containing exactly the word ready. '
             'Then reply with one short sentence saying you did it.')


def _say(ok: bool, text: str) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {text}")
    return ok


def _run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                          env=we._env(), stdin=subprocess.DEVNULL, **we._hidden())


def check_plan(engine: str, live: bool) -> bool:
    print(f"\n{we.NAMES[engine]} ({we.COMMANDS[engine]})")
    exe = we.binary(engine)
    if not _say(bool(exe), f'installed: {exe}' if exe else f'not installed. {we.HOW[engine]}'):
        return False
    try:
        version = _run([exe, '--version']).stdout.strip().splitlines()[-1]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = 'unknown version'
    print(f'        version: {version}')
    signed = we.check(engine, fresh=True)
    ok = _say(signed['ok'], f"signed in with your plan ({signed.get('plan') or 'plan'})" if signed['ok'] else f"{signed['why']}. {signed['how']}")
    if engine == 'claude' and not signed['ok'] and os.environ.get('ANTHROPIC_API_KEY'):
        print('        Note: this PC has ANTHROPIC_API_KEY set, so plain `claude` runs on API credits and looks')
        print('        signed in. Apex hides that key from Claude Code, so it needs your Claude account:')
        print('          claude auth login --claudeai')
    args, needed = FLAGS[engine]
    try:
        help_text = _run([exe, *args]).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        help_text = str(exc)
    missing = [f for f in needed if f not in help_text]
    ok &= _say(not missing, 'accepts every option Apex uses' if not missing else
               f"this version doesn't know {', '.join(missing)}: update it, or tell Apex's maintainer")
    if not live or not ok:
        return ok
    result, wrote = _live(engine, None)
    ok &= result['status'] == 'done' and wrote is True
    if wrote == 'unreadable' and engine == 'chatgpt' and os.name == 'nt':
        # Codex wrote it, but Windows won't let you read it: try Codex's other Windows sandbox mode.
        current = we.codex_windows_sandbox() or "Codex's default"
        print(f'        Trying again with Codex\'s "unelevated" Windows sandbox (now: {current})...')
        result2, wrote2 = _live(engine, 'unelevated')
        if result2['status'] == 'done' and wrote2 is True:
            print('  FIX   That works. Run this once, then restart Apex:')
            print('          .venv\\Scripts\\python.exe scripts\\set_env_key.py WORK_CODEX_WINDOWS_SANDBOX unelevated')
    return ok


def _fresh(work_dir, name: str) -> Path:
    """A new, empty folder for one check: never one an earlier check left behind
    (two checks in the same second would otherwise share it and see its files)."""
    parent = Path(work_dir) / '_plan-check'
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{name}-{time.strftime('%Y%m%d-%H%M%S')}-", dir=parent))


def _readable(path: Path, tries: int = 10, word: str = 'ready'):
    """True if it holds the word, False if missing or wrong, 'unreadable' if Windows
    refuses. Retries for a few seconds, so a file held for a moment isn't mistaken."""
    error = None
    for i in range(tries):
        if not path.exists():
            return False
        try:
            return word in path.read_text(encoding='utf-8', errors='replace').lower()
        except PermissionError as exc:
            error = exc
            time.sleep(0.5)
    print(f'        Windows would not let Apex read it: {error}')
    if os.name == 'nt':
        for cmd in (['icacls', str(path)], ['tasklist', '/fi', 'imagename eq codex*']):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, errors='replace', timeout=30).stdout.strip()
            except (OSError, subprocess.SubprocessError) as exc2:
                out = str(exc2)
            print(f"        {' '.join(cmd[:2])}:\n          " + out.replace('\n', '\n          '))
    return 'unreadable'


def _live(engine: str, windows_sandbox):
    """One tiny real task in the same place real Work tasks go (ApexWork), left there to look at."""
    from agent import work
    folder = _fresh(work.WORK_DIR, engine + (f'-{windows_sandbox}' if windows_sandbox else ''))
    started = time.time()
    try:
        result = we.run(engine, LIVE_TASK, folder, timeout=300, windows_sandbox=windows_sandbox)
    except Exception as exc:                        # report it, never crash the check
        result = {'status': 'failed', 'summary': f'{type(exc).__name__}: {exc}'}
    took = time.time() - started
    _say(result['status'] == 'done', f"a real task finished as {result['status']} in {took:.0f} s")
    print('        it said: ' + (result['summary'].replace('\n', ' ')[:300] or '(nothing)'))
    if result['status'] == 'limited':
        print('        (your plan is at its usage limit right now: that is the plan, not Apex. Try again after it resets.)')
    wrote = _readable(folder / 'apex-check.md')
    _say(wrote is True, {True: 'it wrote apex-check.md and Apex can read it', False: 'it did not write apex-check.md',
                         'unreadable': 'it wrote apex-check.md, but Windows blocks reading it'}[wrote])
    print(f'        folder: {folder}')
    return result, wrote
    return ok


def code_check(engine: str) -> bool:
    """Apex Code's own path (agent/code_engines.py), as a session uses it: the live
    stream in a git project, then a follow-up that resumes the same conversation."""
    from agent import code_engines, work
    print(f"\n{we.NAMES[engine]}: Apex Code")
    folder = _fresh(work.WORK_DIR, f'code-{engine}')
    git = ['git', '-c', 'user.name=Apex check', '-c', 'user.email=apex-check@localhost']
    try:
        subprocess.run(git + ['init', '-q'], cwd=folder, check=True, capture_output=True)
        (folder / 'README.md').write_text('Apex Code check\n', encoding='utf-8')
        subprocess.run(git + ['add', '-A'], cwd=folder, check=True, capture_output=True)
        subprocess.run(git + ['commit', '-q', '-m', 'start'], cwd=folder, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return _say(False, f'git is needed for Apex Code and did not work here: {exc}')
    made = folder / 'apex-code-check.md'
    steps: list = []
    first = code_engines.turn(engine, CODE_TASKS[0], folder, 'safe', None, steps.append, timeout=300)
    _how(steps)
    wrote = _readable(made)
    ok = _say(first['status'] == 'done' and wrote is True,
              f"a live coding step finished as {first['status']}, with {len(steps) - 1} steps streamed"
              + (', but Windows blocks reading the file it wrote' if wrote == 'unreadable' else ''))
    if wrote is not True:
        print('        it said: ' + (first.get('summary') or '').replace('\n', ' ')[:300])
    if wrote == 'unreadable':
        print(f'        folder: {folder}')
        return _unelevated_retry(engine) if engine == 'chatgpt' and os.name == 'nt' else False
    if not first.get('session'):
        return _say(False, 'it gave no conversation id, so follow-ups could not continue it')
    steps.clear()
    second = code_engines.turn(engine, CODE_TASKS[1], folder, 'safe', first['session'], steps.append, timeout=300)
    _how(steps)
    again = _readable(made, word='again')
    ok &= _say(second['status'] == 'done' and again is True, f"a follow-up continued the same conversation ({second['status']})"
               + (', but Windows blocks reading the file' if again == 'unreadable' else ''))
    if not ok:
        print('        it said: ' + ((second if first['status'] == 'done' else first).get('summary') or '').replace('\n', ' ')[:300])
    print(f'        folder: {folder}')
    return ok


def _how(steps: list) -> None:
    """How the plan changed files: its edit tool, or commands (which matters on Windows)."""
    for e in steps:
        if e.get('kind') == 'file':
            print(f"        wrote {e.get('path')} with its edit tool")
        elif e.get('kind') == 'tool' and e.get('tool') == 'command':
            print(f"        ran: {str(e.get('title'))[:160]}")


def _unelevated_retry(engine: str) -> bool:
    """Codex's files stay locked on this PC: try its other Windows sandbox mode once."""
    import config
    current = we.codex_windows_sandbox()
    if current == 'unelevated':
        return False
    print(f'        Trying again with Codex\'s "unelevated" Windows sandbox (now: {current or "Codex default"})...')
    config.WORK_CODEX_WINDOWS_SANDBOX = 'unelevated'
    try:
        ok = code_check(engine)
    finally:
        config.WORK_CODEX_WINDOWS_SANDBOX = current
    if ok:
        print('  FIX   That works. Run this once, then restart Apex:')
        print('          .venv\\Scripts\\python.exe scripts\\set_env_key.py WORK_CODEX_WINDOWS_SANDBOX unelevated')
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--live', action='store_true', help='also run one tiny real task per plan')
    ap.add_argument('--only', choices=['claude', 'chatgpt'], help='check one plan')
    ap.add_argument('--code', action='store_true', help="also Apex Code's path: a live coding step and a follow-up per plan")
    ap.add_argument('--signed-in', choices=['claude', 'chatgpt'],
                    help='only say whether this plan is signed in with the plan (exit 0) or not (exit 1); no usage')
    args = ap.parse_args(argv)
    if args.signed_in:
        signed = we.check(args.signed_in, fresh=True)
        print(f"{we.NAMES[args.signed_in]}: " + (f"signed in ({signed.get('plan') or 'plan'})" if signed['ok'] else signed['why']))
        return 0 if signed['ok'] else 1
    engines = [args.only] if args.only else ['claude', 'chatgpt']
    results = {e: check_plan(e, args.live) for e in engines}
    if args.code:
        results = {e: ok and code_check(e) for e, ok in results.items()}
    print()
    for e, ok in results.items():
        print(f"{we.NAMES[e]}: {'READY' if ok else 'NOT READY'}" + (' (real task passed)' if ok and args.live else ''))
    if any(results.values()):
        print('Apex can work on ' + ' and '.join(we.NAMES[e] for e, ok in results.items() if ok) + '.')
    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
