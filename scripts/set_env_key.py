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
import shutil
import sys
from pathlib import Path


def set_key(env_path: Path, key: str, value: str) -> str:
    """Insert or replace `key`. Returns what happened, for the caller to print."""
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

    what = set_key(Path(args.env), args.key, args.value.strip())
    print(f"[env] {args.key} {what} in {args.env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
