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
// rest, expired, answered or unknown links said plainly, and the same link at the PC;
// and Celine on the build: milestones said in your Voicebox voice (never code; Voicebox
// busy waits and tries once more, then the chime), play-by-play paced, a message she
// drafted (Send, Edit, ✕), and asking her typed (/celine) or out loud (🎙, Ctrl+Shift+Space);
// and the night shift: the morning brief's link (/code#overnight) shows "While you slept"
// (the proof's badge, the rating, Keep through the proof's question, Throw away with why,
// Open), its counts said out loud without titles, the 🌙 badge, and "From your Work list".
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
let exitOk = 0, extraSessions = [];
// What the night shift built (agent/code_studio.overnight), for the morning's strip.
const NIGHT = (id, title, extra = {}) => ({id, title, project: 'Apex', project_id: 1, origin: 'night', task: {id: id - 20, title}, engine: 'claude',
  engine_name: 'Claude plan', last_status: 'done', working: false, verdict: 'unverified', reasons: [], checks: 'none', why: '', claimed: false,
  rating: null, review_state: '', review_engine: '', review_engine_name: '', files_changed: 2, branch: `apex/${id}-x`, updated: now - 3600, ...extra});
let nightDoc = [];
// What tends to happen on the project (agent/code_brain.track_record), as the overview carries it.
const CLAUDE_LINE = {key: 'engine:claude', text: 'Claude plan here: kept 7 of 9, 6 with proof', n: 9, hits: 7, rate: 0.778};
let recordDoc = {lines: [{key: 'no_checks', text: 'thrown away 4 of 6 when no checks ran', n: 6, hits: 4, rate: 0.667}, CLAUDE_LINE],
  engines: {claude: CLAUDE_LINE}, decided: 15, note: 'Counted from 15 finished sessions in the last 90 days.'};
const overview = () => ({owner: 'Alex', projects: [{id: 1, name: 'Apex', path: 'C:\\Apex', checks: 'python -m pytest -q', branch: 'main', checks_exit_ok: exitOk}],
  sessions: [{id: 7, project_id: 1, title: 'Add Focus mode', engine: 'claude', mode: 'safe', status: 'ready', last_status: 'done', files_changed: 2, review_rating: 8, updated: now - 120, working: false},
             {id: 3, project_id: 1, title: 'Old try', engine: 'chatgpt', mode: 'safe', status: 'discarded', last_status: 'done', files_changed: 0, review_rating: null, updated: now - 9000, working: false},
             ...extraSessions],
  plans, default_engine: 'claude', week: {sessions: 2, kept: 0, proved: 1, rating: 8, minutes: 3, credits: 0}, working: 0, record: {1: recordDoc}});
let session = {id: 7, project_id: 1, project: 'Apex', title: 'Add Focus mode', engine: 'claude', mode: 'safe', branch: 'apex/7-add-focus-mode', worktree: 'C:\\ApexWork\\code\\apex-7',
  status: 'ready', working: true, side: '', since: now - 75, summary: '', review_state: 'done', review_engine: 'chatgpt', review_rating: 8, review_text: 'Rating: 8/10\nVerdict: Clean.\nProblems:\n- None found',
  check_state: '', check_output: '', conflict: 0, kept_commit: '', changes: {files: [{path: 'work.js', plus: 11, minus: 0, change: 'update'}, {path: 'new.css', plus: 2, minus: 0, change: 'add'}], plus: 13, minus: 0}};
const E = (id, kind, extra = {}) => ({id, ts: now - 60 + id, kind, ...extra});
const BRAIN = [{kind: 'profile', ref: 'APEX_USER.md', text: 'Alex builds Apex, on Windows.'},
  {kind: 'rule', ref: 1, text: 'Never touch the voice files'}, {kind: 'memory', ref: 12, text: 'Alex wants type hints everywhere'},
  {kind: 'memory', ref: 15, text: 'Small functions, plain names'}];
let brainSources = BRAIN.slice(), forgotten = [], unvouched = [];
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
// Voice (dashboard/server.py /api/speak) and Celine (/api/companion/chat, workspace 'code').
let speakBusy = 0, heard = 'what changed?', celineThreads = new Set([5]);
const spoken = () => calls.filter(c => c.url === '/api/speak').map(c => c.body.text);
function speakApi(body) {
  if (speakBusy) { speakBusy--; return Response.json({error: 'Voicebox is already generating an Apex reply. Try again when it finishes.'}, {status: 503}); }
  return new Response(new Uint8Array([82, 73, 70, 70]), {headers: {'content-type': 'audio/wav'}});
}
function celineApi(body) {
  if (body.thread_id && !celineThreads.has(body.thread_id)) return Response.json({detail: 'Conversation no longer exists. Start a new conversation.'}, {status: 400});
  const tid = body.thread_id || 5; celineThreads.add(tid);
  const answer = /safe/.test(body.message) ? 'Not yet. It is unverified: Apex has not run the checks.' : 'It added the retry in two files.';
  const lines = [{type: 'start', thread_id: tid}, {type: 'token', text: answer.slice(0, 8)}, {type: 'token', text: answer.slice(8)}, {type: 'done', text: answer}];
  return new Response(lines.map(x => JSON.stringify(x)).join('\n') + '\n', {headers: {'content-type': 'application/x-ndjson'}});
}
const pcAsks = {'tok-pc-0123456789abcdef': ASK({command: 'rm -rf build', title: 'Add Focus mode'})};
const calls = [];
let streamOn = false, streamLive = null;
const FILES = ['README.md', 'dashboard/static/work.js', 'dashboard/static/work.css'];
w.fetch = async (url, opts = {}) => {
  calls.push({url, method: opts.method || 'GET', body: opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : null});
  if (url === '/api/code') return Response.json(overview());
  if (url === '/api/code/models/chatgpt') return Response.json({models: [{id: 'gpt-account', label: 'Account GPT', efforts: ['high', 'xhigh']}], error: ''});
  if (url === '/api/code/models/claude') return Response.json({models: [{id: 'opus', label: 'Account Opus', efforts: []}, {id: 'sonnet', label: 'Account Sonnet', efforts: []}], error: ''});
  if (url === '/api/code/projects/1/brain') { const sources = brainSources.filter(x => !forgotten.includes(x.ref)); return Response.json({text: sources.length ? 'What Apex knows…' : '', sources, chars: 99, unvouched}); }
  const vm = url.match(/^\/api\/code\/memories\/(\d+)\/vouch$/);
  if (vm && opts.method === 'POST') { const x = unvouched.find(u => u.ref === +vm[1]); unvouched = unvouched.filter(u => u !== x); brainSources.push(x); return Response.json({ok: true, id: +vm[1]}); }
  if (url.startsWith('/api/code/projects/1/rules')) return rulesApi(url, opts);
  if (url.startsWith('/api/code/projects/1/decisions')) return logApi(url, opts);
  if (url === '/api/code/approvals') return Response.json({items: suggestions});
  if (url === '/api/code/overnight') return Response.json({sessions: nightDoc});
  if (url === '/api/speak') return speakApi(JSON.parse(opts.body));
  if (url === '/api/companion/chat') return celineApi(JSON.parse(opts.body));
  if (url === '/api/companion/transcribe?engine=local') return Response.json({text: heard});
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
  if (/\/file\?path=.*\.png$/.test(url)) return Response.json({path: 'generated_images/art.png', image: {mime: 'image/png', base64: 'aW1hZ2U='}, binary: true, size: 5});
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
// Audio and the chime, as far as the page can tell (the microphone only for Celine's part, below).
const played = [], chimes = [];
w.Audio = class { constructor(src) { this.src = src; } play() { played.push(this.src); setTimeout(() => this.onended && this.onended(), 5); return Promise.resolve(); } pause() {} };
w.URL.createObjectURL = () => 'blob:speech'; w.URL.revokeObjectURL = () => {};
w.AudioContext = class { constructor() { this.currentTime = 0; this.destination = {}; }
  createOscillator() { chimes.push(1); return {frequency: {}, connect: g => g, start() {}, stop() {}}; }
  createGain() { return {gain: {setValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect: x => x}; } };
w.localStorage.setItem('apex.code.narrate', 'off');            // the flow below is quiet; Celine's part turns it on
w.localStorage.setItem('apex.voicebox.profile', 'celine');
w.eval(fs.readFileSync(path.join(base, 'theme.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(base, 'speech_queue.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(base, 'code_narration.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(base, 'code.js'), 'utf8'));
const tick = (ms = 40) => new Promise(r => setTimeout(r, ms));
const type = (text) => { const p = $('prompt'); p.focus(); p.value = text; p.setSelectionRange(text.length, text.length); p.dispatchEvent(new w.Event('input')); };
const key = (k, extra = {}) => $('prompt').dispatchEvent(new w.KeyboardEvent('keydown', {key: k, bubbles: true, cancelable: true, ...extra}));
const last = (re) => calls.filter(c => re.test(c.url) && c.method === 'POST').at(-1);
const lastPut = () => calls.filter(c => c.url === '/api/code/projects/1/rules' && c.method === 'PUT').at(-1);
const current9 = () => w.location.hash === '#allow=tok-pc-0123456789abcdef' && d.body.classList.contains('view-session');

// Celine on the build: what the page says out loud, and asking her about the session.
async function celine() {
  w.navigator.mediaDevices = {getUserMedia: async () => ({getTracks: () => [{stop() {}}]})};
  w.MediaRecorder = class { static isTypeSupported(t) { return t === 'audio/webm'; }
    constructor(stream, o) { this.mimeType = (o || {}).mimeType || ''; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.ondataavailable({data: new w.Blob([new Uint8Array(2000)])}); this.onstop(); } };
  session = {...session, id: 7, status: 'ready', working: false, side: ''};
  if (!d.body.classList.contains('view-session') || !w.location.hash.startsWith('#s=7')) { w.location.hash = '#s=7'; await tick(300); }
  feed.push(E(50, 'note', {text: 'ready'})); await tick(900);
  assert.equal($('narrate').value, 'off', 'the choice is remembered');
  assert.deepEqual([...$('narrate').options].map(o => o.value), ['off', 'milestones', 'play']);
  assert.match($('narrate').title, /Voicebox/); assert.match($('narrate').title, /tts-1/);
  assert.equal(spoken().length, 0, 'nothing is said with narration off');
  $('narrate').value = 'milestones'; $('narrate').dispatchEvent(new w.Event('change'));
  assert.equal(w.localStorage.getItem('apex.code.narrate'), 'milestones');
  // Milestones, in order, in your Voicebox voice; never the code in the summary.
  feed.push(E(51, 'done', {status: 'done', summary: 'Added a retry to `upload.py`. ```py\nsecret_code()\n``` More.', seconds: 70, files: 2, total: 3, engine: 'claude'}),
    E(52, 'checks', {passed: true, state: 'passed', why: '212 passed', seconds: 9, command: 'python -m pytest -q', output: '212 passed'}),
    E(53, 'review', {engine: 'chatgpt', status: 'done', rating: 8, text: 'Rating: 8/10\nVerdict: fine.'}));
  await tick(900);
  assert.deepEqual(spoken(), ['Done.', '3 files changed.', 'Added a retry to upload.py.', 'Checks passed: 212 passed.', 'ChatGPT rates it 8 out of 10.'],
    'one sentence at a time (speech_queue.js)');
  const asked = calls.filter(c => c.url === '/api/speak');
  assert.ok(asked.every(c => c.body.engine === 'voicebox' && c.body.profile === 'celine'), 'your Voicebox voice, never the paid one');
  assert.ok(!spoken().join(' ').includes('secret') && !spoken().join(' ').includes('```'), 'never code');
  assert.equal(played.length, 5);
  // Voicebox busy with another reply: wait, try once more; busy again: the chime instead.
  speakBusy = 1; feed.push(E(54, 'checks', {passed: false, state: 'unknown', why: 'no test count', seconds: 2, command: 'npm test', output: ''}));
  await tick(900); assert.equal(spoken().filter(t => t === 'Checks unknown: no test count.').length, 1, 'it waits before trying again');
  await tick(2400); assert.equal(spoken().filter(t => t === 'Checks unknown: no test count.').length, 2, 'then tries once more');
  assert.equal(played.length, 6);
  const before = chimes.length; speakBusy = 2;
  feed.push(E(55, 'checks', {passed: false, state: 'failed', why: '1 failed', seconds: 2, command: 'npm test', output: '1 failed'}));
  await tick(3600);
  assert.equal(spoken().filter(t => t === 'Checks failed.').length, 2, 'once more, no more');
  assert.ok(chimes.length > before, 'then the chime'); assert.equal(played.length, 6);
  // Play-by-play: one phrase at most every 6 seconds, and none while something is said.
  $('narrate').value = 'play'; $('narrate').dispatchEvent(new w.Event('change'));
  const n0 = spoken().length;
  feed.push(E(56, 'tool', {tool: 'command', title: 'python -m pytest -q tests/test_upload.py', ref: 'pp1'}),
    E(57, 'file', {path: 'agent/upload.py', change: 'update', plus: 2, minus: 1, ref: 'pp2', diff: '@@ -1 +1 @@\n-a\n+b'}),
    E(58, 'tool', {tool: 'read', title: 'Read agent/upload.py', path: 'agent/upload.py', ref: 'pp3'}));
  await tick(900);
  assert.deepEqual(spoken().slice(n0), ['running pytest'], 'the rest within 6 seconds is dropped, not queued');
  $('narrate').value = 'milestones'; $('narrate').dispatchEvent(new w.Event('change'));
  // A message Celine drafted: shown with Send, Edit and ✕; only you send it.
  feed.push(E(59, 'draft', {text: 'Please add a test for the retry in upload.py.', by: 'Celine'})); await tick(900);
  assert.equal(spoken().at(-1), 'Celine drafted a message for you.');
  let card = [...d.querySelectorAll('.card2.draft')].at(-1);
  assert.equal(card.querySelector('.h').firstChild.textContent, '🎙 Celine drafted a message');
  assert.equal(card.querySelector('.dtext').textContent, 'Please add a test for the retry in upload.py.');
  assert.deepEqual([...card.querySelectorAll('.acts button')].map(b => b.textContent), ['Send', 'Edit', '✕']);
  assert.ok(!calls.some(c => c.url === '/api/code/sessions/7/messages' && c.body && c.body.prompt === 'Please add a test for the retry in upload.py.'), 'not sent on its own');
  [...card.querySelectorAll('.acts button')].find(b => b.textContent === 'Edit').click();
  assert.equal($('prompt').value, 'Please add a test for the retry in upload.py.'); $('prompt').value = '';
  [...card.querySelectorAll('.acts button')].find(b => b.textContent === 'Send').click(); await tick(); await tick();
  assert.equal(last(/\/api\/code\/sessions\/7\/messages$/).body.prompt, 'Please add a test for the retry in upload.py.');
  assert.equal(card.querySelector('.h .st').textContent, 'sent'); assert.equal(card.querySelector('.acts'), null);
  feed.push(E(60, 'draft', {text: 'Rename it.', by: 'Celine'})); await tick(900);
  card = [...d.querySelectorAll('.card2.draft')].at(-1);
  [...card.querySelectorAll('.acts button')].find(b => b.textContent === '✕').click();
  assert.equal(card.isConnected, false);
  assert.deepEqual(JSON.parse(w.localStorage.getItem('apex.code.drafts')), {59: 'sent', 60: 'dismissed'});
  // Ask her, typed: /celine. Her answer shows in her card and is said; one conversation per session.
  type('/celine is it safe to keep?'); $('brief-form').requestSubmit(); await tick(); await tick(); await tick(200);
  let chat = calls.filter(c => c.url === '/api/companion/chat');
  assert.equal(chat.length, 1);
  const {turn_id, ...sent} = chat[0].body;
  assert.match(turn_id, /^[a-zA-Z0-9_-]{16,80}$/);
  assert.deepEqual(sent, {message: 'is it safe to keep?', mode: 'discuss', workspace: 'code', code_session: 7, voice: 'voicebox', voice_profile: 'celine'});
  assert.equal($('celine-card').hidden, false);
  assert.equal($('celine-q').textContent, 'is it safe to keep?');
  assert.equal($('celine-a').textContent, 'Not yet. It is unverified: Apex has not run the checks.');
  assert.equal(w.localStorage.getItem('apex.code.celine.7'), '5');
  await tick(300); assert.equal(spoken().at(-1), 'It is unverified: Apex has not run the checks.', 'said, sentence by sentence');
  // Out loud: 🎙 starts listening, Ctrl+Shift+Space ends it; the words go to Celine in the same thread.
  $('celine-btn').click(); await tick();
  assert.ok($('celine-btn').classList.contains('rec'));
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: ' ', code: 'Space', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true}));
  await tick(); await tick(); await tick(200);
  assert.ok(!$('celine-btn').classList.contains('rec'));
  chat = calls.filter(c => c.url === '/api/companion/chat');
  assert.equal(chat.length, 2); assert.equal(chat[1].body.message, 'what changed?'); assert.equal(chat[1].body.thread_id, 5);
  assert.equal($('celine-q').textContent, 'what changed?'); assert.equal($('celine-a').textContent, 'It added the retry in two files.');
  // Her old conversation is gone (deleted elsewhere): a new one, once.
  celineThreads = new Set(); type('/celine what changed?'); $('brief-form').requestSubmit(); await tick(); await tick(); await tick(200);
  chat = calls.filter(c => c.url === '/api/companion/chat');
  assert.equal(chat.length, 4); assert.equal(chat[2].body.thread_id, 5); assert.equal(chat[3].body.thread_id, undefined);
  assert.equal($('celine-a').textContent, 'It added the retry in two files.');
  // The mic still fills the box (the same recording, factored out).
  $('mic').click(); await tick(); assert.ok($('mic').classList.contains('rec'));
  heard = 'add a test'; $('mic').click(); await tick(); await tick(); await tick();
  assert.equal($('prompt').value, 'add a test'); $('prompt').value = '';
  // Ctrl+K offers her too; ✕ closes her card.
  $('palette-btn').click(); $('palette-q').value = 'celine'; $('palette-q').dispatchEvent(new w.Event('input'));
  assert.ok([...$('palette-list').children].some(r => r.textContent === '🎙 Ask Celine about this session')); $('palette').close();
  $('celine-close').click(); assert.equal($('celine-card').hidden, true);
  $('narrate').value = 'off'; $('narrate').dispatchEvent(new w.Event('change'));
  delete w.navigator.mediaDevices; delete w.MediaRecorder;
}

// Switching sessions while the old one streams, and dismissing Celine while she answers:
// nothing from the old session or the dismissed answer lands, moves the feed, or is said.
async function switching() {
  const realFetch = w.fetch, enc = new TextEncoder(), asked8 = [], celineSignals = [];
  const slow = (lines, ms, signal) => new Response(new ReadableStream({start(c) {
    const timer = setTimeout(() => { try { lines.forEach(x => c.enqueue(enc.encode(JSON.stringify(x) + '\n'))); c.close(); } catch (_) {} }, ms);
    if (signal) signal.addEventListener('abort', () => { clearTimeout(timer); try { c.error(new w.DOMException('aborted', 'AbortError')); } catch (_) {} });
  }}));
  let celineReply = null;
  w.fetch = async (url, opts = {}) => {
    if (url.startsWith('/api/code/sessions/7/stream')) {          // session 7's next step is on its way
      calls.push({url, method: 'GET', body: null});
      return slow([{t: 'event', ...E(500, 'done', {status: 'done', summary: 'Old session finished.', total: 1, engine: 'claude'})},
        {t: 'live', v: 999, text: 'old session typing', thinking: '', outputs: {}}], 250, opts.signal);
    }
    if (url === '/api/code/sessions/8') {                         // slow, like the proof's git work: the old step arrives meanwhile
      await new Promise(r => setTimeout(r, 300));
      return Response.json({...session, id: 8, title: 'Other one', working: false, side: ''});
    }
    const m8 = url.match(/^\/api\/code\/sessions\/8\/events\?after=(\d+)$/);
    if (m8) { asked8.push(Number(m8[1])); return Response.json({events: [E(2, 'you', {text: 'The other request'}), E(3, 'note', {text: 'other note'})].filter(e => e.id > Number(m8[1]))}); }
    if (url.startsWith('/api/code/sessions/8/stream')) return new Response('', {status: 503});
    if (url === '/api/companion/chat' && celineReply) {
      calls.push({url, method: 'POST', body: JSON.parse(opts.body)}); celineSignals.push(opts.signal);
      const lines = [{type: 'start', thread_id: 5}, {type: 'token', text: 'Partly '}];
      return new Response(new ReadableStream({start(c) {
        c.enqueue(enc.encode(lines.map(x => JSON.stringify(x)).join('\n') + '\n'));
        const timer = setTimeout(() => { try { c.enqueue(enc.encode(JSON.stringify({type: 'token', text: 'dismissed answer'}) + '\n'
          + JSON.stringify({type: 'done', text: celineReply}) + '\n')); c.close(); } catch (_) {} }, 250);
        if (opts.signal) opts.signal.addEventListener('abort', () => { clearTimeout(timer); try { c.error(new w.DOMException('aborted', 'AbortError')); } catch (_) {} });
      }}));
    }
    return realFetch(url, opts);
  };
  $('narrate').value = 'milestones'; $('narrate').dispatchEvent(new w.Event('change'));
  $('back').click(); await tick();
  w.location.hash = '#s=7'; await tick(150);                       // open, and its stream is reading
  assert.ok(calls.some(c => c.url.startsWith('/api/code/sessions/7/stream')));
  w.location.hash = '#s=8'; await tick(900);                       // switch before session 7's step arrives
  assert.deepEqual(asked8.slice(0, 1), [0], 'the new session\'s history is asked for from the start');
  assert.ok($('feed').textContent.includes('The other request'), 'its history is shown');
  assert.ok(!$('feed').textContent.includes('Old session finished') && !$('feed').textContent.includes('old session typing'),
    'nothing from the old session lands in the new feed');
  assert.ok(!spoken().some(t => /Old session finished/.test(t)), 'nor is said');
  // Celine: ✕ while she answers stops it; her card stays closed and the answer is never said.
  celineReply = 'Partly dismissed answer';
  type('/celine is it ok?'); $('brief-form').requestSubmit(); await tick(); await tick(80);
  assert.equal($('celine-card').hidden, false); assert.match($('celine-a').textContent, /Partly/);
  $('celine-close').click(); await tick(500);
  assert.equal($('celine-card').hidden, true, '✕ is not undone by her next words');
  assert.ok(celineSignals.at(-1) && celineSignals.at(-1).aborted, 'the answer stops streaming');
  assert.ok(!spoken().some(t => /dismissed answer/.test(t)), 'a dismissed answer is never said');
  assert.equal($('celine-btn').disabled, false);
  // …and switching sessions while she answers: the answer about the old one is never said.
  type('/celine is it ok?'); $('brief-form').requestSubmit(); await tick(); await tick(80);
  w.location.hash = '#s=7'; await tick(600);
  assert.ok(celineSignals.at(-1).aborted && !spoken().some(t => /dismissed answer/.test(t)));
  assert.equal($('celine-card').hidden, true);
  celineReply = null; w.fetch = realFetch;
  $('narrate').value = 'off'; $('narrate').dispatchEvent(new w.Event('change'));
  w.location.hash = '#s=7'; await tick(300);
}

// The night shift: the morning brief's link opens "While you slept" (/code#overnight).
async function nightShift() {
  nightDoc = [NIGHT(21, 'Fix login', {verdict: 'proved', checks: 'passed', why: '212 passed', rating: 8, review_state: 'done', review_engine: 'chatgpt', review_engine_name: 'ChatGPT plan'}),
    NIGHT(22, 'Retry uploads', {checks: 'unknown', why: "Could not run 'npm test'", claimed: true, review_state: 'skipped'}),
    NIGHT(23, 'Tidy the logs', {working: true, files_changed: 0})];
  extraSessions = [{id: 21, project_id: 1, title: 'Fix login', engine: 'claude', mode: 'safe', status: 'ready', last_status: 'done', files_changed: 2,
    review_rating: 8, origin: 'night', task_id: 1, updated: now - 60, working: false}];
  $('narrate').value = 'milestones'; $('narrate').dispatchEvent(new w.Event('change'));
  const said = spoken().length;
  w.location.hash = '#overnight'; await tick(300); await tick(300);
  assert.ok(d.body.classList.contains('view-home')); assert.equal($('overnight').hidden, false);
  const rows = () => [...$('overnight-rows').querySelectorAll('.nrow')];
  assert.equal(rows().length, 3);
  const [good, unsure, busyRow] = rows();
  assert.equal(good.querySelector('.p-badge').textContent, '✓ Proved'); assert.ok(good.querySelector('.p-badge').classList.contains('ok'));
  assert.equal(good.querySelector('.why').textContent, 'Ready to Keep: checks passed (212 passed).');
  assert.ok(good.querySelector('.ring10'), 'the rating ring'); assert.match(good.querySelector('.m').textContent, /8\/10 by ChatGPT plan/);
  assert.equal(unsure.querySelector('.p-badge').textContent, '? Unverified');
  assert.equal(unsure.querySelector('.why').textContent, "Not verified: checks couldn't run; it claimed the tests pass, Apex didn't see it.");
  assert.match(unsure.querySelector('.m').textContent, /no independent review/);
  assert.equal(busyRow.querySelector('.p-badge').textContent, '… Working');
  assert.deepEqual([...busyRow.querySelectorAll('.acts button')].map(b => b.textContent), ['Open'], 'nothing to decide while it works');
  assert.deepEqual([...good.querySelectorAll('.acts button')].map(b => b.textContent), ['Keep', 'Throw away', 'Open']);
  // Said out loud in counts only: a title could be private.
  await tick(300);
  assert.deepEqual(spoken().slice(said), ["Two sessions are ready; one isn't verified.", 'One is still working.']);
  assert.ok(!spoken().slice(said).join(' ').match(/Fix login|Retry uploads|Tidy/));
  // The 🌙 badge on its card.
  const card = [...$('cards').children].find(c => c.textContent.includes('Fix login'));
  assert.equal(card.querySelector('.badge.night').textContent, '🌙 night');
  // Keep goes through the proof's question first.
  proofDoc = PROOF({verdict: 'proved', reasons: ['Apex ran python -m pytest -q on checkpoint 2: 212 passed.']});
  [...good.querySelectorAll('.acts button')].find(b => b.textContent === 'Keep').click(); await tick();
  assert.equal($('confirm').open, true); assert.equal($('confirm-title').textContent, 'Keep it?');
  assert.match($('confirm-text').textContent, /This merges 2 files into main of Apex\.[\s\S]*✓ Proved: Apex ran/);
  $('confirm').close('ok'); await tick(); await tick();
  assert.deepEqual(last(/\/api\/code\/sessions\/21\/keep$/).body, {});
  assert.equal(rows().length, 2, 'kept: off the strip');
  // Unproved: "Keep it unproved?", and Esc keeps nothing.
  proofDoc = PROOF();
  [...rows()[0].querySelectorAll('.acts button')].find(b => b.textContent === 'Keep').click(); await tick();
  assert.equal($('confirm-title').textContent, 'Keep it unproved?'); $('confirm').close(); await tick();
  assert.ok(!calls.some(c => /\/sessions\/22\/keep$/.test(c.url)));
  // Throw away asks why.
  [...rows()[0].querySelectorAll('.acts button')].find(b => b.textContent === 'Throw away').click(); await tick();
  assert.equal($('discard-dialog').open, true);
  [...$('discard-reasons').querySelectorAll('[data-reason]')].find(b => b.dataset.reason === 'wrong').click();
  $('discard-dialog').close('ok'); await tick(); await tick();
  assert.deepEqual(last(/\/api\/code\/sessions\/22\/discard$/).body, {reason: 'wrong'});
  assert.equal(rows().length, 1);
  // Open: the session, with where it came from under the first message, and the skipped review said plainly.
  session = {...session, id: 23, title: 'Tidy the logs', origin: 'night', task_id: 3, task: {id: 3, title: 'Tidy the logs'}, status: 'ready',
    working: false, side: '', review_state: 'skipped', review_rating: null, review_text: 'No independent review: the other plan is resting.'};
  [...rows()[0].querySelectorAll('.acts button')].find(b => b.textContent === 'Open').click(); await tick(300); await tick(300);
  assert.ok(d.body.classList.contains('view-session'));
  const from = d.querySelectorAll('.from-work');
  assert.equal(from.length, 1, 'only under the first message');
  assert.equal(from[0].textContent, '🌙 From your Work list: Tidy the logs'); assert.equal(from[0].querySelector('a').getAttribute('href'), '/work');
  assert.ok([...$('s-meta').children].some(x => x.textContent === '🌙 night'));
  assert.equal($('r-body').textContent, 'No independent review: the other plan is resting. Ask for one now if you want it.');
  $('narrate').value = 'off'; $('narrate').dispatchEvent(new w.Event('change'));
  nightDoc = []; extraSessions = [];
  $('back').click(); await tick(300);
  assert.equal($('overnight').hidden, true, 'nothing left: the strip goes');
}

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
  pw.eval(fs.readFileSync(path.join(base, 'speech_queue.js'), 'utf8'));
  pw.eval(fs.readFileSync(path.join(base, 'code_narration.js'), 'utf8'));
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
  assert.ok($('brain-body').querySelector('.bl.k-memory').classList.contains('gone')); assert.equal($('toast').textContent, 'Forgotten. The next message will refresh memory.');
  assert.equal($('brain-chip').textContent, '🧠Apex knows 3 things for Apex', 'the chip counts again');
  $('brain-close').click(); assert.equal($('brain-dialog').open, false);
  brainSources = []; $('brain-chip').click(); await tick(); await tick();
  assert.equal($('brain-body').textContent, 'Tell Celine or Apex chat how you like your code; it shows up here.');
  $('brain-dialog').close(); brainSources = BRAIN.slice(); forgotten = [];
  // An older coding memory (no record of who saved it) waits until Alex says it's his.
  unvouched = [{kind: 'memory', ref: 40, text: 'Alex wants <b>small</b> functions'}];
  $('brain-chip').click(); await tick(); await tick();
  const waitRow = $('brain-body').querySelector('.unvouched .bl');
  assert.equal(waitRow.querySelector('.bt').textContent, 'Alex wants <b>small</b> functions', 'memory text never becomes HTML');
  assert.equal($('brain-body').querySelector('.unvouched .bh').textContent, 'Not told until you OK it');
  waitRow.querySelector('.vouch').click(); await tick(); await tick(); await tick();
  assert.ok(calls.some(c => c.url === '/api/code/memories/40/vouch' && c.method === 'POST'), 'It\'s mine vouches for it');
  assert.equal($('toast').textContent, 'Approved. The next message will refresh memory.');
  assert.equal($('brain-body').querySelector('.unvouched'), null, 'nothing left to vouch for');
  assert.ok([...$('brain-body').querySelectorAll('.bl.k-memory .tag')].some(t => t.textContent === '#40'), 'it is in the brief now');
  $('brain-dialog').close(); brainSources = BRAIN.slice(); forgotten = []; unvouched = [];
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
  assert.ok([...$('model').options].some(o => o.textContent === 'Account Opus'), 'signed-in Claude models are offered');
  $('model').value = 'opus'; $('model').dispatchEvent(new w.Event('change'));
  d.querySelector('[data-engine=chatgpt]').click();
  assert.equal($('model').value, '', 'Claude model is not passed to ChatGPT');
  assert.ok([...$('model').options].some(o => o.value === 'gpt-account'), 'account GPT is selectable');
  $('model').value = 'gpt-account'; $('model').dispatchEvent(new w.Event('change'));
  $('effort').value = 'xhigh'; $('effort').dispatchEvent(new w.Event('change'));
  d.querySelector('[data-engine=claude]').click();
  assert.equal($('model').value, 'opus');
  d.querySelector('[data-engine=chatgpt]').click();
  assert.equal($('model').value, 'gpt-account'); assert.equal($('effort').value, 'xhigh');
  d.querySelector('[data-engine=claude]').click();
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
  assert.equal($('toast').textContent, 'Forgotten. The next message will refresh memory.');
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
  feed.push(E(41, 'image', {path: 'generated_images/art.png'})); await tick(900);
  const generated = $('feed').querySelector('.step.image img');
  assert.ok(generated && generated.src.startsWith('data:image/png;base64,'), 'generated raster displayed in the feed');
  $('feed').querySelector('.step.image button').click(); await tick();
  assert.ok($('viewer-body').querySelector('img'), 'generated raster also opens in the viewer');
  $('viewer').close();
  assert.equal($('box-waiting').hidden, false);
  const memories = calls.filter(c => c.url === '/api/memories' && c.method === 'POST').length;
  [...waits()[0].querySelectorAll('button')].find(b => b.textContent === 'Reject').click(); await tick(); await tick();
  assert.ok(last(/\/api\/staged-writes\/54\/reject$/)); assert.equal($('toast').textContent, 'Rejected. Nothing was saved.');
  assert.equal(calls.filter(c => c.url === '/api/memories' && c.method === 'POST').length, memories, 'Reject saves nothing');
  assert.equal($('box-waiting').hidden, true);
  await celine();
  await switching();
  await nightShift();
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
    + 'and is told when a request expired, was answered, or is unknown; a link tapped at the PC asks the same question), '
    + 'and Celine on the build (milestones in your Voicebox voice with no code, a busy voice tried once more then the chime, play-by-play paced, '
    + 'a draft with Send, Edit and ✕, and asking her typed or out loud with her answer shown and said, in one thread per session; '
    + 'switching sessions mid-stream keeps the old one\'s steps out of the new feed, and ✕ or another session stops her answer, unsaid), '
    + 'and the night shift (While you slept from the morning link: proof badges, the rating, Keep through the proof\'s question, Throw away with why, Open; '
    + 'its counts said without titles; the 🌙 badge; From your Work list under the first message; a skipped review said plainly).');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
