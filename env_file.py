"""Read Apex's .env whatever Windows saved it as.

PowerShell 5's `>>`, `Out-File` and `Add-Content` write UTF-16, and Notepad on
older Windows can save "ANSI" (cp1252). python-dotenv reads UTF-8 only, so one
line added that way made Apex refuse to start with a bare
"'utf-8' codec can't decode byte 0xff". This reads UTF-8 (with or without a
BOM), UTF-16 (by its BOM) and, as a last resort, cp1252, and says once which
it had to use.

Deliberately has no side effects and imports nothing from Apex, so the
launchers can use it without loading .env into their own environment.
"""
from __future__ import annotations

import io
from pathlib import Path

_noted: set = set()


def env_text(path) -> str:
    """The text of `path`, or '' if it doesn't exist or can't be read."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return ''
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    else:
        try:
            text = raw.decode('utf-8-sig')
            if '\x00' not in text:
                return text
            # UTF-8 lines with UTF-16 ones appended (Add-Content on PowerShell 5):
            # the appended ASCII reads back with a NUL after every letter.
            encoding = 'mixed UTF-8/UTF-16'
            text = text.replace('\x00', '').replace('\ufeff', '').replace('\ufffe', '')
            _note(p, encoding)
            return text
        except UnicodeDecodeError:
            encoding = 'cp1252'
    text = raw.decode(encoding, errors='replace')
    _note(p, encoding)
    return text


def _note(p, encoding) -> None:
    if str(p) not in _noted:
        _noted.add(str(p))
        print(f"[Config] {p.name} is saved as {encoding.upper()}, not UTF-8; read it anyway. "
              f"Save it as UTF-8 (Notepad: File > Save As > Encoding) to make this go away.", flush=True)


def env_values(path) -> dict:
    """{KEY: value} from a .env file, like dotenv_values, in any of those encodings."""
    from dotenv import dotenv_values
    return {k: v for k, v in dotenv_values(stream=io.StringIO(env_text(path))).items() if v is not None}


def load_env(path, override: bool = False) -> bool:
    """load_dotenv for a .env file in any of those encodings."""
    from dotenv import load_dotenv
    return load_dotenv(stream=io.StringIO(env_text(path)), override=override)
