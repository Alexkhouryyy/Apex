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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WINDOWS = sys.platform == 'win32'


def apply_launch_defaults(env, dotenv_keys=None):
    """The defaults every launcher shares (scripts/launch_defaults.py)."""
    from scripts import launch_defaults
    return launch_defaults.apply(env, dotenv_keys)


def port_in_use(port, host='127.0.0.1'):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def launch_environment(local_voice=True):
    from dotenv import dotenv_values
    env = {k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}
    env.update(os.environ)
    apply_launch_defaults(env, set())   # env already holds .env and the environment
    if local_voice:
        env.update(TTS_ENGINE='voicebox', VOICEBOX_URL='http://127.0.0.1:17494',
                   VOICEBOX_PROFILE='celine')
    env['APEX_SUPERVISED'] = '1'
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


VOICE_URL = 'http://127.0.0.1:17494/health'
VOICE_WARMUP_NOTICE = 900     # seconds: say so if Celine still isn't ready by then


def voice_health(opener):
    """Celine's voice server health, or {} if it doesn't answer."""
    try:
        with opener.open(VOICE_URL, timeout=2) as response:
            health = json.load(response)
        return health if isinstance(health, dict) else {}
    except (OSError, ValueError, urllib.error.URLError):
        return {}


def start_voice(qwen_python, server, opener):
    """Start Celine's local voice if it can be started; never block Apex on it.

    Returns (process or None, local_voice). local_voice says whether Apex should
    speak through the local voice server: True for one this launcher started or
    an Apex voice server already running; False when there is none, in which case
    Apex keeps the voice settings from .env (and speech falls back to the system
    voice if that is the local server too)."""
    if not qwen_python.is_file():
        print(f'Celine voice: not installed ({qwen_python}). Run Test-Apex-Fast-Voice.cmd once to install it. '
              'Apex starts without it.', flush=True)
        return None, False
    if port_in_use(17494):
        if voice_health(opener).get('service') == 'apex-qwen':
            print('Celine voice: already running; using it.', flush=True)
            return None, True
        print('Celine voice: port 17494 is used by another program. Apex starts without the local voice.', flush=True)
        return None, False
    print('Starting Celine voice in the background. Apex does not wait for it; '
          'the first GPU warm-up can take several minutes.', flush=True)
    return subprocess.Popen([str(qwen_python), '-u', str(ROOT / server)], cwd=ROOT), True


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
    env = launch_environment()
    host, port, dashboard_url = dashboard_address(env)
    dashboard_enabled = env.get('DASHBOARD_ENABLED', 'true').lower() in {'1', 'true', 'yes'}
    if dashboard_enabled and port == 17494:
        raise RuntimeError('DASHBOARD_PORT conflicts with Celine voice port 17494.')
    if dashboard_enabled and port_in_use(port, host):
        raise RuntimeError(f'The Apex dashboard port {port} is occupied. '
                           'Stop the previous launcher with Ctrl+C or quit Apex from its tray, then retry.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    voice = agent = None
    try:
        # Celine's voice is optional: one failure (a GPU that cannot load the
        # model, a missing environment) must not take the rest of Apex down.
        voice, local_voice = start_voice(qwen_python, server, opener)
        voice_started = time.monotonic()
        voice_ready = voice_reported = warmup_noted = False
        # Own the child directly, so stopping this launcher also stops Apex.
        # Only the explicit dashboard restart exit code triggers a new child.
        starts = []
        opened = False
        while True:
            env = launch_environment(local_voice)  # Pick up settings saved before a restart.
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
                             [('Apex', agent)], 'Apex dashboard', 180)
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
            last_probe = 0.0
            while agent.poll() is None:
                if voice is not None and not voice_reported and voice.poll() is not None:
                    print(f'Celine voice stopped (exit {voice.returncode}); see the error above. Apex keeps running, '
                          "and speaks with this PC's own voice until you restart this launcher.", flush=True)
                    voice_reported = True
                elif local_voice and not voice_ready and not voice_reported and time.monotonic() - last_probe >= 5:
                    last_probe = time.monotonic()
                    health = voice_health(opener)
                    if health.get('service') == 'apex-qwen' and health.get('model_loaded'):
                        print('Celine voice: ready.', flush=True)
                        voice_ready = True
                    elif not warmup_noted and time.monotonic() - voice_started > VOICE_WARMUP_NOTICE:
                        print('Celine voice is still warming up after 15 minutes. Apex works meanwhile, '
                              "with this PC's own voice.", flush=True)
                        warmup_noted = True
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
