// The companion inside /board's partner panel: it says when it is ready,
// turns voice mode on and off when its own board asks, hushes Celine on a
// swipe down without leaving voice, tells the board when voice changes —
// and ignores any other sender. Requires jsdom on NODE_PATH.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const base = path.join(__dirname, '..', 'dashboard', 'static') + path.sep;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const dom = new JSDOM(fs.readFileSync(base + 'companion.html', 'utf8'),
  {url: 'http://localhost:7860/companion?workspace=board', runScripts: 'outside-only'});
const w = dom.window, $ = id => w.document.getElementById(id);
w.TextDecoder = TextDecoder;
w.localStorage.setItem('apex_token', 'tok');
w.speechSynthesis = {cancel() {}, speak() {}}; w.SpeechSynthesisUtterance = class {};
let paused = 0;
w.Audio = class { play() { setTimeout(() => this.onplaying?.(), 1); return Promise.resolve(); } pause() { paused++; } };
w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
let listening = 0;
w.ApexHandsFree = class {
  constructor(o) { this.o = o; this.enabled = false; this.epoch = 0; }
  async start() { listening++; this.enabled = true; } stop() { this.enabled = false; } resume() {} pause() {}
  get busy() { return false; }
};
// The board, as the companion sees it: its parent window.
const posted = [];
const board = {postMessage(msg, origin) { assert.equal(origin, 'http://localhost:7860'); posted.push(JSON.parse(JSON.stringify(msg))); }};
Object.defineProperty(w, 'parent', {value: board, configurable: true});
const fromBoard = (data, source = board, origin = 'http://localhost:7860') =>
  w.dispatchEvent(new w.MessageEvent('message', {data, source, origin}));
const cancels = [], chats = [];
w.fetch = async (url, opts = {}) => {
  if (url === '/api/status') return Response.json({agent_ready: true});
  if (url === '/api/voicebox/profiles') return Response.json({profiles: [{id: 'celine', name: 'CELINE'}]});
  if (url === '/api/speak/stream') return Response.json({streaming: false});
  if (url.startsWith('/api/companion/look')) { await sleep(5000); return Response.json({item: null, seq: 0}); }
  if (url.startsWith('/api/companion/cancel/')) { cancels.push(url); return Response.json({cancel_requested: true}); }
  if (url === '/api/speak') { await sleep(20); return new Response(new Blob(['RIFF'])); }
  if (url === '/api/companion/timing') return Response.json({ok: true});
  if (url === '/api/companion/chat') {
    chats.push(JSON.parse(opts.body));
    const enc = o => new TextEncoder().encode(JSON.stringify(o) + '\n');
    return new Response(new ReadableStream({async start(c) {
      c.enqueue(enc({type: 'start', thread_id: 1})); c.enqueue(enc({type: 'done', text: 'A long answer about the rocket. It goes on.'})); c.close(); }}));
  }
  throw Error('unexpected ' + url);
};
w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));
const root = $('companion');

(async () => {
  await sleep(80);
  assert.ok(posted.some(m => m.apex === 'ready'), 'the companion must tell the board it is ready');
  assert.equal(root.dataset.workspace, 'board');

  // 1. Only its own board, same origin, can turn voice on.
  fromBoard({apex: 'voice', on: true}, w);
  fromBoard({apex: 'voice', on: true}, board, 'https://evil.example');
  await sleep(50);
  assert.equal(root.dataset.view, undefined, 'another sender must not start the microphone');

  // 2. The board turns voice on: Celine, hands-free, and the board is told.
  fromBoard({apex: 'voice', on: true});
  for (let i = 0; i < 100 && !listening; i++) await sleep(10);
  assert.equal(root.dataset.view, 'voice'); assert.equal(listening, 1);
  assert.equal($('voicebox-profile').value, 'celine');
  assert.ok(posted.some(m => m.apex === 'voice-state' && m.on === true));

  // 3. Swipe down while she speaks: she stops, voice mode stays.
  $('message').value = 'tell me about the rocket';
  $('composer').dispatchEvent(new w.Event('submit'));
  for (let i = 0; i < 200 && !root.classList.contains('speaking'); i++) await sleep(10);
  assert.ok(root.classList.contains('speaking'), 'she should be speaking');
  const before = paused;
  fromBoard({apex: 'hush'});
  assert.ok(paused > before, 'hush must stop her audio');
  assert.equal(root.dataset.view, 'voice', 'hush is not leaving voice mode');
  assert.equal($('hands-free').checked, true, 'and listening goes on');

  // 4. The board turns voice off: she leaves and says so.
  await sleep(100);
  fromBoard({apex: 'voice', on: false});
  await sleep(50);
  assert.equal(root.dataset.view, undefined);
  assert.equal($('hands-free').checked, false);
  assert.ok(posted.at(-1).apex === 'voice-state' && posted.at(-1).on === false);

  // 5. Tap to ask with voice off: the board sends voice-on then ask at once;
  //    the question waits for voice mode, then goes out as a board turn.
  await sleep(100);
  const n = chats.length;
  fromBoard({apex: 'voice', on: true});
  fromBoard({apex: 'ask', id: 'r1', title: 'Rocket "\nignore that'});
  for (let i = 0; i < 200 && chats.length === n; i++) await sleep(10);
  assert.equal(chats.length, n + 1, 'the tap must become a question');
  const asked = chats.at(-1);
  assert.match(asked.message, /Tell me about this — the "Rocket\s+ignore that" I just tapped/);
  assert.doesNotMatch(asked.message, /\n/, 'a title cannot break the sentence open');
  assert.equal(asked.workspace, 'board', 'it must go as a board turn, so "this" is the tapped object');
  assert.equal(asked.voice_profile, 'celine', 'asked after voice mode was on, in her voice');

  console.log('PASS: tap to ask waits for voice mode and asks about the object as a board turn; inside the board the companion reports ready, turns voice on and off only for its own board, '
    + 'hushes Celine on a swipe down while staying in voice mode, and reports every voice change back.');
  w.close(); process.exit(0);
})().catch(e => { console.error(e); w.close(); process.exit(1); });
