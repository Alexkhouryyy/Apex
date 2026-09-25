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
let speakCalls = 0, chatScript = null;
const spokenText = [];
const stream = lines => new ReadableStream({async start(c) {
  for (const [delay, obj] of lines) { await sleep(delay); c.enqueue(new TextEncoder().encode(JSON.stringify(obj) + '\n')); }
  c.close();
}});
// One reply, the same every time: two sentences early, then a long pause (a
// tool call, or a slow model), then the rest. That pause is what "Speak as it
// writes" is for.
const REPLY = [[0, {type: 'start', thread_id: 1}],
  [40, {type: 'token', text: 'Let me check. '}], [40, {type: 'token', text: 'One moment. '}],
  [300, {type: 'token', text: 'It is Tuesday.'}],
  [20, {type: 'done', text: 'It is Tuesday.'}]];
w.fetch = async (path, opts = {}) => {
  if (path === '/api/status') return Response.json({agent_ready: true});
  if (path === '/api/voicebox/profiles') return Response.json({profiles: []});
  if (path.startsWith('/api/companion/transcribe')) {
    await sleep(50);
    return Response.json({text: 'hello apex'}, {headers: {'Server-Timing': 'stt;dur=40.5'}});
  }
  if (path === '/api/companion/chat') return new Response(stream(chatScript || REPLY));
  // A voice server that cannot stream: Apex answers the stream route with 404.
  if (path === '/api/speak/stream') return Response.json({error: 'no streaming'}, {status: 404});
  if (path === '/api/speak') {
    speakCalls++; spokenText.push(JSON.parse(opts.body).text);
    await sleep(40);
    return new Response(new Blob(['RIFF']), {headers: {'Server-Timing': 'tts;dur=35'}});
  }
  if (path === '/api/companion/timing') { posted.push(JSON.parse(opts.body)); return Response.json({ok: true}); }
  throw Error('unexpected ' + path);
};

w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));

async function spokenTurn(streamOn) {
  $('stream-speech').checked = streamOn;
  const before = posted.length;
  $('mic').click(); await sleep(20);          // start recording
  $('mic').click();                           // "Send voice": speech ends now
  for (let i = 0; i < 150 && posted.length === before; i++) await sleep(20);
  assert.equal(posted.length, before + 1, 'one spoken turn must post exactly one timing record');
  await sleep(50);
  return posted.at(-1);
}
const ORDER = ['stt_done', 'first_token', 'reply_done', 'tts_start', 'tts_ready', 'first_sound'];

(async () => {
  await sleep(30);
  assert.ok($('stream-speech').checked, '"Speak as it writes" should default to on');

  // BEFORE: the whole reply first, then the voice.
  const off = await spokenTurn(false);
  assert.equal(off.mode, 'tap');
  assert.equal(off.voice, 'voicebox');
  assert.equal(off.streamed, false);
  const st = off.stages;
  for (const k of ORDER) assert.ok(typeof st[k] === 'number', `stage ${k} missing: ${JSON.stringify(st)}`);
  for (let i = 1; i < ORDER.length; i++)
    assert.ok(st[ORDER[i]] >= st[ORDER[i - 1]], `${ORDER[i]} (${st[ORDER[i]]}) before ${ORDER[i - 1]} (${st[ORDER[i - 1]]})`);
  // The clock counts from speech end, not from the previous stage.
  assert.ok(st.stt_done >= 45, `stt_done ${st.stt_done}`);
  assert.ok(st.first_sound >= st.reply_done, 'with it off, nothing plays before the reply is written');
  assert.equal(st.stt_server, 40.5, 'Server-Timing stt duration not read');
  assert.equal(st.tts_server, 35, 'Server-Timing tts duration not read');
  assert.ok(!$('voice-timing').hidden && /First sound after/.test($('voice-timing').textContent),
    'the turn timing must be shown on the page');
  const offCalls = speakCalls;

  // AFTER: the same turn, spoken as it is written.
  const on = await spokenTurn(true);
  assert.equal(on.streamed, true);
  const s2 = on.stages;
  for (const k of ORDER) assert.ok(typeof s2[k] === 'number', `stage ${k} missing (on): ${JSON.stringify(s2)}`);
  assert.ok(s2.tts_start < s2.reply_done, 'the voice must start before the reply is finished');
  assert.ok(s2.first_sound < s2.reply_done, 'first sound must come before the reply is finished');
  assert.ok(s2.first_sound + 200 < st.first_sound,
    `first sound ${s2.first_sound.toFixed(0)} ms (on) vs ${st.first_sound.toFixed(0)} ms (off) — not earlier`);
  // What each mode SAYS differs on a turn like this, and on purpose: the reply
  // shown is the final step's text, so whole-reply mode says only that; spoken
  // as it is written, the "let me check" before the pause is said too — the way
  // a person answers.
  assert.deepEqual(spokenText.slice(0, offCalls), ['It is Tuesday.']);
  assert.deepEqual(spokenText.slice(offCalls), ['Let me check.', 'One moment.', 'It is Tuesday.']);
  console.log(`  measured (simulated delays): first sound ${st.first_sound.toFixed(0)} ms whole-reply, `
    + `${s2.first_sound.toFixed(0)} ms as-it-writes`);

  // STOP while sections are still queued for the voice — the reply is fully
  // written, so only the speech queue itself can stop the rest.
  chatScript = [[0, {type: 'start', thread_id: 1}],
    [30, {type: 'token', text: 'First. Second. Third. Fourth. Fifth. '}],
    [10, {type: 'done', text: 'First. Second. Third. Fourth. Fifth.'}]];
  $('stream-speech').checked = true;
  const beforeStop = speakCalls;
  $('mic').click(); await sleep(20); $('mic').click();
  for (let i = 0; i < 50 && speakCalls === beforeStop; i++) await sleep(20);
  await sleep(60);
  assert.ok(speakCalls - beforeStop < 5, 'the test needs sections still queued when Stop is pressed');
  $('stop').click();
  const atStop = speakCalls;
  await sleep(700);
  assert.equal(speakCalls, atStop, 'sections were still requested after Stop');
  chatScript = null;

  // "Start on the first phrase": on, a long first sentence goes to the voice
  // at its first pause; off, it waits for the whole sentence.
  for (const phrase of [true, false]) {
    await sleep(300);
    $('first-phrase').checked = phrase;
    chatScript = [[0, {type: 'start', thread_id: 1}],
      [20, {type: 'token', text: 'Your dentist appointment is on Tuesday, '}],
      [200, {type: 'token', text: 'at three in the afternoon with Dr. Lee at the new office. '}],
      [10, {type: 'done', text: 'Your dentist appointment is on Tuesday, at three in the afternoon with Dr. Lee at the new office.'}]];
    const from = spokenText.length;
    await spokenTurn(true);
    assert.equal(spokenText[from], phrase ? 'Your dentist appointment is on Tuesday,'
      : 'Your dentist appointment is on Tuesday, at three in the afternoon with Dr. Lee at the new office.',
      `first section with first-phrase ${phrase ? 'on' : 'off'}`);
  }
  $('first-phrase').checked = true;
  chatScript = null;

  // A reply that never streamed (an error, a fallback) must still be spoken
  // with the setting on — otherwise that turn is silent.
  await sleep(300);
  chatScript = [[0, {type: 'start', thread_id: 1}], [30, {type: 'done', text: 'Sorry, the model timed out.'}]];
  const beforeErr = spokenText.length;
  await spokenTurn(true);
  assert.deepEqual(spokenText.slice(beforeErr), ['Sorry, the model timed out.'],
    'a reply that was never streamed was not spoken');
  chatScript = null;

  await sleep(300);
  // The wait after the reply is written is the VOICE, and the status says so
  // rather than "thinking" — which read as the brain being stuck.
  {
    let seen = '';
    const slowSpeak = w.fetch;
    w.fetch = async (path, opts) => {
      if (path === '/api/speak') { await sleep(400); seen = $('status').textContent; }
      return slowSpeak(path, opts);
    };
    $('stream-speech').checked = true;
    chatScript = [[0, {type: 'start', thread_id: 1}], [10, {type: 'token', text: 'Hi'}],
      [10, {type: 'done', text: 'Hi'}]];
    await spokenTurn(true);
    w.fetch = slowSpeak; chatScript = null;
    assert.match(seen, /waiting for the voice/, `status while the voice generates was "${seen}"`);
  }

  // A TYPED message is not a voice turn and must not post a timing record.
  const n = posted.length;
  $('message').value = 'typed';
  $('composer').dispatchEvent(new w.Event('submit'));
  for (let i = 0; i < 40; i++) await sleep(20);
  assert.equal(posted.length, n, 'a typed message posted a voice timing record');

  console.log('PASS: spoken turns post one record each with every stage, counted from speech end; '
    + 'whole-reply mode keeps strict stage order, as-it-writes starts the voice before the reply ends '
    + 'and sounds earlier on the same turn; Stop ends all further speech; typed turns post none.');
  dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exit(1); });
