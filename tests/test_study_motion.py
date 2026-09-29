"""Motion in the studies: the car engine's kinematics and the heart's beat.

The generator (scripts/build_study_models.py) samples each moving part over one
cycle; the page (dashboard/static/study-motion.js) only interpolates. So the
physics is checked here, on the committed manifests and the generator itself.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'study_models'))

import car_engine as ce  # noqa: E402
from study_geometry import Mesh, rot  # noqa: E402

from agent import assembly  # noqa: E402


def _motion(model_id):
    return json.loads((ROOT / 'data/assemblies' / f'{model_id}.json').read_text())['motion']


@pytest.mark.parametrize('model_id', ['car-engine', 'heart'])
def test_tracks_are_well_formed(model_id):
    m = _motion(model_id)
    ids = {p['id'] for p in assembly.model(model_id)['parts']}
    n = m['samples'] + 1
    assert m['label'] and m['cycle_seconds'] > 0 and m['tracks'] and set(m['tracks']) <= ids
    assert set(m.get('ghost', [])) <= ids
    for part, t in m['tracks'].items():
        assert len(t['pivot']) == 3, part
        for key in ('angle', 'scale', 'glow'):
            if key in t:
                assert len(t[key]) == n and all(math.isfinite(v) for v in t[key]), (part, key)
        if 'offset' in t:
            assert len(t['offset']) == n and all(len(o) == 3 for o in t['offset'])
        if 'angle' in t:
            assert abs(np.linalg.norm(t['axis']) - 1) < 1e-6
        if 'glow' in t:
            assert t['glow_color'].startswith('#') and all(0 <= v <= 1 for v in t['glow'])
    # The phases tile the whole cycle.
    edges = [(p['from'], p['to']) for p in m['phases']]
    assert edges[0][0] == 0 and edges[-1][1] == 1 and all(a[1] == b[0] for a, b in zip(edges, edges[1:]))


def test_pistons_travel_one_stroke_and_pair_up():
    t = _motion('car-engine')['tracks']
    y = {k: np.array([o[1] for o in t[f'piston-{k}']['offset']]) for k in range(1, 5)}
    for k in y:
        assert y[k].max() - y[k].min() == pytest.approx(2 * ce.THROW, abs=1e-4)
    # 1 & 4 move together, 2 & 3 together, opposite to 1 & 4.
    assert np.allclose(y[1], y[4]) and np.allclose(y[2], y[3])
    assert y[1][0] == 0 and y[1][len(y[1]) // 4] < -0.7          # top at the start, bottom half a turn later


def test_crank_turns_twice_and_cams_once_per_cycle():
    t = _motion('car-engine')['tracks']
    assert t['crankshaft']['angle'][-1] == pytest.approx(4 * math.pi, abs=1e-4)
    assert t['flywheel']['angle'] == t['crankshaft']['angle']
    for kind in ('intake', 'exhaust'):
        assert t[f'{kind}-camshaft']['angle'][-1] == pytest.approx(2 * math.pi, abs=1e-4)


def test_rod_small_end_rides_the_piston_and_big_end_the_crankpin():
    t = _motion('car-engine')['tracks']
    n = len(t['rod-1']['angle']) - 1
    for k in range(4):
        rod = t[f'rod-{k + 1}']
        piv = np.array(rod['pivot'])
        small0 = np.array([ce.CYL[k], ce.piston_at(k, 0), 0.0])
        for i in range(0, n + 1, 5):
            theta = 4 * math.pi * i / n
            Q = rot(rod['axis'], rod['angle'][i])
            small = piv + Q @ (small0 - piv) + rod['offset'][i]
            assert np.allclose(small, [ce.CYL[k], ce.piston_at(k, theta), 0], atol=1e-4), (k, i)
            assert np.allclose(piv + rod['offset'][i], ce.crankpin(k, theta), atol=1e-4), (k, i)


def test_valves_open_on_their_strokes_in_firing_order():
    t = _motion('car-engine')['tracks']
    n = len(t['valves-1-intake']['offset']) - 1
    for k in range(4):
        start = ce.CYCLE_START[k] / (4 * math.pi)
        for kind, stroke in (('intake', 0), ('exhaust', 3)):
            y = [o[1] for o in t[f'valves-{k + 1}-{kind}']['offset']]
            peak = y.index(min(y)) / n
            assert ce.stroke(k, peak) == stroke, (k, kind, peak)
            # Offsets are from where the valve was built (its lift at the cycle start); full lift is reached.
            assert ce.lift(0.0, ce.lobe_phase(k, kind)) - min(y) == pytest.approx(ce.LIFT, abs=1e-3)
    # Cylinder 1's intake peaks 115° of crank into the cycle.
    y = [o[1] for o in t['valves-1-intake']['offset']]
    assert y.index(min(y)) / n * 720 == pytest.approx(115, abs=5)


def test_cam_lobes_touch_the_tappets_all_the_way_round():
    parts, _ = ce.build()
    by_id = {p.id: p for p in parts}
    worst = 0.0
    for kind in ('intake', 'exhaust'):
        z = ce.CAM_Z[kind]
        cam = Mesh.merge(by_id[f'{kind}-camshaft'].by_material['cam'])
        for k in range(4):
            x = ce.CYL[k] - 0.15
            for i in range(0, 145, 4):
                beta = 2 * math.pi * i / 144
                pos = (rot((1, 0, 0), beta) @ (cam.pos - [0, ce.CAM_Y, z]).T).T + [0, ce.CAM_Y, z]
                low = pos[abs(pos[:, 0] - x) < 0.07][:, 1].min()
                worst = max(worst, abs(low - (2.92 - ce.lift(beta, ce.lobe_phase(k, kind)))))
    assert worst < 0.002, worst


def test_power_strokes_glow_in_firing_order_1_3_4_2():
    t = _motion('car-engine')['tracks']
    peaks = {k: np.argmax(t[f'piston-{k}']['glow'][:-1]) for k in range(1, 5)}
    order = sorted(peaks, key=peaks.get)
    i = order.index(1)
    assert order[i:] + order[:i] == [1, 3, 4, 2]
    for k in range(1, 5):                                   # a flash at the start of power, dark otherwise
        g = np.array(t[f'piston-{k}']['glow'])
        assert g.max() == 1 and (g > 0.05).mean() < 0.12


def test_heart_beats_atria_then_ventricles_with_valves_in_step():
    m = _motion('heart')
    n = m['samples']
    t = m['tracks']
    u = np.arange(n + 1) / n
    at = lambda part, key: np.array(t[part][key])
    atria, ventricles = at('right-atrium', 'scale'), at('left-ventricle', 'scale')
    assert u[atria.argmin()] < 0.15 < u[ventricles.argmin()] < 0.5       # atria first, then ventricles
    assert atria.min() < 0.95 and ventricles.min() < 0.9
    aortic, mitral = at('aortic-valve', 'glow'), at('mitral-valve', 'glow')
    ejecting = (u > 0.23) & (u < 0.42)
    filling = (u > 0.55) & (u < 0.95)
    assert (aortic[ejecting] > 0.99).all() and (aortic[filling] == 0).all()
    assert (mitral[filling] > 0.99).all() and (mitral[ejecting] < 0.01).all()
    # Never both sets open at once: blood cannot short-circuit.
    assert (np.minimum(aortic, mitral) < 0.05).all()
    assert at('conduction-system', 'glow')[0] > 0.9


def test_both_subjects_can_play_their_motion():
    for model_id in ('car-engine', 'heart'):
        sid = assembly.create(model_id)['session_id']
        assert assembly.apply(sid, 'rotate')['rotating'] is True
        assembly.apply(sid, 'explode')
        with pytest.raises(ValueError, match='Reassemble'):
            assembly.apply(sid, 'rotate')
