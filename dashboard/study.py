"""Authenticated assembly data and presentation controls."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from agent import assembly
from dashboard.companion import _check_origin, _small_json

router = APIRouter()


@router.get('/study')
async def study_page():
    from dashboard.server import STATIC_DIR
    return FileResponse(STATIC_DIR / 'study.html')


@router.get('/api/study/model/{model_id}')
async def study_model(model_id: str):
    try:
        return assembly.model(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


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
        return assembly.apply(sid, body.get('action'), body.get('part'), body.get('amount'))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
