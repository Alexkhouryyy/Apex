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
const contexts = [];
w.AudioContext = class {
  constructor(opts = {}) { this.state = 'running'; this.destination = {}; this.sampleRate = opts.sampleRate || 48000; contexts.push(this); }
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
let chatScript = null, sectionNo = 0;
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
  if (path === '/api/speak/stream' && (opts.method || 'GET') === 'GET')
    return Response.json(streamMode === '404' ? {streaming: false} : {streaming: true, sample_rate: RATE});
  if (path === '/api/speak/stream') {
    const text = JSON.parse(opts.body).text;
    log.push({kind: 'stream', text, at: now});
    if (streamMode === '404') return Response.json({error: 'no streaming'}, {status: 404});
    // Each section a different level, so the log can tell which section a
    // scheduled block belongs to.
    sectionNo += 1;
    const pieces = pcmPieces(4, 4096 * sectionNo);
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

  // 2. The context runs at the voice's own rate (no per-piece resampling —
  //    the crackle heard on the laptop), blocks meet sample-exactly, and a
  //    sample split across two reads comes out intact.
  assert.equal(contexts.at(-1).sampleRate, RATE, 'audio must play at the voice rate, not the device rate');
  const firstSection = scheduled.filter(b => Math.abs(b.first - 0.125) < 1e-4);
  assert.ok(firstSection.length >= 2 && firstSection.length < 7,
    `network pieces must be glued into fewer blocks (got ${firstSection.length})`);
  assert.ok(Math.abs(firstSection.reduce((a, p) => a + p.dur, 0) - 0.4) < 1e-9,
    'a section must play exactly as much audio as was sent');
  for (let i = 1; i < firstSection.length; i++) {
    const prevEnd = Math.round((firstSection[i - 1].at + firstSection[i - 1].dur) * RATE);
    assert.equal(Math.round(firstSection[i].at * RATE), prevEnd, `block ${i} does not meet the previous one exactly`);
    assert.ok(Math.abs(firstSection[i].at * RATE - Math.round(firstSection[i].at * RATE)) < 1e-6,
      'blocks must start on whole sample frames');
  }
  assert.ok(scheduled.every(s => [0.125, 0.25].some(v => Math.abs(s.first - v) < 1e-4)),
    'a sample split across reads was garbled');

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

  // 5. While streaming, sections are merged and the comma split is off —
  //    every boundary costs a pause (heard on the laptop as long pauses
  //    between a comma and the rest of the sentence).
  {
    $('first-phrase').checked = true;
    const from = log.filter(l => l.kind === 'stream').length;
    await turn([[0, {type: 'start', thread_id: 1}],
      [10, {type: 'token', text: 'Your dentist appointment is on Tuesday, at three in the afternoon with Dr. Lee. '}],
      [10, {type: 'token', text: 'Bring your card. '}], [5, {type: 'token', text: 'Arrive early. '}],
      [5, {type: 'token', text: 'Parking is behind the building. '}],
      [10, {type: 'done', text: 'Parking is behind the building.'}]]);
    const asked = log.filter(l => l.kind === 'stream').slice(from).map(l => l.text);
    assert.equal(asked[0], 'Your dentist appointment is on Tuesday, at three in the afternoon with Dr. Lee.',
      'no comma split while streaming');
    assert.deepEqual(asked.slice(1), ['Bring your card. Arrive early. Parking is behind the building.'],
      `the rest must go as one section, got ${JSON.stringify(asked.slice(1))}`);
  }

  // 6. A voice server that cannot stream: fall back once, then stay on /api/speak.
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
