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


@router.get('/api/study/model/{model_id}/asset')
async def study_asset(model_id: str):
    try:
        path = assembly.asset_path(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if path is None:
        raise HTTPException(404, 'This study has no geometry file.')
    if not path.is_file():
        raise HTTPException(404, 'Geometry file missing. Run scripts/build_study_models.py or import it again.')
    return FileResponse(path, media_type='model/gltf-binary', headers={'Content-Encoding':'gzip'})


@router.post('/api/study/import')
async def import_study(request: Request):
    """Body: the raw .glb/.gltf bytes. Query: name, title, category, source, license, detail."""
    _check_origin(request)
    from agent import study_import
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > study_import.MAX_BYTES:
            raise HTTPException(413, f'That file is larger than {study_import.MAX_BYTES // (1024 * 1024)} MB.')
    q = request.query_params
    try:
        builtin = {m['id'] for m in assembly.library() if not m['imported']}
        manifest = study_import.import_model(bytes(raw), file_name=q.get('name', ''), title=q.get('title', ''),
                                             category=q.get('category', ''), source_url=q.get('source', ''),
                                             license=q.get('license', ''), detail=q.get('detail', 'auto'),
                                             builtin_ids=builtin)
    except study_import.StudyImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    _library_changed()
    return {'id': manifest['id'], 'title': manifest['title'], 'parts': len(manifest['parts'])}


@router.delete('/api/study/model/{model_id}')
async def remove_study(model_id: str, request: Request):
    _check_origin(request)
    from agent import study_import
    try:
        assembly.model_path(model_id)
        study_import.remove(model_id)
    except (ValueError, study_import.StudyImportError) as exc:
        raise HTTPException(400, str(exc)) from exc
    _library_changed()
    return {'removed': model_id}


@router.post('/api/study/model/{model_id}/draft-notes')
async def draft_study_notes(model_id: str, request: Request):
    _check_origin(request)
    import asyncio
    import config
    from agent import provider, study_import
    try:
        body = await _small_json(request)
        manifest = json.loads(assembly.model_path(model_id).read_text())
        record = await asyncio.to_thread(
            study_import.draft_notes, manifest,
            lambda system, user: provider.complete(config.BACKGROUND_MODEL, system, user, max_tokens=8000),
            body.get('about', ''))
    except (ValueError, TypeError, study_import.StudyImportError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:        # the model provider failed: say so plainly
        raise HTTPException(502, 'Could not reach the AI model to draft notes: ' + type(exc).__name__) from exc
    return {'drafted': len(record['parts']), 'of': len(manifest['parts'])}


def _library_changed():
    """The companion's study tool lists the library; refresh it in place."""
    try:
        from agent import core
        core.refresh_study_tool()
    except Exception:
        pass


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
