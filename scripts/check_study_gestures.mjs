// Phase 2b gestures (dashboard/static/study-gestures.js): two-hand pull-apart,
// the spring the model follows, and spin momentum after a hand-spun view.
import {join, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const {TwoHandStretch, Spring, Coast, STRETCH_GAIN, STRETCH_START_MS, RELEASE_MS, LOST_MS, COAST_MAX} =
  await import(pathToFileURL(join(root, 'dashboard/static/study-gestures.js')).href);

const L = (x, pinched = true, extra = {}) => ({id: 1, x, y: 0.5, pinched, ...extra});
const R = (x, pinched = true, extra = {}) => ({id: 2, x, y: 0.5, pinched, ...extra});

// 1. Both pinch together, pull apart: the model separates live, then keeps it on release.
let s = new TwoHandStretch(), t = 0;
assert.equal(s.feed([L(0.4, false), R(0.6, false)], t, 0), null, 'open hands do nothing');
assert.equal(s.feed([L(0.4), R(0.6)], t += 30, 0).state, 'start');
assert.equal(s.feed([L(0.395), R(0.605)], t += 30, 0).amount, 0, 'hand-distance noise does not move the model');
let r = s.feed([L(0.3), R(0.7)], t += 30, 0);
assert.equal(r.state, 'stretch'); assert.ok(Math.abs(r.amount - (0.2 - 0.02) * STRETCH_GAIN) < 0.011, 'separation follows the pull');
r = s.feed([L(0.1), R(0.9)], t += 30, 0);
assert.equal(r.amount, 1, 'a wide pull reaches fully apart (and is capped)');
assert.equal(s.feed([L(0.1, false), R(0.9)], t += 30, 0).state, 'hold', 'one open frame is not a release');
r = s.feed([L(0.1, false), R(0.9)], t += RELEASE_MS + 5, 0);
assert.deepEqual([r.state, r.amount], ['commit', 1]); assert.equal(s.engaged, false);

// 2. From apart, push together: snaps to fully assembled.
s = new TwoHandStretch(); t = 0;
s.feed([L(0.2), R(0.8)], t, 1);
r = s.feed([L(0.46), R(0.54)], t += 30, 1);
assert.equal(r.amount, 0, 'pushing together reassembles'); assert.match(r.label, /reassemble/);

// 3. Two pinches far apart in time are not a two-hand gesture (one hand was already doing something).
s = new TwoHandStretch(); t = 0;
s.feed([L(0.4), R(0.6, false)], t, 0);
assert.equal(s.feed([L(0.4), R(0.6)], t += STRETCH_START_MS + 50, 0), null);
// ...and never while one hand holds a part.
s = new TwoHandStretch();
assert.equal(s.feed([L(0.4), R(0.6)], 0, 0, false), null);

// 4. Cancelling: a fist, a hand lost too long, or a tracking jump returns to where it started.
for (const [name, frames] of [
  ['fist', [[L(0.2), R(0.8, true, {fist: true})]]],
  ['lost', [[L(0.2)], [L(0.2)], [L(0.2)]]],
  ['jump', [[L(0.2), R(0.2)]]],
]) {
  s = new TwoHandStretch(); t = 0;
  s.feed([L(0.4), R(0.6)], t, 0.3); s.feed([L(0.3), R(0.7)], t += 30, 0.3);
  let out;
  for (const f of frames) out = s.feed(f, t += name === 'lost' ? LOST_MS / 2 + 10 : 30, 0.3);
  assert.deepEqual([out.state, out.amount], ['cancel', 0.3], name); assert.equal(s.engaged, false, name);
}
s = new TwoHandStretch(); t = 0;
s.feed([L(0.4), R(0.6)], t, 0); s.feed([L(0.3), R(0.7)], t += 30, 0);
assert.equal(s.feed([L(0.3)], t += 60, 0).state, 'hold', 'a briefly hidden hand holds');
assert.equal(s.feed([L(0.3), R(0.7)], t += 60, 0).state, 'stretch', 'and continues when it returns');

// 5. The spring overshoots a little, then settles on the target.
const sp = new Spring(0);
let peak = 0;
for (let i = 0; i < 120; i++) peak = Math.max(peak, sp.step(1, 1 / 60));
assert.ok(peak > 1.02 && peak < 1.25, `springy, not wild (peak ${peak.toFixed(3)})`);
assert.ok(sp.settled(1), 'settled within two seconds');

// 6. Coast: a moving release keeps spinning and slows down; a stopped hand does not coast; glitches are capped.
const c = new Coast();
for (let i = 0; i <= 6; i++) c.track(i * 0.05, 0, 1000 + i * 30);          // 0.05 rad per 30 ms ≈ 1.7 rad/s
assert.equal(c.release(1000 + 6 * 30 + 60), true);
let turned = 0, frames = 0, first = c.step(1 / 60).theta;
turned += first;
while (c.moving && frames < 600) { turned += c.step(1 / 60)?.theta || 0; frames++; }
assert.ok(first > 0.02 && turned > 0.5 && turned < 1.2, `coasts on and slows down (${turned.toFixed(2)} rad over ${frames} frames)`);
assert.ok(frames < 180, 'settles within about three seconds');
const still = new Coast();
for (let i = 0; i <= 6; i++) still.track(0.3, 0, 1000 + i * 30);
assert.equal(still.release(1200), false, 'a hand that stopped before letting go does not coast');
const late = new Coast();
for (let i = 0; i <= 6; i++) late.track(i * 0.05, 0, 1000 + i * 30);
assert.equal(late.release(1000 + 180 + 400), false, 'no coasting from a stale drag');
const glitch = new Coast();
glitch.track(0, 0, 1000); glitch.track(3, 0, 1030);
assert.equal(glitch.release(1040), true);
assert.ok(glitch.step(1) .theta <= COAST_MAX + 1e-9, 'a tracking glitch cannot fling the view');

console.log('PASS: two-hand pull-apart (live, deadband, snaps to closed/open, release, fist/lost/jump cancel, never during a single-hand hold), '
  + 'a springy but settling follow, and spin momentum that coasts, slows, ignores stopped or stale drags and caps glitches.');
