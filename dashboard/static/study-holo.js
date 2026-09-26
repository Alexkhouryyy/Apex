// Phase 2a — the hologram look for the study: a hand drawn in light, glowing
// part edges with bloom, parts that react to the hand, and soft sounds.
// Presentation only: nothing here decides a grab, moves a part for real, or
// talks to the server. The gesture gate (study-hands.js) and the committed
// study state stay exactly as they were; this reads them and draws.
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';

// MediaPipe's hand skeleton: which of the 21 joints are joined by a bone.
export const BONES = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],
  [9,13],[13,14],[14,15],[15,16],[13,17],[17,18],[18,19],[19,20],[0,17]];
const THUMB = 4, INDEX = 8;

// How closed the pinch is, 0 (wide open) to 1 (at the pinch threshold), for
// the glow between thumb and index. Visual only: whether it IS a pinch is the
// tracker's latched decision (h.pinched), never this number.
export function pinchGlow(ratio, threshold, release) {
  if (!Number.isFinite(ratio) || !Number.isFinite(threshold)) return 0;
  const open = Math.max(Number.isFinite(release) ? release * 2 : 0, threshold + 0.45);
  return Math.max(0, Math.min(1, (open - ratio) / (open - threshold)));
}

// --- The hand, drawn in light --------------------------------------------
export class HoloHand {
  constructor(canvas) { this.canvas = canvas; this.ctx = canvas.getContext('2d'); this.hand = null; this.seenAt = 0; this.state = 'tracking'; }
  set(h, state, now) { if (h) { this.hand = h; this.seenAt = now; } this.state = state || 'tracking'; }
  clear() { this.hand = null; }
  draw(now) {
    const c = this.canvas, ctx = this.ctx, r = c.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
    if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, r.width, r.height);
    const h = this.hand;
    // A hand not seen for a moment fades out rather than freezing in place.
    const fade = h ? Math.max(0, 1 - (now - this.seenAt - 120) / 250) : 0;
    if (!h || fade <= 0 || !Array.isArray(h.joints) || h.joints.length !== 21) return;
    const P = h.joints.map(([x, y]) => [x * r.width, y * r.height]);
    const glow = h.pinched ? 1 : pinchGlow(h.ratio, h.threshold, h.release);
    const hot = this.state === 'held' ? '#f2fffd' : '#8ff6ff';
    ctx.globalAlpha = fade; ctx.lineCap = 'round'; ctx.shadowColor = '#34e1ff';
    // Bones: a soft wide glow, then a fine bright core.
    for (const [width, alpha, blur] of [[7, .18, 18], [1.6, .9, 8]]) {
      ctx.lineWidth = width; ctx.strokeStyle = `rgba(90,220,255,${alpha})`; ctx.shadowBlur = blur;
      ctx.beginPath(); for (const [a, b] of BONES) { ctx.moveTo(...P[a]); ctx.lineTo(...P[b]); } ctx.stroke();
    }
    ctx.shadowBlur = 10;
    for (let i = 0; i < 21; i++) {
      const tip = i === THUMB || i === INDEX, size = tip ? 3.2 + glow * 3.5 : 2.2;
      ctx.fillStyle = tip ? hot : 'rgba(180,245,255,.85)'; ctx.beginPath(); ctx.arc(...P[i], size, 0, Math.PI * 2); ctx.fill();
    }
    // The pinch: a filament between thumb and index that brightens as they
    // close, and a ring that snaps shut when the tracker says "pinched".
    const [tx, ty] = P[THUMB], [ix, iy] = P[INDEX];
    ctx.lineWidth = 1 + glow * 2.5; ctx.shadowBlur = 6 + glow * 20;
    ctx.strokeStyle = `rgba(160,250,255,${.15 + glow * .8})`;
    ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(ix, iy); ctx.stroke();
    const mx = (tx + ix) / 2, my = (ty + iy) / 2;
    ctx.lineWidth = 2; ctx.strokeStyle = h.pinched ? hot : `rgba(140,240,255,${.25 + glow * .5})`;
    ctx.beginPath(); ctx.arc(mx, my, h.pinched ? 9 : 9 + (1 - glow) * 16, 0, Math.PI * 2); ctx.stroke();
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
  }
}

// --- Sound: synthesized, soft, and never before the page is touched --------
export class HoloSound {
  constructor(storage = globalThis.localStorage) {
    this.storage = storage; this.ctx = null;
    let saved = null; try { saved = storage?.getItem('apex.study.sound'); } catch (_) {}
    this.on = saved !== 'off';
  }
  // Browsers allow audio only after the page is clicked or a key pressed.
  unlock() {
    if (!this.on || this.ctx) return;
    const AC = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (AC) { try { this.ctx = new AC(); } catch (_) { this.ctx = null; } }
  }
  toggle() { this.on = !this.on; try { this.storage?.setItem('apex.study.sound', this.on ? 'on' : 'off'); } catch (_) {} if (!this.on && this.ctx) { this.ctx.close?.(); this.ctx = null; } return this.on; }
  // Each cue: [start Hz, end Hz, seconds, peak volume, wave].
  static CUES = {ready: [880, 1320, .09, .05, 'sine'], grab: [220, 150, .16, .09, 'triangle'],
    release: [520, 780, .14, .05, 'sine'], cancel: [330, 220, .12, .04, 'sine'], mode: [1200, 1200, .05, .035, 'sine']};
  play(name) {
    const cue = HoloSound.CUES[name], ctx = this.ctx;
    if (!this.on || !cue || !ctx || ctx.state === 'closed') return false;
    try {
      const [f0, f1, dur, vol, wave] = cue, t = ctx.currentTime, osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = wave; osc.frequency.setValueAtTime(f0, t); osc.frequency.exponentialRampToValueAtTime(f1, t + dur);
      gain.gain.setValueAtTime(0.0001, t); gain.gain.exponentialRampToValueAtTime(vol, t + .012); gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      osc.connect(gain).connect(ctx.destination); osc.start(t); osc.stop(t + dur + .02);
      return true;
    } catch (_) { return false; }
  }
}

// --- The hologram style for the 3D scene ---------------------------------
// The hologram costs frame time, and slow frames delay the page's hand
// requests until they are discarded as stale: hands that lag feel far worse
// than a missing glow. Two stages: if frames stay slower than SLOW_FRAME for
// SLOW_SECONDS, bloom switches off (edges and the drawn hand stay); if they
// are STILL slow for another SLOW_SECONDS, the whole hologram pauses for this
// session. Turning Holo off and on again retries. Measured on a GPU-less
// machine: with the hologram on, the study's real-browser check passed 1 of 4
// runs (hand control timed out); with it off, 3 of 3.
export const SLOW_FRAME = 1 / 25, SLOW_SECONDS = 2;
export function frameGovernor(onSlow) {
  let ema = null, slowFor = 0, tripped = false;
  return {
    feed(dt) {
      if (tripped || !(dt > 0)) return tripped;
      ema = ema === null ? dt : ema + (dt - ema) * .1;
      slowFor = ema > SLOW_FRAME ? slowFor + dt : 0;
      if (slowFor >= SLOW_SECONDS) { tripped = true; onSlow?.(); }
      return tripped;
    },
    reset() { ema = null; slowFor = 0; tripped = false; },
    get tripped() { return tripped; }
  };
}

// Software rendering (no graphics acceleration) cannot afford the hologram.
// The WebGL renderer name says so: SwiftShader, llvmpipe, Microsoft Basic
// Render Driver. Unknown names count as hardware.
export function isSoftwareRenderer(name) { return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(String(name || '')); }

export function setupHoloScene({THREE, scene, camera, renderer, groups, reducedMotion, storage = globalThis.localStorage,
                                software = false, onBloomPaused, onHoloPaused}) {
  // An explicit choice (the Holo button) wins; otherwise on, unless the
  // renderer is software, where it starts off.
  let saved = null; try { saved = storage?.getItem('apex.study.holo'); } catch (_) {}
  let on = saved ? saved !== 'off' : !software;
  const startedOff = !on && !saved && software;
  const edges = new Map();            // part id -> [LineSegments]
  const react = new Map();            // part id -> {glow, lift, pulse}
  let composer = null, bloom = null, focus = {part: null, level: 0, held: false};
  // Stage 1 drops bloom; stage 2 (still slow without it) pauses the hologram.
  const governor = frameGovernor(() => onBloomPaused?.());
  const governor2 = frameGovernor(() => { on = false; apply(); onHoloPaused?.(); });
  const background = new THREE.Color('#050b10'), fog = new THREE.FogExp2('#050b10', .035);

  function buildEdges() {
    for (const lines of edges.values()) for (const l of lines) { l.parent?.remove(l); l.geometry.dispose(); l.material.dispose(); }
    edges.clear();
    for (const [id, group] of groups) {
      const lines = [];
      group.traverse(o => {
        if (!o.isMesh || o.userData.holoEdge) return;
        const geo = new THREE.EdgesGeometry(o.geometry, 28);
        const mat = new THREE.LineBasicMaterial({color: '#5fe6ff', transparent: true, opacity: .32, blending: THREE.AdditiveBlending, depthWrite: false});
        const line = new THREE.LineSegments(geo, mat); line.userData.holoEdge = true; line.raycast = () => {};
        o.add(line); lines.push(line);
      });
      edges.set(id, lines);
    }
    apply();
  }
  function ensureComposer() {
    if (composer) return;
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), .75, .55, .18);
    composer.addPass(bloom); composer.addPass(new OutputPass());
  }
  function apply() {
    for (const lines of edges.values()) for (const l of lines) l.visible = on;
    scene.background = on ? background : null; scene.fog = on ? fog : null;
    if (on) ensureComposer();
  }
  function resize(w, h) { if (composer) { composer.setSize(w, h); composer.setPixelRatio(renderer.getPixelRatio()); } }
  // What the hand is doing right now: which part it hovers, how far along,
  // and whether it holds it. The scene eases toward this every frame.
  function setFocus(part, level, held) { focus = {part: part || null, level: Math.max(0, Math.min(1, level || 0)), held: !!held}; }
  function pulse(part) { const r = react.get(part) || {glow: 0, lift: 0, pulse: 0}; r.pulse = 1; react.set(part, r); }
  function update(dt) {
    // The draw loop caps dt at 50 ms, which is above SLOW_FRAME, so a slow
    // device still registers as slow.
    if (on) { if (governor.tripped) governor2.feed(dt); else governor.feed(dt); }
    for (const [id, group] of groups) {
      const r = react.get(id) || {glow: 0, lift: 0, pulse: 0};
      // A grab flashes brighter for a moment (r.pulse), then settles.
      const target = Math.min(1.6, (id === focus.part ? (focus.held ? 1 : .35 + .5 * focus.level) : 0) + r.pulse * .8);
      const k = reducedMotion?.matches ? 1 : 1 - Math.exp(-dt * 12);
      r.glow += (target - r.glow) * k;
      // Ready to grab: the part rises a little toward you, like it is being
      // drawn to your hand. Presentation only — never a committed transform.
      const liftTarget = on && id === focus.part && !focus.held && focus.level >= 1 ? .12 : 0;
      r.lift += (liftTarget - r.lift) * (reducedMotion?.matches ? 1 : 1 - Math.exp(-dt * 9));
      r.pulse = reducedMotion?.matches ? 0 : Math.max(0, r.pulse - dt * 3.2);
      react.set(id, r);
      for (const l of edges.get(id) || []) l.material.opacity = .32 + r.glow * .6;
      group.userData.holoLift = on ? r.lift : 0;
      group.traverse(o => { if (o.isMesh && !o.userData.holoEdge && o.material?.emissive) {
        if (o.userData.baseEmissive === undefined) o.userData.baseEmissive = o.material.emissive.getHex();
        if (r.glow > .01 && on) { const g = Math.min(1.6, r.glow); o.material.emissive.setRGB(.05 * g, .42 * g, .5 * g); o.userData.holoLit = true; }
        else if (o.userData.holoLit) { o.material.emissive.setHex(o.userData.baseEmissive); o.userData.holoLit = false; }
      } });
    }
  }
  function render() { if (on && composer && !governor.tripped) composer.render(); else renderer.render(scene, camera); }
  function toggle() { on = !on; governor.reset(); governor2.reset(); try { storage?.setItem('apex.study.holo', on ? 'on' : 'off'); } catch (_) {} apply(); return on; }
  return {buildEdges, resize, setFocus, pulse, update, render, toggle, get on() { return on; }, get bloom() { return on && !governor.tripped; },
    get startedOff() { return startedOff; },
    // Selection changes the base glow (study.js sets it); keep it as the baseline.
    rebase() { for (const group of groups.values()) group.traverse(o => { if (o.isMesh && !o.userData.holoEdge && o.material?.emissive) { o.userData.baseEmissive = o.material.emissive.getHex(); o.userData.holoLit = false; } }); }};
}
