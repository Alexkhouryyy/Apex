/* Apex's character: the Mk I suit (avatar-suit.js), rendered with reflections
   (a studio environment for the metal), filmic tone mapping and bloom on its
   energy lines. When it appears, the plates fly in and lock from the feet
   up, then the eyes and reactor power on. Body language comes from
   avatar-pose.js; the vocal grille follows window.ApexVoice.level, the audio
   actually playing.

   window.ApexAvatarCharacter.create(host, {level, state}) -> {dispose, debug}
   or null when the device has no WebGL (the page keeps the orb). If the
   device can't hold the frame rate, bloom turns off by itself. */
import * as THREE from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {buildSuit, ENERGY} from './avatar-suit.js?v=mk1';

const ASSEMBLE_SECONDS = 2.2, SLOW_FRAME_MS = 28;

function webgl() {
  try {
    const c = document.createElement('canvas');
    return Boolean(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (_) { return false; }
}

const easeOutBack = x => { const c = 1.6; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };

function create(host, {level = () => 0, state = () => '', reduced, quality, model} = {}) {
  if (!host || !webgl() || !window.ApexAvatarPose) return null;
  const Pose = window.ApexAvatarPose;
  const calm = reduced ?? window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'high-performance'});
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.setClearColor(0x000000, 0);
  Object.assign(renderer.domElement.style, {position: 'absolute', inset: '0'});
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture;
  pmrem.dispose();
  const key = new THREE.DirectionalLight(0xfff1e0, 2.2); key.position.set(1.6, 3.2, 2.4); scene.add(key);
  const rimL = new THREE.DirectionalLight(0x54ffd8, 3.2); rimL.position.set(-2.2, 2.2, -2); scene.add(rimL);
  const rimR = new THREE.DirectionalLight(0x5aa8ff, 2.4); rimR.position.set(2.4, 1.6, -2.2); scene.add(rimR);
  scene.add(new THREE.HemisphereLight(0x9fd8ff, 0x050809, 0.35));
  const j = buildSuit(); scene.add(j.root);
  const camera = new THREE.PerspectiveCamera(28, 1, 0.05, 30);

  // Glow only where there is energy: a second canvas renders just the lit
  // parts (everything else black), blurs them, and is screen-blended on top.
  // Black adds nothing, so the page shows through and the armour stays crisp.
  const glowRenderer = new THREE.WebGLRenderer({antialias: false, alpha: false, powerPreference: 'high-performance'});
  glowRenderer.setPixelRatio(1);
  glowRenderer.toneMapping = THREE.ACESFilmicToneMapping;
  glowRenderer.outputColorSpace = THREE.SRGBColorSpace;
  glowRenderer.setClearColor(0x000000, 1);
  Object.assign(glowRenderer.domElement.style, {position: 'absolute', inset: '0', mixBlendMode: 'screen', pointerEvents: 'none'});
  // Fade the glow out toward the edges so its faint haze never shows the canvas's rectangle.
  glowRenderer.domElement.style.maskImage = glowRenderer.domElement.style.webkitMaskImage =
    'radial-gradient(ellipse 50% 50% at 50% 50%, #000 62%, transparent 100%)';
  host.appendChild(glowRenderer.domElement);
  const composer = new EffectComposer(glowRenderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.55, 0.18, 0.12);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  const black = new THREE.MeshBasicMaterial({color: 0x000000});
  const glowing = new Set([j.energy.seam, j.energy.eye, j.energy.core, j.energy.grille]);
  const darkMeshes = [];
  scene.traverse(o => { if (o.isMesh && !glowing.has(o.material) && o !== j.beam && !j.hud.includes(o)) darkMeshes.push(o); });
  const saved = new Map();
  function renderGlow() {
    for (const o of darkMeshes) { saved.set(o, o.material); o.material = black; }
    const beam = j.beam.visible; j.beam.visible = false;
    const env = scene.environment; scene.environment = null;
    composer.render();
    scene.environment = env; j.beam.visible = beam;
    for (const o of darkMeshes) o.material = saved.get(o);
  }
  let useBloom = quality !== 'low';

  // Your own model (avatar-model.js), if there is one: it replaces the suit
  // once it has loaded; until then, or if it can't be used, the suit stays.
  let custom = null, modelStatus = model ? 'loading' : 'none', disposed = false;
  if (model) {
    import('./avatar-model.js?v=mk1').then(m => m.loadModel(model)).then(loaded => {
      if (disposed) return;
      custom = loaded; j.hips.visible = false; j.root.add(loaded.object);
      loaded.object.traverse(o => { if (o.isMesh && ![].concat(o.material).some(mat => loaded.glowing.has(mat))) darkMeshes.push(o); });
      modelStatus = 'loaded';
    }).catch(err => { modelStatus = 'failed: ' + (err?.message || err); });
  }

  let width = 0, height = 0, full = false;
  function frame() {
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    if (w === width && h === height) return;
    width = w; height = h;
    renderer.setSize(w, h, false); glowRenderer.setSize(w, h, false); composer.setSize(w, h);
    glowRenderer.domElement.style.width = renderer.domElement.style.width = '100%';
    glowRenderer.domElement.style.height = renderer.domElement.style.height = '100%';
    camera.aspect = w / h;
    full = h / w >= 1.15 && h >= 160;               // tall spaces: the whole suit; small ones: head and shoulders
    const [y, tall, wide] = full ? [1.0, 2.2, 1.05] : [1.66, 0.62, 0.66];
    const half = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
    const fit = Math.max(tall / 2 / half, wide / 2 / (camera.aspect * half));
    camera.position.set(full ? 0.35 : 0.08, y + (full ? 0.12 : 0.02), fit);
    camera.lookAt(0, y, 0);
    camera.updateProjectionMatrix();
    j.base.visible = full;
  }
  const resize = new ResizeObserver(frame); resize.observe(host);

  let gazeX = 0, gazeY = 0;
  const onPointer = e => { gazeX = (e.clientX / window.innerWidth) * 2 - 1; gazeY = (e.clientY / window.innerHeight) * 2 - 1; };
  window.addEventListener('pointermove', onPointer, {passive: true});

  // Suit-up: every plate starts away from its place, then flies home in order, feet first.
  const homeWorld = new THREE.Vector3();
  j.root.updateMatrixWorld(true);
  for (const plate of j.plates) {
    // Resting place and size, read now that the suit is fully built (parts
    // are often scaled or moved after being added).
    plate.home = plate.mesh.position.clone(); plate.scale = plate.mesh.scale.clone();
    plate.mesh.getWorldPosition(homeWorld);
    plate.delay = (homeWorld.y / 2) * (ASSEMBLE_SECONDS - 0.7);
    const dir = new THREE.Vector3(Math.sin(homeWorld.y * 7.3) * 0.5, 0.25, 0.6 + Math.cos(homeWorld.y * 5.1) * 0.3).normalize();
    plate.from = plate.home.clone().addScaledVector(dir, 0.35);
  }
  // The clock starts once a frame has actually been drawn: the first frame
  // compiles the shaders and can take seconds, and the suit-up must not
  // finish before anyone sees it.
  let born = null, drawn = 0;
  const assemble = t => {
    if (born === null && drawn >= 2) born = t;
    const elapsed = calm ? 99 : born === null ? 0 : t - born;
    for (const plate of j.plates) {
      const k = Math.min(1, Math.max(0, (elapsed - plate.delay) / 0.55));
      plate.mesh.position.lerpVectors(plate.from, plate.home, easeOutBack(k));
      plate.mesh.scale.copy(plate.scale).multiplyScalar(Math.max(0.001, Math.min(1, k * 1.4)));
    }
    return Math.min(1, Math.max(0, (elapsed - ASSEMBLE_SECONDS + 0.4) / 0.6));   // power-on, after the last plate
  };

  let visible = true, lv = 0, raf = 0, last = performance.now(), slow = 0, frames = 0;
  const seen = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }); seen.observe(host);
  const tint = new THREE.Color(), want = new THREE.Color();

  function energize(mood, power, p, bars, t) {
    want.setRGB(...ENERGY[mood]);
    tint.lerp(want, 0.08);
    const set = (material, k) => material.color.copy(tint).multiplyScalar(k);
    set(j.energy.seam, 0.9 * power);
    set(j.energy.eye, p.eyeGlow * power * Math.max(0.15, p.eyeOpen));
    set(j.energy.core, (0.6 + p.coreGlow * 1.1) * power);
    set(j.energy.grille, power * 0.35);
    j.grille.forEach((bar, i) => { bar.scale.y = 0.25 + bars[i] * 1.9; });
    j.coreRings.forEach((ring, i) => { ring.rotation.z += (i % 2 ? -1 : 1) * p.coreSpin * (0.6 + i * 0.35) * 0.016; });
    j.hud.forEach((ring, i) => { ring.rotation.z += (i % 2 ? -1 : 1) * p.hudSpeed * (0.4 + i * 0.2) * 0.016; ring.material.color.copy(tint).multiplyScalar(0.55 * power); });
    j.beam.material.uniforms.color.value.copy(tint).multiplyScalar(power);
    j.beam.material.uniforms.time.value = t * 0.6;
    j.scanBar.visible = p.scan > 0 && power > 0.5;
    if (j.scanBar.visible) j.scanBar.position.x = -0.07 + 0.14 * p.scan;
  }

  function pose(p, dt) {
    const ease = Math.min(1, dt * 7);
    const to = (obj, key, v) => { obj[key] += (v - obj[key]) * ease; };
    j.root.position.x = p.shift;
    to(j.spine.rotation, 'x', p.spineX); to(j.spine.rotation, 'z', p.spineZ);
    j.spine.scale.y = p.breathe;
    to(j.head.rotation, 'x', p.headX); to(j.head.rotation, 'y', p.headY); to(j.head.rotation, 'z', p.headZ);
    to(j.l.shoulder.rotation, 'x', p.lShoulderX); to(j.l.shoulder.rotation, 'z', p.lShoulderZ); to(j.l.elbow.rotation, 'x', p.lElbowX);
    to(j.r.shoulder.rotation, 'x', p.rShoulderX); to(j.r.shoulder.rotation, 'z', p.rShoulderZ); to(j.r.elbow.rotation, 'x', p.rElbowX);
  }

  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (!visible || document.hidden) { last = now; return; }
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    frame();
    const t = now / 1000;
    lv = Pose.smooth(lv, Math.max(0, Math.min(1, Number(level()) || 0)));
    const p = Pose.pose(state(), t, lv, {gazeX, gazeY, reduced: calm});
    const power = custom ? 1 : assemble(t);
    if (custom) custom.apply(p, lv); else pose(p, dt);
    energize(p.mood, power, p, Pose.voiceBars(t, p.mouthOpen > 0 ? lv : 0), t);
    const started = performance.now();
    renderer.render(scene, camera); drawn++;
    glowRenderer.domElement.style.display = useBloom ? '' : 'none';
    if (useBloom) renderGlow();
    // Too slow for this device: drop bloom rather than stutter.
    if (useBloom && ++frames > 30) { slow = slow * 0.9 + (performance.now() - started + dt * 1000) * 0.1; if (frames > 90 && slow > SLOW_FRAME_MS) useBloom = false; }
  }
  raf = requestAnimationFrame(loop);

  return {
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf); resize.disconnect(); seen.disconnect();
      window.removeEventListener('pointermove', onPointer);
      scene.traverse(o => { o.geometry?.dispose(); if (o.material) [].concat(o.material).forEach(m => { m.map?.dispose(); m.bumpMap?.dispose(); m.dispose(); }); });
      scene.environment?.dispose(); composer.dispose?.(); black.dispose();
      glowRenderer.dispose(); glowRenderer.domElement.remove(); renderer.dispose(); renderer.domElement.remove();
    },
    debug: () => ({model: modelStatus, bones: custom?.bones, mouth: custom?.mouth,grille: j.grille.map(b => +b.scale.y.toFixed(2)), core: '#' + j.energy.core.color.getHexString(),
      level: lv, bloom: useBloom, assembled: j.plates.every(pl => pl.mesh.position.distanceTo(pl.home) < 1e-3), plates: j.plates.length}),
  };
}

window.ApexAvatarCharacter = {create};
window.dispatchEvent(new Event('apex-avatar-ready'));
