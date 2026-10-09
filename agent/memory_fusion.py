"""Scope-first, read-time Mem++ rank fusion over immutable Apex evidence.

Semantic scores must come from a frozen evaluator snapshot; no embedding/network
calls or inferred permissions occur here. Temporal bounds use observed event time.
"""
import math
import re
from dataclasses import asdict
from agent._vendor.mem_plus_plus.ranking import _rrf_fuse
from agent.learning_eval import digest
from agent.memory_governance import admit


def select(units, context, query, *, semantic_scores=None, occurred_after=None,
           occurred_before=None, limit=5, budget=4000):
    if type(limit) is not int or not 1 <= limit <= 100 or type(budget) is not int or budget <= 0:
        raise ValueError('bounded count and positive byte budget required')
    for bound in (occurred_after, occurred_before):
        if bound is not None and (type(bound) not in (int,float) or not math.isfinite(bound)):
            raise ValueError('finite temporal bounds required')
    if occurred_after is not None and occurred_before is not None and occurred_after > occurred_before:
        raise ValueError('reversed temporal interval')
    admitted, rejected = admit(units, context)
    # Explicit event time is required for temporal queries, rather than pretending
    # ingestion time establishes when a remembered event actually happened.
    eligible = []
    for u in admitted:
        if (occurred_after is not None or occurred_before is not None) and u.occurred_at is None:
            rejected[u.id]='unknown_event_time';continue
        if occurred_after is not None and u.occurred_at < occurred_after:
            rejected[u.id]='event_time';continue
        if occurred_before is not None and u.occurred_at > occurred_before:
            rejected[u.id]='event_time';continue
        eligible.append(u)
    ids={u.id for u in units}
    scores=semantic_scores or {}
    if not set(scores) <= ids or any(type(v) not in (int,float) or not math.isfinite(v) for v in scores.values()):
        raise ValueError('semantic snapshot needs finite scores for known IDs')
    words=set(re.findall(r'\w+',query.lower()))
    lexical={u.id:len(words & set(re.findall(r'\w+',u.text.lower()))) for u in eligible}
    lex=[u.id for u in sorted(eligible,key=lambda u:(-lexical[u.id],u.id)) if lexical[u.id]]
    sem=[u.id for u in sorted(eligible,key=lambda u:(-scores.get(u.id,0),u.id)) if scores.get(u.id,0)>0]
    fused=_rrf_fuse([lex,sem])
    ordered=sorted((u for u in eligible if u.id in fused),key=lambda u:(-fused[u.id],u.id))
    chosen=[];used=0
    for u in ordered:
        n=len(u.text.encode())
        if used+n<=budget and len(chosen)<limit:chosen.append(u);used+=n
    return {'units':chosen,'scores':fused,'rejected':rejected,'used_bytes':used,
            'snapshot_sha':digest({'units':[asdict(u) for u in units],'context':asdict(context),
                                   'query':query,'scores':scores,'after':occurred_after,'before':occurred_before,
                                   'limit':limit,'budget':budget}),
            'policy':'scope-first-mempp-rrf/v1'}
