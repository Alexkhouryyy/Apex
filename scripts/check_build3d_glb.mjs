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
// Placed as syncModels places a card at the middle of the board.
const place = /g\.position\.set\(c\.x, (-?)c\.y, 0\)/.exec(html);
assert.ok(place, 'syncModels no longer places models the way this check expects — update the check');
wrap.position.set(0.5, place[1] === '-' ? -0.5 : 0.5, 0); wrap.updateMatrixWorld(true);
const ndc = new THREE.Vector3(0.5, place[1] === '-' ? -0.5 : 0.5, 0).project(camera);
assert.ok(Math.abs(ndc.x) < 1e-6 && Math.abs(ndc.y) < 1e-6, 'a card at the middle of the board must be drawn at the middle of the screen');
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

console.log('PASS: a board_build GLB loads in the board\'s own three.js loader with its parts, colours and true size in metres, '
  + 'and the board\'s fitToUnitBox shows it the right way up with its outside facing the camera.');
