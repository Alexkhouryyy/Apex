"""Owner management of repository intake and skill development."""
from fastapi import APIRouter, Depends, Request
from dashboard.home import owner, body, invoke
from agent import repository_hub, skill_imports, skill_md, skills, skill_forge
from agent import plugins

router=APIRouter(prefix='/api/environment',dependencies=[Depends(owner)])

@router.get('')
async def state():
    return await invoke(lambda: dict(repositories=repository_hub.inventory(),procedures=skill_imports.inventory(),
        executable=skills.list_skills(),forged=skill_forge.list_forged(),failures=skills.failure_stats()))

@router.post('/repositories')
async def repository(request: Request):
    d=await body(request)
    return await invoke(repository_hub.inspect_repository,d.get('source'),d.get('ref') or 'HEAD')

@router.post('/preview')
async def preview(request: Request):
    d=await body(request)
    return await invoke(skill_imports.preview,d.get('source'),d.get('revision'),d.get('path'))

@router.get('/skills/{name}')
async def source(name:str):
    def read():
        skill_md._safe_name(name)
        p=skill_md._skill_path(name)
        if p.is_file():return dict(kind='procedure',content=p.read_text(encoding='utf-8'))
        code=skills.read_source(name)
        if code is None:raise ValueError('Skill not found.')
        return dict(kind='executable',content=code)
    return await invoke(read)

@router.post('/develop')
async def develop(request: Request):
    d=await body(request)
    return await invoke(skill_forge.develop,d.get('description'),d.get('existing'),d.get('needs_network',False))


@router.get('/plugins')
async def plugin_inventory():
    return await invoke(plugins.inventory)


@router.post('/plugins/preview')
async def plugin_preview(request: Request):
    d=await body(request)
    return await invoke(plugins.preview,d.get('source'),d.get('ref') or 'HEAD',d.get('subdir') or '')


@router.post('/plugins/file')
async def plugin_file(request: Request):
    d=await body(request)
    return await invoke(plugins.review_file,d.get('id'),d.get('path'))


@router.post('/plugins/bundled')
async def plugin_bundled(request: Request):
    d=await body(request)
    return await invoke(plugins.preview_bundled,d.get('name'))


@router.post('/plugins/install')
async def plugin_install(request: Request):
    d=await body(request)
    return await invoke(plugins.install,d.get('id'),d.get('replace'))


@router.post('/plugins/enabled')
async def plugin_enabled(request: Request):
    d=await body(request)
    return await invoke(plugins.set_enabled,d.get('name'),d.get('enabled'),d.get('trust',False))


@router.post('/plugins/settings')
async def plugin_settings(request: Request):
    d=await body(request)
    return await invoke(plugins.configure,d.get('name'),d.get('settings'))


@router.post('/plugins/update')
async def plugin_update(request: Request):
    d=await body(request)
    return await invoke(plugins.check_update,d.get('name'))


@router.post('/plugins/change')
async def plugin_change(request: Request):
    d=await body(request)
    return await invoke(plugins.change,d.get('name'),d.get('action'))


@router.post('/plugins/provider')
async def plugin_provider(request: Request):
    d=await body(request)
    return await invoke(plugins.select_provider,d.get('kind'),d.get('selected'))
