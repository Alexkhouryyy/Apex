// The car's link to the PC (dashboard/static/car_link.js): spoken text is fed
// as it grows, a dropped connection is ridden out, and the driver is told
// plainly what is wrong.
const assert = require('node:assert/strict');
const {textDelta, patient, describe} = require('../dashboard/static/car_link.js');

(async () => {
  assert.deepEqual(textDelta('', 'Hi Alex.'), {delta: 'Hi Alex.', reset: false});
  assert.deepEqual(textDelta('Hi Alex.', 'Hi Alex. The tests pass.'), {delta: ' The tests pass.', reset: false});
  assert.deepEqual(textDelta('Hi Alex.', 'Hi Alex.'), {delta: '', reset: false});
  assert.deepEqual(textDelta('Draft text', 'Final text'), {delta: '', reset: true}, 'a rewrite is not appended speech');

  // Two network failures, then an answer: returned, with the UI told both ways.
  let clock = 0, calls = 0; const states = [], slept = [];
  const flaky = async () => { calls++; if (calls < 3) throw new TypeError('Failed to fetch'); return {status: 'running'}; };
  const got = await patient(flaky, {sleep: async ms => { slept.push(ms); clock += ms; }, now: () => clock, onState: s => states.push(s)});
  assert.deepEqual(got, {status: 'running'});
  assert.deepEqual(states, ['reconnecting', 'connected']);
  assert.ok(slept[1] > slept[0], 'pauses grow between retries');

  // A server answer (even an error) is not a lost connection: no retrying.
  calls = 0;
  const answered = async () => { calls++; const e = new Error('Task not found.'); e.status = 404; throw e; };
  await assert.rejects(patient(answered, {sleep: async () => {}, now: () => 0}), /Task not found/);
  assert.equal(calls, 1);

  // Down for longer than the grace period: give up, say the task is still running on the PC.
  clock = 0;
  const down = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(patient(down, {graceMs: 10000, sleep: async ms => { clock += ms; }, now: () => clock}),
    e => e.offline === true && /keeps running on your PC/.test(e.message));

  assert.equal(describe({ok: true, ms: 84.4}).text, 'Connected to your Apex · 84 ms');
  assert.equal(describe({ok: false, status: 401}).state, 'auth');
  assert.equal(describe({ok: false, status: 503}).state, 'trouble');
  assert.match(describe({ok: false, status: 0}).text, /Can't reach your Apex.*Tailscale/);
  assert.match(describe({ok: false, status: 0, deviceOffline: true}).text, /no internet/);
  console.log('PASS: car link feeds growing text, rides out drops with growing pauses, gives up honestly, and explains the link state.');
})().catch(e => { console.error(e); process.exit(1); });
