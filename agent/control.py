"""Operating Apex from Apex — config, keys, restart and update.

Every setting Apex has lives in `.env`, and until now the only way to change one
was a terminal: open cmd, find the right directory, run `scripts\\set_env_key.py`,
kill the console, start it again. That is four chances to be in the wrong folder,
and most of a week's troubleshooting was exactly that.

This module is the logic half. It has no FastAPI, no request objects and no
rendering, so every refusal below can be attacked directly in a test rather than
poked at through a browser — which matters more here than anywhere else in the
codebase, because these functions edit credentials and restart the process.

## What this is not allowed to do, and why each one is a rule

**Never send a secret value to the browser.** `entries()` reports that a key is
set and how long it is, never what it is. A dashboard that renders your
Anthropic key into the DOM has published it to every extension on the page and
every screenshot you ever take.

**Never write a key Apex does not read.** `config.py` is the list of settings
that exist. Writing `ANTHROPIC_KEY` (no `_API_`) into .env would succeed, look
correct in the UI, and do absolutely nothing — the single most common shape of
bug in this project, this time with a text box in front of it.

**Never restart without a supervisor.** Exiting the process is easy; coming back
is not. If nothing is watching, "Restart" is just "Quit" with a friendlier
label, and you would be left with a dead console and no dashboard to fix it
from. So the launcher advertises itself through `APEX_SUPERVISED`, and without
that flag this refuses and says what to do instead.

**Never pull onto changes you have not saved.** `git pull` onto a dirty tree
either fails or merges over local edits. Both are reported as four distinct
states rather than one boolean, the same way the barehands board is.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# The exit code that means "start me again". Chosen well clear of the codes a
# crash or a signal produces, so a supervisor can tell a requested restart from
# a process that fell over — restarting the latter in a loop is a fork bomb with
# extra steps.
EXIT_RESTART = 42

# Set by the launcher when something is watching the process and will start it
# again on EXIT_RESTART.
SUPERVISOR_ENV = "APEX_SUPERVISED"

# Substrings that mean a value must never leave the machine. Matched against the
# key, not the value: a heuristic on the value would classify a short password as
# safe. Erring toward masking is free; erring the other way publishes a key.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "SID",
                   "CREDENTIAL", "PRIVATE", "WEBHOOK_URL", "DSN")

# Keys that are real settings but must not be edited from a web page.
#
# DASHBOARD_TOKEN is the credential that authorizes this very request. Changing
# it from the dashboard logs you out of the dashboard mid-write, and if the new
# value has a typo there is no way back in except the machine itself. VAPID_
# PRIVATE_KEY cannot be regenerated without killing every push subscription that
# was ever granted.
_UNEDITABLE = {
    "DASHBOARD_TOKEN": "changing this from the dashboard would lock you out of "
                       "the dashboard. Edit .env on the machine.",
    "VAPID_PRIVATE_KEY": "regenerating this silently unsubscribes every device "
                         "that ever enabled push. Edit .env on the machine.",
}


def repo_root() -> Path:
    """The checkout Apex is running from — not the working directory.

    `git pull` run from wherever the console happened to be is how this week
    produced `fatal: 'origin' does not appear to be a git repository`. Anchoring
    on this file's location cannot be in the wrong folder.
    """
    return Path(__file__).resolve().parent.parent


def env_path() -> Path:
    return repo_root() / ".env"


def is_secret(key: str) -> bool:
    return any(m in key.upper() for m in _SECRET_MARKERS)


def known_keys() -> list[str]:
    """Every setting `config.py` actually reads.

    Derived from config's module namespace rather than a hand-kept list, because
    a hand-kept list drifts and then refuses a setting that genuinely exists.
    """
    import config
    out = []
    for name in dir(config):
        if name.startswith("_") or not name.isupper():
            continue
        if callable(getattr(config, name, None)):
            continue
        out.append(name)
    return sorted(out)


def _read_env_file() -> dict:
    """Raw key→value straight out of .env. Values never leave this module."""
    p = env_path()
    if not p.is_file():
        return {}
    out = {}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def describe_value(key: str, value: str) -> str:
    """What the browser is allowed to see about a value.

    A secret becomes its length and last four characters — enough to tell two
    keys apart and to confirm a paste landed, not enough to use.
    """
    if value is None or value == "":
        return ""
    if not is_secret(key):
        return value
    tail = value[-4:] if len(value) > 8 else ""
    return f"set · {len(value)} chars" + (f" · …{tail}" if tail else "")


def entries() -> list[dict]:
    """Every known setting, with masked values, ready for the dashboard."""
    import config
    raw = _read_env_file()
    out = []
    for key in known_keys():
        in_file = key in raw
        value = raw.get(key, "")
        live = getattr(config, key, None)
        # A setting can be live without being in .env — config.py has defaults.
        # Showing only the file would make every default look unset.
        if not in_file and live not in (None, "", [], {}):
            value = str(live)
        out.append({
            "key": key,
            "secret": is_secret(key),
            "set": bool(value),
            "display": describe_value(key, value),
            "in_env_file": in_file,
            "editable": key not in _UNEDITABLE,
            "locked_reason": _UNEDITABLE.get(key, ""),
        })
    return out


def set_setting(key: str, value: str) -> tuple[bool, str]:
    """Write one setting to .env. Returns (ok, message) — never raises.

    Refuses an unknown key. A .env line Apex never reads is indistinguishable
    from a working setting from the outside, and that is the bug this whole
    codebase keeps producing; a text box that can create one on demand is worse
    than no text box.
    """
    key = (key or "").strip()
    if not key:
        return False, "no setting named"
    if key in _UNEDITABLE:
        return False, _UNEDITABLE[key]
    if key not in known_keys():
        return False, (f"config.py does not read {key}, so writing it to .env "
                       f"would do nothing. Check the spelling.")
    sys.path.insert(0, str(repo_root() / "scripts"))
    try:
        import set_env_key
    except Exception as e:      # pragma: no cover - import guard
        return False, f"cannot load the .env writer: {e}"
    try:
        what = set_env_key.set_key(env_path(), key, str(value))
    except set_env_key.EnvWriteRefused as e:
        return False, str(e)
    except Exception as e:
        return False, f"could not write .env: {e}"
    return True, (f"{key} {what}. It takes effect when Apex restarts — nothing "
                  f"re-reads .env while running.")


# -- restart ---------------------------------------------------------------
def supervised() -> bool:
    return os.environ.get(SUPERVISOR_ENV, "").strip().lower() in ("1", "true", "yes")


def restart_status() -> dict:
    """Whether a restart would come back, and what to do if not."""
    if supervised():
        return {"ok": True,
                "detail": "the launcher is watching and will start Apex again."}
    return {"ok": False,
            "detail": ("nothing is supervising this process, so a restart would "
                       "just be a shutdown. Relaunch with Apex.bat (which sets "
                       f"{SUPERVISOR_ENV}) to enable this.")}


_restart_hook = None


def set_restart_hook(fn) -> None:
    """Let the host process decide how to die. Tests set this to observe."""
    global _restart_hook
    _restart_hook = fn


def request_restart(delay: float = 1.0) -> tuple[bool, str]:
    """Exit with EXIT_RESTART so the supervisor brings Apex back.

    The delay exists so the HTTP response reaches the browser before the process
    stops. Without it the dashboard shows a network error and you cannot tell a
    successful restart from a crash — which is the same confusion the board's
    1008 close code produced.
    """
    st = restart_status()
    if not st["ok"]:
        return False, st["detail"]

    hook = _restart_hook

    def _go():
        time.sleep(max(0.0, delay))
        if hook is not None:
            hook()
            return
        os._exit(EXIT_RESTART)

    threading.Thread(target=_go, daemon=True, name="ApexRestart").start()
    return True, "restarting — this page reconnects on its own in a few seconds."


# -- update ----------------------------------------------------------------
def _git(*args, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(repo_root()),
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} timed out"
    except Exception as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def update_status() -> dict:
    """Four states, reported as four things.

    A single "can update" boolean would collapse "you have unsaved work" into
    "no updates", and the first is a warning while the second is fine.
    """
    root = repo_root()
    if not (root / ".git").exists():
        return {"state": "not_a_repo", "detail": f"{root} is not a git checkout.",
                "can_update": False}
    rc, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        return {"state": "error", "detail": branch, "can_update": False}
    rc, dirty = _git("status", "--porcelain")
    if rc != 0:
        return {"state": "error", "detail": dirty, "can_update": False}
    if dirty:
        n = len([x for x in dirty.splitlines() if x.strip()])
        return {"state": "dirty", "branch": branch, "can_update": False,
                "detail": (f"{n} uncommitted change(s) here. Pulling could "
                           f"overwrite them, so this refuses until they are "
                           f"committed or discarded."),
                "changes": dirty.splitlines()[:20]}
    return {"state": "ready", "branch": branch, "can_update": True,
            "detail": f"on {branch}, working tree clean."}


def do_update() -> dict:
    """Pull, and say honestly whether anything arrived.

    "Updated" printed after a pull that fetched nothing is the same lie as a
    board command that returns 204 with no stage open.
    """
    st = update_status()
    if not st.get("can_update"):
        return {"ok": False, "detail": st["detail"], "state": st["state"]}
    before_rc, before = _git("rev-parse", "HEAD")
    rc, out = _git("pull", "origin", st["branch"], timeout=180)
    if rc != 0:
        return {"ok": False, "state": "failed", "detail": out}
    after_rc, after = _git("rev-parse", "HEAD")
    if before_rc == 0 and after_rc == 0 and before == after:
        return {"ok": True, "state": "already_current", "changed": False,
                "detail": "already up to date — nothing to restart for.",
                "output": out}
    rc2, log = _git("log", "--oneline", f"{before}..{after}")
    commits = [l for l in log.splitlines() if l.strip()] if rc2 == 0 else []
    return {"ok": True, "state": "updated", "changed": True,
            "commits": commits[:20], "count": len(commits),
            "detail": f"pulled {len(commits)} commit(s). Restart to run them.",
            "output": out}
