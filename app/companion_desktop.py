"""Persistent native Companion window; only a user activation starts voice.

pywebview runs on this process's main thread. The click-through Tk drawing
surface has its own process and GUI thread. The resident owns this UI process.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from agent.desktop_companion import local_url

ROOT = Path(__file__).resolve().parents[1]


def position(height):
    if os.name == 'nt':
        import ctypes
        user = ctypes.windll.user32
        return max(0, user.GetSystemMetrics(0)-444), max(0, user.GetSystemMetrics(1)-height-64)
    return None, None


FOLLOW_SIZE = (320, 220)   # the compact bubble while it rides beside the cursor


def interactive(window, enabled):
    """Voice help leaves app focus alone; expanded Chat must accept typing."""
    window.focus = enabled
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        get = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
        put = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
        get.argtypes, get.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
        put.argtypes, put.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        hwnd = window.native.Handle.ToInt64()
        style = get(hwnd, -20)
        put(hwnd, -20, style & ~0x08000000 if enabled else style | 0x08000000)


def gui_call(window, callback):
    if os.name == 'nt' and window.native.InvokeRequired:
        from System import Action
        window.native.Invoke(Action(callback))
    else:
        callback()


def reveal(window):
    if os.name == 'nt':
        def restore():
            import ctypes
            from ctypes import wintypes
            user = ctypes.windll.user32
            get, put = user.GetWindowLongPtrW, user.SetWindowLongPtrW
            get.argtypes, get.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
            put.argtypes, put.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
            hwnd = window.native.Handle.ToInt64()
            put(hwnd, -20, get(hwnd, -20) & ~0x20)
            window.native.Opacity = 1
        gui_call(window, restore)


class IPC:
    def __init__(self, url, token, session):
        self.url = local_url(url)
        self.headers = {'X-Apex-Desktop-Session': session, 'Content-Type': 'application/json'}
        if token:
            self.headers['Authorization'] = 'Bearer ' + token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path='', body=None):
        request = urllib.request.Request(self.url + '/api/companion/desktop' + path,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers=self.headers)
        with self.opener.open(request, timeout=3) as response:
            return json.load(response)


class Overlay:
    def __init__(self):
        self.process = None
        self.replies = queue.Queue()

    def send(self, item, wait=False):
        if os.name != 'nt':
            raise RuntimeError('Native screen drawings require Windows.')
        if self.process is None or self.process.poll() is not None:
            if item['kind'] != 'draw':
                return {'ok': True}
            python = Path(sys.executable)
            # Use the console interpreter with redirected handles: stdout is our IPC.
            if python.name.lower() == 'pythonw.exe':
                python = python.with_name('python.exe')
            self.process = subprocess.Popen([str(python), '-u', '-m', 'app.screen_overlay'], cwd=ROOT,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            process = self.process
            def read():
                for line in process.stdout:
                    try:
                        self.replies.put(json.loads(line))
                    except ValueError:
                        pass
            threading.Thread(target=read, daemon=True).start()
            def errors():
                for line in process.stderr:
                    logging.error('Overlay: %s', line.strip()[:500])
            threading.Thread(target=errors, daemon=True).start()
        self.process.stdin.write(json.dumps(item) + '\n')
        self.process.stdin.flush()
        if not wait:
            return {'ok': True}
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                result = self.replies.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if result.get('seq') == item['seq']:
                return result
        raise RuntimeError('Screen overlay did not confirm painting.')

    def stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.stdin.write('{"kind":"quit"}\n')
                process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                from scripts.run_apex_qwen import stop as stop_process
                stop_process(process)
        self.process = None


class Bridge:
    def __init__(self, ipc, token):
        self._ipc, self._token = ipc, token
        self._window = None
        self._ready = threading.Event()
        self._activation = False
        self._dictation = None
        self._canvas = None

    def _trusted(self):
        current = urlsplit(self._window.get_current_url() or '')
        expected = urlsplit(self._ipc.url)
        return (current.scheme, current.hostname, current.port, current.path) == (
            expected.scheme, expected.hostname, expected.port, '/companion')

    def connect(self):
        if not self._trusted():
            raise ValueError('Desktop bridge is only available to the local Companion page.')
        import config
        logging.info('Local Companion page connected to its native bridge.')
        follower = getattr(self, '_follower', None)
        return {'desktop': True, 'token': self._token, 'drawings': os.name == 'nt', 'hotkey': config.COMPANION_HOTKEY,
                'follow': bool(follower and follower.enabled), 'can_follow': bool(follower)}

    def ready(self):
        if not self._trusted():
            raise ValueError('Desktop bridge is only available to the local Companion page.')
        if not self._ready.is_set():
            self._window.hide()
            reveal(self._window)
        if os.name=='nt':
            handles=[]
            gui_call(self._window,lambda:handles.append(self._window.native.Handle.ToInt64()))
            self._ipc.call(body={'action':'surface','role':'companion','hwnd':handles[0]})
        self._ipc.call(body={'action': 'ready'})
        self._ready.set()
        logging.info('Companion authenticated and ready; microphone remains off.')
        return True

    def report_error(self, message):
        if not self._trusted():
            raise ValueError('Desktop bridge is only available to the local Companion page.')
        logging.error('Companion client boot: %s', str(message)[:500])

    def hide(self):
        if not self._trusted():
            raise ValueError('Desktop bridge is only available to the local Companion page.')
        self._ipc.call(body={'action': 'hidden'})
        self._activation = False
        self._stop_dictation()
        self._close_canvas()
        self._window.hide()
        return True

    def _stop_dictation(self):
        if self._dictation is not None:
            self._dictation.stop()

    def _close_canvas(self):
        if self._canvas is not None and self._canvas.poll() is None:
            from scripts.run_apex_qwen import stop as stop_process
            stop_process(self._canvas)
        self._canvas = None

    def choose_mode(self, mode):
        if not self._trusted():
            raise ValueError('Open the local Companion page.')
        self._stop_dictation()
        self._ipc.call(body={'action':'mode', 'mode':mode})
        return True

    def dictate(self, text, generation):
        if not self._trusted() or not self._activation or self._dictation is None:
            raise ValueError('Start dictation with F8 in your selected app first.')
        return self._dictation.insert(text, generation)

    def sketch(self):
        if not self._trusted() or os.name != 'nt':
            raise ValueError('Screen drawing requires the local Windows Companion.')
        self._stop_dictation()
        if self._canvas is not None and self._canvas.poll() is None:
            return {'open':True}
        from agent.look_now import capture_frame
        _, frame = capture_frame()
        python = Path(sys.executable)
        if python.name.lower() == 'pythonw.exe': python = python.with_name('python.exe')
        handles=[]
        gui_call(self._window,lambda:handles.append(self._window.native.Handle.ToInt64()))
        self._canvas = subprocess.Popen([str(python),'-u','-m','app.screen_canvas','--frame',json.dumps(frame),
                                         '--parent-pid',str(os.getpid()),'--companion-hwnd',str(handles[0])], cwd=ROOT,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                        creationflags=subprocess.CREATE_NO_WINDOW)
        process, ready = self._canvas, queue.Queue()
        def read():
            try: ready.put(json.loads(process.stdout.readline()))
            except (ValueError,OSError): ready.put({'ready':False})
            finally: process.stdout.close()
        def errors():
            for line in process.stderr: logging.error('Screen canvas: %s',line.strip()[:500])
            process.stderr.close()
        threading.Thread(target=read,daemon=True).start()
        threading.Thread(target=errors,daemon=True).start()
        try: result = ready.get(timeout=8)
        except queue.Empty: result = {'ready':False}
        if result.get('ready') is not True:
            self._close_canvas()
            raise RuntimeError('The drawing surface could not open. See Companion desktop.log.')
        self._ipc.call(body={'action':'surface','role':'toolbar','hwnd':result['toolbar']})
        return {'open':True}

    def follow(self, on):
        """Ride beside the cursor (on) or go back to the corner (off)."""
        if not self._trusted():
            raise ValueError('Desktop bridge is only available to the local Companion page.')
        follower = getattr(self, '_follower', None)
        if follower is None:
            raise ValueError('Following the cursor needs the Windows Companion.')
        follower.set_enabled(bool(on))
        follower.paused = False
        if on:
            self._window.resize(*FOLLOW_SIZE)
        else:
            self._window.resize(420, 300)
            x, y = position(300)
            if x is not None:
                self._window.move(x, y)
        return {'follow': follower.enabled}

    def expand(self):
        if not self._trusted():
            raise ValueError('Desktop bridge is only available to the local Companion page.')
        self._stop_dictation()
        self._ipc.call(body={'action': 'view'})
        if getattr(self, '_follower', None):
            self._follower.paused = True
        interactive(self._window, True)
        self._window.resize(420, 640)
        x, y = position(640)
        if x is not None:
            self._window.move(x, y)
        self._window.show()
        return True


class MicrophonePermission:
    """One local microphone decision, requested only after a user invocation."""
    def __init__(self, bridge, path):
        self.bridge, self.path = bridge, Path(path)
        try:
            value = json.loads(self.path.read_text(encoding='utf-8')).get('microphone')
            self.allowed = value if type(value) is bool else None
        except (OSError, ValueError, AttributeError):
            self.allowed = None

    def decide(self, kind, initiated=False, uri=None):
        if kind != 'Microphone' or not self.bridge._trusted() or not (self.bridge._activation or initiated):
            return False
        if uri is not None:
            expected, requested = urlsplit(self.bridge._ipc.url), urlsplit(uri)
            if (requested.scheme, requested.hostname, requested.port) != (expected.scheme, expected.hostname, expected.port):
                return False
        if self.allowed is None:
            self.allowed = bool(self.bridge._window.create_confirmation_dialog(
                'Apex microphone', 'Allow Screen Companion to use your microphone when you start voice or screen help? '
                'Stop, Esc or closing the window ends listening.'))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({'microphone': self.allowed}), encoding='utf-8')
        return self.allowed


def parent_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--parent-pid', required=True, type=int)
    args = parser.parse_args(argv)
    session = os.environ.get('APEX_DESKTOP_SESSION')
    if not session:
        raise RuntimeError('Start Desktop Companion from Apex, not as an unregistered window.')
    url = local_url(args.url)
    data = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.apex') / 'Apex' / 'Companion'
    data.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data / 'desktop.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    logging.info('Starting native Companion window, parent %s.', args.parent_pid)
    logging.getLogger('pywebview').propagate = True
    # Allow voice output after an OS hotkey; keep normal origin/web security.
    flags = os.environ.get('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '')
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = flags + (
        ' --autoplay-policy=no-user-gesture-required'
        ' --disable-background-timer-throttling --disable-renderer-backgrounding'
        ' --disable-backgrounding-occluded-windows'
        ' --disable-features=ElasticOverscroll,CalculateNativeWinOcclusion')
    import config
    import webview
    ipc = IPC(url, config.DASHBOARD_TOKEN, session)
    bridge, overlay = Bridge(ipc, config.DASHBOARD_TOKEN), Overlay()
    x, y = position(300)
    window = webview.create_window('Apex Screen Companion', url + '/companion?desktop=1',
                                   width=420, height=300, x=x, y=y, hidden=os.name != 'nt', on_top=True, focus=False,
                                   js_api=bridge, background_color='#101318')
    bridge._window = window
    if os.name == 'nt':
        from app import follow as follow_mod
        follower = follow_mod.Follower(follow_mod.windows_cursor, follow_mod.windows_work_area,
                                       lambda: FOLLOW_SIZE, lambda x, y: window.move(x, y),
                                       enabled=getattr(config, 'COMPANION_FOLLOW_CURSOR', False))
        follower.paused = True        # nothing to follow until the bubble is shown
        bridge._follower = follower
        follower.start()
    permission = MicrophonePermission(bridge, data / 'permissions.json')
    shutting_down = threading.Event()

    def bootstrap():
        if os.name != 'nt':
            return
        # WebView2 can defer script callbacks in a hidden Form. Let it finish
        # booting in a zero-opacity, click-through Form; ready() then hides it.
        window.native.Opacity = 0
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32
        get,put=user.GetWindowLongPtrW,user.SetWindowLongPtrW
        get.argtypes,get.restype=[wintypes.HWND,ctypes.c_int],ctypes.c_ssize_t
        put.argtypes,put.restype=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t],ctypes.c_ssize_t
        hwnd=window.native.Handle.ToInt64()
        # Change styles in place: ShowInTaskbar can recreate the Form handle
        # while WebView2 is still initializing against it.
        put(hwnd,-20,(get(hwnd,-20)&~0x00040000)|0x80|0x08000000|0x20)
    window.events.before_show += bootstrap

    def closing():
        if shutting_down.is_set():
            return True
        bridge._activation = False
        bridge._stop_dictation()
        bridge._close_canvas()
        def stop_page():
            try:
                if bridge._trusted():
                    window.evaluate_js('void window.ApexDesktop?.stop()')
                ipc.call(body={'action': 'hidden'})
            except Exception:
                logging.exception('Could not report window close')
        threading.Thread(target=stop_page, daemon=True).start()
        window.hide()
        return False

    window.events.closing += closing

    def permissions():
        if os.name != 'nt':
            return
        logging.info('Native WebView loaded; attaching microphone permission handling.')
        try:
            from app.window_capture import exclude_from_capture
            if not exclude_from_capture(window.native.Handle.ToInt64()):
                logging.warning('Windows could not exclude the Companion window from screen capture.')
        except Exception:
            logging.exception('Native capture exclusion unavailable')
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
            def requested(sender, event):
                try:
                    kind = str(event.PermissionKind)
                    if kind != 'Microphone':
                        return  # do not auto-grant camera or other permissions
                    allowed = permission.decide(kind, bool(event.IsUserInitiated), str(event.Uri))
                    event.State = CoreWebView2PermissionState.Allow if allowed else CoreWebView2PermissionState.Deny
                    try:
                        event.SavesInProfile = False
                    except AttributeError:
                        pass
                except Exception:
                    event.State = CoreWebView2PermissionState.Deny
                    logging.exception('Microphone permission failed')
            window.native.webview.CoreWebView2.PermissionRequested += requested
            logging.info('Microphone permission hook attached on the GUI thread.')
        except Exception:
            logging.exception('Microphone permission hook unavailable')
    window.events.loaded += lambda: gui_call(window, permissions)

    def commands():
        after, failures = 0, 0
        while not shutting_down.is_set() and parent_alive(args.parent_pid):
            try:
                commands = ipc.call('/events?after=' + str(after))['events']
                failures = 0
                for item in commands:
                    if not bridge._ready.is_set() and item['kind'] == 'activate':
                        reveal(window)
                        window.show()
                        break  # keep the first hotkey until page authentication/boot completes
                    if item['kind'] == 'quit':
                        shutting_down.set()
                        break
                    if item['kind'] == 'show':
                        if getattr(bridge, '_follower', None):
                            bridge._follower.paused = True
                        reveal(window)
                        interactive(window, True)
                        window.resize(420, 640)
                        x, y = position(640)
                        if x is not None:
                            window.move(x, y)
                        window.show()
                    if item['kind'] == 'activate':
                        if not bridge._trusted():
                            window.load_url(url + '/companion?desktop=1')
                            bridge._ready.clear()
                            break
                        bridge._stop_dictation()
                        if item['item'].get('kind') == 'stop': bridge._close_canvas()
                        if item['item'].get('kind') == 'dictate':
                            from app.desktop_dictation import Session
                            item['item']['generation'] = item['seq']
                            bridge._dictation = Session(item['item']['target'], item['seq'])
                            item['item']['target'] = {'title':item['item']['target']['title']}
                        if item['item'].get('kind') != 'stop':
                            reveal(window)
                            interactive(window, False)
                            follower = getattr(bridge, '_follower', None)
                            if follower and follower.enabled:
                                window.resize(*FOLLOW_SIZE)          # the bubble rides the cursor
                                follower.paused = False
                            else:
                                window.resize(420, 300)
                                x, y = position(300)
                                if x is not None:
                                    window.move(x, y)
                            window.show()
                        bridge._activation = item['item'].get('kind') != 'stop'
                        window.evaluate_js('void window.ApexDesktop.activate(' + json.dumps(item['item']) + ')')
                    elif item['kind'] in {'clear', 'hide'}:
                        overlay.send({'kind': 'clear'})
                        if item['kind'] == 'hide':
                            if getattr(bridge, '_follower', None):
                                bridge._follower.paused = True
                            bridge._activation = False
                            bridge._stop_dictation()
                            bridge._close_canvas()
                            if bridge._trusted():
                                window.evaluate_js('void window.ApexDesktop?.stop()')
                            window.hide()
                    elif item['kind'] == 'draw':
                        try:
                            result = overlay.send(item, wait=True)
                            if result.get('ok') and result.get('hwnd'):
                                ipc.call(body={'action':'surface','role':'guidance','hwnd':result['hwnd']})
                        except Exception as exc:
                            result = {'ok': False, 'error': str(exc)}
                        ipc.call(body={'action': 'ack', 'seq': item['seq'], 'result': result})
                    after = item['seq']
            except Exception:
                failures += 1
                if failures in {1, 20}:
                    logging.exception('Desktop command channel unavailable')
                if failures >= 40:
                    break
            shutting_down.wait(.25)
        shutting_down.set()
        if getattr(bridge, '_follower', None):
            bridge._follower.stop()
        bridge._stop_dictation()
        bridge._close_canvas()
        overlay.stop()
        window.destroy()

    try:
        webview.start(commands, private_mode=False, storage_path=str(data / 'browser'),
                      gui='edgechromium' if os.name == 'nt' else None)
    finally:
        shutting_down.set()
        bridge._stop_dictation()
        bridge._close_canvas()
        overlay.stop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('Desktop Companion stopped')
        raise
