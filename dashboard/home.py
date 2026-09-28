"""Owner controls for identity, project continuity, recovery and reviewed skills."""
import json
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from agent import continuity, board_workspaces, skill_imports, task_recovery, team
from dashboard.companion import _check_origin

router = APIRouter()


def owner(request: Request):
    if not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Use the owner dashboard token to open Apex Home.')
    if request.method != 'GET':
        _check_origin(request)


api = APIRouter(prefix='/api/home', dependencies=[Depends(owner)])


async def body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 24000:
            raise HTTPException(413, 'Request is too large.')
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError as exc:
        raise HTTPException(400, 'Expected a JSON object.') from exc


async def invoke(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except board_workspaces.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).warning('Home operation failed: %s', type(exc).__name__)
        raise HTTPException(503, 'This operation is unavailable. Your draft is preserved; inspect saved state before retrying.') from exc


@router.get('/home')
def shell():
    return FileResponse(Path(__file__).parent/'static'/'home.html')


def state():
    return dict(identity=continuity.identity(), workspaces=board_workspaces.listing(),
                skills=skill_imports.inventory(), runs=team.recent())


@api.get('')
async def status():
    return await invoke(state)


@api.post('/identity')
async def identity(request: Request):
    d = await body(request)
    return await invoke(continuity.save_identity, d.get('data'), d.get('revision'))


@api.get('/projects/{wid}')
async def project(wid: str):
    return await invoke(lambda: dict(project=continuity.project(wid), corrections=continuity.corrections(wid)))


@api.post('/projects/{wid}')
async def save_project(wid: str, request: Request):
    d = await body(request)
    return await invoke(continuity.save_project, wid, d.get('data'), d.get('revision'))


@api.post('/projects/{wid}/corrections')
async def corrections(wid: str, request: Request):
    d = await body(request)
    return await invoke(continuity.save_corrections, wid, d.get('items'), d.get('revision'))


@api.post('/skills/preview')
async def preview(request: Request):
    d = await body(request)
    return await invoke(skill_imports.preview, d.get('source'), d.get('revision'), d.get('path'))


@api.post('/skills/install')
async def install(request: Request):
    d = await body(request)
    return await invoke(skill_imports.install, d.get('id'), d.get('name'), d.get('notes'))


@api.post('/skills/enabled')
async def enabled(request: Request):
    d = await body(request)
    return await invoke(skill_imports.set_enabled, d.get('name'), d.get('enabled'))


def propose(rid, name, description, procedure):
    from agent import approvals, skill_md
    skill_md._safe_name(name)
    run = team.get(rid)
    if not run or run['status'] != 'done' or run['verification']['status'] != 'verified':
        raise ValueError('Reusable workflows need a completed run with passed completion contracts.')
    if not isinstance(description, str) or not 1 <= len(description) <= 500 or '\n' in description:
        raise ValueError('Enter a one-line description of at most 500 characters.')
    if not isinstance(procedure, str) or not 20 <= len(procedure) <= 8000:
        raise ValueError('Write the reusable steps and checks in 20–8,000 characters.')
    content = (f'Source: Apex task {rid}. Verification passed at the time of this proposal; '
               'recheck preconditions and results whenever this procedure is reused.\n\n' + procedure)
    return dict(result=approvals.stage('skill', dict(name=name, description=description, content=content)))


@api.post('/runs/{rid}/learn')
async def learn(rid: str, request: Request):
    d = await body(request)
    return await invoke(propose, rid, d.get('name'), d.get('description'), d.get('procedure'))


@api.post('/runs/{rid}/review')
async def review(rid: str, request: Request):
    d = await body(request)
    return await invoke(task_recovery.review, rid, d.get('notes'), d.get('resolutions'))


@api.post('/runs/{rid}/continue')
async def continue_run(rid: str, request: Request):
    from dashboard import server
    if not server._agent_ref:
        raise HTTPException(503, 'Start Apex before continuing a task.')
    d = await body(request)
    return await invoke(task_recovery.continue_run, rid, d.get('task'), d.get('budget_usd'), server._agent_ref)


router.include_router(api)
