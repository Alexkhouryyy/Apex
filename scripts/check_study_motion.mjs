// Sampled study motion (study-motion.js) and the page's pose() with real Three.js,
// against the car engine and heart the generator wrote.
import * as THREE from '../dashboard/static/vendor/three/build/three.module.min.js';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {hasTracks, advance, sampleTrack, phaseAt} from '../dashboard/static/study-motion.js';

const car = JSON.parse(fs.readFileSync('data/assemblies/car-engine.json', 'utf8'));
const heart = JSON.parse(fs.readFileSync('data/assemblies/heart.json', 'utf8'));
const jet = JSON.parse(fs.readFileSync('data/assemblies/jet-engine.json', 'utf8'));
assert(hasTracks(car.motion) && hasTracks(heart.motion) && !hasTracks(jet.motion), 'jet keeps its spin');
const N = car.motion.samples;

// Timing: a cycle takes cycle_seconds, wraps, and never goes negative.
assert.equal(advance(0, car.motion.cycle_seconds / 4, car.motion), .25);
assert(Math.abs(advance(.9, car.motion.cycle_seconds * .2, car.motion) - .1) < 1e-12);
// Interpolation between samples, and both ends of the cycle meet.
const crank = car.motion.tracks.crankshaft;
assert(Math.abs(sampleTrack(crank, .5 / N, N).angle - (crank.angle[0] + crank.angle[1]) / 2) < 1e-12);
assert(Math.abs(sampleTrack(crank, 1, N).angle - 4 * Math.PI) < 1e-4, 'two crank turns per cycle');
const cam = car.motion.tracks['intake-camshaft'];
assert(Math.abs(sampleTrack(cam, .5, N).angle - Math.PI) < 1e-4, 'camshafts turn at half speed');
for (const t of Object.values(car.motion.tracks)) {
  const a = sampleTrack(t, 0, N), b = sampleTrack(t, 1, N);
  assert(Math.abs(a.scale - b.scale) < 1e-9 && Math.abs(a.glow - b.glow) < 1e-6 && a.offset.every((v, k) => Math.abs(v - b.offset[k]) < 1e-6));
}
// Captions: firing order 1-3-4-2 across the four half-turns.
const power = [0, .25, .5, .75].map(u => phaseAt(car.motion, u + .01).match(/Power (\d)/)[1]);
assert.deepEqual(power, ['4', '2', '1', '3']);
assert.match(phaseAt(heart.motion, .3), /aortic & pulmonary valves open/);
assert.match(phaseAt(heart.motion, .7), /tricuspid & mitral/);
// Heart: ventricles smallest while they eject; aortic valve lit only then.
const lv = heart.motion.tracks['left-ventricle'], av = heart.motion.tracks['aortic-valve'];
assert(sampleTrack(lv, .35, heart.motion.samples).scale < .9 && sampleTrack(lv, .8, heart.motion.samples).scale === 1);
assert(sampleTrack(av, .3, heart.motion.samples).glow > .99 && sampleTrack(av, .7, heart.motion.samples).glow === 0);

// pose() from the page, with real Three.js and the page's model units.
const source = fs.readFileSync('dashboard/static/study.js', 'utf8');
const code = source.slice(source.indexOf('function tracksLive('), source.indexOf('// A part\'s resting glow'));
const center = new THREE.Vector3(.1, 1.2, -.2), unit = 4.5 / 6.1;
const env = {current: {rotating: true, explosion: 0, isolated: false, transforms: {}}, motionU: 0};
const {pose, live} = new Function('THREE', 'env', 'hasTracks', 'sampleTrack', 'manifest', 'camera', `
  let manipulation=null,amount=0,rotorAngle=0;
  const state=env;
  ${code.replace(/\bcurrent\b/g, 'state.current').replace(/\bmotionU\b/g, 'state.motionU')}
  return {pose, live: tracksLive};`)(THREE, env, hasTracks, sampleTrack, car, new THREE.PerspectiveCamera());
const scene = p => new THREE.Vector3(...p).sub(center).multiplyScalar(unit);
function group(id, point) {
  const t = car.motion.tracks[id], g = new THREE.Group();
  g.userData = {id, offset: new THREE.Vector3(), center: scene(point), track: {data: t, unit, glow: new THREE.Color(),
    axis: new THREE.Vector3(...(t.axis || [1, 0, 0])).normalize(), pivot: scene(t.pivot)}};
  return g;
}
// Rod 1's small end must ride piston 1's wrist pin through the whole cycle.
const rod = car.motion.tracks['rod-1'], piston = car.motion.tracks['piston-1'];
const THROW = .4, ROD = 1.3, x1 = -1.35, pinY = THROW + ROD;          // piston 1 starts at the top
const g = group('rod-1', [x1, 1, 0]);
for (const u of [0, .13, .37, .5, .77, .99]) {
  env.motionU = u;assert(live());pose(g, true);g.updateMatrix();
  const small = scene([x1, pinY, 0]).applyMatrix4(g.matrix);
  const want = scene([x1, pinY + sampleTrack(piston, u, N).offset[1], 0]);
  assert(small.distanceTo(want) < 2e-3, `rod follows piston at u=${u}: ${small.distanceTo(want)}`);
}
// Exploding or moving a part shows the rest pose; a paused cycle is kept.
env.current = {rotating: false, explosion: .5, isolated: false, transforms: {}};env.motionU = .3;assert(!live());
env.current = {rotating: false, explosion: 0, isolated: false, transforms: {}};assert(live(), 'paused mid-cycle stays posed');
env.motionU = 0;assert(!live(), 'never played: rest pose');
console.log('PASS: motion timing, sampling, loop seam, captions (firing order 1-3-4-2), heart valve/chamber timing, and pose() keeps rod on piston with real Three.js.');
