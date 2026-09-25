// DOM simulation of streamed Celine in the companion: /api/speak/stream PCM
// played through a fake Web Audio context. Requires jsdom on NODE_PATH.
// The fast server and the Apex proxy are covered by tests/test_qwen_fast_server.py
// and tests/test_speak_stream.py; this covers what the PAGE does with the stream.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const base = require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const dom = new JSDOM(fs.readFileSync(base + 'companion.html', 'utf8'),
  {url: 'http://localhost:7860/companion', runScripts: 'outside-only'});
const w = dom.window, $ = id => w.document.getElementById(id);
w.TextDecoder = TextDecoder;
w.localStorage.setItem('apex_token', 'tok');
w.speechSynthesis = {cancel() {}, speak() {}};
w.SpeechSynthesisUtterance = class {};
w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
w.navigator.mediaDevices = {getUserMedia: async () => ({getTracks: () => [{stop() {}}]})};
w.MediaRecorder = class { constructor() { this.state = 'inactive'; }
  start() { this.state = 'recording'; } stop() { this.state = 'inactive'; this.ondataavailable?.({data: new w.Blob(['x'])}); this.onstop?.(); } };
w.Audio = class { play() { setTimeout(() => { this.onplaying?.(); setTimeout(() => this.onended?.(), 20); }, 1); return Promise.resolve(); } pause() {} };

// Fake Web Audio: a clock that runs in real time, and a log of what was
// scheduled when.
const t0 = performance.now();
const scheduled = [], stoppedSources = [];
w.AudioContext = class {
  constructor() { this.state = 'running'; this.destination = {}; }
  get currentTime() { return (performance.now() - t0) / 1000; }
  resume() { return Promise.resolve(); }
  createBuffer(ch, length, rate) { const d = new Float32Array(length);
    return {duration: length / rate, length, getChannelData: () => d, data: d}; }
  createBufferSource() { const src = {connect() {}, start(at) { scheduled.push({at, dur: src.buffer.duration, first: src.buffer.data[0], calledAt: performance.now() - t0}); },
    stop() { stoppedSources.push(src); }}; return src; }
};

// A fake voice: PCM arrives in pieces, 0.1 s of audio every 40 ms (faster
// than real time, like the fast server), with a deliberately odd split.
const RATE = 24000;
function pcmPieces(n, value = 8192) {
  const one = new Uint8Array(2400 * 2);
  const view = new DataView(one.buffer);
  for (let i = 0; i < 2400; i++) view.setInt16(i * 2, value, true);
  const all = new Uint8Array(one.length * n);
  for (let k = 0; k < n; k++) all.set(one, k * one.length);
  // Odd-sized pieces: a sample split across two reads must not be garbled.
  const cuts = [], size = 3001;
  for (let i = 0; i < all.length; i += size) cuts.push(all.slice(i, i + size));
  return cuts;
}
let streamMode = 'stream';            // 'stream' | '404'
const log = [];                       // request order, with times
const received = {};                  // section -> time its stream finished sending
const posted = [];
let chatScript = null;
w.fetch = async (path, opts = {}) => {
  const now = performance.now() - t0;
  if (path === '/api/status') return Response.json({agent_ready: true});
  if (path === '/api/voicebox/profiles') return Response.json({profiles: [{id: 'celine', name: 'CELINE'}]});
  if (path.startsWith('/api/companion/transcribe')) { await sleep(20); return Response.json({text: 'hi'}); }
  if (path === '/api/companion/timing') { posted.push(JSON.parse(opts.body)); return Response.json({ok: true}); }
  if (path === '/api/companion/chat') {
    return new Response(new ReadableStream({async start(c) {
      for (const [d, o] of chatScript) { await sleep(d); c.enqueue(new TextEncoder().encode(JSON.stringify(o) + '\n')); }
      c.close(); }}));
  }
  if (path === '/api/speak/stream') {
    const text = JSON.parse(opts.body).text;
    log.push({kind: 'stream', text, at: now});
    if (streamMode === '404') return Response.json({error: 'no streaming'}, {status: 404});
    const pieces = pcmPieces(4);
    return new Response(new ReadableStream({async start(c) {
      for (const p of pieces) { await sleep(15); c.enqueue(p); }
      received[text] = performance.now() - t0; c.close(); }}),
      {headers: {'X-Sample-Rate': String(RATE)}});
  }
  if (path === '/api/speak') { log.push({kind: 'wav', text: JSON.parse(opts.body).text, at: now});
    await sleep(20); return new Response(new Blob(['RIFF'])); }
  throw Error('unexpected ' + path);
};
w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));

async function turn(script) {
  chatScript = script;
  const before = posted.length;
  $('mic').click(); await sleep(10); $('mic').click();
  for (let i = 0; i < 300 && posted.length === before; i++) await sleep(20);
  assert.equal(posted.length, before + 1, 'the turn never finished');
  return posted.at(-1).stages;
}
const TWO = [[0, {type: 'start', thread_id: 1}], [10, {type: 'token', text: 'First part here. '}],
  [10, {type: 'token', text: 'Second part here. '}], [10, {type: 'done', text: 'First part here. Second part here.'}]];

(async () => {
  await sleep(50);
  $('first-phrase').checked = false;

  // 1. Streams, and plays before the stream has finished arriving.
  const st = await turn(TWO);
  const streams = log.filter(l => l.kind === 'stream');
  assert.deepEqual(streams.map(l => l.text), ['First part here.', 'Second part here.']);
  assert.equal(log.filter(l => l.kind === 'wav').length, 0, 'fell back to /api/speak while streaming worked');
  assert.ok(scheduled[0].calledAt < received['First part here.'],
    'the first piece must be handed to the speakers before the stream finished arriving');
  assert.ok(typeof st.first_sound === 'number' && st.first_sound < st.reply_done + 400);

  // 2. Pieces play back to back, with no gap and no overlap, and a sample
  //    split across two reads comes out intact.
  // 4 x 0.1 s = 19200 bytes, arriving as 3001-byte pieces -> 7 per section.
  const PER_SECTION = Math.ceil(19200 / 3001);
  assert.equal(scheduled.length, 2 * PER_SECTION);
  const firstSection = scheduled.slice(0, PER_SECTION);
  assert.ok(Math.abs(firstSection.reduce((a, p) => a + p.dur, 0) - 0.4) < 1e-6,
    'a section must play exactly as much audio as was sent');
  for (let i = 1; i < firstSection.length; i++) {
    const prevEnd = firstSection[i - 1].at + firstSection[i - 1].dur;
    assert.ok(Math.abs(firstSection[i].at - prevEnd) < 1e-6,
      `piece ${i} starts at ${firstSection[i].at}, previous ends at ${prevEnd}`);
  }
  assert.ok(scheduled.every(s => Math.abs(s.first - 0.25) < 1e-4), 'a sample split across reads was garbled');

  // 3. One synthesis at a time: section 2 is requested only after section 1
  //    has been fully received.
  assert.ok(streams[1].at >= received['First part here.'] - 1,
    `section 2 requested at ${streams[1].at} ms, section 1 finished arriving at ${received['First part here.']} ms`);

  // 4. Stop mid-stream stops the sound, and the next turn is not deadlocked.
  scheduled.length = 0;
  const LONG = [[0, {type: 'start', thread_id: 1}], [10, {type: 'token', text: 'Alpha one two three. '}],
    [10, {type: 'token', text: 'Beta four five six. '}], [10, {type: 'done', text: 'Alpha one two three. Beta four five six.'}]];
  chatScript = LONG;
  $('mic').click(); await sleep(10); $('mic').click();
  for (let i = 0; i < 100 && !scheduled.length; i++) await sleep(10);
  const stopsBefore = stoppedSources.length;
  $('stop').click();
  assert.ok(stoppedSources.length > stopsBefore, 'Stop did not stop the streamed audio');
  await sleep(300);
  const nStreams = log.filter(l => l.kind === 'stream').length;
  await sleep(300);
  assert.equal(log.filter(l => l.kind === 'stream').length, nStreams, 'sections kept being requested after Stop');
  const again = await turn(TWO);
  assert.ok(typeof again.first_sound === 'number', 'the turn after a Stop did not speak — deadlocked?');

  // 5. A voice server that cannot stream: fall back once, then stay on /api/speak.
  streamMode = '404';
  const wavBefore = log.filter(l => l.kind === 'wav').length, streamBefore = log.filter(l => l.kind === 'stream').length;
  await turn(TWO);
  assert.equal(log.filter(l => l.kind === 'stream').length - streamBefore, 1, 'kept asking for a stream after a 404');
  assert.equal(log.filter(l => l.kind === 'wav').length - wavBefore, 2, 'both sections must fall back to /api/speak');

  console.log('PASS: streamed voice plays before the stream ends, back to back with split samples intact, '
    + 'one synthesis at a time, Stop silences it without deadlocking the next turn, and a non-streaming '
    + 'server falls back once and stays on /api/speak.');
  dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exit(1); });
