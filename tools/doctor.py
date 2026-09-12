"""What is actually wrong with THIS Apex, on THIS machine, right now.

    python -m tools.doctor

Every check answers one question a person actually has, in the order that
matters: can it think, can it remember, can it see, can it reach the things you
connected. Each failure says what to run next.

## Why this exists

Apex has 2000 tests, a boot smoke check and three static audits, and all of them
run where the code is DEVELOPED. None of them ran on the laptop, so every
failure there was found one at a time, by a person, in the order they happened
to trip over them: a missing numpy during setup, a credit balance that made
every feature look dead, a relay that never started because a key was never
written.

None of those are hard problems. They were only invisible. This is the check
that makes the state of a real installation visible in one command, and it is
deliberately the only tool here that is written for the person running Apex
rather than for the person building it.

## The rule it follows

A check may only report what it OBSERVED. "Configured" is never reported as
"working" — the distinction this whole codebase keeps rediscovering. So the
model check makes a real (tiny) API call, the camera check asks the tracker
rather than the config, and the relay check tries the URL.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, WARN, BAD = "ok", "warn", "bad"
_MARK = {OK: "[ ok ]", WARN: "[warn]", BAD: "[FAIL]"}

CHECKS = []


def check(order: int, title: str):
    def deco(fn):
        CHECKS.append((order, title, fn))
        return fn
    return deco


# ── 1. Can it think? ─────────────────────────────────────────────────────────

@check(10, "Can Apex reach a model?")
def _model():
    """A real call, not a config read. This is the check that would have said
    'your credit balance is too low' in one line instead of after a full boot
    and a failed conversation."""
    import config
    if getattr(config, "SUBSCRIPTION_ENABLED", False):
        try:
            from agent import subscription
            ok, why = subscription.available()
            if ok:
                return OK, "using your Claude subscription (SUBSCRIPTION_ENABLED=true)"
            return BAD, (f"SUBSCRIPTION_ENABLED=true but {why}\n"
                         f"      -> install the `claude` CLI and log in, or set "
                         f"SUBSCRIPTION_ENABLED=false to use API credits")
        except Exception as e:
            return WARN, f"could not check the subscription path: {e}"

    model = getattr(config, "AGENT_MODEL", "claude-opus-5")
    from agent import provider
    p = provider.provider_for(model)
    if p != "anthropic":
        # A non-Anthropic default means the credit balance check below is the
        # wrong question entirely.
        key_name = provider.PROVIDER_KEY_NAMES.get(p, "")
        if key_name and not getattr(config, key_name, ""):
            return BAD, (f"AGENT_MODEL is {model} ({p}), but {key_name} is not "
                         f"set.\n"
                         f"      -> python scripts/set_env_key.py {key_name} <your key>")
        return OK, (f"configured for {model} via {p}. Ask Apex something to "
                    f"confirm it answers — this check does not spend a token on "
                    f"a paid provider it has not been told to test.")

    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return BAD, ("ANTHROPIC_API_KEY is not set.\n"
                     "      -> python scripts/set_env_key.py ANTHROPIC_API_KEY sk-ant-...")
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=key)
        c.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1,
                          messages=[{"role": "user", "content": "hi"}])
    except Exception as e:
        msg = str(e)
        if "credit balance" in msg.lower():
            return BAD, ("your Anthropic credit balance is too low, so Apex "
                         "cannot answer anything at all.\n"
                         "      -> add credits at console.anthropic.com, OR run:\n"
                         "         python scripts/set_env_key.py SUBSCRIPTION_ENABLED true\n"
                         "         (routes the conversation through your Claude "
                         "subscription instead)")
        if "authentication" in msg.lower() or "401" in msg:
            return BAD, "ANTHROPIC_API_KEY is set but was rejected."
        return BAD, f"the API call failed: {msg[:160]}"
    return OK, "a real API call succeeded"


# ── 2. Can it remember? ──────────────────────────────────────────────────────

@check(20, "Is the memory database healthy?")
def _memory():
    import sqlite3
    from agent import longterm
    p = Path(longterm.DB_PATH)
    if not p.exists():
        return WARN, (f"no database yet at {p}.\n"
                      f"      -> start Apex once; it is created on first boot")
    try:
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
            integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            memories = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    except Exception as e:
        if "no such table" in str(e):
            return WARN, (f"{p} exists but has no tables yet.\n"
                          f"      -> start Apex once; schema.init_all() creates them")
        return BAD, f"cannot read {p}: {e}"
    if integrity != "ok":
        return BAD, f"integrity check failed: {integrity}"
    return OK, (f"{len(tables)} tables, {memories} memories, "
                f"{p.stat().st_size // 1024} KB")


@check(21, "Do all the tables exist?")
def _tables():
    """The drift that had resident mode missing twelve modules' tables."""
    import sqlite3
    from agent import longterm
    expect = {"memories", "board_cards", "outcomes", "lessons", "mcp_audit",
              "scheduled_tasks", "council_runs", "devices", "relay_applied",
              "node_tasks", "device_capabilities", "vault_index"}
    p = Path(longterm.DB_PATH)
    if not p.exists():
        return WARN, ("no database yet.\n"
                      "      -> start Apex once; schema.init_all() creates it")
    with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
        have = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = sorted(expect - have)
    if missing:
        return BAD, (f"missing: {', '.join(missing)}\n"
                     f"      -> start Apex once; schema.init_all() creates them")
    return OK, "every core table is present"


# ── 3. Can it see? ───────────────────────────────────────────────────────────

@check(30, "Hand tracking and the camera")
def _camera():
    import config
    if not getattr(config, "HANDTRACK_ENABLED", False):
        return WARN, "HANDTRACK_ENABLED=false — hand tracking is off"
    from agent import handtrack
    conflict = handtrack.opencv_conflict()
    if conflict:
        return BAD, (f"two OpenCV packages are fighting: {', '.join(conflict)}\n"
                     f"      -> pip uninstall -y opencv-python-headless")
    ok, why = handtrack.available()
    if not ok:
        return BAD, why
    from agent import capabilities
    cam = capabilities.of(capabilities.this_node()).get("camera", {})
    if cam.get("state") == capabilities.YES:
        return OK, cam.get("detail", "camera usable")
    if cam.get("state") == capabilities.NO:
        return BAD, cam.get("detail", "no camera")
    return WARN, ("the software is fine but nothing has opened the device yet.\n"
                  "      -> start Apex and look for '[HandTrack] Camera open'")


@check(31, "Is the pinch threshold measured or guessed?")
def _pinch():
    import config
    r = getattr(config, "HANDTRACK_PINCH_RATIO", 0.70)
    if abs(r - 0.45) < 1e-9:
        return BAD, ("0.45 is the old guess, and it sits BELOW the top of a real "
                     "pinch range — pinches would silently not register.\n"
                     "      -> python scripts/calibrate_pinch.py")
    return OK, (f"HANDTRACK_PINCH_RATIO={r}. If pinch feels unreliable, measure "
                f"yours: python scripts/calibrate_pinch.py")


# ── 4. Can it reach what you connected? ──────────────────────────────────────

@check(40, "MCP servers")
def _mcp():
    from agent import mcp_catalog
    have = mcp_catalog.installed()
    if not have:
        return WARN, ("no MCP servers configured. mcp_servers.json ships with "
                      "`_example_` entries only, and those are skipped.\n"
                      "      -> dashboard, Control tab, 'Add a server'")
    from agent import mcp_policy
    off = mcp_policy.servers_off()
    line = f"{len(have)} configured: {', '.join(have)}"
    if off:
        line += f" (switched off: {', '.join(off)})"
    return OK, line


@check(41, "The relay")
def _relay():
    import config
    from agent import relay
    if not getattr(config, "RELAY_ENABLED", False):
        return WARN, "RELAY_ENABLED=false — nothing is sent anywhere"
    if not getattr(config, "RELAY_URL", ""):
        return BAD, "RELAY_ENABLED=true but RELAY_URL is empty"
    if not getattr(config, "RELAY_KEY", ""):
        return BAD, ("RELAY_KEY is not set, so nothing can be sealed and nothing "
                     "is sent.\n"
                     "      -> python -m agent.relay --new-key\n"
                     "      -> python scripts/set_env_key.py RELAY_KEY <paste it>")
    try:
        relay._fernet()
    except Exception as e:
        return BAD, str(e).split("\n")[0]
    try:
        import urllib.request
        with urllib.request.urlopen(f"{config.RELAY_URL.rstrip('/')}/health",
                                    timeout=5) as r:
            r.read()
    except Exception as e:
        return BAD, (f"cannot reach {config.RELAY_URL}: {type(e).__name__}\n"
                     f"      -> is the relay running? relay\\start-relay.bat")
    return OK, f"{config.RELAY_URL} is reachable and the key is valid"


@check(42, "The dashboard")
def _dashboard():
    import config
    if not getattr(config, "DASHBOARD_TOKEN", ""):
        return BAD, ("DASHBOARD_TOKEN is empty, which turns OFF authentication "
                     "for every dashboard request.\n"
                     "      -> python scripts/set_env_key.py DASHBOARD_TOKEN <a password>")
    return OK, (f"protected by a token, on port "
                f"{getattr(config, 'DASHBOARD_PORT', 7860)}")


@check(50, "Python dependencies")
def _deps():
    missing = []
    for mod, why in (("numpy", "memory embeddings"), ("fastapi", "the dashboard"),
                     ("anthropic", "talking to a model"), ("cryptography", "the relay"),
                     ("mcp", "MCP servers"), ("cv2", "the camera"),
                     ("mediapipe", "hand tracking")):
        try:
            __import__(mod)
        except Exception:
            missing.append(f"{mod} ({why})")
    if missing:
        return WARN, ("not installed: " + "; ".join(missing) +
                      "\n      -> pip install -r requirements.txt")
    return OK, "everything Apex needs is installed"


def main() -> int:
    print("\n  Apex doctor — what is actually working on this machine\n")
    worst = OK
    for _, title, fn in sorted(CHECKS):
        try:
            state, detail = fn()
        except Exception as e:
            state, detail = BAD, f"the check itself failed: {type(e).__name__}: {e}"
        print(f"  {_MARK[state]} {title}")
        for line in str(detail).split("\n"):
            print(f"         {line}")
        print()
        if state == BAD:
            worst = BAD
        elif state == WARN and worst == OK:
            worst = WARN

    if worst == BAD:
        print("  Something above is stopping Apex from working. Fix the [FAIL]"
              " lines first —\n  each one says what to run.\n")
    elif worst == WARN:
        print("  Nothing is broken. The [warn] lines are things that are simply"
              " not set up yet.\n")
    else:
        print("  Everything checked out.\n")
    return 1 if worst == BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
