/* Your own Apex model: any rigged humanoid glTF (.glb), driven by the same
   body language as the built-in suit. Put it at dashboard/static/avatars/apex.glb.

   Bones are found by name, across the usual conventions: Mixamo
   ("mixamorig:LeftForeArm"), VRoid/VRM ("J_Bip_L_LowerArm"), Blender
   ("UpperArm.L", "forearm.R"). Rotations are applied about the world's axes,
   so the rig's own bone axes don't matter, and arms are first brought down
   from a T-pose or A-pose to hang at the sides. The mouth uses a morph target
   if the model has one (jawOpen, mouthOpen, viseme_aa, A); otherwise a jaw
   bone; otherwise the voice shows only in the head and hands. */
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';

const HEIGHT = 1.9;

const role = name => window.ApexAvatarPose.boneRole(name);
const words = name => window.ApexAvatarPose.boneWords(name);

const MOUTHS = ['jawopen', 'mouthopen', 'viseme_aa', 'visemeaa', 'aa', 'a', 'mouth_open', 'jaw_open'];

export async function loadModel(url) {
  const gltf = await new GLTFLoader().loadAsync(url);
  const model = gltf.scene;
  // Stand it on the floor at Apex's height, measured on the posed surface:
  // a skinned mesh's plain bounds are its unposed geometry, often in other
  // units (Mixamo rigs carry a 0.01 scale), so they can be wildly off.
  const extent = () => {
    model.updateMatrixWorld(true);
    const box = new THREE.Box3();
    model.traverse(o => {
      if (o.isSkinnedMesh) { o.skeleton.update(); o.computeBoundingBox(); box.union(o.boundingBox.clone().applyMatrix4(o.matrixWorld)); }
      else if (o.isMesh) { o.geometry.computeBoundingBox(); box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld)); }
    });
    return box;
  };
  let box = extent();
  const tall = Math.max(box.max.y - box.min.y, 1e-6);
  model.scale.multiplyScalar(HEIGHT / tall);
  box = extent();
  model.position.y -= box.min.y;
  model.position.x -= (box.min.x + box.max.x) / 2;
  model.position.z -= (box.min.z + box.max.z) / 2;
  model.updateMatrixWorld(true);

  const bones = {};
  model.traverse(o => {
    if (!o.isBone) return;
    const r = role(o.name);
    if (!r) return;
    // The first match wins, except the chest prefers the highest spine bone.
    if (!bones[r] || (r === 'chest' && /2/.test(o.name))) bones[r] = o;
  });
  if (!bones.chest) bones.chest = bones.spine;
  const missing = ['head', 'lUpper', 'rUpper', 'lFore', 'rFore'].filter(r => !bones[r]);
  if (missing.length) throw Error(`This model has no ${missing.join(', ')} bone the character can drive.`);

  // Mouth: a morph target, else the jaw.
  let mouth = null;
  model.traverse(o => {
    if (mouth || !o.morphTargetDictionary) return;
    const key = Object.keys(o.morphTargetDictionary).find(k => MOUTHS.includes(k.toLowerCase()));
    if (key) mouth = {mesh: o, index: o.morphTargetDictionary[key]};
  });

  const rest = new Map(Object.values(bones).map(b => [b, b.quaternion.clone()]));
  // Arms down: the turn that points each upper arm from its rest direction
  // to hanging straight down, slightly out.
  const hang = {};
  const a = new THREE.Vector3(), b = new THREE.Vector3();
  for (const [key, out] of [['lUpper', 1], ['rUpper', -1]]) {
    const arm = bones[key], fore = bones[key[0] + 'Fore'];
    arm.getWorldPosition(a); fore.getWorldPosition(b);
    // Which way is "the model's left": the side its left arm is on.
    hang[key] = new THREE.Quaternion().setFromUnitVectors(b.sub(a).normalize(), new THREE.Vector3(out * 0.12, -1, 0).normalize());
  }
  const leftIsPlusX = (() => { bones.lUpper.getWorldPosition(a); bones.rUpper.getWorldPosition(b); return a.x > b.x; })();

  const parentQ = new THREE.Quaternion(), worldQ = new THREE.Quaternion(), e = new THREE.Euler();
  function turn(bone, worldTurn) {
    // bone.local = parent^-1 * worldTurn * parent * rest
    bone.parent.getWorldQuaternion(parentQ);
    bone.quaternion.copy(parentQ).invert().multiply(worldTurn).multiply(parentQ).multiply(rest.get(bone));
    bone.updateMatrixWorld(true);
  }
  const euler = (x, y, z) => worldQ.setFromEuler(e.set(x, y, z, 'XYZ'));
  // The suit's poses are written for its left arm on +x; mirror if needed.
  const mirror = leftIsPlusX ? 1 : -1;

  function apply(p, lv) {
    if (bones.chest) turn(bones.chest, euler(p.spineX, 0, p.spineZ));
    if (bones.neck) turn(bones.neck, euler(p.headX * 0.4, p.headY * 0.4, p.headZ * 0.4));
    turn(bones.head, euler(p.headX * (bones.neck ? 0.6 : 1), p.headY * (bones.neck ? 0.6 : 1), p.headZ * (bones.neck ? 0.6 : 1)));
    for (const [s, sign] of [['l', 1], ['r', -1]]) {
      const upper = bones[s + 'Upper'], fore = bones[s + 'Fore'];
      const shoulderX = s === 'l' ? p.lShoulderX : p.rShoulderX, shoulderZ = s === 'l' ? p.lShoulderZ : p.rShoulderZ;
      const elbowX = s === 'l' ? p.lElbowX : p.rElbowX;
      // Hang first, then the pose about the world's axes.
      worldQ.setFromEuler(e.set(shoulderX, 0, (shoulderZ - sign * 0.12) * mirror, 'XYZ')).multiply(hang[s + 'Upper']);
      turn(upper, worldQ);
      turn(fore, euler(elbowX, 0, 0));          // the elbow bends forward, about the world's x axis
    }
    if (mouth) mouth.mesh.morphTargetInfluences[mouth.index] = p.mouthOpen;
    else if (bones.jaw) turn(bones.jaw, euler(p.mouthOpen * 0.35, 0, 0));
  }

  const glowing = new Set();
  model.traverse(o => {
    if (!o.isMesh) return;
    o.frustumCulled = false;
    for (const m of [].concat(o.material)) if (m.emissive && (m.emissive.r + m.emissive.g + m.emissive.b > 0.05 || m.emissiveMap)) glowing.add(m);
  });
  return {object: model, apply, glowing, bones: Object.fromEntries(Object.entries(bones).map(([k, v]) => [k, v.name])), mouth: mouth ? 'morph' : bones.jaw ? 'jaw' : 'none'};
}
