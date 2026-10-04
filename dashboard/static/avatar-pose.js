/* The Apex character's body language, as numbers (no drawing here).
   pose(state, t, level, opts) -> joint angles and face values for one frame;
   avatar.js applies them to the 3D body. Kept apart so it can be tested
   without a GPU (scripts/check_avatar_pose.cjs).

   state: '' (idle), 'listening', 'thinking', 'speaking'
   t: seconds; level: voice loudness 0..1 (already smoothed)
   opts: {gazeX, gazeY} in -1..1 (where the pointer is), reduced (prefers
   reduced motion: no sway or gestures; the mouth and blinks still work) */
(function (root) {
  'use strict';
  const REST = {
    breathe: 1, shift: 0, spineX: 0, spineZ: 0, headX: 0, headY: 0, headZ: 0,
    lShoulderX: 0, lShoulderZ: 0.12, lElbowX: -0.15,
    rShoulderX: 0, rShoulderZ: -0.12, rElbowX: -0.15,
    mouthOpen: 0, mouthWide: 1, eyeOpen: 1, coreGlow: 0.55, coreSpin: 0.2, mood: 'idle',
    eyeGlow: 0.8, hudSpeed: 0.2, scan: 0,
  };
  const BLINK_EVERY = 4.2, BLINK_LONG = 0.14;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // Eyes close briefly about every 4 s, at a jittered but repeatable moment.
  function eyeOpen(t, seed = 0) {
    const k = Math.floor(t / BLINK_EVERY);
    const jitter = Math.abs(Math.sin(k * 12.9898 + seed * 78.233) * 43758.5453) % 1 * 3;
    const phase = t - k * BLINK_EVERY - jitter;
    return phase >= 0 && phase < BLINK_LONG ? 1 - Math.sin(Math.PI * phase / BLINK_LONG) : 1;
  }

  function pose(state, t, level = 0, opts = {}) {
    const p = Object.assign({}, REST);
    const calm = Boolean(opts.reduced);
    const gx = clamp(opts.gazeX || 0, -1, 1), gy = clamp(opts.gazeY || 0, -1, 1);
    const lv = clamp(level, 0, 1);
    p.eyeOpen = eyeOpen(t, opts.seed);
    p.breathe = 1 + (calm ? 0 : 0.012 * Math.sin(t * 1.7));
    p.shift = calm ? 0 : 0.012 * Math.sin(t * 0.45);
    p.spineZ = calm ? 0 : 0.015 * Math.sin(t * 0.45);
    p.headY = gx * 0.35 + (calm ? 0 : 0.06 * Math.sin(t * 0.31));
    p.headX = -gy * 0.2 + (calm ? 0 : 0.03 * Math.sin(t * 0.53));
    if (!calm) { p.lShoulderX = 0.04 * Math.sin(t * 0.7); p.rShoulderX = 0.04 * Math.sin(t * 0.7 + 2); }

    if (state === 'listening') {
      p.mood = 'listen';
      p.spineX = 0.09;                         // leans in
      p.headZ = calm ? 0 : 0.12;               // head tilted, attentive
      p.coreGlow = 0.7 + (calm ? 0 : 0.15 * Math.sin(t * 3));
      p.eyeGlow = 1.25; p.hudSpeed = 0.6;       // eyes brighten: it is paying attention
    } else if (state === 'thinking') {
      p.mood = 'think';
      p.headX = -0.15; p.headY += 0.22; p.headZ = -0.06;   // looks up and aside
      if (!calm) { p.rShoulderX = -0.6; p.rShoulderZ = 0.55; p.rElbowX = -2.3; }   // hand to chin
      p.coreGlow = 0.6 + 0.2 * Math.sin(t * 4);
      p.coreSpin = 1.6;
      p.eyeGlow = 0.9; p.hudSpeed = 1.2;
      p.scan = calm ? 0 : (t * 0.9) % 1;          // a light sweeping across the eyes
    } else if (state === 'speaking') {
      p.mood = 'speak';
      p.mouthOpen = lv > 0.02 ? Math.min(1, lv * 1.6) : 0;
      p.mouthWide = 1 - 0.3 * p.mouthOpen;      // rounder as it opens
      p.headX += 0.06 * lv;                     // small nods on stressed words
      p.coreGlow = 0.6 + 0.8 * lv;
      p.coreSpin = 0.6;
      p.eyeGlow = 0.95 + 0.45 * lv; p.hudSpeed = 0.4 + 0.6 * lv;
      if (!calm) {                              // talks with its hands, more when louder
        p.rShoulderX = -0.35 - 0.55 * lv * (0.5 + 0.5 * Math.sin(t * 2.1));
        p.rElbowX = -0.6 - 0.9 * lv;
        p.lShoulderX = -0.15 - 0.35 * lv * (0.5 + 0.5 * Math.sin(t * 1.7 + 1));
        p.lElbowX = -0.4 - 0.6 * lv;
      }
    }
    return p;
  }

  // The vocal grille: five bars from one loudness value, tallest in the middle,
  // each wobbling at its own rate so it reads as a voice, not a single light.
  const BAR_SHAPE = [0.55, 0.85, 1, 0.85, 0.55];
  function voiceBars(t, level) {
    const lv = clamp(level, 0, 1);
    return BAR_SHAPE.map((w, i) => lv < 0.02 ? 0 : clamp(lv * w * (0.7 + 0.3 * Math.sin(t * (9 + i * 3.7) + i)), 0, 1));
  }

  // Loudness rises fast and falls slower, so the mouth snaps open on a
  // syllable and closes naturally instead of flickering every frame.
  function smooth(previous, raw) {
    return previous + (raw - previous) * (raw > previous ? 0.5 : 0.18);
  }

  // Which body part a rig's bone is, from its name, across the usual
  // conventions: Mixamo ("mixamorig:LeftForeArm"), VRoid/VRM
  // ("J_Bip_L_LowerArm"), Blender ("UpperArm.L", "forearm.R"). Used by
  // avatar-model.js to drive your own model.
  const boneWords = name => String(name).replace(/^mixamorig\d*[:_]?/i, '').replace(/([a-z])([A-Z])/g, '$1 $2')
    .toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  function boneRole(name) {
    const t = boneWords(name), has = w => t.includes(w);
    if (t.some(w => /^(end|top|tip|twist|roll|ik|ctrl|pole|target)$/.test(w))) return null;
    if (t.some(w => /^(thumb|index|middle|ring|pinky|toe|eye|finger)/.test(w))) return null;
    const side = has('left') || has('l') ? 'l' : has('right') || has('r') ? 'r' : '';
    if (has('hips') || has('pelvis')) return 'hips';
    if (has('head')) return 'head';
    if (has('neck')) return 'neck';
    if (has('chest') || has('torso') || has('spine2') || (has('spine') && (has('2') || has('upper')))) return 'chest';
    if (has('spine') || has('spine1')) return 'spine';
    if (side && (has('forearm') || (has('fore') && has('arm')) || (has('lower') && has('arm')))) return side + 'Fore';
    if (side && has('arm') && !has('hand')) return side + 'Upper';
    if (has('jaw')) return 'jaw';
    return null;
  }

  const api = {pose, eyeOpen, smooth, voiceBars, boneRole, boneWords, REST};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ApexAvatarPose = api;
})(typeof window !== 'undefined' ? window : globalThis);
