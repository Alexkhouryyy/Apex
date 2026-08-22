"""Skill Forge — Apex writes its own tools when it hits a capability gap.

When the agent can't do something, skill_forge.attempt_forge(client, gap) asks
Claude to write a Python tool, validates it in a Docker sandbox, and stages it
for user approval. **Every** forged tool stages — there is no auto-register path.
The model-declared `is_read_only` flag is deliberately not trusted, because it
was never checked against the code, so an injected "is_read_only": true would
otherwise ship host-executing tools unattended.

(An earlier version of this docstring described auto-registering read-only
tools. That path was removed; the text outlived it.)

Approved tools are registered via agent/self_mod.register_new_tool() and persist
across restarts in ~/.voice_agent_overlay.json.
"""
import json
import subprocess
import sys
import textwrap
import time
from typing import Optional, Callable

import config
from agent import longterm, telemetry

_NOTIFY_FN: Optional[Callable] = None


def set_notify_fn(fn: Callable) -> None:
    global _NOTIFY_FN
    _NOTIFY_FN = fn


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS forged_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL,
                code TEXT NOT NULL,
                test_case TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                approved_at REAL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_forged_status ON forged_tools(status, created_at DESC)"
        )
        # Networked skills (internet/external services) carry needs_network=1 and are
        # always staged for one-time user approval. Added via migration for old DBs.
        try:
            c.execute("ALTER TABLE forged_tools ADD COLUMN needs_network INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists


def list_forged(status: Optional[str] = None) -> list[dict]:
    cols = "id, name, description, status, created_at, COALESCE(needs_network, 0)"
    try:
        with longterm._conn() as c:
            if status:
                rows = c.execute(
                    f"SELECT {cols} FROM forged_tools "
                    "WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = c.execute(
                    f"SELECT {cols} FROM forged_tools ORDER BY created_at DESC"
                ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "name": r[1], "description": r[2], "status": r[3],
         "created_at": r[4], "needs_network": bool(r[5])}
        for r in rows
    ]


def approve_forged(tool_id: int) -> str:
    """Register an approved forged tool via self_mod. Returns status message."""
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT name, description, code FROM forged_tools "
                "WHERE id = ? AND status = 'pending'",
                (tool_id,),
            ).fetchone()
    except Exception as e:
        return f"DB error: {e}"
    if not row:
        return f"Tool #{tool_id} not found or not pending."
    name, description, code = row

    try:
        from agent import self_mod
        result = self_mod.register_new_tool(
            name=name,
            description=description,
            input_schema={"type": "object", "properties": {}, "required": []},
            code=code,
        )
        with longterm._conn() as c:
            c.execute(
                "UPDATE forged_tools SET status = 'approved', approved_at = ? WHERE id = ?",
                (time.time(), tool_id),
            )
        return result
    except Exception as e:
        return f"Registration failed: {e}"


def reject_forged(tool_id: int) -> str:
    with longterm._conn() as c:
        c.execute(
            "UPDATE forged_tools SET status = 'rejected' WHERE id = ?", (tool_id,)
        )
    return f"Tool #{tool_id} rejected."


_FORGE_PROMPT = """\
The user's AI agent needs a new tool to fill this capability gap:

CAPABILITY GAP: {gap}

Write a Python tool function that fills this gap. Requirements:
- Function named `run` with signature `def run(inputs: dict) -> str:`
- Accept a dict of inputs and return a string result
- Use ONLY the Python standard library — no pip installs
- Handle all errors gracefully and return an error string (never raise)
- Be safe to run in a sandboxed subprocess

Also provide:
- tool_name: short snake_case name (e.g. "read_rss_feed", "parse_json_file")
- description: one clear sentence describing what it does
- test_case: a minimal inputs dict that tests the function (it will be run)
- is_read_only: true if the tool only reads/computes (no writes, no network mutations)

Output ONLY valid JSON, no markdown fences:
{{
  "tool_name": "...",
  "description": "...",
  "code": "def run(inputs: dict) -> str:\\n    ...",
  "test_case": {{}},
  "is_read_only": true
}}
"""


def _validate_in_sandbox(code: str, test_inputs: dict) -> tuple[bool, str]:
    """Run the tool's test case in an isolated subprocess. Returns (passed, output)."""
    script = textwrap.dedent(f"""\
        import json, sys
        inputs = {json.dumps(test_inputs)}
        {code}
        try:
            result = run(inputs)
            print(json.dumps({{"ok": True, "result": str(result)[:300]}}))
        except Exception as e:
            print(json.dumps({{"ok": False, "error": str(e)}}))
    """)
    try:
        from tools import sandbox
        # DECISION (a): forged code is model-written and untrusted — validate it in
        # Docker, never on the host. No Docker → validation cannot proceed safely.
        try:
            backend = sandbox.autonomous_backend()
        except sandbox.SandboxUnavailable:
            return False, "Docker sandbox required to validate a forged tool (unavailable)."
        res = backend.run_python(script, timeout=10)
        out = (res.get("stdout") or "").strip()
        if not out:
            if res.get("returncode") == -1 and "timed out" in (res.get("stderr") or ""):
                return False, "sandbox timed out (10s)"
            return False, ((res.get("stderr") or "")[:200] or "no output")
        data = json.loads(out)
        if data.get("ok"):
            return True, data.get("result", "")
        return False, data.get("error", "unknown error")
    except Exception as e:
        return False, str(e)


def attempt_forge(client, gap_description: str) -> Optional[dict]:
    """Ask Claude to write a tool for the gap. Returns the staged tool dict or None."""
    prompt = _FORGE_PROMPT.format(gap=gap_description)
    try:
        resp = telemetry.create(
            client,
            call_site="agent.skill_forge/forge",
            model=config.AGENT_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
    except Exception as e:
        print(f"[SkillForge] Claude call failed: {e}")
        return None

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        return None

    try:
        proposal = json.loads(text[start:end])
    except Exception:
        return None

    name = proposal.get("tool_name", "").strip()
    description = proposal.get("description", "").strip()
    code = proposal.get("code", "").strip()
    test_case = proposal.get("test_case", {})

    if not name or not code or not name.isidentifier():
        return None

    passed, output = _validate_in_sandbox(code, test_case)
    if not passed:
        print(f"[SkillForge] Sandbox failed for '{name}': {output}")
        return None

    now = time.time()
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO forged_tools "
                "(name, description, code, test_case, status, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (name, description, code, json.dumps(test_case), now),
            )
            tool_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        print(f"[SkillForge] DB insert failed: {e}")
        return None

    # DECISION (b): every forged tool stages for approval. The model-declared
    # `is_read_only` flag is NOT trusted — it was never verified against the code,
    # so an injected "read-only:true" could ship host-executing tools unattended.
    if _NOTIFY_FN:
        try:
            _NOTIFY_FN(
                title="New tool ready to install",
                body=f"'{name}': {description}",
                kind="skill_forge",
                priority="normal",
                url=f"/?tab=skills",
            )
        except Exception:
            pass
    print(f"[SkillForge] Staged '{name}' for user approval (id={tool_id})")
    proposal["auto_approved"] = False

    proposal["id"] = tool_id
    proposal["sandbox_output"] = output
    return proposal


# ── Skill acquisition (used by the Constellation experts) ─────────────────────

_NET_FORGE_PROMPT = """\
The user's AI expert needs a new skill to fill this capability gap:

CAPABILITY GAP: {gap}

Write a Python skill that fills this gap. Requirements:
- Function named `run` with signature `def run(inputs: dict) -> str:`
- Accept a dict of inputs and return a human-readable string result
- This skill MAY use the network via the standard library (urllib.request, ssl,
  json, http.client). Do NOT pip-install anything — standard library only.
- Read any secrets/tokens from os.environ (e.g. os.environ.get("SOME_TOKEN")).
  Never hard-code credentials.
- Handle all errors gracefully and return an error string (never raise)

Also provide:
- tool_name: short snake_case name (e.g. "post_to_webhook", "fetch_weather")
- description: one clear sentence describing what it does
- test_case: a minimal inputs dict (it will NOT be executed, only compiled)
- env_vars: list of environment variable names the skill expects (may be empty)

Output ONLY valid JSON, no markdown fences:
{{
  "tool_name": "...",
  "description": "...",
  "code": "def run(inputs: dict) -> str:\\n    ...",
  "test_case": {{}},
  "env_vars": []
}}
"""


def _compile_check(code: str) -> tuple[bool, str]:
    """Compile-only validation (no execution) — used for networked skills that
    would otherwise cause real side effects when run."""
    try:
        ns: dict = {}
        exec(compile(code, "<forged>", "exec"), ns)
    except Exception as e:
        return False, f"compile error: {e}"
    if "run" not in ns or not callable(ns["run"]):
        return False, "code must define def run(inputs: dict) -> str"
    return True, "compiled OK"


def _propose(client, gap: str, allow_network: bool) -> Optional[dict]:
    """Ask the model for a skill proposal and validate it. Returns a dict or None."""
    prompt = (_NET_FORGE_PROMPT if allow_network else _FORGE_PROMPT).format(gap=gap)
    try:
        resp = telemetry.create(
            client, call_site="agent.skill_forge/acquire",
            model=config.AGENT_MODEL, max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
    except Exception as e:
        print(f"[SkillForge] acquire generation failed: {e}")
        return None

    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        return None
    try:
        p = json.loads(text[start:end])
    except Exception:
        return None

    name = (p.get("tool_name") or "").strip()
    description = (p.get("description") or "").strip()
    code = (p.get("code") or "").strip()
    if not name or not code or not name.isidentifier():
        return None

    if allow_network:
        ok, out = _compile_check(code)
    else:
        ok, out = _validate_in_sandbox(code, p.get("test_case", {}))
    if not ok:
        print(f"[SkillForge] validation failed for '{name}': {out}")
        return None

    return {"name": name, "description": description, "code": code,
            "test_case": p.get("test_case", {}), "output": out,
            "env_vars": p.get("env_vars", [])}


def acquire(client, description: str, *, allow_network: bool = False,
            trigger: str = "expert") -> str:
    """Forge a brand-new skill on demand for a capability gap.

    Both kinds end up staged for approval, by different routes: offline skills
    go through skills.create_skill(), which gates any trigger that is not
    "manual" into approvals.stage(); networked skills are written straight into
    forged_tools with needs_network=1. Returns a status string for the expert to
    relay to the user.

    This docstring previously said offline skills were "installed immediately
    (the expert can run them the same turn)". That stopped being true when the
    trigger gate was added, and nothing caught it because no test existed.
    """
    prop = _propose(client, description, allow_network)
    if not prop:
        return ("Couldn't forge a working skill for that — generation or validation "
                "failed. Try describing the capability more concretely.")
    name, desc, code = prop["name"], prop["description"], prop["code"]

    if not allow_network:
        try:
            from agent import skills as _skills
            msg = _skills.create_skill(name, desc, code, _trigger=trigger)
        except Exception as e:
            return f"Forged '{name}' but installation failed: {e}"
        # create_skill gates anything whose trigger is not "manual" into
        # approvals.stage() — which is the safety gate doing its job. Reporting
        # that as "didn't install cleanly" told the user a success was a
        # failure, and made the gate look like a bug worth removing.
        if "STAGED for approval" in msg:
            return (f"Forged a skill '{name}': {desc}. It is staged for your "
                    f"approval — approve it in the dashboard and I can run it "
                    f"from then on.")
        if "created and loaded" not in msg and "kept at previous" not in msg:
            return f"Forged '{name}' but it didn't install cleanly: {msg}"
        return (f"Installed a new skill '{name}': {desc}. It's live now — run it with "
                f"run_skill('{name}', {{...}}).")

    # Networked → stage for one-time approval.
    now = time.time()
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO forged_tools "
                "(name, description, code, test_case, status, created_at, needs_network) "
                "VALUES (?, ?, ?, ?, 'pending', ?, 1)",
                (name, desc, code, json.dumps(prop.get("test_case", {})), now),
            )
            tool_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        return f"Forged '{name}' but couldn't stage it for approval: {e}"

    env_note = ""
    if prop.get("env_vars"):
        env_note = " It will use: " + ", ".join(str(v) for v in prop["env_vars"]) + "."
    if _NOTIFY_FN:
        try:
            _NOTIFY_FN(
                title="New networked skill needs approval",
                body=f"'{name}': {desc}",
                kind="skill_forge", priority="normal", url="/?tab=skills",
            )
        except Exception:
            pass
    return (f"Forged a networked skill '{name}': {desc}. Because it uses the internet, "
            f"it's staged for your one-time approval in the Skill Forge tab.{env_note} "
            f"Approve it there and I can run it from then on.")
