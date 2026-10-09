"""Explicit, scoped evidence admission and reversible provider views.

Original Apex policies inspired by MemTrim, memory governance and personalization.
Permissions are host-supplied metadata; relevance and prompt wording cannot grant them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import contextvars
from contextlib import contextmanager
import json
import re
import time
from uuid import uuid4

from agent import longterm
from agent.incident_replay import boundary
from agent._vendor.memagent.base_memory import BaseMemoryProvider
from agent._vendor.memagent.memory_types import (
    MemoryItem, MemoryRequest, MemoryResponse, MemoryStatus, MemoryType, TrajectoryData,
)

_context = contextvars.ContextVar("governed_memory_context", default=None)


@contextmanager
def use(context):
    if not isinstance(context, Context):
        raise TypeError("a trusted Context is required")
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def current():
    return _context.get()


@dataclass(frozen=True)
class Context:
    subject: str
    domain: str
    purpose: str
    now: float
    task_signature: str = ""
    support: dict[str, str] = field(default_factory=dict)

    allow_inferred: bool = False

    def __post_init__(self):
        import math
        if any(not isinstance(v, str) or not v for v in (self.subject, self.domain, self.purpose)):
            raise ValueError("trusted subject, domain and purpose are required")
        if type(self.now) not in (int, float) or not math.isfinite(self.now):
            raise ValueError("finite current timestamp required")
        if type(self.allow_inferred) is not bool or not isinstance(self.support, dict):
            raise ValueError("invalid context policy")
        if not isinstance(self.task_signature, str) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.support.items()):
            raise ValueError("typed task signature and current support facts required")
        object.__setattr__(self, "support", dict(self.support))


@dataclass(frozen=True)
class Unit:
    id: str
    text: str
    subject: str
    domain: str
    purposes: tuple[str, ...]
    source: str
    provenance: tuple[str, ...]
    created_at: float
    expires_at: float | None = None
    approved: bool = False
    inferred: bool = False
    revoked: bool = False
    kind: str = "fact"  # fact | episode | procedure | outcome
    fact_key: str = ""
    fact_value: str = ""
    task_signature: str = ""
    support: dict[str, str] = field(default_factory=dict)
    occurred_at: float | None = None

    def __post_init__(self):
        import math
        if any(not isinstance(v, str) or not v for v in
               (self.id, self.text, self.subject, self.domain, self.source)):
            raise ValueError("memory identity, content, scope and source are required")
        if len(self.text) > 16000 or not self.provenance or not self.purposes:
            raise ValueError("bounded content, explicit purposes and provenance required")
        if not isinstance(self.provenance, (list, tuple)) or not isinstance(self.purposes, (list, tuple)):
            raise ValueError("provenance and purposes must be explicit sequences")
        if any(not isinstance(v, str) or not v for v in (*self.provenance, *self.purposes)):
            raise ValueError("provenance and purposes must be explicit nonempty strings")
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "purposes", tuple(self.purposes))
        if any(type(v) is not bool for v in (self.approved, self.inferred, self.revoked)):
            raise ValueError("memory policy flags must be booleans")
        if self.kind not in {"fact", "episode", "procedure", "outcome"}:
            raise ValueError("unknown evidence kind")
        if type(self.created_at) not in (int, float) or not math.isfinite(self.created_at) or (self.expires_at is not None and
                (type(self.expires_at) not in (int, float) or not math.isfinite(self.expires_at) or self.expires_at <= self.created_at)):
            raise ValueError("invalid freshness interval")
        if not isinstance(self.support, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.support.items()):
            raise ValueError("support must contain typed facts")
        object.__setattr__(self, "support", dict(self.support))
        if any(not isinstance(v, str) for v in (self.fact_key, self.fact_value, self.task_signature)):
            raise ValueError("typed facts and task signatures required")
        if self.occurred_at is not None and (type(self.occurred_at) not in (int, float) or not math.isfinite(self.occurred_at)):
            raise ValueError("observed event time must be finite")
        if bool(self.fact_key) != bool(self.fact_value):
            raise ValueError("fact key and value must be supplied together")
        if self.kind in {"procedure", "outcome"} and not self.task_signature:
            raise ValueError("derived procedures/outcomes require a task signature")
        if self.kind == "outcome" and not self.support:
            raise ValueError("outcomes require original support facts")


def _reason(unit: Unit, ctx: Context):
    if unit.subject != ctx.subject:
        return "subject"
    if unit.domain != ctx.domain:
        return "domain"
    if ctx.purpose not in unit.purposes:
        return "purpose"
    if unit.revoked:
        return "revoked"
    if not unit.approved:
        return "permission"
    if unit.inferred and not ctx.allow_inferred:
        return "inferred"
    if unit.created_at > ctx.now or (unit.expires_at is not None and unit.expires_at <= ctx.now):
        return "freshness"
    if unit.kind in {"procedure", "outcome"} and unit.task_signature != ctx.task_signature:
        return "task_signature"
    if unit.fact_key in ctx.support and unit.fact_value != ctx.support[unit.fact_key]:
        return "conflicting_current_evidence"
    if unit.support and any(ctx.support.get(k) != v for k, v in unit.support.items()):
        return "changed_support"
    return None


@boundary("apex.memory_admission", encode_result=lambda result: {
    "units": [asdict(u) for u in result[0]], "rejected": result[1]},
    decode_result=lambda result: ([Unit(**u) for u in result["units"]], result["rejected"]))
def admit(units: list[Unit], ctx: Context):
    """Return admitted units plus ID-only reasons; conflicting records are physically absent."""
    if len({u.id for u in units}) != len(units):
        raise ValueError("memory IDs must be unique")
    eligible, rejected = [], {}
    for unit in units:
        reason = _reason(unit, ctx)
        if reason:
            rejected[unit.id] = reason
        else:
            eligible.append(unit)
    values = {}
    for unit in eligible:
        if unit.fact_key:
            values.setdefault(unit.fact_key, set()).add(unit.fact_value)
    seen, kept = set(), []
    for unit in sorted(eligible, key=lambda u: (u.inferred, -u.created_at, u.id)):
        if unit.fact_key and len(values[unit.fact_key]) > 1:
            rejected[unit.id] = "conflicting_memory"
            continue
        # Current evidence already has this fact; duplicated evidence adds no influence.
        if unit.fact_key and ctx.support.get(unit.fact_key) == unit.fact_value:
            rejected[unit.id] = "repeated_current_evidence"
            continue
        key = (unit.fact_key, unit.fact_value) if unit.fact_key else (unit.kind, unit.text.strip(), unit.occurred_at, tuple(sorted(unit.support.items())))
        if key in seen:
            rejected[unit.id] = "duplicate_evidence"
            continue
        seen.add(key)
        kept.append(unit)
    return kept, rejected


def render(units):
    """Cited evidence is data, with inspect/forget IDs and explicit inference provenance."""
    return "\n".join(f"[memory:{u.id}; source:{u.source}; {'inferred' if u.inferred else 'observed'}] {u.text}" for u in units)


def init_db():
    with longterm._write_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS governed_memory (
                     id TEXT PRIMARY KEY, subject TEXT NOT NULL, domain TEXT NOT NULL,
                     kind TEXT NOT NULL, data_json TEXT NOT NULL, ts REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_governed_scope ON governed_memory(subject,domain,kind,ts)")
        c.execute("""CREATE TABLE IF NOT EXISTS memory_tombstones (
                     id TEXT PRIMARY KEY, subject TEXT NOT NULL, ts REAL NOT NULL)""")


@boundary("apex.governed_memory_write")
def store(unit: Unit):
    """Store immutable raw evidence; derived views refer to provenance IDs."""
    init_db()
    with longterm._write_conn() as c:
        if c.execute("SELECT 1 FROM memory_tombstones WHERE id=?", (unit.id,)).fetchone():
            raise ValueError("revoked ID cannot be resurrected")
        c.execute("INSERT INTO governed_memory VALUES (?,?,?,?,?,?)",
                  (unit.id, unit.subject, unit.domain, unit.kind, json.dumps(asdict(unit)), unit.created_at))
    return unit.id


def inspect(subject: str, domain: str | None = None):
    init_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT data_json FROM governed_memory WHERE subject=?"
                         + (" AND domain=?" if domain else "") + " ORDER BY ts DESC LIMIT 1000",
                         (subject, domain) if domain else (subject,)).fetchall()
    return [Unit(**{**json.loads(raw), "purposes": tuple(json.loads(raw)["purposes"]),
                   "provenance": tuple(json.loads(raw)["provenance"])}) for (raw,) in rows]


@boundary("apex.governed_memory_forget")
def forget(ident: str, subject: str):
    """Purge the source and all derived views that transitively cite it, then tombstone IDs."""
    init_db()
    with longterm._write_conn() as c:
        rows = c.execute("SELECT id,data_json FROM governed_memory WHERE subject=?", (subject,)).fetchall()
        data = {i: json.loads(raw) for i, raw in rows}
        if ident not in data:
            return {"deleted": []}
        deleted = {ident}
        while True:
            more = {i for i, u in data.items() if set(u["provenance"]) & deleted}
            if more <= deleted:
                break
            deleted |= more
        for i in deleted:
            c.execute("DELETE FROM governed_memory WHERE id=? AND subject=?", (i, subject))
            c.execute("INSERT OR IGNORE INTO memory_tombstones VALUES (?,?,?)", (i, subject, time.time()))
    return {"deleted": sorted(deleted)}


class ScopedProvider(BaseMemoryProvider):
    """Actual MemAgent interface adapter around Apex's admitted evidence store."""
    def __init__(self, name: str, kinds: set[str], context: Context):
        super().__init__(MemoryType.LIGHTWEIGHT_MEMORY, {"name": name})
        self.name, self.kinds, self.context = name, kinds, context

    def initialize(self):
        init_db()
        return True

    def provide_memory(self, request: MemoryRequest):
        from agent.memory_fusion import select
        units = [u for u in inspect(self.context.subject, self.context.domain) if u.kind in self.kinds]
        selected = select(units, self.context, request.query)
        return MemoryResponse([MemoryItem(u.id, u.text, asdict(u), selected["scores"][u.id]) for u in selected["units"]],
                              self.memory_type, len(units) - len(selected["rejected"]))

    def take_in_memory(self, trajectory_data: TrajectoryData):
        # Ingestion cannot infer approval, identity, or permission from trajectory text.
        return False, "Store explicit approved evidence through the owner admission API."

    def probe(self, query: str, top_k=1):
        result = self.provide_memory(MemoryRequest(query, "", MemoryStatus.BEGIN))
        return {"provider": self.name, "ids": [m.id for m in result.memories[:top_k]],
                "total": result.total_count}


def recall(query: str, ctx: Context, *, mode="portfolio"):
    """Deterministic three-provider portfolio; unknown tasks use verified facts only."""
    kinds = {"verified": {"fact"}, "episodic": {"episode"}, "procedural": {"procedure"}}
    selected = ["verified"]
    if mode == "portfolio" and ctx.task_signature:
        selected += ["episodic", "procedural"]
    elif mode not in {"portfolio", "verified"}:
        raise ValueError("mode must be portfolio or verified")
    out, seen = [], set()
    for name in selected:
        response = ScopedProvider(name, kinds[name], ctx).provide_memory(MemoryRequest(query, "", MemoryStatus.BEGIN))
        for item in response.memories:
            if item.id not in seen:
                seen.add(item.id)
                out.append({"id": item.id, "content": item.content, "provider": name,
                            "provenance": item.metadata["provenance"], "source": item.metadata["source"]})
    return {"memories": out, "providers": selected, "policy": "deterministic-scope-first/v1"}
