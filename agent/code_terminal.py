"""Owner-operated persistent shells: PTY on Unix, ConPTY on Windows.

Input is never stored in the brain. Only bounded live output is kept. This is
the owner's local shell, with the host permissions of Code's command runner.
"""
from __future__ import annotations
import os
import atexit
import json
import sys
import subprocess
import threading
from pathlib import Path

MAX_OUTPUT = 128_000
_lock = threading.RLock()
_shells = {}


def _dimensions(rows, cols):
    if type(rows) is not int or type(cols) is not int or not 5 <= rows <= 200 or not 10 <= cols <= 400:
        raise ValueError('Terminal size must be 5–200 rows and 10–400 columns.')
    return rows, cols


def _spawn(folder: str, env: dict, rows: int, cols: int, windows: bool | None = None):
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise ValueError('Install Apex’s Windows requirements (pywinpty), then restart Apex to use the terminal.') from exc
        argv = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/q']
    else:
        env = {**env, 'TERM': 'xterm-256color', 'PS1': '\\w $ '}
        return PosixProcess(folder, env, rows, cols)
    return PtyProcess.spawn(argv, cwd=str(folder), env=env, dimensions=(rows, cols))


class PosixProcess:
    """forkpty runs in a fresh single-threaded helper, never the threaded API."""
    def __init__(self, folder, env, rows, cols):
        self.io_lock = threading.Lock()
        self.child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), '--pty-helper',
                                       str(rows), str(cols)], cwd=folder, env=env, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                      encoding='utf-8', start_new_session=True)
        result = json.loads(self.child.stdout.readline())
        if 'pid' not in result:
            self.child.wait(timeout=5)
            raise ValueError(result.get('error', 'Could not start the terminal.'))
        self.pid = result['pid']

    def read(self, size):
        line = self.child.stdout.readline()
        if not line:
            raise EOFError
        result = json.loads(line)
        if result.get('closed'):
            raise EOFError
        return result.get('data', '')

    def _send(self, message):
        with self.io_lock:
            self.child.stdin.write(json.dumps(message) + '\n')
            self.child.stdin.flush()

    def write(self, text):
        self._send({'input': text})

    def setwinsize(self, rows, cols):
        self._send({'rows': rows, 'cols': cols})

    def isalive(self):
        return self.child.poll() is None

    def close(self, force=True):
        try:
            self._send({'close': True})
        except (OSError, ValueError):
            pass
        try:
            self.child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=3)
        self.child.stdin.close()
        self.child.stdout.close()


class Shell:
    def __init__(self, process):
        self.process, self.lock = process, threading.RLock()
        self.text, self.offset, self.start, self.closed = '', 0, 0, False
        self.reader = threading.Thread(target=self._read, daemon=True, name=f'ApexCodeShell-{process.pid}')
        self.reader.start()

    def _read(self):
        try:
            while not self.closed:
                try:
                    data = self.process.read(4096)
                except EOFError:
                    break
                if not data:
                    # pywinpty can consume an internal wake-up packet without
                    # output. That does not mean the shell has exited.
                    if self.process.isalive():
                        continue
                    break
                with self.lock:
                    self.text = (self.text + data)[-MAX_OUTPUT:]
                    self.offset += len(data)
                    self.start = self.offset - len(self.text)
        except OSError:
            pass
        finally:
            self.closed = True

    def snapshot(self, after):
        with self.lock:
            reset = after < self.start or after > self.offset
            after = self.start if reset else after
            return {'data': self.text[max(0, after - self.start):], 'offset': self.offset,
                    'reset': reset, 'running': not self.closed and self.process.isalive()}

    def close(self):
        self.closed = True
        try:
            if self.process.isalive() and os.name == 'nt':
                from agent import work_engines
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                               capture_output=True, timeout=10, **work_engines._hidden())
            elif self.process.isalive():
                import signal
                import psutil
                try:
                    children = psutil.Process(self.process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                # Interactive shells use distinct foreground process groups and
                # ignore SIGTERM. End every known job before closing the buffered
                # PTY reader, whose close otherwise waits forever for EOF.
                groups = {self.process.pid}
                for child in children:
                    try:
                        if os.getsid(child.pid) == self.process.pid:
                            groups.add(os.getpgid(child.pid))
                        child.kill()
                    except (psutil.NoSuchProcess, ProcessLookupError):
                        pass
                for group in groups:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.process.close(force=True)
        except (OSError, subprocess.SubprocessError):
            pass
        self.reader.join(timeout=2)


def start(sid: int, folder: str, env: dict, rows=24, cols=80):
    rows, cols = _dimensions(rows, cols)
    if not Path(folder).is_dir():
        raise ValueError('This workspace is unavailable.')
    with _lock:
        old = _shells.get(sid)
        if old and old.snapshot(0)['running']:
            return old.snapshot(0)
        if old:
            old.close()
        shell = Shell(_spawn(folder, env, rows, cols))
        _shells[sid] = shell
        return shell.snapshot(0)


def read(sid: int, after=0):
    with _lock:
        shell = _shells.get(sid)
    return shell.snapshot(max(0, after)) if shell else {'data': '', 'offset': 0, 'reset': True, 'running': False}


def write(sid: int, text: str):
    if not isinstance(text, str) or len(text) > 16_000:
        raise ValueError('Terminal input is at most 16,000 characters per request.')
    with _lock:
        shell = _shells.get(sid)
    if not shell or shell.closed or not shell.process.isalive():
        raise ValueError('This terminal has ended. Start a new terminal.')
    shell.process.write(text)
    return {'sent': True}


def resize(sid: int, rows: int, cols: int):
    rows, cols = _dimensions(rows, cols)
    with _lock:
        shell = _shells.get(sid)
    if shell and not shell.closed:
        shell.process.setwinsize(rows, cols)
    return {'resized': True}


def stop(sid: int):
    with _lock:
        shell = _shells.pop(sid, None)
    if shell:
        shell.close()
    return {'stopped': bool(shell)}


def stop_all():
    with _lock:
        ids = list(_shells)
    for sid in ids:
        stop(sid)


atexit.register(stop_all)


def _pty_helper():
    """Small stdio bridge; all fork/exec work precedes creation of any thread."""
    from ptyprocess import PtyProcessUnicode
    output_lock = threading.Lock()
    def emit(data):
        with output_lock:
            print(json.dumps(data), flush=True)
    try:
        process = PtyProcessUnicode.spawn(['/bin/bash', '--noprofile', '--norc', '-i'],
                                         env=dict(os.environ), dimensions=(int(sys.argv[2]), int(sys.argv[3])))
    except Exception as exc:
        emit({'error': str(exc)})
        return
    emit({'pid': process.pid})
    def forward():
        try:
            while True:
                emit({'data': process.read(4096)})
        except (EOFError, OSError):
            emit({'closed': True})
            os._exit(0)
    threading.Thread(target=forward, daemon=True).start()
    for line in sys.stdin:
        message = json.loads(line)
        if message.get('close'):
            break
        if 'input' in message:
            process.write(message['input'])
        if 'rows' in message:
            process.setwinsize(message['rows'], message['cols'])
    import signal
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.close(force=True)


if __name__ == '__main__' and sys.argv[1:2] == ['--pty-helper']:
    _pty_helper()
