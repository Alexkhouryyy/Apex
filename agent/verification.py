"""Work verification — goals must be PROVEN complete, not merely asserted.

Today an agent closes a goal by writing `status='done'`. Nothing checks it. An
agent that believes it finished and an agent that actually finished are
indistinguishable in the record, which quietly corrupts every downstream signal
(reflection, evaluation, and any future learning loop) with unearned successes.

A goal may carry one or more **completion contracts** — checkable criteria
agreed up front. `verify()` evaluates them and writes an evidence ledger. The
gate in goals.update_goal then refuses to close a contracted goal whose
contracts do not pass.

Contract kinds:
  command     — run a shell command; exit 0 passes. Self-initiated, so it runs
                through the Docker sandbox per the origin-based sandbox decision.
  file_exists — a path exists (and is non-empty)
  contains    — a file's text contains a required substring
  llm         — a model judges gathered evidence against a stated criterion,
                for goals whose completion is real but not mechanically checkable
  manual      — only a human can confirm; never self-passes

Design rules:
  - Fail CLOSED. An unrunnable check is a FAILURE, never a pass. The entire
    value of this module is that it cannot be talked into saying yes.
  - Goals with no contract stay closable (backward compatible) but are recorded
    as unverified, so the two cases never blur.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from agent import longterm

COMMAND = "command"
FILE_EXISTS = "file_exists"
CONTAINS = "contains"
LLM = "llm"
MANUAL = "manual"

VALID_KINDS = {COMMAND, FILE_EXISTS, CONTAINS, LLM, MANUAL}

_COMMAND_TIMEOUT_S = 120


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS goal_contracts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id    INTEGER NOT NULL,
                kind       TEXT NOT NULL,
                spec       TEXT NOT NULL,
                detail     TEXT DEFAULT '',
                created_at REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS verification_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id     INTEGER NOT NULL,
                contract_id INTEGER,
                ts          REAL NOT NULL,
                passed      INTEGER NOT NULL,
                evidence    TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_contracts_goal ON goal_contracts(goal_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_verif_goal ON verification_log(goal_id, ts DESC)")


def add_contract(goal_id: int, kind: str, spec: str, detail: str = "") -> str:
    """Attach a completion criterion to a goal."""
    kind = (kind or "").strip().lower()
    if kind not in VALID_KINDS:
        return f"Invalid contract kind {kind!r}. Use one of: {sorted(VALID_KINDS)}"
    if not (spec or "").strip():
        return "A contract needs a spec (the command, path, or criterion)."
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO goal_contracts (goal_id, kind, spec, detail, created_at) VALUES (?,?,?,?,?)",
            (int(goal_id), kind, spec.strip(), detail or "", time.time()),
        )
    return f"Contract #{cur.lastrowid} added to goal #{goal_id}: [{kind}] {spec.strip()[:80]}"


def list_contracts(goal_id: int) -> list[dict]:
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT id, kind, spec, detail FROM goal_contracts WHERE goal_id = ? ORDER BY id",
                (int(goal_id),),
            ).fetchall()
    except Exception as e:
        # A MISSING TABLE provably means zero contracts were ever recorded, so
        # "no contracts" is the correct answer, not a failure. Narrow on purpose:
        # any other error re-raises so the gate still fails closed.
        if "no such table" in str(e).lower():
            return []
        raise
    return [{"id": r[0], "kind": r[1], "spec": r[2], "detail": r[3]} for r in rows]


# --- individual checks -------------------------------------------------------

def _check_command(spec: str) -> tuple[bool, str]:
    """Run a command in the sandbox. Exit 0 passes. Unrunnable == FAIL."""
    try:
        from tools import sandbox
        try:
            backend = sandbox.autonomous_backend()
        except sandbox.SandboxUnavailable:
            return False, ("Docker sandbox unavailable — verification commands are "
                           "self-initiated and must not run on the host. Not verified.")
        res = backend.run_shell(spec, timeout=_COMMAND_TIMEOUT_S)
        rc = res.get("returncode")
        out = ((res.get("stdout") or "") + (res.get("stderr") or "")).strip()[:500]
        return (rc == 0), f"exit={rc} :: {out or '(no output)'}"
    except Exception as e:
        return False, f"check could not run: {e}"


def _check_file_exists(spec: str) -> tuple[bool, str]:
    try:
        p = os.path.expanduser(spec.strip())
        if not os.path.exists(p):
            return False, f"missing: {p}"
        size = os.path.getsize(p) if os.path.isfile(p) else -1
        if size == 0:
            return False, f"exists but empty: {p}"
        return True, f"exists ({size} bytes): {p}" if size >= 0 else f"exists (dir): {p}"
    except Exception as e:
        return False, f"check could not run: {e}"


def _check_contains(spec: str, detail: str) -> tuple[bool, str]:
    """spec = path, detail = required substring."""
    try:
        p = os.path.expanduser(spec.strip())
        needle = (detail or "").strip()
        if not needle:
            return False, "no required substring given (put it in `detail`)"
        if not os.path.isfile(p):
            return False, f"missing: {p}"
        text = open(p, "r", errors="replace").read()
        return (needle in text), (f"found {needle!r}" if needle in text else f"{needle!r} not in {p}")
    except Exception as e:
        return False, f"check could not run: {e}"


_LLM_SYS = (
    "You are a strict completion auditor. You are given a COMPLETION CRITERION and "
    "EVIDENCE gathered from the user's system. Decide whether the criterion is "
    "demonstrably met by the evidence. Be skeptical: absence of evidence is NOT "
    "evidence of completion. Reply with exactly 'PASS: <one-line reason>' or "
    "'FAIL: <one-line reason>'."
)


def _check_llm(spec: str, evidence: str, client=None) -> tuple[bool, str]:
    try:
        import config
        from agent import provider
        model = getattr(config, "PROACTIVE_MODEL", None) or getattr(config, "AGENT_MODEL", None)
        if not model:
            return False, "no model configured for LLM verification"
        user = f"COMPLETION CRITERION:\n{spec}\n\nEVIDENCE:\n{evidence or '(none gathered)'}"
        out = (provider.complete(model, _LLM_SYS, user, max_tokens=200) or "").strip()
        passed = out.upper().startswith("PASS")
        return passed, out[:300]
    except Exception as e:
        return False, f"judge could not run: {e}"


# --- the gate ----------------------------------------------------------------

def verify(goal_id: int, evidence: str = "", client=None) -> dict:
    """Evaluate every contract on a goal and write the evidence ledger.

    Returns {contracted, passed, results:[{kind, spec, passed, evidence}]}.
    A goal with no contracts returns contracted=False, passed=True (nothing to
    disprove) — the caller decides what that means.
    """
    contracts = list_contracts(goal_id)
    if not contracts:
        return {"contracted": False, "passed": True, "results": []}

    results = []
    for ct in contracts:
        kind, spec, detail = ct["kind"], ct["spec"], ct.get("detail", "")
        if kind == COMMAND:
            ok, ev = _check_command(spec)
        elif kind == FILE_EXISTS:
            ok, ev = _check_file_exists(spec)
        elif kind == CONTAINS:
            ok, ev = _check_contains(spec, detail)
        elif kind == LLM:
            ok, ev = _check_llm(spec, evidence, client=client)
        elif kind == MANUAL:
            ok, ev = False, "manual confirmation required — a human must close this"
        else:
            ok, ev = False, f"unknown contract kind {kind!r}"

        results.append({"contract_id": ct["id"], "kind": kind, "spec": spec,
                        "passed": ok, "evidence": ev})
        try:
            with longterm._conn() as c:
                c.execute(
                    "INSERT INTO verification_log (goal_id, contract_id, ts, passed, evidence) "
                    "VALUES (?,?,?,?,?)",
                    (int(goal_id), ct["id"], time.time(), 1 if ok else 0, ev[:2000]),
                )
        except Exception:
            pass

    return {"contracted": True, "passed": all(r["passed"] for r in results), "results": results}


def format_result(res: dict) -> str:
    if not res.get("contracted"):
        return "No completion contract on this goal — closed unverified."
    lines = ["VERIFIED ✓" if res["passed"] else "NOT VERIFIED ✗"]
    for r in res["results"]:
        mark = "✓" if r["passed"] else "✗"
        lines.append(f"  {mark} [{r['kind']}] {r['spec'][:70]} — {r['evidence'][:160]}")
    return "\n".join(lines)


def history(goal_id: int, limit: int = 20) -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT ts, contract_id, passed, evidence FROM verification_log "
            "WHERE goal_id = ? ORDER BY ts DESC LIMIT ?", (int(goal_id), limit),
        ).fetchall()
    return [{"ts": r[0], "contract_id": r[1], "passed": bool(r[2]), "evidence": r[3]} for r in rows]
