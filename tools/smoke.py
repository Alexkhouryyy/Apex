"""Boot Apex for real and check that its answers are true.

Every bug found on the day this was written was found by starting Apex and
reading what came out — not by 977 unit tests, and not by the wiring, autonomy
or SQL audits. Those audits hunt code that *cannot* run. This hunts code that
runs and is wrong, which is the class the user actually experiences:

    "It doesn't know the date."

Nothing raised. Nothing logged. The model answered with a confident, plausible,
wrong date. No static check can see that, because the defect was in what the
prompt did not contain.

## What is and is not under test

A scripted Messages API stands in for the model, so this needs no API key, costs
nothing, and never flakes on model wording. That server is an **instrument, not
the subject**. Every assertion is on Apex's side of the boundary:

  * what Apex *sends* — is today's date in the system prompt? are the tools
    offered? is the cache breakpoint still before the volatile block?
  * what Apex *does* with the reply — did the row get inserted, the file get
    written, the cost get recorded, the dashboard answer?

Asserting on the model's intelligence would test Anthropic. Asserting on the
request and the side effects tests Apex.

## The generic detector

`no_silent_failures` is the most valuable check here and the one that
generalises. Apex fails open by design: subsystems catch their own exceptions,
print a line, and carry on. That is good for uptime and terrible for truth —
"never wired up" and "working perfectly" look identical from outside. Scanning
the boot log for failure markers turns every swallowed exception into a test
failure. It would have caught the profile digest bug the day it was written,
years before a human happened to read the line.

Run directly for a report:  python -m tools.smoke
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Lines Apex prints when a subsystem quietly gave up. Fail-open means these are
# the *only* evidence that something is broken.
FAILURE_MARKERS = (
    "no such column", "no such table", "Traceback (most recent call last)",
    "failed:", "Failed:", " failed —", "OperationalError", "AttributeError",
    "NameError", "TypeError:", "KeyError:", "ImportError",
)

# Known-acceptable noise: things that legitimately cannot work in a test
# environment with no keys, no display and no network. Kept explicit and short —
# every entry is a hole in the detector, so each says why it is allowed.
ALLOWED_NOISE = (
    "Embedding model unavailable",      # sentence-transformers not installed
    "Profile digest loop skipped",      # needs a real client
    "MCP",                              # no MCP servers configured
    "playwright",                       # no browser in CI
    "DISPLAY",                          # headless
)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── The scripted model ────────────────────────────────────────────────────────

class _ModelServer:
    """Serves the Anthropic Messages API and records every request verbatim.

    The recording is the point: assertions read `requests` to see exactly what
    Apex put on the wire.
    """

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.requests: list[dict] = []
        self.port = free_port()
        self._httpd = None
        self._thread = None

    def _next_response(self, body: dict) -> dict:
        # Apex makes background calls (world model, reflection) alongside the
        # conversation. Only the agent loop is offered tools, so that is the
        # discriminator — without it the script would desync unpredictably.
        is_agent_turn = bool(body.get("tools"))
        if not is_agent_turn:
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}

        used_tool = any(
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in m["content"])
            for m in body.get("messages", []))
        step = 1 if used_tool else 0
        if step < len(self.script):
            return self.script[step]
        return {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}

    def start(self):
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or "{}")
                server.requests.append(body)
                r = server._next_response(body)
                out = json.dumps({
                    "id": "msg_smoke", "type": "message", "role": "assistant",
                    "model": body.get("model", "claude-opus-5"),
                    "content": r["content"], "stop_reason": r["stop_reason"],
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1000, "output_tokens": 200},
                }).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._httpd = HTTPServer(("127.0.0.1", self.port), H)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()

    # -- helpers the checks use ------------------------------------------------

    def agent_requests(self) -> list[dict]:
        return [r for r in self.requests if r.get("tools")]

    def system_text(self, req: dict) -> str:
        sys_blocks = req.get("system") or []
        if isinstance(sys_blocks, str):
            return sys_blocks
        return "\n".join(b.get("text", "") for b in sys_blocks)


# ── One boot ──────────────────────────────────────────────────────────────────

@dataclass
class BootResult:
    stdout: str
    db_path: Path
    home: Path
    dashboard_port: int
    server: _ModelServer
    returncode: int | None
    log_bytes: int
    dashboard_status: int | None = None
    # Collected while the process is alive — see boot(). Checks run after it has
    # been terminated, so anything needing a live server must be captured here.
    dashboard_sweep: list = field(default_factory=list)
    # How long the scripted turn took to land its first tool_events row. Not
    # asserted against a threshold — reported, so a creeping slowdown shows up
    # in the check detail instead of being absorbed by the deadline.
    turn_seconds: float = 0.0

    def rows(self, table: str, where: str = "") -> list[tuple]:
        try:
            with sqlite3.connect(self.db_path) as c:
                q = f'SELECT * FROM "{table}"' + (f" WHERE {where}" if where else "")
                return c.execute(q).fetchall()
        except Exception:
            return []


def boot(say: str = "", script: list[dict] | None = None,
         timeout: float = 75.0, extra_env: dict | None = None,
         args: tuple[str, ...] = ("--think",),
         turn_timeout: float = 45.0) -> BootResult:
    """Start main.py --text against the scripted model, say one thing, stop.

    `--think` by default, deliberately. Without it `use_thinking` is False and
    the thinking parameter is never built, so the check for removed parameters
    passed while a hardcoded budget_tokens sat in the code — a green result over
    a code path that never executed.
    """
    import os

    work = Path(tempfile.mkdtemp(prefix="apex_smoke_"))
    server = _ModelServer(script or [
        {"content": [{"type": "text", "text": "Hello."}], "stop_reason": "end_turn"},
    ]).start()

    port = free_port()
    env = dict(os.environ)
    # HOME is redirected below so Apex writes its vault and notes into the temp
    # tree instead of the real one. That also moves where Python looks for
    # per-user site-packages, which silently hid `requests` and killed the boot
    # before it started. Pin the real one onto PYTHONPATH first.
    import site
    user_site = site.getusersitepackages()
    if isinstance(user_site, str) and Path(user_site).is_dir():
        env["PYTHONPATH"] = os.pathsep.join(
            [user_site] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))

    env.update({
        "DB_PATH": str(work / "apex.db"),
        "HOME": str(work),
        "USERPROFILE": str(work),          # Windows equivalent of HOME
        "ANTHROPIC_API_KEY": "sk-ant-smoke",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.port}",
        "DASHBOARD_PORT": str(port),
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_TOKEN": "smoke-token",
        "TELEGRAM_POLLING": "false",
        "PYTHONUNBUFFERED": "1",
        # Hand tracking ON, on a machine with no camera (true of wherever this
        # suite runs in CI). Booting the ENABLED path is the point: the disabled
        # path does nothing by definition, so exercising it proves nothing. This
        # is how we learn that Apex still boots cleanly when hand tracking is
        # configured but has no camera to open — the normal state of a machine
        # that has none, or has one in use by something else.
        # no_silent_failures polices the "cleanly" half.
        "HANDTRACK_ENABLED": "true",
    })
    env.update(extra_env or {})

    proc = subprocess.Popen(
        [sys.executable, "-u", "main.py", "--text", *args],
        cwd=str(REPO), env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    dashboard_status = None
    sweep: list = []
    turn_seconds = 0.0
    try:
        if say:
            proc.stdin.write(say + "\n")
        proc.stdin.flush()
        proc.stdin.close()          # EOF -> Apex parks headless, keeps serving
        # communicate() flushes stdin, which now raises on a closed handle.
        proc.stdin = None

        # Wait for the dashboard to answer, which also means boot finished.
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                import urllib.request
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/", timeout=2) as r:
                    dashboard_status = r.status
                    break
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(0.5)

        # Wait for the turn to FINISH, rather than guessing how long a turn
        # takes on this machine.
        #
        # This was `time.sleep(6)`. Six seconds is enough here and was not
        # enough on a GitHub runner, so `tool_calls_take_effect` failed
        # intermittently — five red runs in a row at one point — for a reason
        # that had nothing to do with the code under test. A hard gate that
        # fails on machine speed teaches everyone to stop reading it, which is
        # worse than not having it.
        #
        # The scripted turn calls `remember`, so the turn is over when the row
        # it writes appears. Polling for exactly what the check then asserts is
        # deliberate and is not self-fulfilling: on timeout the row is still
        # absent and the check still fails. What it removes is only the
        # question "was six seconds enough", which was never the interesting
        # one. `turn_seconds` is reported so a real slowdown stays visible
        # instead of being absorbed by a generous deadline.
        turn_start = time.time()
        turn_deadline = turn_start + turn_timeout
        while time.time() < turn_deadline:
            if proc.poll() is not None:
                break
            with sqlite3.connect(str(work / "apex.db")) as c:
                try:
                    if c.execute("SELECT 1 FROM tool_events LIMIT 1").fetchone():
                        break
                except sqlite3.Error:
                    pass         # table not created yet — boot is still going
            time.sleep(0.25)
        turn_seconds = time.time() - turn_start
        # A beat for the writes the tool triggers downstream (embedding, the
        # awareness log) to land after the row that signalled us.
        time.sleep(1.0)

        # Sweep the dashboard while it is still serving. Doing this in a check
        # instead gave 61/61 "connection refused" — the checks run after the
        # process is gone.
        if dashboard_status == 200:
            sweep = _sweep_dashboard_live(port)
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=20)[0] or ""
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0] or ""
        server.stop()

    return BootResult(
        stdout=out, db_path=work / "apex.db", home=work, dashboard_port=port,
        server=server, returncode=proc.returncode, log_bytes=len(out),
        dashboard_status=dashboard_status, dashboard_sweep=sweep,
        turn_seconds=turn_seconds,
    )


# ── Checks ────────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    check: str
    ok: bool
    detail: str = ""


CHECKS: list = []


def check(fn):
    CHECKS.append(fn)
    return fn


@check
def boot_completes(r: BootResult) -> Finding:
    ok = "Session #" in r.stdout and "Awareness] Monitor started" in r.stdout
    return Finding("boot_completes", ok,
                   "" if ok else "startup did not reach the awareness monitor")


@check
def no_silent_failures(r: BootResult) -> Finding:
    """The generic detector — see the module docstring.

    Apex swallows its own exceptions and prints a line. That line is the only
    evidence anything is wrong, so it has to be a test failure rather than log
    noise a person may or may not read.
    """
    hits = []
    for line in r.stdout.splitlines():
        if any(m in line for m in FAILURE_MARKERS) and \
           not any(n in line for n in ALLOWED_NOISE):
            hits.append(line.strip())
    return Finding("no_silent_failures", not hits,
                   "; ".join(hits[:5]) + (f" (+{len(hits)-5} more)" if len(hits) > 5 else ""))


@check
def dashboard_serves(r: BootResult) -> Finding:
    ok = r.dashboard_status == 200
    return Finding("dashboard_serves", ok,
                   "" if ok else f"GET / returned {r.dashboard_status}")


@check
def prompt_carries_todays_date(r: BootResult) -> Finding:
    """The bug that motivated this suite."""
    from datetime import datetime
    reqs = r.server.agent_requests()
    if not reqs:
        return Finding("prompt_carries_todays_date", False, "no agent turn was sent")
    text = r.server.system_text(reqs[0])
    now = datetime.now()
    want = [str(now.year), now.strftime("%B"), str(now.day)]
    missing = [w for w in want if w not in text]
    return Finding("prompt_carries_todays_date", not missing,
                   f"system prompt is missing {missing}" if missing else "")


@check
def volatile_block_is_after_the_cache_breakpoint(r: BootResult) -> Finding:
    """A per-turn value before the breakpoint busts the prompt cache on every
    request — a silent, permanent cost increase."""
    reqs = r.server.agent_requests()
    if not reqs:
        return Finding("volatile_block_is_after_the_cache_breakpoint", False, "no agent turn")
    blocks = reqs[0].get("system") or []
    if not isinstance(blocks, list):
        return Finding("volatile_block_is_after_the_cache_breakpoint", False,
                       "system prompt is not a block list, so it cannot be cached")
    last_cached = max((i for i, b in enumerate(blocks) if b.get("cache_control")),
                      default=-1)
    time_idx = next((i for i, b in enumerate(blocks)
                     if "CURRENT DATE AND TIME" in b.get("text", "")), None)
    if time_idx is None:
        return Finding("volatile_block_is_after_the_cache_breakpoint", False,
                       "no time block found")
    ok = time_idx > last_cached
    return Finding("volatile_block_is_after_the_cache_breakpoint", ok,
                   "" if ok else f"time block at {time_idx}, breakpoint at {last_cached}")


@check
def tools_are_offered(r: BootResult) -> Finding:
    reqs = r.server.agent_requests()
    if not reqs:
        return Finding("tools_are_offered", False, "no agent turn")
    names = {t.get("name") for t in reqs[0].get("tools", [])}
    # Names read from agent/core.py, not recalled. The first version of this
    # check asserted "run_shell" — which does not exist; the tool is "bash" —
    # and so reported a bug in Apex that was a bug in the test.
    required = {"remember", "recall", "bash", "research", "current_time"}
    missing = required - names
    return Finding("tools_are_offered", not missing,
                   f"missing {sorted(missing)} (of {len(names)} offered)"
                   if missing else f"{len(names)} tools offered")


@check
def the_model_is_current(r: BootResult) -> Finding:
    reqs = r.server.agent_requests()
    if not reqs:
        return Finding("the_model_is_current", False, "no agent turn")
    model = reqs[0].get("model", "")
    ok = model.startswith("claude-opus-5") or model.startswith("claude-")
    return Finding("the_model_is_current", ok, f"model={model}")


@check
def no_removed_parameters_are_sent(r: BootResult) -> Finding:
    """budget_tokens and temperature are 400s on Claude 4.7+."""
    bad = []
    for req in r.server.agent_requests():
        if isinstance(req.get("thinking"), dict) and "budget_tokens" in req["thinking"]:
            bad.append("thinking.budget_tokens")
        if "temperature" in req and str(req.get("model", "")).startswith("claude-opus-5"):
            bad.append("temperature")
    return Finding("no_removed_parameters_are_sent", not bad, ", ".join(sorted(set(bad))))


@check
def tool_calls_take_effect(r: BootResult) -> Finding:
    """A tool the model calls must change the world, not just return a string."""
    mem = r.rows("memories")
    events = r.rows("tool_events")
    ok = bool(mem) and bool(events)
    return Finding("tool_calls_take_effect", ok,
                   f"memories={len(mem)} tool_events={len(events)} "
                   f"in {r.turn_seconds:.1f}s")


@check
def spend_is_recorded(r: BootResult) -> Finding:
    """An unrecorded cost means the budget cap is not protecting anything."""
    try:
        with sqlite3.connect(r.db_path) as c:
            rows = c.execute(
                "SELECT model, cost_usd FROM usage_log").fetchall()
    except Exception as e:
        return Finding("spend_is_recorded", False, f"usage_log unreadable: {e}")
    if not rows:
        return Finding("spend_is_recorded", False, "no usage rows for a turn that ran")
    priced = [m for m, c in rows if c and c > 0]
    return Finding("spend_is_recorded", bool(priced),
                   f"{len(rows)} calls, {len(priced)} with a non-zero cost")


@check
def headless_stdin_does_not_spin(r: BootResult) -> Finding:
    """This path once wrote 129 MB in 90 seconds at 101% CPU.

    Counts prompts rather than bytes. The first version of this check measured
    log size and *missed the bug it was written for*: after terminate(),
    communicate() returns only what fits the 64KB pipe buffer, so a runaway loop
    and a healthy run both reported ~65KB. The prompt count is unaffected by
    buffering — a healthy boot emits one or two, the spin emitted 12,803.
    """
    prompts = r.stdout.count("YOU:")
    ok = prompts <= 5
    return Finding("headless_stdin_does_not_spin", ok,
                   f"{prompts} input prompts ({r.log_bytes} bytes captured)")


@check
def core_tables_exist(r: BootResult) -> Finding:
    """Every init_db must actually have run — the shape of the restraint bug,
    where a table was created by nothing and the feature silently did nothing."""
    # One table per subsystem that has its own init_db, so a missing init call
    # shows up here. Names verified against the CREATE TABLE statements.
    want = {"memories", "turn_log", "usage_log", "tool_events", "entities",
            "goals", "reflections", "chat_threads", "budget_config",
            "interruptions", "held_notifications", "staged_writes"}
    try:
        with sqlite3.connect(r.db_path) as c:
            have = {t for (t,) in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception as e:
        return Finding("core_tables_exist", False, str(e))
    missing = want - have
    return Finding("core_tables_exist", not missing,
                   f"missing {sorted(missing)}" if missing else f"{len(have)} tables")


# ── The dashboard tabs ────────────────────────────────────────────────────────
# 26 tabs backed by ~66 GET endpoints. A tab whose endpoint 500s renders as an
# empty panel, which is indistinguishable from "you have no goals yet" — the
# same fail-open ambiguity as everywhere else in Apex, but in the surface the
# user looks at most.

# Routes deliberately not swept, each with the reason. Skipping without saying
# so would be the same lie as a green check on an unexercised path.
SKIP_ROUTES = {
    "/": "the SPA shell, not JSON",
    "/sw.js": "static asset",
    "/manifest.webmanifest": "static asset",
    "/api/events": "server-sent events — would hang a plain GET",
    "/api/camera/frame": "binary JPEG, and there is no camera here",
    "/api/pair/qr": "binary PNG",
}

# Path parameters that make a route callable. A value that is *expected* to be
# absent is fine — 404 is a correct answer and is treated as such below.
# An optional integration reporting that it is not set up is the *correct*
# answer, not a defect — Apex ships email, calendar, Signal, Twilio and more that
# most installs never configure. Distinguishing this from a real fault is the
# whole difficulty of sweeping a dashboard: "Email not configured" is healthy,
# "no such column: properties" is not. Counted and reported either way, because
# an unconfigured endpoint is coverage this sweep did not achieve.
NOT_CONFIGURED = (
    "not configured", "not set", "no credentials", "not enabled",
    "disabled", "unavailable", "no api key", "missing",
)

ROUTE_PARAMS = {
    "thread_id": "1", "goal_id": "1", "doc_id": "1",
    "session_id": "1", "uid": "1", "token_id": "1",
}


def dashboard_get_routes() -> tuple[list[str], list[str]]:
    """(callable routes, skipped routes) parsed from the server source."""
    src = (REPO / "dashboard" / "server.py").read_text(errors="replace")
    routes = sorted(set(re.findall(r'@app\.get\("([^"]+)"', src)))
    callable_routes, skipped = [], []
    for route in routes:
        if route in SKIP_ROUTES:
            skipped.append(route)
            continue
        filled, ok = route, True
        for name in re.findall(r"\{(\w+)\}", route):
            if name in ROUTE_PARAMS:
                filled = filled.replace("{" + name + "}", ROUTE_PARAMS[name])
            else:
                ok = False
        (callable_routes if ok else skipped).append(filled if ok else route)
    return callable_routes, skipped


def _sweep_dashboard_live(port: int, timeout: float = 8.0) -> list[dict]:
    """GET every dashboard endpoint. Must run while Apex is still up."""
    import urllib.error
    import urllib.request

    routes, _ = dashboard_get_routes()
    out = []
    for route in routes:
        url = f"http://127.0.0.1:{port}{route}"
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer smoke-token",
            "Accept": "application/json",
        })
        entry = {"route": route}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                entry["status"] = resp.status
                entry["body"] = resp.read(20000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            entry["status"] = e.code
            entry["body"] = e.read(4000).decode("utf-8", "replace")
        except Exception as e:
            entry["status"] = None
            entry["body"] = f"{type(e).__name__}: {e}"
        out.append(entry)
    return out


@check
def every_dashboard_tab_answers(r: BootResult) -> Finding:
    """No endpoint may 500, hang, or return an error payload.

    A fresh database legitimately has no goals, memories or reflections, so
    *empty* is a pass. What is never acceptable is the server failing to
    answer — that is a broken tab wearing an empty tab's clothes.
    """
    results = r.dashboard_sweep
    if not results:
        return Finding("every_dashboard_tab_answers", False,
                       "no sweep recorded — the dashboard never came up")
    broken, unconfigured = [], []
    for e in results:
        status, body = e.get("status"), e.get("body", "")
        if status is None:
            broken.append(f"{e['route']} -> {body[:80]}")
        elif status == 404:
            continue                      # a made-up id is legitimately absent
        elif status >= 500:
            broken.append(f"{e['route']} -> HTTP {status}")
        elif status == 401:
            broken.append(f"{e['route']} -> 401 (token rejected)")
        else:
            # 200 with an error payload is the fail-open shape: the tab renders,
            # the panel is empty, and the reason is buried in the JSON.
            try:
                data = json.loads(body)
            except Exception:
                continue
            if isinstance(data, dict) and data.get("error"):
                msg = str(data["error"])
                if any(m in msg.lower() for m in NOT_CONFIGURED):
                    unconfigured.append(f"{e['route']}: {msg[:50]}")
                else:
                    broken.append(f"{e['route']} -> error: {msg[:70]}")
    detail = (f"{len(results) - len(broken) - len(unconfigured)} ok, "
              f"{len(unconfigured)} unconfigured, {len(broken)} broken")
    if broken:
        detail += " — " + "; ".join(broken[:6])
        if len(broken) > 6:
            detail += f" (+{len(broken) - 6} more)"
    return Finding("every_dashboard_tab_answers", not broken, detail)


@check
def hand_tracking_fails_loudly_with_no_camera(r: BootResult) -> Finding:
    """HANDTRACK_ENABLED is on in the smoke env, on a machine with no camera —
    true of wherever this suite runs, this container included.

    Two things have to be true and neither is obvious. The tracker must
    actually attempt to open the camera (a subsystem gated behind a flag is
    exactly the shape that gets built and never constructed); and when that
    fails, it must NAME the condition rather than going quiet, because "no
    camera" and "hand tracking is silently broken" are otherwise the same
    silence. It must also not trip a failure marker, which no_silent_failures
    checks independently.
    """
    attempted = "[HandTrack] Watching camera" in r.stdout
    named = "would not open" in r.stdout or "Hand tracking is off" in r.stdout
    if attempted and named:
        return Finding("hand_tracking_fails_loudly_with_no_camera", True,
                       "tracker started, absent camera reported")
    missing = []
    if not attempted:
        missing.append("the tracker never started")
    if not named:
        missing.append("nothing said the camera was unavailable")
    return Finding("hand_tracking_fails_loudly_with_no_camera", False, "; ".join(missing))


def run(say: str = "remember that my name is Alex", verbose: bool = True) -> list[Finding]:
    script = [
        {"content": [
            {"type": "text", "text": "Noting that."},
            {"type": "tool_use", "id": "t1", "name": "remember",
             "input": {"content": "User is Alex", "kind": "fact", "importance": 9}},
         ], "stop_reason": "tool_use"},
        {"content": [{"type": "text", "text": "Saved."}], "stop_reason": "end_turn"},
    ]
    r = boot(say=say, script=script)
    findings = []
    for fn in CHECKS:
        try:
            findings.append(fn(r))
        except Exception as e:
            findings.append(Finding(fn.__name__, False, f"check itself raised: {e}"))
    if verbose:
        report(findings, r)
    return findings


def report(findings: list[Finding], r: BootResult | None = None) -> None:
    bad = [f for f in findings if not f.ok]
    print(f"\n[Smoke] {len(findings) - len(bad)}/{len(findings)} checks passed")
    for f in findings:
        mark = "✓" if f.ok else "✗"
        print(f"  {mark} {f.check}" + (f" — {f.detail}" if f.detail else ""))
    if r is not None and bad:
        print("\n--- boot output (last 40 lines) ---")
        print("\n".join(r.stdout.splitlines()[-40:]))


if __name__ == "__main__":
    results = run()
    sys.exit(1 if any(not f.ok for f in results) else 0)
