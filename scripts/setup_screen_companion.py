"""Install or remove current-user Windows Companion login startup."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def startup_command(python: str) -> str:
    path = Path(python)
    windowed = path.with_name('pythonw.exe')
    if windowed.is_file():
        path = windowed
    return subprocess.list2cmdline([str(path), str(ROOT / 'scripts' / 'start_screen_companion.py')])


def install(command: str):
    import winreg
    from app.autostart import APP_NAME
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        try:
            previous, kind = winreg.QueryValueEx(key, APP_NAME)
        except FileNotFoundError:
            previous = None
        if previous and 'start_screen_companion.py' not in previous:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r'Software\Apex\ScreenCompanion', 0, winreg.KEY_SET_VALUE) as backup:
                winreg.SetValueEx(backup, 'PreviousStartup', 0, kind, previous)
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)


def uninstall():
    # Remove only our profile; do not remove another Apex resident startup entry.
    import winreg
    from app.autostart import APP_NAME
    path = r'Software\Microsoft\Windows\CurrentVersion\Run'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            if 'start_screen_companion.py' not in value:
                return False
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Apex\ScreenCompanion', 0,
                                    winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as backup:
                    previous, kind = winreg.QueryValueEx(backup, 'PreviousStartup')
                    winreg.SetValueEx(key, APP_NAME, 0, kind, previous)
                    winreg.DeleteValue(backup, 'PreviousStartup')
            except FileNotFoundError:
                winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        raise RuntimeError('Screen Companion login setup is for Windows.')
    if args.uninstall:
        print('Companion login startup removed.' if uninstall() else 'Companion login startup was not installed.')
        return 0
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-screen-companion.txt')],
                   cwd=ROOT, check=True)
    command = startup_command(sys.executable)
    install(command)
    if not args.no_start:
        python = Path(sys.executable)
        if python.with_name('pythonw.exe').is_file():
            python = python.with_name('pythonw.exe')
        subprocess.Popen([str(python), str(ROOT / 'scripts' / 'start_screen_companion.py')], cwd=ROOT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    print('Screen Companion will start hidden when you sign into Windows.')
    print('F8: screen help on/off (or your COMPANION_HOTKEY setting). Esc or Stop: stop and hide.')
    print('First use asks for microphone permission. Voice and vision follow your current Apex configuration.')
    print(r'Logs: %LOCALAPPDATA%\Apex\Companion\startup.log and desktop.log')
    print('Remove login startup: Setup-Apex-Screen-Companion.cmd --uninstall')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Screen Companion setup stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
