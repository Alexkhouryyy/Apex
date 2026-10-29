"""Silent login launcher. Attach to an owned Apex service or start the prepared stack."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def environment():
    from dotenv import dotenv_values
    env = {k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}
    env.update(os.environ)
    env['DASHBOARD_ENABLED'] = 'true'  # native voice and screen help use this local backend
    # Feature defaults (desktop Companion on, wake phrases off) live in config.py,
    # the same for every launcher; nothing else is injected.
    return env


def attach(env):
    """Never stop an existing service or replace a process using its port."""
    from scripts.run_apex_qwen import dashboard_address, port_in_use
    host, port, url = dashboard_address(env)
    if not port_in_use(port, host):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {'Content-Type': 'application/json'}
    if env.get('DASHBOARD_TOKEN'):
        headers['Authorization'] = 'Bearer ' + env['DASHBOARD_TOKEN']
    try:
        with opener.open(url + '/health', timeout=3) as response:
            health = json.load(response)
        if health.get('service') != 'apex' or health.get('agent_ready') is not True:
            raise RuntimeError('The dashboard port is occupied by a service that is not ready Apex.')
        req = urllib.request.Request(url + '/api/companion/desktop/start', data=b'{}', headers=headers)
        with opener.open(req, timeout=10) as response:
            result = json.load(response)
        if not result.get('running'):
            raise RuntimeError('Apex did not start the desktop window.')
        return True
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError('An existing dashboard could not enable Desktop Companion. Update and restart that Apex instance; no process was stopped.') from exc


def own_startup():
    """One login supervisor per Windows account; keep the handle until exit."""
    if os.name != 'nt':
        return None
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes, kernel.CreateMutexW.restype = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE
    kernel.GetLastError.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, 'Local\\ApexScreenCompanionLogin')
    if not handle:
        raise OSError('Could not create the Companion startup lock.')
    if kernel.GetLastError() == 183:
        kernel.CloseHandle(handle)
        return False
    return handle


def supervise(env, log):
    """Bring up Apex immediately; optional local voice warms in parallel."""
    from scripts.run_apex_qwen import port_in_use, stop
    voice = child = None
    qwen = Path.home() / 'apex-qwen-fast-env' / 'Scripts' / 'python.exe'
    try:
        if qwen.is_file():
            use_qwen = not port_in_use(17494)
            if not use_qwen:
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open('http://127.0.0.1:17494/health', timeout=2) as response:
                        use_qwen = json.load(response).get('service') == 'apex-qwen'
                except (OSError, ValueError, urllib.error.URLError):
                    pass
            if use_qwen:
                env.update(TTS_ENGINE='voicebox', VOICEBOX_URL='http://127.0.0.1:17494', VOICEBOX_PROFILE='celine')
                if not port_in_use(17494):
                    voice = subprocess.Popen([str(qwen), '-u', str(ROOT/'scripts'/'qwen_fast_server.py')],
                                             cwd=ROOT, env=env, stdout=log, stderr=log,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                    logging.info('Local voice is warming in parallel; Apex and its hotkey do not wait for the GPU.')
            else:
                logging.warning('Local voice port is occupied by another service; keeping existing voice settings.')
        env['APEX_SUPERVISED'] = '1'
        resident_python = Path(sys.executable)
        if resident_python.name.lower() == 'pythonw.exe':
            resident_python = resident_python.with_name('python.exe')
        starts = []
        while True:
            now = time.monotonic()
            starts = [t for t in starts if now-t < 120]
            if len(starts) >= 5:
                raise RuntimeError('Too many Apex restarts in two minutes.')
            starts.append(now)
            env['APEX_LAUNCH_ID'] = uuid.uuid4().hex
            child = subprocess.Popen([str(resident_python), str(ROOT/'main.py'), '--resident'], cwd=ROOT, env=env,
                                     stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            voice_reported = False
            while child.poll() is None:
                if voice and voice.poll() is not None and not voice_reported:
                    logging.error('Local voice stopped. Screen Companion remains available with text/device voice; see the voice output above.')
                    voice_reported = True
                time.sleep(.5)
            if child.returncode != 42:
                return child.returncode
            logging.info('Restarting Apex; retaining the owned voice process.')
            time.sleep(1)
    finally:
        try:
            stop(child)
        finally:
            stop(voice)


def main():
    if os.name != 'nt':
        raise RuntimeError('This login launcher is for Windows.')
    data = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Apex' / 'Companion'
    data.mkdir(parents=True, exist_ok=True)
    # pythonw has no console handles. Give launcher and owned children real logs.
    log = (data / 'startup.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    logging.basicConfig(stream=log, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    lock = own_startup()
    if lock is False:
        logging.info('Companion login launcher is already running.')
        return 0
    try:
        os.chdir(ROOT)
        env = environment()
        if attach(env):
            logging.info('Connected to the existing Apex. Companion is waiting for its hotkey.')
            return 0
        os.environ.update(env)
        logging.info('Starting Apex. No voice model is installed by this launcher.')
        return supervise(env, log)
    except Exception:
        logging.exception('Screen Companion startup failed')
        return 1
    finally:
        if lock:
            ctypes.windll.kernel32.CloseHandle(lock)


if __name__ == '__main__':
    raise SystemExit(main())
