"""Durable assembly-study snapshots in Apex's existing SQLite database.

Projects are shared by dashboard-token holders. Optimistic versions prevent
lost updates across tabs/processes. Source model hashes prevent silently
reinterpreting a saved study after the model's definition changes.
"""
from copy import deepcopy
import hashlib
import json
import math
import time
import uuid

from agent import assembly, longterm


class Conflict(ValueError):
    pass


class Missing(ValueError):
    pass


def ensure_db():
    with longterm._conn() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS study_projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
            model_hash TEXT NOT NULL, snapshot TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""")


def model_hash():
    return hashlib.sha256(assembly.MODEL_PATH.read_bytes()).hexdigest()


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _workspace(value, model):
    if not isinstance(value, dict):
        raise ValueError('Expected study notes and camera view.')
    notes = value.get('notes', {})
    ids = {p['id'] for p in model['parts']} | {'overview'}
    if (not isinstance(notes, dict) or any(k not in ids or not isinstance(v, str)
            or len(v) > 8000 for k, v in notes.items())):
        raise ValueError('Each component note must be text, at most 8,000 characters.')
    camera = value.get('camera')
    if (not isinstance(camera, dict) or any(not isinstance(camera.get(k), list)
            or len(camera[k]) != 3 or any(not _number(n, -1000, 1000) for n in camera[k])
            for k in ('position', 'target'))):
        raise ValueError('Camera position and target must be finite 3D coordinates.')
    distance = math.dist(camera['position'], camera['target'])
    if not 2.4 <= distance <= 30.1:
        raise ValueError('Camera distance is outside the study range.')
    angle = value.get('rotor_angle', 0)
    if not _number(angle, -math.tau, math.tau):
        raise ValueError('Invalid saved rotor angle.')
    return deepcopy(dict(notes=notes, camera=camera, rotor_angle=angle))


def save(body, project_id=None):
    fingerprint = model_hash()
    if body.get('model_hash') != fingerprint:
        raise Conflict('The model changed since this page loaded. Reload before saving a new study.')
    name = body.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError('Give this study a name of 1–120 characters.')
    s = assembly.state(body.get('session_id'))
    if type(body.get('session_revision')) is not int or body['session_revision'] != s['revision']:
        raise Conflict('The view changed before saving. Wait for it to update, then save again.')
    workspace = _workspace(body.get('workspace'), assembly.model(s['model']))
    # Live ids and undo history are not part of a saved project.
    view = {k: v for k, v in s.items() if k not in ('session_id', 'revision', 'rotating')}
    snapshot = json.dumps(dict(view=view, workspace=workspace), ensure_ascii=False)
    now = time.time()
    ensure_db()
    with longterm._conn() as db:
        if project_id is None:
            project_id, version = uuid.uuid4().hex, 1
            db.execute('INSERT INTO study_projects VALUES (?,?,?,?,?,?,?)',
                       (project_id, name.strip(), version, fingerprint, snapshot, now, now))
        else:
            version = body.get('version')
            if type(version) is not int or version < 1:
                raise ValueError('A saved project version is required.')
            result = db.execute('''UPDATE study_projects
                SET name=?,version=version+1,model_hash=?,snapshot=?,updated_at=?
                WHERE id=? AND version=? AND model_hash=?''',
                (name.strip(), fingerprint, snapshot, now, project_id, version, fingerprint))
            if result.rowcount != 1:
                raise Conflict('This project changed elsewhere or uses a different model revision. '
                               'Save a copy to keep your work, or reopen the latest project.')
            version += 1
    return dict(id=project_id, name=name.strip(), version=version, updated_at=now,
                model_hash=fingerprint)


def recent():
    ensure_db()
    with longterm._conn() as db:
        rows = db.execute('''SELECT id,name,version,updated_at,model_hash
                             FROM study_projects ORDER BY updated_at DESC LIMIT 100''').fetchall()
    fingerprint = model_hash()
    return [dict(zip(('id', 'name', 'version', 'updated_at', 'model_hash'), row),
                 compatible=row[4] == fingerprint) for row in rows]


def open_project(project_id):
    ensure_db()
    with longterm._conn() as db:
        row = db.execute('SELECT name,version,model_hash,snapshot,updated_at FROM study_projects WHERE id=?',
                         (project_id,)).fetchone()
    if row is None:
        raise Missing('Saved study not found.')
    name, version, fingerprint, raw, updated = row
    if fingerprint != model_hash():
        raise Conflict('This study uses a different model revision. Its saved data is preserved; '
                       'automatic migration is not available yet.')
    snapshot = json.loads(raw)
    workspace = _workspace(snapshot['workspace'], assembly.model(snapshot['view']['model']))
    s = assembly.restore(snapshot['view'])
    return dict(project=dict(id=project_id, name=name, version=version,
                             model_hash=fingerprint, updated_at=updated),
                state=s, workspace=workspace)
