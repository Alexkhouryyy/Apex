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
    } else if (state === 'thinking') {
      p.mood = 'think';
      p.headX = -0.15; p.headY += 0.22; p.headZ = -0.06;   // looks up and aside
      if (!calm) { p.rShoulderX = -0.6; p.rShoulderZ = 0.55; p.rElbowX = -2.3; }   // hand to chin
      p.coreGlow = 0.6 + 0.2 * Math.sin(t * 4);
      p.coreSpin = 1.6;
    } else if (state === 'speaking') {
      p.mood = 'speak';
      p.mouthOpen = lv > 0.02 ? Math.min(1, lv * 1.6) : 0;
      p.mouthWide = 1 - 0.3 * p.mouthOpen;      // rounder as it opens
      p.headX += 0.06 * lv;                     // small nods on stressed words
      p.coreGlow = 0.6 + 0.8 * lv;
      p.coreSpin = 0.6;
      if (!calm) {                              // talks with its hands, more when louder
        p.rShoulderX = -0.35 - 0.55 * lv * (0.5 + 0.5 * Math.sin(t * 2.1));
        p.rElbowX = -0.6 - 0.9 * lv;
        p.lShoulderX = -0.15 - 0.35 * lv * (0.5 + 0.5 * Math.sin(t * 1.7 + 1));
        p.lElbowX = -0.4 - 0.6 * lv;
      }
    }
    return p;
  }

  // Loudness rises fast and falls slower, so the mouth snaps open on a
  // syllable and closes naturally instead of flickering every frame.
  function smooth(previous, raw) {
    return previous + (raw - previous) * (raw > previous ? 0.5 : 0.18);
  }

  const api = {pose, eyeOpen, smooth, REST};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ApexAvatarPose = api;
})(typeof window !== 'undefined' ? window : globalThis);
