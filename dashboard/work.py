"""Work page and API (agent/work.py): projects and tasks across job, studies,
business and software, the Today view, and handing a task to Apex. Handing
work to Apex lets it write files and use tools, so that needs the owner's
master token; reading and editing tasks needs any signed-in device."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config
from agent import work
from dashboard.companion import _check_origin

router = APIRouter()
STATIC = Path(__file__).parent / 'static'


async def _json(request: Request, limit=20000):
    _check_origin(request)
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


def _guard(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except work.OwnerRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except work.WorkError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/work')
async def work_page():
    return FileResponse(STATIC / 'work.html')


@router.get('/api/work')
async def overview():
    return {'areas': [{'id': a, 'name': work.AREA_NAMES[a]} for a in work.AREAS],
            'projects': work.list_projects(), 'tasks': work.list_tasks(), 'today': work.today_view()}


@router.post('/api/work/projects')
async def new_project(request: Request):
    body = await _json(request)
    return _guard(work.add_project, body.get('name', ''), body.get('area', 'other'), body.get('client', ''), body.get('notes', ''))


@router.patch('/api/work/projects/{pid}')
async def edit_project(pid: int, request: Request):
    body = await _json(request)
    return _guard(work.update_project, pid, by_owner=_is_owner(request), **{k: v for k, v in body.items()
                                               if k in ('name', 'area', 'client', 'notes', 'status', 'code_project_id')})


@router.post('/api/work/tasks')
async def new_task(request: Request):
    body = await _json(request)
    fields = {k: body[k] for k in ('title', 'quick', 'area', 'project_id', 'due', 'priority', 'notes', 'status', 'apex_ok') if k in body}
    return _guard(work.add_task, by_owner=_is_owner(request), **fields)


@router.patch('/api/work/tasks/{tid}')
async def edit_task(tid: int, request: Request):
    body = await _json(request)
    changes = {k: v for k, v in body.items()
               if k in ('title', 'notes', 'due', 'priority', 'area', 'project_id', 'status', 'waiting_on', 'apex_ok')}
    return _guard(work.update_task, tid, by_owner=_is_owner(request), **changes)


@router.delete('/api/work/tasks/{tid}')
async def remove_task(tid: int, request: Request):
    _check_origin(request)
    if not _guard(work.delete_task, tid, by_owner=_is_owner(request)):
        raise HTTPException(404, 'No such task.')
    return {'deleted': tid}


@router.get('/api/work/tasks/{tid}')
async def one_task(tid: int):
    task = work.get_task(tid)
    if not task:
        raise HTTPException(404, 'No such task.')
    return {**task, 'files': work.files_of(task)}


def _is_owner(request: Request):
    return not config.DASHBOARD_TOKEN or bool(getattr(request.state, 'is_master', False))


def _owner(request: Request, what: str):
    if not _is_owner(request):
        raise HTTPException(403, f'Only the owner (master dashboard token) can {what}.')


@router.post('/api/work/tasks/{tid}/apex')
async def hand_to_apex(tid: int, request: Request):
    body = await _json(request)
    _owner(request, 'hand work to Apex')
    budget = body.get('budget_usd', work.APEX_BUDGET)
    if type(budget) not in (int, float) or not 0.05 <= budget <= 5:
        raise HTTPException(400, 'The spending cap must be between $0.05 and $5.')
    engine = body.get('engine', 'api')
    from agent import work_engines
    if engine not in work_engines.ENGINES:
        raise HTTPException(400, 'Choose claude, chatgpt or api.')
    from dashboard import server
    if not server._agent_ref:
        raise HTTPException(503, 'Apex is still starting.')
    return _guard(work.give_to_apex, tid, server._agent_ref, budget, engine=engine)


@router.post('/api/work/tasks/{tid}/apex/stop')
async def stop_apex(tid: int, request: Request):
    await _json(request)
    _owner(request, 'stop Apex')
    return _guard(work.stop_apex, tid)


# The always-on agent (agent/work_agent.py). Changing it lets Apex act on its
# own, so that needs the owner; any signed-in device can see what it's doing.

@router.get('/api/work/agent')
async def agent_status():
    from agent import work_agent
    return work_agent.status()


@router.put('/api/work/agent')
async def agent_settings(request: Request):
    body = await _json(request)
    _owner(request, 'change the always-on agent')
    from agent import work_agent
    if body.pop('clear_limits', False) is True:
        work_agent.clear_limits()
    _guard(work_agent.update_settings, **body)
    return work_agent.status()


@router.post('/api/work/agent/check')
async def check_plans(request: Request):
    """Ask Claude Code and Codex how they're signed in. Uses none of your plan."""
    await _json(request)
    _owner(request, 'check the plans')
    from agent import work_agent
    import asyncio
    return await asyncio.to_thread(work_agent.check_plans)
