// DOM simulation of "look now" in the companion: a hotkey / "Hey Celly"
// request arrives on /api/companion/look and becomes a turn that refers to
// the server's capture. Requires jsdom on NODE_PATH.
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
let pauses = 0, plays = 0;
w.Audio = class {
  play() { plays++; setTimeout(() => { this.onplaying?.(); this.t = setTimeout(() => this.onended?.(), slowReply ? 2000 : 20); }, 1); return Promise.resolve(); }
  pause() { pauses++; clearTimeout(this.t); } };
w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
let listening = 0, activated = false;
w.ApexHandsFree = class { constructor(o){this.o=o;this.enabled=false;this.epoch=0;} async start(){listening++;this.enabled=true;}
  stop(){this.enabled=false;} resume(){} pause(){} get busy(){return false;} };
Object.defineProperty(w.navigator, 'userActivation', {value: {get hasBeenActive() { return activated; }}});

const polls = [], chats = [];
let pending = [];            // look items the "server" will hand out, in order
let slowReply = false;
w.fetch = async (path, opts = {}) => {
  if (path === '/api/status') return Response.json({agent_ready: true});
  if (path === '/api/voicebox/profiles') return Response.json({profiles: [{id: 'celine', name: 'CELINE'}]});
  if (path === '/api/speak/stream') return Response.json({streaming: false});
  if (path.startsWith('/api/companion/look')) {
    const after = Number(new URL('http://x' + path).searchParams.get('after'));
    polls.push(after);
    for (let i = 0; i < 20 && !pending.length; i++) await sleep(10);   // a (short) long-poll
    const item = pending.shift() || null;
    return Response.json({item, seq: item ? item.seq : Math.max(after, 0)});
  }
  if (path === '/api/companion/chat') {
    chats.push(JSON.parse(opts.body));
    const text = slowReply ? 'A long answer that is still being spoken. And more.' : 'I see your editor.';
    return new Response(new ReadableStream({async start(c) {
      c.enqueue(new TextEncoder().encode(JSON.stringify({type: 'start', thread_id: 1}) + '\n'));
      await sleep(10);
      c.enqueue(new TextEncoder().encode(JSON.stringify({type: 'done', text}) + '\n')); c.close(); }}));
  }
  if (path === '/api/speak') { await sleep(slowReply ? 400 : 5); return new Response(new Blob(['RIFF'])); }
  if (path === '/api/companion/timing') return Response.json({ok: true});
  throw Error('unexpected ' + path);
};
w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));

(async () => {
  await sleep(80);
  assert.equal(polls[0], -1, 'a page that just opened must start from now, not replay old requests');

  // 1. The hotkey: no words — the default question, the server's capture.
  pending.push({seq: 7, source: 'hotkey', question: '', ts: 1});
  for (let i = 0; i < 100 && !chats.length; i++) await sleep(10);
  assert.equal(chats.length, 1, 'a look request must start a turn');
  assert.equal(chats[0].look_id, 7);
  assert.equal(chats[0].screen_image, null, 'the browser must not attach its own image to a look turn');
  assert.match(chats[0].message, /Look at my screen/);
  assert.ok(polls.includes(7), 'the next poll must ask for requests after the one just answered');

  // 2. "Hey Celly, why is this failing?" — her words are the question, and
  //    whatever Celine was still saying gives way to it.
  await sleep(100);
  slowReply = true;
  $('message').value = 'tell me something long';
  const playsBefore = plays;
  $('composer').dispatchEvent(new w.Event('submit'));
  for (let i = 0; i < 200 && plays === playsBefore; i++) await sleep(10);   // she is talking now
  assert.ok(plays > playsBefore, 'the long reply never started speaking');
  const pausesBefore = pauses;
  pending.push({seq: 8, source: 'wake', question: 'why is this failing?', ts: 2});
  for (let i = 0; i < 300 && chats.length < 3; i++) await sleep(10);
  assert.equal(chats.length, 3, 'the wake request must still get its turn');
  assert.equal(chats[2].message, 'why is this failing?');
  assert.equal(chats[2].look_id, 8);
  assert.ok(pauses > pausesBefore, 'the speech in progress must be stopped for the new request');

  // 3. Ctrl+Alt+T (a "talk" item): Voice mode on, and off on the next
  //    press — but not before one click on the page, which the browser
  //    needs before it opens the mic and sound; it says so instead.
  await sleep(300);
  const root = w.document.getElementById('companion');
  const chatsBefore = chats.length;
  pending.push({seq: 9, kind: 'talk', source: 'hotkey', question: '', ts: 3});
  for (let i = 0; i < 100 && !$('error').textContent; i++) await sleep(10);
  assert.match($('error').textContent, /Click anywhere on this page once/);
  assert.equal(root.dataset.view, undefined); assert.equal(listening, 0);
  activated = true;
  pending.push({seq: 10, kind: 'talk', source: 'hotkey', question: '', ts: 4});
  for (let i = 0; i < 200 && root.dataset.view !== 'voice'; i++) await sleep(10);
  assert.equal(root.dataset.view, 'voice', 'the talk hotkey must start Voice mode'); assert.equal(listening, 1);
  pending.push({seq: 11, kind: 'talk', source: 'hotkey', question: '', ts: 5});
  for (let i = 0; i < 200 && root.dataset.view === 'voice'; i++) await sleep(10);
  assert.equal(root.dataset.view, undefined, 'pressed again, it stops');
  assert.equal(chats.length, chatsBefore, 'a talk press is not a question to answer');

  console.log('PASS: Ctrl+Alt+T toggles Voice mode (after one click on the page, with a clear message before); look requests start from now, become turns that use the server capture (no browser image), '
    + 'carry the spoken question when there is one, and cut off speech already in progress.');
  dom.window.close();
  process.exit(0);
})().catch(e => { console.error(e); dom.window.close(); process.exit(1); });
