"""Check that Apex's Work tasks can run on your Claude and ChatGPT plans.

    python scripts/work_plans_check.py          # no usage: installed, signed in with the plan, flags
    python scripts/work_plans_check.py --live   # also one tiny real task per plan (a few seconds of usage)
    python scripts/work_plans_check.py --signed-in claude   # exit 0 if signed in with the plan

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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import work_engines as we  # noqa: E402

FLAGS = {
    'claude': (['--help'], ['-p', '--output-format', '--permission-mode', '--allowedTools']),
    'chatgpt': (['exec', '--help'], ['--skip-git-repo-check', '--ephemeral', '--cd', '--sandbox', 'workspace-write',
                                     '--output-last-message', 'read from stdin']),
}
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


def _readable(path: Path, tries: int = 10):
    """True if it says ready, False if missing or wrong, 'unreadable' if Windows refuses.
    Retries for a few seconds, so a file still held open for a moment isn't mistaken."""
    for i in range(tries):
        if not path.exists():
            return False
        try:
            return 'ready' in path.read_text(encoding='utf-8', errors='replace').lower()
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
    folder = Path(work.WORK_DIR) / '_plan-check' / f"{engine}-{time.strftime('%Y%m%d-%H%M%S')}"
    if windows_sandbox:
        folder = folder.with_name(folder.name + '-' + windows_sandbox)
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--live', action='store_true', help='also run one tiny real task per plan')
    ap.add_argument('--only', choices=['claude', 'chatgpt'], help='check one plan')
    ap.add_argument('--signed-in', choices=['claude', 'chatgpt'],
                    help='only say whether this plan is signed in with the plan (exit 0) or not (exit 1); no usage')
    args = ap.parse_args(argv)
    if args.signed_in:
        signed = we.check(args.signed_in, fresh=True)
        print(f"{we.NAMES[args.signed_in]}: " + (f"signed in ({signed.get('plan') or 'plan'})" if signed['ok'] else signed['why']))
        return 0 if signed['ok'] else 1
    engines = [args.only] if args.only else ['claude', 'chatgpt']
    results = {e: check_plan(e, args.live) for e in engines}
    print()
    for e, ok in results.items():
        print(f"{we.NAMES[e]}: {'READY' if ok else 'NOT READY'}" + (' (real task passed)' if ok and args.live else ''))
    if any(results.values()):
        print('Apex can work on ' + ' and '.join(we.NAMES[e] for e, ok in results.items() if ok) + '.')
    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
