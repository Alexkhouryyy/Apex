"""Explicit, idempotent continuation from a reviewed receipt; never replay a run."""
import hashlib
import json
from agent import team, continuity


def review(rid, notes, resolutions):
    with team._lock:
        run = team.get(rid)
        if not run or run['status'] not in ('interrupted', 'blocked', 'failed') or rid in team._active:
            raise ValueError('Only stopped, interrupted or failed tasks can be reviewed for continuation.')
        if not isinstance(notes, str) or not 20 <= len(notes.strip()) <= 2500:
            raise ValueError('Describe what remains and what you checked in 20–2,500 characters.')
        unknown = {f'{i}:{j}' for i,s in enumerate(run['steps']) for j,e in enumerate(s['evidence']) if e['status'] == 'outcome_unknown'}
        if not isinstance(resolutions, dict) or set(resolutions) != unknown:
            raise ValueError('Check and resolve every action with an unknown outcome before continuing.')
        for value in resolutions.values():
            if not isinstance(value, str) or not 20 <= len(value.strip()) <= 1500:
                raise ValueError('For each uncertain action, describe observed state and whether its effect occurred (20–1,500 characters).')
        old = continuity.read('recovery:'+rid, {})
        return continuity.write('recovery:'+rid, dict(notes=notes.strip(), resolutions=resolutions,
            receipt_hash=_hash(run)), old['revision'])


def _hash(run):
    return hashlib.sha256(json.dumps(run, sort_keys=True).encode()).hexdigest()


def continue_run(rid, remaining_task, budget_usd, agent):
    with team._lock:
        run = team.get(rid)
        reviewed = continuity.read('recovery:'+rid, {})
        review_data = reviewed['data']
        if not run or rid in team._active or not review_data or review_data['receipt_hash'] != _hash(run):
            raise ValueError('Review the current receipt before starting a continuation.')
        if not isinstance(remaining_task, str) or not 1 <= len(remaining_task.strip()) <= 4000:
            raise ValueError('Specify only the remaining work in 1–4,000 characters.')
        # Retain every event status. Large results stay on the parent receipt.
        ledger = [dict(role=s['role'], status=s['status'], result=s['result'][:500],
                       actions=[dict(tool=e['tool'], status=e['status'], result=e.get('result','')[:250]) for e in s['evidence']]) for s in run['steps']]
        recovery = dict(parent_id=rid, review_revision=reviewed['revision'], **review_data)
        context = ('This is an explicitly reviewed continuation. Do only the remaining task. '
                   'Previous actions may already have effects; inspect current state before any write. '
                   'Never replay the original task or tool calls. Owner reconciliation is a report, not independent verification.\n' +
                   json.dumps(dict(parent_id=rid, ledger=ledger, owner_review=review_data), ensure_ascii=False))
        if len(context) > 12000:
            raise ValueError('This receipt is too large for a continuation. Start a focused task after inspecting the parent receipt.')
        request_hash = hashlib.sha256(json.dumps([rid, reviewed['revision'], remaining_task.strip(), budget_usd]).encode()).hexdigest()
        return team.submit(dict(id='continue_'+request_hash[:48], task=remaining_task.strip(), context=context,
            models=run['models'], roles=[r for r in run['roles'] if r != 'apex'], budget_usd=budget_usd,
            **({'goal_id':run['goal_id']} if run.get('goal_id') else {})), agent, recovery=recovery)
