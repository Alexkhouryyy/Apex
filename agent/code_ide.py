"""Apex-owned Theia processes. Each IDE opens only its session's working copy."""
from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
from agent import code_studio as studio, work

APP = Path(__file__).resolve().parents[1] / 'ide'
_lock = threading.RLock()
_runtimes: dict[int, 'Runtime'] = {}
MAX_RUNNING = 2


def installation() -> dict:
    node = shutil.which('node')
    built = (APP / 'src-gen/backend/main.js').is_file() and (APP / 'lib/frontend/index.html').is_file()
    plugins = (APP / 'plugins/.apex-ready.json').is_file()
    return {'installed': bool(node and built and plugins), 'node': bool(node), 'built': built,
            'plugins': plugins, 'engine': 'Eclipse Theia', 'version': '1.76.0',
            'setup': 'Run Setup-Apex-Code-IDE.cmd once, then reopen Full IDE.'}


class Runtime:
    def __init__(self, sid: int):
        self.sid = sid
        self.process: subprocess.Popen | None = None
        self.port: int | None = None
        self.secret = secrets.token_urlsafe(32)
        self.connection_token = None
        self.log = None
        self.webviews = None
        self.log_path = Path(work.WORK_DIR) / 'ide' / str(sid) / 'server.log'

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, session: dict):
        if self.running:
            return
        # A crashed engine may leave its webview gateway and log open. Retire
        # that generation before reconnecting, including its browser grants.
        if self.process is not None or self.webviews is not None or self.log is not None:
            self.stop()
        self.secret = secrets.token_urlsafe(32)
        self.connection_token = None
        if not installation()['installed']:
            raise studio.CodeError(installation()['setup'])
        active = sum(r.running for r in _runtimes.values())
        if active >= MAX_RUNNING:
            raise studio.CodeError('Two IDE sessions are already open. Save and close one before opening another.')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        directory = self.log_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        preferences = directory / 'config'
        preferences.mkdir(exist_ok=True)
        settings = preferences / 'settings.json'
        env = dict(os.environ)
        env.update(studio._workspace_env(session))
        interpreter = shutil.which('python', path=env.get('PATH')) if env.get('VIRTUAL_ENV') else sys.executable
        if not settings.exists():
            settings.write_text(json.dumps({'python.defaultInterpreterPath': interpreter,
                'python.pythonPath': interpreter, 'python.languageServer': 'None',
                'python.useEnvironmentsExtension': False, 'python.experiments.enabled': False,
                'python.terminal.activateEnvironment': False,
                'python.terminal.activateEnvInCurrentTerminal': False,
                'telemetry.enableTelemetry': False, 'files.autoSave': 'off'}), encoding='utf-8')
        env.update(APEX_IDE_SECRET=self.secret, APEX_PARENT_PID=str(os.getpid()),
                   APEX_IDE_USER_DIR=str(Path(work.WORK_DIR) / 'ide' / 'user'),
                   THEIA_CONFIG_DIR=str(preferences), THEIA_PLUGINS='local-dir:' + str(APP / 'plugins'))
        env.pop('DASHBOARD_TOKEN', None)
        # Modern Node exposes navigator. VS Code's TypeScript extension then
        # mistakes Theia's Node plugin host for a browser and skips type errors.
        env['NODE_OPTIONS'] = (env.get('NODE_OPTIONS', '') + ' --no-experimental-global-navigator').strip()
        # The bridge's PluginHostEnvironmentVariable contribution also opts out
        # of Theia's fallback getter after the plugin host's environment is built.
        self.log = self.log_path.open('a', encoding='utf-8')
        try:
            from dashboard.ide_webviews import WebviewGateway
            self.webviews = WebviewGateway(self.sid)
            self.webviews.start()
            env['APEX_IDE_WEBVIEW_PORT'] = str(self.webviews.port)
            env.pop('THEIA_WEBVIEW_EXTERNAL_ENDPOINT', None)
            self.process = subprocess.Popen([shutil.which('node'), str(APP / 'src-gen/backend/main.js'),
                str(Path(session['worktree']).resolve()), '--hostname', '127.0.0.1', '--port', str(self.port)],
                cwd=APP, env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline and self.running:
                    try:
                        response = client.get(f'http://127.0.0.1:{self.port}/apex-ide-health',
                                              headers={'x-apex-ide-secret': self.secret})
                        if response.status_code == 200 and response.json().get('engine') == 'apex-theia':
                            page = client.get(f'http://127.0.0.1:{self.port}/',
                                              headers={'x-apex-ide-secret': self.secret})
                            self.connection_token = page.cookies.get('theia-connection-token')
                            if not self.connection_token:
                                raise studio.CodeError('The IDE did not establish its private connection token.')
                            return
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(.2)
            raise studio.CodeError(f'The IDE could not start. See {self.log_path}.')
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.webviews:
            self.webviews.stop()
            self.webviews = None
        if self.running:
            # Retain Process handles: psutil checks process creation times before
            # signalling, including Windows processes whose PIDs may be reused.
            try:
                parent = psutil.Process(self.process.pid)
                children = parent.children(recursive=True)
                for p in children:
                    try:
                        p.terminate()
                    except psutil.NoSuchProcess:
                        pass
                self.process.terminate()
                _, alive = psutil.wait_procs(children, timeout=3)
                for p in alive:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
        if self.log:
            self.log.close()
            self.log = None


def get(sid: int) -> Runtime | None:
    with _lock:
        runtime = _runtimes.get(sid)
        return runtime if runtime and runtime.running else None


@studio._exclusive
def start(sid: int) -> dict:
    session = studio._editable_session(sid)
    with _lock:
        runtime = _runtimes.setdefault(sid, Runtime(sid))
        runtime.start(session)
    return status(sid)


def status(sid: int) -> dict:
    runtime = get(sid)
    return {**installation(), 'running': bool(runtime), 'url': f'/code/ide/{sid}/',
            'log': str(runtime.log_path) if runtime else None}


def stop(sid: int):
    with _lock:
        runtime = _runtimes.pop(sid, None)
        if runtime:
            runtime.stop()


@studio._exclusive
def checkpoint(sid: int) -> dict:
    session = studio._editable_session(sid)
    previous = studio._git(session['worktree'], 'rev-parse', 'HEAD').strip()
    warning = studio._owner_file_checkpoint(sid, previous, 'saved in the IDE')
    studio._set(sid, files_changed=studio._count_changed(sid))
    return {'saved': True, 'warning': warning}


def stop_all():
    for sid in list(_runtimes):
        stop(sid)


atexit.register(stop_all)
