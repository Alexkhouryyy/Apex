"""Autostart-on-login installer for Apex Resident.

Run:
    python -m app.autostart install     # set up autostart for current OS
    python -m app.autostart uninstall   # remove it
    python -m app.autostart status      # show whether autostart is active

Supports Linux (XDG .desktop in ~/.config/autostart),
macOS (LaunchAgent plist), and Windows (HKCU\\...\\Run registry key).
"""
import os
import platform
import sys
from pathlib import Path

APP_NAME = "ApexResident"
APP_LABEL = "Apex Resident"

# Resolve the project root and main.py so the installer is portable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _PROJECT_ROOT / "main.py"
_PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Linux — XDG autostart
# ---------------------------------------------------------------------------

def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / "apex-resident.desktop"


def _linux_install() -> str:
    target = _linux_desktop_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = f"""[Desktop Entry]
Type=Application
Name={APP_LABEL}
Comment=Always-on AI companion
Exec={_PYTHON} {_MAIN_PY} --resident
Icon=utilities-terminal
Terminal=false
X-GNOME-Autostart-enabled=true
"""
    target.write_text(content, encoding='utf-8')
    target.chmod(0o644)
    return f"Installed: {target}"


def _linux_uninstall() -> str:
    target = _linux_desktop_path()
    if target.exists():
        target.unlink()
        return f"Removed: {target}"
    return "Not installed."


def _linux_status() -> str:
    target = _linux_desktop_path()
    return f"Installed at {target}" if target.exists() else "Not installed."


# ---------------------------------------------------------------------------
# macOS — LaunchAgent plist
# ---------------------------------------------------------------------------

def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.apex.resident.plist"


def _macos_install() -> str:
    target = _macos_plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.apex.resident</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_PYTHON}</string>
        <string>{_MAIN_PY}</string>
        <string>--resident</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>{Path.home()}/.apex/launchd.out.log</string>
    <key>StandardErrorPath</key><string>{Path.home()}/.apex/launchd.err.log</string>
</dict>
</plist>
"""
    target.write_text(content, encoding='utf-8')
    # Best-effort launchctl load — never fatal
    try:
        import subprocess
        subprocess.run(["launchctl", "load", str(target)], check=False, timeout=5)
    except Exception:
        pass
    return f"Installed: {target}"


def _macos_uninstall() -> str:
    target = _macos_plist_path()
    if not target.exists():
        return "Not installed."
    try:
        import subprocess
        subprocess.run(["launchctl", "unload", str(target)], check=False, timeout=5)
    except Exception:
        pass
    target.unlink()
    return f"Removed: {target}"


def _macos_status() -> str:
    target = _macos_plist_path()
    return f"Installed at {target}" if target.exists() else "Not installed."


# ---------------------------------------------------------------------------
# Windows — registry Run key
# ---------------------------------------------------------------------------

def _windows_install() -> str:
    try:
        import winreg  # type: ignore
    except ImportError:
        return "winreg not available — not on Windows?"
    # Prefer pythonw.exe so login doesn't briefly flash a console window
    python_exe = _PYTHON
    pythonw_candidate = python_exe.replace("python.exe", "pythonw.exe")
    if pythonw_candidate != python_exe and Path(pythonw_candidate).exists():
        python_exe = pythonw_candidate
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE,
    )
    value = f'"{python_exe}" -X utf8 "{_MAIN_PY}" --resident'   # UTF-8 text, as every launcher
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
    winreg.CloseKey(key)
    return f"Installed HKCU registry key: {APP_NAME} -> {value}"


def _windows_uninstall() -> str:
    try:
        import winreg  # type: ignore
    except ImportError:
        return "winreg not available — not on Windows?"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        return f"Removed HKCU registry key: {APP_NAME}"
    except FileNotFoundError:
        return "Not installed."


def _windows_status() -> str:
    try:
        import winreg  # type: ignore
    except ImportError:
        return "winreg not available — not on Windows?"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        value, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return f"Installed: {value}"
    except FileNotFoundError:
        return "Not installed."


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _dispatch(action: str) -> str:
    system = platform.system()
    handlers = {
        "Linux": (_linux_install, _linux_uninstall, _linux_status),
        "Darwin": (_macos_install, _macos_uninstall, _macos_status),
        "Windows": (_windows_install, _windows_uninstall, _windows_status),
    }
    if system not in handlers:
        return f"Unsupported OS: {system}"
    install, uninstall, status = handlers[system]
    if action == "install":
        return install()
    if action == "uninstall":
        return uninstall()
    if action == "status":
        return status()
    return f"Unknown action: {action}"


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"install", "uninstall", "status"}:
        print("Usage: python -m app.autostart [install|uninstall|status]")
        return 1
    print(_dispatch(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
