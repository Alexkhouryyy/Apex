"""Inline-four car engine (DOHC, 16 valves): an original illustrative model.

Crankshaft along x at y = 0; cylinders stand up the y axis; intake on +z,
exhaust on -z. The block's cylinder liners are cut open toward the viewer.
"""
import math

import numpy as np

from study_geometry import (Mesh, Part, TAU, belt_path, box, catmull, cylinder, extrude, grid, hull2d, revolve,
                            ring, shell, tube)

CYL = [-1.35, -0.45, 0.45, 1.35]
THROW, ROD, BORE = 0.4, 1.3, 0.4
CRANK = [0.0, math.pi, math.pi, 0.0]           # inline-four: 1 & 4 up, 2 & 3 down
INTAKE_LOBE = [0.0, 1.5 * math.pi, 0.5 * math.pi, math.pi]   # firing order 1-3-4-2, cam at half speed

MATERIALS = {
    'block': dict(color=(0.60, 0.63, 0.66, 1), metal=0.6, rough=0.5),
    'liner': dict(color=(0.36, 0.38, 0.41, 1), metal=0.8, rough=0.35),
    'head': dict(color=(0.72, 0.74, 0.76, 1), metal=0.6, rough=0.42),
    'gasket': dict(color=(0.74, 0.46, 0.26, 1), metal=0.8, rough=0.35),
    'cover': dict(color=(0.11, 0.12, 0.13, 1), metal=0.3, rough=0.7),
    'piston': dict(color=(0.80, 0.82, 0.84, 1), metal=0.8, rough=0.3),
    'ring': dict(color=(0.22, 0.23, 0.25, 1), metal=0.8, rough=0.3),
    'crank': dict(color=(0.64, 0.68, 0.72, 1), metal=0.9, rough=0.25),
    'rod': dict(color=(0.52, 0.56, 0.60, 1), metal=0.9, rough=0.3),
    'cam': dict(color=(0.78, 0.80, 0.82, 1), metal=0.95, rough=0.2),
    'valve': dict(color=(0.70, 0.72, 0.74, 1), metal=0.9, rough=0.25),
    'spring': dict(color=(0.24, 0.52, 0.66, 1), metal=0.7, rough=0.3),
    'ceramic': dict(color=(0.95, 0.95, 0.93, 1), metal=0.0, rough=0.3),
    'coil': dict(color=(0.16, 0.16, 0.17, 1), metal=0.2, rough=0.6),
    'accent': dict(color=(0.78, 0.13, 0.12, 1), metal=0.2, rough=0.5),
    'rubber': dict(color=(0.08, 0.08, 0.09, 1), metal=0.0, rough=0.8),
    'sprocket': dict(color=(0.58, 0.62, 0.66, 1), metal=0.9, rough=0.3),
    'intake': dict(color=(0.30, 0.33, 0.36, 1), metal=0.5, rough=0.5),
    'exhaust': dict(color=(0.50, 0.37, 0.30, 1), metal=0.7, rough=0.5),
    'pan': dict(color=(0.20, 0.21, 0.23, 1), metal=0.5, rough=0.6),
    'flywheel': dict(color=(0.40, 0.43, 0.46, 1), metal=0.9, rough=0.35),
    'filter': dict(color=(0.12, 0.30, 0.66, 1), metal=0.4, rough=0.4),
}

Y = (0, 1, 0)


def piston_y(k):
    a = CRANK[k]
    return THROW * math.cos(a) + math.sqrt(ROD ** 2 - (THROW * math.sin(a)) ** 2)


def crankpin(k):
    return np.array([CYL[k], THROW * math.cos(CRANK[k]), THROW * math.sin(CRANK[k])])


def disc_x(x0, x1, r, center=(0, 0), seg=40):
    return cylinder((x0, center[0], center[1]), (x1, center[0], center[1]), r, seg=seg)


def gear(x0, x1, yz, r, teeth):
    m = [disc_x(x0, x1, r, yz, seg=48)]
    for k in range(teeth):
        a = TAU * k / teeth
        c = np.array([(x0 + x1) / 2, yz[0] + (r + 0.012) * math.cos(a), yz[1] + (r + 0.012) * math.sin(a)])
        R = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
        m.append(box(c, (x1 - x0, 0.03, 0.022), R))
    return Mesh.merge(m)


def ribbon(x0, x1, circles, thick=0.025):
    """A flat belt around pulleys, in the y/z plane between x0 and x1."""
    path = belt_path(circles)
    c = path.mean(0)
    nxt, prv = np.roll(path, -1, 0), np.roll(path, 1, 0)
    t = nxt - prv
    n = np.stack([t[:, 1], -t[:, 0]], 1)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    n *= np.sign(((path - c) * n).sum(1))[:, None]
    q = path + thick * n
    loop = lambda x, pts: np.stack([np.full(len(pts), x), pts[:, 0], pts[:, 1]], 1)
    P = np.stack([loop(x0, path), loop(x1, path), loop(x1, q), loop(x0, q)], 1)
    return grid(P, wrap_u=True, wrap_v=True)


def build():
    parts = []

    def P(*a, **k):
        p = Part(*a, **k); parts.append(p); return p

    # --- Block: crankcase, walls, bulkheads and liners cut open toward you.
    blk = P('block', 'Engine block', 'Block', (0, 0, 0))
    blk.add('block',
            box((0, 0.85, -0.69), (3.9, 2.5, 0.12)),                  # back wall
            box((0, 0.25, 0.69), (3.9, 1.3, 0.12)),                   # front skirt (cut away above)
            box((-1.9, 0.85, 0), (0.1, 2.5, 1.5)), box((1.9, 0.85, 0), (0.1, 2.5, 1.5)),
            box((0, 2.06, -0.61), (3.9, 0.08, 0.28)))                 # deck, back strip
    for x in (-1.8, -0.9, 0.0, 0.9, 1.8):                             # main-bearing bulkheads
        blk.add('block', box((x, 0.18, -0.4), (0.12, 0.36, 0.42)), box((x, 0.18, 0.4), (0.12, 0.36, 0.42)),
                box((x, 0.28, 0), (0.12, 0.16, 0.38)))
    for c in CYL:
        blk.add('liner', shell([(0.35, 0.47), (2.1, 0.47)], [(0.35, BORE), (2.1, BORE)], origin=(c, 0, 0), axis=Y,
                               seg=48, start=0.9, arc=TAU - 1.8))

    caps = P('main-caps', 'Main bearing caps', 'Block', (0, -1.1, 0))
    for x in (-1.8, -0.9, 0.0, 0.9, 1.8):
        caps.add('block', box((x, -0.17, -0.25), (0.12, 0.34, 0.12)), box((x, -0.17, 0.25), (0.12, 0.34, 0.12)),
                 box((x, -0.28, 0), (0.12, 0.12, 0.38)))
        for z in (-0.25, 0.25):
            caps.add('crank', cylinder((x, -0.34, z), (x, -0.4, z), 0.045, seg=6))

    # --- Crankshaft: journals, crankpins, webs with counterweights.
    ck = P('crankshaft', 'Crankshaft', 'Rotating assembly', (0, -0.8, 0))
    ck.add('crank', disc_x(-2.45, -1.9, 0.09), disc_x(-1.92, -1.57, 0.16), disc_x(1.57, 1.97, 0.16), disc_x(1.95, 2.0, 0.3))
    for k, c in enumerate(CYL):
        a = CRANK[k]
        pin = crankpin(k)
        ck.add('crank', cylinder((c - 0.13, pin[1], pin[2]), (c + 0.13, pin[1], pin[2]), 0.14, seg=32))
        cw = [(0.46 * math.cos(a + math.pi + t), 0.46 * math.sin(a + math.pi + t)) for t in np.linspace(-1.1, 1.1, 14)]
        arm = [(pin[1] + 0.19 * math.cos(t), pin[2] + 0.19 * math.sin(t)) for t in np.linspace(0, TAU, 16, endpoint=False)]
        web = hull2d(np.array(cw + arm))
        for side in (-1, 1):
            ck.add('crank', extrude(web, (c + side * 0.18 - 0.05, 0, 0), (1, 0, 0), 0.1))
        if k < 3:
            ck.add('crank', disc_x(c + 0.2, CYL[k + 1] - 0.2, 0.16))

    # --- Pistons (each its own part) and connecting rods.
    rods = P('rods', 'Connecting rods', 'Rotating assembly', (0, 0.35, 0))
    for k, c in enumerate(CYL):
        yp = piston_y(k)
        top = yp + 0.3
        pst = P(f'piston-{k + 1}', f'Piston {k + 1}', 'Rotating assembly', (0, 1.0, 0))
        pst.add('piston', revolve([(top - 0.55, 0), (top - 0.55, 0.385), (top - 0.02, 0.39), (top, 0.37), (top + 0.01, 0.2), (top + 0.01, 0)],
                                  origin=(c, 0, 0), axis=Y, seg=48))
        for dy in (0.07, 0.13, 0.19):
            pst.add('ring', ring((c, top - dy, 0), Y, 0.39, 0.012, seg=48, tube_seg=6))
        pst.add('crank', cylinder((c - 0.3, yp, 0), (c + 0.3, yp, 0), 0.055, seg=16))
        pin = crankpin(k)
        small, big = np.array([c, yp, 0.0]), pin
        axis = (small - big) / np.linalg.norm(small - big)
        rods.add('rod', shell([(-0.07, 0.2), (0.07, 0.2)], [(-0.07, 0.145), (0.07, 0.145)], origin=big, axis=(1, 0, 0), seg=32),
                 shell([(-0.06, 0.1), (0.06, 0.1)], [(-0.06, 0.057), (0.06, 0.057)], origin=small, axis=(1, 0, 0), seg=24),
                 box((big + small) / 2, (0.1, np.linalg.norm(small - big) - 0.28, 0.12),
                     np.stack([[1, 0, 0], axis, np.cross([1, 0, 0], axis)], 1)))
        for z in (-0.12, 0.12):
            rods.add('rod', cylinder(big + (0.0, -0.18 if CRANK[k] == 0 else 0.18, z), big + (0.0, -0.25 if CRANK[k] == 0 else 0.25, z), 0.03, seg=6))

    # --- Head gasket, cylinder head, valves, camshafts, cover, ignition.
    gk = P('head-gasket', 'Head gasket', 'Cylinder head', (0, 1.4, 0))
    gk.add('gasket', box((0, 2.11, -0.62), (3.9, 0.02, 0.26)), box((0, 2.11, 0.62), (3.9, 0.02, 0.26)),
           box((-1.9, 2.11, 0), (0.1, 0.02, 1.5)), box((1.9, 2.11, 0), (0.1, 0.02, 1.5)))
    for c in CYL:
        gk.add('gasket', shell([(2.1, 0.5), (2.12, 0.5)], [(2.1, 0.41), (2.12, 0.41)], origin=(c, 0, 0), axis=Y, seg=48))

    hd = P('cylinder-head', 'Cylinder head', 'Cylinder head', (0, 2.2, 0))
    hd.add('head', box((0, 2.51, 0), (3.9, 0.78, 1.44)))
    for x in (-1.8, -0.9, 0.0, 0.9, 1.8):
        for z in (-0.22, 0.22):
            hd.add('head', box((x, 2.96, z), (0.14, 0.12, 0.2)))
    for c in CYL:
        hd.add('head', box((c, 2.5, 0.76), (0.34, 0.2, 0.1)))
        hd.add('head', cylinder((c, 2.5, -0.72), (c, 2.5, -0.8), 0.12, seg=24))

    vv = P('valves', 'Valves, springs & tappets', 'Valvetrain', (0, 1.75, 0))
    for c in CYL:
        for z in (-0.22, 0.22):
            for dx in (-0.15, 0.15):
                x = c + dx
                vv.add('valve', revolve([(2.14, 0), (2.14, 0.11), (2.17, 0.11), (2.26, 0.025), (2.82, 0.025), (2.82, 0)],
                                        origin=(x, 0, z), axis=Y, seg=20))
                vv.add('valve', cylinder((x, 2.82, z), (x, 2.92, z), 0.085, seg=20))
                helix = [(x + 0.06 * math.cos(t), 2.52 + 0.28 * t / (5 * TAU), z + 0.06 * math.sin(t)) for t in np.linspace(0, 5 * TAU, 90)]
                vv.add('spring', tube(helix, 0.011, seg=6))

    cams = P('camshafts', 'Camshafts (intake & exhaust)', 'Valvetrain', (0, 2.6, 0))
    for z, phase0 in ((0.22, 0.0), (-0.22, -1.75)):
        cams.add('cam', disc_x(-2.1, 1.95, 0.05, (3.02, z), seg=20))
        for k, c in enumerate(CYL):
            for dx in (-0.15, 0.15):
                ph = INTAKE_LOBE[k] + phase0
                prof = [(0.1 + 0.075 * max(0.0, math.cos(t - ph)) ** 3) * np.array([math.cos(t), math.sin(t)])
                        for t in np.linspace(0, TAU, 40, endpoint=False)]
                prof = [(3.02 - p[0], z + p[1]) for p in prof]
                cams.add('cam', extrude(prof, (c + dx - 0.06, 0, 0), (1, 0, 0), 0.12))

    cover = P('valve-cover', 'Valve cover', 'Cylinder head', (0, 3.5, 0))
    t = np.linspace(0, math.pi, 22)
    prof = [(2.92, 0.72)] + [(3.1 + 0.28 * math.sin(a) ** 0.6, 0.72 * math.cos(a)) for a in t] + [(2.92, -0.72)]
    cover.add('cover', extrude(prof, (-1.95, 0, 0), (1, 0, 0), 3.9, smooth=False))
    cover.add('accent', cylinder((0.8, 3.33, 0.42), (0.8, 3.4, 0.42), 0.1, seg=24))

    ign = P('ignition', 'Spark plugs & ignition coils', 'Ignition', (0, 3.1, 0))
    for c in CYL:
        ign.add('crank', cylinder((c, 2.16, 0), (c, 2.5, 0), 0.04, seg=16), cylinder((c, 2.5, 0), (c, 2.6, 0), 0.06, seg=6))
        ign.add('ceramic', cylinder((c, 2.6, 0), (c, 2.98, 0), 0.036, seg=16))
        ign.add('coil', cylinder((c, 2.98, 0), (c, 3.42, 0), 0.055, seg=16), box((c, 3.48, 0), (0.28, 0.14, 0.22)))
        ign.add('accent', box((c + 0.1, 3.48, 0.13), (0.07, 0.07, 0.06)))

    # --- Breathing: intake and exhaust manifolds.
    im = P('intake-manifold', 'Intake manifold & throttle body', 'Air & exhaust', (0, 0.7, 1.5))
    im.add('intake', cylinder((-1.85, 2.3, 1.45), (1.8, 2.3, 1.45), 0.26, seg=32),
           cylinder((-2.2, 2.3, 1.45), (-1.85, 2.3, 1.45), 0.19, seg=32))
    for c in CYL:
        im.add('intake', tube(catmull([(c, 2.35, 1.25), (c, 2.72, 1.12), (c, 2.64, 0.9), (c, 2.5, 0.8)], 8), 0.1, seg=16))

    ex = P('exhaust-manifold', 'Exhaust manifold', 'Air & exhaust', (0, 0.3, -1.5))
    for c in CYL:
        ex.add('exhaust', tube(catmull([(c, 2.5, -0.78), (c, 2.45, -1.0), (c * 0.7, 1.95, -1.25), (c * 0.3, 1.4, -1.32), (0, 1.0, -1.32)], 8), 0.085, seg=14))
    ex.add('exhaust', tube(catmull([(0, 1.0, -1.32), (0.2, 0.3, -1.3), (0.3, -0.6, -1.25)], 8), 0.13, seg=18))

    # --- Front: timing belt and accessory drive. Back: flywheel.
    tb = P('timing-belt', 'Timing belt & sprockets', 'Drive', (-1.3, 0, 0))
    pulleys = [(0.0, 0.0, 0.13), (3.02, 0.22, 0.2), (3.02, -0.22, 0.2), (1.6, 0.45, 0.09), (1.1, -0.42, 0.12)]
    tb.add('sprocket', gear(-2.16, -2.08, (0, 0), 0.11, 18), gear(-2.16, -2.08, (3.02, 0.22), 0.18, 34),
           gear(-2.16, -2.08, (3.02, -0.22), 0.18, 34), disc_x(-2.17, -2.07, 0.09, (1.6, 0.45)), disc_x(-2.17, -2.07, 0.12, (1.1, -0.42)))
    tb.add('rubber', ribbon(-2.165, -2.075, [(y, z, r + 0.005) for y, z, r in pulleys]))

    acc = P('accessory-drive', 'Crank pulley, alternator & drive belt', 'Drive', (-2.0, 0, 0.4))
    acc.add('crank', disc_x(-2.44, -2.28, 0.3, seg=48))
    acc.add('rubber', disc_x(-2.4, -2.32, 0.305, seg=48))
    acc.add('block', cylinder((-2.2, 1.2, 1.05), (-1.45, 1.2, 1.05), 0.25, seg=32))
    for x in np.linspace(-2.1, -1.55, 6):
        acc.add('block', ring((x, 1.2, 1.05), (1, 0, 0), 0.25, 0.012, seg=32, tube_seg=6))
    acc.add('sprocket', disc_x(-2.38, -2.2, 0.08, (1.2, 1.05)), disc_x(-2.38, -2.3, 0.08, (0.8, -0.6)))
    acc.add('rubber', ribbon(-2.37, -2.31, [(0.0, 0.0, 0.31), (1.2, 1.05, 0.085), (0.8, -0.6, 0.085)]))

    fw = P('flywheel', 'Flywheel', 'Drive', (1.4, 0, 0))
    fw.add('flywheel', disc_x(2.0, 2.12, 0.92, seg=72), disc_x(2.12, 2.16, 0.4, seg=48))
    for k in range(96):
        a = TAU * k / 96
        R = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
        fw.add('flywheel', box((2.06, 0.935 * math.cos(a), 0.935 * math.sin(a)), (0.1, 0.035, 0.03), R))
    for k in range(6):
        a = TAU * k / 6
        fw.add('crank', cylinder((2.12, 0.22 * math.cos(a), 0.22 * math.sin(a)), (2.17, 0.22 * math.cos(a), 0.22 * math.sin(a)), 0.035, seg=6))

    # --- Lubrication: oil pan and filter.
    pan = P('oil-pan', 'Oil pan (sump)', 'Lubrication', (0, -1.5, 0))
    top, bot = (-0.4, 1.92, 0.74), (-1.25, 1.7, 0.56)
    corners = lambda y, hx, hz: [(-hx, y, -hz), (hx, y, -hz), (hx, y, hz), (-hx, y, hz)]
    ring_top, ring_bot = np.array(corners(*top)), np.array(corners(*bot))
    for i in range(4):
        j = (i + 1) % 4
        pan.add('pan', grid(np.array([[ring_top[i], ring_bot[i]], [ring_top[j], ring_bot[j]]]), flip=True))
    pan.add('pan', box((0, -1.25, 0), (3.4, 0.01, 1.12)), box((0, -0.42, 0), (3.95, 0.04, 1.6)))
    pan.add('crank', cylinder((0.8, -1.25, 0), (0.8, -1.32, 0), 0.06, seg=6))

    of = P('oil-filter', 'Oil filter', 'Lubrication', (0, -0.2, -1.2))
    of.add('filter', cylinder((1.2, 0.35, -0.75), (1.2, 0.35, -1.25), 0.17, seg=32))
    for z in np.linspace(-0.85, -1.2, 7):
        of.add('filter', ring((1.2, 0.35, z), (0, 0, 1), 0.17, 0.008, seg=32, tube_seg=6))

    return parts, None
