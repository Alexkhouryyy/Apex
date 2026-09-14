"""Team endpoints inherit dashboard authentication."""
import json
from fastapi import APIRouter, HTTPException, Request
from agent import team
from dashboard.companion import _check_origin

router = APIRouter()

@router.get('/api/team')
async def list_runs():
    return {'runs': team.recent()}

@router.post('/api/team')
async def submit(request: Request):
    _check_origin(request)
    from dashboard import server
    if not server._agent_ref:
        raise HTTPException(503, 'Apex is still starting.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 30000:
            raise HTTPException(413, 'Task is too large.')
    try:
        return team.submit(json.loads(raw), server._agent_ref)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

@router.post('/api/team/{rid}/stop')
async def stop(rid: str, request: Request):
    _check_origin(request)
    if not team.get(rid):
        raise HTTPException(404, 'Task not found.')
    return {'stop_requested': team.stop(rid)}
