"""Owner-controlled measured builds; reuses the board's workspace and Forge."""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Request
from agent import board_workspaces, design_workbench
from agent.board import ContentConflict
from dashboard.companion import _check_origin, _is_owner

router = APIRouter()


def owner(request):
    if not _is_owner(request):
        raise HTTPException(403, 'Measured design tools are controlled by the owner.')


@router.get('/api/board/design/{identifier}')
async def inspect(request: Request, identifier: str):
    owner(request)
    try:
        return await asyncio.to_thread(design_workbench.inspect, identifier)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/board/design')
async def change(request: Request):
    _check_origin(request)
    owner(request)
    try:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise ValueError('Design request is too large.')
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError('Expected a design request.')
        action = body.get('action')
        if action in ('create', 'edit'):
            return await asyncio.to_thread(getattr(design_workbench, action), body)
        if action in ('check', 'export'):
            return await asyncio.to_thread(design_workbench.fabrication, body, export=action == 'export')
        raise ValueError('Choose create, edit, check or export.')
    except (ContentConflict, board_workspaces.Conflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError, design_workbench.forge.ForgeError) as exc:
        raise HTTPException(400, str(exc)) from exc
