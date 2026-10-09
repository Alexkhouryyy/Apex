"""Versioned skill-object candidates, evidence receipts, promotion and rollback.

Only operator/evaluator code calls these functions; no model-facing evaluation tool.
The DB is an audit consistency boundary, not isolation from a compromised host.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

from agent import longterm
from agent.learning_eval import artifact_digest, digest
from agent.incident_replay import boundary


def init_db():
    with longterm._write_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS learning_versions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT, artifact TEXT NOT NULL,
            artifact_sha TEXT NOT NULL, object_json TEXT NOT NULL, receipt_json TEXT,
            status TEXT NOT NULL, canary_failures INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL)""")
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_active
                     ON learning_versions(name) WHERE status IN ('active','stable')""")
        columns = {r[1] for r in c.execute("PRAGMA table_info(learning_versions)")}
        if "installed_sha" not in columns:
            c.execute("ALTER TABLE learning_versions ADD COLUMN installed_sha TEXT")
        c.execute("CREATE TABLE IF NOT EXISTS learning_contracts (sha TEXT PRIMARY KEY,id TEXT UNIQUE NOT NULL,data_json TEXT NOT NULL)")


def freeze_contract(contract):
    """Operator-only one-time registration; changing thresholds requires a new contract ID."""
    from dataclasses import asdict
    init_db()
    with longterm._write_conn() as c:
        row = c.execute("SELECT sha FROM learning_contracts WHERE id=?", (contract.id,)).fetchone()
        if row and row[0] != contract.sha:
            raise ValueError("a frozen evaluation contract cannot be rewritten")
        c.execute("INSERT OR IGNORE INTO learning_contracts VALUES (?,?,?)",
                  (contract.sha, contract.id, json.dumps(asdict(contract))))
    return contract.sha


def propose(name, code, skill_object, *, failure_class="procedure"):
    """NO_PATCH is valid when a skill edit cannot repair the attributed failure."""
    if failure_class in {"retrieval", "perception", "model_capability", "permission"}:
        return {"status": "NO_PATCH", "reason": failure_class}
    if failure_class not in {"procedure", "applicability", "exclusion", "composition"}:
        raise ValueError("failure attribution must name an addressable skill object")
    if not isinstance(name, str) or not name.isidentifier() or not isinstance(code, str) or not code:
        raise ValueError("named nonempty artifact required")
    for key in ("address", "applicability", "procedure", "exclusions", "support_traces", "contradiction_traces"):
        if key not in skill_object:
            raise ValueError(f"skill object requires {key}")
    if not skill_object["support_traces"] or skill_object["contradiction_traces"]:
        raise ValueError("supporting traces required; contradicted rules cannot become candidates")
    for key in ("address", "applicability", "procedure"):
        if not isinstance(skill_object[key], str) or not skill_object[key] or len(skill_object[key]) > 16000:
            raise ValueError("addressable skill fields must be bounded nonempty text")
    for key in ("exclusions", "support_traces", "contradiction_traces"):
        values = skill_object[key]
        if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("skill traces and exclusions must contain explicit text IDs/rules")
    init_db()
    ident = uuid4().hex
    with longterm._write_conn() as c:
        prior = c.execute("SELECT id FROM learning_versions WHERE name=? AND status IN ('active','stable')", (name,)).fetchone()
        c.execute("INSERT INTO learning_versions (id,name,parent,artifact,artifact_sha,object_json,receipt_json,status,canary_failures,ts) VALUES (?,?,?,?,?,?,NULL,'trial',0,?)",
                  (ident, name, prior[0] if prior else None, code, artifact_digest(code),
                   json.dumps(skill_object, sort_keys=True), time.time()))
    return {"id": ident, "status": "trial", "artifact_sha": artifact_digest(code)}


def attach_receipt(ident, receipt):
    """Trusted evaluator stores a receipt. Learner self-ratings are not acceptable inputs."""
    body = {k: v for k, v in receipt.items() if k != "receipt_sha"}
    if receipt.get("receipt_sha") != digest(body) or type(receipt.get("passed")) is not bool:
        raise ValueError("invalid evaluation receipt")
    with longterm._write_conn() as c:
        if not c.execute("SELECT 1 FROM learning_contracts WHERE sha=?", (receipt.get("contract_sha"),)).fetchone():
            raise ValueError("receipt must use an operator-frozen evaluation contract")
        row = c.execute("SELECT artifact_sha,status,parent FROM learning_versions WHERE id=?", (ident,)).fetchone()
        if not row or row[1] != "trial" or row[0] != receipt.get("artifact_sha"):
            raise ValueError("receipt must match a current trial artifact")
        prior = c.execute("SELECT artifact_sha FROM learning_versions WHERE id=?", (row[2],)).fetchone() if row[2] else None
        if receipt.get("baseline_artifact_sha") != (prior[0] if prior else None):
            raise ValueError("receipt must identify the exact parent baseline artifact")
        c.execute("UPDATE learning_versions SET receipt_json=? WHERE id=?", (json.dumps(receipt), ident))


def require_evaluated(name, code):
    """Check the exact installed bytes, rather than a model-declared name or pass flag."""
    init_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT receipt_json FROM learning_versions WHERE name=? AND artifact_sha=? AND status IN ('active','stable')",
                         (name, artifact_digest(code))).fetchall()
    for (raw,) in rows:
        if not raw:
            continue
        receipt = json.loads(raw)
        if receipt.get("passed") is True and receipt.get("receipt_sha") == digest({k: v for k, v in receipt.items() if k != "receipt_sha"}):
            return receipt
    raise RuntimeError("This generated skill needs a promoted version with a matching passed held-out evaluation receipt before installation.")


def note_installed(name, code, source):
    with longterm._write_conn() as c:
        updated = c.execute("UPDATE learning_versions SET installed_sha=? WHERE name=? AND artifact_sha=? AND status IN ('active','stable')",
                            (artifact_digest(source), name, artifact_digest(code)))
        if updated.rowcount != 1:
            raise RuntimeError("active evaluated version changed during installation; restore prior bytes")


def runtime_allowed(name, source):
    init_db()
    with longterm._conn() as c:
        tracked = c.execute("SELECT 1 FROM learning_versions WHERE name=? AND installed_sha IS NOT NULL", (name,)).fetchone()
        if not tracked:
            return True  # existing/manual skills outside the evaluated lifecycle
        row = c.execute("SELECT installed_sha FROM learning_versions WHERE name=? AND status IN ('active','stable')", (name,)).fetchone()
    return bool(row and row[0] == artifact_digest(source))


@boundary("apex.skill_promotion")
def promote(ident, *, expected_active=None):
    """Operator promotion with an atomic lease; does not execute generated code."""
    with longterm._write_conn() as c:
        row = c.execute("SELECT name,parent,receipt_json,status FROM learning_versions WHERE id=?", (ident,)).fetchone()
        if not row or row[3] != "trial" or not row[2]:
            raise ValueError("only an evaluated trial can be promoted")
        name, parent, raw, _ = row
        receipt = json.loads(raw)
        if receipt.get("passed") is not True or receipt.get("receipt_sha") != digest({k: v for k, v in receipt.items() if k != "receipt_sha"}):
            raise ValueError("failed candidate cannot be promoted")
        active = c.execute("SELECT id FROM learning_versions WHERE name=? AND status IN ('active','stable')", (name,)).fetchone()
        current = active[0] if active else None
        if current != expected_active or parent != current:
            raise RuntimeError("active skill changed; evaluate against the current baseline")
        c.execute("UPDATE learning_versions SET status='retired' WHERE name=? AND status IN ('active','stable')", (name,))
        c.execute("UPDATE learning_versions SET status='active' WHERE id=?", (ident,))
    return {"id": ident, "status": "active"}


def canary(ident, *, correct, permission_violation=False, contradiction=False):
    """Two consecutive failures or one permission/verified contradiction retire a rule."""
    if any(type(v) is not bool for v in (correct, permission_violation, contradiction)):
        raise ValueError("canary observations must be booleans")
    with longterm._write_conn() as c:
        row = c.execute("SELECT name,parent,status,canary_failures FROM learning_versions WHERE id=?", (ident,)).fetchone()
        if not row or row[2] not in {"active", "stable"}:
            raise ValueError("canary must target an active version")
        failures = 0 if correct else row[3] + 1
        rollback = permission_violation or contradiction or failures >= 2
        c.execute("UPDATE learning_versions SET canary_failures=?,status=? WHERE id=?",
                  (failures, "retired" if rollback else "stable" if correct else row[2], ident))
        restored = None
        if rollback and row[1]:
            previous = c.execute("SELECT status FROM learning_versions WHERE id=?", (row[1],)).fetchone()
            if previous and previous[0] == "retired":
                c.execute("UPDATE learning_versions SET status='active',canary_failures=0 WHERE id=?", (row[1],))
                restored = row[1]
    return {"rolled_back": rollback, "restored": restored, "consecutive_failures": failures}


def listing(name=None):
    init_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT id,name,parent,artifact_sha,status,canary_failures FROM learning_versions"
                         + (" WHERE name=?" if name else "") + " ORDER BY ts", (name,) if name else ()).fetchall()
    return [dict(zip(("id", "name", "parent", "artifact_sha", "status", "canary_failures"), r)) for r in rows]
