"""Owner management of repository intake and skill development."""
from fastapi import APIRouter, Depends, Request
from dashboard.home import owner, body, invoke
from agent import repository_hub, skill_imports, skill_md, skills, skill_forge

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
