"""Pinned, reviewable documentation-only imports. No package installation or scripts.

Repository provenance is retained with every file. Review is an owner decision,
not a claim that scanning can prove a prompt or procedure safe.
"""
import base64
import hashlib
import json
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
import httpx
from agent import continuity, skill_md

REPOS = {'openclaw': 'openclaw/openclaw', 'hermes': 'NousResearch/hermes-agent'}
_MANIFEST = '.apex-import.json'


def file_hash(path):
    # Git's Windows checkout may change LF to CRLF without changing the review.
    return hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def _github(repo, revision, path):
    url = f'https://api.github.com/repos/{repo}/contents/{path}'
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        with client.stream('GET', url, params={'ref': revision}, headers={'Accept':'application/vnd.github+json'}) as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 1_000_000:
                    raise ValueError('Upstream response is too large.')
    return json.loads(raw)


def preview(source, revision, path):
    if source not in REPOS or not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Select OpenClaw or Hermes and an immutable 40-character commit SHA.')
    if not isinstance(path, str) or not re.fullmatch(r'skills/[A-Za-z0-9_/-]+', path) or '..' in path or len(path) > 200:
        raise ValueError('Choose a skill directory under skills/.')
    repo = REPOS[source]
    files, unsupported = {}, []
    visited = 0
    started = time.monotonic()

    def fetch(folder):
        nonlocal visited
        visited += 1
        if visited > 12 or time.monotonic() - started > 60:
            raise ValueError('This skill has too many directories for a documentation-only import.')
        entries = _github(repo, revision, folder)
        if not isinstance(entries, list):
            raise ValueError('Expected a skill directory.')
        for item in entries:
            full = item.get('path', '')
            if not full.startswith(path+'/'):
                raise ValueError('Invalid upstream path.')
            rel = full[len(path)+1:]
            if rel in (_MANIFEST, 'UPSTREAM-LICENSE.txt'):
                raise ValueError('The upstream bundle uses a reserved Apex metadata filename.')
            if not all(re.fullmatch('[A-Za-z0-9_.-]+', part) and part not in ('.', '..') for part in rel.split('/')):
                raise ValueError('Unsupported file name.')
            if len(files) + len(unsupported) >= 40:
                raise ValueError('This skill contains too many files.')
            if item.get('type') == 'dir':
                fetch(full)
            elif item.get('type') != 'file' or PurePosixPath(rel).suffix.lower() not in ('.md', '.txt', '.json', '.yaml', '.yml'):
                unsupported.append(rel)
            else:
                value = _github(repo, revision, full)
                if value.get('encoding') != 'base64' or value.get('type') != 'file':
                    raise ValueError('Only ordinary UTF-8 documentation files can be imported.')
                content = base64.b64decode(value['content'], validate=False).decode('utf-8')
                files[rel] = content
                if sum(len(v.encode('utf-8')) for v in files.values()) > 300_000:
                    raise ValueError('This skill is too large to review here.')
    fetch(path)
    if 'SKILL.md' not in files:
        raise ValueError('The selected directory has no SKILL.md.')
    license_data = _github(repo, revision, 'LICENSE')
    license_text = base64.b64decode(license_data['content']).decode('utf-8')
    if len(license_text) > 30000:
        raise ValueError('License is too large.')
    fm = skill_md._parse_frontmatter(files['SKILL.md'])
    bundle = dict(source=source, repo=repo, revision=revision, path=path, files=files,
                  license=license_text, unsupported=unsupported,
                  description=fm.get('description', '')[:700],
                  review_help='Review instructions, metadata, platform requirements, tools and every support file. Apex does not supply upstream runtime tool names. Scripts and binaries block installation.')
    digest = hashlib.sha256(json.dumps(bundle, sort_keys=True).encode()).hexdigest()
    existing = continuity.read('skill-review:'+digest, {})
    if not existing['revision']:
        continuity.write('skill-review:'+digest, bundle, 0)
    return dict(id=digest, **bundle)


def install(review_id, name, review_notes):
    skill_md._safe_name(name)
    if not isinstance(review_id, str) or not re.fullmatch('[0-9a-f]{64}', review_id):
        raise ValueError('Preview the exact revision first.')
    if not isinstance(review_notes, str) or not 20 <= len(review_notes.strip()) <= 2000:
        raise ValueError('Record the required tools, platform compatibility and any dependencies in 20–2,000 characters.')
    bundle = continuity.read('skill-review:'+review_id, {})['data']
    if not bundle or bundle['unsupported']:
        raise ValueError('This import contains unsupported files or has no saved preview. Adapt it before installation.')
    root = skill_md._SKILLS_DIR
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.exists():
        raise ValueError('That skill name already exists. Choose a new name; existing skills are preserved.')
    stage = Path(tempfile.mkdtemp(prefix='.review-', dir=root))
    try:
        files = {**bundle['files'], 'UPSTREAM-LICENSE.txt': bundle['license']}
        for rel, content in files.items():
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding='utf-8', newline='\n')
        manifest = {k:bundle[k] for k in ('source','repo','revision','path','description')}
        manifest.update(enabled=True, reviewed_at=time.time(), review_notes=review_notes.strip(), review_id=review_id,
                        hashes={k:file_hash(stage/k) for k in files})
        (stage/_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    usage = skill_md._load_usage()
    usage[name] = dict(use_count=0, last_used_at=time.time())
    skill_md._save_usage(usage)
    return dict(name=name, **manifest)


def available(folder):
    manifest_path = folder / _MANIFEST
    if not manifest_path.exists():
        return True
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not manifest['enabled']:
            return False
        if 'SKILL.md' not in manifest['hashes']:
            return False
        for rel, digest in manifest['hashes'].items():
            path = folder / rel
            if not path.resolve().is_relative_to(folder.resolve()) or path.is_symlink() or file_hash(path) != digest:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def inventory():
    out = []
    for path in sorted(skill_md._SKILLS_DIR.glob('*/SKILL.md')):
        if path.parent.name.startswith('.'):
            continue
        manifest = path.parent/_MANIFEST
        try:
            metadata = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
            out.append(dict(name=path.parent.name, imported=bool(metadata), available=available(path.parent), metadata=metadata))
        except (OSError, ValueError):
            out.append(dict(name=path.parent.name, imported=True, available=False, metadata={'error':'Invalid manifest'}))
    return out


def set_enabled(name, enabled):
    path = skill_md._skill_path(name).parent/_MANIFEST
    if type(enabled) is not bool or not path.is_file():
        raise ValueError('Choose an imported skill and a boolean enabled state.')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['enabled'] = enabled
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return dict(enabled=enabled, available=available(path.parent))
