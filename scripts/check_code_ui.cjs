// The Code page (dashboard/static/code.js) against a stubbed API: the greeting
// and your plans; @ and / as you type; starting a session with a model and an
// effort; polling until the live stream connects, then steps and typing over the
// stream; every kind of step (reads grouped, commands with their result, edits
// with their diff inline, the plan, checkpoints, done, the second opinion's
// rating); nothing an agent writes ever running as HTML; Allow once; Esc stops;
// a Copy button on every code block, command, output and diff (with a fallback);
// !commands and the terminal; /commands; the file tree and coloured viewer;
// Ctrl+K; Plan first then Build it; history; Keep only on a real "yes" (never
// Esc), Keep & push; a resting plan never offered by default; what Apex knows
// about you: the card under your first message, the home chip and its dialog,
// Forget on a memory, and the palette entry; and rules said once: the chips under
// a correction, the Rules tab (on/off, edit, delete, add, earlier versions, rules
// for all code), a clash with another window keeping your text, /rule, Ctrl+K; and
// the proof card: what it said next to what Apex saw, checks that couldn't decide
// shown as unknown (never a tick), Prove it with pasted output kept as yours, exit 0
// counting as a pass, a stale pass, the goalpost and disagreement lines, the second
// opinion's independence and its invented citations in red, and Keep asking before
// keeping unproved work (Keep anyway), even when the proof changed after it was read;
// and every session teaching Apex: the week's proved count, the home page's track
// record in counts (or "not enough sessions yet"), the chosen plan's line in the hint,
// Throw away asking why (Esc throws nothing away), what Keep and Throw away wrote into
// Apex, and the Rules tab's decision log with History and Restore; and Apex's memory
// on tap: the plan asking it as a 🧠 step, and a memory it suggested waiting for your
// OK (this project's only), with Approve, Reject and Edit (your wording is saved, never
// wiped by a poll while you type); and away mode: a stopped command sent to your phone,
// answered there (Allow once or Don't allow) with a device token that can't read the
// rest, expired, answered or unknown links said plainly, and the same link at the PC.
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
let exitOk = 0;
// What tends to happen on the project (agent/code_brain.track_record), as the overview carries it.
const CLAUDE_LINE = {key: 'engine:claude', text: 'Claude plan here: kept 7 of 9, 6 with proof', n: 9, hits: 7, rate: 0.778};
let recordDoc = {lines: [{key: 'no_checks', text: 'thrown away 4 of 6 when no checks ran', n: 6, hits: 4, rate: 0.667}, CLAUDE_LINE],
  engines: {claude: CLAUDE_LINE}, decided: 15, note: 'Counted from 15 finished sessions in the last 90 days.'};
const overview = () => ({owner: 'Alex', projects: [{id: 1, name: 'Apex', path: 'C:\\Apex', checks: 'python -m pytest -q', branch: 'main', checks_exit_ok: exitOk}],
  sessions: [{id: 7, project_id: 1, title: 'Add Focus mode', engine: 'claude', mode: 'safe', status: 'ready', last_status: 'done', files_changed: 2, review_rating: 8, updated: now - 120, working: false},
             {id: 3, project_id: 1, title: 'Old try', engine: 'chatgpt', mode: 'safe', status: 'discarded', last_status: 'done', files_changed: 0, review_rating: null, updated: now - 9000, working: false}],
  plans, default_engine: 'claude', week: {sessions: 2, kept: 0, proved: 1, rating: 8, minutes: 3, credits: 0}, working: 0, record: {1: recordDoc}});
let session = {id: 7, project_id: 1, project: 'Apex', title: 'Add Focus mode', engine: 'claude', mode: 'safe', branch: 'apex/7-add-focus-mode', worktree: 'C:\\ApexWork\\code\\apex-7',
  status: 'ready', working: true, side: '', since: now - 75, summary: '', review_state: 'done', review_engine: 'chatgpt', review_rating: 8, review_text: 'Rating: 8/10\nVerdict: Clean.\nProblems:\n- None found',
  check_state: '', check_output: '', conflict: 0, kept_commit: '', changes: {files: [{path: 'work.js', plus: 11, minus: 0, change: 'update'}, {path: 'new.css', plus: 2, minus: 0, change: 'add'}], plus: 13, minus: 0}};
const E = (id, kind, extra = {}) => ({id, ts: now - 60 + id, kind, ...extra});
const BRAIN = [{kind: 'profile', ref: 'APEX_USER.md', text: 'Alex builds Apex, on Windows.'},
  {kind: 'rule', ref: 1, text: 'Never touch the voice files'}, {kind: 'memory', ref: 12, text: 'Alex wants type hints everywhere'},
  {kind: 'memory', ref: 15, text: 'Small functions, plain names'}];
let brainSources = BRAIN.slice(), forgotten = [];
let feed = [
  E(1, 'you', {text: 'Add Focus mode', engine: 'claude', mode: 'safe', brief: {chars: 240, sources: BRAIN}}),
  E(2, 'thinking', {text: 'Plan it'}),
  E(3, 'text', {text: 'I\'ll add **Focus**. <img src=x onerror="window.pwned=1"> `code`\n```sh\nnpm test\n```'}),
  E(4, 'todo', {items: [{text: 'Add button', done: false, active: true}, {text: 'Test', done: false, active: false}]}),
  E(5, 'tool', {tool: 'read', title: 'Read work.js', path: 'work.js', ref: 'r1'}),
  E(6, 'result', {ref: 'r1', ok: true, output: ''}),
  E(7, 'tool', {tool: 'read', title: 'Read work.css', path: 'work.css', ref: 'r2'}),
  E(8, 'tool', {tool: 'search', title: 'Searched for renderHeader', ref: 'g1'}),
  E(9, 'file', {path: 'work.js', change: 'update', plus: 1, minus: 0, ref: 'e1', diff: '@@ -4,1 +4,2 @@\n a\n+b'}),
  E(10, 'todo', {items: [{text: 'Add button', done: true, active: false}, {text: 'Test', done: false, active: true}]}),
  E(11, 'tool', {tool: 'command', title: 'python -m pytest -q', ref: 'b1'}),
  E(12, 'result', {ref: 'b1', ok: false, exit_code: 1, output: '1 failed'}),
  E(13, 'blocked', {title: 'Bash: rm -rf build', command: 'rm -rf build', allow_id: 'tok-blocked-0123456789'}),
];
// Rules (agent/code_brain.py): this project's, with a revision, and those for all code.
const CLASH = {detail: 'Rules changed in another window. Reload; your text is kept.'};
let ruleDoc = {items: [{text: 'Never touch the voice files', active: true}], revision: 1, global: [{id: 31, text: 'Always write type hints'}]};
let clashes = 0;
const ruleVersions = () => [{revision: ruleDoc.revision, updated: now, items: ruleDoc.items}, {revision: 1, updated: now - 7200, items: [{text: 'Never touch the voice files', active: true}]}];
function rulesApi(url, opts) {
  const body = opts.body ? JSON.parse(opts.body) : {};
  if (url === '/api/code/projects/1/rules/history') return Response.json({versions: ruleVersions()});
  if (url === '/api/code/projects/1/rules/restore') {
    assert.equal(body.current_revision, ruleDoc.revision, 'a restore names the version it replaces');
    ruleDoc = {...ruleDoc, items: ruleVersions().find(v => v.revision === body.revision).items, revision: ruleDoc.revision + 1};
    return Response.json(ruleDoc);
  }
  if (opts.method === 'POST' || opts.method === 'PUT') {
    if (clashes) { clashes--; ruleDoc = {...ruleDoc, revision: ruleDoc.revision + 1}; return Response.json(CLASH, {status: 409}); }
    if (body.scope === 'all') { ruleDoc = {...ruleDoc, global: [...ruleDoc.global, {id: 40 + ruleDoc.global.length, text: body.text}]}; return Response.json(ruleDoc); }
    assert.equal(body.revision, ruleDoc.revision, 'every save names the version it read');
    const items = opts.method === 'PUT' ? body.items : [...ruleDoc.items, {text: body.text, active: true}];
    ruleDoc = {...ruleDoc, items, revision: ruleDoc.revision + 1};
  }
  return Response.json(ruleDoc);
}
// The project's decision log (agent/code_brain.write_back writes it; the Rules tab reads it).
const LOG1 = '2026-10-07 kept "Add Focus" (2 files, abc123456789, checks passed, proof proved, review 8/10 by ChatGPT plan): Added Focus.';
const LOG2 = '2026-10-08 threw away "Old try" (it was wrong)';
let logDoc = {decisions: `${LOG1}\n${LOG2}`, revision: 2, updated: now};
function logApi(url, opts) {
  if (url === '/api/code/projects/1/decisions/history') return Response.json({versions: [{revision: logDoc.revision, updated: now, decisions: logDoc.decisions},
    {revision: 1, updated: now - 7200, decisions: LOG1}]});
  if (url === '/api/code/projects/1/decisions/restore') {
    assert.equal(JSON.parse(opts.body).current_revision, logDoc.revision, 'a restore names the version it replaces');
    logDoc = {decisions: LOG1, revision: logDoc.revision + 1, updated: now};
  }
  return Response.json(logDoc);
}
// The proof (agent/code_studio.proof): what it said, what Apex saw, and the verdict.
const PROOF = (extra = {}) => ({verdict: 'unverified', engine: 'claude', head: 'c'.repeat(40),
  reasons: ["Apex hasn't run the checks on this change yet.", "Its own test run showed 3 passed: its run, not Apex's checks."],
  claims: [{sentence: 'Added Focus and ran the tests.', pass_claim: false}],
  saw_agent: [{by: 'agent', engine: 'claude', command: 'python -m pytest -q', ok: true, verdict: true, evidence: '3 passed', checkpoint: 1, stale: false}],
  saw_you: [], owner_evidence: null, goalpost: [],
  checks: {state: 'none', why: '', sha: '', checkpoint: null, command: 'python -m pytest -q', stale: null, changed_since: []},
  review: {engine: 'chatgpt', rating: 8, independence: 'independent', label: 'independent: other plan', disagreement: '',
    citations: [{cite: 'work.js:4', file: 'work.js', line: 4, kind: 'in', label: 'in the change'},
                {cite: 'nope.py:9', file: 'nope.py', line: 9, kind: 'invented', label: 'file not found: possibly invented'}]},
  ...extra});
let proofDoc = PROOF(), keepClash = 0;
// Memories a session suggested through Apex's memory server (agent/code_brain.suggested), waiting for the owner's OK.
let suggestions = [];
// Away mode (agent/code_studio.pending_allow / answer_allow): a command Safe mode stopped, asked on your phone.
const ASK = (extra = {}) => ({command: 'npm install sharp', title: 'Fix login', project: 'Apex', engine: 'claude', expires: now + 3600,
  answered: null, choice: '', ...extra});
function askApi(asks, token, opts) {
  const a = asks[token];
  if (!a) return Response.json({detail: "Apex doesn't know that request."}, {status: 404});
  if (opts.method !== 'POST') return Response.json(a);
  const {choice} = JSON.parse(opts.body);
  if (a.busy) { a.busy = false; return Response.json({detail: 'Apex is still working on your last message. Wait, or press Stop.'}, {status: 400}); }
  if (a.answered || a.expires < now) return Response.json({detail: 'Already answered or expired.'}, {status: 400});
  Object.assign(a, {answered: now, choice});
  return Response.json(a);
}
const pcAsks = {'tok-pc-0123456789abcdef': ASK({command: 'rm -rf build', title: 'Add Focus mode'})};
const calls = [];
let streamOn = false, streamLive = null;
const FILES = ['README.md', 'dashboard/static/work.js', 'dashboard/static/work.css'];
w.fetch = async (url, opts = {}) => {
  calls.push({url, method: opts.method || 'GET', body: opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : null});
  if (url === '/api/code') return Response.json(overview());
  if (url === '/api/code/projects/1/brain') { const sources = brainSources.filter(x => !forgotten.includes(x.ref)); return Response.json({text: sources.length ? 'What Apex knows…' : '', sources, chars: 99}); }
  if (url.startsWith('/api/code/projects/1/rules')) return rulesApi(url, opts);
  if (url.startsWith('/api/code/projects/1/decisions')) return logApi(url, opts);
  if (url === '/api/code/approvals') return Response.json({items: suggestions});
  const ask = url.match(/^\/api\/code\/allow\/([\w-]+)$/);
  if (ask) return askApi(pcAsks, ask[1], opts);
  const sw = url.match(/^\/api\/staged-writes\/(\d+)\/(approve|reject)$/);
  if (sw && opts.method === 'POST') {
    suggestions = suggestions.filter(x => x.id !== Number(sw[1]));
    return Response.json({ok: true, result: sw[2] === 'approve' ? `Approved #${sw[1]}: Remembered [#77 preference importance=5]: it` : `Staged write #${sw[1]} rejected.`});
  }
  if (url === '/api/memories' && opts.method === 'POST') return Response.json({result: 'Remembered [#78 decision importance=5]: it'});
  let mm = url.match(/^\/api\/memories\/(\d+)$/);
  if (mm && opts.method === 'DELETE') {
    forgotten.push(Number(mm[1])); ruleDoc = {...ruleDoc, global: ruleDoc.global.filter(g => g.id !== Number(mm[1]))};
    return Response.json({result: `Forgot memory #${mm[1]}`});
  }
  if (url === '/api/code/sessions' && opts.method === 'POST') return Response.json({...session, id: 9});
  if (/^\/api\/code\/sessions\/\d+\/proof$/.test(url)) return Response.json(proofDoc);
  if (/^\/api\/code\/sessions\/\d+\/evidence$/.test(url) && opts.method === 'POST') {
    const {output} = JSON.parse(opts.body);
    proofDoc = {...proofDoc, owner_evidence: {source: 'owner-reported', verdict: /passed/.test(output), evidence: output, sha: 'c'.repeat(40), stale: false},
      reasons: [...proofDoc.reasons, `You reported ${output}: noted, but Apex didn't see it run.`]};
    return Response.json(proofDoc);
  }
  if (url === '/api/code/projects/1' && opts.method === 'PATCH') { exitOk = JSON.parse(opts.body).exit_ok ? 1 : 0; return Response.json(overview().projects[0]); }
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
    if (m[2] === 'keep') {
      const body = JSON.parse(opts.body || '{}');
      if (keepClash && !body.unverified_ok) {                          // the proof changed after the page read it
        keepClash--; return Response.json({detail: 'Not proved: 1 file changed since.', proof: PROOF({reasons: ['The checks passed on checkpoint 5, but 1 file changed since: late.py.']})}, {status: 409});
      }
      session = {...session, status: 'kept', kept_commit: 'abc1234567'}; return Response.json(session);
    }
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
const lastPut = () => calls.filter(c => c.url === '/api/code/projects/1/rules' && c.method === 'PUT').at(-1);
const current9 = () => w.location.hash === '#allow=tok-pc-0123456789abcdef' && d.body.classList.contains('view-session');

// Away mode on a phone (/code#allow=…): its own device token can't read Apex Code (403),
// yet the link in a notification asks, and answers, the one question.
async function phone() {
  const OWNER_ONLY = {detail: 'Apex Code is for the owner only (master dashboard token).'};
  const asks = {'tok-phone-0123456789abc': ASK(), 'tok-busy-0123456789abcd': ASK({busy: true}),
    'tok-old-0123456789abcdef': ASK({expires: now - 60}), 'tok-done-0123456789abcd': ASK({answered: now - 30, choice: 'pc'})};
  const pd = new JSDOM(fs.readFileSync(path.join(base, 'code.html'), 'utf8'),
    {url: 'http://localhost:7860/code#allow=tok-phone-0123456789abc', runScripts: 'outside-only', pretendToBeVisual: true});
  const pw = pd.window, P = id => pw.document.getElementById(id), seen = [];
  pw.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  pw.HTMLDialogElement.prototype.close = function (v) { if (v !== undefined) this.returnValue = v; this.open = false; this.dispatchEvent(new pw.Event('close')); };
  pw.scrollTo = () => {}; pw.CSS = pw.CSS || {escape: x => String(x)};
  pw.localStorage.setItem('apex_token', 'a-device-token');
  pw.fetch = async (url, opts = {}) => {
    seen.push({url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null, auth: (opts.headers || {}).Authorization});
    const m = url.match(/^\/api\/code\/allow\/([\w-]+)$/);
    if (m) return askApi(asks, m[1], opts);
    return Response.json(OWNER_ONLY, {status: 403});                    // every other Code route is the owner's
  };
  pw.Response = Response;
  pw.eval(fs.readFileSync(path.join(base, 'theme.js'), 'utf8'));
  pw.eval(fs.readFileSync(path.join(base, 'code.js'), 'utf8'));
  await tick(); await tick(); await tick();
  assert.equal(seen[0].url, '/api/code/allow/tok-phone-0123456789abc', 'the question is read before the overview');
  assert.equal(seen[0].auth, 'Bearer a-device-token');
  assert.ok(seen.some(c => c.url === '/api/code'), 'the overview is still tried (the owner may be on a phone)');
  assert.equal(P('allow-dialog').open, true);
  assert.equal(P('allow-text').textContent, 'Your Claude plan wants to run npm install sharp in "Fix login" (Apex).');
  assert.equal(P('allow-text').querySelector('code').textContent, 'npm install sharp');
  assert.match(P('allow-when').textContent, /^Answer by .+\. Always allow is only on the Code page at your PC\.$/);
  assert.deepEqual([...P('allow-acts').querySelectorAll('button')].map(b => b.textContent), ["Don't allow", 'Allow once']);
  assert.equal(P('allow-acts').hidden, false); assert.equal(P('allow-done').hidden, true);
  assert.ok(P('toast').hidden && !/owner/.test(P('toast').textContent), 'the owner-only message is not shown here');
  P('allow-once').click(); await tick(); await tick();
  assert.deepEqual(seen.filter(c => c.method === 'POST').map(c => [c.url, c.body]), [['/api/code/allow/tok-phone-0123456789abc', {choice: 'once'}]]);
  assert.equal(P('allow-result').textContent, 'Done: it carries on.'); assert.equal(P('allow-result').className, 'allow-result good');
  assert.equal(P('allow-acts').hidden, true); assert.equal(P('allow-done').hidden, false);
  P('allow-close').click();
  assert.equal(P('allow-dialog').open, false); assert.equal(pw.location.hash, '', 'closing it forgets the link');
  // Still working at the PC: the error, and the link still works, so the buttons come back.
  pw.location.hash = '#allow=tok-busy-0123456789abcd'; await tick(); await tick();
  assert.equal(P('allow-dialog').open, true);
  P('allow-once').click(); await tick(); await tick(); await tick();
  assert.equal(P('allow-result').textContent, 'Apex is still working on your last message. Wait, or press Stop.');
  assert.equal(P('allow-result').className, 'allow-result bad'); assert.equal(P('allow-acts').hidden, false, 'it can be tried again');
  P('allow-once').click(); await tick(); await tick();
  assert.equal(P('allow-result').textContent, 'Done: it carries on.');
  // Expired, answered at the PC, or unknown: said plainly, and nothing to tap.
  pw.location.hash = '#allow=tok-old-0123456789abcdef'; await tick(); await tick();
  assert.match(P('allow-when').textContent, /^This request expired at .+\. Answer it on the Code page at your PC\.$/);
  assert.equal(P('allow-acts').hidden, true); assert.equal(P('allow-done').hidden, false); assert.equal(P('allow-result').hidden, true);
  pw.location.hash = '#allow=tok-done-0123456789abcd'; await tick(); await tick();
  assert.equal(P('allow-when').textContent, 'Already answered on the Code page at your PC.'); assert.equal(P('allow-acts').hidden, true);
  pw.location.hash = '#allow=tok-gone-0123456789abcd'; await tick(); await tick();
  assert.equal(P('allow-text').textContent, "Apex doesn't know this request. Open Apex Code at your PC to see the session.");
  assert.equal(P('allow-acts').hidden, true); assert.equal(P('allow-done').hidden, false);
  assert.equal(P('feed').children.length, 0, 'nothing else of Apex Code is shown');
  assert.equal(P('session-list').children.length, 0);
  pw.close();
}

(async () => {
  await tick(); await tick();
  // Home: Alex, both plans, the week, quick starts, recent work.
  assert.match($('greeting').textContent, /, Alex\.$/);
  assert.equal($('greeting').querySelector('.name').textContent, 'Alex');
  assert.match($('greet-sub').textContent, /Both plans are ready/);
  assert.deepEqual([...d.querySelectorAll('.plan b')].map(b => b.textContent), ['Claude', 'ChatGPT']);
  assert.match($('week').textContent, /\$0API credits/);
  // What tends to happen here, in counts: the week's proved, the track record strip, and the chosen plan's line.
  assert.match($('week').textContent, /0kept · 1 proved/);
  assert.equal($('record').hidden, false); assert.equal($('record-title').textContent, 'On Apex');
  assert.deepEqual([...$('record-lines').querySelectorAll('.rl')].map(r => r.textContent),
    ['▲Thrown away 4 of 6 when no checks ran', '◆Claude plan here: kept 7 of 9, 6 with proof']);
  assert.equal($('hint').textContent, 'Every session works on its own branch. Nothing touches your project until you press Keep. '
    + 'Claude plan here: kept 7 of 9, 6 with proof.');
  d.querySelector('[data-engine=chatgpt]').click();
  assert.equal($('hint').textContent, 'Every session works on its own branch. Nothing touches your project until you press Keep.', 'no line for a plan without enough sessions');
  d.querySelector('[data-engine=claude]').click();
  assert.equal(d.querySelectorAll('.card').length, 2);
  assert.ok(d.querySelector('.card .ring10'), 'a rated session shows its score');
  assert.ok($('home-slot').contains($('brief-form')), 'the brief sits on the home page');
  assert.ok(d.body.classList.contains('term'), 'the Terminal look by default');
  // What Apex knows about you, for this project: a chip, and a dialog with Forget on each memory.
  assert.equal($('brain-chip').hidden, false); assert.equal($('brain-chip').textContent, '🧠Apex knows 4 things for Apex');
  $('brain-chip').click(); await tick(); await tick();
  assert.equal($('brain-dialog').open, true);
  assert.deepEqual([...$('brain-body').querySelectorAll('.bh')].map(h => h.firstChild.textContent), ['About you', 'Your rules for this project', 'How you like your code']);
  assert.ok([...$('brain-body').querySelectorAll('.bh button')].some(b => b.textContent === 'Edit rules'), 'rules can be edited');
  assert.equal($('brain-body').querySelectorAll('.forget').length, 2, 'only memories can be forgotten here');
  $('brain-body').querySelector('.bl.k-memory .forget').click(); await tick(); await tick();
  assert.ok(calls.some(c => c.url === '/api/memories/12' && c.method === 'DELETE'), 'Forget deletes the memory');
  assert.ok($('brain-body').querySelector('.bl.k-memory').classList.contains('gone')); assert.equal($('toast').textContent, "Forgotten. Later sessions won't hear it.");
  assert.equal($('brain-chip').textContent, '🧠Apex knows 3 things for Apex', 'the chip counts again');
  $('brain-close').click(); assert.equal($('brain-dialog').open, false);
  brainSources = []; $('brain-chip').click(); await tick(); await tick();
  assert.equal($('brain-body').textContent, 'Tell Celine or Apex chat how you like your code; it shows up here.');
  $('brain-dialog').close(); brainSources = BRAIN.slice(); forgotten = [];
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
  assert.equal(f.querySelector('.step.blocked .sent-phone').textContent, '📱 Sent to your phone', 'Apex asked on your phone too');
  assert.equal($('box-waiting').hidden, true, 'nothing waiting for your OK: no box');
  assert.ok(calls.some(c => c.url === '/api/code/approvals'), 'what is waiting is read with the overview');
  // Under your first message: what Apex told the plan about you, folded, grouped by where it came from.
  const told = f.querySelector('details.brain');
  assert.ok(told && told.previousElementSibling.classList.contains('you') && !told.open, 'a folded card under the message');
  assert.equal(told.querySelector('summary').textContent, '🧠 What Apex told your Claude plan about you · 4 things');
  assert.deepEqual([...told.querySelectorAll('.bl')].map(r => r.querySelector('.tag') ? r.querySelector('.tag').textContent : ''), ['', 'rule 1', '#12', '#15']);
  [...told.querySelectorAll('.bl.k-memory')][1].querySelector('.forget').click(); await tick();
  assert.ok(calls.some(c => c.url === '/api/memories/15' && c.method === 'DELETE'));
  assert.ok([...told.querySelectorAll('.bl.k-memory')][1].classList.contains('gone') && [...told.querySelectorAll('.bl.k-memory')][1].querySelector('.forget').disabled);
  assert.equal($('toast').textContent, "Forgotten. Later sessions won't hear it.");
  assert.equal(told.querySelectorAll('.step, .prose, .copy').length, 0, 'the card is not a step of the work');
  assert.match(f.querySelector('.working').textContent, /Apex is working · Claude plan · 1:1\d/, 'the clock counts from when you sent it');
  assert.equal($('send').textContent, '■ Stop');
  // Copy buttons: every code block, command, output and diff copies its own text and says "Copied".
  const copied = [];
  Object.defineProperty(w.navigator, 'clipboard', {configurable: true, value: {writeText: async t => { copied.push(t); }}});
  const copyOf = (root) => { const b = root.querySelector('button.copy'); assert.ok(b, 'a Copy button is there'); assert.equal(b.textContent, 'Copy'); return b; };
  const clickCopy = async (b) => { b.click(); await tick(10); };
  let b = copyOf(f.querySelector('.prose .cw'));
  await clickCopy(b); assert.equal(copied.at(-1), 'npm test', 'a code block copies just its code'); assert.equal(b.textContent, 'Copied');
  assert.equal(f.querySelector('.prose .cw pre').textContent, 'npm test', 'the button is not part of the code');
  b = copyOf(cmd.querySelector('.line')); await clickCopy(b); assert.equal(copied.at(-1), 'python -m pytest -q', 'a command copies the command');
  b = copyOf(cmd.querySelector('details')); await clickCopy(b); assert.equal(copied.at(-1), '1 failed', 'its output copies the output');
  b = copyOf(f.querySelector('.step.file .cw')); await clickCopy(b); assert.equal(copied.at(-1), '@@ -4,1 +4,2 @@\n a\n+b', 'a diff copies the raw diff');
  assert.equal(b.parentNode.firstChild, b, 'a diff’s button has its own row above it, never over its first line');
  assert.equal(f.querySelector('.prose .cw').firstChild.tagName, 'PRE', 'a code block’s button floats in the corner of the pre');
  await tick(1700); assert.equal(b.textContent, 'Copy', 'Copied fades back to Copy');
  // No clipboard API (or it refuses): a hidden textarea and execCommand('copy').
  Object.defineProperty(w.navigator, 'clipboard', {configurable: true, value: undefined});
  let viaExec = null; d.execCommand = c => { viaExec = {c, text: d.querySelector('textarea[readonly]').value}; return true; };
  b = copyOf(cmd.querySelector('.line')); b.focus(); await clickCopy(b);
  assert.equal(d.activeElement, b, 'focus returns to the Copy button after the fallback');
  assert.deepEqual(viaExec, {c: 'copy', text: 'python -m pytest -q'}); assert.equal(b.textContent, 'Copied'); assert.equal(d.querySelector('textarea[readonly]'), null, 'the helper textarea is cleaned up');
  d.execCommand = () => false; await clickCopy(b); assert.equal(b.textContent, 'Copy failed', 'a refused copy says so');
  // The API is there but refuses (insecure page, no focus): fall back to the textarea rather than fail.
  Object.defineProperty(w.navigator, 'clipboard', {configurable: true, value: {writeText: async () => { throw new Error('denied'); }}});
  viaExec = null; d.execCommand = c => { viaExec = {c, text: d.querySelector('textarea[readonly]').value}; return true; };
  await clickCopy(b); assert.deepEqual(viaExec, {c: 'copy', text: 'python -m pytest -q'}, 'a refused writeText falls back'); assert.equal(b.textContent, 'Copied');
  // Allow once, like Claude Code's prompt.
  [...f.querySelectorAll('.step.blocked button')].find(b => b.textContent === 'Allow once').click(); await tick();
  assert.deepEqual(last(/\/allow$/).body, {command: 'rm -rf build', always: false});
  // Away mode at the PC: a notification tapped while the page is open asks the same one question.
  w.location.hash = '#allow=tok-pc-0123456789abcdef'; await tick(); await tick();
  assert.equal($('allow-dialog').open, true, 'the link opens the question');
  assert.equal($('allow-text').textContent, 'Your Claude plan wants to run rm -rf build in "Add Focus mode" (Apex).');
  assert.equal($('allow-acts').hidden, false); assert.equal(current9(), true, 'the session stays open behind it');
  $('allow-no').click(); await tick(); await tick();
  assert.deepEqual(last(/\/api\/code\/allow\//).body, {choice: 'no'});
  assert.equal($('allow-result').textContent, 'Done: it will find another way, or stop and explain.');
  assert.equal($('allow-acts').hidden, true); assert.equal($('allow-done').hidden, false);
  $('allow-close').click(); assert.equal($('allow-dialog').open, false);
  assert.equal(w.location.hash, '#s=9', 'closing it goes back to the session');
  // Esc stops a running task.
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await tick();
  assert.ok(calls.some(c => c.url === '/api/code/sessions/9/stop' && c.method === 'POST'));
  // The live stream connects: the AI's words appear as it types, then the rest of the turn.
  streamOn = true; streamLive = {v: 3, text: 'Typing **now**', thinking: 'weighing it', outputs: {b1: 'line 1\nline 2'}};
  await tick(1700);
  assert.ok(calls.some(c => /\/stream\?after=13$/.test(c.url)), 'the stream asks from the last step it has');
  assert.equal(f.querySelector('.typing .prose').textContent, 'Typing now▍'); assert.match(f.querySelector('.typing .think').textContent, /weighing it/);
  // Live command output sits in a Copy wrapper; the height cap stays on the pre, and Copy gives just the output.
  Object.defineProperty(w.navigator, 'clipboard', {configurable: true, value: {writeText: async t => { copied.push(t); }}});
  const lo = f.querySelector('.live-out');
  assert.ok(lo.classList.contains('cw') && lo.firstChild.tagName === 'PRE' && lo.firstChild.classList.contains('out'), 'live output: a pre inside the Copy wrapper');
  assert.equal(lo.firstChild.textContent, 'line 1\nline 2');
  await clickCopy(copyOf(lo)); assert.equal(copied.at(-1), 'line 1\nline 2');
  // Words still being typed have no Copy buttons (they'd be rebuilt on every snapshot); they appear once the message is whole.
  streamLive = {...streamLive, v: 5, text: 'Typing **now**\n```sh\nls\n```'};
  await tick(1000);
  assert.ok(f.querySelector('.typing .prose pre'), 'a finished code fence still being typed shows as code'); assert.equal(f.querySelectorAll('.typing .copy').length, 0, '…without a Copy button yet');
  assert.equal(f.querySelectorAll('.typing .cursor').length, 1);
  streamLive = {...streamLive, v: 7, text: 'Typing **now**'};
  await tick(1000);
  streamLive = {v: 2, text: '', thinking: '', outputs: {}};             // an older snapshot arriving late…
  await tick(1000);
  assert.equal(f.querySelector('.typing .prose').textContent, 'Typing now▍', '…never wipes the newer one');
  streamLive = {v: 8, text: '', thinking: '', outputs: {}};
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
  // The proof card: what it said, word for word, next to what Apex saw; nothing proved yet.
  assert.equal($('box-proof').hidden, false);
  assert.equal($('box-proof').previousElementSibling.id, 'box-checks'); assert.equal($('box-proof').nextElementSibling.id, 'box-actions');
  assert.equal($('p-badge').textContent, '? Unverified'); assert.equal($('p-badge').className, 'p-badge warn');
  assert.deepEqual([...$('p-reasons').children].map(li => li.textContent), proofDoc.reasons);
  assert.deepEqual([...$('p-said').querySelectorAll('q')].map(q => q.textContent), ['Added Focus and ran the tests.']);
  assert.equal($('p-saw').textContent, 'It ran (Claude plan)python -m pytest -q✓ 3 passed · step 1');
  assert.equal($('p-prove').hidden, true, 'Prove it only when the checks could not decide');
  // The second opinion: how independent it is, and each file:line it cites, the invented one in red.
  assert.equal($('r-body').querySelector('.indep').textContent, 'independent: other plan');
  assert.deepEqual([...$('r-body').querySelectorAll('details.full .cite')].map(c => [c.className, c.textContent]),
    [['cite in', 'work.js:4in the change'], ['cite invented', 'nope.py:9file not found: possibly invented']]);
  assert.equal($('a-keep').textContent, '✓ Keep it → main'); assert.equal($('a-keep').disabled, false);
  // A changed file opens its full diff, with numbered lines.
  f.querySelector('.step.file .p').click(); await tick();
  assert.equal($('diff').open, true); assert.equal($('diff-path').textContent, 'work.js');
  assert.deepEqual([...d.querySelectorAll('#diff-body .dl')].map(r => r.className.split(' ')[1]), ['hunk', 'ctx', 'del', 'add', 'add']);
  assert.deepEqual([...d.querySelectorAll('#diff-body .dl.add .ln')].map(x => x.textContent), ['', '2', '', '3']);
  await clickCopy(copyOf($('diff-body'))); assert.equal(copied.at(-1), '@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d', 'the diff viewer copies the diff');
  $('diff-next').click(); await tick(); assert.equal($('diff-path').textContent, 'new.css');
  $('diff').close();
  // !command runs in the terminal; its output lands in the feed and the Terminal tab.
  type('!ls -la'); key('Enter', {ctrlKey: true}); await tick();
  assert.deepEqual(last(/\/terminal$/).body, {command: 'ls -la'});
  feed.push(E(20, 'term', {command: 'ls -la', ref: 't1'}), E(21, 'term_done', {ref: 't1', exit_code: 0, output: 'total 0', seconds: 1}),
            E(22, 'file', {path: 'big.js', change: 'update', plus: 30, minus: 0, ref: 'e2', diff: '@@ -1,1 +1,31 @@\n' + Array.from({length: 30}, (_, i) => '+l' + i).join('\n')}),
            E(23, 'checks', {command: 'pytest -q', passed: false, seconds: 3, output: 'F'}));
  await tick(900);
  const term = f.querySelector('.step.term');
  assert.match(term.textContent, /❯ls -la✓ 0:01/); assert.match(term.querySelector('pre').textContent, /total 0/);
  assert.match($('t-log').textContent, /❯ ls -latotal 0/);
  // Copy on the !command line and its output, on the Terminal tab (output only, not the exit line), on a folded diff, and on checks.
  await clickCopy(copyOf(term.querySelector('.line'))); assert.equal(copied.at(-1), 'ls -la');
  await clickCopy(copyOf(term.querySelector('details'))); assert.equal(copied.at(-1), 'total 0');
  assert.match($('t-log').querySelector('pre').textContent, /\[exit 0/);
  await clickCopy(copyOf($('t-log'))); assert.equal(copied.at(-1), 'total 0', 'the Terminal tab copies the output alone');
  const big = [...f.querySelectorAll('.step.file')].at(-1);
  assert.ok(big.querySelector('.cw > .inline-diff.folded') && big.querySelector('.more-lines') && !big.querySelector('.cw .more-lines'), 'a long diff stays folded, Copy outside the fold');
  await clickCopy(copyOf(big)); assert.match(copied.at(-1), /^@@ -1,1 \+1,31 @@\n\+l0\n/); assert.equal(copied.at(-1).split('\n').length, 31, 'a folded diff copies all of it');
  const chk = [...f.querySelectorAll('.step.cmd')].find(x => /Checks: pytest/.test(x.textContent));
  await clickCopy(copyOf(chk.querySelector('.line'))); assert.equal(copied.at(-1), 'pytest -q');
  await clickCopy(copyOf(chk.querySelector('details'))); assert.equal(copied.at(-1), 'F');
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
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true})); await tick();
  $('palette-q').value = 'what apex knows'; $('palette-q').dispatchEvent(new w.Event('input'));
  assert.equal($('palette-list').children[0].textContent, '🧠 What Apex knows about me');
  $('palette-q').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); await tick(); await tick();
  assert.equal($('brain-dialog').open, true); assert.match($('brain-sub').textContent, /new session in Apex starts out knowing/);
  assert.deepEqual([...$('brain-body').querySelectorAll('.bl .tag')].map(t => t.textContent), ['rule 1', '#12'], 'the memory forgotten from the feed is gone');
  $('brain-close').click();
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
  // Say it once: a reply that corrects the agent gets chips under it; one tap and an edit make it a rule.
  feed.push(E(25, 'you', {text: 'No, never touch the voice files or the wake word', engine: 'chatgpt', mode: 'full', correction: true}),
            E(26, 'done', {status: 'done', summary: 'Put them back.', seconds: 4, files: 0, total: 2, engine: 'chatgpt'}),
            E(27, 'you', {text: 'Now add the toggle', engine: 'chatgpt', mode: 'full'}),
            E(28, 'you', {text: 'Always add type hints', engine: 'chatgpt', mode: 'full', correction: true}),
            E(29, 'you', {text: 'Stop renaming things', engine: 'chatgpt', mode: 'full', correction: true}));
  await tick(900);
  const fixes = [...f.querySelectorAll('.fix')];
  assert.equal(fixes.length, 3, 'chips only under corrections');
  assert.ok(fixes[0].previousElementSibling.classList.contains('you'), 'right under the message');
  assert.deepEqual([...fixes[0].querySelectorAll('button')].map(b => b.textContent), ['Make this a rule for Apex', 'Remember for all my code', '✕']);
  fixes[0].querySelector('button').click();
  const area = fixes[0].querySelector('textarea');
  assert.equal(area.value, 'No, never touch the voice files or the wake word'); assert.equal(fixes[0].querySelector('.count').textContent, '48/500');
  area.value = 'Never touch the wake word'; area.dispatchEvent(new w.Event('input'));
  assert.equal(fixes[0].querySelector('.count').textContent, '25/500');
  [...fixes[0].querySelectorAll('button')].find(b => b.textContent === 'Save rule').click(); await tick(); await tick();
  assert.deepEqual(last(/\/projects\/1\/rules$/).body, {text: 'Never touch the wake word', scope: 'project', revision: 1});
  assert.equal($('toast').textContent, 'Rule saved. Every session here follows it; this one hears it on its next turn.');
  assert.match(fixes[0].textContent, /✓ Rule saved\./); assert.equal(fixes[0].querySelectorAll('button').length, 0);
  fixes[1].querySelectorAll('button')[1].click();                      // for all my code
  [...fixes[1].querySelectorAll('button')].find(b => b.textContent === 'Save rule').click(); await tick(); await tick();
  assert.deepEqual(last(/\/projects\/1\/rules$/).body, {text: 'Always add type hints', scope: 'all', revision: 2});
  assert.match($('toast').textContent, /^Rule saved for all your code\./);
  fixes[2].querySelector('.x').click(); assert.equal(fixes[2].isConnected, false, '✕ puts the chips away');
  // The Rules tab: on/off, edit, delete, add; a clash with another window reloads and keeps your words.
  d.querySelector('[data-tab=rules]').click(); await tick(); await tick();
  assert.equal(d.querySelector('[data-pane=rules]').hidden, false);
  // The project's decision log, newest first, read-only, with History and Restore.
  assert.deepEqual([...$('log-lines').querySelectorAll('.log-line')].map(x => x.textContent), [LOG2, LOG1]);
  assert.equal($('log-lines').querySelectorAll('button, input, textarea').length, 0, 'the log is read-only');
  $('log-history').open = true; $('log-history').dispatchEvent(new w.Event('toggle')); await tick(); await tick();
  const logVer = $('log-versions').querySelector('.version');
  assert.match(logVer.textContent, /^Version 1/); assert.equal(logVer.querySelectorAll('li').length, 1);
  [...logVer.querySelectorAll('button')].find(b => b.textContent === 'Restore').click(); await tick(); await tick();
  assert.deepEqual(last(/\/decisions\/restore$/).body, {revision: 1, current_revision: 2});
  assert.equal($('toast').textContent, 'Version 1 of the decision log is back.');
  assert.deepEqual([...$('log-lines').querySelectorAll('.log-line')].map(x => x.textContent), [LOG1]);
  $('log-history').open = false;
  assert.deepEqual([...$('rules-list').querySelectorAll('.rule .rt')].map(x => x.textContent), ['Never touch the voice files', 'Never touch the wake word']);
  assert.equal($('rules-count').textContent, '2 on · 2/20');
  assert.match($('rules-sub').textContent, /Every new session in Apex follows these/);
  assert.deepEqual([...$('rules-global').querySelectorAll('.rt')].map(x => x.textContent), ['Always write type hints', 'Always add type hints']);
  const box = $('rules-list').querySelector('.rule input[type=checkbox]');
  box.checked = false; box.dispatchEvent(new w.Event('change')); await tick(); await tick();
  assert.deepEqual(lastPut().body.items[0], {text: 'Never touch the voice files', active: false});
  assert.ok($('rules-list').querySelector('.rule').classList.contains('off'), 'a rule turned off shows it');
  clashes = 1;
  [...$('rules-list').children[1].querySelectorAll('button')].find(b => b.textContent === 'Edit').click();
  let editing = $('rules-list').querySelector('.rule.editing textarea');
  editing.value = 'Never touch the wake word file'; editing.dispatchEvent(new w.Event('input'));
  [...$('rules-list').querySelectorAll('.rule.editing button')].find(b => b.textContent === 'Save').click(); await tick(); await tick(); await tick();
  assert.equal($('toast').textContent, 'Rules changed in another window. Reload; your text is kept.');
  editing = $('rules-list').querySelector('.rule.editing textarea');
  assert.ok(editing && editing.value === 'Never touch the wake word file', 'after the reload, the edit is still open with your words');
  [...$('rules-list').querySelectorAll('.rule.editing button')].find(b => b.textContent === 'Save').click(); await tick(); await tick();
  assert.equal(lastPut().body.items[1].text, 'Never touch the wake word file');
  assert.equal($('rules-list').querySelector('.rule.editing'), null);
  [...$('rules-list').children[0].querySelectorAll('button')].find(b => b.textContent === 'Delete').click(); await tick(); await tick();
  assert.deepEqual(ruleDoc.items, [{text: 'Never touch the wake word file', active: true}]);
  $('rules-new').value = 'Use pathlib'; $('rules-add').dispatchEvent(new w.Event('submit', {cancelable: true})); await tick(); await tick();
  assert.equal(last(/\/projects\/1\/rules$/).body.text, 'Use pathlib'); assert.equal($('rules-new').value, '');
  clashes = 1; $('rules-new').value = 'Keep it small'; $('rules-add').dispatchEvent(new w.Event('submit', {cancelable: true})); await tick(); await tick();
  assert.equal($('rules-new').value, 'Keep it small', 'a clash keeps what you typed');
  $('rules-new').value = '';
  // Earlier versions, and Restore.
  $('rules-history').open = true; $('rules-history').dispatchEvent(new w.Event('toggle')); await tick(); await tick();
  const ver = $('rules-versions').querySelector('.version');
  assert.match(ver.textContent, /^Version 1/); assert.equal(ver.querySelectorAll('li').length, 1);
  [...ver.querySelectorAll('button')].find(b => b.textContent === 'Restore').click(); await tick(); await tick();
  assert.deepEqual(ruleDoc.items, [{text: 'Never touch the voice files', active: true}]); assert.equal($('toast').textContent, 'Version 1 is back.');
  assert.deepEqual([...$('rules-list').querySelectorAll('.rt')].map(x => x.textContent), ['Never touch the voice files']);
  // Rules for all code: Forget.
  $('rules-global').querySelector('.forget').click(); await tick(); await tick();
  assert.ok(calls.some(c => c.url === '/api/memories/31' && c.method === 'DELETE'));
  assert.deepEqual([...$('rules-global').querySelectorAll('.rt')].map(x => x.textContent), ['Always add type hints']);
  // /rule from the chat box, Ctrl+K, and the brief card's Edit rules link.
  type('/ru'); await tick(); assert.match($('suggest').textContent, /\/ruleMake a rule for this project/); key('Escape');
  type('/rule Never print a key'); key('Enter', {ctrlKey: true}); await tick(); await tick();
  assert.equal(last(/\/projects\/1\/rules$/).body.text, 'Never print a key'); assert.equal($('prompt').value, '');
  d.querySelector('[data-tab=changes]').click();
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true})); await tick();
  $('palette-q').value = 'rules for'; $('palette-q').dispatchEvent(new w.Event('input'));
  assert.equal($('palette-list').children[0].textContent, '📏 Rules for this project');
  $('palette-q').dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); await tick(); await tick();
  assert.equal(d.querySelector('[data-pane=rules]').hidden, false);
  d.querySelector('[data-tab=changes]').click();
  [...told.querySelectorAll('button')].find(b => b.textContent === 'Edit rules').click(); await tick();
  assert.equal(d.querySelector('[data-pane=rules]').hidden, false, 'the brief card opens the Rules tab');
  d.querySelector('[data-tab=changes]').click();
  // Checks that couldn't decide: '? unknown' in the feed and the panel, never a tick, and a Prove it block.
  const UNKNOWN = 'Exit 0, but no test count in the output: maybe no tests ran.';
  session = {...session, check_state: 'unknown', check_evidence: UNKNOWN};
  proofDoc = PROOF({checks: {state: 'unknown', why: UNKNOWN, sha: 'b'.repeat(40), checkpoint: 2, command: 'python -m pytest -q', stale: false, changed_since: []},
    reasons: [`The checks couldn't decide: ${UNKNOWN}`]});
  feed.push(E(30, 'checks', {command: 'python -m pytest -q', state: 'unknown', passed: false, why: UNKNOWN, seconds: 4, output: 'collected 0 items'}));
  await tick(900);
  const unk = [...f.querySelectorAll('.step.cmd')].at(-1);
  assert.equal(unk.querySelector('.st').textContent, '? unknown · 0:04'); assert.equal(unk.querySelector('.st').className, 'st warn');
  assert.equal(unk.querySelector('.why').textContent, UNKNOWN); assert.equal(unk.querySelector('details').open, true);
  assert.equal($('k-state').textContent, `? Unknown: ${UNKNOWN}`); assert.equal($('k-state').className, 'k-state warn');
  assert.equal($('p-saw').querySelector('.saw').textContent, `Apex ranpython -m pytest -q? ${UNKNOWN} · checkpoint 2`);
  assert.equal($('p-saw').querySelector('.saw .mark').className, 'mark warn', 'unknown is a question mark, never a tick');
  assert.equal($('p-prove').hidden, false); assert.match($('p-where').textContent, /Run this yourself in C:\\ApexWork\\code\\apex-7/);
  await clickCopy(copyOf($('p-cmd'))); assert.equal(copied.at(-1), 'python -m pytest -q', 'Prove it copies the exact command');
  $('p-send').click(); await tick(); assert.equal($('toast').textContent, 'Paste what the command printed first.');
  $('p-paste').value = '5 passed'; $('p-send').click(); await tick(); await tick();
  assert.deepEqual(last(/\/evidence$/).body, {output: '5 passed'}); assert.equal($('p-paste').value, '');
  assert.equal($('toast').textContent, "Added as what you reported. Only Apex's own checks can prove it.");
  assert.equal([...$('p-saw').querySelectorAll('.saw')].at(-1).textContent, 'You reported✓ 5 passed · pasted, not seen by Apex');
  feed.push(E(31, 'owner_evidence', {source: 'owner-reported', verdict: true, evidence: '5 passed', sha: 'c'.repeat(40)})); await tick(900);
  assert.equal([...f.querySelectorAll('.step.mark')].at(-1).textContent, "You reported: 5 passed (pasted; Apex didn't see it run)");
  assert.equal($('p-badge').textContent, '? Unverified', 'what you paste never proves it');
  // Exit 0 counts as a pass: a setting of the project, saved at once.
  assert.equal($('k-exit').checked, false);
  $('k-exit').checked = true; $('k-exit').dispatchEvent(new w.Event('change')); await tick(); await tick();
  assert.deepEqual(calls.filter(c => c.url === '/api/code/projects/1' && c.method === 'PATCH').at(-1).body, {exit_ok: true});
  assert.match($('toast').textContent, /^Exit 0 now counts as a pass/); assert.equal($('k-exit').checked, true);
  // A pass on an older checkpoint, a change that edits tests, and a second opinion that disagrees.
  session = {...session, check_state: 'passed', check_evidence: '212 passed'};
  proofDoc = PROOF({checks: {state: 'passed', why: '212 passed', sha: 'd'.repeat(40), checkpoint: 4, command: 'python -m pytest -q', stale: true, changed_since: ['a.py', 'b.py']},
    reasons: ['The checks passed on checkpoint 4, but 2 files changed since: a.py, b.py. That result is stale: run them again.'], goalpost: ['tests/test_focus.py'],
    review: {...PROOF().review, rating: 4, disagreement: "Apex's checks passed, but the second opinion rates it 4/10: read what it found."}});
  feed.push(E(32, 'checks', {command: 'python -m pytest -q', state: 'passed', passed: true, why: '212 passed', seconds: 9, output: '212 passed'}));
  await tick(900);
  assert.equal($('k-state').textContent, '✓ Passed on an older checkpoint'); assert.equal($('k-state').className, 'k-state warn');
  assert.equal($('p-stale').textContent, '2 files changed since: a.py, b.py'); assert.equal($('p-stale').hidden, false);
  assert.match($('p-goalpost').textContent, /edits tests \(tests\/test_focus\.py\)/); assert.equal($('p-goalpost').hidden, false);
  assert.equal($('p-disagree').textContent, "⚖ Apex's checks passed, but the second opinion rates it 4/10: read what it found.");
  assert.ok($('p-saw').querySelector('.saw').classList.contains('old')); assert.match($('p-saw').querySelector('.saw').textContent, /212 passed · checkpoint 4 \(older\)$/);
  assert.equal($('p-prove').hidden, true);
  // Throw away asks why; Esc throws nothing away, and no reason is fine.
  $('a-discard').click(); await tick();
  assert.equal($('discard-dialog').open, true);
  const reasons = () => [...$('discard-reasons').querySelectorAll('.chip')];
  assert.deepEqual(reasons().map(b => b.textContent), ['Changed my mind', 'It was wrong', 'Poor quality', 'Something better came along']);
  reasons()[2].click(); $('discard-dialog').close(); await tick(); await tick();                     // Esc
  assert.equal(calls.filter(c => /\/discard$/.test(c.url)).length, 0, 'Esc never throws away');
  $('a-discard').click(); await tick();
  assert.ok(reasons().every(b => b.getAttribute('aria-checked') === 'false'), 'it opens with no reason chosen');
  reasons()[1].click(); assert.equal(reasons()[1].getAttribute('aria-checked'), 'true');
  reasons()[3].click(); assert.deepEqual(reasons().map(b => b.getAttribute('aria-checked')), ['false', 'false', 'false', 'true'], 'one reason at a time');
  reasons()[3].click(); assert.equal(reasons()[3].getAttribute('aria-checked'), 'false', 'a second tap takes it back');
  reasons()[1].click(); $('discard-dialog').close('ok'); await tick(); await tick();
  assert.deepEqual(last(/\/discard$/).body, {reason: 'wrong'}); assert.equal($('toast').textContent, 'Thrown away. Apex noted why.');
  $('a-discard').click(); await tick(); $('discard-dialog').close('ok'); await tick(); await tick();
  assert.deepEqual(last(/\/discard$/).body, {reason: ''}); assert.equal($('toast').textContent, 'Thrown away.');
  // Keep, unproved: it says why, and Cancel keeps nothing.
  $('a-keep').click(); await tick();
  assert.equal($('confirm-title').textContent, 'Keep it unproved?'); assert.equal($('confirm-ok').textContent, 'Keep anyway');
  assert.match($('confirm-text').textContent, /^\? Unverified\. The checks passed on checkpoint 4, but 2 files changed since: a\.py, b\.py\./);
  assert.match($('confirm-text').textContent, /merges 2 files into main of Apex\./);
  $('confirm').close('cancel'); await tick();
  // Proved: the usual question, with what proves it.
  session = {...session, check_evidence: '212 passed'};
  proofDoc = PROOF({verdict: 'proved', reasons: ['Apex ran python -m pytest -q on checkpoint 5: 212 passed.'],
    checks: {state: 'passed', why: '212 passed', sha: 'e'.repeat(40), checkpoint: 5, command: 'python -m pytest -q', stale: false, changed_since: []}});
  // Keep: Esc (no answer) must never count as yes; only a real yes merges.
  $('a-keep').click(); await tick();
  assert.equal($('confirm').open, true); assert.match($('confirm-text').textContent, /merges 2 files into main of Apex\. Restart Apex/);
  assert.equal($('confirm-title').textContent, 'Keep it?'); assert.match($('confirm-text').textContent, /✓ Proved: Apex ran python -m pytest -q on checkpoint 5: 212 passed\.$/);
  assert.equal($('p-badge').textContent, '✓ Proved'); assert.equal($('p-badge').className, 'p-badge ok');
  $('confirm').close('cancel'); await tick();                          // Cancel
  $('confirm').returnValue = 'ok';                                     // an earlier dialog's "yes" still in there…
  $('a-keep').click(); await tick(); $('confirm').close(); await tick(); // …then Esc
  assert.equal(calls.filter(c => /\/keep$/.test(c.url)).length, 0, 'Esc and Cancel never keep');
  recordDoc = {lines: [], engines: {}, decided: 3, note: 'Not enough sessions yet (3 of 5)'};      // what the next overview says
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
  assert.equal($('record-lines').textContent, 'Not enough sessions yet (3 of 5)', 'below 5 sessions: the note, never a rate');
  assert.ok(!/%/.test($('record').textContent));
  // Keep anyway: unproved work is kept only on a real yes, and the page says it knows.
  session = {...session, status: 'ready', kept_commit: ''}; proofDoc = PROOF();
  w.location.hash = '#s=9'; await tick(300); await tick(300);
  assert.ok(d.body.classList.contains('view-session')); assert.equal($('a-keep').disabled, false);
  $('a-keep').click(); await tick();
  assert.equal($('confirm-title').textContent, 'Keep it unproved?');
  $('confirm').close('ok'); await tick(); await tick();
  assert.deepEqual(calls.filter(c => /\/keep$/.test(c.url)).at(-1).body, {unverified_ok: true});
  // The proof changed after the page read it: the server's 409 brings the new one, and it asks again.
  session = {...session, status: 'ready', kept_commit: ''}; proofDoc = PROOF({verdict: 'proved', reasons: ['Apex ran python -m pytest -q on checkpoint 5: 212 passed.']});
  feed.push(E(33, 'undo', {files: 0})); await tick(900);
  assert.equal($('a-keep').disabled, false);
  const keeps = calls.filter(c => /\/keep$/.test(c.url)).length;
  keepClash = 1; $('a-keep').click(); await tick();
  assert.equal($('confirm-title').textContent, 'Keep it?'); $('confirm').close('ok'); await tick(); await tick();
  assert.equal($('confirm-title').textContent, 'Keep it unproved?'); assert.match($('confirm-text').textContent, /1 file changed since: late\.py/);
  $('confirm').close('ok'); await tick(); await tick();
  assert.deepEqual(calls.filter(c => /\/keep$/.test(c.url)).slice(keeps).map(c => c.body), [{}, {unverified_ok: true}]);
  feed.push(E(34, 'kept', {commit: 'abc1234567', into: 'main', files: 2, restart: false, proof: 'unverified', unverified: true,
    learned: {decision: true, daily: false, outcome: true}})); await tick(900);
  assert.equal(f.querySelector('.card2.kept .unproved').textContent, 'Kept without proof (unverified): you chose Keep anyway.');
  assert.equal(f.querySelector('.card2.kept .learned').textContent, "Saved to Apex: decision log ✓ · couldn't write today's note · outcome ✓");
  assert.equal(f.querySelector('.card2.kept .learned .bad').textContent, "couldn't write today's note");
  feed.push(E(35, 'discarded', {reason: 'wrong', learned: {decision: true, daily: true, outcome: true}})); await tick(900);
  const thrown = [...f.querySelectorAll('.step.mark')].at(-1);
  assert.match(thrown.textContent, /^Thrown away \(it was wrong\): the session's branch and copy are gone\./);
  assert.equal(thrown.querySelector('.learned').textContent, "Saved to Apex: decision log ✓ · today's note ✓ · outcome ✓");
  // Brain on tap: the plan asked Apex's memory (a 🧠 step), and what it suggested waits for your OK.
  const SUGGEST = (id, content, kind, session_id, project_id = 1, why = '') => ({id, ts: now, content, kind, why, tags: 'from-mcp,code',
    source: `Apex Code session, project ${project_id}`, project_id, session_id});
  suggestions = [SUGGEST(51, 'Alex logs errors with structlog <img src=x onerror="window.pwned=2">', 'preference', 9, 1, 'he said so twice'),
    SUGGEST(52, 'The cache is keyed by user id', 'decision', 4), SUGGEST(53, 'Another project\'s note', 'note', 11, 2)];
  feed.push(E(36, 'tool', {tool: 'memory', title: "Checked Apex's memory: error logging", ref: 'm1'}), E(37, 'result', {ref: 'm1', ok: true, output: ''}),
            E(38, 'tool', {tool: 'memory', title: 'Suggested a memory for your OK: Alex logs errors with structlog', ref: 'm2'}));
  await tick(900);
  const mem = [...f.querySelectorAll('.step.memory')];
  assert.equal(mem.length, 2, 'each call to Apex\'s memory is its own step');
  assert.equal(mem[0].textContent, "🧠Checked Apex's memory: error logging✓"); assert.equal(mem[0].querySelector('.st').className, 'st ok');
  assert.equal(mem[1].querySelector('.mt').textContent, 'Suggested a memory for your OK: Alex logs errors with structlog');
  assert.equal($('box-waiting').hidden, false, 'the suggestion shows without waiting for the next poll');
  assert.equal($('wait-count').textContent, '2', "this project's suggestions only");
  const waits = () => [...$('wait-list').querySelectorAll('.wm')];
  assert.deepEqual(waits().map(r => [r.querySelector('.wk').textContent, r.querySelector('.wh .muted').textContent, r.querySelector('.wt').textContent]),
    [['preference', 'from this session', 'Alex logs errors with structlog <img src=x onerror="window.pwned=2">'], ['decision', 'from session 4', 'The cache is keyed by user id']]);
  assert.equal(waits()[0].querySelector('.why').textContent, 'Why: he said so twice');
  assert.equal(waits()[0].querySelector('img'), null, 'what a plan suggests is never HTML'); assert.equal(w.pwned, undefined);
  [...waits()[0].querySelectorAll('button')].find(b => b.textContent === 'Approve').click(); await tick(); await tick();
  assert.ok(last(/\/api\/staged-writes\/51\/approve$/), 'Approve goes through the approvals queue');
  assert.equal($('toast').textContent, "Saved to Apex's memory. The next session here hears it.");
  assert.deepEqual(waits().map(r => r.querySelector('.wt').textContent), ['The cache is keyed by user id']);
  // Edit: your wording. A poll while you type never wipes it; Save rejects the suggestion, then saves yours.
  [...waits()[0].querySelectorAll('button')].find(b => b.textContent === 'Edit').click();
  const wArea = $('wait-list').querySelector('.wm.editing textarea');
  assert.equal(wArea.value, 'The cache is keyed by user id');
  wArea.value = 'The cache is keyed by user id and day';
  feed.push(E(39, 'tool', {tool: 'memory', title: "Checked Apex's memory: cache", ref: 'm3'})); await tick(900);
  assert.equal($('wait-list').querySelector('.wm.editing textarea').value, 'The cache is keyed by user id and day', 'a refresh keeps what you typed');
  [...$('wait-list').querySelectorAll('.wm.editing button')].find(b => b.textContent === 'Save my version').click(); await tick(); await tick(); await tick();
  const rejectAt = calls.findIndex(c => c.url === '/api/staged-writes/52/reject'), savedAt = calls.findIndex(c => c.url === '/api/memories' && c.method === 'POST');
  assert.ok(rejectAt >= 0 && savedAt > rejectAt, 'Edit rejects the suggestion, then saves your version');
  assert.deepEqual(calls[savedAt].body, {content: 'The cache is keyed by user id and day', kind: 'decision', tags: 'from-mcp,code'});
  assert.equal($('toast').textContent, 'Saved your version. The next session here hears it.');
  assert.equal($('box-waiting').hidden, true, 'nothing left for this project: the box goes');
  // Reject: nothing is saved.
  suggestions = [...suggestions, SUGGEST(54, 'Alex hates tabs', 'preference', 9)];
  feed.push(E(40, 'done', {status: 'done', summary: 'Done.', seconds: 3, files: 0, total: 2, engine: 'claude'})); await tick(900);
  assert.equal($('box-waiting').hidden, false);
  const memories = calls.filter(c => c.url === '/api/memories' && c.method === 'POST').length;
  [...waits()[0].querySelectorAll('button')].find(b => b.textContent === 'Reject').click(); await tick(); await tick();
  assert.ok(last(/\/api\/staged-writes\/54\/reject$/)); assert.equal($('toast').textContent, 'Rejected. Nothing was saved.');
  assert.equal(calls.filter(c => c.url === '/api/memories' && c.method === 'POST').length, memories, 'Reject saves nothing');
  assert.equal($('box-waiting').hidden, true);
  await phone();
  console.log('PASS: greeting and plans, @ and / as you type, starting with a model and effort, polling then the live stream (typing as it writes), '
    + 'every feed step (agent text never HTML, plan in place, reads grouped, failed command open, edits with their diff inline), Copy buttons (and their fallback), Allow once, Esc stops, '
    + 'done with tokens, the second opinion ring, the diff viewer, !commands and the terminal, /commands, the file tree and coloured viewer with @, '
    + 'history, Ctrl+K, Plan first then Build it, Keep only on a real yes, Keep & push, back home, '
    + 'what Apex knows about you (the card under your message, the home chip and dialog, Forget, the palette), '
    + 'rules said once (chips under a correction, the Rules tab with on/off, edit, delete, add, versions and Forget, a clash keeping your text, /rule, Ctrl+K), '
    + 'and the proof card (what it said vs what Apex saw, unknown checks never a tick, Prove it kept as yours, exit 0 as a pass, a stale pass, goalpost and disagreement, '
    + 'independence and invented citations, Keep asking before keeping unproved work, and again when the proof changed), '
    + 'and every session teaching Apex (the week\'s proved count, the track record in counts or "not enough sessions yet", the plan\'s line in the hint, '
    + 'Throw away asking why, what Keep and Throw away wrote, the decision log with History and Restore), '
    + 'and Apex\'s memory on tap (the 🧠 step, memories waiting for your OK with Approve, Reject and Edit), '
    + 'and away mode (📱 sent to your phone; a phone with its own token answers Allow once or Don\'t allow, never sees the owner-only message, '
    + 'and is told when a request expired, was answered, or is unknown; a link tapped at the PC asks the same question).');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
