// Take-apart for imported models (dashboard/static/study-import.js autoExplode),
// with the real three.js: parts stacked on one axis spread along that axis in
// order; concentric parts spread along the model's longest side; off-centre
// parts also move outward; a single piece stays put.
import {join, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
globalThis.document = {getElementById: () => null};
const THREE = await import(pathToFileURL(join(root, 'dashboard/static/vendor/three/build/three.module.min.js')).href);
const {autoExplode} = await import(pathToFileURL(join(root, 'dashboard/static/study-import.js')).href);
const V = (...a) => new THREE.Vector3(...a);
const entries = centers => centers.map(c => ({center: V(...c), offset: V()}));

// 1. A motor stacked along z: order along z is kept and the spread is wide.
const stack = entries([[0, 0, -0.3], [0, 0, 0.1], [0, 0, -0.1], [0, 0, 0.3]]);
autoExplode(THREE, stack, V(4.5, 4.5, 1.5));
const z = stack.map(e => e.offset.z);
assert.ok(z[0] < z[2] && z[2] < z[1] && z[1] < z[3], 'parts keep their order along the stacking axis');
assert.ok(z[3] - z[0] > 4, 'the spread is wide enough to see each part');
assert.ok(stack.every(e => Math.abs(e.offset.x) < 1e-6 && Math.abs(e.offset.y) < 1e-6), 'on-axis parts stay on the axis');

// 2. All concentric (same centre): spread along the longest side (here x).
const same = entries([[0, 0, 0], [0, 0, 0], [0, 0, 0]]);
autoExplode(THREE, same, V(6, 1, 1));
assert.ok(same.every(e => Math.abs(e.offset.y) < 1e-6 && Math.abs(e.offset.z) < 1e-6));
assert.equal(new Set(same.map(e => e.offset.x.toFixed(3))).size, 3, 'each concentric part gets its own place');

// 3. Off-centre parts also move outward, away from the axis.
const ring = entries([[0, 0, -0.4], [0, 0, 0.4], [1.5, 0, 0], [-1.5, 0, 0.05]]);
autoExplode(THREE, ring, V(4.5, 4.5, 2));
const axis = V(0, 0, 1);
const radialOut = (e, c) => e.offset.clone().sub(axis.clone().multiplyScalar(e.offset.dot(axis))).dot(V(...c).setZ(0).normalize());
assert.ok(radialOut(ring[2], [1.5, 0, 0]) > 0.5 && radialOut(ring[3], [-1.5, 0, 0]) > 0.5, 'off-centre parts move outward');

// 4. One piece: nothing to take apart.
const one = entries([[0.3, 0.2, 0.1]]);
autoExplode(THREE, one, V(1, 1, 1));
assert.equal(one[0].offset.length(), 0);

console.log('PASS: imported models come apart along their stacking axis in order (or their longest side when concentric), off-centre parts move outward, and a single piece stays put.');
