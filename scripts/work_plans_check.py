"""Check that Apex's Work tasks can run on your Claude and ChatGPT plans.

    python scripts/work_plans_check.py          # no usage: installed, signed in with the plan, flags
    python scripts/work_plans_check.py --live   # also one tiny real task per plan (a few seconds of usage)

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
import tempfile
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
    with tempfile.TemporaryDirectory(prefix='apex-plan-check-') as tmp:
        started = time.time()
        result = we.run(engine, LIVE_TASK, Path(tmp), timeout=300)
        took = time.time() - started
        made = Path(tmp, 'apex-check.md')
        wrote = made.exists() and 'ready' in made.read_text(encoding='utf-8', errors='replace').lower()
        ok &= _say(result['status'] == 'done', f"a real task finished as {result['status']} in {took:.0f} s")
        ok &= _say(wrote, 'it wrote apex-check.md in the task folder' if wrote else 'it did not write apex-check.md')
        print('        it said: ' + (result['summary'].replace('\n', ' ')[:200] or '(nothing)'))
        if result['status'] == 'limited':
            print('        (your plan is at its usage limit right now: that is the plan, not Apex. Try again after it resets.)')
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--live', action='store_true', help='also run one tiny real task per plan')
    ap.add_argument('--only', choices=['claude', 'chatgpt'], help='check one plan')
    args = ap.parse_args(argv)
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
