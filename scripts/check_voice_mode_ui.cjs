// DOM simulation of Voice mode in the companion: one button turns the page
// into talk-only — Celine's voice, hands-free listening, the chat hidden — and
// Stop, Esc or a failure turns it back with the user's settings restored.
// Requires jsdom on NODE_PATH.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const base = require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const html = fs.readFileSync(base + 'companion.html', 'utf8');
const css = fs.readFileSync(base + 'companion.css', 'utf8');
const dom = new JSDOM(html, {url: 'http://localhost:7860/companion', runScripts: 'outside-only'});
const w = dom.window, $ = id => w.document.getElementById(id);
w.TextDecoder = TextDecoder;
w.localStorage.setItem('apex_token', 'tok');
w.speechSynthesis = {cancel() {}, speak() {}};
w.SpeechSynthesisUtterance = class {};
w.Audio = class { play() { setTimeout(() => { this.onplaying?.(); this.onended?.(); }, 1); return Promise.resolve(); } pause() {} };
w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};

let starts = 0, stops = 0, micFails = false, profilesDown = false;
w.ApexHandsFree = class {
  constructor(opts) { this.opts = opts; this.enabled = false; this.epoch = 0; }
  async start() { starts++; if (micFails) { this.opts.onError(new Error('Microphone permission was denied.')); return; } this.enabled = true; }
  stop() { stops++; this.enabled = false; }
  resume() {}
  pause() {}
  get busy() { return false; }
};
const chats = [];
w.fetch = async (path, opts = {}) => {
  if (path === '/api/status') return Response.json({agent_ready: true});
  if (path === '/api/voicebox/profiles') {
    if (profilesDown) return new Response('{}', {status: 503});
    return Response.json({profiles: [{id: 'other', name: 'Other'}, {id: 'celine', name: 'CELINE'}]});
  }
  if (path === '/api/speak/stream') return Response.json({streaming: false});
  if (path.startsWith('/api/companion/look')) { await sleep(50); return Response.json({item: null, seq: 0}); }
  if (path === '/api/companion/chat') {
    chats.push(JSON.parse(opts.body));
    return new Response(new ReadableStream({start(c) {
      c.enqueue(new TextEncoder().encode(JSON.stringify({type: 'start', thread_id: 1}) + '\n'));
      c.enqueue(new TextEncoder().encode(JSON.stringify({type: 'done', text: 'Hi, I am Celine.'}) + '\n')); c.close(); }}));
  }
  if (path === '/api/speak') return new Response(new Blob(['RIFF']));
  if (path === '/api/companion/timing') return Response.json({ok: true});
  throw Error('unexpected ' + path);
};
w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));
const root = $('companion');
const esc = () => w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));

(async () => {
  await sleep(80);
  assert.match(html, /companion\.js\?v=voice-mode-1/, 'bump the cache-bust so browsers load the new page');
  assert.match(css, /#companion\[data-view=voice\]>:not\(header\):not\(\.presence\):not\(#messages\):not\(#error\)\{display:none\}/,
    'voice mode must hide the chat chrome but keep the orb, captions and errors');
  assert.equal($('voice-exit').hidden, true, 'the exit button is only for voice mode');

  // The user's own settings, to be put back afterwards.
  $('voice').value = 'browser'; $('spoken').checked = false;

  // 1. Enter: Celine's voice, her profile, spoken replies, hands-free on.
  $('voice-mode').click();
  for (let i = 0; i < 100 && !starts; i++) await sleep(10);
  assert.equal(root.dataset.view, 'voice');
  assert.equal($('voice-mode').getAttribute('aria-pressed'), 'true');
  assert.equal($('voice-exit').hidden, false);
  assert.equal($('voice').value, 'voicebox');
  assert.equal($('voicebox-profile').value, 'celine', 'voice mode must pick the CELINE profile, not whatever was first');
  assert.equal($('spoken').checked, true);
  assert.equal($('hands-free').checked, true);
  assert.equal(starts, 1, 'hands-free listening must start');

  // 2. A turn in voice mode is a Celine turn, labelled as hers.
  $('message').value = 'what is your name';
  $('composer').dispatchEvent(new w.Event('submit'));
  for (let i = 0; i < 100 && !root.querySelector('.message.agent'); i++) await sleep(10);
  assert.equal(chats.at(-1).voice, 'voicebox');
  assert.equal(chats.at(-1).voice_profile, 'celine');
  assert.equal(root.querySelector('.message.agent .role').textContent, 'CELINE');
  await sleep(100);

  // 3. Esc leaves, stops listening and restores the user's settings.
  esc();
  await sleep(50);
  assert.equal(root.dataset.view, undefined, 'Esc must leave voice mode');
  assert.equal($('hands-free').checked, false, 'leaving must stop listening');
  assert.ok(stops > 0);
  assert.equal($('voice').value, 'browser', 'leaving must restore the voice the user had');
  assert.equal($('spoken').checked, false);
  assert.equal($('voice-exit').hidden, true);
  esc();    // Esc outside voice mode does nothing (and throws nothing)

  // 4. Stop (the in-view button) also leaves.
  $('voice-mode').click();
  for (let i = 0; i < 100 && starts < 2; i++) await sleep(10);
  assert.equal(root.dataset.view, 'voice');
  $('voice-exit').click();
  await sleep(50);
  assert.equal(root.dataset.view, undefined, 'Stop must leave voice mode');

  // 5. No microphone: the reason shows and the view does not stay open deaf.
  micFails = true;
  $('voice-mode').click();
  for (let i = 0; i < 100 && starts < 3; i++) await sleep(10);
  await sleep(30);
  assert.equal(root.dataset.view, undefined, 'a voice mode that cannot hear must close');
  assert.match($('error').textContent, /Microphone permission/);
  micFails = false;

  // 6. No voice server: say so, and do not open.
  profilesDown = true; $('voice').value = 'voicebox';
  const before = starts;
  $('voice-mode').click();
  await sleep(100);
  assert.equal(root.dataset.view, undefined);
  assert.equal(starts, before, 'no listening without a voice to answer');
  assert.match($('error').textContent, /Voice server not reachable/);

  console.log('PASS: voice mode switches to Celine (her profile), spoken replies and hands-free, labels replies as hers, '
    + 'and Esc, Stop, a missing microphone or a missing voice server all leave it with the settings restored.');
  dom.window.close();
  process.exit(0);
})().catch(e => { console.error(e); dom.window.close(); process.exit(1); });
