// DOM simulation of the relay's /phone page (the page lives inside
// relay/server.py so the relay stays one file). Requires jsdom on NODE_PATH.
// Mocks fetch; the real routes are covered by tests/test_relay_phone.py.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const src = fs.readFileSync(require('path').join(__dirname, '..', 'relay', 'server.py'), 'utf8');
const html = /PHONE_PAGE = r"""([\s\S]*?)"""/.exec(src)[1];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';

function page(fetchImpl, token) {
  const dom = new JSDOM(html, {url: 'https://relay.example.ts.net/phone', runScripts: 'dangerously',
    beforeParse(w) { if (token) w.localStorage.setItem('apex_relay_token', token); w.fetch = fetchImpl; }});
  return dom;
}
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {status, headers: {'Content-Type': 'application/json'}});

(async () => {
  // 1. No token yet: the login form, nothing else.
  {
    const dom = page(async () => { throw Error('must not call the relay without a token'); });
    await sleep(20);
    const $ = id => dom.window.document.getElementById(id);
    assert.ok(!$('login').hidden && $('ask').hidden, 'first visit must ask for the token');
    dom.window.close();
  }

  // 2. A refused token goes back to the login with a reason.
  {
    const dom = page(async () => json({error: 'bad token'}, 401), 'wrong');
    await sleep(30);
    const $ = id => dom.window.document.getElementById(id);
    assert.ok(!$('login').hidden, 'a 401 must return to the token form');
    assert.match($('error').textContent, /refused/);
    dom.window.close();
  }

  // 3. The full loop: ask, wait, answer — and a hostile answer stays text.
  {
    const now = () => Date.now() / 1000;
    let asked = null, polls = 0, sentAuth = '';
    const dom = page(async (path, opts = {}) => {
      sentAuth = opts.headers?.Authorization || '';
      if (path === '/status') return json({now: now(), context_at: now() - 60, snapshot_at: now() - 60, answerer_seen_at: now() - 2});
      if (path === '/questions' && (!opts.method || opts.method === 'GET')) return json({items: []});
      if (path === '/questions' && opts.method === 'POST') { asked = JSON.parse(opts.body).text; return json({ok: true, id: 7}); }
      if (path === '/questions/7') {
        polls++;
        return polls < 2 ? json({id: 7, text: asked, status: 'answering', created_at: now(), answered_at: 0, error: '', requests: []})
          : json({id: 7, text: asked, status: 'answered', created_at: now(), answered_at: now(), error: '',
                  answer: HOSTILE, requests: [{tool: 'send_email', why: 'you asked'}]});
      }
      throw Error('unexpected ' + path);
    }, 'good-token');
    await sleep(30);
    const w = dom.window, $ = id => w.document.getElementById(id);
    assert.equal(sentAuth, 'Bearer good-token');
    assert.ok($('login').hidden && !$('ask').hidden);
    assert.match($('status').textContent, /Answerer online/);
    assert.match($('status').textContent, /Laptop last sent/);
    $('text').value = 'When is my dentist?';
    $('ask').dispatchEvent(new w.Event('submit', {cancelable: true}));
    await sleep(20);
    assert.equal(asked, 'When is my dentist?');
    assert.match($('q7').textContent, /When is my dentist\?/);
    for (let i = 0; i < 30 && polls < 2; i++) await sleep(200);
    await sleep(50);
    const card = $('q7');
    assert.equal(card.querySelectorAll('img').length, 0, 'an answer injected markup');
    assert.ok(card.textContent.includes(HOSTILE), 'the answer must still be readable as text');
    assert.equal(w.pwned, undefined);
    assert.match(card.textContent, /Queued for your laptop: send_email/);
    assert.match(card.textContent, /Answered/);
    dom.window.close();
  }

  // 4. Nobody answering: after 20 s the page says so instead of spinning.
  {
    const now = () => Date.now() / 1000;
    const dom = page(async path => {
      if (path === '/status') return json({now: now(), context_at: 0, snapshot_at: 0, answerer_seen_at: 0});
      if (path === '/questions') return json({items: [{id: 3, text: 'anyone?', status: 'queued',
        created_at: now() - 60, answered_at: 0, error: '', requests: []}]});
      if (path === '/questions/3') return json({id: 3, text: 'anyone?', status: 'queued',
        created_at: now() - 60, answered_at: 0, error: '', requests: []});
      throw Error(path);
    }, 'good-token');
    await sleep(40);
    const $ = id => dom.window.document.getElementById(id);
    assert.match($('status').textContent, /Answerer offline/);
    assert.match($('status').textContent, /never/, 'a laptop that never sent anything must say so');
    assert.match($('q3').textContent, /Nobody is picking this up/);
    dom.window.close();
  }

  // 5. Forgetting the token clears it from the phone.
  {
    const dom = page(async path => path === '/status'
      ? json({now: 1, context_at: 0, snapshot_at: 0, answerer_seen_at: 0}) : json({items: []}), 'good-token');
    await sleep(30);
    const w = dom.window, $ = id => w.document.getElementById(id);
    $('forget').click();
    assert.equal(w.localStorage.getItem('apex_relay_token'), null);
    assert.ok(!$('login').hidden);
    dom.window.close();
  }

  console.log('PASS: relay phone page asks for the token first, returns to it on 401, '
    + 'asks and shows the answer as text with queued actions, says when nobody is answering, '
    + 'and forgets the token on request.');
})().catch(e => {
  // Exit, not exitCode: a page left open keeps its polling timers alive, so a
  // failed check would otherwise hang CI instead of failing it.
  console.error(e); process.exit(1);
});
