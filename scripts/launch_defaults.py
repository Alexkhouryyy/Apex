"""One set of startup defaults for every Apex launcher.

Apex used to behave differently depending on how it was started: the sign-in
launcher (scripts/start_screen_companion.py) turned the desktop Companion on and
wake phrases off, Start-Apex-All (scripts/run_apex_qwen.py) left the Companion
off and turned "Hey Celly" on, and start-apex.bat (scripts/apex_supervisor.py)
used config.py's own defaults. Every launcher now applies these, so the same
.env gives the same Apex.

A default only fills a gap: anything set in the environment or in .env wins.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {
    # The native, always-on-top Companion window (F8). Without it the hotkeys,
    # Follow and on-screen drawing have nothing to open.
    'COMPANION_DESKTOP_ENABLED': 'true',
    # Hotkeys start a request (F8, Ctrl+Alt+C); no microphone listens in the
    # background unless .env turns a wake phrase on.
    'CELINE_WAKE_ENABLED': 'false',
    'WAKE_WORD_ENABLED': 'false',
}


def dotenv_keys(root: Path = ROOT) -> set[str]:
    try:
        from dotenv import dotenv_values
        return set(dotenv_values(Path(root) / '.env'))
    except Exception:
        return set()


def apply(env: dict, keys: set[str] | None = None) -> dict:
    """Fill in DEFAULTS for whatever neither `env` nor .env (`keys`) sets."""
    keys = dotenv_keys() if keys is None else keys
    for name, value in DEFAULTS.items():
        if name not in env and name not in keys:
            env[name] = value
    return env
