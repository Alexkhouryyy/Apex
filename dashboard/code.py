"""Apex Code page and API (agent/code_studio.py): coding sessions on your Claude
and ChatGPT plans, each on its own branch.

Coding runs tools on this PC and reads your code, so every Code route needs
the owner's master token (when a dashboard token is set), reading included;
changes also need a same-site request. The page itself is public, like every
Apex page: it holds no data until the API answers. The one exception is away
mode's /api/code/allow/{token}: a command Safe mode stopped, answered from your
phone, which any signed-in device holding that link's secret can do.
"""
import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

import config
from agent import board_workspaces, code_brain, code_studio
from dashboard.companion import _check_origin

router = APIRouter()
STATIC = Path(__file__).parent / 'static'


def _owner(request: Request) -> None:
    if config.DASHBOARD_TOKEN and not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Apex Code is for the owner only (master dashboard token).')


async def _json(request: Request, limit=60000, owner=True) -> dict:
    _check_origin(request)
    if owner:
        _owner(request)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, 'Too large.')
    try:
        body = json.loads(raw or b'{}')
    except ValueError:
        raise HTTPException(400, 'Expected JSON.')
    if not isinstance(body, dict):
        raise HTTPException(400, 'Expected an object.')
    return body


async def _do(call, *args, **kwargs):
    """Run in a thread (git and the plan checks take a moment); errors in plain words."""
    try:
        return await asyncio.to_thread(call, *args, **kwargs)
    except (code_studio.CodeError, code_brain.CodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/code')
async def code_page():
    return FileResponse(STATIC / 'code.html')


@router.get('/api/code')
async def overview(request: Request):
    _owner(request)
    return await _do(code_studio.overview)


@router.get('/api/code/models/{engine}')
async def coding_models(engine: str, request: Request, refresh: bool = False):
    _owner(request)
    if engine not in ('claude', 'chatgpt'):
        raise HTTPException(400, 'Choose Claude or ChatGPT.')
    from agent import code_catalog
    return await asyncio.to_thread(code_catalog.catalog, engine, refresh)


@router.get('/api/code/usage/{engine}')
async def coding_usage(engine: str, request: Request, refresh: bool = False):
    """Provider-reported account limits/activity; owner-only and read-only."""
    _owner(request)
    if engine not in ('claude', 'chatgpt'):
        raise HTTPException(400, 'Choose Claude or ChatGPT.')
    from agent import code_usage
    return await asyncio.to_thread(code_usage.snapshot, engine, refresh)


@router.get('/api/code/overnight')
async def overnight(request: Request):
    """What the night shift built, for the morning (agent/code_studio.overnight): each
    session waiting for you, with the proof's verdict, the second opinion and the files."""
    _owner(request)
    return {'sessions': await _do(code_studio.overnight)}


@router.get('/api/code/approvals')
async def approvals_waiting(request: Request):
    """Memories a session suggested through Apex's memory server, waiting for your OK
    (agent/code_brain.suggested). Approve and Reject are the queue's own:
    /api/staged-writes/{id}/approve and /reject."""
    _owner(request)
    return {'items': await asyncio.to_thread(code_brain.suggested)}


# Away mode: a command Safe mode stopped, answered from your phone (agent/code_studio.answer_allow).
# These two skip _owner on purpose: the link's secret is the permission, so a phone
# paired with its own device token can answer (the auth middleware still wants a
# valid token). They show nothing else about the session, can only allow once or
# say no, and are the only Code routes a device token reaches.
ALLOW_LINK = re.compile(r'[\w-]{16,64}')


async def _away(call, token: str, *args):
    if not ALLOW_LINK.fullmatch(token):
        raise HTTPException(404, "Apex doesn't know that request.")
    try:
        return await asyncio.to_thread(call, token, *args)
    except code_studio.NoSuchAllow as exc:
        raise HTTPException(404, str(exc)) from exc
    except code_studio.CodeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/code/allow/{token}')
async def allow_request(token: str):
    """What the link asks: the command, the session's title and project, the plan and until when."""
    return await _away(code_studio.pending_allow, token)


@router.post('/api/code/allow/{token}')
async def answer_allow(token: str, request: Request):
    """{"choice": "once"} runs it now; {"choice": "no"} tells the plan to find another way."""
    body = await _json(request, limit=2000, owner=False)
    who = f"{request.client.host if request.client else '?'} {request.headers.get('user-agent', '')[:80]}".strip()
    return await _away(code_studio.answer_allow, token, body.get('choice'), who)


@router.post('/api/code/projects')
async def add_project(request: Request):
    body = await _json(request)
    call = code_studio.create_project if body.get('create') is True else code_studio.add_project
    return await _do(call, body.get('path', ''), body.get('name', ''))


@router.patch('/api/code/projects/{pid}')
async def edit_project(pid: int, request: Request):
    body = await _json(request)
    if isinstance(body.get('forget_allowed'), str):
        return await _do(code_studio.forget_allowed, pid, body['forget_allowed'])
    return await _do(code_studio.update_project, pid, body.get('name'), body.get('checks'), body.get('exit_ok'))


@router.post('/api/code/sessions')
async def start_session(request: Request):
    body = await _json(request)
    if not isinstance(body.get('project_id'), int):
        raise HTTPException(400, 'Choose a project.')
    return await _do(code_studio.start, body['project_id'], body.get('prompt', ''),
                     body.get('engine', 'claude'), body.get('mode', 'safe'), body.get('model') or '',
                     body.get('effort') or '', body.get('plan') is True)


@router.get('/api/code/sessions/{sid}')
async def one_session(sid: int, request: Request):
    _owner(request)
    s = await _do(code_studio.session, sid)
    try:
        s['changes'] = await asyncio.to_thread(code_studio.changes, sid)
    except code_studio.CodeError:
        s['changes'] = {'files': [], 'plus': 0, 'minus': 0}
    return s


@router.get('/api/code/sessions/{sid}/events')
async def session_events(sid: int, request: Request, after: int = 0):
    _owner(request)
    await _do(code_studio.session, sid)
    return {'events': await asyncio.to_thread(code_studio.events, sid, max(0, after))}


@router.get('/api/code/sessions/{sid}/diff')
async def file_diff(sid: int, request: Request, path: str = ''):
    _owner(request)
    return PlainTextResponse(await _do(code_studio.diff, sid, path))


@router.post('/api/code/sessions/{sid}/messages')
async def send(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.send, sid, body.get('prompt', ''), body.get('engine'), body.get('mode'),
                     model=body.get('model'), effort=body.get('effort'), plan=body.get('plan') is True)


# 'discard' has its own route above, which takes the reason (an empty body is no reason).
@router.post('/api/code/sessions/{sid}/queue')
async def enqueue(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.enqueue, sid, body.get('prompt', ''), body.get('engine'), body.get('mode'),
                     model=body.get('model'), effort=body.get('effort'), plan=body.get('plan') is True,
                     request_id=body.get('request_id'))


@router.post('/api/code/sessions/{sid}/queue-pause')
async def pause_queue(sid: int, request: Request):
    await _json(request)
    return await _do(code_studio.pause_queue, sid)


@router.post('/api/code/sessions/{sid}/queue-resume')
async def resume_queue(sid: int, request: Request):
    await _json(request)
    return await _do(code_studio.resume_queue, sid)


@router.post('/api/code/sessions/{sid}/queue-remove')
async def remove_queued(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.remove_queued, sid, body.get('id', ''))


ACTIONS = {'stop': code_studio.stop, 'undo': code_studio.undo, 'catch-up': code_studio.catch_up,
           'discard': code_studio.discard, 'checks': code_studio.run_checks}


@router.post('/api/code/sessions/{sid}/keep')
async def keep(sid: int, request: Request):
    """Keep only what Apex saw work, unless you say {"unverified_ok": true}: without
    proof it's 409, with the proof saying why, so the page can ask you first."""
    body = await _json(request)
    try:
        return await asyncio.to_thread(code_studio.keep, sid, body.get('push') is True,
                                       body.get('unverified_ok') is not True)
    except code_studio.NotProved as exc:
        return JSONResponse({'detail': str(exc), 'proof': exc.proof}, status_code=409)
    except code_studio.CodeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/code/sessions/{sid}/discard')
async def discard(sid: int, request: Request):
    """Throw the session away, with why ({"reason": "wrong"}; none is fine): it goes
    into the project's decision log and the outcomes ledger (agent/code_brain.py)."""
    body = await _json(request)
    return await _do(code_studio.discard, sid, body.get('reason') or '')


@router.get('/api/code/sessions/{sid}/proof')
async def session_proof(sid: int, request: Request):
    """What the agent said it checked, next to what Apex saw (agent/code_studio.proof)."""
    _owner(request)
    return await _do(code_studio.proof, sid)


@router.post('/api/code/sessions/{sid}/evidence')
async def evidence(sid: int, request: Request):
    """Output you pasted from running the checks yourself: kept as yours, never as Apex's."""
    body = await _json(request, limit=code_studio.MAX_EVIDENCE * 4 + 1000)
    return await _do(code_studio.owner_evidence, sid, body.get('output', ''))


@router.post('/api/code/sessions/{sid}/allow')
async def allow(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.allow, sid, body.get('command', ''), body.get('always') is True)


@router.post('/api/code/sessions/{sid}/terminal')
async def terminal(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.terminal, sid, body.get('command', ''))


@router.get('/api/code/sessions/{sid}/live')
async def live(sid: int, request: Request):
    _owner(request)
    return code_studio.live(sid)


@router.get('/api/code/sessions/{sid}/tree')
async def session_tree(sid: int, request: Request):
    _owner(request)
    return await _do(code_studio.tree, sid)


@router.get('/api/code/projects/{pid}/tree')
async def project_tree(pid: int, request: Request):
    _owner(request)
    return await _do(code_studio.tree, None, pid)


@router.get('/api/code/projects/{pid}/brain')
async def project_brain(pid: int, request: Request, q: str = ''):
    """What a new session in this project would be told about you, line by line
    (agent/code_brain.py). A memory is forgotten with DELETE /api/memories/{id}."""
    _owner(request)
    await _do(code_studio.project, pid)
    out = await asyncio.to_thread(code_brain.brief_block, pid, q[:code_studio.MAX_PROMPT])
    out['unvouched'] = await asyncio.to_thread(code_brain.unvouched, pid)
    return out


@router.post('/api/code/memories/{mid}/vouch')
async def vouch_memory(mid: int, request: Request):
    """You say a coding memory is yours, so later sessions hear it (agent/code_brain.vouch).
    Owner-only: a device token or a model's tool call can save a memory, never vouch for one."""
    _owner(request)
    return await _do(code_brain.vouch, mid)


# Rules: corrections said once (agent/code_brain.py), for this project or for all code.
RULES_CONFLICT = 'Rules changed in another window. Reload; your text is kept.'
LOG_CONFLICT = 'The decision log changed in another window. Reload, then restore again.'


async def _rules(call, pid: int, *args, conflict=RULES_CONFLICT):
    """A rules call for a project that exists: a stale revision is 409, anything else wrong 400."""
    await _do(code_studio.project, pid)
    try:
        return await asyncio.to_thread(call, pid, *args)
    except board_workspaces.Conflict as exc:          # before ValueError: it is one
        raise HTTPException(409, conflict) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/code/projects/{pid}/rules')
async def project_rules(pid: int, request: Request):
    _owner(request)
    return await _rules(code_brain.rules, pid)


@router.post('/api/code/projects/{pid}/rules')
async def add_rule(pid: int, request: Request):
    body = await _json(request)
    return await _rules(code_brain.add_rule, pid, body.get('text'), body.get('scope', 'project'), body.get('revision'))


@router.put('/api/code/projects/{pid}/rules')
async def save_rules(pid: int, request: Request):
    body = await _json(request)
    return await _rules(code_brain.save_rules, pid, body.get('items'), body.get('revision'))


@router.get('/api/code/projects/{pid}/rules/history')
async def rules_history(pid: int, request: Request):
    _owner(request)
    return {'versions': await _rules(code_brain.rules_history, pid)}


@router.post('/api/code/projects/{pid}/rules/restore')
async def restore_rules(pid: int, request: Request):
    body = await _json(request)
    return await _rules(code_brain.restore_rules, pid, body.get('revision'), body.get('current_revision'))


# What every session taught Apex (agent/code_brain.py): the counted track record,
# and the project's decision log that Keep and Throw away write (read-only here).

@router.get('/api/code/projects/{pid}/record')
async def project_record(pid: int, request: Request, days: int = 90):
    _owner(request)
    return await _rules(code_brain.track_record, pid, max(1, min(days, 3650)))


@router.get('/api/code/projects/{pid}/decisions')
async def decision_log(pid: int, request: Request):
    _owner(request)
    return await _rules(code_brain.decision_log, pid)


@router.get('/api/code/projects/{pid}/decisions/history')
async def decision_log_history(pid: int, request: Request):
    _owner(request)
    return {'versions': await _rules(code_brain.decision_log_history, pid)}


@router.post('/api/code/projects/{pid}/decisions/restore')
async def restore_decision_log(pid: int, request: Request):
    body = await _json(request)
    return await _rules(code_brain.restore_decision_log, pid, body.get('revision'), body.get('current_revision'),
                        conflict=LOG_CONFLICT)


@router.get('/api/code/sessions/{sid}/file')
async def session_file(sid: int, request: Request, path: str = ''):
    _owner(request)
    return await _do(code_studio.read_file, path, sid)


@router.get('/api/code/projects/{pid}/file')
async def project_file(pid: int, request: Request, path: str = ''):
    _owner(request)
    return await _do(code_studio.read_file, path, None, pid)


@router.get('/api/code/sessions/{sid}/history')
async def history(sid: int, request: Request):
    _owner(request)
    return {'checkpoints': await _do(code_studio.history, sid)}


@router.get('/api/code/sessions/{sid}/commit')
async def commit(sid: int, request: Request, sha: str = ''):
    _owner(request)
    return PlainTextResponse(await _do(code_studio.commit_diff, sid, sha))


@router.get('/api/code/sessions/{sid}/stream')
async def stream(sid: int, request: Request, after: int = 0, once: bool = False):
    """Everything as it happens, one JSON object per line: stored steps ({"t":"event"}),
    the live text and command output ({"t":"live"}), and a ping every 10 s. The page
    reads it with fetch (which can carry the token) and falls back to polling."""
    _owner(request)
    await _do(code_studio.session, sid)

    async def lines():
        last, seen_live, quiet, started = max(0, after), -1, 0.0, asyncio.get_running_loop().time()
        while True:
            if await request.is_disconnected():
                return
            for e in await asyncio.to_thread(code_studio.events, sid, last):
                last = e['id']
                yield json.dumps({'t': 'event', **e}) + '\n'
                quiet = 0.0
            lv = code_studio.live(sid)
            if lv['v'] != seen_live:
                seen_live = lv['v']
                yield json.dumps({'t': 'live', **lv}) + '\n'
                quiet = 0.0
            if once:                                          # what there is now, then close (tests, slow links)
                return
            quiet += 0.15
            if quiet >= 10:
                yield '{"t":"ping"}\n'
                quiet = 0.0
            if asyncio.get_running_loop().time() - started > 600:    # the page reconnects
                return
            await asyncio.sleep(0.15)
    return StreamingResponse(lines(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/api/code/sessions/{sid}/review')
async def second_opinion(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.review, sid, body.get('engine'))


@router.post('/api/code/sessions/{sid}/{action}')
async def act(sid: int, action: str, request: Request):
    await _json(request)
    if action not in ACTIONS:
        raise HTTPException(404, 'Unknown action.')
    result = await _do(ACTIONS[action], sid)
    return {'stopped': result} if action == 'stop' else result
