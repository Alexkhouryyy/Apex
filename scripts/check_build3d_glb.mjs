// A board_build GLB, opened by the SAME three.js GLTFLoader the board uses,
// and placed by the board's OWN fitToUnitBox (read out of board.html) — then
// asked the questions a person would: is it the right size, are the parts all
// there with their colours, and on this board's camera, is it the right way up?
// No browser and no GPU: the loader parses in Node, and "which way up" is the
// camera's projection of the model's top and bottom.
import {register} from 'node:module';
import {execFileSync} from 'node:child_process';
import {readFileSync, mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const threeUrl = pathToFileURL(join(root, 'dashboard/static/vendor/three/build/three.module.min.js')).href;
// GLTFLoader imports the bare name "three", which the board's import map
// resolves in the browser; this does the same for Node.
register('data:text/javascript,' + encodeURIComponent(
  `export async function resolve(s, c, next) { return s === 'three' ? {url: ${JSON.stringify(threeUrl)}, shortCircuit: true} : next(s, c); }`));
const THREE = await import('three');
const {GLTFLoader} = await import(pathToFileURL(join(root, 'dashboard/static/vendor/three/examples/jsm/loaders/GLTFLoader.js')).href);

// A rocket: 60 cm white body standing on y=0, red nose on top.
const out = join(mkdtempSync(join(tmpdir(), 'apex-b3-')), 'rocket.glb');
execFileSync(process.env.PYTHON || 'python', ['-c', `
import sys; sys.path.insert(0, ${JSON.stringify(root)})
from agent import build3d
parts = build3d.validate([
  {"shape": "cylinder", "size": [20, 60, 20], "at": [0, 30, 0], "color": "white", "name": "body"},
  {"shape": "cone", "size": [20, 25, 20], "at": [0, 72.5, 0], "color": "red", "name": "nose"},
  {"shape": "torus", "size": [30, 4, 30], "at": [0, 5, 0], "color": "#ffaa00", "name": "ring"}])
open(${JSON.stringify(out)}, "wb").write(build3d.to_glb(parts, "Rocket"))
import json; open(${JSON.stringify(out + '.json')}, "w").write(json.dumps(parts))
`], {stdio: 'inherit'});

const buf = readFileSync(out);
const gltf = await new Promise((ok, fail) =>
  new GLTFLoader().parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), '', ok, fail));

// 1. The parts, by name, with their colours.
const meshes = {};
gltf.scene.traverse(o => { if (o.isMesh) meshes[o.name] = o; });
assert.deepEqual(Object.keys(meshes).sort(), ['body', 'nose', 'ring']);
// glTF colours are linear, and so is three.js's working colour: compare those.
const red = meshes.nose.material.color;
assert.ok(Math.abs(red.r - 0.8) < 1e-3 && Math.abs(red.g - 0.05) < 1e-3, 'the nose must be the red that was asked for');

// 2. True size, in metres: 20 cm wide, 85 cm tall (0 .. 72.5 + 12.5).
const box = new THREE.Box3().setFromObject(gltf.scene), size = box.getSize(new THREE.Vector3());
assert.ok(Math.abs(size.y - 0.85) < 1e-3, `height ${size.y} m, expected 0.85`);
assert.ok(Math.abs(size.x - 0.30) < 1e-3, `width ${size.x} m (the ring is 30 cm)`);
assert.ok(Math.abs(box.min.y) < 1e-3, 'it stands on y = 0');

// 3. On the board: the board's own fitToUnitBox and camera. The nose must be
//    ABOVE the body on screen, and the camera must see the outside of the
//    body (its front faces), not the inside.
const html = readFileSync(join(root, 'dashboard/static/board.html'), 'utf8');
const fit = /function fitToUnitBox\(obj\) \{[\s\S]*?\n\}/.exec(html)[0];
const tilt = /const MODEL_TILT = ([0-9.]+)/.exec(html);
const fitToUnitBox = new Function('THREE', 'MODEL_TILT', `${fit}; return fitToUnitBox;`)(THREE, tilt ? Number(tilt[1]) : 0);
const cam = /new THREE\.OrthographicCamera\(([^)]*)\)/.exec(html)[1].split(',').map(Number);
const camera = new THREE.OrthographicCamera(...cam);                   // exactly as init3D()
camera.position.z = 10; camera.updateMatrixWorld(); camera.updateProjectionMatrix();
const wrap = fitToUnitBox(gltf.scene);
// Placed by the page's own placeModel, as syncModels places every model.
const placeSrc = /function placeModel\(g, c, base, aspect\) \{[\s\S]*?\n\}/.exec(html)[0];
const placeModel = new Function(`${placeSrc}; return placeModel;`)();
const base = wrap.scale.x;
const toBoard = v => { const n = v.clone().project(camera); return [(n.x + 1) / 2, (1 - n.y) / 2, n.z]; };
placeModel(wrap, {x: 0.5, y: 0.5, scale: 1, rot: 0}, base, 1); wrap.updateMatrixWorld(true);
const mid = toBoard(new THREE.Vector3().setFromMatrixPosition(wrap.matrixWorld));
assert.ok(Math.abs(mid[0] - 0.5) < 1e-6 && Math.abs(mid[1] - 0.5) < 1e-6, 'a card at the middle of the board must be drawn at the middle of the screen');
const screenY = mesh => {
  const c = new THREE.Box3().setFromObject(mesh).getCenter(new THREE.Vector3());
  return c.project(camera).y;           // NDC: +1 is the top of the screen
};
assert.ok(screenY(meshes.nose) > screenY(meshes.body),
  'on the board the nose must be drawn ABOVE the body — the model is upside down');
// Front faces are counter-clockwise on screen. Take the body's triangle
// nearest the camera and check its winding as the camera sees it.
const g = meshes.body.geometry, pos = g.attributes.position, idx = g.index;
const m = meshes.body.matrixWorld;
let best = null;
for (let t = 0; t < idx.count; t += 3) {
  const w = [0, 1, 2].map(k => new THREE.Vector3().fromBufferAttribute(pos, idx.getX(t + k)).applyMatrix4(m));
  const z = (w[0].z + w[1].z + w[2].z) / 3;
  if (!best || z > best.z) best = {z, w};
}
const s = best.w.map(v => v.clone().project(camera));
const area = (s[1].x - s[0].x) * (s[2].y - s[0].y) - (s[2].x - s[0].x) * (s[1].y - s[0].y);
// three.js flips the front face when an object's matrix is mirrored
// (negative determinant) — account for it as the renderer does.
const mirrored = m.determinant() < 0;
assert.ok((area > 0) !== mirrored, 'the camera would see the inside of the model (its back faces)');

// 4. True proportions on a WIDE window: before placeModel's aspect term the
//    camera stretched every model sideways by the window's shape. The body is
//    20 cm by 60 cm; in pixels it must stay 1 : 3 (less the tilt's
//    foreshortening of the height, cos 0.35, plus the depth it shows).
const W = 1200, H = 700;
placeModel(wrap, {x: 0.5, y: 0.5, scale: 1, rot: 0}, base, W / H); wrap.updateMatrixWorld(true);
const bodyBox = new THREE.Box3();
{
  const bp = meshes.body.geometry.attributes.position, bm = meshes.body.matrixWorld;
  for (let i = 0; i < bp.count; i++) {
    const [x, y] = toBoard(new THREE.Vector3().fromBufferAttribute(bp, i).applyMatrix4(bm));
    bodyBox.expandByPoint(new THREE.Vector3(x * W, y * H, 0));
  }
}
const px = bodyBox.getSize(new THREE.Vector3());
const tiltA = tilt ? Number(tilt[1]) : 0;
const expected = (0.60 * Math.cos(tiltA) + 0.20 * Math.sin(tiltA)) / 0.20;
assert.ok(Math.abs(px.y / px.x - expected) < 0.02,
  `drawn ${px.x.toFixed(1)} x ${px.y.toFixed(1)} px: height/width ${(px.y / px.x).toFixed(3)}, true ${expected.toFixed(3)} — the model is stretched`);

// 5. Python's idea of where each part is drawn (agent/board_parts.py, used to
//    pick the part under a pinch) must match what three.js draws — at an odd
//    place, scale, turn and window shape — or a pinch grabs the wrong part.
const card = {x: 0.31, y: 0.62, scale: 1.4, rot: 0.7};
placeModel(wrap, card, base, W / H); wrap.updateMatrixWorld(true);
const drawn = {};
for (const name of ['body', 'nose', 'ring']) {
  const c = meshes[name].userData.centre;
  assert.ok(Array.isArray(c) && meshes[name].userData.part !== undefined, `the ${name} must carry its part index and centre`);
  drawn[name] = toBoard(new THREE.Vector3(...c).applyMatrix4(meshes[name].parent.matrixWorld));
}
const py = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import sys, json; sys.path.insert(0, ${JSON.stringify(root)})
import numpy as np
from agent import board_parts
parts = json.load(open(${JSON.stringify(out + '.json')}))
card = ${JSON.stringify(card)}
pts = np.array([p["at"] for p in parts]) / 100.0
proj = board_parts.project(card, parts, pts, ${W / H})
picks = [board_parts.pick(card, parts, float(x), float(y), ${W / H}) for x, y, _ in proj]
print(json.dumps({"proj": proj.tolist(), "picks": picks}))
`]).toString());
['body', 'nose', 'ring'].forEach((name, i) => {
  const [x, y] = py.proj[i], [dx, dy] = drawn[name];
  assert.ok(Math.hypot(x - dx, y - dy) < 1e-4,
    `the ${name}: Python puts it at (${x.toFixed(4)}, ${y.toFixed(4)}), three.js draws it at (${dx.toFixed(4)}, ${dy.toFixed(4)})`);
});
assert.equal(py.picks[1], 1, 'a pinch on the nose must pick the nose');

console.log('PASS: a board_build GLB loads in the board\'s own three.js loader with its parts, colours and true size in metres, '
  + 'and the board\'s fitToUnitBox and placeModel show it the right way up, outside facing the camera, in true proportion on a wide window, '
  + 'exactly where agent/board_parts.py computes each part to be.');
