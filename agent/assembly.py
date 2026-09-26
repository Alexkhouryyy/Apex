"""Curated assembly studies with bounded, per-viewer presentation state.

Study controls never alter board meshes or manufacture anything. Sessions and
history are temporary; an expired/restarted session returns a clear error.
"""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path
import threading
import uuid

_LOCK = threading.RLock()
_SESSIONS = OrderedDict()
MAX_SESSIONS = 32
HISTORY = 40
MODEL_PATH = Path(__file__).resolve().parents[1] / 'data' / 'assemblies' / 'dc-motor.json'


def model(model_id='dc-motor'):
    if model_id != 'dc-motor':
        raise ValueError('Only the brushed DC motor study is available yet.')
    return json.loads(MODEL_PATH.read_text())


def create(model_id='dc-motor'):
    model(model_id)
    with _LOCK:
        sid = uuid.uuid4().hex
        state = dict(session_id=sid, model=model_id, selected=None, explosion=0.0,
                     isolated=False, hidden=[], rotating=False, section=False, revision=0)
        _SESSIONS[sid] = dict(state=state, undo=[], redo=[])
        while len(_SESSIONS) > MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
        return deepcopy(state)


def _entry(sid):
    if not isinstance(sid, str) or sid not in _SESSIONS:
        raise ValueError('This study session expired. Open a new motor study.')
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
    with _LOCK:
        s = create(data['id'])
        s.update({k: deepcopy(snapshot[k]) for k in
                  ('selected', 'hidden', 'explosion', 'isolated', 'section')})
        # Motion is deliberately paused on restore; the saved rotor angle is
        # restored by the viewer. Undo begins with this saved view.
        _SESSIONS[s['session_id']]['state'] = s
        return deepcopy(s)


def _part(value, data):
    if not isinstance(value, str):
        raise ValueError('Choose a component first.')
    part = next((p for p in data['parts'] if value.casefold() in (p['id'].casefold(), p['name'].casefold())), None)
    if not part:
        raise ValueError('Unknown component. Choose a name from the component list.')
    return part['id']


def apply(sid, action, part=None, amount=None):
    with _LOCK:
        entry = _entry(sid)
        old = entry['state']
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
        elif action == 'assemble':
            new.update(explosion=0.0, isolated=False, hidden=[], rotating=False, section=False)
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
            if new['explosion'] or new['isolated']:
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
