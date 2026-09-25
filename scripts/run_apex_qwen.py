"""Start local Qwen and Apex together without editing the user's .env."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def port_in_use(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main(argv=None):
    # --fast: the streaming engine (scripts/qwen_fast_server.py) in the
    # environment Test-Apex-Fast-Voice.cmd installs. First audio in about a
    # second instead of the whole section first; measured on the laptop.
    fast = '--fast' in (sys.argv[1:] if argv is None else argv)
    env_name, server = (('apex-qwen-fast-env', 'scripts/qwen_fast_server.py') if fast
                        else ('apex-qwen-env', 'scripts/qwen_server.py'))
    qwen_python = Path.home() / env_name / 'Scripts' / 'python.exe'
    if not qwen_python.is_file():
        hint = ' Run Test-Apex-Fast-Voice.cmd once to install it.' if fast else ''
        raise RuntimeError(f'Qwen environment missing: {qwen_python}.{hint}')
    if port_in_use(17494) or port_in_use(7860):
        raise RuntimeError('Close existing Apex/Qwen servers first (ports 7860 and 17494).')
    voice = agent = None
    try:
        voice = subprocess.Popen([str(qwen_python), '-u', str(ROOT / server)], cwd=ROOT)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if voice.poll() is not None:
                raise RuntimeError('Qwen stopped during startup. Read the error above.')
            try:
                with opener.open('http://127.0.0.1:17494/health', timeout=2) as response:
                    health = json.load(response)
                if health.get('service') == 'apex-qwen' and health.get('model_loaded'):
                    break
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Qwen startup timed out after 15 minutes.')
        env = os.environ.copy()
        env.update(TTS_ENGINE='voicebox', VOICEBOX_URL='http://127.0.0.1:17494', VOICEBOX_PROFILE='celine')
        print('\nCeline connected. Open http://127.0.0.1:7860/companion once Apex is ready.\n', flush=True)
        agent = subprocess.Popen([sys.executable, str(ROOT / 'main.py'), '--text'], cwd=ROOT, env=env)
        while agent.poll() is None:
            if voice.poll() is not None:
                raise RuntimeError('Qwen voice service stopped. Restart this launcher; see error above.')
            time.sleep(1)
        return agent.returncode
    finally:
        stop(agent)
        stop(voice)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nApex and Qwen stopped.')
    except Exception as exc:
        print(f'Launcher stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
