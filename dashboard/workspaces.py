"""Authenticated operations on this host's shared active workspace."""
import sqlite3
from fastapi import APIRouter, HTTPException, Request
from agent import board_workspaces
from dashboard.companion import _check_origin, _small_json

router = APIRouter()


@router.get('/api/board/workspaces/{workspace_id}/studies')
async def linked_studies(workspace_id: str):
    try:
        return {'studies':board_workspaces.linked_studies(workspace_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(503, 'Study references are unavailable. Try again.') from exc


@router.post('/api/board/workspaces/{workspace_id}/studies')
async def link_study(workspace_id: str, request: Request):
    _check_origin(request)
    try:
        body = await _small_json(request)
        board_workspaces.link_study(workspace_id, body.get('project_id'), body.get('action'), body.get('version'))
        return {'ok':True}
    except board_workspaces.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except (sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(503, 'Could not update the study reference. Try again.') from exc


@router.get('/api/board/workspaces')
async def workspaces():
    try:
        return board_workspaces.listing()
    except (sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(503, 'Workspace storage is unavailable. Try again.') from exc


@router.post('/api/board/workspaces')
async def workspace_action(request: Request):
    _check_origin(request)
    try:
        body = await _small_json(request)
        if body.get('action') == 'create':
            return {'workspace':board_workspaces.create(body.get('name'), body.get('copy_current', False), body.get('context'))}
        if body.get('action') == 'switch':
            return {'active':board_workspaces.switch(body.get('id'), body.get('context'))}
        raise ValueError('Unknown workspace action.')
    except board_workspaces.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except (sqlite3.Error, RuntimeError) as exc:
        raise HTTPException(503, 'Could not save or open the workspace. The active board was not replaced.') from exc
