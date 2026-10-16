"""Measured edits and checked fabrication of the exact build shown on a board.

Recipes use centimetres, glTF metres, and the workbench/Forge millimetres.
An image is a reference; it never supplies an inferred physical scale.
"""
from __future__ import annotations

import copy
import math
import re
import threading
from dataclasses import asdict

import numpy as np

from agent import assets, board_parts, board_workspaces, build3d, forge, props
from agent.board import ContentConflict, get_board

_SOURCE = re.compile(r'^created/([a-z0-9-]+)/v(\d+)\.glb$')
_export_lock = threading.Lock()


def vector(value, name, factor=1):
    if (not isinstance(value, list) or len(value) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(name + ' needs three finite numbers.')
    return [v * factor for v in value]


def _card(board, identifier, expected_src=None):
    card = next((c for c in board.cards() if c['id'] == identifier), None)
    if not card or card['kind'] != 'model':
        raise ValueError('Select a model on this workspace first.')
    if expected_src is not None and expected_src != card['src']:
        raise ContentConflict('This model changed. Your draft is preserved; reload the saved version before applying it.')
    match = _SOURCE.fullmatch(card['src'])
    recipe = board_parts.recipe_for_src(card['src']) if match else None
    if not recipe:
        raise ValueError('This model has no editable build recipe. Use Study to inspect imported models, or create a measured build here.')
    return card, recipe[1], match.group(1), int(match.group(2))


def _part(parts, index):
    if type(index) is not int or not 0 <= index < len(parts):
        raise ValueError('Select an existing component.')
    return parts[index]


def _state(board, card, parts, version):
    return {'workspace': board.workspace_context(), 'card': card, 'source': card['src'],
            'version': version, 'extent_mm': [round(v * 10, 4) for v in build3d.extent_cm(parts)],
            'parts': [{'index': i, 'name': p['name'], 'shape': p['shape'],
                       'size_mm': [round(v * 10, 4) for v in p['size']],
                       'position_mm': [round(v * 10, 4) for v in p['at']],
                       'rotation_deg': p['rotate'], 'color': p['color']}
                      for i, p in enumerate(parts)]}


def inspect(identifier):
    board = get_board()
    with board._lock:
        card, parts, _, version = _card(board, identifier)
        return _state(board, card, parts, version)


def create(body):
    board = get_board()
    title = body.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError('Give the design a name of 1–80 characters.')
    title = ' '.join(title.split())
    size = vector(body.get('size_mm'), 'Dimensions in millimetres', .1)
    if body.get('shape') == 'enclosure':
        # Separate, editable components. This is an example assembly, not a
        # boolean-cut CAD solid or a claim that a particular circuit fits.
        diameter, height, depth = size
        if abs(diameter - depth) > 1e-6 or min(size) < .4:
            raise ValueError('A round enclosure needs equal X/Z dimensions and dimensions of at least 4 mm.')
        thickness = min(.2, height / 4)
        rows = [dict(name='Base', shape='cylinder', size=[diameter, thickness, depth], at=[0, thickness / 2, 0], color='#249ea5'),
                dict(name='Wall', shape='tube', size=[diameter, height - thickness, depth],
                     at=[0, (height + thickness) / 2, 0], hole=.8, color='#51c7cd'),
                dict(name='Lid', shape='cylinder', size=[diameter, thickness, depth],
                     at=[diameter * 1.3, thickness / 2, 0], color='#a4dee1')]
    else:
        if body.get('shape') not in build3d.SHAPES:
            raise ValueError('Choose a supported primitive or the enclosure example.')
        rows = [dict(name=title, shape=body['shape'], size=size, color='#51c7cd')]
    rows = build3d.validate(rows)
    with board._lock:
        board_workspaces.check_context(board, body.get('workspace'))
        if assets.find_by_title(title):
            raise ValueError('A design with this name already exists. Choose a new name or edit its existing model.')
        built = build3d.build(title, rows)
        card = board.add('model', title, src=built['src'])
        board.select(card.id)
        return inspect(card.id)


def edit(body):
    board = get_board()
    with board._lock:
        board_workspaces.check_context(board, body.get('workspace'))
        card, parts, _, _ = _card(board, body.get('id'), body.get('expected_src'))
        if not isinstance(body.get('expected_src'), str):
            raise ValueError('Reload the saved model before editing.')
        idle, why = board.hands_idle()
        if not idle:
            raise ContentConflict('Release the object before applying dimensions: ' + why)
        index = body.get('part')
        _part(parts, index)
        rows = copy.deepcopy(parts)
        changes = body.get('changes')
        if not isinstance(changes, dict) or not changes or set(changes) - {'name', 'size_mm', 'position_mm', 'rotation_deg'}:
            raise ValueError('Change the component name, dimensions, position or rotation.')
        for field, key, factor in [('size_mm', 'size', .1), ('position_mm', 'at', .1), ('rotation_deg', 'rotate', 1)]:
            if field in changes:
                rows[index][key] = vector(changes[field], field, factor)
        if 'name' in changes:
            if not isinstance(changes['name'], str) or not 1 <= len(changes['name'].strip()) <= 60:
                raise ValueError('A component name needs 1–60 characters.')
            rows[index]['name'] = changes['name'].strip()
        rows = build3d.validate(rows)
        board.rebuild(card['id'], rows)
        current = inspect(card['id'])
        board.select(card['id'], {'kind': 'part', 'mesh': index, 'face': 0,
                                 'name': rows[index]['name'], 'src': current['source']})
        return current


def _mesh(parts, index):
    selected = parts if index is None else [_part(parts, index)]
    triangles = []
    for p in selected:
        points, _, faces = build3d.part_mesh(p)
        triangles.append(points[faces.reshape(-1, 3)] * 1000)
    # Put the chosen scope on its own build plate without changing its size,
    # orientation, or the saved assembly's positions.
    tris = np.concatenate(triangles)[..., [0, 2, 1]]
    tris[..., 1] *= -1  # glTF Y-up -> manufacturing Z-up, preserving winding
    low, high = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    tris = tris - np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
    return forge.Mesh(tris, source='Apex measured build', assumed='glTF metres converted to millimetres; scope placed on build plate')


def fabrication(body, *, export=False):
    board = get_board()
    with board._lock:
        board_workspaces.check_context(board, body.get('workspace'))
        if not isinstance(body.get('expected_src'), str):
            raise ValueError('Reload the saved model before checking or exporting.')
        card, parts, slug, version = _card(board, body.get('id'), body['expected_src'])
        index = body.get('part')
        if index is not None:
            _part(parts, index)
        parts = copy.deepcopy(parts)
    mesh = _mesh(parts, index)
    settings = forge.machine_settings()
    report = forge.validate(mesh, **settings)
    result = {'source': card['src'], 'version': version, 'part': index,
              'verdict': report.verdict, 'ok': report.ok, 'description': report.describe(),
              'findings': [asdict(f) for f in report.findings], 'stats': report.stats,
              'machine': settings,
              'scope': parts[index]['name'] if index is not None else 'Whole assembly'}
    if export:
        fmt = body.get('format', '3mf')
        if fmt not in forge.FORMATS:
            raise ValueError('Choose 3MF or STL.')
        if not report.ok:
            return result  # unknown and failed checks never produce a file
        with _export_lock, board._lock:
            board_workspaces.check_context(board, body.get('workspace'))
            _card(board, card['id'], card['src'])
            filename = assets.next_filename(slug, ext=fmt)
            dest = assets.asset_root(slug) / filename
            forge.export(mesh, dest, fmt=fmt, title=card['title'], report=report)
            try:
                assets.add_version(slug, filename, command={'tool': 'apex_forge', 'action': 'export',
                                   'format': fmt, 'from_version': version, 'part': index}, parent=version)
            except Exception:
                dest.unlink(missing_ok=True)
                raise
            result['file'] = f'created/{slug}/{filename}'
    return result
