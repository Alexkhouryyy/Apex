"""Permission tiers and an audit trail for MCP tool calls.

Every outward-facing capability in Apex is deny-by-default with an explicit
allowlist. Channels are. IoT entities are. Sub-agent roles are
(`agent/subagent_scope.py`). Dangerous shell commands go through
`agent/safety.py`. Gestures ship with three verbs mapped and the rest inert.

MCP was the exception, and it was the worst one to have made. It loads
**third-party servers from a config file Apex does not own**
(`~/.claude/settings.json`, `mcp_servers.json`, the Claude Desktop config), so
the tool surface can change without a line of Apex changing. Those tools reach
real accounts — mail, calendars, documents, deploys. And `agent/core.py`
dispatched `mcp__*` straight through with a single IoT special-case: no tier,
no read-versus-write distinction, no record of what was called.

The blueprint's Phase 5 success check is "Apex discovers and uses the tool
end-to-end **safely**". Discovery and use already worked. This file is the
adverb.

## Why a server's own annotation cannot be trusted on its own

MCP lets a server annotate each tool with `readOnlyHint`, `destructiveHint`
and friends, and reading that is obviously right — the server knows what its
tools do. But the server is precisely the party this gate exists to constrain.
A sloppy or hostile server can mark `send_email` read-only, and if the
annotation were the whole answer, saying the magic word would buy an
unprompted write.

So the annotation may only ever make classification **stricter**, never looser:

    tier = the more restrictive of (Apex's own reading of the name,
                                   the server's annotation)

`destructiveHint: true` on something Apex reads as a get is believed — the
server is warning us and it is the only one who knows. `readOnlyHint: true` on
something Apex reads as a send is not, because that direction is the one worth
lying about. The asymmetry is the whole design, and `test_mcp_policy.py` holds
it.

## Unknown means write

A verb Apex does not recognise is classified `write`, not `read`. Any other
default hands every newly-invented tool name a free pass, and "we had not
thought of that verb yet" is not a safety property. This is the same reasoning
that made `ROLE_TOOLS` an allowlist.

## What gets recorded

Every decision — including refusals — lands in `mcp_audit`. A log of only the
calls that succeeded cannot answer "what did it try to do", which is the
question you have after something goes wrong.

Inputs are recorded as **key names and a hash, never values**, matching the
posture `agent/trajectory.py` already takes. MCP arguments carry message
bodies, addresses and occasionally credentials; an audit trail that quietly
becomes a copy of your mail is a worse problem than the one it was written to
solve. The hash still distinguishes "called twice with the same arguments"
from "called twice with different ones", which is what an audit actually needs.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Optional

import config
from agent import longterm

READ = "read"
WRITE = "write"

# Verbs that only ever look. Matched at the START of the tool name (or of a
# `_`/`-` separated segment following the server's own prefix), because a
# leading verb is the name's actual claim about itself; the same word buried in
# the middle usually qualifies a noun ("update_search_index" is not a search).
_READ_VERBS = frozenset({
    "get", "list", "read", "search", "fetch", "find", "query", "view", "show",
    "describe", "check", "count", "lookup", "download", "status", "inspect",
    "browse", "preview", "diff", "compare", "summarize", "summarise",
})

# Verbs that change something. Matched ANYWHERE in the name, deliberately:
# `get_or_create_channel` leads with a read verb and creates a channel. When a
# name makes both claims, the write claim is the true one.
_WRITE_VERBS = frozenset({
    "create", "add", "new", "send", "post", "write", "update", "edit", "patch",
    "delete", "remove", "destroy", "drop", "set", "put", "move", "rename",
    "copy", "duplicate", "upload", "import", "apply", "run", "exec", "execute",
    "deploy", "publish", "release", "merge", "close", "reopen", "archive",
    "unarchive", "reply", "comment", "trigger", "start", "stop", "restart",
    "pause", "resume", "buy", "purchase", "pay", "refund", "order", "approve",
    "reject", "revoke", "grant", "invite", "assign", "unassign", "schedule",
    "cancel", "restore", "reset", "sync", "enable", "disable", "install",
    "uninstall", "subscribe", "unsubscribe", "resolve", "unresolve", "label",
    "unlabel", "tag", "untag", "mark", "unmark", "star", "unstar", "share",
    "unshare", "clear", "empty", "purge", "trash", "spam", "fork", "commit",
    "push", "revert", "rollback", "migrate", "provision", "register", "spawn",
})

_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokens(name: str) -> list[str]:
    """Lowercase word tokens, splitting snake_case, kebab-case and camelCase."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name or "")
    return [t for t in _SPLIT.split(spaced.lower()) if t]


def split_name(full_tool_name: str) -> tuple[str, str]:
    """`mcp__slack__send_message` -> ("slack", "send_message").

    Falls back to ("", name) for anything not in that shape rather than
    raising: this runs on the dispatch path, and a gate that throws is a gate
    that gets wrapped in a bare `except` by the next person to hit it.
    """
    if not full_tool_name.startswith("mcp__"):
        return "", full_tool_name
    rest = full_tool_name[len("mcp__"):]
    server, sep, tool = rest.partition("__")
    return (server, tool) if sep else ("", rest)


def classify_name(tool_name: str) -> tuple[str, str]:
    """Apex's OWN reading of a tool name, ignoring anything the server says.

    Returns (tier, why). Never returns "unknown" as a tier — an unrecognised
    name is a write, and `why` says that is what happened so the reason is not
    mistaken for a positive identification.
    """
    toks = _tokens(tool_name)
    if not toks:
        return WRITE, "the tool has no usable name, so it is treated as a write"

    hit = next((t for t in toks if t in _WRITE_VERBS), None)
    if hit:
        return WRITE, f"the name contains '{hit}'"
    if toks[0] in _READ_VERBS:
        return READ, f"the name starts with '{toks[0]}' and contains no write verb"
    return WRITE, (f"'{toks[0]}' is not a verb Apex recognises, and an "
                   f"unrecognised tool is treated as a write")


def annotation_tier(annotations) -> tuple[Optional[str], str]:
    """What the SERVER says about its own tool, or (None, why) if it says nothing.

    Reads both the snake_case attributes this MCP SDK exposes and the
    camelCase wire names, because the two have differed across SDK versions and
    a rename would otherwise turn every annotation into a silent `None` —
    indistinguishable from a server that annotates nothing.
    """
    if annotations is None:
        return None, "the server annotated nothing"

    def _hint(*names):
        for n in names:
            if isinstance(annotations, dict):
                if n in annotations:
                    return annotations[n]
            elif hasattr(annotations, n):
                return getattr(annotations, n)
        return None

    destructive = _hint("destructive_hint", "destructiveHint")
    read_only = _hint("read_only_hint", "readOnlyHint")
    if destructive is True:
        return WRITE, "the server marks it destructive"
    if read_only is True:
        return READ, "the server marks it read-only"
    return None, "the server annotated nothing decisive"


def classify(tool_name: str, annotations=None) -> tuple[str, str]:
    """The tier actually used, and the sentence explaining it.

    The more restrictive of Apex's reading and the server's. See the module
    docstring: the annotation may tighten, never loosen.
    """
    own, own_why = classify_name(tool_name)
    theirs, their_why = annotation_tier(annotations)

    if theirs == WRITE and own == READ:
        return WRITE, (f"{their_why}, so it counts as a write even though "
                       f"{own_why}")
    if theirs == READ and own == WRITE:
        return WRITE, (f"{own_why}, and a server calling its own tool "
                       f"read-only does not override that")
    if own == READ:
        return READ, own_why
    return WRITE, own_why


# ── the runtime switch ───────────────────────────────────────────────────────
#
# A server can be turned off from the dashboard without editing .env and without
# a restart, stored in SQLite so the choice survives one. This is the same shape
# as agent/iot.py's kill switch, for the same reason: a safety control you have
# to restart the process to use is a safety control nobody uses in the moment
# they need it.
#
# **The switch can only ever narrow.** MCP_DENY from .env outranks it in both
# directions: a server denied there stays denied however the toggle is set. A
# control panel that could re-enable something the config file forbids would
# make the config file advisory, and anyone who set MCP_DENY meant it.
#
# Cached briefly so the tool-offering path — which runs on every turn — does not
# hit SQLite per tool. The TTL is short enough that a flip takes effect within a
# few seconds, which is what "without a restart" has to mean in practice.
_SWITCH_TTL = 3.0
# None means "not loaded", which is NOT the same as {} meaning "loaded, nothing
# is switched off". The first version used {} for both, and since {} is falsy
# the cache never engaged at all until somebody toggled something — so the
# common case (no server ever switched off) hit SQLite on every turn, and the
# revert test for cache invalidation passed whether the invalidation was there
# or not, because there was never a cache to invalidate.
_switch_cache: dict | None = None
_switch_at: float = 0.0
_switch_lock = __import__("threading").Lock()


def _switch_state() -> dict:
    """{server: enabled} for servers that have been explicitly toggled."""
    global _switch_cache, _switch_at
    with _switch_lock:
        if _switch_cache is not None and (time.time() - _switch_at) < _SWITCH_TTL:
            return dict(_switch_cache)
    try:
        with longterm._conn() as c:
            rows = c.execute("SELECT server, enabled FROM mcp_servers").fetchall()
        state = {r[0]: bool(r[1]) for r in rows}
    except Exception:
        # A missing table must not disable every server. Failing open here is
        # correct and deliberate: this switch's job is to let you turn things
        # OFF, and a database problem is not you turning something off.
        return {}
    with _switch_lock:
        _switch_cache, _switch_at = state, time.time()
    return dict(state)


def set_server_enabled(server: str, enabled: bool) -> dict:
    """Turn one server on or off. Returns what actually took effect.

    Says so when .env still forbids it, rather than reporting success for a
    switch that changes nothing — a toggle that flips in the UI and does not
    flip in reality is worse than no toggle.
    """
    server = str(server or "").strip()
    if not server:
        raise ValueError("set_server_enabled needs a server name")
    with longterm._conn() as c:
        c.execute("INSERT INTO mcp_servers (server, enabled, changed_at)"
                  " VALUES (?, ?, ?) ON CONFLICT(server) DO UPDATE SET"
                  " enabled=excluded.enabled, changed_at=excluded.changed_at",
                  (server, 1 if enabled else 0, time.time()))
        c.commit()
    global _switch_cache
    with _switch_lock:
        _switch_cache, _switch_at = None, 0.0     # next read reloads
    still_denied = _matches(_rules(getattr(config, "MCP_DENY", [])), server, "*")
    return {"server": server, "enabled": bool(enabled),
            "effective": bool(enabled) and not still_denied,
            "note": (f"'{still_denied}' in MCP_DENY still blocks this server — "
                     f"the switch cannot override .env")
                    if (enabled and still_denied) else ""}


def server_enabled(server: str) -> bool:
    """Default on. A server appearing in a config file is somebody adding it;
    it should work without also being switched on here."""
    return _switch_state().get(str(server or "").strip(), True)


def servers_off() -> list[str]:
    return sorted(s for s, on in _switch_state().items() if not on)


# ── policy ───────────────────────────────────────────────────────────────────

ALLOW = "allow"
DENY = "deny"
ASK = "ask"


def _rules(raw) -> list[str]:
    """`server:tool` entries, lowercased. Accepts a list or a comma string so a
    config read either way behaves the same."""
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(e).strip().lower() for e in (raw or []) if str(e).strip()]


def _matches(rules: list[str], server: str, tool: str) -> Optional[str]:
    """Return the rule that matched, or None. `server:*` and a bare `server`
    both mean the whole server; `*` means everything."""
    s, t = (server or "").lower(), (tool or "").lower()
    for r in rules:
        if r in ("*", f"{s}:{t}", f"{s}:*", s):
            return r
    return None


def decide(full_tool_name: str, annotations=None) -> dict:
    """The whole verdict for one call, as data rather than as a side effect.

    Pure — it reads config and nothing else, takes no locks and writes no rows,
    so the policy can be tested exhaustively without an MCP server, a database
    or a user to prompt. `enforce()` below is the part with consequences.
    """
    server, tool = split_name(full_tool_name)
    tier, why = classify(tool, annotations)
    policy = str(getattr(config, "MCP_POLICY", "ask")).strip().lower()

    deny_hit = _matches(_rules(getattr(config, "MCP_DENY", [])), server, tool)
    allow_hit = _matches(_rules(getattr(config, "MCP_ALLOW", [])), server, tool)

    def out(action, reason):
        return {"action": action, "tier": tier, "server": server, "tool": tool,
                "policy": policy, "reason": reason, "why_tier": why}

    # Deny wins over everything, including an explicit allow and MCP_POLICY=all.
    # A list of things that must never happen is worthless if another setting
    # can outrank it.
    if deny_hit:
        return out(DENY, f"'{deny_hit}' is on MCP_DENY")
    if server and not server_enabled(server):
        return out(DENY, f"the '{server}' server is switched off in the dashboard")
    if policy == "off":
        return out(DENY, "MCP_POLICY=off — no MCP tool runs")
    if allow_hit:
        return out(ALLOW, f"'{allow_hit}' is on MCP_ALLOW")
    if policy == "all":
        return out(ALLOW, "MCP_POLICY=all — every tool runs unasked")
    if tier == READ:
        return out(ALLOW, f"a read: {why}")
    if policy == "read_only":
        return out(DENY, f"a write, and MCP_POLICY=read_only — {why}")
    return out(ASK, f"a write: {why}")


# ── audit ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS mcp_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                server TEXT NOT NULL,
                tool TEXT NOT NULL,
                tier TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                arg_keys TEXT NOT NULL DEFAULT '',
                args_hash TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0,
                ok INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(ts DESC)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                server     TEXT PRIMARY KEY,
                enabled    INTEGER NOT NULL DEFAULT 1,
                changed_at REAL NOT NULL DEFAULT 0
            )
        """)


def fingerprint(inputs: dict) -> tuple[str, str]:
    """(comma-joined key names, short hash of the arguments).

    Values never leave this function. The hash is over canonical JSON so the
    same call twice hashes the same, and it is truncated to 16 hex characters —
    enough to distinguish calls in a log, far too little to attack.
    """
    if not isinstance(inputs, dict):
        return "", ""
    keys = ",".join(sorted(str(k) for k in inputs))
    try:
        blob = json.dumps(inputs, sort_keys=True, default=str)
    except Exception:
        blob = repr(sorted(inputs.items(), key=lambda kv: str(kv[0])))
    return keys, hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def record(verdict: dict, inputs: dict, *, decision: str, duration_ms: int = 0,
           ok: bool = False, error: str = "") -> None:
    """Write one audit row. Never raises: an audit failure must not become a
    tool failure, or the first unwritable database turns into "MCP is broken"."""
    try:
        keys, digest = fingerprint(inputs)
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO mcp_audit (ts, server, tool, tier, decision, reason,"
                " arg_keys, args_hash, duration_ms, ok, error)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), verdict.get("server", ""), verdict.get("tool", ""),
                 verdict.get("tier", ""), decision, str(verdict.get("reason", ""))[:500],
                 keys[:500], digest, int(duration_ms), 1 if ok else 0, str(error)[:500]))
    except Exception as e:
        print(f"[MCP] Could not write the audit row: {e}")


def recent(limit: int = 50) -> list[dict]:
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT ts, server, tool, tier, decision, reason, arg_keys,"
                " args_hash, duration_ms, ok, error FROM mcp_audit"
                " ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
    except Exception:
        return []
    cols = ("ts", "server", "tool", "tier", "decision", "reason", "arg_keys",
            "args_hash", "duration_ms", "ok", "error")
    return [dict(zip(cols, r)) for r in rows]


def summary(days: int = 30) -> dict:
    """Counts by decision, so "nothing has been refused" and "nothing has been
    recorded" are different answers on the dashboard rather than one blank."""
    since = time.time() - days * 86400
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT decision, COUNT(*) FROM mcp_audit WHERE ts >= ?"
                " GROUP BY decision", (since,)).fetchall()
    except Exception:
        return {"total": 0, "by_decision": {}, "days": days}
    by = {d: n for d, n in rows}
    return {"total": sum(by.values()), "by_decision": by, "days": days}


# ── enforcement ──────────────────────────────────────────────────────────────

# Set by whoever knows how to reach the user. Left None deliberately rather
# than defaulting to input(): see enforce().
_confirm_fn = None


def set_confirm_fn(fn) -> None:
    global _confirm_fn
    _confirm_fn = fn


def _ask(reason: str) -> bool:
    """Put a write to the user. NO when there is nobody to ask.

    Falls back to `agent.safety`'s confirm function so MCP inherits the one
    approval path Apex already has — including `safety.interactive_only`'s rule
    that a background thread may not seize the console, and resident mode's
    notify-and-refuse. Two separate prompting mechanisms would eventually
    disagree about who is allowed to ask.

    With no confirm function anywhere, this returns False rather than calling
    `input()`. A permission gate that blocks on stdin is a permission gate that
    hangs a daemon, and "the user was not at the keyboard" has never been a
    reason to permit a write.
    """
    fn = _confirm_fn
    if fn is None:
        try:
            from agent import safety
            fn = safety._confirm_fn
        except Exception:
            fn = None
    if fn is None:
        return False
    try:
        return bool(fn(reason))
    except Exception as e:
        print(f"[MCP] Confirmation failed, refusing: {e}")
        return False


def enforce(full_tool_name: str, inputs: dict, annotations=None) -> Optional[str]:
    """Gate one MCP call. Returns None to proceed, or the refusal to return.

    Records the decision either way — a refusal that leaves no trace is
    indistinguishable, a week later, from a call nobody made.
    """
    v = decide(full_tool_name, annotations)
    action = v["action"]

    if action == ALLOW:
        record(v, inputs, decision="allowed")
        return None

    if action == DENY:
        record(v, inputs, decision="denied")
        return refusal(v)

    where = (f"the '{v['server']}' MCP server" if v.get("server")
             else "an MCP server")
    reason = f"{full_tool_name} wants to make a change through {where} ({v['why_tier']})."
    if _ask(reason):
        record(v, inputs, decision="asked_allowed")
        return None
    record(v, inputs, decision="asked_denied")
    return (f"[MCP blocked] {full_tool_name} is a write and was not approved. "
            f"{v['why_tier'].capitalize()}. Approve it once when asked, or add "
            f"'{_label(v)}' to MCP_ALLOW to stop being asked.")


def _label(v: dict) -> str:
    """`server:tool`, or just the tool when the name was not in the
    `mcp__server__tool` shape. `_connect_server` always builds that shape, so
    this is for the degenerate case — and a refusal reading "':srv__do' is not
    allowed", with an empty server before the colon, tells nobody anything."""
    return f"{v['server']}:{v['tool']}" if v.get("server") else v.get("tool", "?")


def refusal(v: dict) -> str:
    """The refusal text, written so the next step is obvious.

    A gate that says only "denied" gets worked around or switched off wholesale,
    which is worse than the gate not existing. This names the rule that fired
    and the one setting that changes it.
    """
    return (f"[MCP blocked] {_label(v)} did not run — {v['reason']}. To allow "
            f"it, add '{_label(v)}' to MCP_ALLOW in .env (or set "
            f"MCP_POLICY=ask to be prompted for writes instead).")
