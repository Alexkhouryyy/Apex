/* The Apex character: a full-body android drawn with three.js, made for Apex
   (no outside model, so no licence or likeness questions). Its chest core is
   the orb; its visor face blinks, follows the pointer and moves its mouth
   with the voice that is actually playing (window.ApexVoice.level).
   Body language comes from avatar-pose.js.

   window.ApexAvatarCharacter.create(host, {level, state}) -> {dispose} or
   null when the device has no WebGL (the page keeps the orb). */
import * as THREE from './vendor/three/build/three.module.min.js';

const COLORS = {
  body: 0x13252c, trim: 0x7aebc7, glow: 0x7ff5d2, visor: 0x050b10,
  idle: 0x67e9bb, listen: 0x6fc8ff, think: 0xb7a6ff, speak: 0x8dffe0,
};

function webgl() {
  try {
    const c = document.createElement('canvas');
    return Boolean(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (_) { return false; }
}

function part(geometry, material) {
  return new THREE.Mesh(geometry, material);
}

// Dark armour with a glowing edge: light added where the surface turns away
// from the camera (a Fresnel rim), so the silhouette reads on a dark screen.
function rimMaterial(options, rimColor, strength) {
  const material = new THREE.MeshStandardMaterial(options);
  material.onBeforeCompile = shader => {
    shader.uniforms.rimColor = {value: new THREE.Color(rimColor)};
    shader.uniforms.rimStrength = {value: strength};
    shader.fragmentShader = 'uniform vec3 rimColor;\nuniform float rimStrength;\n' + shader.fragmentShader.replace(
      '#include <opaque_fragment>',
      'float apexRim = pow(1.0 - clamp(dot(normalize(normal), normalize(vViewPosition)), 0.0, 1.0), 2.4);\n' +
      'outgoingLight += rimColor * apexRim * rimStrength;\n#include <opaque_fragment>');
  };
  return material;
}

function limb(length, radius, material) {
  // A joint group whose child hangs down -Y, so rotating the group swings the limb.
  const joint = new THREE.Group();
  const mesh = part(new THREE.CapsuleGeometry(radius, length - radius * 2, 6, 14), material);
  mesh.position.y = -length / 2;
  joint.add(mesh);
  const end = new THREE.Group();
  end.position.y = -length;
  joint.add(end);
  return {joint, end};
}

function at(object, x, y, z) { object.position.set(x, y, z); return object; }

function ring(radius, material) {
  const mesh = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.006, 8, 48), material);
  mesh.rotation.x = Math.PI / 2;
  return mesh;
}

function build() {
  const body = rimMaterial({color: COLORS.body, metalness: 0.5, roughness: 0.35}, COLORS.glow, 0.9);
  const trim = new THREE.MeshBasicMaterial({color: COLORS.trim});
  const j = {};

  j.root = new THREE.Group();
  j.hips = new THREE.Group(); j.hips.position.y = 0.98; j.root.add(j.hips);
  const pelvis = part(new THREE.CylinderGeometry(0.15, 0.12, 0.14, 24), body);
  pelvis.scale.set(1, 1, 0.7); j.hips.add(pelvis);
  j.hips.add(at(ring(0.17, trim), 0, 0.07, 0));

  j.spine = new THREE.Group(); j.spine.position.y = 0.08; j.hips.add(j.spine);
  const torso = part(new THREE.CylinderGeometry(0.2, 0.13, 0.5, 28), body);   // broad chest, narrow waist
  torso.scale.set(1.15, 1, 0.68); torso.position.y = 0.25; j.spine.add(torso);
  const chest = part(new THREE.SphereGeometry(0.2, 28, 16, 0, Math.PI * 2, 0, Math.PI / 2), body);
  chest.scale.set(1.15, 0.35, 0.68); chest.position.y = 0.5; j.spine.add(chest);
  // The core: the orb, now a heart.
  j.core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.055, 2),
    new THREE.MeshStandardMaterial({color: COLORS.idle, emissive: COLORS.idle, emissiveIntensity: 1.2, roughness: 0.2}));
  j.core.position.set(0, 0.36, 0.13); j.spine.add(j.core);
  j.coreHalo = new THREE.Mesh(new THREE.RingGeometry(0.07, 0.085, 40),
    new THREE.MeshBasicMaterial({color: COLORS.idle, transparent: true, opacity: 0.7, side: THREE.DoubleSide}));
  j.coreHalo.position.set(0, 0.36, 0.14); j.spine.add(j.coreHalo);

  j.neck = new THREE.Group(); j.neck.position.y = 0.6; j.spine.add(j.neck);
  j.neck.add(part(new THREE.CylinderGeometry(0.045, 0.055, 0.08, 16), body));
  j.neck.add(at(ring(0.058, trim), 0, -0.03, 0));
  j.head = new THREE.Group(); j.head.position.y = 0.06; j.neck.add(j.head);
  const skull = part(new THREE.SphereGeometry(0.13, 32, 24), body);
  skull.scale.set(0.88, 1.05, 0.95); skull.position.y = 0.12; j.head.add(skull);
  const visor = new THREE.Mesh(new THREE.SphereGeometry(0.118, 32, 16, -0.95, 1.9, 1.05, 0.95),
    new THREE.MeshStandardMaterial({color: COLORS.visor, metalness: 0.9, roughness: 0.15}));
  visor.position.set(0, 0.12, 0.012); visor.scale.set(0.9, 1.04, 0.98); j.head.add(visor);
  const face = new THREE.MeshBasicMaterial({color: COLORS.glow});
  j.eyes = [-1, 1].map(side => {
    const eye = new THREE.Mesh(new THREE.CapsuleGeometry(0.011, 0.02, 4, 10), face);
    eye.rotation.z = Math.PI / 2; eye.position.set(side * 0.042, 0.145, 0.115);
    j.head.add(eye); return eye;
  });
  j.mouth = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.01, 0.01), face);
  j.mouth.position.set(0, 0.075, 0.113); j.head.add(j.mouth);

  const arm = side => {
    const shoulder = new THREE.Group(); shoulder.position.set(side * 0.26, 0.5, 0); j.spine.add(shoulder);
    shoulder.add(part(new THREE.SphereGeometry(0.06, 16, 12), body));
    const upper = limb(0.28, 0.045, body); shoulder.add(upper.joint);
    const fore = limb(0.26, 0.038, body); upper.end.add(fore.joint);
    fore.end.add(at(ring(0.04, trim), 0, 0.02, 0));
    const hand = part(new THREE.SphereGeometry(0.045, 16, 12), body);
    hand.scale.set(0.8, 1.2, 0.6); hand.position.y = -0.04; fore.end.add(hand);
    return {shoulder: upper.joint, elbow: fore.joint};
  };
  j.l = arm(1); j.r = arm(-1);
  for (const side of [1, -1]) {
    const hip = new THREE.Group(); hip.position.set(side * 0.1, -0.03, 0); j.hips.add(hip);
    const thigh = limb(0.44, 0.065, body); hip.add(thigh.joint);
    const shin = limb(0.44, 0.052, body); thigh.end.add(shin.joint);
    shin.end.add(at(ring(0.055, trim), 0, 0.05, 0));
    const foot = part(new THREE.BoxGeometry(0.09, 0.05, 0.2), body);
    foot.position.set(0, -0.02, 0.04); shin.end.add(foot);
  }
  // A faint floor disc so it stands somewhere rather than floats.
  const floor = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.42, 48),
    new THREE.MeshBasicMaterial({color: COLORS.trim, transparent: true, opacity: 0.12, side: THREE.DoubleSide}));
  floor.rotation.x = -Math.PI / 2; floor.position.y = 0.001; j.root.add(floor);
  return j;
}

function create(host, {level = () => 0, state = () => '', reduced} = {}) {
  if (!host || !webgl() || !window.ApexAvatarPose) return null;
  const Pose = window.ApexAvatarPose;
  const calm = reduced ?? window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xcffff3, 0x0a1418, 1.4));
  const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(1.5, 3, 2.5); scene.add(key);
  const rim = new THREE.DirectionalLight(0x67e9bb, 2.2); rim.position.set(-2, 2, -2.5); scene.add(rim);
  const j = build(); scene.add(j.root);
  const camera = new THREE.PerspectiveCamera(30, 1, 0.05, 20);

  let width = 0, height = 0;
  function frame() {
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    if (w === width && h === height) return;
    width = w; height = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Tall spaces show the whole body; small or wide ones, head and shoulders.
    // Heights: feet 0, shoulders about 1.55, top of the head about 1.98.
    const full = h / w >= 1.15 && h >= 160;
    const [y, tall, wide] = full ? [0.97, 2.25, 1.0] : [1.68, 0.72, 0.72];
    const half = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
    const fit = Math.max(tall / 2 / half, wide / 2 / (camera.aspect * half));
    camera.position.set(0, y + 0.03, fit);
    camera.lookAt(0, y, 0);
    camera.updateProjectionMatrix();
  }
  const resize = new ResizeObserver(frame); resize.observe(host);

  let gazeX = 0, gazeY = 0;
  const onPointer = e => {
    gazeX = (e.clientX / window.innerWidth) * 2 - 1;
    gazeY = (e.clientY / window.innerHeight) * 2 - 1;
  };
  window.addEventListener('pointermove', onPointer, {passive: true});

  let visible = true, lv = 0, raf = 0, last = performance.now();
  const seen = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }); seen.observe(host);
  const tint = new THREE.Color();

  function apply(p, dt) {
    const ease = Math.min(1, dt * 8);
    const to = (obj, key, v) => { obj[key] += (v - obj[key]) * ease; };
    j.root.position.x = p.shift;
    to(j.spine.rotation, 'x', p.spineX); to(j.spine.rotation, 'z', p.spineZ);
    j.spine.scale.y = p.breathe;
    to(j.head.rotation, 'x', p.headX); to(j.head.rotation, 'y', p.headY); to(j.head.rotation, 'z', p.headZ);
    to(j.l.shoulder.rotation, 'x', p.lShoulderX); to(j.l.shoulder.rotation, 'z', p.lShoulderZ); to(j.l.elbow.rotation, 'x', p.lElbowX);
    to(j.r.shoulder.rotation, 'x', p.rShoulderX); to(j.r.shoulder.rotation, 'z', p.rShoulderZ); to(j.r.elbow.rotation, 'x', p.rElbowX);
    j.mouth.scale.set(p.mouthWide, 1 + p.mouthOpen * 3.2, 1);      // the voice, not the frame rate
    for (const eye of j.eyes) eye.scale.y = Math.max(0.12, p.eyeOpen);
    tint.set(COLORS[p.mood]);
    j.core.material.color.lerp(tint, ease); j.core.material.emissive.lerp(tint, ease);
    j.core.material.emissiveIntensity = 0.6 + p.coreGlow * 1.4;
    j.coreHalo.material.color.lerp(tint, ease);
    j.coreHalo.scale.setScalar(1 + p.coreGlow * 0.25);
    j.core.rotation.y += p.coreSpin * dt;
  }

  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (!visible || document.hidden) { last = now; return; }
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    frame();
    lv = Pose.smooth(lv, Math.max(0, Math.min(1, Number(level()) || 0)));
    apply(Pose.pose(state(), now / 1000, lv, {gazeX, gazeY, reduced: calm}), dt);
    renderer.render(scene, camera);
  }
  raf = requestAnimationFrame(loop);

  return {
    dispose() {
      cancelAnimationFrame(raf); resize.disconnect(); seen.disconnect();
      window.removeEventListener('pointermove', onPointer);
      scene.traverse(o => { o.geometry?.dispose(); o.material?.dispose?.(); });
      renderer.dispose(); renderer.domElement.remove();
    },
    // For checks: the current mouth opening and core colour.
    debug: () => ({mouth: j.mouth.scale.y, core: '#' + j.core.material.color.getHexString(), level: lv}),
  };
}

window.ApexAvatarCharacter = {create};
window.dispatchEvent(new Event('apex-avatar-ready'));
