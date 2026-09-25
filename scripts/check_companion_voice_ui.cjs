// DOM simulation of the companion's VOICE setup across the login boundary.
// Requires jsdom on NODE_PATH. Mocks fetch; does not verify a real browser.
//
// Both regressions below were found by driving the real page in Chromium, not
// by reading the code, and neither produced a JS error — the page looked fine
// and quietly did the wrong thing.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const base = require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;

const dom = new JSDOM(fs.readFileSync(base + 'companion.html', 'utf8'),
  {url: 'http://localhost:7860/companion', runScripts: 'outside-only'});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
const tick = () => new Promise(r => setTimeout(r, 15));

w.TextDecoder = TextDecoder;
w.speechSynthesis = {cancel() {}, speak(u) { u.onend?.(); }};
w.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };

// The server as it actually behaves: everything under /api needs the token,
// so a fetch made before the login dialog is answered gets a 401.
const PROFILES = {profiles: [{id: 'celine', name: 'Celine'},
                             {id: 'p-ryan', name: 'Apex Qwen Local'}]};
let profileFetches = 0, voiceServerDown = false;
w.fetch = async (path, opts = {}) => {
  const authed = Boolean(opts.headers && opts.headers.Authorization);
  if (path === '/api/voicebox/profiles') {
    profileFetches++;
    if (!authed) return new Response('', {status: 401});
    return voiceServerDown ? Response.json({error: 'Keep Voicebox open on the Apex laptop.'}, {status: 503})
      : Response.json(PROFILES);
  }
  if (!authed) return new Response('', {status: 401});
  if (path === '/api/status') return Response.json({agent_ready: true});
  if (path.startsWith('/api/chat/threads')) return Response.json({messages: []});
  throw Error(path);
};

w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));

(async () => {
  await tick();

  // Before login: the list was fetched, refused, and swallowed. That part is
  // intended — Voicebox may simply not be open, and a banner on every load is
  // a banner nobody reads.
  assert.equal(profileFetches, 1, 'the profile list should be fetched on load');
  assert.deepEqual([...$('voicebox-profile').options].map(o => o.value), [''],
    'before authenticating there is nothing but the default');
  assert.ok(!$('error').hidden, 'the 401 should surface while unauthenticated');
  assert.ok($('voice-note').hidden, 'before sign-in the answer is "sign in", not "start a voice server"');

  // Log in, exactly as a user does.
  w.localStorage.setItem('apex_token', 'whowantstobeking');
  $('token').value = 'whowantstobeking';
  $('login-form').dispatchEvent(new w.Event('submit'));
  await tick(); await tick();

  // REGRESSION 1 — the list is refetched once a token exists.
  // It used to be fetched exactly once, at script load, BEFORE the login
  // dialog was answered. The catch swallowed the 401 and the comment said
  // "retry when voice changes" — but Local Qwen is the DEFAULT voice, so that
  // change never happens. A user's own cloned voice stayed invisible until
  // they happened to reload the page.
  assert.ok(profileFetches >= 2,
    'the profile list must be refetched after the token is accepted');
  const names = [...$('voicebox-profile').options].map(o => o.textContent);
  assert.ok(names.includes('Celine'),
    `a cloned voice must appear after login, got ${JSON.stringify(names)}`);

  // REGRESSION 2 — a successful boot clears the earlier failure.
  // The login handler cleared #login-error and not #error, so the red
  // "Enter your Apex dashboard token to continue." banner from the pre-login
  // 401 stayed on screen for the whole session. The page worked; it read as
  // broken. Caught from a screenshot, not from a test.
  assert.ok($('error').hidden,
    `a working page must not still show "${$('error').textContent}"`);

  // The remembered choice survives and is what gets sent.
  $('voicebox-profile').value = 'celine';
  $('voicebox-profile').dispatchEvent(new w.Event('change'));
  assert.equal(w.localStorage.getItem('apex.voicebox.profile'), 'celine');

  // Local Qwen is the default voice, which is why regression 1 was invisible.
  assert.equal($('voice').value, 'voicebox');
  assert.ok($('voice-note').hidden, 'a reachable voice server must not show the note');

  // No voice server (Apex started with Apex.bat, not Start-Apex-Celine.cmd):
  // the picker has only the default, so say what to start instead of nothing.
  // Found on real hardware: Celine "was not there" and nothing said why.
  voiceServerDown = true;
  $('voice').dispatchEvent(new w.Event('change'));
  await tick(); await tick();
  assert.ok(!$('voice-note').hidden, 'an unreachable voice server must be named, not silent');
  assert.match($('voice-note').textContent, /Start-Apex-Celine\.cmd/);
  assert.ok($('error').hidden, 'still no red banner for it');

  console.log('PASS: profile list refetched after login, cloned voice offered, '
    + 'stale token banner cleared, profile choice remembered.');
  dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
