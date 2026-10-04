// The car's orb screen (/drive#orb): one tap starts Voice mode full screen, on a
// cloud Apex with no Celine server it talks in the cloud voice and says so, the
// screen is kept awake, the link light stays visible, and Stop brings the tap
// screen back. Requires jsdom on NODE_PATH.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const base = require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const dom = new JSDOM(fs.readFileSync(base + 'companion.html', 'utf8'), {url: 'https://apex.up.railway.app/drive#orb', runScripts: 'outside-only'});
const w = dom.window, $ = id => w.document.getElementById(id);
w.TextDecoder = TextDecoder;
w.localStorage.setItem('apex_token', 'tok');
w.speechSynthesis = {cancel() {}, speak() {}};
w.SpeechSynthesisUtterance = class {};
w.setInterval = () => 0;
const realTimeout = w.setTimeout.bind(w);
w.setTimeout = (fn, ms) => ms >= 5000 ? 0 : realTimeout(fn, ms);
let wakes = 0, starts = 0;
Object.defineProperty(w.navigator, 'wakeLock', {value: {request: async () => { wakes++; return {released: false}; }}});
w.ApexHandsFree = class {
  constructor(opts) { this.opts = opts; this.enabled = false; }
  async start() { starts++; this.enabled = true; }
  stop() { this.enabled = false; } resume() {} pause() {}
  get busy() { return false; }
};
w.fetch = async (path) => {
  if (path === '/api/status') return Response.json({agent_ready: true, voices: {openai: true}});
  if (path === '/api/voicebox/profiles') return new Response('{}', {status: 503});   // no Celine server in the cloud
  if (path === '/api/speak/stream') return Response.json({streaming: false});
  if (path === '/api/companion/jobs') return Response.json({jobs: []});
  throw Error('unexpected ' + path);
};
w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
w.eval(fs.readFileSync(base + 'car_link.js', 'utf8'));
w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));
const root = $('companion');

(async () => {
  await sleep(120);
  assert.ok(w.document.body.classList.contains('orb-screen'));
  const start = w.document.getElementById('orb-start');
  assert.ok(start && !start.hidden, 'a full-screen tap to start, since the microphone needs one tap');
  assert.equal(w.document.getElementById('car-link').closest('.presence'), root.querySelector('.presence'),
    'the link light sits with the orb, so it stays visible in Voice mode');
  start.click();
  await sleep(150);
  assert.equal(root.dataset.view, 'voice');
  assert.equal($('voice').value, 'openai', 'no Celine server here: the cloud voice speaks');
  assert.match($('status').textContent, /cloud voice/);
  assert.equal(starts, 1, 'hands-free listening started');
  assert.equal(wakes, 1, 'the screen is kept awake');
  assert.ok(start.hidden);
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await sleep(30);
  assert.equal(root.dataset.view, undefined);
  assert.equal(start.hidden, false, 'Stop brings the tap-to-start screen back');
  console.log('PASS: orb screen starts with one tap, falls back to the cloud voice on a cloud Apex, keeps the screen awake, shows the link light, and returns to tap-to-start.');
  dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exit(1); });
