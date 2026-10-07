// The Code page (dashboard/static/code.js) against a stubbed API: the greeting
// and your plans; @ and / as you type; starting a session with a model and an
// effort; polling until the live stream connects, then steps and typing over the
// stream; every kind of step (reads grouped, commands with their result, edits
// with their diff inline, the plan, checkpoints, done, the second opinion's
// rating); nothing an agent writes ever running as HTML; Allow once; Esc stops;
// !commands and the terminal; /commands; the file tree and coloured viewer;
// Ctrl+K; Plan first then Build it; history; Keep only on a real "yes" (never
// Esc), Keep & push; and a resting plan never offered by default.
const {JSDOM} = require('jsdom'), fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const base = path.join(__dirname, '..', 'dashboard', 'static');
const dom = new JSDOM(fs.readFileSync(path.join(base, 'code.html'), 'utf8'), {url: 'http://localhost:7860/code', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.HTMLDialogElement.prototype.close = function (v) { if (v !== undefined) this.returnValue = v; this.open = false; this.dispatchEvent(new w.Event('close')); };
w.HTMLFormElement.prototype.requestSubmit = function () { this.dispatchEvent(new w.Event('submit', {cancelable: true})); };
w.scrollTo = () => {};
w.ReadableStream = ReadableStream; w.TextDecoder = TextDecoder; w.AbortController = AbortController;
w.CSS = w.CSS || {escape: x => String(x).replace(/["\\]/g, '\\$&')};
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
  E(9, 'file', {path: 'work.js', change: 'update', plus: 1, minus: 0, ref: 'e1', diff: '@@ -4,1 +4,2 @@\n a\n+b'}),
  E(10, 'todo', {items: [{text: 'Add button', done: true, active: false}, {text: 'Test', done: false, active: true}]}),
  E(11, 'tool', {tool: 'command', title: 'python -m pytest -q', ref: 'b1'}),
  E(12, 'result', {ref: 'b1', ok: false, exit_code: 1, output: '1 failed'}),
  E(13, 'blocked', {title: 'Bash: rm -rf build', command: 'rm -rf build'}),
];
const calls = [];
let streamOn = false, streamLive = null;
const FILES = ['README.md', 'dashboard/static/work.js', 'dashboard/static/work.css'];
w.fetch = async (url, opts = {}) => {
  calls.push({url, method: opts.method || 'GET', body: opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : null});
  if (url === '/api/code') return Response.json(overview());
  if (url === '/api/code/sessions' && opts.method === 'POST') return Response.json({...session, id: 9});
  let m = url.match(/^\/api\/code\/sessions\/(\d+)\/events\?after=(.*)$/);
  if (m) { assert.match(m[2], /^\d+$/, 'the feed always asks with a real number'); return Response.json({events: feed.filter(e => e.id > Number(m[2]))}); }
  m = url.match(/^\/api\/code\/sessions\/(\d+)\/stream\?after=(.*)$/);
  if (m) {
    assert.match(m[2], /^\d+$/);
    if (!streamOn) return new Response('', {status: 503});
    const lines = feed.filter(e => e.id > Number(m[2])).map(e => JSON.stringify({t: 'event', ...e}));
    if (streamLive) lines.push(JSON.stringify({t: 'live', ...streamLive}));
    await new Promise(r => setTimeout(r, 60));
    return new Response(lines.join('\n') + '\n');
  }
  if (/\/live$/.test(url)) return Response.json({v: 0, text: '', thinking: '', outputs: {}});
  if (/\/tree$/.test(url)) return Response.json({files: FILES, cut: false});
  if (/\/file\?path=/.test(url)) return Response.json({path: decodeURIComponent(url.split('path=')[1]), text: 'const x = 1; // hi\nreturn "s";', lang: 'js', binary: false, too_big: false, size: 30});
  if (/\/history$/.test(url)) return Response.json({checkpoints: [{sha: 'a'.repeat(40), short: 'aaaaaaaa', files: 2, ts: now, undone: false, catch_up: false}]});
  if (/\/commit\?sha=/.test(url)) return new Response('Step\n\ndiff --git a/work.js b/work.js\nindex 1..2\n--- a/work.js\n+++ b/work.js\n@@ -1,1 +1,2 @@\n a\n+b', {status: 200});
  if (/\/diff\?path=/.test(url)) return new Response('@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d', {status: 200});
  m = url.match(/^\/api\/code\/sessions\/(\d+)\/(\w[\w-]*)$/);
  if (m && opts.method === 'POST') {
    if (m[2] === 'keep') { session = {...session, status: 'kept', kept_commit: 'abc1234567'}; return Response.json(session); }
    if (['messages', 'allow', 'review', 'terminal'].includes(m[2])) return Response.json({...session});
    return Response.json({stopped: true});
  }
  if (/^\/api\/code\/sessions\/\d+$/.test(url)) return Response.json(session);
  throw Error('unexpected ' + url);
};
w.Response = Response;
w.eval(fs.readFileSync(path.join(base, 'theme.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(base, 'code.js'), 'utf8'));
const tick = (ms = 40) => new Promise(r => setTimeout(r, ms));
const type = (text) => { const p = $('prompt'); p.focus(); p.value = text; p.setSelectionRange(text.length, text.length); p.dispatchEvent(new w.Event('input')); };
const key = (k, extra = {}) => $('prompt').dispatchEvent(new w.KeyboardEvent('keydown', {key: k, bubbles: true, cancelable: true, ...extra}));
const last = (re) => calls.filter(c => re.test(c.url) && c.method === 'POST').at(-1);
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
  assert.ok(d.body.classList.contains('term'), 'the Terminal look by default');
  [...d.querySelectorAll('.chip')].find(b => b.textContent.includes('Fix a bug')).click();
  assert.equal($('prompt').value, 'Fix this bug in Apex: ');
  // @ files and / commands as you type.
  type('Look at @wor'); await tick(); await tick();
  assert.equal($('suggest').hidden, false);
  assert.deepEqual([...$('suggest').querySelectorAll('b')].map(b => b.textContent).slice(0, 2), ['dashboard/static/work.js', 'dashboard/static/work.css']);
  key('Tab'); assert.equal($('prompt').value, 'Look at @dashboard/static/work.js '); assert.equal($('suggest').hidden, true);
  type('/re'); await tick();
  assert.match($('suggest').textContent, /\/reviewSecond opinion/); key('Escape'); assert.equal($('suggest').hidden, true);
  // A plan at its limit is never the default: the ready one is offered.
  plans = [{id: 'claude', name: 'Claude plan', ready: false, why: 'reached its usage limit, resting until 15:40', how: ''}, plans[1]];
  // Start a session from the brief (Ctrl+Enter), with a model and an effort.
  $('model').value = 'opus'; $('model').dispatchEvent(new w.Event('change'));
  $('effort').value = 'high'; $('effort').dispatchEvent(new w.Event('change'));
  type('Add Focus mode');
  d.querySelector('[data-mode=full]').click();
  key('Enter', {ctrlKey: true});
  await tick(); await tick(); await tick();
  const start = calls.find(c => c.url === '/api/code/sessions');
  assert.deepEqual(start.body, {project_id: 1, prompt: 'Add Focus mode', engine: 'claude', mode: 'full', model: 'opus', effort: 'high', plan: false});
  assert.ok(d.body.classList.contains('view-session') && d.body.classList.contains('term')); assert.equal(w.location.hash, '#s=9');
  assert.ok($('session-slot').contains($('brief-form')), 'the brief docks under the feed');
  // The session's plan is resting: the composer offers the ready one.
  assert.equal(d.querySelector('[data-engine=chatgpt]').getAttribute('aria-checked'), 'true');
  // The feed (the stream is down: polling brought it).
  const f = $('feed');
  assert.equal(f.querySelector('.avatar').textContent, 'A');
  assert.equal(f.querySelector('.prose img'), null, 'agent text never becomes HTML');
  assert.equal(w.pwned, undefined);
  assert.match(f.querySelector('.prose').textContent, /<img src=x/); assert.equal(f.querySelector('.prose strong').textContent, 'Focus');
  assert.equal(f.querySelectorAll('.todo').length, 1, 'the plan updates in place');
  assert.match(f.querySelector('.todo').textContent, /✓Add button/);
  assert.match(f.querySelector('.step.reads').textContent, /^◇Read 2 files, searched 1 time/);
  const cmd = f.querySelector('.step.cmd');
  assert.equal(cmd.querySelector('.st').textContent, '✗ exit 1'); assert.equal(cmd.querySelector('details').open, true);
  assert.deepEqual([...f.querySelectorAll('.step.file .inline-diff .dl')].map(r => r.className.split(' ')[1]), ['hunk', 'ctx', 'add'], 'the edit shows inline');
  assert.deepEqual([...f.querySelectorAll('.step.file .inline-diff .dl.add .ln')].map(x => x.textContent), ['', '5']);
  assert.match(f.querySelector('.step.blocked').textContent, /Safe mode stopped: Bash: rm -rf build/);
  assert.match(f.querySelector('.working').textContent, /Apex is working · Claude plan · 1:1\d/, 'the clock counts from when you sent it');
  assert.equal($('send').textContent, '■ Stop');
  // Allow once, like Claude Code's prompt.
  [...f.querySelectorAll('.step.blocked button')].find(b => b.textContent === 'Allow once').click(); await tick();
  assert.deepEqual(last(/\/allow$/).body, {command: 'rm -rf build', always: false});
  // Esc stops a running task.
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await tick();
  assert.ok(calls.some(c => c.url === '/api/code/sessions/9/stop' && c.method === 'POST'));
  // The live stream connects: the AI's words appear as it types, then the rest of the turn.
  streamOn = true; streamLive = {v: 3, text: 'Typing **now**', thinking: 'weighing it', outputs: {}};
  await tick(1700);
  assert.ok(calls.some(c => /\/stream\?after=13$/.test(c.url)), 'the stream asks from the last step it has');
  assert.equal(f.querySelector('.typing .prose').textContent, 'Typing now▍'); assert.match(f.querySelector('.typing .think').textContent, /weighing it/);
  streamLive = {v: 2, text: '', thinking: '', outputs: {}};             // an older snapshot arriving late…
  await tick(1000);
  assert.equal(f.querySelector('.typing .prose').textContent, 'Typing now▍', '…never wipes the newer one');
  streamLive = {v: 4, text: '', thinking: '', outputs: {}};
  feed.push(E(14, 'text', {text: 'Added Focus.'}), E(15, 'checkpoint', {sha: 'a', prev: 'b', files: 2}),
            E(16, 'done', {status: 'done', summary: 'Added Focus.', seconds: 75, files: 2, total: 2, engine: 'claude', tokens: 12345}),
            E(17, 'review_started', {engine: 'chatgpt'}), E(18, 'review_step', {title: 'Read work.js'}),
            E(19, 'review', {engine: 'chatgpt', status: 'done', rating: 8, text: 'Rating: 8/10\nVerdict: Clean.\nProblems:\n- None found'}));
  session = {...session, working: false, tokens: 12345};
  await tick(1300);
  assert.equal(f.querySelector('.typing'), null, 'the typed words give way to the stored message');
  assert.ok(f.querySelector('.prose.final'), 'the last answer is the summary, not repeated');
  assert.match(f.querySelector('.card2.done-ok').textContent, /✓ Done1:15 · 2 files this step · 2 in total · 12.3k tokens · on your Claude plan/);
  assert.equal(f.querySelector('.card2 .ring10 text').textContent, '8');
  assert.equal(f.querySelector('.working'), null);
  assert.equal($('send').textContent, 'SendCtrl ⏎');
  assert.equal($('s-tokens').textContent, '12.3k tokens');
  // The side panel: changes, the review ring, Keep → main.
  assert.equal($('c-count').textContent, '2 · +13 −0');
  assert.equal(d.querySelector('#r-body .ring10 text').textContent, '8');
  assert.match($('r-body').textContent, /ChatGPT plan saysClean\./);
  assert.equal($('a-keep').textContent, '✓ Keep it → main'); assert.equal($('a-keep').disabled, false);
  // A changed file opens its full diff, with numbered lines.
  f.querySelector('.step.file .p').click(); await tick();
  assert.equal($('diff').open, true); assert.equal($('diff-path').textContent, 'work.js');
  assert.deepEqual([...d.querySelectorAll('#diff-body .dl')].map(r => r.className.split(' ')[1]), ['hunk', 'ctx', 'del', 'add', 'add']);
  assert.deepEqual([...d.querySelectorAll('#diff-body .dl.add .ln')].map(x => x.textContent), ['', '2', '', '3']);
  $('diff-next').click(); await tick(); assert.equal($('diff-path').textContent, 'new.css');
  $('diff').close();
  // !command runs in the terminal; its output lands in the feed and the Terminal tab.
  type('!ls -la'); key('Enter', {ctrlKey: true}); await tick();
  assert.deepEqual(last(/\/terminal$/).body, {command: 'ls -la'});
  feed.push(E(20, 'term', {command: 'ls -la', ref: 't1'}), E(21, 'term_done', {ref: 't1', exit_code: 0, output: 'total 0', seconds: 1}));
  await tick(900);
  const term = f.querySelector('.step.term');
  assert.match(term.textContent, /❯ls -la✓ 0:01/); assert.match(term.querySelector('pre').textContent, /total 0/);
  assert.match($('t-log').textContent, /❯ ls -latotal 0/);
  // /commands.
  type('/review'); key('Enter', {ctrlKey: true}); await tick();
  assert.ok(last(/\/review$/), '/review asks for a second opinion');
  // Files: the tree, a coloured viewer, and @ from the viewer.
  d.querySelector('[data-tab=files]').click(); await tick(); await tick();
  assert.equal($('f-list').children.length, 3);
  $('f-filter').value = 'css'; $('f-filter').dispatchEvent(new w.Event('input')); await tick();
  assert.equal($('f-list').children[0].title, 'dashboard/static/work.css');
  $('f-list').children[0].click(); await tick(); await tick();
  assert.equal($('viewer').open, true);
  assert.equal(d.querySelector('#viewer-body .tk-k').textContent, 'const');
  assert.equal(d.querySelector('#viewer-body .tk-c').textContent, '// hi');
  assert.equal(d.querySelector('#viewer-body .tk-s').textContent, '"s"');
  assert.equal(d.querySelector('#viewer-body .gutter').textContent, '1\n2');
  $('prompt').value = '';
  $('viewer-mention').click();
  assert.equal($('prompt').value, '@dashboard/static/work.css '); assert.equal($('viewer').open, false);
  $('prompt').value = '';
  // History: each checkpoint's diff, file by file.
  d.querySelector('[data-tab=history]').click(); await tick(); await tick();
  $('h-list').children[0].click(); await tick(); await tick();
  assert.equal($('diff-path').textContent, 'Checkpoint aaaaaaaa');
  assert.equal(d.querySelector('#diff-body .dl.file .tx').textContent, 'work.js');
  $('diff').close();
  // Ctrl+K: everything, by name.
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true})); await tick();
  assert.equal($('palette').open, true);
  $('palette-q').value = 'second op'; $('palette-q').dispatchEvent(new w.Event('input'));
  assert.equal($('palette-list').children[0].textContent, '⚖ Second opinion');
  const reviews = calls.filter(c => /\/review$/.test(c.url)).length;
  $('palette-q').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); await tick();
  assert.equal(calls.filter(c => /\/review$/.test(c.url)).length, reviews + 1); assert.equal($('palette').open, false);
  // Plan first, then Build it.
  $('plan-toggle').click(); assert.equal($('send').textContent, 'Plan itCtrl ⏎');
  type('Add a dark mode'); key('Enter', {ctrlKey: true}); await tick();
  assert.equal(last(/\/messages$/).body.plan, true);
  feed.push(E(22, 'you', {text: 'Add a dark mode', engine: 'chatgpt', mode: 'full', plan: true}), E(23, 'text', {text: '1. Add a toggle\n2. Test it'}),
            E(24, 'done', {status: 'done', summary: '1. Add a toggle\n2. Test it', seconds: 9, files: 0, total: 2, engine: 'chatgpt', plan: true}));
  await tick(900);
  const planCard = [...f.querySelectorAll('.card2')].at(-1);
  assert.match(planCard.textContent, /Plan ready/);
  [...planCard.querySelectorAll('button')].find(b => b.textContent === '▶ Build it').click(); await tick();
  assert.deepEqual([last(/\/messages$/).body.prompt, last(/\/messages$/).body.plan], ['Go ahead with that plan.', false]);
  // Keep: Esc (no answer) must never count as yes; only a real yes merges.
  $('a-keep').click(); await tick();
  assert.equal($('confirm').open, true); assert.match($('confirm-text').textContent, /merges 2 files into main of Apex\. Restart Apex/);
  $('confirm').close('cancel'); await tick();                          // Cancel
  $('confirm').returnValue = 'ok';                                     // an earlier dialog's "yes" still in there…
  $('a-keep').click(); await tick(); $('confirm').close(); await tick(); // …then Esc
  assert.equal(calls.filter(c => /\/keep$/.test(c.url)).length, 0, 'Esc and Cancel never keep');
  $('a-push').click(); await tick(); $('confirm').close('ok'); await tick(); await tick();
  assert.deepEqual(calls.filter(c => /\/keep$/.test(c.url)).map(c => c.body), [{push: true}]);
  await tick(); await tick();
  assert.equal($('a-keep').textContent, '✓ Kept'); assert.equal($('prompt').disabled, true);
  // No microphone API here: a clear message, not an error.
  $('mic').click(); await tick();
  assert.match($('toast').textContent, /can't record here/);
  // Back home.
  $('back').click(); await tick();
  assert.ok(d.body.classList.contains('view-home')); assert.equal(w.location.hash, '');
  console.log('PASS: greeting and plans, @ and / as you type, starting with a model and effort, polling then the live stream (typing as it writes), '
    + 'every feed step (agent text never HTML, plan in place, reads grouped, failed command open, edits with their diff inline), Allow once, Esc stops, '
    + 'done with tokens, the second opinion ring, the diff viewer, !commands and the terminal, /commands, the file tree and coloured viewer with @, '
    + 'history, Ctrl+K, Plan first then Build it, Keep only on a real yes, Keep & push, and back home.');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
