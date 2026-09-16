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


def main():
    qwen_python = Path.home() / 'apex-qwen-env' / 'Scripts' / 'python.exe'
    if not qwen_python.is_file():
        raise RuntimeError(f'Qwen environment missing: {qwen_python}')
    if port_in_use(17494) or port_in_use(7860):
        raise RuntimeError('Close existing Apex/Qwen servers first (ports 7860 and 17494).')
    voice = agent = None
    try:
        voice = subprocess.Popen([str(qwen_python), '-u', str(ROOT / 'scripts/qwen_server.py')], cwd=ROOT)
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
