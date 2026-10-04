// The Apex character's body language (dashboard/static/avatar-pose.js), as numbers.
const assert = require('node:assert/strict');
const P = require('../dashboard/static/avatar-pose.js');

// The mouth follows the voice only while speaking, and is shut in silence.
assert.equal(P.pose('speaking', 3, 0).mouthOpen, 0, 'silent: mouth shut');
assert.ok(P.pose('speaking', 3, 0.3).mouthOpen < P.pose('speaking', 3, 0.6).mouthOpen, 'louder opens wider');
assert.equal(P.pose('speaking', 3, 2).mouthOpen, 1, 'never past fully open');
for (const s of ['', 'listening', 'thinking']) assert.equal(P.pose(s, 3, 0.9).mouthOpen, 0, `${s || 'idle'}: no talking without speech`);
assert.ok(P.pose('speaking', 3, 0.9).coreGlow > P.pose('speaking', 3, 0).coreGlow, 'the core brightens with the voice');

// Each state reads differently.
assert.ok(P.pose('listening', 3, 0).spineX > 0.05, 'listening leans in');
assert.equal(P.pose('listening', 3, 0).mood, 'listen');
const think = P.pose('thinking', 3, 0);
assert.ok(think.rElbowX < -2 && think.rShoulderX < -0.4, 'thinking brings a hand up to the face');
assert.ok(think.headX < 0, 'thinking looks up');
assert.equal(P.pose('', 3, 0).mood, 'idle');

// Talking with its hands: more movement when louder.
const quiet = P.pose('speaking', 3, 0.1), loud = P.pose('speaking', 3, 0.9);
assert.ok(loud.rElbowX < quiet.rElbowX && loud.lElbowX < quiet.lElbowX, 'louder speech, bigger gestures');

// Reduced motion: no sway or gestures, but the mouth and blinks still work.
const calm = P.pose('speaking', 3, 0.9, {reduced: true});
assert.equal(calm.shift, 0); assert.equal(calm.rShoulderX, 0); assert.equal(calm.rElbowX, P.REST.rElbowX);
assert.ok(calm.mouthOpen > 0.9);
assert.equal(P.pose('thinking', 3, 0, {reduced: true}).rElbowX, P.REST.rElbowX, 'no hand to chin with reduced motion');

// Gaze follows the pointer, within limits.
assert.ok(P.pose('', 0, 0, {gazeX: 1, reduced: true}).headY > 0.3);
assert.equal(P.pose('', 0, 0, {gazeX: 5, reduced: true}).headY, P.pose('', 0, 0, {gazeX: 1, reduced: true}).headY);

// Blinks: about 10–20 a minute, eyes open over 95% of the time.
let blinks = 0, shut = 0, wasOpen = true;
for (let t = 0; t < 60; t += 0.005) {
  const o = P.eyeOpen(t); if (o < 0.5) shut++;
  if (o < 0.5 && wasOpen) blinks++; wasOpen = o >= 0.5;
}
assert.ok(blinks >= 10 && blinks <= 20, `blinks per minute: ${blinks}`);
assert.ok(shut * 0.005 / 60 < 0.05, 'eyes mostly open');

// Smoothing: opens fast on a syllable, closes slower.
assert.ok(P.smooth(0, 1) > 1 - P.smooth(1, 0), 'rises faster than it falls');

// The suit's lights: eyes brighten when listening, a scan sweeps while thinking.
assert.ok(P.pose('listening', 3, 0).eyeGlow > P.pose('', 3, 0).eyeGlow);
assert.ok(P.pose('thinking', 3.2, 0).scan > 0 && P.pose('thinking', 3.2, 0, {reduced: true}).scan === 0);
// The vocal grille: silent is flat; speech raises the middle bars most.
assert.deepEqual(P.voiceBars(2, 0), [0, 0, 0, 0, 0]);
const bars = P.voiceBars(2, 0.8);
assert.ok(bars.every(b => b > 0 && b <= 1) && bars[2] > bars[0] && bars[2] > bars[4], `bars ${bars}`);

// Your own model: bones found by name across rig conventions.
const roles = {
  'mixamorig:LeftArm': 'lUpper', 'mixamorig:LeftForeArm': 'lFore', 'mixamorig:RightArm': 'rUpper', 'mixamorigRightForeArm': 'rFore',
  'mixamorig:Spine2': 'chest', 'mixamorig:Spine': 'spine', 'mixamorig:Head': 'head', 'mixamorig:HeadTop_End': null,
  'mixamorig:LeftHand': null, 'mixamorig:LeftShoulder': null, 'mixamorig:LeftHandIndex1': null,
  'J_Bip_L_UpperArm': 'lUpper', 'J_Bip_R_LowerArm': 'rFore', 'J_Bip_C_Chest': 'chest', 'J_Bip_C_Hips': 'hips',
  'UpperArm.L': 'lUpper', 'forearm.R': 'rFore', 'LowerArmL': 'lFore', 'Torso': 'chest', 'Jaw': 'jaw', 'DEF-upper_arm.L': 'lUpper',
};
for (const [name, want] of Object.entries(roles)) assert.equal(P.boneRole(name), want, name);

console.log(`PASS: mouth follows the voice only while speaking, states read differently, gestures scale with loudness, reduced motion keeps only mouth and blinks, gaze is bounded, ${blinks} blinks a minute, smoothing rises fast and falls slow, the suit's lights and grille follow state and voice, and bones are recognised across Mixamo, VRoid and Blender names.`);
