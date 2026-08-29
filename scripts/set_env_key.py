"""Set one key in .env without touching anything else.

Apex.bat used to write the key with `>".env" echo KEY=value`, and a single `>`
truncates the file. Anyone whose .env still carried a `your_key_here` placeholder
on some *other* line — the state you get by copying .env.example — lost the whole
file: DASHBOARD_TOKEN, VAPID_PRIVATE_KEY, Telegram tokens, all of it. The VAPID
private key in particular cannot be recovered; push subscriptions die with it.

Doing the edit in Python rather than batch also means it can be tested, which the
`>` could not be.

Usage:  python scripts/set_env_key.py ANTHROPIC_API_KEY sk-ant-...
        python scripts/set_env_key.py KEY VALUE --env path/to/.env
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


# A key must look like a shell/env identifier. Anything else cannot be read back
# by dotenv anyway, so writing it would be a silent no-op.
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvWriteRefused(ValueError):
    """The write was refused because it would have corrupted or hijacked .env."""


def set_key(env_path: Path, key: str, value: str) -> str:
    """Insert or replace `key`. Returns what happened, for the caller to print.

    Raises EnvWriteRefused rather than writing something dangerous.

    The newline check is the load-bearing one. This file is written as
    `newline.join(lines)`, so a value containing a line break does not become a
    multi-line value — it becomes a SECOND ASSIGNMENT, of any variable the
    caller names:

        set_key(env, "FOO", "ok\nDASHBOARD_TOKEN=attacker")

    used to produce a .env with the attacker's dashboard token appended. That was
    close to harmless while the only caller was a command line, and stopped being
    harmless the moment /api/control/env could reach it over HTTP.
    """
    if not isinstance(key, str) or not _KEY_RE.match(key or ""):
        raise EnvWriteRefused(f"{key!r} is not a valid environment variable name")
    if not isinstance(value, str):
        raise EnvWriteRefused("value must be text")
    if "\n" in value or "\r" in value:
        raise EnvWriteRefused(
            "a line break in the value would write a second assignment, not a "
            "multi-line value")
    if "\x00" in value:
        raise EnvWriteRefused("a NUL byte cannot go in .env")

    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n", encoding="utf-8", newline="")
        return "created"

    # Back up before touching a file that may hold unrecoverable secrets.
    shutil.copy2(env_path, env_path.with_suffix(env_path.suffix + ".bak"))

    # newline="" disables universal-newline translation on the way in, so the
    # file's real line ending is still visible to detect. Without it every file
    # reads back as \n and CRLF is silently lost.
    with open(env_path, "r", encoding="utf-8", errors="replace", newline="") as f:
        original = f.read()
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()

    out, replaced = [], False
    for line in lines:
        stripped = line.lstrip()
        # Only match a real assignment: `KEY=`. A commented `#KEY=` stays a
        # comment, and KEY_SOMETHING= is a different variable.
        if not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == key:
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            # A duplicate assignment further down would win at load time, so
            # drop it rather than leave the file self-contradicting.
            continue
        out.append(line)

    if not replaced:
        out.append(f"{key}={value}")

    # newline="" again on the way out: the default would translate our "\n" to
    # os.linesep, turning a deliberate "\r\n" into "\r\r\n" on Windows — the one
    # platform this script runs on.
    with open(env_path, "w", encoding="utf-8", newline="") as f:
        f.write(newline.join(out) + newline)
    return "updated" if replaced else "added"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Set one key in a .env file")
    ap.add_argument("key")
    ap.add_argument("value")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args(argv)

    if not args.value.strip():
        print("[env] refusing to write an empty value", file=sys.stderr)
        return 1

    try:
        what = set_key(Path(args.env), args.key, args.value.strip())
    except EnvWriteRefused as e:
        print(f"[env] refusing to write {args.key}: {e}", file=sys.stderr)
        return 1
    print(f"[env] {args.key} {what} in {args.env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
