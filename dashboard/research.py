"""Owner-only inspection and explicit research controls; never model-authorized."""
import time
from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from dashboard.home import owner, body, invoke
from agent import memory_governance as memory, learning_registry

router = APIRouter(prefix="/api/research", dependencies=[Depends(owner)])


@router.get("/memory")
async def inspect_memory(domain: str | None = None):
    return await invoke(lambda: [asdict(u) for u in memory.inspect("owner", domain)])


@router.post("/memory")
async def save_memory(request: Request):
    data = await body(request)
    routing = data.pop("routing", None)
    # This authenticated operation is an explicit owner approval. Identity cannot
    # be supplied by model/provider output, and no existing record is overwritten.
    data = {**data, "id": uuid4().hex, "subject": "owner", "approved": True,
            "revoked": False, "created_at": time.time(), "source": "owner-input"}
    data.setdefault("purposes", ())
    data.setdefault("provenance", ("owner-input",))
    def save():
        unit = memory.Unit(**data)
        if routing is not None:
            from agent import memory_router
            result = memory_router.route(unit, routing["probabilities"],
                                         iteration=routing.get("iteration", 0), source=routing["source"])
            if result["status"] != "routed":
                return {k: v for k, v in result.items() if k != "unit"}
            unit = result["unit"]
        return {"id": memory.store(unit)}
    return await invoke(save)


@router.post("/memory/{ident}/forget")
async def forget_memory(ident: str):
    return await invoke(memory.forget, ident, "owner")


@router.post("/memory/recall")
async def recall_memory(request: Request):
    data = await body(request)
    return await invoke(lambda: memory.recall(data.get("query", ""), memory.Context(
        subject="owner", domain=data.get("domain", ""), purpose=data.get("purpose", ""), now=time.time(),
        task_signature=data.get("task_signature", ""), support=data.get("support", {}),
        allow_inferred=data.get("allow_inferred", False))))


@router.get("/learning")
async def learning_versions():
    return await invoke(learning_registry.listing)


@router.post("/context/{ident}/revive")
async def revive_context(ident: str, request: Request):
    from agent.context_graph import ContextGraph, load_archive
    data = await body(request)
    return await invoke(lambda: ContextGraph.from_messages(load_archive(ident)).revive(data["ids"]))


@router.post("/learning/{ident}/canary")
async def canary(ident: str, request: Request):
    data = await body(request)
    return await invoke(lambda: learning_registry.canary(
        ident, correct=data["correct"], permission_violation=data.get("permission_violation", False),
        contradiction=data.get("contradiction", False)))
