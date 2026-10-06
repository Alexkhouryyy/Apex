/* Apex Mk I: the armour, built in code (no model file, nobody's likeness).
   Gunmetal plates with a clear coat over a dark woven under-suit, teal energy
   seams, an angular helmet with eye slits, swept fins and a vocal grille, and
   the orb as a reactor core in the chest. buildSuit() returns the joint
   groups avatar.js animates, the parts that glow, and every plate with its
   resting place so the suit can assemble itself. */
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

export const ENERGY = {idle: [0.35, 2.3, 1.75], listen: [0.45, 1.5, 2.6], think: [1.45, 0.95, 2.6], speak: [0.6, 2.6, 2.0]};

function wovenTexture() {
  // A fine hexagonal weave for the under-suit, used as a bump map.
  const size = 128, c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  g.fillStyle = '#808080'; g.fillRect(0, 0, size, size);
  g.strokeStyle = '#2a2a2a'; g.lineWidth = 2;
  const r = 9, h = Math.sqrt(3) * r;
  for (let row = -1; row < size / h + 1; row++) {
    for (let col = -1; col < size / (1.5 * r) + 1; col++) {
      const x = col * 1.5 * r, y = row * h + (col % 2 ? h / 2 : 0);
      g.beginPath();
      for (let k = 0; k < 6; k++) { const a = Math.PI / 3 * k; g.lineTo(x + r * Math.cos(a), y + r * Math.sin(a)); }
      g.closePath(); g.stroke();
    }
  }
  const texture = new THREE.CanvasTexture(c);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(6, 6);
  return texture;
}

function materials() {
  const energy = color => new THREE.MeshBasicMaterial({color: new THREE.Color().setRGB(...color), toneMapped: false});
  return {
    armor: new THREE.MeshPhysicalMaterial({color: 0x1d2228, metalness: 1, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.12, envMapIntensity: 1.25}),
    sleeve: new THREE.MeshPhysicalMaterial({color: 0x1d2228, metalness: 1, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.12, envMapIntensity: 1.25, side: THREE.DoubleSide}),
    accent: new THREE.MeshPhysicalMaterial({color: 0x5b6670, metalness: 1, roughness: 0.2, clearcoat: 0.6, envMapIntensity: 1.4}),
    dark: new THREE.MeshPhysicalMaterial({color: 0x0a0d10, metalness: 0.9, roughness: 0.45, envMapIntensity: 0.8}),
    mask: new THREE.MeshPhysicalMaterial({color: 0x07090b, metalness: 0.7, roughness: 0.55, clearcoat: 0.3, clearcoatRoughness: 0.4, envMapIntensity: 0.45}),
    under: new THREE.MeshStandardMaterial({color: 0x0b1013, metalness: 0.25, roughness: 0.8, bumpMap: wovenTexture(), bumpScale: 0.6, side: THREE.DoubleSide}),
    seam: energy(ENERGY.idle), eye: energy(ENERGY.idle), core: energy(ENERGY.idle), grille: energy(ENERGY.idle),
  };
}

// --- shape helpers ---------------------------------------------------------

function extrude(points, depth, bevel = 0.006) {
  const shape = new THREE.Shape(points.map(([x, y]) => new THREE.Vector2(x, y)));
  const g = new THREE.ExtrudeGeometry(shape, {depth, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 3, curveSegments: 8});
  g.center();
  return g;
}

function curve(geometry, amount) {
  // Wrap a flat plate around the body: push its sides back.
  const p = geometry.attributes.position;
  for (let i = 0; i < p.count; i++) p.setZ(i, p.getZ(i) - amount * p.getX(i) * p.getX(i));
  geometry.computeVertexNormals();
  return geometry;
}

function shell(radiusTop, radiusBottom, height, arc = Math.PI * 2, start = 0) {
  // A tapered armour sleeve (open or wrapped part-way around a limb).
  const g = new THREE.CylinderGeometry(radiusTop, radiusBottom, height, 32, 1, arc < Math.PI * 2, start, arc);
  return g;
}

function lathe(profile, arcStart = 0, arcLength = Math.PI * 2, segments = 48) {
  // A body part turned on a lathe from [radius, height] pairs: real curves,
  // a waist narrower than the chest, limbs that taper. Part-way around makes
  // a plate that hugs the body underneath.
  return new THREE.LatheGeometry(profile.map(([r, y]) => new THREE.Vector2(r, y)), segments, arcStart, arcLength);
}

function rbox(w, h, d, r = 0.008) { return new RoundedBoxGeometry(w, h, d, 3, r); }

function at(object, x = 0, y = 0, z = 0) { object.position.set(x, y, z); return object; }

export function buildSuit() {
  const m = materials();
  const j = {plates: [], energy: m};
  const add = (parent, mesh, armor = true) => {
    parent.add(mesh);
    if (armor) j.plates.push({mesh});
    return mesh;
  };
  const mesh = (geometry, material) => new THREE.Mesh(geometry, material);
  const seam = (radius, tube = 0.004) => {
    const s = mesh(new THREE.TorusGeometry(radius, tube, 8, 64), m.seam);
    s.rotation.x = Math.PI / 2;
    return s;
  };

  j.root = new THREE.Group();
  j.hips = at(new THREE.Group(), 0, 1.0, 0); j.root.add(j.hips);

  // --- pelvis and belt
  add(j.hips, at(mesh(new THREE.CylinderGeometry(0.15, 0.13, 0.16, 32), m.under), 0, -0.02, 0), false).scale.z = 0.72;
  const belt = add(j.hips, at(mesh(new THREE.CylinderGeometry(0.158, 0.152, 0.05, 40), m.armor), 0, 0.055, 0));
  belt.scale.z = 0.74;
  add(j.hips, at(seam(0.157), 0, 0.055, 0), false).scale.y = 0.74;
  const buckle = add(j.hips, at(mesh(extrude([[-0.05, 0.02], [0.05, 0.02], [0.03, -0.03], [-0.03, -0.03]], 0.015), m.accent), 0, 0.045, 0.118));
  buckle.rotation.x = -0.1;
  for (const side of [-1, 1]) {   // hip guards
    const guard = add(j.hips, at(mesh(curve(extrude([[0, 0.05], [0.07, 0.04], [0.06, -0.08], [0.0, -0.06]], 0.012), 4), m.armor), side * 0.135, -0.035, 0.02));
    guard.rotation.y = side * 1.25;
  }

  // --- torso: a V-shaped body (lathe-turned), a breastplate that hugs it,
  // segmented abdominal bands, a back plate and a collar.
  j.spine = at(new THREE.Group(), 0, 0.08, 0); j.hips.add(j.spine);
  const BODY = [[0.0, -0.02], [0.125, -0.02], [0.13, 0.08], [0.15, 0.2], [0.185, 0.33], [0.205, 0.43], [0.2, 0.51], [0.165, 0.565], [0.09, 0.6], [0.0, 0.6]];
  const body = mesh(lathe(BODY), m.under); body.scale.z = 0.66; j.spine.add(body);
  const grow = (profile, d) => profile.map(([r, y]) => [r + d, y]);
  // Breastplate: the chest section of the body, a little larger, front only.
  const chest = add(j.spine, mesh(lathe(grow(BODY.slice(3, 8), 0.014), -1.25, 2.5), m.sleeve));
  chest.scale.z = 0.7;
  const backplate = add(j.spine, mesh(lathe(grow(BODY.slice(2, 8), 0.012), Math.PI - 1.15, 2.3), m.sleeve));
  backplate.scale.z = 0.7;
  // Pectoral plates over the breastplate, each with a lit lower edge.
  for (const side of [-1, 1]) {
    const pec = add(j.spine, at(mesh(curve(extrude([[0.0, 0.07], [0.15, 0.06], [0.16, -0.01], [0.1, -0.07], [0.0, -0.05]], 0.016, 0.006), 3), m.accent), side * 0.09, 0.455, 0.14));
    pec.scale.x = side; pec.rotation.y = side * 0.38; pec.rotation.x = -0.15;
  }
  // The line between the pectoral plates and the one under them, lit.
  add(j.spine, at(mesh(rbox(0.008, 0.16, 0.006, 0.003), m.seam), 0, 0.45, 0.153), false).rotation.x = -0.18;
  for (const side of [-1, 1]) {
    const under = add(j.spine, at(mesh(rbox(0.13, 0.008, 0.006, 0.003), m.seam), side * 0.075, 0.335, 0.128), false);
    under.rotation.set(0.1, side * 0.42, side * -0.18);
  }
  // Abdominal bands: three rings of plate with gaps between them.
  [[0.07, 0.12], [0.135, 0.185], [0.2, 0.25]].forEach(([y0, y1]) => {
    const r0 = 0.13 + (y0 - 0.08) * 0.17 + 0.012, r1 = 0.13 + (y1 - 0.08) * 0.17 + 0.012;
    const band = add(j.spine, mesh(lathe([[r0, y0], [r0 + 0.004, (y0 + y1) / 2], [r1, y1]], -0.9, 1.8, 24), m.sleeve));
    band.scale.z = 0.68;
  });
  // The reactor: the orb, as the suit's heart.
  j.reactor = new THREE.Group(); at(j.reactor, 0, 0.42, 0.172); j.reactor.rotation.x = -0.12; j.spine.add(j.reactor);
  add(j.reactor, mesh(new THREE.TorusGeometry(0.05, 0.011, 16, 48), m.accent));
  add(j.reactor, at(mesh(new THREE.CylinderGeometry(0.04, 0.046, 0.012, 48), m.dark), 0, 0, -0.004)).rotation.x = Math.PI / 2;
  j.coreLight = mesh(new THREE.CircleGeometry(0.03, 48), m.core); j.coreLight.position.z = 0.004; j.reactor.add(j.coreLight);
  j.coreRings = [0.037, 0.045].map((r, i) => {
    const ring = mesh(new THREE.RingGeometry(r - 0.0025, r, 48, 1, i * 1.3, Math.PI * (1.2 + i * 0.4)), m.core);
    ring.position.z = 0.006 + i * 0.001; j.reactor.add(ring); return ring;
  });
  const collar = add(j.spine, at(mesh(lathe([[0.1, 0.0], [0.115, 0.025], [0.085, 0.05]]), m.accent), 0, 0.55, 0));
  collar.scale.z = 0.8;
  add(j.spine, at(seam(0.1), 0, 0.555, 0), false).scale.y = 0.8;

  // --- neck and helmet
  j.neck = at(new THREE.Group(), 0, 0.6, 0); j.spine.add(j.neck);
  j.neck.add(mesh(new THREE.CylinderGeometry(0.048, 0.06, 0.09, 24), m.under));
  j.head = at(new THREE.Group(), 0, 0.05, 0); j.neck.add(j.head);
  // Helmet: a faceted dark mask (flat planes, tapering to a chin) under a
  // polished skull cap, eye slits set into the mask, fins swept back.
  const maskGeo = new THREE.SphereGeometry(0.118, 14, 12);
  const v = maskGeo.attributes.position;
  for (let i = 0; i < v.count; i++) {
    let x = v.getX(i), y = v.getY(i), z = v.getZ(i);
    if (y < 0) x *= 1 + y * 2.6;                     // taper to the chin
    if (z > 0) z *= 0.86;                            // a flatter face
    if (z > 0 && y > 0.02 && y < 0.06) z += 0.008;   // brow line
    v.setXYZ(i, x * 0.9, y * 1.12, z);
  }
  maskGeo.computeVertexNormals();
  const maskMaterial = m.mask.clone(); maskMaterial.flatShading = true;
  add(j.head, at(mesh(maskGeo, maskMaterial), 0, 0.125, 0.004));
  const cap = add(j.head, at(mesh(new THREE.SphereGeometry(0.124, 40, 24, Math.PI * 0.62, Math.PI * 1.76, 0, Math.PI * 0.62), m.armor), 0, 0.128, -0.006));
  cap.scale.set(0.93, 1.1, 1.04);
  add(j.head, at(mesh(rbox(0.006, 0.07, 0.006, 0.002), m.seam), 0, 0.235, 0.05), false).rotation.x = 0.9;          // crest line
  j.eyes = [-1, 1].map(side => {
    const eye = mesh(extrude([[0, 0.004], [0.042, 0.016], [0.046, 0.006], [0.006, -0.008]], 0.004, 0.0015), m.eye);
    eye.scale.x = side; at(eye, side * 0.03, 0.152, 0.106); eye.rotation.y = side * 0.38; eye.rotation.z = side * -0.1;
    j.head.add(eye); return eye;
  });
  j.scanBar = mesh(rbox(0.005, 0.024, 0.004, 0.002), m.eye); at(j.scanBar, 0, 0.156, 0.111); j.scanBar.visible = false; j.head.add(j.scanBar);
  j.grille = [-2, -1, 0, 1, 2].map(i => {
    const bar = mesh(rbox(0.005, 0.014, 0.004, 0.0015), m.grille);
    at(bar, i * 0.009, 0.083, 0.096 - Math.abs(i) * 0.004); j.head.add(bar); return bar;
  });
  for (const side of [-1, 1]) {
    const fin = add(j.head, at(mesh(extrude([[0, 0], [0.012, 0.07], [0.03, 0.0]], 0.01, 0.004), m.accent), side * 0.07, 0.235, -0.04));
    fin.rotation.set(-0.55, side * 0.25, side * -0.15);                                                              // swept-back fins
    add(j.head, at(mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.015, 24), m.accent), side * 0.108, 0.125, -0.01)).rotation.z = Math.PI / 2;
  }

  // --- arms: shoulder armour, sleeves, gauntlets, hands with a palm emitter
  const limb = (parent, length, rTop, rBottom, armorArc, bulge = 0.12) => {
    // Under-suit turned with a muscle bulge a third of the way down; armour
    // wraps the front part of it, slightly larger, with a lit edge at each end.
    const joint = new THREE.Group(); parent.add(joint);
    const rMid = Math.max(rTop, rBottom) * (1 + bulge);
    const profile = [[0, 0], [rTop * 0.85, -0.01], [rMid, -length * 0.33], [rBottom, -length * 0.85], [rBottom * 0.85, -length], [0, -length]];
    joint.add(mesh(lathe(profile.map(([r, y]) => [r, y]), 0, Math.PI * 2, 32), m.under));
    const wrap = profile.slice(1, 5).map(([r, y]) => [r + 0.008, y]);
    add(joint, mesh(lathe(wrap, -armorArc / 2, armorArc, 32), m.sleeve));
    const end = at(new THREE.Group(), 0, -length, 0); joint.add(end);
    return {joint, end};
  };
  const arm = side => {
    const shoulder = at(new THREE.Group(), side * 0.27, 0.5, 0); j.spine.add(shoulder);
    const pauldron = add(shoulder, at(mesh(new THREE.SphereGeometry(0.085, 32, 16, 0, Math.PI * 2, 0, Math.PI * 0.55), m.armor), side * 0.01, 0.01, 0));
    pauldron.scale.set(1.15, 0.9, 1.05);
    const layer = add(shoulder, at(mesh(new THREE.SphereGeometry(0.075, 32, 12, 0, Math.PI * 2, Math.PI * 0.42, Math.PI * 0.16), m.accent), side * 0.012, -0.012, 0));
    layer.scale.set(1.2, 1, 1.1);
    add(shoulder, at(seam(0.07, 0.003), side * 0.01, -0.03, 0), false);
    const upper = limb(shoulder, 0.29, 0.052, 0.044, Math.PI * 1.25, 0.18);
    add(upper.end, mesh(new THREE.SphereGeometry(0.042, 24, 16), m.accent));                                       // elbow cap
    const fore = limb(upper.end, 0.27, 0.046, 0.04, Math.PI * 1.9, 0.12);
    for (const y of [-0.06, -0.2]) add(fore.joint, at(seam(0.044 - (y < -0.1 ? 0.004 : 0), 0.0035), 0, y, 0), false);
    const hand = at(new THREE.Group(), 0, -0.01, 0); fore.end.add(hand);
    add(hand, at(mesh(rbox(0.07, 0.08, 0.035, 0.012), m.armor), 0, -0.04, 0));
    j[side < 0 ? 'rPalm' : 'lPalm'] = at(mesh(new THREE.CircleGeometry(0.014, 24), m.seam), side * -0.018, -0.042, 0.0);
    j[side < 0 ? 'rPalm' : 'lPalm'].rotation.y = side * -Math.PI / 2; hand.add(j[side < 0 ? 'rPalm' : 'lPalm']);
    for (let f = 0; f < 4; f++) {                                                                                   // fingers, slightly curled
      const finger = at(new THREE.Group(), (f - 1.5) * 0.017, -0.078, 0.004); finger.rotation.x = -0.45; hand.add(finger);
      add(finger, at(mesh(rbox(0.014, 0.04, 0.016, 0.005), m.armor), 0, -0.02, 0));
      const tip = at(new THREE.Group(), 0, -0.04, 0); tip.rotation.x = -0.75; finger.add(tip);
      add(tip, at(mesh(rbox(0.013, 0.032, 0.015, 0.005), m.accent), 0, -0.016, 0));
    }
    const thumb = at(new THREE.Group(), side * -0.038, -0.035, 0.012); thumb.rotation.z = side * 0.6; hand.add(thumb);
    add(thumb, at(mesh(rbox(0.015, 0.04, 0.016, 0.005), m.armor), 0, -0.02, 0));
    return {shoulder: upper.joint, elbow: fore.joint};
  };
  j.l = arm(1); j.r = arm(-1);

  // --- legs
  for (const side of [-1, 1]) {
    const hip = at(new THREE.Group(), side * 0.095, -0.06, 0); j.hips.add(hip);
    const thigh = limb(hip, 0.45, 0.078, 0.056, Math.PI * 1.35, 0.15);
    add(thigh.joint, at(mesh(curve(extrude([[0, 0.12], [0.05, 0.1], [0.045, -0.12], [0, -0.14]], 0.012), 6), m.accent), side * 0.06, -0.2, 0.01)).rotation.y = side * 1.4;
    add(thigh.joint, at(mesh(rbox(0.006, 0.2, 0.004, 0.002), m.seam), 0, -0.2, 0.083), false);                    // lit thigh line
    const kneeGuard = add(thigh.end, at(mesh(extrude([[-0.04, 0.05], [0.04, 0.05], [0.045, -0.02], [0.0, -0.06], [-0.045, -0.02]], 0.02, 0.008), m.armor), 0, 0.0, 0.058));
    kneeGuard.rotation.x = 0.1;
    const knee = add(thigh.end, at(mesh(new THREE.SphereGeometry(0.05, 24, 16, 0, Math.PI * 2, 0, Math.PI * 0.6), m.accent), 0, 0, 0.03));
    knee.rotation.x = Math.PI / 2;
    const shin = limb(thigh.end, 0.45, 0.055, 0.042, Math.PI * 1.4, 0.2);
    add(shin.joint, at(seam(0.052, 0.0035), 0, -0.12, 0), false);
    const ridge = add(shin.joint, at(mesh(extrude([[-0.018, 0.14], [0.018, 0.14], [0.012, -0.16], [-0.012, -0.16]], 0.014, 0.005), m.accent), 0, -0.21, 0.058));
    ridge.rotation.x = 0.05;
    const boot = add(shin.end, at(mesh(rbox(0.1, 0.075, 0.22, 0.022), m.armor), 0, -0.01, 0.045));
    add(shin.end, at(mesh(rbox(0.09, 0.04, 0.06, 0.015), m.accent), 0, -0.005, 0.13)).rotation.x = 0.25;          // toe cap
    boot.rotation.x = 0.04;
    add(shin.end, at(mesh(rbox(0.1, 0.012, 0.215, 0.005), m.dark), 0, -0.048, 0.045));
    add(shin.end, at(mesh(rbox(0.08, 0.004, 0.16, 0.002), m.seam), 0, -0.042, 0.065), false);
    add(shin.end, at(mesh(new THREE.CylinderGeometry(0.052, 0.058, 0.05, 24), m.accent), 0, 0.03, 0));
  }

  // --- the projector it stands on
  j.base = new THREE.Group(); j.root.add(j.base);
  const disc = mesh(new THREE.CircleGeometry(0.5, 64), new THREE.MeshBasicMaterial({color: 0x0a2e2a, transparent: true, opacity: 0.55}));
  disc.rotation.x = -Math.PI / 2; disc.position.y = 0.001; j.base.add(disc);
  j.hud = [0.36, 0.44, 0.52].map((r, i) => {
    const ring = mesh(new THREE.RingGeometry(r - 0.006, r, 96, 1, i, Math.PI * (1.1 + 0.25 * i)),
      new THREE.MeshBasicMaterial({color: new THREE.Color().setRGB(...ENERGY.idle).multiplyScalar(0.5), transparent: true, opacity: 0.75, toneMapped: false, side: THREE.DoubleSide}));
    ring.rotation.x = -Math.PI / 2; ring.position.y = 0.003 + i * 0.001; j.base.add(ring); return ring;
  });
  const beam = mesh(new THREE.CylinderGeometry(0.42, 0.5, 2.1, 48, 1, true), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
    uniforms: {color: {value: new THREE.Color().setRGB(...ENERGY.idle)}, time: {value: 0}},
    vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'uniform vec3 color; uniform float time; varying vec2 vUv;' +
      'void main(){ float fade = pow(1.0 - vUv.y, 2.2) * 0.08; float lines = 0.6 + 0.4 * step(0.5, fract(vUv.y * 60.0 - time));' +
      ' gl_FragColor = vec4(color * fade * lines, fade); }',
  }));
  beam.position.y = 1.05; j.beam = beam; j.base.add(beam);
  return j;
}
