"""Curated assembly studies with bounded, per-viewer presentation state.

Study controls never alter board meshes or manufacture anything. Sessions and
history are temporary; an expired/restarted session returns a clear error.
"""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import json
import re
import math
from pathlib import Path
import threading
import uuid

_LOCK = threading.RLock()
_SESSIONS = OrderedDict()
MAX_SESSIONS = 32
HISTORY = 40
MODEL_DIR = Path(__file__).resolve().parents[1] / 'data' / 'assemblies'
MODEL_PATH = MODEL_DIR / 'dc-motor.json'
_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')


def _imports():
    from agent import study_import
    return study_import.study_dir()


def model_path(model_id='dc-motor'):
    """A subject in the study library: built in (data/assemblies/<id>.json) or
    imported by the person (their study folder, see agent/study_import.py)."""
    if not isinstance(model_id, str) or not _ID.fullmatch(model_id):
        raise ValueError('Unknown assembly study.')
    for root in (MODEL_DIR, _imports()):
        if (root / f'{model_id}.json').is_file():
            return root / f'{model_id}.json'
    raise ValueError('Unknown assembly study.')


def model(model_id='dc-motor'):
    from agent import study_import
    return study_import.with_notes(json.loads(model_path(model_id).read_text()))


def asset_path(model_id):
    """The subject's geometry file, or None (the DC motor is drawn by the page)."""
    path = model_path(model_id)
    data = json.loads(path.read_text())
    name = _LEGACY_ASSETS.get(model_id) or data.get('asset_file')
    if not data.get('asset') or not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.glb\.gz', name):
        return None
    base = path.parent if data.get('imported') else Path(__file__).resolve().parents[1] / 'dashboard' / 'static' / 'models'
    return base / name


_LEGACY_ASSETS = {'openmotor-125': 'openmotor.glb.gz'}


def library():
    """Every subject, for the study page's picker and the companion's tool."""
    out, seen = [], set()
    for root in (MODEL_DIR, _imports()):
        for path in sorted(root.glob('*.json')):
            if not _ID.fullmatch(path.stem) or path.stem in seen:
                continue
            try:
                m = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(m, dict) or m.get('id') != path.stem or not isinstance(m.get('parts'), list):
                continue
            seen.add(m['id'])
            out.append({'id': m['id'], 'title': m['title'], 'subtitle': m.get('subtitle', ''),
                        'category': m.get('category', 'Engineering'), 'summary': m.get('summary', ''),
                        'fidelity': m['fidelity'], 'parts': len(m['parts']), 'imported': bool(m.get('imported')),
                        'motion': bool(m.get('motion')) or m['id'] == 'dc-motor', 'order': m.get('order', 100)})
    return sorted(out, key=lambda m: (m['order'], m['title']))


def has_motion(model_id):
    return model_id == 'dc-motor' or bool(model(model_id).get('motion'))


def create(model_id='dc-motor'):
    model(model_id)
    with _LOCK:
        sid = uuid.uuid4().hex
        state = dict(session_id=sid, model=model_id, selected=None, explosion=0.0,
                     isolated=False, hidden=[], rotating=False, section=False, transforms={}, revision=0)
        _SESSIONS[sid] = dict(state=state, undo=[], redo=[])
        while len(_SESSIONS) > MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
        return deepcopy(state)


def _entry(sid):
    if not isinstance(sid, str) or sid not in _SESSIONS:
        raise ValueError('This study session expired. Open the study again.')
    _SESSIONS.move_to_end(sid)
    return _SESSIONS[sid]


def state(sid):
    with _LOCK:
        return deepcopy(_entry(sid)['state'])


def restore(snapshot):
    """Create an independent session from a validated saved presentation."""
    data = model(snapshot.get('model'))
    ids = {p['id'] for p in data['parts']}
    selected = snapshot.get('selected')
    hidden = snapshot.get('hidden')
    explosion = snapshot.get('explosion')
    if (selected is not None and selected not in ids
            or not isinstance(hidden, list) or any(p not in ids for p in hidden)
            or type(explosion) not in (int, float) or not 0 <= explosion <= 1
            or any(type(snapshot.get(k)) is not bool for k in ('isolated', 'section'))
            or snapshot['isolated'] and (selected is None or selected in hidden)):
        raise ValueError('Saved study contains an invalid view.')
    transforms = snapshot.get('transforms', {})
    if not isinstance(transforms, dict) or any(p not in ids for p in transforms):
        raise ValueError('Invalid saved component transforms.')
    transforms = {p: _transform(v) for p, v in transforms.items()}
    with _LOCK:
        s = create(data['id'])
        s.update({k: deepcopy(snapshot[k]) for k in
                  ('selected', 'hidden', 'explosion', 'isolated', 'section')})
        s['transforms'] = transforms
        # Motion is deliberately paused on restore; the saved rotor angle is
        # restored by the viewer. Undo begins with this saved view.
        _SESSIONS[s['session_id']]['state'] = s
        return deepcopy(s)


def _part(value, data):
    if not isinstance(value, str):
        raise ValueError('Choose a component first.')
    exact = next((p for p in data['parts'] if value.casefold() == p['id'].casefold()), None)
    matches = [p for p in data['parts'] if value.casefold() == p['name'].casefold()]
    if not exact and len(matches) > 1:
        raise ValueError('Several components have that name. Choose an exact component id.')
    part = exact or (matches[0] if matches else None)
    if not part:
        raise ValueError('Unknown component. Choose a name from the component list.')
    return part['id']


def _transform(value):
    if not isinstance(value, dict):
        raise ValueError('Expected a component position and rotation.')
    for key, bound in (('position', 20), ('rotation', math.tau)):
        row = value.get(key)
        if not isinstance(row, list) or len(row) != 3 or any(
                type(v) not in (int, float) or not math.isfinite(v) or abs(v) > bound for v in row):
            raise ValueError('Component transform is outside the study range.')
    return {k: list(value[k]) for k in ('position', 'rotation')}


def apply(sid, action, part=None, amount=None, transform=None, expected_revision=None):
    with _LOCK:
        entry = _entry(sid)
        old = entry['state']
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != old['revision']):
            raise ValueError('The study changed during this gesture. Movement cancelled; try again.')
        data = model(old['model'])
        new = deepcopy(old)
        if action in ('undo', 'redo'):
            stack, other = (entry['undo'], entry['redo']) if action == 'undo' else (entry['redo'], entry['undo'])
            if stack:
                other.append(deepcopy(old)); new = stack.pop()
        elif action == 'select':
            new['selected'] = _part(part, data)
            new['hidden'] = [p for p in new['hidden'] if p != new['selected']]
        elif action == 'explode':
            value = 1.0 if amount is None else amount
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError('Separation must be a number from 0 to 1.')
            new['explosion'] = float(value)
            new['rotating'] = False
        elif action == 'transform':
            chosen = _part(part, data)
            if chosen in new['hidden'] or new['isolated'] and new['selected'] != chosen:
                raise ValueError('Show the component before moving it.')
            new['transforms'][chosen] = _transform(transform)
            new.update(selected=chosen, rotating=False)
        elif action == 'reset_part':
            chosen = _part(part or new['selected'], data)
            new['transforms'].pop(chosen, None)
        elif action == 'assemble':
            new.update(explosion=0.0, isolated=False, hidden=[], rotating=False, section=False, transforms={})
        elif action == 'isolate':
            new['selected'] = _part(part or new['selected'], data)
            new['isolated'] = not new['isolated']
            new['hidden'] = [p for p in new['hidden'] if p != new['selected']]
            new['rotating'] = False
        elif action == 'hide':
            selected = _part(part or new['selected'], data)
            new['hidden'] = sorted(set(new['hidden']) | {selected})
            new['isolated'] = False
            new['selected'] = None
        elif action == 'show_all':
            new.update(hidden=[], isolated=False)
        elif action == 'rotate':
            if not has_motion(old['model']):
                raise ValueError('Illustrative motion is only available for subjects that include it.')
            if new['explosion'] or new['isolated'] or new['transforms']:
                raise ValueError('Reassemble the model before showing rotor motion.')
            new['rotating'] = not new['rotating']
        elif action == 'section':
            new['section'] = not new['section']
        else:
            raise ValueError('Unknown study action.')
        if new != old:
            if action not in ('undo', 'redo'):
                entry['undo'].append(deepcopy(old)); entry['undo'] = entry['undo'][-HISTORY:]
                entry['redo'].clear()
            new['revision'] = old['revision'] + 1
            entry['state'] = new
        return deepcopy(entry['state'])


def context(sid):
    s = state(sid)
    data = model(s['model'])
    part = next((p for p in data['parts'] if p['id'] == s['selected']), None)
    return {'state': s, 'title': data['title'], 'fidelity': data['fidelity'],
            'limitations': data['limitations'], 'selected_part': part,
            'available_parts': [{'id': p['id'], 'name': p['name']} for p in data['parts']],
            'sources': data['sources']}
