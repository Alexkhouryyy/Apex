// Phase 2a hologram (dashboard/static/study-holo.js), with the real three.js
// and a stand-in renderer (no GPU here): the pinch glow, the slow-frame bloom
// governor, sounds that stay silent until allowed, the hand drawn in light,
// and the scene reacting to the hand — then all of it undone by the toggle.
import {register} from 'node:module';
import {join, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vendor = pathToFileURL(join(root, 'dashboard/static/vendor/three')).href;
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(s, c, next) {
  if (s === 'three') return {url: ${JSON.stringify(vendor + '/build/three.module.min.js')}, shortCircuit: true};
  if (s.startsWith('three/addons/')) return {url: ${JSON.stringify(vendor + '/examples/jsm/')} + s.slice(13), shortCircuit: true};
  return next(s, c);
}`));
globalThis.devicePixelRatio = 1;
const THREE = await import('three');
const {pinchGlow, frameGovernor, SLOW_FRAME, HoloSound, HoloHand, BONES, setupHoloScene, isSoftwareRenderer} =
  await import(pathToFileURL(join(root, 'dashboard/static/study-holo.js')).href);

// 1. Pinch glow: 0 wide open, 1 at the threshold, never outside 0..1.
assert.equal(pinchGlow(0.35, 0.35, 0.45), 1);
assert.equal(pinchGlow(0.10, 0.35, 0.45), 1);
assert.equal(pinchGlow(2.0, 0.35, 0.45), 0);
assert.ok(pinchGlow(0.5, 0.35, 0.45) > pinchGlow(0.7, 0.35, 0.45), 'closing the fingers must brighten the glow');
assert.equal(pinchGlow(NaN, 0.35, 0.45), 0);
assert.equal(BONES.length, 21, 'the MediaPipe hand has 21 bones in this drawing');

// 2. The governor: steady slow frames trip it; normal frames and a brief
//    hitch do not; reset clears it.
let tripped = 0;
const gov = frameGovernor(() => tripped++);
for (let i = 0; i < 400; i++) gov.feed(1 / 60);
// A hitch: a handful of frames at the draw loop's 50 ms cap (~0.4 s).
for (let i = 0; i < 8; i++) gov.feed(0.05);
assert.equal(gov.tripped, false, 'a short hitch must not pause the glow');
for (let i = 0; i < 200; i++) gov.feed(1 / 60);
for (let i = 0; i < 60; i++) gov.feed(SLOW_FRAME * 1.4);    // 60 slow frames ≈ 3.4 s
assert.equal(gov.tripped, true); assert.equal(tripped, 1, 'it trips once');
gov.reset(); assert.equal(gov.tripped, false);

// 3. Sound: silent (and harmless) until the page is touched; muting persists.
const store = new Map();
const storage = {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v)};
const played = [];
class FakeAudio {
  constructor() { this.currentTime = 0; this.state = 'running'; this.destination = {}; }
  createOscillator() { const o = {type: '', frequency: {setValueAtTime() {}, exponentialRampToValueAtTime() {}},
    connect: g => g, start() { played.push(o.type); }, stop() {}}; return o; }
  createGain() { return {gain: {setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect: d => d}; }
  close() { this.state = 'closed'; }
}
const s = new HoloSound(storage);
assert.equal(s.on, true, 'sound is on by default');
assert.equal(s.play('grab'), false, 'no sound before the page has been clicked');
globalThis.AudioContext = FakeAudio;
s.unlock(); assert.equal(s.play('grab'), true); assert.equal(played.length, 1);
assert.equal(s.play('nonsense'), false);
s.toggle(); assert.equal(store.get('apex.study.sound'), 'off');
assert.equal(s.play('ready'), false, 'muted is muted');
assert.equal(new HoloSound(storage).on, false, 'mute is remembered');

// 4. The hand drawn in light: bones and joints for a full hand; nothing for a
//    partial one; it fades out when the hand has not been seen for a moment.
const calls = [];
const ctx = new Proxy({}, {get: (_, k) => k === 'setTransform' || k === 'clearRect' ? () => {} :
  typeof k === 'string' && ['beginPath', 'moveTo', 'lineTo', 'stroke', 'arc', 'fill'].includes(k) ? (...a) => calls.push(k) : undefined,
  set: () => true});
const canvas = {width: 0, height: 0, getContext: () => ctx, getBoundingClientRect: () => ({width: 400, height: 300})};
const hh = new HoloHand(canvas);
const joints = Array.from({length: 21}, (_, i) => [0.3 + i * 0.01, 0.4 + i * 0.01]);
hh.set({joints, ratio: 0.6, threshold: 0.35, release: 0.45, pinched: false}, 'tracking', 1000);
hh.draw(1000);
assert.ok(calls.filter(c => c === 'lineTo').length >= BONES.length, 'every bone is drawn');
assert.ok(calls.filter(c => c === 'arc').length >= 21, 'every joint is drawn');
calls.length = 0; hh.draw(1000 + 400);
assert.equal(calls.length, 0, 'a hand unseen for 0.4 s has faded out');
calls.length = 0; hh.set({joints: joints.slice(0, 7)}, 'tracking', 2000); hh.draw(2000);
assert.equal(calls.length, 0, 'a partial skeleton is not drawn');

// 5. The scene: edges on every mesh, the focused part lights up and — only
//    when ready, not while held — rises toward you; the toggle undoes it all.
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera();
const groups = new Map();
for (const id of ['armature', 'shaft']) {
  const g = new THREE.Group(); g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial()));
  scene.add(g); groups.set(id, g);
}
let rendered = 0;
const renderer = {getPixelRatio: () => 1, getSize: v => v.set(200, 100), setRenderTarget() {}, render() { rendered++; },
  getRenderTarget: () => null, autoClear: true, clear() {}, getClearColor: c => c, getClearAlpha: () => 1, setClearColor() {}, setClearAlpha() {}};
const holo = setupHoloScene({THREE, scene, camera, renderer, groups, reducedMotion: {matches: false}, storage});
holo.buildEdges();
const edgesOf = id => { const out = []; groups.get(id).traverse(o => { if (o.isLineSegments) out.push(o); }); return out; };
assert.equal(edgesOf('armature').length, 1, 'every mesh gets a glowing edge outline');
assert.ok(scene.background && scene.fog, 'the hologram space is dark with depth fog');
const meshOf = id => groups.get(id).children[0];
holo.setFocus('armature', 1, false);
for (let i = 0; i < 60; i++) holo.update(1 / 60);
assert.ok(edgesOf('armature')[0].material.opacity > 0.8, 'the part under the hand glows');
assert.ok(meshOf('armature').material.emissive.g > 0.3);
assert.ok(groups.get('armature').userData.holoLift > 0.1, 'ready to grab: it rises toward you');
assert.equal(groups.get('shaft').userData.holoLift, 0, 'other parts stay put');
holo.setFocus('armature', 1, true);
for (let i = 0; i < 60; i++) holo.update(1 / 60);
assert.ok(groups.get('armature').userData.holoLift < 0.01, 'held: no longer floating, it follows the hand');
holo.setFocus(null, 0, false);
for (let i = 0; i < 90; i++) holo.update(1 / 60);
assert.equal(meshOf('armature').material.emissive.getHex(), 0, 'the glow returns to its own colour afterwards');
holo.pulse('shaft'); holo.update(1 / 60);
assert.ok(edgesOf('shaft')[0].material.opacity > 0.4, 'a grab flashes');
holo.toggle();
assert.equal(holo.on, false); assert.equal(store.get('apex.study.holo'), 'off');
assert.ok(edgesOf('armature').every(l => !l.visible), 'Holo off hides the edges');
assert.equal(scene.background, null); assert.equal(scene.fog, null);
rendered = 0; holo.render(); assert.equal(rendered, 1, 'Holo off renders plainly');

// 6. Software rendering starts with the hologram off — unless chosen — and
//    stage 2 of the governor pauses the whole hologram when still slow.
for (const n of ['Google SwiftShader', 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)))', 'llvmpipe (LLVM 15.0.7, 256 bits)', 'Microsoft Basic Render Driver'])
  assert.ok(isSoftwareRenderer(n), n);
for (const n of ['ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)', 'Apple M2', '', null])
  assert.ok(!isSoftwareRenderer(n), String(n));
const fresh = new Map(), freshStore = {getItem: k => fresh.get(k) ?? null, setItem: (k, v) => fresh.set(k, v)};
const soft = setupHoloScene({THREE, scene: new THREE.Scene(), camera, renderer, groups: new Map(), reducedMotion: {matches: false}, storage: freshStore, software: true});
assert.equal(soft.on, false, 'no GPU: the hologram starts off'); assert.equal(soft.startedOff, true);
fresh.set('apex.study.holo', 'on');
assert.equal(setupHoloScene({THREE, scene: new THREE.Scene(), camera, renderer, groups: new Map(), reducedMotion: {matches: false}, storage: freshStore, software: true}).on, true,
  'switched on by hand, it stays on even without a GPU');
let bloomPaused = 0, holoPaused = 0;
fresh.clear();
const slow = setupHoloScene({THREE, scene: new THREE.Scene(), camera, renderer, groups: new Map(), reducedMotion: {matches: false}, storage: freshStore,
  onBloomPaused: () => bloomPaused++, onHoloPaused: () => holoPaused++});
for (let i = 0; i < 60; i++) slow.update(0.05);
assert.equal(bloomPaused, 1); assert.equal(slow.on, true, 'stage 1 only drops the glow');
for (let i = 0; i < 60; i++) slow.update(0.05);
assert.equal(holoPaused, 1); assert.equal(slow.on, false, 'still slow: the hologram pauses');
assert.equal(fresh.get('apex.study.holo'), undefined, 'a pause is for this session, not a saved choice');

console.log('PASS: pinch glow, slow-frame glow governor, silent-until-allowed sounds with a remembered mute, '
  + 'the hand drawn in light (with fade and no partial skeletons), and scene edges/glow/lift that react to the hand and toggle off cleanly.');
