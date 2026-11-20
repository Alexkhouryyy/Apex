"""Background awareness on the owner's Windows laptop: private copies are never
kept, old observations are forgotten, and the window watcher sees windows.

Before: every clipboard copy (passwords and keys included) went into the
perception log forever and into the model's context, and the window watcher
only knew the Linux xdotool, so on Windows it saw nothing.
"""
import time
from types import SimpleNamespace

import pytest

from agent import awareness, longterm, perception


@pytest.mark.parametrize('text', ['sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123',
                                  'ghp_' + 'a1B2' * 9,
                                  'Tr0ub4dor&3', 'hunter2!Pass', 'Xk9qL2mP7vR4',
                                  '-----BEGIN RSA PRIVATE KEY-----\nMIIB'])
def test_secrets_and_passwords_look_private(text):
    assert awareness.looks_private(text)


@pytest.mark.parametrize('text', ['Meet at 5 near the bakery', 'https://example.com/a1B2?x=1', 'README.md',
                                  'lebanon', 'call mom', '2026-11-20'])
def test_ordinary_copies_do_not(text):
    assert not awareness.looks_private(text)


def test_anything_copied_from_a_password_manager_is_private():
    assert awareness.looks_private('my mother maiden name', 'Bitwarden - Vault')
    assert not awareness.looks_private('my mother maiden name', 'Notepad')


def test_the_clipboard_watcher_never_logs_a_password(monkeypatch):
    logged = []
    log = SimpleNamespace(add=lambda source, content: logged.append(content))
    copies = iter(['Tr0ub4dor&3', 'see you at 5'])
    watcher = awareness.ClipboardWatcher(log, interval=0.01)
    monkeypatch.setattr(awareness, 'pyperclip', SimpleNamespace(paste=lambda: next(copies)))
    stops = iter([False, False, True])
    monkeypatch.setattr(watcher._stop_event, 'wait', lambda timeout=None: next(stops))
    watcher.run()
    assert logged == ['Copied: [something private, not kept]', 'Copied: see you at 5']


def test_old_observations_are_forgotten_and_old_private_copies_scrubbed(test_db, monkeypatch):
    perception.init_db()
    monkeypatch.setattr(perception, '_last_prune', time.time())     # only the scrub may prune here
    now = time.time()
    perception.log_event('window', 'Switched to: Old window', now - 40 * 86400)
    perception.log_event('clipboard', 'Copied: Tr0ub4dor&3', now - 86400)
    perception.log_event('clipboard', 'Copied: see you at 5', now - 86400)
    assert awareness.scrub_stored_copies() == 1
    with longterm._conn() as c:
        rows = [r[0] for r in c.execute('SELECT content FROM perception_log ORDER BY id')]
    assert rows == ['Copied: [something private, not kept]', 'Copied: see you at 5']


def test_logging_prunes_at_most_hourly(test_db, monkeypatch):
    perception.init_db()
    pruned = []
    monkeypatch.setattr(perception, 'prune', lambda days=perception.RETENTION_DAYS: pruned.append(days) or 0)
    monkeypatch.setattr(perception, '_last_prune', 0.0)
    perception.log_event('window', 'a')
    perception.log_event('window', 'b')
    assert pruned == [perception.RETENTION_DAYS]


def test_the_window_watcher_reads_windows_titles_on_windows(monkeypatch):
    calls = []
    class User32:
        def GetForegroundWindow(self): return 42
        def GetWindowTextLengthW(self, hwnd): calls.append(hwnd); return 20
        def GetWindowTextW(self, hwnd, buffer, size): buffer.value = 'Inbox - Gmail'
    import ctypes
    monkeypatch.setattr(awareness, '_WINDOWS', True)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=User32()), raising=False)
    monkeypatch.setattr(awareness.subprocess, 'run', lambda *a, **k: pytest.fail('xdotool is not on Windows'))
    assert awareness.active_window_title() == 'Inbox - Gmail' and calls == [42]


def test_the_monitor_cleans_old_copies_on_start_and_knows_the_front_window(monkeypatch):
    scrubbed = []
    monkeypatch.setattr(awareness, 'scrub_stored_copies', lambda: scrubbed.append(1) or 0)
    monitor = awareness.AwarenessMonitor(agent_proactive_check=lambda s: None, speak_fn=lambda t: None)
    monkeypatch.setattr(monitor, '_start_watchers', lambda: None)
    monkeypatch.setattr(monitor, 'stop', lambda: None)
    monitor.window._last_title = 'KeePassXC'
    assert monitor.clipboard.window_title() == 'KeePassXC'
    monitor.start()
    assert scrubbed == [1]
    monkeypatch.setattr(awareness, '_running', False)
