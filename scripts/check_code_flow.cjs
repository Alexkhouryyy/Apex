// Acceptance of the shared idea/improvement composer; synthetic API, no model calls.
const {JSDOM} = require('jsdom');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const base = path.join(__dirname, '..', 'dashboard', 'static');
const projects = [{id: 1, name: 'Atlas', path: 'C:/Atlas', checks: 'npm test'}, {id: 2, name: 'Workshop', path: 'C:/Workshop', checks: 'pytest'}];
const plans = ['claude', 'chatgpt'].map(id => ({id, name: id + ' plan', ready: true}));
const session = (id, working = false) => ({id, project_id: id === 7 ? 1 : 2, project: id === 7 ? 'Atlas' : 'Workshop',
  title: id === 7 ? 'Build a reading room' : 'Improve reminders', engine: 'claude', mode: 'safe', model: '', effort: '',
  branch: 'apex/example', worktree: 'C:/sample', status: 'ready', working, operation: '', side: '', terminal: false, tokens: 0,
  changes: {files: [{path: 'app.py', change: 'update', plus: 2, minus: 1}], plus: 2, minus: 1},
  queue: {items: [], paused: false, reason: ''}, summary: '', review_state: '', check_state: '', conflict: 0, since: Date.now()/1000});
const proof = () => ({verdict: 'unverified', reasons: ['No current checks'], claims: [], saw_agent: [], saw_you: [], owner_evidence: null,
  goalpost: [], review: null, checks: {state: 'none', why: '', command: 'npm test', stale: false, changed_since: []}});
const sessions = {7: session(7, true), 8: session(8)};
const calls = [], errors = [];
let failQueue = false, holdMessage = null, messageEntered = null;
const dom = new JSDOM(fs.readFileSync(path.join(base, 'code.html'), 'utf8'), {url: 'http://localhost/code', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
w.HTMLDialogElement.prototype.showModal = function() { this.open = true; };
w.HTMLDialogElement.prototype.close = function(value) { this.returnValue = value; this.open = false; this.dispatchEvent(new w.Event('close')); };
w.HTMLFormElement.prototype.requestSubmit = function() { this.dispatchEvent(new w.Event('submit', {cancelable: true})); };
w.HTMLElement.prototype.scrollIntoView = function() {};
w.CSS = {escape: s => s}; w.scrollTo = () => {};
w.Response = Response; w.AbortController = AbortController; w.TextDecoder = TextDecoder;
w.addEventListener('error', e => errors.push(e.message));
w.fetch = async (url, options = {}) => {
  const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : {};
  calls.push({url, method, body});
  if (url === '/api/code') return Response.json({owner: 'Alex', projects, plans, sessions: Object.values(sessions), default_engine: 'claude', record: {}, week: {sessions: 2, kept: 0, proved: 0, rating: null, minutes: 0}});
  if (url === '/api/code/approvals') return Response.json({items: []});
  if (url === '/api/code/overnight') return Response.json({sessions: []});
  if (url.includes('/projects/') && url.endsWith('/brain')) return Response.json({sources: [], unvouched: [], chars: 0});
  if (url === '/api/code/projects') { const p = {id: 3, name: body.name || 'New idea', path: body.path, checks: ''}; projects.push(p); return Response.json(p); }
  if (url === '/api/code/sessions' && method === 'POST') { sessions[9] = {...session(9, true), project_id: body.project_id}; return Response.json(sessions[9]); }
  const match = url.match(/^\/api\/code\/sessions\/(\d+)(?:\/([^?]+))?/);
  if (!match) throw new Error('Unexpected request ' + url);
  const s = sessions[match[1]], action = match[2];
  if (!action) return Response.json(s);
  if (action === 'events') {
    const events = s.id === 7 && s.queue.paused && !s.working ? [
      {id: 1, ts: Date.now()/1000, kind: 'you', text: 'Plan the reading room', engine: 'claude', mode: 'safe'},
      {id: 2, ts: Date.now()/1000, kind: 'done', status: 'done', plan: true, engine: 'claude', summary: 'Plan ready', files: 0, total: 1, seconds: 1}] : [];
    const after = Number(new URL(url, 'http://localhost').searchParams.get('after') || 0);
    return Response.json({events: events.filter(e => e.id > after)});
  }
  if (action === 'proof') return Response.json(proof());
  if (action === 'live') return Response.json({v: 1, text: '', thinking: '', outputs: {}});
  if (action === 'stream') return Response.json({}, {status: 404});
  if (action === 'queue') {
    if (failQueue) return Response.json({detail: 'Connection interrupted'}, {status: 503});
    s.queue.items.push({id: body.request_id, ...body}); return Response.json(s.queue);
  }
  if (action === 'queue-pause') { s.queue.paused = true; s.queue.reason = 'Paused by you'; return Response.json(s.queue); }
  if (action === 'queue-resume') { s.queue.paused = false; return Response.json(s.queue); }
  if (action === 'queue-remove') { s.queue.items = s.queue.items.filter(x => x.id !== body.id); return Response.json(s.queue); }
  if (action === 'stop') { s.working = false; s.queue.paused = true; s.queue.reason = 'Stopped by you'; return Response.json({stopped: true}); }
  if (action === 'messages') {
    if (messageEntered) messageEntered();
    if (holdMessage) await holdMessage;
    s.working = true; return Response.json(s);
  }
  throw new Error('Unexpected action ' + action);
};
w.localStorage.setItem('apex.code.narrate', 'off');
w.localStorage.setItem('apex.code.composer-drafts', JSON.stringify({'p:1': {text: 'Saved idea before reload', updated: Date.now()}}));
w.eval(fs.readFileSync(path.join(base, 'code.js'), 'utf8'));
const tick = (ms = 30) => new Promise(resolve => setTimeout(resolve, ms));
const type = text => { $('prompt').value = text; $('prompt').dispatchEvent(new w.Event('input', {bubbles: true})); };
const open = async id => { w.location.hash = `#s=${id}`; await tick(180); };
const submit = async text => { type(text); $('brief-form').requestSubmit(); await tick(120); };
(async () => {
  await tick(200);
  assert.equal($('prompt').value, 'Saved idea before reload');
  type('New Atlas idea');
  $('project').value = '2'; $('project').dispatchEvent(new w.Event('change')); await tick();
  assert.equal($('prompt').value, ''); type('Improve Workshop');
  $('project').value = '1'; $('project').dispatchEvent(new w.Event('change')); await tick();
  assert.equal($('prompt').value, 'New Atlas idea', 'Projects own their drafts');
  await open(7);
  assert.equal($('prompt').value, '', 'A session does not inherit a project draft');
  assert.match($('send').textContent, /Queue next/); assert.equal($('stop-run').hidden, false);
  await submit('Add keyboard navigation');
  assert.equal(calls.filter(c => c.url.endsWith('/stop')).length, 0, 'Submitting must never stop the current run');
  assert.equal(sessions[7].queue.items[0].prompt, 'Add keyboard navigation');
  assert.match($('queue-items').textContent, /Add keyboard navigation/);
  assert.match($('toast').textContent, /runs next, in order/);
  assert.equal($('prompt').value, '');
  failQueue = true; await submit('Keep this after failure');
  assert.equal($('prompt').value, 'Keep this after failure');
  const failed = calls.filter(c => c.url.endsWith('/queue')).at(-1).body.request_id;
  failQueue = false; $('brief-form').requestSubmit(); await tick(120);
  assert.equal(calls.filter(c => c.url.endsWith('/queue')).at(-1).body.request_id, failed, 'Retry keeps the idempotency key');
  assert.equal($('prompt').value, '');
  $('queue-items').querySelector('button').click(); await tick(100);
  assert.equal(sessions[7].queue.items.length, 1);
  $('queue-toggle').click(); await tick(100); assert.equal(sessions[7].queue.paused, true);
  assert.equal($('queue-toggle').disabled, true, 'Cannot resume while the current step is still running');
  $('stop-run').click(); await tick(600);
  assert.equal(calls.filter(c => c.url.endsWith('/stop')).length, 1);
  assert.equal(sessions[7].queue.paused, true);
  await submit('While paused');                                   // said at once, never a silent wait
  assert.match($('toast').textContent, /follow-ups are paused: Stopped by you.*Resume/);
  sessions[7].queue.items = sessions[7].queue.items.filter(x => x.prompt !== 'While paused');
  assert.match($('code-path').textContent, /Build.*Inspect.*Verify.*Keep/);
  const approvePlan = [...$('feed').querySelectorAll('button')].find(b => b.textContent.includes('Build it'));
  assert.ok(approvePlan, 'The plan offers its explicit Build it action');
  approvePlan.click(); await tick(120);
  assert.equal(calls.filter(c => c.url === '/api/code/sessions/7/messages').at(-1).body.prompt, 'Go ahead with that plan.');
  assert.equal(sessions[7].queue.items.length, 1, 'Build it does not jump into the pending follow-ups');
  assert.equal(sessions[7].queue.paused, true);
  type('Draft in session 7'); await open(8);
  assert.equal($('prompt').value, ''); type('Draft in session 8'); await open(7);
  assert.equal($('prompt').value, 'Draft in session 7');
  await open(8);
  // A response from session 8 arriving after switching must preserve session 7's draft and state.
  let finish, entered;
  holdMessage = new Promise(resolve => { finish = resolve; });
  const ready = new Promise(resolve => { entered = resolve; }); messageEntered = entered;
  type('Send this to session 8'); $('brief-form').requestSubmit(); await ready;
  await open(7); type('New thought in session 7'); finish(); await tick(160);
  holdMessage = null; messageEntered = null;
  assert.equal($('prompt').value, 'New thought in session 7');
  assert.equal($('s-title').textContent, 'Build a reading room');
  $('back').click(); await tick(); assert.equal($('prompt').value, 'New Atlas idea');
  $('add-project').click(); $('add-path').value = 'C:/Projects/fresh-idea'; $('add-name').value = 'Fresh idea'; $('add-create').checked = true;
  $('add-dialog').close('ok'); await tick(160);
  assert.equal(calls.filter(c => c.url === '/api/code/projects').at(-1).body.create, true);
  assert.equal($('prompt').value, 'New Atlas idea', 'Creating a project carries the current idea into its new draft');
  await submit('Build a focused notes app');
  const build = calls.filter(c => c.url === '/api/code/sessions' && c.method === 'POST').at(-1);
  assert.equal(build.body.project_id, 3); assert.equal(build.body.prompt, 'Build a focused notes app');
  assert.deepEqual(errors, []);
  console.log('PASS: idea creation, existing projects, per-context drafts, queued Send, explicit Stop, pause/remove, failed retry idempotency, stale response isolation and proof workflow.');
  dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exitCode = 1; });
