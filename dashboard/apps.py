"""Owner-only app management. Credentials and provider account IDs stay server-side."""
import json
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from agent import apps, mcp_catalog, mcp_client
from dashboard.companion import _check_origin

router = APIRouter()


def owner(request: Request):
    if not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Sign in with the Apex owner dashboard token to manage apps.')
    if request.method != 'GET': _check_origin(request)


api = APIRouter(prefix='/api/apps', dependencies=[Depends(owner)])


async def invoke(fn, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except (apps.AppError, mcp_catalog.InstallRefused) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).warning('Apps operation failed: %s', type(exc).__name__)
        raise HTTPException(502, 'The connection could not be updated. Check its status before retrying.') from exc


async def body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16000: raise HTTPException(413, 'Request is too large.')
    try:
        data = json.loads(raw)
        if not isinstance(data, dict): raise ValueError()
        return data
    except ValueError as exc:
        raise HTTPException(400, 'Expected a JSON object.') from exc


@router.get('/apps')
def shell():
    return FileResponse(Path(__file__).parent / 'static' / 'apps.html')


@api.get('/status')
async def status(): return await invoke(apps.status)


@api.get('/catalog')
async def catalog(q: str = '', category: str = '', cursor: str = ''):
    return await invoke(apps.catalog, q, category, cursor)


@api.get('/categories')
async def categories(): return await invoke(apps.categories)


@api.post('/settings')
async def settings(request: Request):
    return await invoke(apps.configure, (await body(request)).get('key'))


@api.post('/refresh')
async def refresh(): return await invoke(apps.refresh)


@api.post('/{slug}/connect')
async def connect(slug: str): return await invoke(apps.connect, slug)


@api.post('/{slug}/enabled')
async def enabled(slug: str, request: Request):
    return await invoke(apps.set_enabled, slug, (await body(request)).get('enabled'))


@api.post('/{slug}/disconnect')
async def disconnect(slug: str): return await invoke(apps.disconnect, slug)


@api.get('/{slug}/tools')
async def tools(slug: str):
    result = await invoke(apps.tools)
    return {'items': [{k: t[k] for k in ('name', 'description', 'input_schema')}
                      for t in result if t['toolkit'] == slug]}


@api.get('/local')
async def local():
    return {'items': mcp_catalog.listing(root=apps.ROOT), 'runtime': mcp_client.status()}


def reload_local():
    from dashboard import server
    if server._agent_ref:
        count = server._agent_ref.load_mcp_tools()
        return {'loaded': count, 'note': 'Connection settings saved and tools refreshed.'}
    return {'loaded': None, 'note': 'Connection settings saved. Start Apex to use its tools.'}


@api.post('/local/reload')
async def reload(): return await invoke(reload_local)


@api.post('/local/{slug}/install')
async def install(slug: str, request: Request):
    data = await body(request)
    secrets = data.get('secrets', {})
    if not isinstance(secrets, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in secrets.items()):
        raise HTTPException(400, 'Credentials must be text fields.')
    result = await invoke(mcp_catalog.install, slug, secrets=secrets, root=apps.ROOT)
    result.update(await invoke(reload_local))
    return result


@api.post('/local/{slug}/remove')
async def remove(slug: str):
    result = await invoke(mcp_catalog.uninstall, slug, root=apps.ROOT)
    result.update(await invoke(reload_local))
    return result


router.include_router(api)
