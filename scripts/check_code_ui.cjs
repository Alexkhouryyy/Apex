// The Code page (dashboard/static/code.js) against a stubbed API: the greeting
// and your plans, starting a session from the brief, every kind of step in the
// live feed (reads grouped, commands with their result, edits that open the
// diff, the plan, checkpoints, done, the second opinion's rating), nothing an
// agent writes ever running as HTML, Keep only on a real "yes" (never Esc),
// Esc stopping a running task, and a resting plan never offered by default.
const {JSDOM} = require('jsdom'), fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const base = path.join(__dirname, '..', 'dashboard', 'static');
const dom = new JSDOM(fs.readFileSync(path.join(base, 'code.html'), 'utf8'), {url: 'http://localhost:7860/code', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function (v) { if (v !== undefined) this.returnValue = v; this.open = false; this.dispatchEvent(new w.Event('close')); };
w.HTMLFormElement.prototype.requestSubmit = function () { this.dispatchEvent(new w.Event('submit', {cancelable: true})); };
w.scrollTo = () => {};
const now = Date.now() / 1000;
let plans = [{id: 'claude', name: 'Claude plan', ready: true, why: '', how: ''}, {id: 'chatgpt', name: 'ChatGPT plan', ready: true, why: '', how: ''}];
const overview = () => ({owner: 'Alex', projects: [{id: 1, name: 'Apex', path: 'C:\\Apex', checks: 'python -m pytest -q', branch: 'main'}],
  sessions: [{id: 7, project_id: 1, title: 'Add Focus mode', engine: 'claude', mode: 'safe', status: 'ready', last_status: 'done', files_changed: 2, review_rating: 8, updated: now - 120, working: false},
             {id: 3, project_id: 1, title: 'Old try', engine: 'chatgpt', mode: 'safe', status: 'discarded', last_status: 'done', files_changed: 0, review_rating: null, updated: now - 9000, working: false}],
  plans, default_engine: 'claude', week: {sessions: 2, kept: 0, rating: 8, minutes: 3, credits: 0}, working: 0});
let session = {id: 7, project_id: 1, project: 'Apex', title: 'Add Focus mode', engine: 'claude', mode: 'safe', branch: 'apex/7-add-focus-mode', worktree: 'C:\\ApexWork\\code\\apex-7',
  status: 'ready', working: true, side: '', since: now - 75, summary: '', review_state: 'done', review_engine: 'chatgpt', review_rating: 8, review_text: 'Rating: 8/10\nVerdict: Clean.\nProblems:\n- None found',
  check_state: '', check_output: '', conflict: 0, kept_commit: '', changes: {files: [{path: 'work.js', plus: 11, minus: 0, change: 'update'}, {path: 'new.css', plus: 2, minus: 0, change: 'add'}], plus: 13, minus: 0}};
const E = (id, kind, extra = {}) => ({id, ts: now - 60 + id, kind, ...extra});
let feed = [
  E(1, 'you', {text: 'Add Focus mode', engine: 'claude', mode: 'safe'}),
  E(2, 'thinking', {text: 'Plan it'}),
  E(3, 'text', {text: 'I\'ll add **Focus**. <img src=x onerror="window.pwned=1"> `code`'}),
  E(4, 'todo', {items: [{text: 'Add button', done: false, active: true}, {text: 'Test', done: false, active: false}]}),
  E(5, 'tool', {tool: 'read', title: 'Read work.js', path: 'work.js', ref: 'r1'}),
  E(6, 'result', {ref: 'r1', ok: true, output: ''}),
  E(7, 'tool', {tool: 'read', title: 'Read work.css', path: 'work.css', ref: 'r2'}),
  E(8, 'tool', {tool: 'search', title: 'Searched for renderHeader', ref: 'g1'}),
  E(9, 'file', {path: 'work.js', change: 'update', plus: 11, minus: 0, ref: 'e1'}),
  E(10, 'todo', {items: [{text: 'Add button', done: true, active: false}, {text: 'Test', done: false, active: true}]}),
  E(11, 'tool', {tool: 'command', title: 'python -m pytest -q', ref: 'b1'}),
  E(12, 'result', {ref: 'b1', ok: false, exit_code: 1, output: '1 failed'}),
  E(13, 'blocked', {title: 'Bash: rm -rf build'}),
];
const calls = [];
w.fetch = async (url, opts = {}) => {
  calls.push({url, method: opts.method || 'GET', body: opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : null});
  if (url === '/api/code') return w.Response ? Response.json(overview()) : null;
  if (url === '/api/code/sessions' && opts.method === 'POST') return Response.json({...session, id: 9});
  let m = url.match(/^\/api\/code\/sessions\/(\d+)\/events\?after=(.*)$/);
  if (m) { assert.match(m[2], /^\d+$/, 'the feed always asks with a real number'); return Response.json({events: feed.filter(e => e.id > Number(m[2]))}); }
  if (/\/diff\?path=/.test(url)) return new Response('@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d', {status: 200});
  m = url.match(/^\/api\/code\/sessions\/(\d+)\/(\w[\w-]*)$/);
  if (m && opts.method === 'POST') {
    if (m[2] === 'keep') { session = {...session, status: 'kept', kept_commit: 'abc1234567'}; return Response.json(session); }
    if (m[2] === 'messages') return Response.json({...session, working: true});
    return Response.json({stopped: true});
  }
  if (/^\/api\/code\/sessions\/\d+$/.test(url)) return Response.json(session);
  throw Error('unexpected ' + url);
};
w.Response = Response;
w.eval(fs.readFileSync(path.join(base, 'theme.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(base, 'code.js'), 'utf8'));
const tick = (ms = 40) => new Promise(r => setTimeout(r, ms));
(async () => {
  await tick(); await tick();
  // Home: Alex, both plans, the week, quick starts, recent work.
  assert.match($('greeting').textContent, /, Alex\.$/);
  assert.equal($('greeting').querySelector('.name').textContent, 'Alex');
  assert.match($('greet-sub').textContent, /Both plans are ready/);
  assert.deepEqual([...d.querySelectorAll('.plan b')].map(b => b.textContent), ['Claude', 'ChatGPT']);
  assert.match($('week').textContent, /\$0API credits/);
  assert.equal(d.querySelectorAll('.card').length, 2);
  assert.ok(d.querySelector('.card .ring10'), 'a rated session shows its score');
  assert.ok($('home-slot').contains($('brief-form')), 'the brief sits on the home page');
  [...d.querySelectorAll('.chip')].find(b => b.textContent.includes('Fix a bug')).click();
  assert.equal($('prompt').value, 'Fix this bug in Apex: ');
  // A plan at its limit is never the default: the ready one is offered.
  plans = [{id: 'claude', name: 'Claude plan', ready: false, why: 'reached its usage limit, resting until 15:40', how: ''}, plans[1]];
  // Start a session from the brief (Ctrl+Enter).
  $('prompt').value = 'Add Focus mode';
  d.querySelector('[data-mode=full]').click();
  $('prompt').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true}));
  await tick(); await tick(); await tick();
  const start = calls.find(c => c.url === '/api/code/sessions');
  assert.deepEqual(start.body, {project_id: 1, prompt: 'Add Focus mode', engine: 'claude', mode: 'full'});
  assert.equal(d.body.className, 'view-session'); assert.equal(w.location.hash, '#s=9');
  assert.ok($('session-slot').contains($('brief-form')), 'the brief docks under the feed');
  // The session's plan is resting: the composer offers the ready one.
  assert.equal(d.querySelector('[data-engine=chatgpt]').getAttribute('aria-checked'), 'true');
  // The feed.
  const f = $('feed');
  assert.equal(f.querySelector('.avatar').textContent, 'A');
  assert.equal(f.querySelector('.prose img'), null, 'agent text never becomes HTML');
  assert.equal(w.pwned, undefined);
  assert.match(f.querySelector('.prose').textContent, /<img src=x/); assert.equal(f.querySelector('.prose strong').textContent, 'Focus');
  assert.equal(f.querySelectorAll('.todo').length, 1, 'the plan updates in place');
  assert.match(f.querySelector('.todo').textContent, /✓Add button/);
  const reads = f.querySelector('.step.reads');
  assert.match(reads.textContent, /^◇Read 2 files, searched 1 time/);
  const cmd = f.querySelector('.step.cmd');
  assert.equal(cmd.querySelector('.st').textContent, '✗ exit 1'); assert.equal(cmd.querySelector('details').open, true);
  assert.match(f.querySelector('.step.blocked').textContent, /Blocked in Safe mode: Bash: rm -rf build/);
  assert.match(f.querySelector('.working').textContent, /Apex is working · Claude plan · 1:1\d/, 'the clock counts from when you sent it');
  assert.match(f.querySelector('.working .last').textContent, /^Running python -m pytest -q|Blocked/);
  assert.equal($('send').textContent, '■ Stop');
  // Esc stops a running task.
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await tick();
  assert.ok(calls.some(c => c.url === '/api/code/sessions/9/stop' && c.method === 'POST'));
  // The rest of the turn arrives: done, then the second opinion.
  feed.push(E(14, 'text', {text: 'Added Focus.'}), E(15, 'checkpoint', {sha: 'a', prev: 'b', files: 2}),
            E(16, 'done', {status: 'done', summary: 'Added Focus.', seconds: 75, files: 2, total: 2, engine: 'claude'}),
            E(17, 'review_started', {engine: 'chatgpt'}), E(18, 'review_step', {title: 'Read work.js'}),
            E(19, 'review', {engine: 'chatgpt', status: 'done', rating: 8, text: 'Rating: 8/10\nVerdict: Clean.\nProblems:\n- None found'}));
  session = {...session, working: false};
  await tick(1300);
  assert.ok(f.querySelector('.prose.final'), 'the last answer is the summary, not repeated');
  assert.match(f.querySelector('.card2.done-ok').textContent, /✓ Done1:15 · 2 files this step · 2 in total · on your Claude plan/);
  assert.equal(f.querySelector('.card2 .ring10 text').textContent, '8');
  assert.equal(f.querySelector('.working'), null);
  assert.equal($('send').textContent, 'SendCtrl ⏎');
  // The side panel: changes, the review ring, Keep → main.
  assert.equal($('c-count').textContent, '2 · +13 −0');
  assert.equal(d.querySelector('#r-body .ring10 text').textContent, '8');
  assert.match($('r-body').textContent, /ChatGPT plan saysClean\./);
  assert.equal($('a-keep').textContent, '✓ Keep it → main'); assert.equal($('a-keep').disabled, false);
  // A changed file opens its diff, with numbered lines.
  f.querySelector('.step.file').click(); await tick();
  assert.equal($('diff').open, true); assert.equal($('diff-path').textContent, 'work.js');
  assert.deepEqual([...d.querySelectorAll('#diff-body .dl')].map(r => r.className.split(' ')[1]), ['hunk', 'ctx', 'del', 'add', 'add']);
  assert.deepEqual([...d.querySelectorAll('#diff-body .dl.add .ln')].map(x => x.textContent), ['', '2', '', '3']);
  $('diff-next').click(); await tick(); assert.equal($('diff-path').textContent, 'new.css');
  $('diff').close();
  // Keep: Esc (no answer) must never count as yes; only "Keep it" merges.
  $('a-keep').click(); await tick();
  assert.equal($('confirm').open, true); assert.match($('confirm-text').textContent, /merges 2 files into main of Apex\. Restart Apex/);
  $('confirm').close('cancel'); await tick();                          // Cancel
  $('confirm').returnValue = 'ok';                                     // an earlier dialog's "yes" still in there…
  $('a-keep').click(); await tick(); $('confirm').close(); await tick(); // …then Esc
  assert.equal(calls.filter(c => /\/keep$/.test(c.url)).length, 0, 'Esc and Cancel never keep');
  $('a-keep').click(); await tick(); $('confirm').close('ok'); await tick(); await tick();
  assert.equal(calls.filter(c => /\/keep$/.test(c.url)).length, 1);
  await tick(); await tick();
  assert.equal($('a-keep').textContent, '✓ Kept'); assert.equal($('prompt').disabled, true);
  // No microphone API here: a clear message, not an error.
  $('mic').click(); await tick();
  assert.match($('toast').textContent, /can't record here/);
  // Back home.
  $('back').click(); await tick();
  assert.equal(d.body.className, 'view-home'); assert.equal(w.location.hash, '');
  console.log('PASS: greeting and plans, quick starts, starting a session (Ctrl+Enter, Full mode), the brief docking, a resting plan never offered, '
    + 'every feed step (agent text never HTML, plan in place, reads grouped, failed command open, blocked), Esc stops, done and the summary not repeated, '
    + 'the second opinion ring, the side panel, the diff with line numbers and next file, Keep only on a real yes, and back home.');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
