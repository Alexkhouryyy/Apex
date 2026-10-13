"""Native input routing and failures; all transports are mocked, no desktop actions."""
import ctypes
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tools import computer, windows_input


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    monkeypatch.setattr(computer.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    # Unexpected commands fail before subprocess can touch the real host.
    monkeypatch.setattr(computer.subprocess, "run", Mock(side_effect=AssertionError("unmocked native input")))


def x11(monkeypatch, *, fail_at=None, stdout=""):
    calls = []
    def run(args, **kwargs):
        assert kwargs == {"capture_output": True, "text": True, "timeout": 10}
        calls.append(args)
        return NS(returncode=1 if len(calls) == fail_at else 0, stdout=stdout,
                  stderr="cannot connect to X server" if len(calls) == fail_at else "")
    monkeypatch.setattr(computer.subprocess, "run", run)
    return calls


def gui(monkeypatch, system="Windows"):
    monkeypatch.setattr(computer.platform, "system", lambda: system)
    backend = NS(**{name: Mock() for name in ("click", "moveTo", "write", "hotkey", "scroll", "dragTo", "size", "failSafeCheck")})
    backend.size.return_value = (1366, 768)
    backend.KEYBOARD_KEYS = ["ctrl", "ctrlleft", "ctrlright", "enter", "esc", "win", "command", "a", "b", "c", " "]
    monkeypatch.setitem(sys.modules, "pyautogui", backend)
    return backend


@pytest.mark.parametrize("operation", [lambda: computer.click(10, 20), lambda: computer.type_text("hello"),
    lambda: computer.hotkey("ctrl", "a"), lambda: computer.scroll(10, 20, 2),
    lambda: computer.move_mouse(10, 20), computer.get_screen_size, lambda: computer.drag(1, 2, 3, 4)])
def test_x11_failure_never_reports_success(monkeypatch, operation):
    x11(monkeypatch, fail_at=1)
    with pytest.raises(computer.ComputerControlError, match="cannot connect"):
        operation()


def test_click_stops_if_movement_fails(monkeypatch):
    calls = x11(monkeypatch, fail_at=1)
    with pytest.raises(computer.ComputerControlError): computer.click(10, 20)
    assert calls == [["xdotool", "mousemove", "--", "10", "20"]]


def test_click_error_after_move_is_propagated(monkeypatch):
    calls = x11(monkeypatch, fail_at=2)
    with pytest.raises(computer.ComputerControlError): computer.click(10, 20)
    assert len(calls) == 2


def test_x11_double_click_and_literal_typing(monkeypatch):
    calls = x11(monkeypatch)
    assert "double right click" in computer.click(10, 20, button="right", double=True)
    computer.type_text("--help مرحبا", delay_ms=0)
    assert calls[-2] == ["xdotool", "click", "--repeat", "2", "--delay", "100", "3"]
    assert calls[-1] == ["xdotool", "type", "--delay=0", "--", "--help مرحبا"]


def test_long_typing_gets_time_to_send_the_whole_text(monkeypatch):
    captured = []
    def run(args, **kwargs):
        captured.append((args, kwargs))
        return NS(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(computer.subprocess, "run", run)
    text = "a" * 1000
    computer.type_text(text, delay_ms=30)
    assert captured[0][0][-1] == text
    assert captured[0][1]["timeout"] > 30


def test_drag_releases_button_when_movement_fails(monkeypatch):
    calls = x11(monkeypatch, fail_at=3)
    with pytest.raises(computer.ComputerControlError): computer.drag(1, 2, 3, 4)
    assert calls[-1] == ["xdotool", "mouseup", "1"]


@pytest.mark.parametrize("failure", [FileNotFoundError(), computer.subprocess.TimeoutExpired("xdotool", 10)])
def test_missing_or_hung_transport_is_explicit(monkeypatch, failure):
    monkeypatch.setattr(computer.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises((computer.ComputerUnavailable, computer.ComputerControlError)):
        computer.click(1, 2)


@pytest.mark.parametrize("output", ["", "broken", "1920 0", "-1 1080"])
def test_invalid_geometry_is_never_a_default_resolution(monkeypatch, output):
    x11(monkeypatch, stdout=output)
    with pytest.raises(computer.ComputerControlError): computer.get_screen_size()


def test_x11_geometry_is_reported(monkeypatch):
    x11(monkeypatch, stdout="2560 1440")
    assert computer.get_screen_size() == (2560, 1440)


@pytest.mark.parametrize("system", ["Windows", "Darwin"])
def test_native_platform_routes_without_xdotool(monkeypatch, system):
    backend = gui(monkeypatch, system)
    computer.click(10, 20, "middle", double=True)
    backend.click.assert_called_once_with(x=10, y=20, button="middle", clicks=2, interval=0.1)
    computer.move_mouse(30, 40)
    backend.moveTo.assert_called_once_with(30, 40)
    computer.hotkey("Control", "Return")
    backend.hotkey.assert_called_once_with("ctrl", "enter")
    computer.scroll(10, 20, -3)
    backend.scroll.assert_called_once_with(-3, x=10, y=20)
    computer.drag(1, 2, 3, 4)
    backend.dragTo.assert_called_once_with(3, 4, duration=0.3, button="left")
    assert computer.get_screen_size() == (1366, 768)
    computer.subprocess.run.assert_not_called()


def test_native_backend_errors_and_failsafe_propagate(monkeypatch):
    backend = gui(monkeypatch)
    backend.click.side_effect = RuntimeError("fail-safe triggered")
    with pytest.raises(RuntimeError, match="fail-safe"): computer.click(1, 2)
    assert backend.click.call_count == 1


def test_unknown_hotkey_is_rejected_before_input(monkeypatch):
    backend = gui(monkeypatch)
    with pytest.raises(ValueError): computer.hotkey("ctrl", "imaginary_key")
    backend.hotkey.assert_not_called()


@pytest.mark.parametrize("operation", [lambda: computer.click("--help", 0),
    lambda: computer.scroll(0, 0, "2"), lambda: computer.click(0, 0, double="false"),
    lambda: computer.drag(0, 0, 1, False)])
def test_invalid_input_cannot_reach_desktop_transport(monkeypatch, operation):
    backend = gui(monkeypatch)
    with pytest.raises(ValueError): operation()
    backend.click.assert_not_called()
    backend.moveTo.assert_not_called()
    backend.scroll.assert_not_called()


def test_mac_typing_rejects_unicode_before_any_input(monkeypatch):
    backend = gui(monkeypatch, "Darwin")
    with pytest.raises(computer.ComputerUnavailable, match="ASCII"):
        computer.type_text("abc مرحبا")
    backend.write.assert_not_called()
    computer.type_text("Abc", 10)
    backend.write.assert_called_once_with("Abc", interval=0.01)


def test_windows_typing_routes_to_unicode_and_failsafe(monkeypatch):
    backend = gui(monkeypatch)
    typed = Mock()
    monkeypatch.setattr(windows_input, "type_unicode", typed)
    computer.type_text("مرحبا 😊", 0)
    typed.assert_called_once_with("مرحبا 😊", 0, check_abort=backend.failSafeCheck)
    backend.write.assert_not_called()


@pytest.mark.parametrize("mode", ["headless", "wayland", "unsupported", "missing_gui"])
def test_unavailable_desktops_do_not_execute(monkeypatch, mode):
    if mode == "headless": monkeypatch.delenv("DISPLAY")
    elif mode == "wayland": monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    elif mode == "unsupported": monkeypatch.setattr(computer.platform, "system", lambda: "OtherOS")
    else:
        monkeypatch.setattr(computer.platform, "system", lambda: "Windows")
        monkeypatch.setitem(sys.modules, "pyautogui", None)
    with pytest.raises(computer.ComputerUnavailable): computer.click(1, 2)
    computer.subprocess.run.assert_not_called()


def test_windows_unicode_abi_surrogates_and_control_keys(monkeypatch):
    batches = []
    def send(count, events, size):
        assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        batches.append([(event.data.ki.wVk, event.data.ki.wScan, event.data.ki.dwFlags) for event in events])
        return count
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: NS(SendInput=Mock(side_effect=send)), raising=False)
    windows_input.type_unicode("م😊\r\n\t", 0)
    assert batches[0] == [(0, ord("م"), 4), (0, ord("م"), 6)]
    assert [scan for vk, scan, flags in batches[1] if flags == 4] == [0xD83D, 0xDE0A]
    assert batches[2] == [(13, 0, 0), (13, 0, 2)]
    assert batches[3] == [(9, 0, 0), (9, 0, 2)]


def test_partial_windows_input_attempts_release_and_raises(monkeypatch):
    send = Mock(side_effect=[1, 2])
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: NS(SendInput=send), raising=False)
    with pytest.raises(computer.ComputerControlError, match="partial"):
        windows_input.type_unicode("ab", 0)
    assert send.call_count == 2
    assert all(event.data.ki.dwFlags & 2 for event in send.call_args.args[1])


def test_unicode_typing_can_be_aborted_between_characters(monkeypatch):
    send = Mock(side_effect=lambda count, *args: count)
    abort = Mock(side_effect=[None, RuntimeError("user fail-safe")])
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: NS(SendInput=send), raising=False)
    with pytest.raises(RuntimeError, match="fail-safe"):
        windows_input.type_unicode("ab", 0, check_abort=abort)
    assert send.call_count == 1


def test_unicode_batches_keep_surrogate_pairs_together_and_check_cancellation(monkeypatch):
    calls=[]
    def send(count,events,size):
        calls.append([(e.data.ki.wScan,e.data.ki.dwFlags) for e in events]);return count
    monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**kw:NS(SendInput=Mock(side_effect=send)),raising=False)
    abort=Mock(side_effect=[None,RuntimeError('session stopped')])
    with pytest.raises(RuntimeError,match='session stopped'):
        windows_input.type_unicode('a'*31+'😊'+'later',0,check_abort=abort,batch_size=32)
    assert len(calls)==1 and len(calls[0])==66
    assert [scan for scan,flags in calls[0][-4:] if flags==4]==[0xd83d,0xde0a]


@pytest.mark.parametrize('size',[0,33,True])
def test_unicode_input_refuses_unbounded_batches(size):
    with pytest.raises(ValueError):windows_input.type_unicode('text',0,batch_size=size)


def test_agent_dispatch_does_not_claim_failed_click(monkeypatch):
    from agent import core
    monkeypatch.setattr(core.safety, "check", lambda *a, **kw: (True, ""))
    x11(monkeypatch, fail_at=1)
    result = core._execute_tool("click", {"x": 10, "y": 20})
    assert "cannot connect" in result and "Sent left click" not in result
