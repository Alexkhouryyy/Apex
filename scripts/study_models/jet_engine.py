"""High-bypass turbofan: an original illustrative model built from code.

Built along s (inlet at negative s), then mirrored so the fan faces the
study's default camera. Casings are 270° cutaways with the open quadrant
toward the viewer, the way display engines are cut.
"""
import math

import numpy as np

from study_geometry import (Mesh, Part, TAU, blade, blade_row, box, cylinder, revolve, ring, shell, tube,
                            catmull)

CUT = dict(start=math.pi / 2, arc=1.5 * math.pi)   # open quadrant faces +y/+z (the camera)

MATERIALS = {
    'titanium': dict(color=(0.72, 0.76, 0.80, 1), metal=0.85, rough=0.28),
    'fan-blade': dict(color=(0.56, 0.62, 0.68, 1), metal=0.9, rough=0.22),
    'nacelle': dict(color=(0.86, 0.88, 0.90, 1), metal=0.1, rough=0.45),
    'nacelle-inner': dict(color=(0.42, 0.46, 0.50, 1), metal=0.3, rough=0.6),
    'case': dict(color=(0.45, 0.50, 0.54, 1), metal=0.8, rough=0.35),
    'compressor': dict(color=(0.66, 0.71, 0.74, 1), metal=0.9, rough=0.25),
    'stator': dict(color=(0.52, 0.58, 0.62, 1), metal=0.8, rough=0.35),
    'hot': dict(color=(0.74, 0.47, 0.25, 1), metal=0.7, rough=0.4, emissive=(0.35, 0.10, 0.0)),
    'turbine': dict(color=(0.78, 0.62, 0.36, 1), metal=0.85, rough=0.3),
    'nozzle': dict(color=(0.38, 0.36, 0.34, 1), metal=0.8, rough=0.45),
    'shaft-lp': dict(color=(0.30, 0.62, 0.78, 1), metal=0.7, rough=0.3),
    'shaft-hp': dict(color=(0.86, 0.55, 0.26, 1), metal=0.7, rough=0.3),
    'fuel': dict(color=(0.84, 0.70, 0.22, 1), metal=0.8, rough=0.3),
    'gearbox': dict(color=(0.30, 0.34, 0.37, 1), metal=0.7, rough=0.5),
    'spinner': dict(color=(0.20, 0.22, 0.25, 1), metal=0.6, rough=0.3),
}


def disk(s, r0, r1, w):
    return revolve([(s - w / 2, r0), (s - w / 2, r1), (s + w / 2, r1), (s + w / 2, r0), (s - w / 2, r0)], seg=48)


def build():
    parts = []

    def P(*a, **k):
        p = Part(*a, **k); parts.append(p); return p

    # Spinner: an ogive nose that guides air into the fan's roots.
    t = np.linspace(0, 1, 18)
    prof = [(-1.56 + 0.48 * x, 0.30 * math.sqrt(max(0.0, 1 - (1 - x) ** 2))) for x in t]
    P('spinner', 'Spinner (nose cone)', 'Fan', (-1.9, 0, 0)).add('spinner', revolve(prof + [(-1.08, 0.0)], seg=48))

    # Fan: 22 wide-chord twisted blades on a hub.
    fan = P('fan', 'Fan', 'Fan', (-1.35, 0, 0))
    fan.add('titanium', disk(-0.96, 0.05, 0.31, 0.26))
    st = 8
    radii = np.linspace(0.30, 0.985, st)
    fan.add('fan-blade', *[blade(-0.96, radii, np.linspace(0.30, 0.44, st), np.linspace(0.45, 1.08, st),
                                 np.linspace(0.028, 0.008, st), TAU * k / 22, camber=0.07, n=12) for k in range(22)])

    fc = P('fan-case', 'Fan containment case', 'Fan', (-1.1, 1.6, 0))
    fc.add('case', shell([(-1.24, 1.055), (-0.66, 1.055)], [(-1.24, 1.0), (-0.66, 1.0)], seg=72, **CUT))
    for s in (-1.15, -0.96, -0.77):
        fc.add('case', revolve([(s - 0.02, 1.055), (s - 0.01, 1.075), (s + 0.01, 1.075), (s + 0.02, 1.055)], seg=72, **CUT))

    stations = [-2.0, -1.85, -1.5, -1.3, -1.25, -0.65, -0.3, 0.2, 0.55]
    outer = [1.08, 1.16, 1.21, 1.22, 1.22, 1.2, 1.16, 1.08, 1.01]
    inner = [1.02, 0.99, 0.99, 1.0, 1.065, 1.065, 1.0, 0.98, 0.975]
    nac = P('nacelle', 'Nacelle (engine cowling)', 'Structure', (-0.6, 2.6, 0))
    nac.add('nacelle', revolve(list(zip(stations, outer)), seg=80, **CUT),
            revolve([(stations[0], inner[0]), (-2.03, 1.05), (stations[0], outer[0])][::-1], seg=80, **CUT))
    nac.add('nacelle-inner', revolve(list(zip(stations, inner)), seg=80, flip=True, **CUT))
    from study_geometry import grid, frame
    e0, e1, e2 = frame((1, 0, 0))
    for a, flip in ((CUT['start'], False), (CUT['start'] + CUT['arc'], True)):
        d = math.cos(a) * e1 + math.sin(a) * e2
        Pts = np.stack([[s * e0 + r * d for s, r in zip(stations, outer)], [s * e0 + r * d for s, r in zip(stations, inner)]], 1)
        nac.add('nacelle-inner', grid(Pts, flip=flip))
    nac.add('nacelle-inner', revolve([(stations[-1], outer[-1]), (stations[-1], inner[-1])], seg=80, **CUT))

    P('ogv', 'Outlet guide vanes', 'Fan', (-0.9, 0, 0)).add(
        'stator', blade_row(-0.52, 0.64, 0.99, 36, 0.13, 0.25, 0.1, 0.008, stations=3, camber=0.08))

    cowl_s = [-0.8, -0.72, -0.5, -0.2, 0.3, 0.8, 1.2, 1.36]
    cowl = P('core-cowl', 'Core cowl & splitter', 'Structure', (0.2, -2.0, 0))
    cowl.add('nacelle', shell(list(zip(cowl_s, [0.59, 0.635, 0.66, 0.66, 0.645, 0.62, 0.585, 0.565])),
                              list(zip(cowl_s, [0.585, 0.575, 0.57, 0.56, 0.555, 0.55, 0.545, 0.535])), seg=72, **CUT))

    # Low-pressure compressor (booster): 3 stages, on the fan's spool.
    lpc = P('lpc', 'Low-pressure compressor (booster)', 'Compressor', (-0.6, 0, 0))
    lpc.add('compressor', revolve([(-0.76, 0.1), (-0.74, 0.35), (-0.44, 0.35), (-0.42, 0.12)], seg=64))
    for k, s in enumerate((-0.70, -0.60, -0.50)):
        lpc.add('compressor', blade_row(s, 0.35, 0.515, 40, 0.055, 0.55, 0.95, 0.006, phase=k * 0.05))

    # High-pressure compressor: 9 stages squeezing the air ~10x more.
    hpc = P('hpc', 'High-pressure compressor', 'Compressor', (-0.25, 0, 0))
    hs = np.linspace(-0.30, 0.30, 9)
    hub = np.linspace(0.245, 0.31, 9)
    tip = np.linspace(0.40, 0.37, 9)
    hpc.add('compressor', revolve([(-0.34, 0.1), (-0.33, 0.24)] + [(s, h) for s, h in zip(hs, hub)] + [(0.35, 0.315), (0.36, 0.1)], seg=64))
    for k, (s, h, tp) in enumerate(zip(hs, hub, tip)):
        hpc.add('compressor', blade_row(s, h, tp, 38 + 3 * k, 0.034, 0.6, 0.9, 0.004, phase=k * 0.07, stations=3))

    # Compressor casing with its stator vanes (they stay still).
    cc = P('compressor-case', 'Compressor casing & stator vanes', 'Compressor', (-0.3, 1.2, 0))
    cs = [-0.76, -0.42, -0.36, 0.36]
    ci = [0.522, 0.522, 0.405, 0.375]
    cc.add('case', shell(list(zip(cs, [r + 0.03 for r in ci])), list(zip(cs, ci)), seg=64, **CUT))
    for s in (-0.65, -0.55, -0.45):
        cc.add('stator', blade_row(s, 0.36, 0.52, 44, 0.04, -0.45, -0.3, 0.005, stations=3))
    for s, h, tp in zip(hs[:-1] + 0.037, hub[:-1], tip[:-1]):
        cc.add('stator', blade_row(s, h + 0.005, tp, 48, 0.026, -0.5, -0.35, 0.004, stations=3))

    # Annular combustor: a ring-shaped chamber where fuel burns.
    loop = catmull([(0.40, 0.30), (0.37, 0.35), (0.40, 0.405), (0.55, 0.415), (0.72, 0.395),
                    (0.73, 0.35), (0.72, 0.31), (0.55, 0.285)], n=6, closed=True)
    comb = P('combustor', 'Combustion chamber (annular)', 'Combustion', (0.2, 0, 0))
    comb.add('hot', revolve(np.vstack([loop, loop[:1]]), seg=64, **CUT))
    for s in (0.47, 0.55, 0.63):   # cooling-air rings on the liner
        comb.add('hot', ring((s, 0, 0), (1, 0, 0), 0.412, 0.006, seg=64))

    fuel = P('fuel-nozzles', 'Fuel nozzles & manifold', 'Combustion', (0.2, 0.9, 0))
    fuel.add('fuel', ring((0.38, 0, 0), (1, 0, 0), 0.53, 0.012, seg=72))
    for k in range(18):
        a = TAU * k / 18
        d = np.array([0, math.cos(a), math.sin(a)])
        fuel.add('fuel', tube([np.array([0.38, 0, 0]) + 0.53 * d, np.array([0.38, 0, 0]) + 0.44 * d,
                               np.array([0.39, 0, 0]) + 0.36 * d], 0.009, seg=8),
                 cylinder(np.array([0.39, 0, 0]) + 0.36 * d, np.array([0.41, 0, 0]) + 0.36 * d, 0.016, seg=12))

    core = P('core-case', 'Combustor & turbine casing', 'Turbine', (0.5, 1.3, 0))
    ks = [0.36, 0.76, 0.80, 1.0, 1.42]
    ki = [0.43, 0.43, 0.43, 0.445, 0.49]
    core.add('case', shell(list(zip(ks, [r + 0.03 for r in ki])), list(zip(ks, ki)), seg=64, **CUT))

    hpt = P('hpt', 'High-pressure turbine', 'Turbine', (0.55, 0, 0))
    for k, s in enumerate((0.80, 0.88)):
        hpt.add('turbine', disk(s, 0.09, 0.275, 0.035),
                blade_row(s, 0.27, 0.425, 52, 0.035, -1.0, -0.75, 0.007, phase=k * 0.06, stations=3, camber=0.14))

    vanes = P('turbine-vanes', 'Turbine nozzle guide vanes', 'Turbine', (0.6, -0.9, 0))
    vanes.add('turbine', blade_row(0.76, 0.275, 0.43, 32, 0.035, 0.9, 0.8, 0.009, stations=3, camber=0.14),
              blade_row(0.84, 0.275, 0.43, 36, 0.03, 0.9, 0.8, 0.008, stations=3, camber=0.14))
    for s in np.arange(1.03, 1.4, 0.1):
        vanes.add('turbine', blade_row(s, 0.29, 0.44 + (s - 1) * 0.1, 40, 0.035, 0.8, 0.7, 0.007, stations=3, camber=0.14))

    lpt = P('lpt', 'Low-pressure turbine', 'Turbine', (0.95, 0, 0))
    for k, s in enumerate(np.arange(0.98, 1.4, 0.1)):
        lpt.add('turbine', disk(s, 0.05, 0.29, 0.03),
                blade_row(s, 0.285, 0.44 + (s - 0.98) * 0.1, 70 + 4 * k, 0.035, -0.95, -0.7, 0.006, phase=k * 0.04, stations=3, camber=0.12))

    P('core-nozzle', 'Core exhaust nozzle', 'Exhaust', (1.25, 0, 0)).add(
        'nozzle', shell([(1.36, 0.535), (1.6, 0.515), (1.86, 0.47)], [(1.36, 0.505), (1.6, 0.49), (1.86, 0.45)], seg=64, **CUT))
    plug_t = np.linspace(0, 1, 14)
    P('exhaust-plug', 'Exhaust plug (tail cone)', 'Exhaust', (1.6, 0, 0)).add(
        'nozzle', revolve([(1.42, 0.0), (1.42, 0.28)] + [(1.42 + 0.7 * x, 0.28 * (1 - x ** 1.6) + 0.01) for x in plug_t[1:]] + [(2.13, 0.0)], seg=48))

    P('lp-shaft', 'Low-pressure shaft', 'Shafts', (0.2, -1.1, 0)).add('shaft-lp', cylinder((-1.0, 0, 0), (1.45, 0, 0), 0.045, seg=24))
    hp = P('hp-shaft', 'High-pressure shaft', 'Shafts', (0, -0.75, 0))
    hp.add('shaft-hp', shell([(-0.33, 0.1), (0.92, 0.1)], [(-0.33, 0.07), (0.92, 0.07)], seg=32),
           revolve([(0.36, 0.1), (0.42, 0.2), (0.62, 0.24), (0.8, 0.27)], seg=48))

    gb = P('gearbox', 'Accessory gearbox & tower shaft', 'Accessories', (0, -1.5, 0))
    gb.add('gearbox', box((0.0, -0.47, 0), (0.42, 0.09, 0.26)), cylinder((-0.33, -0.1, 0), (-0.2, -0.43, 0), 0.02, seg=12))
    for s in (-0.12, 0.02, 0.15):
        gb.add('gearbox', cylinder((s, -0.515, 0), (s, -0.545, 0), 0.05, seg=20))

    motion = {'label': 'Spin the spools', 'axis': [1, 0, 0], 'pivot': [0, 0, 0],
              'parts': {'spinner': 1, 'fan': 1, 'lpc': 1, 'lpt': 1, 'lp-shaft': 1, 'hpc': 1.7, 'hpt': 1.7, 'hp-shaft': 1.7}}
    return parts, motion
