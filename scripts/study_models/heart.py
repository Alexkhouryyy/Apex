"""Human heart: an original illustrative model built from code.

Anatomical axes: x = the body's left, y = up (superior), z = forward
(anterior), so the study's default camera sees the front of the heart.
Chambers are smooth deformed forms; vessels are swept tubes; the coronary
arteries are projected onto the chambers' outer surface.
"""
import math

import numpy as np

from study_geometry import Mesh, Part, TAU, blob, catmull, grid, revolve, tube, _unit

MATERIALS = {
    'lv': dict(color=(0.46, 0.07, 0.08, 1), metal=0.0, rough=0.55),
    'rv': dict(color=(0.52, 0.11, 0.14, 1), metal=0.0, rough=0.55),
    'la': dict(color=(0.55, 0.12, 0.11, 1), metal=0.0, rough=0.55),
    'ra': dict(color=(0.38, 0.11, 0.22, 1), metal=0.0, rough=0.55),
    'artery': dict(color=(0.66, 0.06, 0.06, 1), metal=0.0, rough=0.4),
    'vein': dict(color=(0.12, 0.22, 0.60, 1), metal=0.0, rough=0.4),
    'pulm-vein': dict(color=(0.56, 0.12, 0.22, 1), metal=0.0, rough=0.4),
    'valve': dict(color=(0.94, 0.86, 0.70, 1), metal=0.0, rough=0.5),
    'chordae': dict(color=(0.96, 0.93, 0.85, 1), metal=0.0, rough=0.5),
    'septum': dict(color=(0.36, 0.05, 0.07, 1), metal=0.0, rough=0.6),
    'coronary': dict(color=(0.95, 0.42, 0.10, 1), metal=0.0, rough=0.35),
    'conduction': dict(color=(1.0, 0.82, 0.25, 1), metal=0.0, rough=0.4, emissive=(0.9, 0.62, 0.05)),
}


def organic(scale):
    return lambda x, a: 1 + scale * (0.6 * np.sin(3 * a + 2.1 * x) + 0.4 * np.cos(5 * a - 3.3 * x))


def ventricle(p0, p1, R, squash, ref):
    L = float(np.linalg.norm(np.subtract(p1, p0)))
    t = np.linspace(0, 1, 30)
    prof = []
    for x in t:
        if x < 0.22:
            r = R * math.sqrt(max(0.0, 1 - (1 - x / 0.22) ** 2))
        else:
            r = R * max(0.0, 1 - ((x - 0.22) / 0.78) ** 1.7) ** 0.75
        prof.append((x * L - 0.12 * L, r))
    return revolve(prof, p0, np.subtract(p1, p0), ref=ref, seg=56, squash=squash, rmul=organic(0.03))


def vessel(points, r0, r1, n=10, seg=18):
    path = catmull(points, n)
    return tube(path, np.linspace(r0, r1, len(path)), seg=seg)


def valve(center, axis, R, leaflets, depth, cusps=False):
    """An annulus with leaflets that meet in the middle (cusps curve back like pockets)."""
    c = np.asarray(center, float)
    ax = _unit(axis)
    e1 = _unit(np.cross(ax, [0.3, 0.2, 1.0]))
    e2 = np.cross(ax, e1)
    from study_geometry import ring
    meshes = [ring(c, ax, R, R * 0.12, seg=48, tube_seg=10)]
    gap = 0.06
    for k in range(leaflets):
        a0 = TAU * k / leaflets + gap
        a1 = TAU * (k + 1) / leaflets - gap
        u = np.linspace(a0, a1, 16)[:, None]
        v = np.linspace(0, 1, 10)[None, :]
        belly = np.sin((u - a0) / (a1 - a0) * math.pi)
        radius = R * (1 - 0.88 * v * (0.55 + 0.45 * belly))
        down = depth * v * (0.6 + 0.4 * belly) * (-1 if cusps else 1)
        d = np.cos(u)[..., None] * e1 + np.sin(u)[..., None] * e2
        P = c + radius[..., None] * d + (down[..., None] if np.ndim(down) else down) * ax
        meshes.append(grid(P))
    return Mesh.merge(meshes), c, ax, e1, e2


def build():
    parts = []

    def P(*a, **k):
        p = Part(*a, **k); parts.append(p); return p

    lv_m = ventricle((0.30, 0.22, -0.20), (0.95, -1.45, 0.28), 0.64, (1.0, 0.9), (0, 0, 1))
    rv_m = ventricle((-0.32, 0.18, 0.30), (0.5, -1.02, 0.62), 0.56, (1.0, 0.72), (0.6, 0, -0.5))
    ra_m = Mesh.merge([blob((-0.75, 0.45, 0.05), [(1, 0, 0), (0, 0, 1), (0, 1, 0)], (0.46, 0.46, 0.52), bumps=organic(0.03)),
                       vessel([(-0.62, 0.62, 0.30), (-0.42, 0.76, 0.52), (-0.18, 0.8, 0.66)], 0.2, 0.06, seg=16)])
    la_m = Mesh.merge([blob((0.25, 0.56, -0.56), [(1, 0, 0), (0, 0, 1), (0, 1, 0)], (0.62, 0.42, 0.36), bumps=organic(0.03)),
                       vessel([(0.68, 0.58, -0.25), (0.86, 0.62, 0.05), (0.9, 0.58, 0.28)], 0.17, 0.05, seg=16)])

    P('left-ventricle', 'Left ventricle', 'Chambers', (1.3, -0.8, 0.1)).add('lv', lv_m)
    P('right-ventricle', 'Right ventricle', 'Chambers', (-0.3, -0.7, 1.4)).add('rv', rv_m)
    P('right-atrium', 'Right atrium', 'Chambers', (-1.3, 0.2, 0.3)).add('ra', ra_m)
    P('left-atrium', 'Left atrium', 'Chambers', (0.5, 0.6, -1.4)).add('la', la_m)

    P('septum', 'Interventricular septum', 'Chambers', (0.3, -0.4, 0.5)).add(
        'septum', blob((0.22, -0.45, 0.28), [_unit((0.45, -0.85, 0.28)), _unit((0.3, 0.0, -0.95)), _unit((0.8, 0.45, 0.4))],
                       (0.85, 0.42, 0.05), nu=36, nv=16))

    aorta = P('aorta', 'Aorta', 'Great vessels', (0.1, 1.4, 0))
    aorta.add('artery',
              vessel([(0.05, 0.35, 0.05), (0.0, 0.8, 0.16), (0.02, 1.25, 0.15), (0.2, 1.6, -0.05), (0.55, 1.64, -0.4),
                      (0.74, 1.3, -0.72), (0.76, 0.5, -0.86), (0.73, -0.6, -0.86), (0.7, -1.6, -0.8)], 0.23, 0.15, seg=24),
              vessel([(0.12, 1.55, 0.0), (-0.05, 1.95, 0.05), (-0.25, 2.4, 0.1)], 0.1, 0.08),
              vessel([(0.34, 1.63, -0.16), (0.36, 2.0, -0.13), (0.38, 2.45, -0.1)], 0.065, 0.055),
              vessel([(0.53, 1.6, -0.33), (0.72, 1.95, -0.36), (0.98, 2.22, -0.36)], 0.075, 0.065))

    pt = P('pulmonary-trunk', 'Pulmonary trunk & arteries', 'Great vessels', (0, 1.0, 1.2))
    pt.add('vein',
           vessel([(0.0, 0.3, 0.56), (0.12, 0.75, 0.5), (0.28, 1.02, 0.15)], 0.18, 0.16, seg=22),
           vessel([(0.28, 1.02, 0.15), (0.65, 1.06, -0.15), (1.08, 0.95, -0.36)], 0.12, 0.1),
           vessel([(0.28, 1.02, 0.15), (0.0, 1.0, -0.32), (-0.6, 0.95, -0.47), (-0.98, 0.9, -0.47)], 0.12, 0.1))

    P('superior-vena-cava', 'Superior vena cava', 'Great vessels', (-1.0, 1.3, 0)).add(
        'vein', vessel([(-0.72, 0.72, 0.05), (-0.7, 1.4, 0.0), (-0.68, 2.08, -0.02)], 0.15, 0.14, seg=20))
    P('inferior-vena-cava', 'Inferior vena cava', 'Great vessels', (-1.0, -1.3, 0)).add(
        'vein', vessel([(-0.72, 0.2, -0.08), (-0.69, -0.4, -0.2), (-0.66, -1.15, -0.26)], 0.16, 0.15, seg=20))
    pv = P('pulmonary-veins', 'Pulmonary veins', 'Great vessels', (0.2, 0.5, -1.9))
    for a, b in (((0.0, 0.66, -0.7), (-0.58, 0.82, -0.86)), ((0.0, 0.44, -0.72), (-0.58, 0.36, -0.92)),
                 ((0.55, 0.66, -0.72), (1.12, 0.82, -0.86)), ((0.55, 0.44, -0.72), (1.12, 0.36, -0.92))):
        mid = (np.add(a, b) / 2) + (0, 0, -0.08)
        pv.add('pulm-vein', vessel([a, mid, b], 0.08, 0.07, seg=14))

    def with_chordae(center, axis, R, leaflets, depth, tip):
        m, c, ax, e1, e2 = valve(center, axis, R, leaflets, depth)
        extra = []
        papillary = [c + tip * ax + 0.12 * (math.cos(a) * e1 + math.sin(a) * e2) for a in (0.9, 3.9)]
        for k in range(leaflets * 2):
            a = TAU * (k + 0.5) / (leaflets * 2)
            edge = c + R * 0.35 * (math.cos(a) * e1 + math.sin(a) * e2) + depth * 0.85 * ax
            pap = papillary[0] if math.cos(a - 0.9) > 0 else papillary[1]
            extra.append(tube([edge, (edge + pap) / 2, pap], 0.008, seg=6))
        for p in papillary:
            extra.append(blob(p + 0.05 * ax, [e1, e2, ax], (0.05, 0.05, 0.1), nu=16, nv=10))
        return m, Mesh.merge(extra)

    m, ch = with_chordae((-0.32, 0.12, 0.22), (0.5, -0.62, 0.4), 0.17, 3, 0.22, 0.5)
    P('tricuspid-valve', 'Tricuspid valve', 'Valves', (-0.6, -0.1, 0.6)).add('valve', m).add('chordae', ch)
    m, ch = with_chordae((0.36, 0.22, -0.2), (0.42, -0.82, 0.26), 0.18, 2, 0.24, 0.55)
    P('mitral-valve', 'Mitral valve', 'Valves', (0.8, -0.1, -0.5)).add('valve', m).add('chordae', ch)
    P('aortic-valve', 'Aortic valve', 'Valves', (0.05, 0.7, 0.2)).add(
        'valve', valve((0.05, 0.4, 0.06), (-0.08, 1, 0.2), 0.16, 3, 0.1, cusps=True)[0])
    P('pulmonary-valve', 'Pulmonary valve', 'Valves', (0.0, 0.6, 0.8)).add(
        'valve', valve((0.02, 0.38, 0.55), (0.2, 1, -0.05), 0.15, 3, 0.1, cusps=True)[0])

    # Coronary arteries run in the grooves on the heart's surface.
    shell_pts = np.concatenate([lv_m.pos, rv_m.pos, ra_m.pos, la_m.pos])
    centre = np.array([0.05, -0.25, 0.05])
    rel = shell_pts - centre
    dist = np.linalg.norm(rel, axis=1)
    dirs = rel / dist[:, None]

    def on_surface(p, lift=0.035):
        d = _unit(np.subtract(p, centre))
        near = dirs @ d > math.cos(0.07)
        return centre + d * ((dist[near].max() if near.any() else np.linalg.norm(np.subtract(p, centre))) + lift)

    def artery(points, r0, r1):
        path = catmull([on_surface(p) for p in points], 10)
        path = np.array([on_surface(p) for p in path])
        return tube(path, np.linspace(r0, r1, len(path)), seg=10)

    cor = P('coronary-arteries', 'Coronary arteries', 'Blood supply', (0.2, 0.0, 1.9))
    cor.add('coronary',
            artery([(0.2, 0.45, 0.25), (0.25, 0.1, 0.62), (0.4, -0.4, 0.72), (0.62, -0.9, 0.62), (0.9, -1.4, 0.45)], 0.04, 0.018),
            artery([(0.28, 0.42, 0.1), (0.62, 0.3, 0.05), (0.9, 0.1, -0.25), (0.75, 0.0, -0.62)], 0.035, 0.018),
            artery([(0.38, -0.05, 0.6), (0.72, -0.3, 0.45), (0.98, -0.6, 0.2)], 0.025, 0.014),
            artery([(-0.1, 0.42, 0.25), (-0.42, 0.18, 0.5), (-0.7, -0.1, 0.35), (-0.72, -0.35, -0.05), (-0.35, -0.55, -0.5), (0.1, -0.8, -0.45)], 0.04, 0.018))

    con = P('conduction-system', 'Electrical conduction system', 'Electrical system', (-0.5, 0.2, 0.9))
    sa, av = np.array([-0.62, 0.84, 0.2]), np.array([-0.16, 0.2, 0.02])
    his = np.array([0.02, 0.0, 0.14])
    con.add('conduction', blob(sa, [(1, 0, 0), (0, 1, 0), (0, 0, 1)], (0.06, 0.06, 0.05), nu=16, nv=10),
            blob(av, [(1, 0, 0), (0, 1, 0), (0, 0, 1)], (0.05, 0.04, 0.04), nu=16, nv=10),
            vessel([sa, (-0.5, 0.55, 0.15), av], 0.012, 0.012, seg=8),
            vessel([av, his], 0.02, 0.018, seg=8),
            vessel([his, (0.3, -0.5, 0.22), (0.62, -1.0, 0.28), (0.82, -1.28, 0.28)], 0.016, 0.01, seg=8),
            vessel([his, (0.1, -0.4, 0.42), (0.35, -0.85, 0.55), (0.46, -1.02, 0.56)], 0.016, 0.01, seg=8))
    return parts, motion()


# --- One heartbeat, slowed down. u is the fraction of the cycle.
BEAT_SECONDS = 1.6
SAMPLES = 96
LV_BASE, RV_BASE = np.array([0.30, 0.22, -0.20]), np.array([-0.32, 0.18, 0.30])
TRICUSPID, MITRAL = np.array([-0.32, 0.12, 0.22]), np.array([0.36, 0.22, -0.2])


def _smooth(a, b, u):
    t = min(1.0, max(0.0, (u - a) / (b - a)))
    return t * t * (3 - 2 * t)


def atria_squeeze(u):
    return _smooth(0.0, 0.07, u) * (1 - _smooth(0.11, 0.2, u))


def ventricle_squeeze(u):
    return _smooth(0.15, 0.3, u) * (1 - _smooth(0.4, 0.55, u))


def av_open(u):          # tricuspid and mitral: open while the ventricles fill
    return 1 - _smooth(0.13, 0.16, u) + _smooth(0.49, 0.53, u)


def semilunar_open(u):   # aortic and pulmonary: open while the ventricles eject
    return _smooth(0.19, 0.22, u) * (1 - _smooth(0.43, 0.46, u))


def conduction(u):       # SA node fires, then the impulse reaches the ventricles
    u %= 1.0
    return max(math.exp(-u / 0.03), 0.8 * math.exp(-((u - 0.14) / 0.025) ** 2))


def motion():
    us = [i / SAMPLES for i in range(SAMPLES + 1)]
    ventricles = (LV_BASE + RV_BASE) / 2
    squeeze = lambda f, depth: [round(1 - depth * f(u), 5) for u in us]
    open_ = lambda f: [round(f(u), 4) for u in us]
    tracks = {
        'left-ventricle': {'pivot': LV_BASE.tolist(), 'scale': squeeze(ventricle_squeeze, 0.12)},
        'right-ventricle': {'pivot': RV_BASE.tolist(), 'scale': squeeze(ventricle_squeeze, 0.12)},
        'septum': {'pivot': ventricles.tolist(), 'scale': squeeze(ventricle_squeeze, 0.1)},
        'coronary-arteries': {'pivot': ventricles.tolist(), 'scale': squeeze(ventricle_squeeze, 0.06)},
        'right-atrium': {'pivot': TRICUSPID.tolist(), 'scale': squeeze(atria_squeeze, 0.08)},
        'left-atrium': {'pivot': MITRAL.tolist(), 'scale': squeeze(atria_squeeze, 0.08)},
        'conduction-system': {'glow': open_(conduction), 'glow_color': '#ffd23a'},
    }
    for valve, f in (('tricuspid-valve', av_open), ('mitral-valve', av_open),
                     ('aortic-valve', semilunar_open), ('pulmonary-valve', semilunar_open)):
        tracks[valve] = {'glow': open_(f), 'glow_color': '#5dffa8'}
    phases = [
        (0.0, 0.15, 'Atria contract · they top up the ventricles through the open tricuspid & mitral valves'),
        (0.15, 0.2, 'Ventricles start to squeeze · all four valves shut (the "lub")'),
        (0.2, 0.45, 'Ventricles eject · aortic & pulmonary valves open; blood leaves for the body and lungs'),
        (0.45, 0.5, 'Ventricles relax · all four valves shut (the "dub")'),
        (0.5, 1.0, 'Filling · blood flows from the atria through the open tricuspid & mitral valves'),
    ]
    return {'label': 'Beat', 'cycle_seconds': BEAT_SECONDS, 'samples': SAMPLES, 'tracks': tracks,
            'hint': 'Beating, slowed down · turn on Section to cut the heart open and watch the valves light as they open',
            'phases': [{'from': a, 'to': b, 'text': t} for a, b, t in phases]}
