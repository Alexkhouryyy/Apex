"""Bounded dependency-aware work plans inspired by SAIGE, implemented for Apex."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math

from agent.incident_replay import boundary


@boundary("apex.delegation_plan")
def plan(nodes, *, confidence=0.0, max_workers=4):
    from agent.orchestrator import ROLE_PROMPTS
    if not nodes or len(nodes) > 50 or type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise ValueError("1–50 nodes and 1–4 workers required")
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("finite dependency confidence in [0,1] required")
    lookup = {}
    for node in nodes:
        ident = node["id"]
        if not isinstance(ident, str) or not ident or len(ident) > 200 or ident in lookup:
            raise ValueError("unique node IDs required")
        if node["role"] not in ROLE_PROMPTS or not isinstance(node["task"], str) or not node["task"] or len(node["task"]) > 16000:
            raise ValueError("valid worker role and task required")
        deps = node["depends_on"]
        if not isinstance(deps, list) or len(deps) != len(set(deps)):
            raise ValueError("explicit unique dependency lists required")
        lookup[ident] = dict(node)
    for ident, node in lookup.items():
        if ident in node["depends_on"] or not set(node["depends_on"]) <= lookup.keys():
            raise ValueError("missing or self dependency")
    remaining, completed, waves = set(lookup), set(), []
    while remaining:
        ready = sorted(i for i in remaining if set(lookup[i]["depends_on"]) <= completed)
        if not ready:
            raise ValueError("cyclic dependency graph")
        waves.append(ready)
        completed.update(ready)
        remaining.difference_update(ready)
    density = sum(len(n["depends_on"]) for n in nodes) / max(1, len(nodes)*(len(nodes)-1)/2)
    parallel = confidence >= .8 and density <= .5 and any(len(wave) > 1 for wave in waves)
    return {"mode": "bounded_parallel" if parallel else "single", "nodes": lookup,
            "waves": waves, "max_workers": max_workers if parallel else 1,
            "density": density, "confidence": confidence,
            "reason": "independent_branches" if parallel else "coupled_or_uncertain"}


def execute(spec, run_node):
    """Pure scheduling seam. Only declared predecessor artifacts reach a worker.

    Single-mode plans are returned to the parent rather than creating extra agents.
    The callback must use Apex's existing role, budget and effect-authority gates.
    """
    # Revalidate user-controlled specs; caller cannot smuggle a larger worker cap.
    spec = plan(list(spec["nodes"].values()), confidence=spec["confidence"], max_workers=spec["max_workers"])
    if spec["mode"] == "single":
        return {"mode": "single", "status": "parent_execution_required", "plan": spec}
    results = {}
    for wave in spec["waves"]:
        with ThreadPoolExecutor(max_workers=spec["max_workers"]) as pool:
            jobs = {}
            for ident in wave:
                node = spec["nodes"][ident]
                if any(results[d]["status"] != "done" for d in node["depends_on"]):
                    results[ident] = {"status": "blocked", "reason": "predecessor_failed"}
                    continue
                artifacts = {d: results[d]["artifact"] for d in node["depends_on"]}
                jobs[pool.submit(run_node, node, artifacts)] = ident
            for future in as_completed(jobs):
                ident = jobs[future]
                try:
                    artifact = future.result()
                    if not isinstance(artifact, str) or not artifact.strip() or len(artifact) > 16000:
                        raise ValueError("worker must return a bounded nonempty artifact")
                    results[ident] = {"status": "done", "artifact": artifact}
                except Exception as exc:
                    results[ident] = {"status": "error", "reason": type(exc).__name__}
    return {"mode": spec["mode"], "results": results}
