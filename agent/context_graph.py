"""Reversible, dependency-preserving context selection for closed API models.

An original Apex graph selector, not ReCAP's unavailable attention implementation.
The canonical messages are never edited; omitted blocks can be revived by ID.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import re

from agent.learning_eval import digest
from agent.incident_replay import boundary


def plain(value):
    """SDK objects as plain dicts, serialized the way the SDK sends them back
    (anthropic._utils._transform): only fields the API actually set. A full
    model_dump() adds fields like "caller": null to every tool_use block,
    which the API never sent and may reject on the next request."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(exclude_unset=True, mode="json", by_alias=True,
                                      exclude=getattr(value, "__api_exclude__", None)))
    return value


@dataclass(frozen=True)
class Block:
    id: str
    payload: dict
    depends_on: tuple[str, ...] = ()
    protected: bool = False
    importance: float = 0.0

    @property
    def bytes(self):
        return len(json.dumps(self.payload, ensure_ascii=False, sort_keys=True).encode())


class ContextGraph:
    def __init__(self, blocks):
        self.blocks = tuple(copy.deepcopy(list(blocks)))
        self.by_id = {b.id: b for b in self.blocks}
        if len(self.by_id) != len(self.blocks):
            raise ValueError("duplicate context IDs")
        for b in self.blocks:
            if not set(b.depends_on) <= self.by_id.keys():
                raise ValueError("missing dependency")

    def closure(self, ids):
        result, pending = set(), list(ids)
        while pending:
            ident = pending.pop()
            if ident not in self.by_id:
                raise ValueError("unknown context ID")
            if ident not in result:
                result.add(ident)
                pending.extend(self.by_id[ident].depends_on)
        return result

    def revive(self, ids):
        wanted = self.closure(ids)
        return copy.deepcopy([b.payload for b in self.blocks if b.id in wanted])

    @boundary("apex.context_selection")
    def select(self, query, budget):
        if type(budget) is not int or budget <= 0:
            raise ValueError("positive byte budget required")
        selected = self.closure(b.id for b in self.blocks if b.protected)
        cost = lambda ids: sum(self.by_id[i].bytes for i in ids)
        if cost(selected) > budget:
            return {"status": "protected_overflow", "messages": self.revive(self.by_id),
                    "selected_ids": list(self.by_id), "omitted_ids": [], "used_bytes": cost(self.by_id)}
        query_words = set(re.findall(r"\w+", query.lower()))
        scored = []
        for index, block in enumerate(self.blocks):
            text = json.dumps(block.payload, ensure_ascii=False)
            overlap = len(query_words & set(re.findall(r"\w+", text.lower())))
            scored.append((overlap + block.importance, index, block.id))
        # Relevant evidence first, recent evidence on equal scores. A group is
        # admitted only if its entire dependency closure fits.
        for _, _, ident in sorted(scored, reverse=True):
            proposed = selected | self.closure([ident])
            if cost(proposed) <= budget:
                selected = proposed
        ids = [b.id for b in self.blocks if b.id in selected]
        return {"status": "selected", "messages": self.revive(ids), "selected_ids": ids,
                "omitted_ids": [b.id for b in self.blocks if b.id not in selected], "used_bytes": cost(selected)}

    @classmethod
    def from_messages(cls, messages, keep=12):
        messages = plain(copy.deepcopy(messages))
        ids = [f"message-{i}-{digest(m)[:12]}" for i, m in enumerate(messages)]
        dependencies = {i: set() for i in ids}
        uses, results = {}, {}
        protected = set(ids[-keep:])
        latest_writes = {}
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError("canonical messages must be objects")
            content = message.get("content", [])
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            if any(not isinstance(b, dict) for b in blocks):
                raise ValueError("canonical content blocks must be objects")
            if message.get("role") in {"system", "developer"} or (message.get("role") == "user" and any(b.get("type") != "tool_result" for b in blocks)):
                protected.add(ids[index])
            for block in blocks:
                if block.get("type") == "tool_use":
                    if not isinstance(block.get("id"), str) or not block["id"] or block["id"] in uses:
                        raise ValueError("tool invocation IDs must be unique nonempty strings")
                    uses[block["id"]] = ids[index]
                    if block.get("name") in {"write_file", "append_file", "remember", "memory", "skill_manage",
                                              "self_modify", "develop_skill", "bash"}:
                        key = (block.get("name"), json.dumps(block.get("input", {}).get("path", "")))
                        if key in latest_writes:
                            dependencies[ids[index]].add(latest_writes[key])
                        latest_writes[key] = ids[index]
                        if block.get("name") == "bash":
                            protected.add(ids[index])  # shell effects cannot be inferred reliably from text
                elif block.get("type") == "tool_result":
                    if not isinstance(block.get("tool_use_id"), str) or not block["tool_use_id"] or block["tool_use_id"] in results:
                        raise ValueError("tool result IDs must be unique nonempty strings")
                    results[block["tool_use_id"]] = ids[index]
        protected.update(latest_writes.values())
        for ident, use in uses.items():
            if ident in results:
                result = results[ident]
                dependencies[use].add(result)
                dependencies[result].add(use)
            else:
                protected.add(use)  # a pending invocation cannot be hidden
        for ident, result in results.items():
            if ident not in uses:
                # An orphaned tool result was already present: selection must
                # not pretend it is a well-formed complete history.
                raise ValueError("orphaned tool result in canonical context")
        return cls(Block(ident, message, tuple(sorted(dependencies[ident])), ident in protected)
                   for ident, message in zip(ids, messages))


def save_archive(ident, messages):
    """Persist canonical history separately from the selected presentation."""
    from agent import longterm
    raw = json.dumps(plain(messages), ensure_ascii=False, allow_nan=False)
    with longterm._write_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS context_archives (id TEXT PRIMARY KEY,data_json TEXT NOT NULL)")
        prior = c.execute("SELECT data_json FROM context_archives WHERE id=?", (ident,)).fetchone()
        if prior:
            previous = json.loads(prior[0])
            if plain(messages)[:len(previous)] != previous:
                raise ValueError("canonical context is append-only; edited history needs a new archive ID")
        c.execute("INSERT INTO context_archives VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json", (ident, raw))


def load_archive(ident):
    from agent import longterm
    with longterm._conn() as c:
        row = c.execute("SELECT data_json FROM context_archives WHERE id=?", (ident,)).fetchone()
    if not row:
        raise ValueError("context archive not found")
    return json.loads(row[0])
