// DOM simulation of one spoken companion turn, end to end, with the mic, the
// server and the speaker faked and each stage delayed by a known amount.
// Requires jsdom on NODE_PATH. Proves the stage marks fire in turn order, on
// one clock, and reach /api/companion/timing — not that a real browser's
// timings are right (that is what the user's own 20 turns are for).
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const base = require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;

const dom = new JSDOM(fs.readFileSync(base + 'companion.html', 'utf8'),
  {url: 'http://localhost:7860/companion', runScripts: 'outside-only'});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));

w.TextDecoder = TextDecoder;
w.localStorage.setItem('apex_token', 'tok');
w.speechSynthesis = {cancel() {}, speak(u) { u.onstart?.(); u.onend?.(); }};
w.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
w.URL.createObjectURL = () => 'blob:fake'; w.URL.revokeObjectURL = () => {};
w.crypto.randomUUID ??= () => 'turn-' + Math.random();

// The speaker: plays for 30 ms.
let plays = 0;
w.Audio = class {
  constructor(src) { this.src = src; }
  play() { plays++; setTimeout(() => { this.onplaying?.(); setTimeout(() => this.onended?.(), 30); }, 5); return Promise.resolve(); }
  pause() {}
};
// The microphone.
w.navigator.mediaDevices = {getUserMedia: async () => ({getTracks: () => [{stop() {}}]})};
w.MediaRecorder = class {
  constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
  start() { this.state = 'recording'; }
  stop() { this.state = 'inactive'; this.ondataavailable?.({data: new w.Blob(['x'])}); this.onstop?.(); }
};

const posted = [];
const stream = lines => new ReadableStream({async start(c) {
  for (const [delay, obj] of lines) { await sleep(delay); c.enqueue(new TextEncoder().encode(JSON.stringify(obj) + '\n')); }
  c.close();
}});
w.fetch = async (path, opts = {}) => {
  if (path === '/api/status') return Response.json({agent_ready: true});
  if (path === '/api/voicebox/profiles') return Response.json({profiles: []});
  if (path.startsWith('/api/companion/transcribe')) {
    await sleep(50);
    return Response.json({text: 'hello apex'}, {headers: {'Server-Timing': 'stt;dur=40.5'}});
  }
  if (path === '/api/companion/chat') {
    return new Response(stream([[0, {type: 'start', thread_id: 1}],
      [40, {type: 'token', text: 'Hi. '}], [40, {type: 'token', text: 'There.'}],
      [20, {type: 'done', text: 'Hi. There.'}]]));
  }
  if (path === '/api/speak') {
    await sleep(40);
    return new Response(new Blob(['RIFF']), {headers: {'Server-Timing': 'tts;dur=35'}});
  }
  if (path === '/api/companion/timing') { posted.push(JSON.parse(opts.body)); return Response.json({ok: true}); }
  throw Error('unexpected ' + path);
};

w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));

(async () => {
  await sleep(30);
  $('mic').click(); await sleep(20);          // start recording
  $('mic').click();                           // "Send voice": speech ends now
  for (let i = 0; i < 100 && !posted.length; i++) await sleep(20);

  assert.equal(posted.length, 1, 'one spoken turn must post exactly one timing record');
  const {mode, stages: st, voice} = posted[0];
  assert.equal(mode, 'tap');
  assert.equal(voice, 'voicebox');
  const order = ['stt_done', 'first_token', 'reply_done', 'tts_start', 'tts_ready', 'first_sound'];
  for (const k of order) assert.ok(typeof st[k] === 'number', `stage ${k} missing: ${JSON.stringify(st)}`);
  for (let i = 1; i < order.length; i++)
    assert.ok(st[order[i]] >= st[order[i - 1]], `${order[i]} (${st[order[i]]}) before ${order[i - 1]} (${st[order[i - 1]]})`);
  // The fake stages add up: 50 ms transcription, then ~100 ms of reply, then
  // 40 ms of synthesis. The clock must be counting from speech end, not from
  // the previous stage.
  assert.ok(st.stt_done >= 45, `stt_done ${st.stt_done}`);
  assert.ok(st.first_sound >= 180, `first_sound ${st.first_sound} is shorter than the stages it contains`);
  assert.equal(st.stt_server, 40.5, 'Server-Timing stt duration not read');
  assert.equal(st.tts_server, 35, 'Server-Timing tts duration not read');
  assert.ok(!$('voice-timing').hidden && /First sound after/.test($('voice-timing').textContent),
    'the turn timing must be shown on the page');

  // A TYPED message is not a voice turn and must not post a timing record.
  $('message').value = 'typed';
  $('composer').dispatchEvent(new w.Event('submit'));
  for (let i = 0; i < 40; i++) await sleep(20);
  assert.equal(posted.length, 1, 'a typed message posted a voice timing record');

  console.log('PASS: a spoken turn posts one record with every stage, in turn order, '
    + 'counted from speech end, with server durations; typed turns post none.');
  dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
