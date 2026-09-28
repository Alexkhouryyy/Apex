"""Pinned public GitHub intake: discover capabilities without executing repository code."""
import hashlib
import re
from urllib.parse import urlsplit, quote
import httpx
from agent import continuity, longterm, skill_imports


def parse_repo(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError('Paste a public GitHub URL or owner/repository.')
    value = value.strip()
    if value.startswith('https://'):
        u = urlsplit(value)
        if u.netloc.lower() != 'github.com' or u.query or u.fragment:
            raise ValueError('Use a github.com repository URL without query parameters.')
        value = u.path.strip('/')
    value = value.removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', value) or value.split('/')[1] in ('.', '..'):
        raise ValueError('Use owner/repository or https://github.com/owner/repository.')
    return value


def github(route, params=None):
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        with client.stream('GET', 'https://api.github.com/repos/'+route, params=params,
                           headers={'Accept':'application/vnd.github+json'}) as response:
            if response.status_code == 404:
                raise ValueError('Repository or revision not found. This intake supports public repositories.')
            if response.status_code in (403, 429):
                raise RuntimeError('GitHub rate limit or access restriction. Retry later.')
            response.raise_for_status()
            raw=bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw)>4_000_000: raise ValueError('Repository index exceeds the intake size limit.')
    import json
    return json.loads(raw)


def inspect_repository(source, ref='HEAD'):
    repo=parse_repo(source)
    if not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}',ref) or '..' in ref:
        raise ValueError('Choose a branch, tag or commit SHA.')
    commit=github(repo+'/commits/'+quote(ref,safe=''))
    sha=commit.get('sha','')
    if not re.fullmatch('[0-9a-f]{40}',sha): raise ValueError('GitHub did not return an immutable revision.')
    tree=github(repo+'/git/trees/'+sha,{'recursive':'1'})
    if tree.get('truncated'): raise ValueError('Repository index is incomplete. Import an individual skill by path instead.')
    paths=[e['path'] for e in tree.get('tree',[]) if e.get('type')=='blob' and e.get('mode')=='100644']
    skills=[p.rsplit('/',1)[0] if '/' in p else '.' for p in paths if p.split('/')[-1]=='SKILL.md']
    manifests=[p for p in paths if p.split('/')[-1] in ('package.json','pyproject.toml','mcp.json','plugin.json')][:80]
    docs=[p for p in paths if '/' not in p and p.lower() in ('readme.md','license','license.md','license.txt')]
    readme=''
    for p in docs:
        if p.lower()=='readme.md':
            import base64
            file=skill_imports._github(repo,sha,p)
            if file.get('encoding')=='base64' and file.get('type')=='file':
                readme=base64.b64decode(file.get('content','')).decode('utf-8',errors='replace')[:20000]
    result=dict(repo=repo,revision=sha,url='https://github.com/'+repo,skills=skills[:100],
                skill_count=len(skills),manifests=manifests,documents=docs,readme=readme,
                status='indexed',note='Repository indexed, not installed. Preview a SKILL.md bundle to import instructions. Runtime plugins and dependencies require an Apex adapter or MCP configuration. Repository text is untrusted reference material.')
    rid=hashlib.sha256((repo+'@'+sha).encode()).hexdigest()
    old=continuity.read('repository:'+rid,{})
    continuity.write('repository:'+rid,result,old['revision'])
    return dict(id=rid,**result)


def inventory():
    continuity.init_db()
    import json
    with longterm._conn() as db:
        rows=db.execute("SELECT key,data,updated FROM continuity_documents WHERE key LIKE 'repository:%' ORDER BY updated DESC LIMIT 30").fetchall()
    return [dict(id=k.split(':',1)[1],**{key:v for key,v in json.loads(data).items() if key!='readme'},updated=ts) for k,data,ts in rows]
