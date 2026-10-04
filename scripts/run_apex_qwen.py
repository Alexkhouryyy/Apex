"""Start local Qwen and Apex together without editing the user's .env."""
import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == 'win32'


def _dotenv_keys():
    try:
        from dotenv import dotenv_values
        return set(dotenv_values(ROOT / '.env'))
    except Exception:
        return set()


def apply_wake_default(env, dotenv_keys=None):
    """CELINE_WAKE_ENABLED=true unless the environment or .env already says."""
    keys = _dotenv_keys() if dotenv_keys is None else dotenv_keys
    if 'CELINE_WAKE_ENABLED' not in env and 'CELINE_WAKE_ENABLED' not in keys:
        env['CELINE_WAKE_ENABLED'] = 'true'
    return env


def port_in_use(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def launch_environment():
    from dotenv import dotenv_values
    env = {k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}
    env.update(os.environ)
    apply_wake_default(env)
    env.update(TTS_ENGINE='voicebox', VOICEBOX_URL='http://127.0.0.1:17494',
               VOICEBOX_PROFILE='celine', APEX_SUPERVISED='1')
    return env


def dashboard_address(env):
    host = env.get('DASHBOARD_HOST', '127.0.0.1')
    # Match the server's fallback for a tokenless public bind.
    if host not in {'127.0.0.1', 'localhost', '::1'} and not env.get('DASHBOARD_TOKEN'):
        host = '127.0.0.1'
    if host == '0.0.0.0':
        host = '127.0.0.1'
    port = int(env.get('DASHBOARD_PORT', '7860'))
    shown = f'[{host}]' if ':' in host else host
    return host, port, f'http://{shown}:{port}'


def wait_healthy(opener, url, accept, processes, label, timeout):
    deadline = time.monotonic() + timeout
    next_notice = time.monotonic() + 15
    while time.monotonic() < deadline:
        for name, process in processes:
            if process.poll() is not None:
                raise RuntimeError(f'{name} stopped during startup (exit {process.returncode}). '
                                   'See the console or ~/.apex/resident.log.')
        try:
            with opener.open(url, timeout=2) as response:
                health = json.load(response)
            if isinstance(health, dict) and accept(health):
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        if time.monotonic() >= next_notice:
            print(f'Waiting for {label}...', flush=True)
            next_notice = time.monotonic() + 15
        time.sleep(1)
    raise RuntimeError(f'{label} did not become ready within {timeout} seconds. '
                       'See the console or ~/.apex/resident.log.')


def stop(process):
    if process is not None and process.poll() is None:
        if WINDOWS:
            # A Windows venv python.exe is a redirector with a real interpreter
            # child. Terminating only the redirector leaves Apex/Qwen running.
            result = subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode and process.poll() is None:
                raise RuntimeError(f'Could not stop owned process tree {process.pid}. '
                                   'Quit Apex from its tray before restarting. '
                                   + result.stderr.strip())
            process.wait(timeout=10)
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main(argv=None):
    if WINDOWS:
        def interrupted(*_):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGBREAK, interrupted)
    # --fast: the streaming engine (scripts/qwen_fast_server.py) in the
    # environment Test-Apex-Fast-Voice.cmd installs. First audio in about a
    # second instead of the whole section first; measured on the laptop.
    args = sys.argv[1:] if argv is None else argv
    fast = '--fast' in args
    resident = '--resident' in args
    env_name, server = (('apex-qwen-fast-env', 'scripts/qwen_fast_server.py') if fast
                        else ('apex-qwen-env', 'scripts/qwen_server.py'))
    qwen_python = Path.home() / env_name / 'Scripts' / 'python.exe'
    if not qwen_python.is_file():
        hint = ' Run Test-Apex-Fast-Voice.cmd once to install it.' if fast else ''
        raise RuntimeError(f'Qwen environment missing: {qwen_python}.{hint}')
    env = launch_environment()
    host, port, dashboard_url = dashboard_address(env)
    dashboard_enabled = env.get('DASHBOARD_ENABLED', 'true').lower() in {'1', 'true', 'yes'}
    if dashboard_enabled and port == 17494:
        raise RuntimeError('DASHBOARD_PORT conflicts with Celine voice port 17494.')
    if port_in_use(17494) or (dashboard_enabled and port_in_use(port, host)):
        raise RuntimeError(f'An Apex/Qwen port is occupied ({port}, 17494). '
                           'Stop the previous launcher with Ctrl+C or quit Apex from its tray, then retry.')
    voice = agent = None
    try:
        print('Starting Celine voice. The first GPU warm-up can take several minutes.', flush=True)
        voice = subprocess.Popen([str(qwen_python), '-u', str(ROOT / server)], cwd=ROOT)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        wait_healthy(opener, 'http://127.0.0.1:17494/health',
                     lambda h: h.get('service') == 'apex-qwen' and h.get('model_loaded'),
                     [('Celine voice', voice)], 'Celine voice', 900)
        print('Celine voice: ready.', flush=True)
        # Own the child directly, so stopping this launcher also stops Apex.
        # Only the explicit dashboard restart exit code triggers a new child.
        starts = []
        opened = False
        while True:
            env = launch_environment()  # Pick up settings saved before a restart.
            host, port, dashboard_url = dashboard_address(env)
            dashboard_enabled = env.get('DASHBOARD_ENABLED', 'true').lower() in {'1', 'true', 'yes'}
            if dashboard_enabled and (port == 17494 or port_in_use(port, host)):
                raise RuntimeError(f'Dashboard port {port} is occupied. Check DASHBOARD_PORT or stop the other Apex instance.')
            now = time.monotonic()
            starts = [t for t in starts if now - t < 120]
            if len(starts) >= 5:
                raise RuntimeError('Too many Apex restarts in two minutes; stopping.')
            starts.append(now)
            # Windows venv redirectors make Popen.pid differ from os.getpid()
            # inside Apex. Identify this particular launch across that boundary.
            launch_id = uuid.uuid4().hex
            env['APEX_LAUNCH_ID'] = launch_id
            print('Starting Apex' + (' resident mode' if resident else '') + '...', flush=True)
            agent = subprocess.Popen([sys.executable, str(ROOT / 'main.py'),
                                      '--resident' if resident else '--text'], cwd=ROOT, env=env)
            if dashboard_enabled:
                wait_healthy(opener, dashboard_url + '/health',
                             lambda h: h.get('service') == 'apex' and h.get('launch_id') == launch_id
                             and h.get('agent_ready') is True,
                             [('Apex', agent), ('Celine voice', voice)], 'Apex dashboard', 180)
                print(f'Dashboard: ready. {dashboard_url}/home\n'
                      f'Screen Companion: {dashboard_url}/companion\n'
                      'Open Companion and click Voice mode to allow browser audio/microphone access.', flush=True)
                if '--open' in args and not opened:
                    try:
                        webbrowser.open(dashboard_url + '/companion')
                    except Exception:
                        print('Open the Companion link above in your browser.', flush=True)
                    opened = True
            else:
                print('Dashboard: disabled in your settings.', flush=True)
            print('Background features and MCP servers follow your existing settings.\n'
                  'Apps requiring keys or account authorization still need setup in Apps.\n'
                  'Keep this window open. Press Ctrl+C to stop Apex and Celine.', flush=True)
            while agent.poll() is None:
                if voice.poll() is not None:
                    raise RuntimeError('Celine voice stopped. Restart this launcher; see error above.')
                time.sleep(1)
            if agent.returncode != 42:
                return agent.returncode
            print('Restart requested. Keeping Celine voice warm...', flush=True)
            time.sleep(2)
    finally:
        try:
            stop(agent)
        finally:
            stop(voice)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nApex and Qwen stopped.')
    except Exception as exc:
        print(f'Launcher stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
