"""Import a real 3D model into the study library.

Takes a self-contained glTF 2.0 file (.glb, or .gltf with everything
embedded), checks it can be shown safely, splits it into study components
from its own node tree, and stores it with a manifest in the user's study
folder (~/.apex/study, or APEX_STUDY_DIR), never in the repository.

Components come from the file's node names: the part list is only as good as
the names the model's author gave. Notes start empty; `draft_notes` asks the
background model to draft what each part is and how it connects, stored in a
separate notes file so the manifest (and saved studies pinned to its hash)
never change.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import secrets
import struct
import time
from pathlib import Path

MAX_BYTES = 150 * 1024 * 1024
MAX_PARTS = 300
MANY_PARTS = 60            # above this, repeated pieces (Bolt 1..40) become one part
AUTO_PARTS = 8             # "Main assemblies": stop descending once there are this many
UNSUPPORTED = {
    'KHR_draco_mesh_compression': 'Draco compression',
    'EXT_meshopt_compression': 'meshopt compression',
    'KHR_texture_basisu': 'Basis Universal textures',
    'EXT_texture_webp': 'WebP-only textures',
    'EXT_texture_avif': 'AVIF-only textures',
}
_GENERIC = re.compile(
    r'^(?:object|node|mesh|group|root|rootnode|scene|model|sketchfab_model|gltf_scenerootnode|gltf_scene_root_node|'
    r'defaultmaterial|default|polysurface|pcube|pcylinder|psphere|null|empty|geo|geometry|primitive|shape|body|solid|'
    r'part|component|instance|transform|collada|fbx|obj)?[\s_.\-:#]*\d*(?:\.(?:fbx|obj|stl|dae|blend|3ds|max))?$',
    re.I)
_SUFFIX = re.compile(r'(?:[:.#_\s]\d+|\s*\(\d+\))+$')


class StudyImportError(ValueError):
    """A file that cannot become a study, with a message for the person."""


def study_dir() -> Path:
    root = Path(os.path.expanduser(os.getenv('APEX_STUDY_DIR', '~/.apex/study')))
    root.mkdir(parents=True, exist_ok=True)
    return root


# --- Reading and checking the file -------------------------------------------------

def read_gltf(raw: bytes) -> dict:
    """The glTF JSON of a .glb or embedded .gltf, after safety checks."""
    if len(raw) > MAX_BYTES:
        raise StudyImportError(f'That file is larger than {MAX_BYTES // (1024 * 1024)} MB.')
    if raw[:4] == b'glTF':
        if len(raw) < 12:
            raise StudyImportError('This .glb file is truncated.')
        version, length = struct.unpack_from('<II', raw, 4)
        if version != 2:
            raise StudyImportError('Only glTF 2.0 files are supported. Re-export as glTF 2.0 (.glb).')
        if len(raw) < 20:
            raise StudyImportError('This .glb file is truncated.')
        if length != len(raw):
            raise StudyImportError('This .glb file is truncated or damaged (its length does not match).')
        size, kind = struct.unpack_from('<I4s', raw, 12)
        if kind != b'JSON' or 20 + size > len(raw):
            raise StudyImportError('This .glb file is damaged (no readable description chunk).')
        text, binary = raw[20:20 + size], True
    else:
        text, binary = raw, False
    try:
        doc = json.loads(text.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StudyImportError('That is not a glTF file. Export your model as glTF 2.0 binary (.glb) — '
                           'from Blender: File → Export → glTF 2.0; STL, OBJ, FBX and STEP need converting first.') from None
    if not isinstance(doc, dict) or not str(doc.get('asset', {}).get('version', '')).startswith('2'):
        raise StudyImportError('Only glTF 2.0 files are supported. Re-export as glTF 2.0 (.glb).')
    missing = [UNSUPPORTED[e] for e in doc.get('extensionsRequired', []) if e in UNSUPPORTED]
    if missing:
        raise StudyImportError('This file needs ' + ' and '.join(missing) + ', which the study viewer cannot decode yet. '
                           'Re-export without compression (in Blender, untick "Compression"), or run '
                           '"npx @gltf-transform/cli copy in.glb out.glb" to decompress it.')
    for kind in ('buffers', 'images'):
        for i, item in enumerate(doc.get(kind, [])):
            uri = item.get('uri') if isinstance(item, dict) else None
            if uri is None:
                if kind == 'buffers' and not binary:
                    raise StudyImportError('This .gltf file is missing its data. Export as a single .glb instead.')
                continue
            if not isinstance(uri, str) or not uri.startswith('data:'):
                raise StudyImportError('This file refers to separate files (' + ('textures' if kind == 'images' else 'geometry data') +
                                   '). Export it as one self-contained .glb file.')
    nodes, meshes = doc.get('nodes'), doc.get('meshes')
    if not isinstance(nodes, list) or not nodes or not isinstance(meshes, list) or not meshes:
        raise StudyImportError('This file has no 3D geometry in it.')
    return doc


# --- Turning the node tree into study components -----------------------------------

def _clean(name) -> str:
    if not isinstance(name, str):
        return ''
    name = re.sub(r'[\x00-\x1f]', '', name).strip()
    base = _SUFFIX.sub('', name).strip(' _-.:') or name
    if ' ' not in base:
        base = base.replace('_', ' ')
    return re.sub(r'\s+', ' ', base)[:80].strip()


def _meaningful(name: str) -> bool:
    return bool(name) and not _GENERIC.match(name.replace(' ', '_')) and re.search(r'[A-Za-z]', name) is not None and not name.startswith('=>')


def _slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:48] or 'part'


def components(doc: dict, detail: str = 'auto') -> list[dict]:
    """Disjoint node subtrees that each contain geometry, named for people.

    `auto` ("main assemblies") descends through wrapper nodes until there are
    enough pieces to study; `fine` ("every piece") goes down to each mesh.
    """
    nodes = doc['nodes']
    scene = doc.get('scenes', [{}])[doc.get('scene', 0) if isinstance(doc.get('scene'), int) else 0]
    roots = [r for r in scene.get('nodes', range(len(nodes))) if isinstance(r, int) and 0 <= r < len(nodes)]
    kids = {i: [c for c in (n.get('children') or []) if isinstance(c, int) and 0 <= c < len(nodes)] for i, n in enumerate(nodes)}
    memo = {}

    def has_mesh(i, seen=()):
        if i in memo:
            return memo[i]
        if i in seen:
            return False
        memo[i] = 'mesh' in nodes[i] or any(has_mesh(c, seen + (i,)) for c in kids[i])
        return memo[i]

    def expandable(i):
        return 'mesh' not in nodes[i] and any(has_mesh(c) for c in kids[i])

    frontier = [r for r in roots if has_mesh(r)]
    if not frontier:
        raise StudyImportError('This file has no 3D geometry in its scene.')
    while any(expandable(i) for i in frontier):
        if detail != 'fine' and len(frontier) >= AUTO_PARTS:
            break
        nxt = []
        for i in frontier:
            nxt.extend([c for c in kids[i] if has_mesh(c)] if expandable(i) else [i])
        # Unwrapping named objects ("Front wheel" > "Object_4") adds no pieces: stop there.
        if len(nxt) == len(frontier) and any(_meaningful(_clean(nodes[i].get('name'))) for i in frontier):
            break
        if detail != 'fine' and len(nxt) > MANY_PARTS:
            break
        frontier = nxt

    parent = {c: i for i, cs in kids.items() for c in cs}

    def label(i):
        own = _clean(nodes[i].get('name'))
        if _meaningful(own):
            return own
        up = parent.get(i)
        while up is not None:                   # "Object_4" wrapped by "Front wheel"
            if len([c for c in kids[up] if has_mesh(c)]) != 1:
                break
            if _meaningful(_clean(nodes[up].get('name'))):
                return _clean(nodes[up].get('name'))
            up = parent.get(up)
        mesh = nodes[i].get('mesh')
        if isinstance(mesh, int) and 0 <= mesh < len(doc['meshes']):
            m = _clean(doc['meshes'][mesh].get('name'))
            if _meaningful(m):
                return m
        for c in kids[i]:                       # a wrapper around one named thing
            if has_mesh(c) and _meaningful(_clean(nodes[c].get('name'))):
                return _clean(nodes[c].get('name'))
        return ''

    def group(i):
        up = parent.get(i)
        while up is not None:
            g = _clean(nodes[up].get('name'))
            if _meaningful(g) and up not in roots:
                return g
            up = parent.get(up)
        return 'Components'

    items = [{'nodes': [i], 'name': label(i), 'group': group(i), 'raw': str(nodes[i].get('name') or '')[:120]} for i in frontier]
    if len(items) > MANY_PARTS:                 # 40 identical bolts become "Bolt ×40"
        merged = {}
        for it in items:
            key = (it['group'], it['name']) if it['name'] else ('', id(it))
            merged.setdefault(key, []).append(it)
        items = []
        for same in merged.values():
            first = dict(same[0], nodes=[n for it in same for n in it['nodes']])
            if len(same) > 1:
                first['name'] = f"{first['name']} ×{len(same)}"
            items.append(first)
    if len(items) > MAX_PARTS:                  # still too many: keep the file studyable in chunks
        size = -(-len(items) // MAX_PARTS)
        items = [{'nodes': [n for it in items[k:k + size] for n in it['nodes']], 'name': f'Pieces {k // size + 1}',
                  'group': 'Components', 'raw': ''} for k in range(0, len(items), size)]
    unnamed = 0
    counts = {}
    for it in items:
        if not it['name']:
            unnamed += 1
            it['name'] = f'Part {unnamed}'
        counts[it['name']] = counts.get(it['name'], 0) + 1
    seen_names, ids = {}, set()
    for it in items:
        if counts[it['name']] > 1:
            seen_names[it['name']] = seen_names.get(it['name'], 0) + 1
            it['name'] = f"{it['name']} #{seen_names[it['name']]}"
        base = _slug(it['name'])
        pid, k = base, 2
        while pid in ids:
            pid, k = f'{base}-{k}', k + 1
        ids.add(pid)
        it['id'] = pid
    return items


# --- Storing it as a study subject -------------------------------------------------

def _text(value, limit, default=''):
    value = re.sub(r'[\x00-\x1f]', ' ', value).strip() if isinstance(value, str) else ''
    return value[:limit] or default


def import_model(raw: bytes, *, file_name: str = '', title: str = '', category: str = '', source_url: str = '',
                 license: str = '', detail: str = 'auto', builtin_ids=()) -> dict:
    doc = read_gltf(raw)
    parts = components(doc, 'fine' if detail == 'fine' else 'auto')
    stem = Path(file_name or 'model').stem
    title = _text(title, 80) or _text(stem.replace('_', ' ').replace('-', ' '), 80, 'Imported model')
    category = _text(category, 40, 'Imported')
    source_url = _text(source_url, 500)
    if source_url and not re.match(r'^https?://', source_url):
        raise StudyImportError('The source link must start with http:// or https://.')
    root = study_dir()
    while True:
        model_id = f'{_slug(title)[:40]}-{secrets.token_hex(3)}'
        if model_id not in builtin_ids and not (root / f'{model_id}.json').exists():
            break
    single = len(parts) == 1
    manifest = {
        'id': model_id, 'revision': '1.0', 'geometry_revision': '1', 'title': title,
        'subtitle': f'Imported model · {len(parts)} part' + ('' if single else 's'), 'category': category, 'order': 50,
        'summary': f'Imported from {_text(file_name, 120, "a glTF file")}.',
        'fidelity': 'Imported model · shown as provided', 'caption': 'Imported model · parts named from the file',
        'imported': {'file_name': _text(file_name, 200), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                     'at': int(time.time()), 'detail': 'fine' if detail == 'fine' else 'auto'},
        'asset': f'/api/study/model/{model_id}/asset', 'asset_file': f'{model_id}.glb.gz', 'auto_explode': True,
        'validation': {'purpose': 'Studying an imported 3D model',
                       'geometry': 'The imported file, shown as provided',
                       'dimensions': 'From the file; units and scale not verified',
                       'materials': 'As in the file; not verified',
                       'physics': 'No simulation',
                       'review': 'No notes yet; component names come from the file'},
        'limitations': ('Imported by you. Components are the pieces the file itself defines, named from its '
                        'node names, so some may be generic or grouped. Scale, materials and accuracy are the '
                        "file's own and not verified. Check the model's licence before sharing it."
                        + (' This file is a single piece, so it cannot be taken apart.' if single else '')),
        'sources': [{'id': 'file', 'title': ('Source · ' if source_url else 'Imported file · ') + (
            _text(file_name, 120) or title), 'url': source_url, 'license': _text(license, 80)}],
        'parts': [{'id': p['id'], **({'node': p['nodes'][0]} if len(p['nodes']) == 1 else {'nodes': p['nodes']}),
                   'name': p['name'], 'group': p['group'],
                   'purpose': 'Not described yet. Use Draft notes, or ask Céline about it.',
                   'connection': f'Part of the imported model’s “{p["group"]}” group.',
                   'model_note': f'Named from the file: “{p["raw"]}”.' if p['raw'] else 'Unnamed in the file.',
                   'source': 'file'} for p in parts],
    }
    (root / f'{model_id}.glb.gz').write_bytes(gzip.compress(raw, 6, mtime=0))
    (root / f'{model_id}.json').write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + '\n')
    return manifest


def remove(model_id: str) -> None:
    root = study_dir()
    path = root / f'{model_id}.json'
    if not path.is_file() or not json.loads(path.read_text()).get('imported'):
        raise StudyImportError('Only imported models can be removed.')
    for suffix in ('.json', '.glb.gz', '.notes.json'):
        (root / f'{model_id}{suffix}').unlink(missing_ok=True)


# --- AI-drafted notes, kept beside the manifest ------------------------------------

NOTES_SYSTEM = (
    'You write short study notes for the components of a 3D model someone is learning from. For each '
    'component you get its id, its name from the file and the group it belongs to. Write "purpose" (what '
    'it is and does, one or two plain sentences) and "connection" (how it relates to the parts around it, '
    'one sentence). Names come from the file and can be cryptic: when unsure what a part is, say what it '
    'most likely is and that this is a guess. Never invent dimensions, specifications, part numbers or '
    'figures. For anatomy, teach; never give medical advice. Reply with JSON only: '
    '{"summary": "...", "parts": {"<id>": {"purpose": "...", "connection": "..."}}}')


def _json_block(text: str) -> dict:
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise StudyImportError('The notes came back unreadable. Try again.')
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise StudyImportError('The notes came back unreadable. Try again.') from None
    if not isinstance(data, dict):
        raise StudyImportError('The notes came back unreadable. Try again.')
    return data


def draft_notes(manifest: dict, complete, about: str = '') -> dict:
    """Ask the model for notes on every component; store and return them.
    `complete(system, user) -> str` is the model call (injected for tests)."""
    if not manifest.get('imported'):
        raise StudyImportError('Notes can only be drafted for imported models.')
    parts = manifest['parts']
    notes, summary = {}, ''
    for k in range(0, len(parts), 80):
        batch = [{'id': p['id'], 'name': p['name'], 'group': p['group']} for p in parts[k:k + 80]]
        user = json.dumps({'model': manifest['title'], 'what_the_person_said_it_is': _text(about, 300),
                           'file_name': manifest['imported'].get('file_name', ''), 'components': batch}, ensure_ascii=False)
        data = _json_block(complete(NOTES_SYSTEM, user))
        known = {p['id'] for p in batch}
        for pid, entry in (data.get('parts') or {}).items():
            if pid in known and isinstance(entry, dict):
                purpose, connection = _text(entry.get('purpose'), 400), _text(entry.get('connection'), 300)
                if purpose:
                    notes[pid] = {'purpose': purpose, 'connection': connection or None}
        summary = summary or _text(data.get('summary'), 300)
    if not notes:
        raise StudyImportError('No usable notes came back. Try again.')
    record = {'by': 'ai', 'at': int(time.time()), 'summary': summary, 'parts': notes}
    (study_dir() / f"{manifest['id']}.notes.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + '\n')
    return record


def with_notes(manifest: dict) -> dict:
    """The manifest as shown: drafted notes overlaid, labelled as AI-drafted."""
    path = study_dir() / f"{manifest['id']}.notes.json"
    if not manifest.get('imported') or not path.is_file():
        return manifest
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return manifest
    out = dict(manifest, parts=[dict(p) for p in manifest['parts']], validation=dict(manifest['validation']))
    for p in out['parts']:
        n = record.get('parts', {}).get(p['id'])
        if isinstance(n, dict) and n.get('purpose'):
            p['purpose'] = n['purpose']
            if n.get('connection'):
                p['connection'] = n['connection']
            p['notes_by'] = 'ai'
    if record.get('summary'):
        out['summary'] = record['summary']
    out['caption'] = 'Imported model · AI-drafted notes, not reviewed'
    out['validation']['review'] = 'Notes AI-drafted from the file’s part names; not reviewed'
    out['notes'] = {'by': 'ai', 'at': record.get('at')}
    return out
