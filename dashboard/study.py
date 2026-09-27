"""Authenticated assembly data and presentation controls."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from agent import assembly, study_projects
import json
from dashboard.companion import _check_origin, _small_json

router = APIRouter()


@router.get('/api/study/camera')
def study_camera():
    """Authenticated, opt-in preview; reuse tracking's camera, never open one."""
    from agent.handtrack import active_tracker
    tracker = active_tracker()
    jpeg = tracker.latest_jpeg() if tracker else None
    if not jpeg:
        raise HTTPException(503, 'Camera mirror unavailable. Start hand tracking on your Apex laptop.')
    return Response(jpeg, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.post('/api/study/session/{sid}/hands')
async def study_hands(sid: str, request: Request):
    _check_origin(request)
    from agent import study_input
    try:
        body = await _small_json(request)
        return study_input.control(sid, body.get('owner'), body.get('action'))
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc


async def _project_body(request):
    # Notes are larger than the board's 1KB command body; stream a bounded
    # amount instead of reading an unbounded upload into memory.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 600_000:
            raise ValueError('Study project request is too large.')
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object.')
    return body


@router.get('/api/study/projects')
async def list_projects():
    return {'projects': study_projects.recent()}


@router.post('/api/study/projects')
@router.post('/api/study/projects/{project_id}')
async def save_project(request: Request, project_id: str = None):
    _check_origin(request)
    try:
        return study_projects.save(await _project_body(request), project_id)
    except study_projects.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/study/projects/{project_id}/open')
async def open_project(project_id: str, request: Request):
    _check_origin(request)
    try:
        return study_projects.open_project(project_id)
    except study_projects.Missing as exc:
        raise HTTPException(404, str(exc)) from exc
    except study_projects.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/study')
async def study_page():
    from dashboard.server import STATIC_DIR
    return FileResponse(STATIC_DIR / 'study.html')


@router.get('/api/study/model/{model_id}')
async def study_model(model_id: str):
    try:
        return dict(assembly.model(model_id), model_hash=study_projects.model_hash(model_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/api/study/models')
async def study_library():
    return {'models': assembly.library()}


# Legacy file name for the OpenMotor CAD; library subjects name theirs in `asset_file`.
_ASSET_FILES = {'openmotor-125': 'openmotor.glb.gz'}


@router.get('/api/study/model/{model_id}/asset')
async def study_asset(model_id: str):
    from dashboard.server import STATIC_DIR
    try:
        data = assembly.model(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    name = _ASSET_FILES.get(model_id) or data.get('asset_file')
    if not data.get('asset') or not isinstance(name, str) or '/' in name or '\\' in name or not name.endswith('.glb.gz'):
        raise HTTPException(404, 'This study has no geometry file.')
    path = STATIC_DIR / 'models' / name
    if not path.is_file():
        raise HTTPException(404, 'Geometry file missing. Run scripts/build_study_models.py.')
    return FileResponse(path, media_type='model/gltf-binary', headers={'Content-Encoding':'gzip'})


@router.post('/api/study/session')
async def create_study(request: Request):
    _check_origin(request)
    try:
        body = await _small_json(request)
        return assembly.create(body.get('model', 'dc-motor'))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/study/session/{sid}')
async def study_state(sid: str):
    try:
        return assembly.state(sid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/api/study/session/{sid}')
async def study_action(sid: str, request: Request):
    _check_origin(request)
    try:
        body = await _small_json(request)
        return assembly.apply(sid, body.get('action'), body.get('part'), body.get('amount'),
                              body.get('transform'), body.get('expected_revision'))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
