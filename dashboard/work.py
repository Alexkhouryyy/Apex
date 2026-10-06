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
    return _guard(work.update_project, pid, **{k: v for k, v in body.items() if k in ('name', 'area', 'client', 'notes', 'status')})


@router.post('/api/work/tasks')
async def new_task(request: Request):
    body = await _json(request)
    fields = {k: body[k] for k in ('title', 'quick', 'area', 'project_id', 'due', 'priority', 'notes', 'status') if k in body}
    return _guard(work.add_task, **fields)


@router.patch('/api/work/tasks/{tid}')
async def edit_task(tid: int, request: Request):
    body = await _json(request)
    return _guard(work.update_task, tid, **{k: v for k, v in body.items()
                                            if k in ('title', 'notes', 'due', 'priority', 'area', 'project_id', 'status', 'waiting_on')})


@router.delete('/api/work/tasks/{tid}')
async def remove_task(tid: int, request: Request):
    _check_origin(request)
    if not work.delete_task(tid):
        raise HTTPException(404, 'No such task.')
    return {'deleted': tid}


@router.get('/api/work/tasks/{tid}')
async def one_task(tid: int):
    task = work.get_task(tid)
    if not task:
        raise HTTPException(404, 'No such task.')
    return {**task, 'files': work.files_of(task)}


@router.post('/api/work/tasks/{tid}/apex')
async def hand_to_apex(tid: int, request: Request):
    body = await _json(request)
    if config.DASHBOARD_TOKEN and not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Only the owner (master dashboard token) can hand work to Apex.')
    budget = body.get('budget_usd', work.APEX_BUDGET)
    if type(budget) not in (int, float) or not 0.05 <= budget <= 5:
        raise HTTPException(400, 'The spending cap must be between $0.05 and $5.')
    from dashboard import server
    if not server._agent_ref:
        raise HTTPException(503, 'Apex is still starting.')
    return _guard(work.give_to_apex, tid, server._agent_ref, budget)
