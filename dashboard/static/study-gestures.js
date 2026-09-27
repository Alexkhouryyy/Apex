// Phase 2b: two-handed and momentum gestures for the study, as small pure
// state machines so they can be tested without a camera or a GPU.
//
//   TwoHandStretch — pinch with both hands and pull them apart: the model
//     separates live; push them together to reassemble; let go to keep it.
//   Coast — a view spun by hand keeps turning after release and slows down.
//   Spring — the springy amount the model follows while stretched.

// Pulling the hands apart by this share of the view width goes from assembled
// to fully apart (0.55 of the width ≈ a comfortable shoulder-width pull).
export const STRETCH_GAIN = 1 / 0.55;
export const STRETCH_START_MS = 450;   // both pinches must begin within this of each other
export const STRETCH_DEADBAND = 0.02;  // hand-distance noise that must not move the model
export const RELEASE_MS = 55;          // one open frame is noise; this long is a release
export const LOST_MS = 180;            // an unseen hand for longer than this cancels
export const TOUCH = 0.1;              // hand distance that counts as "together" (tracked hands never fully overlap)

const inView = h => Number.isFinite(h.x) && Number.isFinite(h.y) && h.id != null;
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

export class TwoHandStretch {
  constructor() { this.pinchSince = new Map(); this.active = null; }

  get engaged() { return !!this.active; }

  cancel() { const was = this.active; this.active = null; return was ? {state: 'cancel', amount: was.base} : null; }

  // hands: the latest fresh sample's hands. base: the current explosion (0..1).
  // allowed: false while one hand is holding a part (single-hand gestures win).
  // Returns null when not stretching, else {state, amount, label}.
  feed(hands, now, base, allowed = true) {
    hands = (hands || []).filter(inView);
    for (const h of hands) {
      if (h.pinched && !h.fist) { if (!this.pinchSince.has(h.id)) this.pinchSince.set(h.id, now); }
      else this.pinchSince.delete(h.id);
    }
    for (const id of [...this.pinchSince.keys()]) if (!hands.some(h => h.id === id)) this.pinchSince.delete(id);

    const a = this.active;
    if (!a) {
      if (!allowed || hands.length < 2) return null;
      const both = hands.filter(h => this.pinchSince.has(h.id)).slice(0, 2);
      if (both.length < 2) return null;
      const [t0, t1] = both.map(h => this.pinchSince.get(h.id));
      if (Math.abs(t0 - t1) > STRETCH_START_MS) return null;       // one pinch long before the other: not a two-hand gesture
      this.active = {ids: both.map(h => h.id), d0: dist(both[0], both[1]), base, amount: base, last: both, lostAt: null, openAt: null};
      return {state: 'start', amount: base, label: 'Two hands · pull apart to separate, push together to reassemble'};
    }

    const pair = a.ids.map(id => hands.find(h => h.id === id));
    if (pair.some(h => h?.fist)) { this.active = null; return {state: 'cancel', amount: a.base, label: 'Closed fist · separation cancelled'}; }
    if (pair.some(h => !h)) {
      a.lostAt ??= now;
      if (now - a.lostAt > LOST_MS) { this.active = null; return {state: 'cancel', amount: a.base, label: 'Hand lost · separation cancelled'}; }
      return {state: 'hold', amount: a.amount, label: 'Hand briefly hidden · holding'};
    }
    a.lostAt = null;
    if (pair.some((h, k) => dist(h, a.last[k]) > 0.3)) { this.active = null; return {state: 'cancel', amount: a.base, label: 'Tracking jumped · separation cancelled'}; }
    a.last = pair;
    if (pair.some(h => !h.pinched)) {
      a.openAt ??= now;
      if (now - a.openAt < RELEASE_MS) return {state: 'hold', amount: a.amount, label: 'Release to apply…'};
      this.active = null;
      return {state: 'commit', amount: a.amount, label: a.amount === 0 ? 'Reassembled' : `Separated ${Math.round(a.amount * 100)}%`};
    }
    a.openAt = null;
    const d = dist(pair[0], pair[1]), delta = d - a.d0;
    let amount = a.base;
    if (delta > STRETCH_DEADBAND) amount = a.base + (delta - STRETCH_DEADBAND) * STRETCH_GAIN;       // pulling apart: linear
    else if (delta < -STRETCH_DEADBAND && a.d0 > TOUCH + STRETCH_DEADBAND)                           // pushing together: hands
      amount = a.base * Math.max(0, (d + STRETCH_DEADBAND - TOUCH) / (a.d0 - TOUCH));               // touching always reassembles
    amount = Math.min(1, Math.max(0, amount));
    if (amount < 0.04) amount = 0; else if (amount > 0.96) amount = 1;   // easy to reach fully closed or fully open
    a.amount = Math.round(amount * 100) / 100;
    return {state: 'stretch', amount: a.amount,
            label: a.amount === 0 ? 'Together · release to reassemble' : `Pulling apart · ${Math.round(a.amount * 100)}% · release to keep`};
  }
}

// A damped spring: follows a target with a little overshoot, so pulling the
// model apart feels elastic rather than dragged. Settles in about half a second.
export class Spring {
  constructor(value = 0, stiffness = 170, damping = 15) { this.value = value; this.velocity = 0; this.k = stiffness; this.c = damping; }
  step(target, dt) {
    const steps = Math.max(1, Math.ceil(dt / (1 / 120)));
    for (let i = 0; i < steps; i++) {
      const h = dt / steps;
      this.velocity += ((target - this.value) * this.k - this.velocity * this.c) * h;
      this.value += this.velocity * h;
    }
    return this.value;
  }
  settled(target) { return Math.abs(target - this.value) < 1e-3 && Math.abs(this.velocity) < 1e-2; }
  set(value) { this.value = value; this.velocity = 0; }
}

// Momentum for a hand-spun view: measure the angular speed over the last
// moments of the drag; after release keep turning with friction.
export const COAST_WINDOW_MS = 120;   // speed is measured over this much of the end of the drag
export const COAST_MIN = 0.35;        // rad/s: slower than this at release just stops
export const COAST_MAX = 9;           // rad/s cap, so a tracking glitch cannot fling the view
export const COAST_FRICTION = 2.2;    // per second: about a second and a half to settle

export class Coast {
  constructor() { this.samples = []; this.velocity = null; }
  track(theta, phi, now) {
    this.samples.push({theta, phi, now});
    while (this.samples.length > 2 && now - this.samples[0].now > COAST_WINDOW_MS) this.samples.shift();
  }
  // Called on release: start coasting if the hand was still moving.
  release(now) {
    const s = this.samples;
    this.samples = [];
    if (s.length < 2) return false;
    const first = s[0], last = s[s.length - 1];
    // A hand that stopped still sends (unchanged) pinched frames, which read as zero speed; this gap only
    // rejects a drag whose frames ended long ago (release is confirmed ~55 ms late; slow pages sample less often).
    if (now - last.now > 300) return false;
    const dt = (last.now - first.now) / 1000;
    if (dt <= 0.015) return false;
    let vt = (last.theta - first.theta) / dt, vp = (last.phi - first.phi) / dt;
    const speed = Math.hypot(vt, vp);
    if (speed < COAST_MIN) return false;
    if (speed > COAST_MAX) { vt *= COAST_MAX / speed; vp *= COAST_MAX / speed; }
    this.velocity = {theta: vt, phi: vp};
    return true;
  }
  // Each frame: the angle change to apply, or null when stopped.
  step(dt) {
    const v = this.velocity;
    if (!v) return null;
    const out = {theta: v.theta * dt, phi: v.phi * dt};
    const f = Math.exp(-COAST_FRICTION * dt);
    v.theta *= f; v.phi *= f;
    if (Math.hypot(v.theta, v.phi) < 0.05) this.velocity = null;
    return out;
  }
  stop() { this.velocity = null; this.samples = []; }
  get moving() { return !!this.velocity; }
}
