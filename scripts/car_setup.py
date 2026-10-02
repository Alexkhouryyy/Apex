"""Put Apex's car page on your phone or car screen, privately, over HTTPS.

    python scripts/car_setup.py          check, then turn it on
    python scripts/car_setup.py --off    turn it off again

Uses Tailscale: a free private network between your own devices. Your PC
gets an HTTPS address only your signed-in devices can open (nobody else on
the internet can), and Apex itself keeps listening only on this PC. HTTPS
matters: a phone browser won't let a page use the microphone without it.

What it does:
  1. finds Tailscale and checks you're signed in
  2. makes sure Apex has a dashboard token (the password the car will ask for)
  3. runs `tailscale serve --bg <port>`: https://<your-pc>.<tailnet>.ts.net → Apex
  4. prints the address to open on the phone or car screen
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_PATHS = [Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale' / 'tailscale.exe']


def find_tailscale() -> str | None:
    found = shutil.which('tailscale')
    if found:
        return found
    for path in WINDOWS_PATHS:
        if path.is_file():
            return str(path)
    return None


def status(exe: str, run=subprocess.run) -> dict:
    out = run([exe, 'status', '--json'], capture_output=True, text=True, timeout=20)
    if out.returncode != 0 and not out.stdout.strip():
        raise RuntimeError(out.stderr.strip() or 'tailscale status failed')
    return json.loads(out.stdout)


def address(state: dict) -> str | None:
    """The PC's https name on the tailnet, or None when not signed in."""
    if state.get('BackendState') != 'Running':
        return None
    name = ((state.get('Self') or {}).get('DNSName') or '').rstrip('.')
    return name or None


def env_token(env_file: Path) -> str:
    if not env_file.is_file():
        return ''
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        key, _, value = line.partition('=')
        if key.strip() == 'DASHBOARD_TOKEN':
            return value.strip().strip('"').strip("'")
    return ''


def add_token(env_file: Path) -> str:
    """Append a new DASHBOARD_TOKEN to .env (never replacing an existing one)."""
    token = secrets.token_urlsafe(18)
    existing = env_file.read_text(encoding='utf-8-sig') if env_file.is_file() else ''
    with env_file.open('a', encoding='utf-8') as f:
        if existing and not existing.endswith('\n'):
            f.write('\n')
        f.write(f'DASHBOARD_TOKEN={token}\n')
    return token


def main(argv=None, run=subprocess.run, ask=input) -> int:
    argv = sys.argv[1:] if argv is None else argv
    port = os.environ.get('DASHBOARD_PORT', '7860')
    exe = find_tailscale()
    if not exe:
        print('Tailscale is not installed.\n'
              '  1. Install it on this PC: https://tailscale.com/download/windows\n'
              '  2. Install the Tailscale app on your phone (or car screen) and sign in with the SAME account.\n'
              '  3. Run this again.')
        return 1
    if '--off' in argv:
        run([exe, 'serve', '--https=443', 'off'], timeout=20)
        print('Apex is no longer served on your tailnet.')
        return 0
    try:
        name = address(status(exe, run))
    except Exception as exc:
        print(f'Could not ask Tailscale for its status: {exc}\nOpen Tailscale and sign in, then run this again.')
        return 1
    if not name:
        print('Tailscale is installed but not signed in. Open it from the system tray, sign in, then run this again.')
        return 1

    env_file = ROOT / '.env'
    token = env_token(env_file) or os.environ.get('DASHBOARD_TOKEN', '')
    if not token:
        print('Apex has no dashboard token yet. The car needs one: it is the password that keeps the page yours.')
        if ask('Create one now and save it in .env? [y/N] ').strip().lower() != 'y':
            print('Not turned on. Set DASHBOARD_TOKEN in .env, then run this again.')
            return 1
        token = add_token(env_file)
        print(f'\nYour Apex token (type it once on the phone/car; it is also in .env):\n  {token}\n'
              'Restart Apex so it uses the token.\n')

    result = run([exe, 'serve', '--bg', port], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        print(f'Tailscale could not serve Apex: {detail}\n'
              'If it mentions HTTPS certificates, enable HTTPS for your tailnet at '
              'https://login.tailscale.com/admin/dns (one click), then run this again.')
        return 1
    print('Done. On your phone or car screen, with Tailscale on, open:\n'
          f'  https://{name}/drive\n\n'
          'Keep in mind:\n'
          '  - The PC must be on, awake and running Apex (set Windows to not sleep while plugged in).\n'
          '  - Only your own signed-in devices can open that address.\n'
          '  - Use it by voice; read the screen only when parked.\n'
          '  - To turn it off: Setup-Apex-Car.cmd --off')
    return 0


if __name__ == '__main__':
    sys.exit(main())
