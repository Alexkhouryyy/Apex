// Apex Code (dashboard/code.py, agent/code_studio.py): coding sessions on your
// Claude and ChatGPT plans. Home: greeting, your plans, the brief, recent work.
// Session: what you asked and what Apex did, live, step by step; the change,
// a second opinion out of 10, checks, the proof (what it said vs what Apex
// saw), and Keep / Undo / Catch up / Throw away (with why). What every session
// taught Apex shows on the home page, in counts, and in the Rules tab's decision log.
// While it works, the plan can ask Apex's memory (a 🧠 step); a memory it suggests
// waits in the side panel for your OK. Away mode: a command Safe mode stopped is
// answered from your phone (/code#allow=…), and a finished session's ping opens it.
// Celine on the build: ask her out loud (🎙, Ctrl+Shift+Space) or typed (/celine) about
// the session; she answers in her card and in your Voicebox voice, and may draft a message
// that only you send. Milestones (or play-by-play) are said out loud as they happen.
// Night shift: a +apex software task from your Work list, built overnight on a plan
// (🌙). The morning brief's link (/code#overnight) opens "While you slept": each one
// with what Apex saw, its rating, and Keep / Throw away / Open. Nothing is kept for you.
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const NS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
  const store = {
    get(k, d = '') { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  };
  const auth = () => { const t = store.get('apex_token'); return t ? {Authorization: 'Bearer ' + t} : {}; };
  async function api(path, options = {}, as = 'json') {
    const r = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...auth(), ...(options.headers || {})}});
    if (r.status === 401) { $('login').showModal(); throw new Error('Enter your Apex token.'); }
    if (as === 'text' && r.ok) return r.text();
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { const err = new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${r.status})`); err.status = r.status; err.body = body; throw err; }
    return body;
  }
  const post = (path, body = {}) => api(path, {method: 'POST', body: JSON.stringify(body)});
  const put = (path, body = {}) => api(path, {method: 'PUT', body: JSON.stringify(body)});
  let toastTimer;
  function say(text, kind = '', link) {
    const t = $('toast'); t.replaceChildren(text); t.className = 'toast ' + kind; t.hidden = false;
    if (link) { t.append(' '); const a = el('a', '', link.text); a.href = link.href; t.append(a); }
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, kind === 'error' ? 7000 : 4000);
  }

  const NAME = {claude: 'Claude plan', chatgpt: 'ChatGPT plan'};
  const OTHER = {claude: 'chatgpt', chatgpt: 'claude'};
  let ov = null, current = null, detail = null, lastId = 0, feed = null, pollTimer = null, lastOverview = 0, quiet = 0;
  let project = Number(store.get('apex.code.project')) || null;
  let engine = store.get('apex.code.engine'), mode = store.get('apex.code.mode', 'safe');
  let model = '', effort = '', planFirst = false, files = null, filesFor = null, live = null, liveSeen = -1, initial = true;
  const MODELS = {claude: [['', 'Model: default'], ['fable', 'Fable'], ['opus', 'Opus'], ['sonnet', 'Sonnet'], ['haiku', 'Haiku']],
    chatgpt: [['', 'Model: default'], ['__other', 'Type a model…']]};
  const catalogs = {}, choices = {};
  function saveChoice() {
    if (!NAME[engine]) return;
    choices[engine] = {model, effort};
    store.set(`apex.code.model.${engine}`, model);
    store.set(`apex.code.effort.${engine}`, effort);
  }
  function switchEngine(next) {
    saveChoice(); engine = next;
    const saved = choices[engine] || {model: store.get(`apex.code.model.${engine}`, ''), effort: store.get(`apex.code.effort.${engine}`, '')};
    model = saved.model; effort = saved.effort;
    store.set('apex.code.engine', engine); renderEngine();
  }
  async function loadModels() {
    await Promise.all(Object.keys(NAME).map(async provider => {
      try { catalogs[provider] = await api(`/api/code/models/${provider}`); }
      catch (_) { catalogs[provider] = {models: [], error: 'Model discovery unavailable; enter an exact model ID.'}; }
    }));
    renderEngine();
  }
  const busy = () => !!(detail && (detail.working || detail.side));

  // ---------------------------------------------------------------- small pieces
  function ago(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`; if (s < 172800) return 'yesterday';
    return new Date(ts * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short'});
  }
  const clock = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  function reactor() {
    const s = svg('svg', {viewBox: '0 0 24 24', class: 'reactor', 'aria-hidden': 'true'});
    s.append(svg('circle', {class: 'ring', cx: 12, cy: 12, r: 10}), svg('circle', {class: 'core', cx: 12, cy: 12, r: 4}));
    return s;
  }
  function ring10(rating, size = 44) {
    const r = 18, c = 2 * Math.PI * r;
    const s = svg('svg', {viewBox: '0 0 44 44', width: size, height: size, class: 'ring10 ' + (rating >= 8 ? 'hi' : rating >= 6 ? 'mid' : 'lo'), role: 'img', 'aria-label': `${rating} out of 10`});
    s.append(svg('circle', {class: 'bg', cx: 22, cy: 22, r}),
      svg('circle', {class: 'fg', cx: 22, cy: 22, r, 'stroke-dasharray': `${(rating / 10) * c} ${c}`, transform: 'rotate(-90 22 22)'}));
    const t = svg('text', {x: 22, y: 22}); t.textContent = rating; s.append(t);
    return s;
  }
  // A safe little Markdown: paragraphs, lists, **bold**, `code` and ``` blocks. Built
  // as DOM nodes, never innerHTML, so nothing an agent writes can run in the page.
  // `live` is the text still being typed: no Copy buttons until the message is whole.
  function md(text, live) {
    const frag = document.createDocumentFragment();
    const parts = String(text || '').split(/```[\w+-]*\n?([\s\S]*?)```/g);
    parts.forEach((part, i) => {
      if (i % 2) { const code = part.replace(/\n$/, ''), pre = el('pre'); pre.append(el('code', '', code)); frag.append(live ? pre : withCopy(pre, () => code)); return; }
      let list = null, para = [];
      const flush = () => { if (para.length) { const p = el('p'); inline(p, para.join('\n')); frag.append(p); para = []; } };
      for (const raw of part.split('\n')) {
        const line = raw.trimEnd();
        const item = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)/);
        if (item) { flush(); if (!list) { list = el('ul'); frag.append(list); } const li = el('li'); inline(li, item[1]); list.append(li); continue; }
        list = null;
        const head = line.match(/^#{1,4}\s+(.*)/);
        if (head) { flush(); const p = el('p', 'h'); inline(p, head[1]); frag.append(p); continue; }
        if (!line.trim()) { flush(); continue; }
        para.push(line.trim());
      }
      flush();
    });
    return frag;
  }
  function inline(node, text) {
    const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g; let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) node.append(text.slice(last, m.index));
      node.append(m[0][0] === '`' ? el('code', '', m[0].slice(1, -1)) : el('strong', '', m[0].slice(2, -2)));
      last = m.index + m[0].length;
    }
    if (last < text.length) node.append(text.slice(last));
  }
  const plain = t => String(t || '').replace(/```[\s\S]*?```/g, '').replace(/[`*#>]/g, '').replace(/\n{2,}/g, '\n').trim();

  // ---------------------------------------------------------------- overview: plans, home, rail
  async function loadOverview() {
    ov = await api('/api/code');
    loadWaiting();                                       // what a session suggested, polled with the overview
    lastOverview = Date.now();
    if (!ov.projects.some(p => p.id === project)) project = ov.projects[0] && ov.projects[0].id;
    if (!engine || !NAME[engine]) engine = ov.default_engine;
    renderPlans(); renderHome(); renderRail(); renderEngine();
  }
  function planState(p) { return p.ready ? 'ready' : /signed in|not signed|installed|bills|credits/.test(p.why) ? 'out' : 'resting'; }
  function renderPlans() {
    const root = $('plans'); root.replaceChildren();
    for (const p of ov.plans) {
      const working = ov.sessions.some(s => s.working && s.engine === p.id);
      const chip = el('div', `plan ${p.id} ${planState(p)}${working ? ' busy' : ''}`);
      chip.title = p.ready ? `${p.name}: signed in and ready` : `${p.name} ${p.why}. ${p.how || ''}`;
      chip.append(reactor(), el('b', '', p.name.replace(/ plan$/, '')), el('span', '', working ? 'working' : p.ready ? 'ready' : planState(p) === 'out' ? 'not ready' : 'resting'));
      root.append(chip);
    }
  }
  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  }
  const STARTS = [
    ['⚑', 'Fix a bug', 'Fix this bug in Apex: '],
    ['✦', 'Add a feature', 'Add this to Apex: '],
    ['◎', 'Explain', 'Explain how this part of Apex works. Change nothing, just explain: '],
    ['✓', 'Write tests', 'Write tests for '],
    ['◈', 'Make it look better', 'Make this page cleaner and more futuristic, without breaking anything: '],
    ['⚖', 'Brutal review', 'Review the last commit honestly. Rate it out of 10, list what is wrong with file:line, and change nothing.'],
  ];
  function renderHome() {
    const name = ov.owner;
    $('today').textContent = new Date().toLocaleDateString(undefined, {weekday: 'long', day: 'numeric', month: 'long'}).toUpperCase() + ' · APEX CODE';
    const g = $('greeting'); g.replaceChildren(greeting() + (name ? ', ' : '.'));
    if (name) { g.append(el('span', 'name', name), '.'); }
    const ready = ov.plans.filter(p => p.ready), down = ov.plans.filter(p => !p.ready);
    $('greet-sub').textContent = !down.length ? 'Both plans are ready. Zero API credits. What are we building?'
      : ready.length ? `Your ${down[0].name} ${down[0].why}. Your ${ready[0].name} has you covered. What are we building?`
      : `Neither plan is ready: ${down[0].how || 'run Setup-Apex-Work-Plans.cmd'}`;
    const w = ov.week, week = $('week'); week.replaceChildren();
    for (const [v, label, cls] of [[w.sessions, 'sessions this week', ''], [w.kept, `kept · ${w.proved || 0} proved`, ''],
      [w.rating == null ? '–' : `${w.rating}/10`, 'average rating', ''], [`${w.minutes}m`, 'of AI work', ''], ['$0', 'API credits', 'good']]) {
      const s = el('div', 'stat ' + cls); s.append(el('b', '', String(v)), el('span', '', label)); week.append(s);
    }
    renderRecord();
    const starts = $('starts'); starts.replaceChildren();
    for (const [icon, label, text] of STARTS) {
      const b = el('button', 'chip'); b.type = 'button'; b.append(el('i', '', icon), label);
      b.onclick = () => { const p = $('prompt'); p.value = text; p.focus(); p.setSelectionRange(text.length, text.length); autosize(); };
      starts.append(b);
    }
    const cards = $('cards'); cards.replaceChildren();
    const list = ov.sessions.filter(s => !project || s.project_id === project).slice(0, 9);
    if (!list.length) cards.append(el('p', 'empty', 'No sessions yet. Tell Apex what to build above: it works on its own branch, so nothing you have is at risk.'));
    for (const s of list) cards.append(sessionCard(s));
  }
  function sessionState(s) {
    return s.working ? 'working' : s.status === 'kept' ? 'kept' : s.status === 'discarded' ? 'discarded'
      : ['failed', 'limited', 'signed_out', 'missing', 'interrupted', 'stopped'].includes(s.last_status) ? 'failed' : 'review';
  }
  const STATE_TEXT = {working: 'Apex is working', kept: 'kept', discarded: 'thrown away', failed: 'stopped', review: 'ready for you'};
  const STOP_TEXT = {limited: 'hit the plan limit', signed_out: 'plan not signed in', missing: 'plan not set up', interrupted: 'interrupted', stopped: 'you stopped it'};
  const stateText = s => sessionState(s) === 'failed' ? (STOP_TEXT[s.last_status] || 'stopped') : STATE_TEXT[sessionState(s)];
  function sessionCard(s) {
    const c = el('button', 'card'); c.type = 'button';
    const body = el('div', 'grow');
    const t = el('div', 't', s.title);
    if (s.origin === 'night') t.append(nightBadge());
    body.append(t, el('div', 'm', `${stateText(s)} · ${NAME[s.engine] || s.engine} · ${s.files_changed} file${s.files_changed === 1 ? '' : 's'} · ${ago(s.updated)}`));
    c.append(el('i', 'state ' + sessionState(s)), body);
    if (s.review_rating != null) c.append(ring10(s.review_rating, 38));
    c.onclick = () => open(s.id);
    return c;
  }
  function nightBadge() {
    const b = el('span', 'badge night', '🌙 night'); b.title = 'Built by the night shift from your Work list'; return b;
  }
  function renderRail() {
    const sel = $('project'); sel.replaceChildren();
    for (const p of ov.projects) { const o = el('option', '', p.name + (p.branch ? ` · ${p.branch}` : '')); o.value = p.id; sel.append(o); }
    sel.value = String(project || '');
    const list = $('session-list'); list.replaceChildren();
    const mine = ov.sessions.filter(s => !project || s.project_id === project);
    if (!mine.length) list.append(el('p', 'fine', 'Your sessions show up here.'));
    for (const s of mine) {
      const row = el('div', `srow ${s.status}`); row.setAttribute('role', 'button'); row.tabIndex = 0;
      row.setAttribute('aria-current', String(s.id === current));
      const body = el('div', 'grow');
      body.append(el('span', 't', (s.origin === 'night' ? '🌙 ' : '') + s.title), el('span', 'm', `${NAME[s.engine] || s.engine} · ${ago(s.updated)}${s.review_rating != null ? ` · ${s.review_rating}/10` : ''}`));
      row.append(el('i', 'state ' + sessionState(s)), body);
      row.onclick = () => { open(s.id); closeDrawers(); };
      row.onkeydown = e => { if (e.key === 'Enter') row.click(); };
      list.append(row);
    }
  }
  $('project').onchange = e => { project = Number(e.target.value); store.set('apex.code.project', project); renderHome(); renderRail(); renderEngine(); loadBrain(); };

  // ---------------------------------------------------------------- what tends to happen here (agent/code_brain.track_record)
  // Counted from this project's sessions every time, never stored: a line shows only with
  // 5 sessions behind it and in at least 2 of every 5, always as "X of N", never a percentage.
  const recordFor = pid => (ov && ov.record && ov.record[pid]) || null;
  const capital = t => t ? t[0].toUpperCase() + t.slice(1) : '';
  function renderRecord() {
    const r = recordFor(project), p = ov.projects.find(x => x.id === project), box = $('record');
    box.hidden = !r || !p;
    if (box.hidden) return;
    $('record-title').textContent = `On ${p.name}`;
    $('record-lines').replaceChildren(...r.lines.map(x => {
      const row = el('div', 'rl ' + (x.key.startsWith('engine:') ? 'k-plan' : 'k-pattern'));   // k-: .plan is the header's chip
      row.title = `${x.hits} of ${x.n} sessions in the last 90 days, counted again every time`;
      row.append(el('i', '', x.key.startsWith('engine:') ? '◆' : '▲'), capital(x.text));
      return row;
    }));
    if (!r.lines.length) $('record-lines').append(el('p', 'fine', r.note));
  }
  function planLine() {                                   // the chosen plan's own line, for the hint
    const r = recordFor(current && detail ? detail.project_id : project);
    return r && r.engines ? r.engines[engine] || null : null;
  }

  // ---------------------------------------------------------------- the brief: plan, mode, voice
  function renderEngine() {
    for (const b of document.querySelectorAll('[data-engine]')) {
      const p = ov && ov.plans.find(x => x.id === b.dataset.engine);
      b.setAttribute('aria-checked', String(b.dataset.engine === engine));
      b.classList.toggle('resting', !!p && !p.ready);
      b.title = p ? (p.ready ? `${p.name}: ready` : `${p.name} ${p.why}`) : '';
    }
    for (const b of document.querySelectorAll('[data-mode]')) b.setAttribute('aria-checked', String(b.dataset.mode === mode));
    const sel = $('model'), catalog = catalogs[engine];
    const opts = catalog && catalog.models.length ? [['', 'Model: account default'], ...catalog.models.map(m => [m.id, m.label])]
      : (MODELS[engine] || MODELS.claude).filter(([value]) => value !== '__other');
    opts.push(['__other', 'Type a model…']);
    sel.title = catalog && catalog.error || 'Models from your signed-in coding client';
    const known = opts.some(([v]) => v === model);
    sel.replaceChildren(...opts.map(([v, t]) => { const o = el('option', '', t); o.value = v; return o; }));
    if (model && !known) { const o = el('option', '', model); o.value = model; sel.insertBefore(o, sel.lastChild); }
    sel.value = model || '';
    $('model-custom').hidden = true;
    const picked = catalog && catalog.models.find(m => m.id === model);
    const levels = picked && picked.efforts.length ? picked.efforts : engine === 'chatgpt' ? ['low', 'medium', 'high', 'xhigh'] : ['low', 'medium', 'high', 'max'];
    const effortOptions = ['', ...levels];
    if (effort && !effortOptions.includes(effort)) effortOptions.push(effort);
    $('effort').replaceChildren(...effortOptions.map(value => { const o = el('option', '', value ? `Effort: ${value}` : 'Effort: default'); o.value = value; return o; }));
    $('effort').value = effort || '';
    $('plan-toggle').setAttribute('aria-pressed', String(planFirst));
    const hint = $('hint'), p = ov && ov.plans.find(x => x.id === engine);
    let text = 'Every session works on its own branch. Nothing touches your project until you press Keep.', warn = false;
    if (p && !p.ready) { text = `Your ${p.name} ${p.why}. ${p.how || 'Pick the other plan.'}`; warn = true; }
    else if (detail && detail.status === 'ready' && detail.engine !== engine) { text = `Next message goes to your ${NAME[engine]}, with a recap of this session.`; warn = true; }
    else if (mode === 'full') { text = 'Full: Apex may run any command, inside this session\'s own copy of the project.'; warn = true; }
    else if (planLine()) text += ` ${capital(planLine().text)}.`;
    hint.textContent = text; hint.className = 'hint' + (warn ? ' warn' : '');
    const send = $('send'); send.classList.toggle('stop', busy());
    send.replaceChildren(busy() ? '■ Stop' : planFirst ? 'Plan it' : current ? 'Send' : 'Build it', ...(busy() ? [] : [el('kbd', '', 'Ctrl ⏎')]));
    send.classList.toggle('primary', !busy());
    const finished = detail && detail.status !== 'ready';
    $('prompt').disabled = !!finished; send.disabled = !!finished;
    $('prompt').placeholder = finished ? `This session is ${detail.status === 'kept' ? 'kept' : 'thrown away'}. Start a new one.`
      : current ? 'Ask for a change, a fix, or "explain…"' : 'Tell Apex what to build, fix or explain…';
  }
  for (const b of document.querySelectorAll('[data-engine]')) b.onclick = () => switchEngine(b.dataset.engine);
  for (const b of document.querySelectorAll('[data-mode]')) b.onclick = () => { mode = b.dataset.mode; store.set('apex.code.mode', mode); renderEngine(); };
  $('model').onchange = () => {
    if ($('model').value === '__other') { $('model-custom').hidden = false; $('model-custom').value = model; $('model-custom').focus(); return; }
    model = $('model').value;
    saveChoice();
    renderEngine();
  };
  $('model-custom').onchange = () => { model = $('model-custom').value.trim(); saveChoice(); renderEngine(); };
  $('effort').onchange = () => { effort = $('effort').value; saveChoice(); };
  $('plan-toggle').onclick = () => { planFirst = !planFirst; renderEngine(); };
  function autosize() { const p = $('prompt'); p.style.height = 'auto'; p.style.height = Math.min(p.scrollHeight + 2, innerHeight * 0.4) + 'px'; }
  $('prompt').addEventListener('input', autosize);
  $('prompt').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); $('brief-form').requestSubmit(); } });

  $('brief-form').onsubmit = async e => {
    e.preventDefault();
    if (busy()) return stop();
    const text = $('prompt').value.trim();
    if (!text) { $('prompt').focus(); return; }
    hideSuggest();
    if (text.startsWith('/')) { $('prompt').value = ''; autosize(); return slash(text); }
    if (text.startsWith('!')) {
      if (!current) { say('Start a session first: terminal commands run in its own copy.', 'error'); return; }
      $('prompt').value = ''; autosize(); return runTerminal(text.slice(1).trim());
    }
    $('send').disabled = true;
    const opts = {engine, mode, model, effort, plan: planFirst};
    try {
      if (!current) {
        if (!project) throw new Error('Add a project first.');
        const s = await post('/api/code/sessions', {project_id: project, prompt: text, ...opts});
        $('prompt').value = ''; autosize(); planFirst = false;
        await open(s.id);
      } else {
        detail = await post(`/api/code/sessions/${current}/messages`, {prompt: text, ...opts});
        $('prompt').value = ''; autosize(); planFirst = false; renderEngine(); schedule(300);
      }
    } catch (err) { say(err.message, 'error'); }
    $('send').disabled = false; renderEngine();
  };
  async function stop() {
    if (!current) return;
    try { await post(`/api/code/sessions/${current}/stop`); say('Stopping…'); schedule(300); } catch (err) { say(err.message, 'error'); }
  }

  // Talk instead of typing: recorded here, transcribed by Apex on this PC (no API credits).
  // One recording at a time, for the mic or for Celine; pressing its button again ends it.
  let rec = null;
  async function recordOnce(btn, hint) {                  // the recording, or null (nothing said, or no microphone)
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) { say('This browser can\'t record here. Type instead.', 'error'); return null; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); } catch (_) { say('The microphone is blocked for this page. Allow it, then try again.', 'error'); return null; }
    const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t)) || '';
    const chunks = [], mine = new MediaRecorder(stream, type ? {mimeType: type} : undefined);
    rec = mine;
    return new Promise(resolve => {
      mine.ondataavailable = ev => { if (ev.data.size) chunks.push(ev.data); };
      mine.onstop = () => {
        stream.getTracks().forEach(t => t.stop()); btn.classList.remove('rec');
        if (rec === mine) rec = null;
        const blob = new Blob(chunks, {type: mine.mimeType || type || 'audio/webm'});
        resolve(blob.size < 1500 ? null : blob);
      };
      mine.start(); btn.classList.add('rec'); say(hint);
      setTimeout(() => { if (mine.state === 'recording') mine.stop(); }, 90000);
    });
  }
  async function transcribe(blob) {
    const r = await fetch('/api/companion/transcribe?engine=local', {method: 'POST', body: blob, headers: {'Content-Type': blob.type, ...auth()}});
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `failed (${r.status})`);
    return (body.text || '').trim();
  }
  $('mic').onclick = async () => {
    if (rec) { rec.stop(); return; }
    const blob = await recordOnce($('mic'), 'Listening… tap the mic again when you\'re done.');
    if (!blob) return;
    say('Listening back…');
    try {
      const p = $('prompt'), text = await transcribe(blob);
      if (!text) { say('I didn\'t catch that. Try again, a little closer.'); return; }
      p.value = (p.value.trim() ? p.value.trimEnd() + ' ' : '') + text; autosize(); p.focus();
      say('Got it. Check it, then send.', 'good');
    } catch (err) { say('Could not transcribe: ' + err.message, 'error'); }
  };

  // ---------------------------------------------------------------- views
  function show(view) {
    document.body.classList.remove('view-home', 'view-session'); document.body.classList.add('view-' + view);
    $('home').hidden = view !== 'home'; $('session').hidden = view !== 'session';
    (view === 'home' ? $('home-slot') : $('session-slot')).append($('brief-form'));
  }
  async function open(id) {
    // The old session's stream, typing and voice end here, before anything is awaited:
    // its next step must never land in this feed (or move lastId), nor be said out loud.
    disconnect(); celineStop(); initial = true; liveSeen = -1; live = null;
    current = id; detail = null; lastId = 0; feed = {turn: null, root: $('feed'), yous: 0}; proof = null; proofDue = true; $('celine-card').hidden = true;
    $('feed').replaceChildren(); show('session');
    if (location.hash !== `#s=${id}`) history.replaceState(null, '', `#s=${id}`);
    try {
      await Promise.all([refreshDetail(), loadOverview()]);  // fresh plan status: one may have hit its limit since
      engine = detail.engine; mode = detail.mode; model = detail.model || ''; effort = detail.effort || ''; planFirst = false;
      saveChoice();
      files = null; live = null; liveSeen = -1; initial = true;
      const mine = ov && ov.plans.find(p => p.id === engine), other = ov && ov.plans.find(p => p.id === OTHER[engine]);
      if (mine && !mine.ready && other && other.ready) switchEngine(other.id);
      renderEngine(); await pull();
      if (!document.querySelector('[data-pane=rules]').hidden) renderRules();
    }
    catch (err) { say(err.message, 'error'); home(); return; }
    if (ov) renderRail();
    $('feed').scrollTop = $('feed').scrollHeight;
    initial = false;
    connect(id);
    schedule(400);
  }
  function home() {
    saveChoice();
    current = null; detail = null; files = null; proof = null; show('home'); disconnect(); celineStop(); $('celine-card').hidden = true;
    history.replaceState(null, '', location.pathname);
    engine = store.get('apex.code.engine') || (ov && ov.default_engine) || 'claude'; mode = store.get('apex.code.mode', 'safe');
    const saved = choices[engine] || {model: store.get(`apex.code.model.${engine}`, ''), effort: store.get(`apex.code.effort.${engine}`, '')};
    model = saved.model; effort = saved.effort;
    if (ov) { renderHome(); renderRail(); } renderEngine(); loadBrain();
    if (night && night.length) loadOvernight(false);       // the morning's strip stays, kept up to date
  }
  $('back').onclick = home;
  $('new-session').onclick = () => { home(); closeDrawers(); $('prompt').focus(); };

  // ---------------------------------------------------------------- the feed
  function newTurn(you) {
    const root = el('div', 'turn');
    if (you) {
      const row = el('div', 'you'), bubble = el('div', 'bubble'), who = el('div', 'who');
      row.append(el('div', 'avatar', ((ov && ov.owner) || 'You')[0].toUpperCase()), bubble);
      who.append(((ov && ov.owner) || 'You').toUpperCase(), el('span', 'badge ' + you.engine, NAME[you.engine] || you.engine));
      if (you.mode === 'full') who.append(el('span', 'badge full', 'FULL'));
      who.append(el('span', '', ago(you.ts)));
      bubble.append(who, el('div', 'text', you.text));
      if (!feed.yous++ && detail && detail.origin === 'night' && detail.task) {     // the night shift's first message
        const from = el('p', 'from-work'), a = el('a', '', detail.task.title || `task ${detail.task.id}`);
        a.href = '/work'; from.append('🌙 From your Work list: ', a); bubble.append(from);
      }
      root.append(row);
      if (you.correction && !ruled(you)) root.append(fixRow(you));
      if (you.brief && (you.brief.sources || []).length) root.append(briefCard(you));
    }
    const tl = el('div', 'timeline'); root.append(tl);
    feed.root.append(root);
    feed.turn = {root, tl, reads: null, todo: null, lastProse: null, lastText: '', tools: {}, review: null, checks: null};
    return feed.turn;
  }
  const turn = () => feed.turn || newTurn(null);
  function step(cls, ...kids) {
    const s = el('div', 'step ' + cls); s.append(...kids);
    s.dataset.label = kids.map(k => typeof k === 'string' ? k : k.textContent).join(' ').replace(/\s+/g, ' ').trim();
    turn().tl.append(s); return s;
  }
  const ic = t => el('span', 'ic', t);

  // ---------------------------------------------------------------- what Apex knows about you (agent/code_brain.py)
  // A new conversation starts with it: Claude as its system prompt, ChatGPT ahead of the
  // first message. The card under that message lists exactly what went, by where it came
  // from; a memory can be forgotten from there, and from the home page's chip.
  const BRAIN = [['profile', 'About you'], ['standing', 'Standing rules'], ['rule', 'Your rules for this project'],
    ['handoff', 'Project handoff'], ['record', 'Measured on this project'], ['memory', 'How you like your code'], ['session', 'Recent sessions here']];
  const brainTag = x => x.kind === 'memory' || x.kind === 'session' ? `#${x.ref}` : x.kind === 'rule' ? `rule ${x.ref}`
    : x.kind === 'handoff' ? (x.ref === 'next_step' ? 'next step' : 'decisions') : '';
  function brainList(sources, afterForget) {
    const box = el('div', 'brain-list');
    for (const [kind, label] of BRAIN) {
      const items = sources.filter(x => x.kind === kind);
      if (!items.length) continue;
      const group = el('div', 'bg'), head = el('div', 'bh', label);
      if (kind === 'rule') head.append(button('Edit rules', editRules, 'link'));
      group.append(head);
      for (const x of items) {
        const row = el('div', 'bl k-' + kind), tag = brainTag(x);   // k-: .session is the page's own
        if (tag) row.append(el('span', 'tag', tag));
        row.append(el('span', 'bt', x.text));
        if (kind === 'memory' && x.ref != null) {
          row.dataset.ref = String(x.ref);
          row.append(button('Forget', () => forgetMemory(x.ref, afterForget), 'small forget'));
        }
        group.append(row);
      }
      box.append(group);
    }
    return box;
  }
  function briefCard(you) {
    const n = you.brief.sources.length, d = el('details', 'more brain');
    d.append(el('summary', '', `🧠 What Apex told your ${NAME[you.engine] || you.engine} about you · ${n} thing${n === 1 ? '' : 's'}`),
      brainList(you.brief.sources));
    return d;
  }
  async function forgetMemory(id, after) {
    try {
      await api(`/api/memories/${encodeURIComponent(id)}`, {method: 'DELETE'});
      for (const row of document.querySelectorAll(`.bl.k-memory[data-ref="${CSS.escape(String(id))}"]`)) {   // in the feed and the dialog
        row.classList.add('gone'); const b = row.querySelector('.forget'); if (b) b.disabled = true;
      }
      say('Forgotten. The next message will refresh memory.', 'good');
      if (after) after();
    } catch (err) { say(err.message, 'error'); }
  }
  function editRules() {                                  // the Rules tab of a session, where there is one
    const t = document.querySelector('[data-tab=rules]');
    if (!t || !current) { say('Open a session in this project to edit its rules.'); return; }
    if ($('brain-dialog').open) $('brain-dialog').close();
    t.click();
  }
  let brain = null;                                       // what a new session in the chosen project would hear
  async function loadBrain() {
    const pid = project;
    if (!pid) { $('brain-chip').hidden = true; return; }
    try { const b = await api(`/api/code/projects/${pid}/brain`); if (pid === project) { brain = b; renderBrainChip(); } }
    catch (err) { $('brain-chip').hidden = true; console.warn('[Code] what Apex knows did not load:', err.message); }
  }
  function renderBrainChip() {
    const p = ov && ov.projects.find(x => x.id === project), n = brain ? brain.sources.length : 0, name = p ? p.name : 'this project';
    $('brain-chip').replaceChildren(el('i', '', '🧠'), n ? `Apex knows ${n} thing${n === 1 ? '' : 's'} for ${name}` : `Apex knows nothing yet for ${name}`);
    $('brain-chip').hidden = false;
  }
  async function openBrain() {
    const pid = current && detail ? detail.project_id : project, p = ov && ov.projects.find(x => x.id === pid);
    $('brain-sub').textContent = `What a new session in ${p ? p.name : 'this project'} starts out knowing: your Claude plan gets it as its system prompt, `
      + 'your ChatGPT plan ahead of your first message. Keys and passwords are taken out first.';
    $('brain-body').replaceChildren(el('p', 'fine', 'Loading…'));
    if (!$('brain-dialog').open) $('brain-dialog').showModal();
    if (!pid) { $('brain-body').replaceChildren(el('p', 'fine', 'Add a project first.')); return; }
    try {
      const b = await api(`/api/code/projects/${pid}/brain`);
      if (pid === project) { brain = b; renderBrainChip(); }
      $('brain-body').replaceChildren(b.sources.length ? brainList(b.sources, loadBrain)
        : el('p', 'empty', 'Tell Celine or Apex chat how you like your code; it shows up here.'));
      if ((b.unvouched || []).length) $('brain-body').append(unvouchedList(b.unvouched));
    } catch (err) { $('brain-body').replaceChildren(el('p', 'fine', err.message)); }
  }
  // Coding memories saved from a chat, a device or a tool call, or before Apex kept track of
  // who saved what. A page or a paired phone can plant one, so none reaches your plan until
  // you say it's yours here (agent/code_brain.unvouched, POST /api/code/memories/{id}/vouch).
  function unvouchedList(items) {
    const group = el('div', 'bg unvouched');
    group.append(el('div', 'bh', 'Not told until you OK it'),
      el('p', 'fine', 'Saved from a chat, a device or before Apex kept track. Apex only tells your plan what you vouch for.'));
    for (const x of items) {
      const row = el('div', 'bl k-memory'), use = button('It\'s mine, use it', async () => {
        use.disabled = true;
        try {
          await api(`/api/code/memories/${encodeURIComponent(x.ref)}/vouch`, {method: 'POST'});
          say('Approved. The next message will refresh memory.', 'good');
          openBrain(); loadBrain();
        } catch (err) { use.disabled = false; say(err.message, 'error'); }
      }, 'small vouch');
      row.dataset.ref = String(x.ref);
      row.append(el('span', 'tag', `#${x.ref}`), el('span', 'bt', x.text), use,
        button('Forget', () => forgetMemory(x.ref, openBrain), 'small forget'));
      group.append(row);
    }
    const box = el('div', 'brain-list'); box.append(group);
    return box;
  }
  $('brain-chip').onclick = openBrain;
  $('brain-close').onclick = () => $('brain-dialog').close();

  // ---------------------------------------------------------------- waiting for your OK (agent/code_brain.suggested)
  // A plan can suggest a memory through Apex's memory server (remember). It is only staged:
  // it shows here, for this project, until you approve it, reject it, or save your own
  // wording of it. Once approved, the next session's brief can bring it back.
  let waiting = [];
  const WAIT_KIND = {fact: 'fact', preference: 'preference', project: 'project', decision: 'decision', note: 'note'};
  async function loadWaiting() {
    try { waiting = (await api('/api/code/approvals')).items || []; }
    catch (err) { console.warn('[Code] the memories waiting for your OK did not load:', err.message); return; }
    renderWaiting();
  }
  function renderWaiting() {
    const box = $('box-waiting'), list = $('wait-list');
    const mine = detail ? waiting.filter(x => x.project_id === detail.project_id) : [];
    box.hidden = !mine.length;
    $('wait-count').textContent = mine.length ? String(mine.length) : '';
    if (list.querySelector('.wm.editing')) return;              // never wipe what you are typing
    list.replaceChildren(...mine.map(waitRow));
  }
  function waitRow(x) {
    const row = el('div', 'wm'), head = el('div', 'wh');
    row.dataset.id = String(x.id);
    head.append(el('span', 'wk', WAIT_KIND[x.kind] || 'note'),
      el('span', 'muted', x.session_id === current ? 'from this session' : x.session_id ? `from session ${x.session_id}` : 'from a session'));
    row.append(head, el('p', 'wt', x.content));
    if (x.why) row.append(el('p', 'fine why', `Why: ${x.why}`));
    const acts = el('div', 'acts');
    acts.append(button('Approve', () => decideWaiting(x, 'approve'), 'small primary'), button('Reject', () => decideWaiting(x, 'reject'), 'small'),
      button('Edit', () => editWaiting(row, x), 'small'));
    row.append(acts);
    return row;
  }
  async function decideWaiting(x, how) {
    try {
      const r = await post(`/api/staged-writes/${encodeURIComponent(x.id)}/${how}`);
      if (how === 'approve' && !/^Approved/.test(r.result || '')) throw new Error(r.result || r.error || 'It was not saved.');
      say(how === 'approve' ? 'Saved to Apex\'s memory. The next session here hears it.' : 'Rejected. Nothing was saved.', how === 'approve' ? 'good' : '');
      if (how === 'approve') loadBrain();
    } catch (err) { say(err.message, 'error'); }
    await loadWaiting();
  }
  function editWaiting(row, x) {                              // your wording: the suggestion is rejected, yours is saved
    row.classList.add('editing'); row.replaceChildren();
    const area = el('textarea'); area.value = x.content; area.maxLength = 1000; area.rows = 3; area.setAttribute('aria-label', 'Your wording of this memory');
    const save = button('Save my version', async () => {
      const text = area.value.trim();
      if (text.length < 5) { say('A memory is 5 to 1000 characters.', 'error'); return; }
      save.disabled = true;
      try {
        await post(`/api/staged-writes/${encodeURIComponent(x.id)}/reject`);
        await post('/api/memories', {content: text, kind: x.kind || 'note', tags: x.tags || 'from-mcp,code'});
        say('Saved your version. The next session here hears it.', 'good');
        row.classList.remove('editing'); loadBrain(); await loadWaiting();
      } catch (err) { save.disabled = false; say(err.message, 'error'); }
    }, 'small primary');
    const cancel = button('Cancel', () => { row.classList.remove('editing'); renderWaiting(); }, 'small');
    const bar = el('div', 'acts'); bar.append(save, cancel);
    row.append(area, bar); area.focus();
  }

  // ---------------------------------------------------------------- rules: say it once (agent/code_brain.py)
  // A reply that corrects the agent ("no, never…") gets two chips under it: keep it as a
  // rule for this project, or for all your code. Every later session, the second opinion
  // and Celine follow it, and the session you're in hears it ahead of its next message.
  // The Rules tab lists them, with on/off, edit, delete and earlier versions.
  const RULE_MAX = 500;
  const RULE_SAVED = {project: 'Rule saved. Every session here follows it; this one hears it on its next turn.',
    all: 'Rule saved for all your code. Every session follows it; this one hears it on its next turn.'};
  const ruledKey = 'apex.code.ruled';                     // corrections already answered, on this browser
  const ruledList = () => { try { return JSON.parse(store.get(ruledKey, '[]')); } catch (_) { return []; } };
  const ruled = you => current != null && ruledList().includes(`${current}:${you.id}`);
  function markRuled(you) { if (current != null) store.set(ruledKey, JSON.stringify([...ruledList(), `${current}:${you.id}`].slice(-200))); }
  const rulesPid = () => (current && detail ? detail.project_id : project);
  const rulesUrl = pid => `/api/code/projects/${pid}/rules`;
  async function addRule(pid, text, scope) {                 // the revision is read just before, so a tap rarely clashes
    const {revision} = await api(rulesUrl(pid));
    const r = await post(rulesUrl(pid), {text, scope, revision});
    if (rules && rules.pid === pid) { rules = {pid, ...r}; drawRules(); }
    loadBrain();
    return r;
  }
  function counter(area, out) { const n = () => { out.textContent = `${area.value.length}/${RULE_MAX}`; out.classList.toggle('over', area.value.length > RULE_MAX); }; area.addEventListener('input', n); n(); }
  function fixRow(you) {
    const box = el('div', 'fix'), chips = el('div', 'fix-chips');
    const name = (detail && detail.project) || 'this project';
    chips.append(el('span', 'fix-q', 'Say it once?'),
      button(`Make this a rule for ${name}`, () => draft('project'), 'chip'),
      button('Remember for all my code', () => draft('all'), 'chip'),
      button('✕', () => { markRuled(you); box.remove(); }, 'chip x'));
    chips.lastChild.setAttribute('aria-label', 'Not a rule');
    box.append(chips);
    function draft(scope) {
      const area = el('textarea'), count = el('span', 'count'), save = button('Save rule', go, 'primary small');
      area.rows = 2; area.maxLength = RULE_MAX; area.value = you.text.slice(0, RULE_MAX); area.setAttribute('aria-label', 'The rule');
      counter(area, count);
      const bar = el('div', 'fix-bar');
      bar.append(el('span', 'fine', scope === 'all' ? 'For all your code' : `For ${name}`), count, el('span', 'grow'), button('Cancel', () => { form.remove(); chips.hidden = false; }, 'small'), save);
      const form = el('div', 'fix-draft'); form.append(area, bar);
      chips.hidden = true; box.append(form); area.focus();
      async function go() {
        const text = area.value.trim();
        if (!text) { say('Write the rule first.', 'error'); return; }
        const pid = detail ? detail.project_id : project;
        save.disabled = true;
        try {
          await addRule(pid, text, scope);
          markRuled(you); box.replaceChildren(el('p', 'fine saved', '✓ ' + RULE_SAVED[scope])); say(RULE_SAVED[scope], 'good');
        } catch (err) { say(err.message, 'error'); save.disabled = false; }      // the text stays in the box
      }
    }
    return box;
  }

  let rules = null;                                       // {pid, items, revision, global}: the Rules tab
  async function renderRules() {
    const pid = rulesPid();
    renderLog();
    if (!pid) { $('rules-list').replaceChildren(el('p', 'fine', 'Add a project first.')); return; }
    try { rules = {pid, ...await api(rulesUrl(pid))}; } catch (err) { $('rules-list').replaceChildren(el('p', 'fine', err.message)); return; }
    drawRules(); if ($('rules-history').open) loadVersions();
  }
  function drawRules(reopen) {                            // reopen: {was, text} an edit to keep after a reload
    const name = (detail && detail.project_id === rules.pid && detail.project) || (ov && (ov.projects.find(p => p.id === rules.pid) || {}).name) || 'this project';
    $('rules-sub').textContent = `Every new session in ${name} follows these, and the second opinion checks them. A session already open hears a change ahead of its next message.`;
    const list = $('rules-list');
    list.replaceChildren(...rules.items.map((r, i) => ruleRow(r, i)));
    if (!rules.items.length) list.append(el('p', 'empty', 'No rules yet. Correct Apex in a session ("No, never…") and keep it as a rule, or add one here.'));
    $('rules-count').textContent = rules.items.length ? `${rules.items.filter(r => r.active).length} on · ${rules.items.length}/20` : '';
    if (reopen) {
      const i = rules.items.findIndex(r => r.text === reopen.was);
      if (i >= 0) editRule(i, reopen.text); else { $('rules-new').value = reopen.text; }
    }
    const g = $('rules-global');
    g.replaceChildren(...rules.global.map(x => {
      const row = el('div', 'rule global'); row.dataset.ref = String(x.id);
      row.append(el('span', 'rt', x.text), button('Forget', () => forgetRule(x.id), 'small forget'));
      return row;
    }));
    if (!rules.global.length) g.append(el('p', 'fine', 'None yet. "Remember for all my code" under a correction puts one here.'));
  }
  function ruleRow(r, i) {
    const row = el('div', 'rule' + (r.active ? '' : ' off')), on = el('input');
    on.type = 'checkbox'; on.checked = r.active; on.setAttribute('aria-label', r.active ? 'On: turn this rule off' : 'Off: turn this rule on');
    on.onchange = () => saveRules(rules.items.map((x, j) => j === i ? {...x, active: on.checked} : x), on.checked ? 'Rule on.' : 'Rule off. Sessions stop hearing it.');
    row.append(on, el('span', 'rt', r.text), button('Edit', () => editRule(i), 'small'),
      button('Delete', () => saveRules(rules.items.filter((_, j) => j !== i), 'Rule deleted. Earlier versions can bring it back.'), 'small danger'));
    return row;
  }
  function editRule(i, text) {
    const row = $('rules-list').children[i], r = rules.items[i];
    const area = el('textarea'), count = el('span', 'count'), bar = el('div', 'fix-bar');
    area.rows = 2; area.maxLength = RULE_MAX; area.value = text ?? r.text; area.setAttribute('aria-label', 'Edit the rule');
    counter(area, count);
    bar.append(count, el('span', 'grow'), button('Cancel', () => drawRules(), 'small'),
      button('Save', () => { const t = area.value.trim(); if (!t) { say('A rule needs words. Delete it instead.', 'error'); return; }
        saveRules(rules.items.map((x, j) => j === i ? {...x, text: t} : x), 'Rule saved.', {was: r.text, text: area.value}); }, 'primary small'));
    row.className = 'rule editing'; row.replaceChildren(area, bar); area.focus();
  }
  async function saveRules(items, okText, keep) {
    const pid = rules.pid;
    try { rules = {pid, ...await put(rulesUrl(pid), {items, revision: rules.revision})}; drawRules(); say(okText, 'good'); loadBrain(); if ($('rules-history').open) loadVersions(); }
    catch (err) {
      say(err.message, 'error');
      if (err.status === 409) { try { rules = {pid, ...await api(rulesUrl(pid))}; } catch (_) {} drawRules(keep); }      // reloaded; your text is kept
      else if (!keep) drawRules();
    }
  }
  $('rules-add').onsubmit = async e => {
    e.preventDefault();
    const text = $('rules-new').value.trim();
    if (!text || !rules) return;
    const pid = rules.pid;
    try {
      rules = {pid, ...await post(rulesUrl(pid), {text, scope: 'project', revision: rules.revision})};
      $('rules-new').value = ''; drawRules(); say(RULE_SAVED.project, 'good'); loadBrain(); if ($('rules-history').open) loadVersions();
    } catch (err) {
      say(err.message, 'error');                                                    // the text stays in the box
      if (err.status === 409) { try { rules = {pid, ...await api(rulesUrl(pid))}; drawRules(); } catch (_) {} }
    }
  };
  async function forgetRule(id) {
    try { await api(`/api/memories/${encodeURIComponent(id)}`, {method: 'DELETE'}); say('Forgotten. The next message will refresh memory.', 'good'); await renderRules(); loadBrain(); }
    catch (err) { say(err.message, 'error'); }
  }
  async function loadVersions() {
    const box = $('rules-versions');
    if (!rules) return;
    try {
      const {versions} = await api(`${rulesUrl(rules.pid)}/history`);
      const older = versions.filter(v => v.revision !== rules.revision);
      box.replaceChildren(...older.map(v => {
        const row = el('div', 'version'), on = v.items.filter(x => x.active).length, head = el('div', 'vh');
        head.append(el('b', '', `Version ${v.revision}`), el('span', 'muted', `${ago(v.updated)} · ${v.items.length} rule${v.items.length === 1 ? '' : 's'}, ${on} on`),
          el('span', 'grow'), button('Restore', () => restoreRules(v.revision), 'small'));
        const ul = el('ul');
        for (const x of v.items) ul.append(el('li', x.active ? '' : 'off', x.text));
        row.append(head, ul); return row;
      }));
      if (!older.length) box.append(el('p', 'fine', 'No earlier versions yet: each save keeps one.'));
    } catch (err) { box.replaceChildren(el('p', 'fine', err.message)); }
  }
  $('rules-history').addEventListener('toggle', () => { if ($('rules-history').open) loadVersions(); });

  // The project's decision log: a line for every session kept or thrown away (agent/code_brain.write_back),
  // which every later session here reads in its brief. Read-only; History brings back an earlier version.
  const logUrl = pid => `/api/code/projects/${pid}/decisions`;
  const logLines = text => String(text || '').split('\n').filter(l => l.trim());
  let decisionLog = null;                                 // {pid, decisions, revision}
  async function renderLog() {
    const pid = rulesPid();
    if (!pid) { $('log-lines').replaceChildren(); return; }
    try { decisionLog = {pid, ...await api(logUrl(pid))}; } catch (err) { $('log-lines').replaceChildren(el('p', 'fine', err.message)); return; }
    drawLog(); if ($('log-history').open) loadLogVersions();
  }
  function drawLog() {
    const lines = logLines(decisionLog.decisions).reverse();                       // newest first
    $('log-lines').replaceChildren(...lines.map(l => el('div', 'log-line', l)));
    if (!lines.length) $('log-lines').append(el('p', 'fine', 'Nothing yet. Keep or throw away a session and Apex writes a line here, for every later session to read.'));
  }
  async function loadLogVersions() {
    const box = $('log-versions');
    if (!decisionLog) return;
    try {
      const {versions} = await api(`${logUrl(decisionLog.pid)}/history`);
      const older = versions.filter(v => v.revision !== decisionLog.revision);
      box.replaceChildren(...older.map(v => {
        const row = el('div', 'version'), head = el('div', 'vh'), lines = logLines(v.decisions);
        head.append(el('b', '', `Version ${v.revision}`), el('span', 'muted', `${ago(v.updated)} · ${lines.length} line${lines.length === 1 ? '' : 's'}`),
          el('span', 'grow'), button('Restore', () => restoreLog(v.revision), 'small'));
        const ul = el('ul');
        for (const l of lines.slice(-3).reverse()) ul.append(el('li', '', l));
        row.append(head, ul); return row;
      }));
      if (!older.length) box.append(el('p', 'fine', 'No earlier versions yet: each session that ends keeps one.'));
    } catch (err) { box.replaceChildren(el('p', 'fine', err.message)); }
  }
  $('log-history').addEventListener('toggle', () => { if ($('log-history').open) loadLogVersions(); });
  async function restoreLog(revision) {
    const pid = decisionLog.pid;
    try {
      decisionLog = {pid, ...await post(`${logUrl(pid)}/restore`, {revision, current_revision: decisionLog.revision})};
      drawLog(); loadLogVersions(); loadBrain(); say(`Version ${revision} of the decision log is back.`, 'good');
    } catch (err) { say(err.message, 'error'); if (err.status === 409) renderLog(); }
  }
  async function restoreRules(revision) {
    const pid = rules.pid;
    try { rules = {pid, ...await post(`${rulesUrl(pid)}/restore`, {revision, current_revision: rules.revision})}; drawRules(); loadVersions(); loadBrain(); say(`Version ${revision} is back.`, 'good'); }
    catch (err) { say(err.message, 'error'); if (err.status === 409) renderRules(); }
  }

  function render(e) {
    const t = e.kind === 'you' ? null : turn();
    if (t && !['result', 'tool'].includes(e.kind)) t.reads = null;
    switch (e.kind) {
      case 'you': newTurn(e); break;
      case 'note': step('note', '· ' + e.text); break;
      case 'thinking': { const d = el('details', 'more step thinking'); d.append(el('summary', '', 'Thinking'), el('p', '', e.text)); t.tl.append(d); break; }
      case 'text': { const s = step('prose'); s.append(md(e.text)); t.lastProse = s; t.lastText = e.text; break; }
      case 'tool':
        if (e.tool === 'read' || e.tool === 'search') {
          if (!t.reads) { t.reads = step('reads'); t.reads.items = []; t.reads.head = el('span'); t.reads.chips = el('div', 'chips'); t.reads.append(ic('◇'), t.reads.head, t.reads.chips); }
          t.reads.items.push(e);
          const n = t.reads.items.filter(x => x.tool === 'read').length, q = t.reads.items.length - n;
          t.reads.head.textContent = [n && `Read ${n} file${n > 1 ? 's' : ''}`, q && `searched ${q} time${q > 1 ? 's' : ''}`].filter(Boolean).join(', ');
          t.reads.dataset.label = `${t.reads.head.textContent}: ${e.path || e.title}`;
          t.reads.chips.append(el('span', '', e.path || e.title.replace(/^Searched for /, '⌕ ')));
        } else if (e.tool === 'command') {
          t.reads = null;
          const s = step('cmd'), line = el('div', 'line');
          line.append(el('span', 'sym', '$'), el('span', 'c', e.title), el('span', 'st muted', '…'));
          line.title = e.detail || e.title; s.dataset.label = `Running ${e.title}`;
          copyLine(line, e.title); s.append(line); if (e.ref) { t.tools[e.ref] = s; s.dataset.ref = e.ref; }
        } else if (e.tool === 'memory') {                 // it asked Apex's memory, or suggested a memory
          t.reads = null;
          const s = step('memory', ic('🧠'), el('span', 'mt', e.title), el('span', 'st muted', '…'));
          if (e.ref) { t.tools[e.ref] = s; s.dataset.ref = e.ref; }
        } else { t.reads = null; step('', ic(e.tool === 'web' ? '◌' : '·'), e.title); }
        break;
      case 'result': {
        const s = t.tools[e.ref];
        if (s) {
          const st = s.querySelector('.st');
          st.className = 'st ' + (e.ok ? 'ok' : 'bad');
          st.textContent = e.ok ? '✓' : (e.exit_code != null ? `✗ exit ${e.exit_code}` : '✗');
          if (e.output) { const d = el('details', 'more'); if (!e.ok) d.open = true; d.append(el('summary', '', 'Output'), withCopy(el('pre', 'out', e.output), () => e.output)); s.append(d); }
        } else if (!e.ok && e.output) step('error', ic('✗'), e.output.split('\n')[0].slice(0, 300));
        break;
      }
      case 'file': {
        const s = step('file');
        const label = {add: '+', delete: '−', write: '✎', update: '✎'}[e.change] || '✎';
        s.append(ic(label), el('span', 'p', e.path));
        if (e.plus != null) s.append(el('span', 'plus', `+${e.plus}`), el('span', 'minus', `−${e.minus || 0}`));
        if (e.change === 'add') s.append(el('span', 'muted', '  new file'));
        if (e.change === 'delete') s.append(el('span', 'muted', '  deleted'));
        s.querySelector('.p').onclick = () => openDiff(e.path);
        if (e.diff) s.append(inlineDiff(e.diff));
        break;
      }
      case 'image': {
        const card = step('image');
        card.append(button(`Open generated image: ${e.path}`, () => openFile(e.path)));
        api(`/api/code/sessions/${current}/file?path=${encodeURIComponent(e.path)}`).then(file => {
          if (!file.image || !['image/png', 'image/jpeg', 'image/webp'].includes(file.image.mime)) return;
          const image = el('img'); image.alt = e.path;
          image.src = `data:${file.image.mime};base64,${file.image.base64}`;
          image.style.maxWidth = '100%'; image.style.maxHeight = '420px';
          card.append(image);
        }).catch(error => card.append(el('p', 'fine', error.message)));
        break;
      }
      case 'todo': {
        const box = el('div', 'todo');
        for (const it of e.items) { const r = el('div', 'ti' + (it.done ? ' done' : it.active ? ' active' : '')); r.append(el('b', '', it.done ? '✓' : it.active ? '▸' : '○'), it.text); box.append(r); }
        if (t.todo) t.todo.replaceWith(box); else t.tl.append(box);
        t.todo = box; break;
      }
      case 'blocked': {
        const s = step('blocked', ic('⛔'), `Safe mode stopped: ${e.title}`);
        if (e.command && current && detail && detail.status === 'ready') {
          const acts = el('div', 'acts');
          acts.append(button('Allow once', () => allowCmd(e.command, false), 'primary'),
            button('Always allow in this project', () => allowCmd(e.command, true)));
          // Away mode: Apex also asked on your phone, where one tap allows it once (or says no).
          if (e.allow_id) acts.append(el('span', 'sent-phone', '📱 Sent to your phone'));
          s.append(acts);
        }
        break;
      }
      case 'term': {
        const line = el('div', 'line');
        line.append(el('span', 'sym you-sym', '❯'), el('span', 'c', e.command), el('span', 'st muted', '…'));
        copyLine(line, e.command);
        const s = step('cmd term', line); s.dataset.ref = e.ref; s.dataset.label = `You ran ${e.command}`;
        t.tools[e.ref] = s; termLog(e); break;
      }
      case 'term_done': {
        const s = t.tools[e.ref] || $('feed').querySelector(`[data-ref="${CSS.escape(e.ref)}"]`);
        if (s) {
          const st = s.querySelector('.st'); st.className = 'st ' + (e.exit_code === 0 ? 'ok' : 'bad');
          st.textContent = e.exit_code === 0 ? `✓ ${clock(e.seconds || 0)}` : `✗ exit ${e.exit_code ?? '?'}`;
          s.querySelectorAll('.live-out').forEach(x => x.remove());
          if (e.output) { const d = el('details', 'more'); d.open = true; d.append(el('summary', '', 'Output'), withCopy(el('pre', 'out', e.output), () => e.output)); s.append(d); }
        }
        termLog(e); break;
      }
      case 'pushed': step(e.ok ? 'mark' : 'error', e.ok ? `⇡ ${e.text}` : e.text); break;
      case 'error': step('error', ic('✗'), e.text); break;
      case 'checkpoint': step('mark', e.catch_up ? 'Caught up with your latest work' : `Checkpoint · ${e.files} file${e.files === 1 ? '' : 's'} saved on the session's branch`); break;
      case 'undo': step('mark', `↶ Undid a step (${e.files} file${e.files === 1 ? '' : 's'})`); break;
      case 'owner_evidence': step('mark', `You reported: ${e.evidence || 'output without a test count'} (pasted; Apex didn't see it run)`); break;
      case 'conflict': {
        const c = el('div', 'card2 warnish'), h = el('div', 'h');
        h.append('⚠ Your latest work clashes with this session');
        c.append(h, el('p', 'muted', `In: ${e.files.join(', ')}. Apex can sort it out: it keeps what both sides meant.`));
        const acts = el('div', 'acts'), fix = el('button', 'primary', 'Ask Apex to fix them');
        fix.onclick = () => sendText(`Resolve the merge conflicts in: ${e.files.join(', ')}. Keep what both sides intended, remove every conflict marker, make sure it still works, then summarise what you decided.`);
        acts.append(fix); c.append(acts); t.tl.append(c); break;
      }
      case 'done': renderDone(e, t); feed.turn = null; break;
      case 'review_started': {
        const s = step('', ic('⚖'), `Second opinion from your ${NAME[e.engine]}…`);
        t.review = s; break;
      }
      case 'review_step': if (t.review) { const d = el('div', 'muted', '  ' + e.title); d.style.fontSize = '12px'; t.review.append(d); } break;
      case 'review': renderReview(e, t); feed.turn = null; break;
      case 'checks_started': {
        const line = el('div', 'line');
        line.append(el('span', 'sym', '▶'), el('span', 'c', `Checks: ${e.command}`), el('span', 'st muted', '…'));
        t.checks = step('cmd', line); copyLine(line, e.command); break;
      }
      case 'checks': {                                   // passed, failed, or unknown: it couldn't say
        const s = t.checks || step('cmd'), state = e.state || (e.passed ? 'passed' : 'failed');
        if (!t.checks) { const line = el('div', 'line'); line.append(el('span', 'sym', '▶'), el('span', 'c', `Checks: ${e.command}`), el('span', 'st')); copyLine(line, e.command); s.append(line); }
        const st = s.querySelector('.st'); st.className = 'st ' + (CHECK_LOOK[state] || CHECK_LOOK.unknown)[1];
        st.textContent = `${(CHECK_LOOK[state] || CHECK_LOOK.unknown)[0]} · ${clock(e.seconds)}`;
        if (e.why) s.append(el('div', 'why', e.why));
        if (e.output) { const d = el('details', 'more'); if (state !== 'passed') d.open = true; d.append(el('summary', '', 'Output'), withCopy(el('pre', 'out', e.output), () => e.output)); s.append(d); }
        t.checks = null; feed.turn = null; break;
      }
      case 'kept': {
        const c = el('div', 'card2 kept'), h = el('div', 'h');
        h.append(`✓ Kept: merged into ${e.into} (${e.files} file${e.files === 1 ? '' : 's'})`);
        c.append(h, el('p', 'muted', e.restart ? 'Your Apex has it now. Restart Apex to run it.' : 'It\'s in your project now.'));
        if (e.unverified) c.append(el('p', 'muted unproved', `Kept without proof (${e.proof || 'unverified'}): you chose Keep anyway.`));
        else if (e.proof === 'proved') c.append(el('p', 'muted', '✓ Proved: Apex\'s own checks passed on what was kept.'));
        if (e.learned) c.append(learnedLine(e.learned));
        t.tl.append(c); break;
      }
      case 'draft': renderDraft(e, t); break;
      case 'discarded': {
        const why = REASONS[e.reason], s = step('mark', `Thrown away${why ? ` (${why.toLowerCase()})` : ''}: the session's branch and copy are gone. Your project never changed.`);
        if (e.learned) s.append(learnedLine(e.learned));
        break;
      }
      default: break;
    }
  }
  // A message Celine drafted for the plan: only ever sent by you (Send), or put in the box to change (Edit).
  const drafts = () => { try { return JSON.parse(store.get('apex.code.drafts', '{}')) || {}; } catch (_) { return {}; } };
  function markDraft(id, how) { const d = drafts(), keys = Object.keys(d); if (keys.length > 300) delete d[keys[0]]; d[id] = how; store.set('apex.code.drafts', JSON.stringify(d)); }
  function renderDraft(e, t) {
    const how = drafts()[e.id];
    if (how === 'dismissed') return;
    const c = el('div', 'card2 draft'), h = el('div', 'h');
    h.append(`🎙 ${e.by || 'Celine'} drafted a message`, el('span', 'st', how === 'sent' ? 'sent' : 'not sent'));
    c.append(h, el('div', 'dtext', e.text));
    if (how !== 'sent') {
      const acts = el('div', 'acts');
      acts.append(button('Send', async () => {
        if (busy()) { say('Apex is still working in this session. Wait, or press Stop.', 'error'); return; }
        if (await sendText(e.text)) { markDraft(e.id, 'sent'); acts.remove(); h.querySelector('.st').textContent = 'sent'; }
      }, 'primary'),
      button('Edit', () => { $('prompt').value = e.text; autosize(); $('prompt').focus(); say('In the box: change it, then send.'); }),
      button('✕', () => { markDraft(e.id, 'dismissed'); c.remove(); }, 'ghost'));
      acts.lastChild.title = 'Dismiss: nothing is sent'; acts.lastChild.setAttribute('aria-label', 'Dismiss the draft');
      c.append(acts);
    }
    t.tl.append(c);
  }
  const CHECK_LOOK = {passed: ['✓ passed', 'ok'], failed: ['✗ failed', 'bad'], unknown: ['? unknown', 'warn']};
  // What a finished session wrote into Apex (agent/code_brain.write_back): each place, or why not.
  const REASONS = {changed_mind: 'Changed my mind', wrong: 'It was wrong', poor: 'Poor quality', superseded: 'Something better came along'};
  const LEARNED = [['decision', 'decision log', 'couldn\'t write the decision log'], ['daily', 'today\'s note', 'couldn\'t write today\'s note'],
    ['outcome', 'outcome', 'couldn\'t record the outcome']];
  function learnedLine(learned) {
    const p = el('div', 'learned'); p.append('Saved to Apex: ');
    LEARNED.forEach(([key, ok, bad], i) => { if (i) p.append(' · '); p.append(el('span', learned[key] ? 'ok' : 'bad', learned[key] ? `${ok} ✓` : bad)); });
    return p;
  }
  const DONE = {done: ['✓ Done', 'done-ok'], stopped: ['■ Stopped', ''], limited: ['⚠ Plan limit reached', 'warnish'],
    signed_out: ['⚠ Plan not signed in', 'warnish'], missing: ['⚠ Plan not set up', 'warnish'], failed: ['✗ Stopped with an error', 'done-bad'],
    interrupted: ['⚠ Interrupted', 'warnish']};
  function renderDone(e, t) {
    if (!initial) chime(e.status);
    if (e.plan && e.status === 'done') {                    // Plan first: the plan, then Build it
      if (t.lastProse) t.lastProse.classList.add('final');
      const c = el('div', 'card2 done-ok'), h = el('div', 'h');
      h.append('📋 Plan ready', el('span', 'st', `${clock(e.seconds || 0)} · nothing changed yet`));
      const acts = el('div', 'acts');
      acts.append(button('▶ Build it', () => sendText('Go ahead with that plan.'), 'primary'),
        button('Change the plan', () => { $('prompt').value = 'Change the plan: '; $('prompt').focus(); planFirst = true; renderEngine(); }));
      c.append(h, acts); t.tl.append(c); return;
    }
    const [label, cls] = DONE[e.status] || [e.status, ''];
    if (e.status === 'done' && t.lastProse && plain(t.lastText) === plain(e.summary)) t.lastProse.classList.add('final');
    const c = el('div', 'card2 ' + cls), h = el('div', 'h');
    const bits = [clock(e.seconds || 0), e.files ? `${e.files} file${e.files === 1 ? '' : 's'} this step` : 'no file changes', e.total != null ? `${e.total} in total` : '', e.tokens ? `${tokens(e.tokens)} tokens` : '', `on your ${NAME[e.engine] || 'plan'}`];
    h.append(label, el('span', 'st', bits.filter(Boolean).join(' · ')));
    c.append(h);
    if (e.summary && !(e.status === 'done' && t.lastProse && t.lastProse.classList.contains('final'))) { const p = el('div', 'prose'); p.append(md(e.summary)); c.append(p); }
    const acts = el('div', 'acts');
    if (e.status === 'done') {
      acts.append(button('⚖ Second opinion', () => review()), button('▶ Run checks', () => runChecks()), button('🔊 Read it to me', () => speak(e.summary || t.lastText)));
    } else if (['limited', 'signed_out', 'missing'].includes(e.status) && e.engine) {
      acts.append(button(`Continue on your ${NAME[OTHER[e.engine]]}`, () => { switchEngine(OTHER[e.engine]); sendText('Carry on with the request from where it stopped.'); }, 'primary'));
    }
    if (acts.childNodes.length) c.append(acts);
    t.tl.append(c);
  }
  function renderReview(e, t) {
    if (t.review) t.review.remove();
    const c = el('div', 'card2'), h = el('div', 'h');
    if (e.status !== 'done') {
      h.append(`⚠ The second opinion didn't finish (${e.status})`); c.append(h, el('p', 'muted', e.text || '')); t.tl.append(c); return;
    }
    const score = el('div', 'score');
    if (e.rating != null) score.append(ring10(e.rating, 52));
    const words = el('div');
    words.append(el('b', '', `Your ${NAME[e.engine]} says`), el('div', 'muted', 'Second opinion · brutally honest'));
    score.append(words); c.append(score);
    const p = el('div', 'prose'); p.style.marginTop = '8px'; p.append(md(e.text.replace(/^\**rating\**:?\**\s*\d+(\.\d+)?\s*\/\s*10\s*\n?/i, ''))); c.append(p);
    const acts = el('div', 'acts');
    acts.append(button('Fix what it found', () => sendText(`A second opinion on your change said:\n\n${e.text}\n\nFix the real problems it found (skip any you disagree with, and say why).`), 'primary'));
    c.append(acts); t.tl.append(c);
  }
  function button(text, fn, cls = '') { const b = el('button', cls, text); b.type = 'button'; b.onclick = fn; return b; }
  // Copy buttons: navigator.clipboard, or a hidden textarea where that isn't allowed.
  async function copyText(text) {
    try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); return true; } } catch (_) {}
    const was = document.activeElement;                    // focus goes back here afterwards
    const ta = el('textarea'); ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    (document.querySelector('dialog[open]') || document.body).append(ta); ta.focus({preventScroll: true}); ta.select();
    try { return !!document.execCommand('copy'); } catch (_) { return false; }
    finally { ta.remove(); if (was && was !== document.body && was.focus) was.focus({preventScroll: true}); }
  }
  function copyBtn(getText) {
    const b = button('Copy', async () => {
      const text = String(getText());
      const ok = text ? await copyText(text) : false;          // a command still running has no output yet
      b.textContent = ok ? 'Copied' : text ? 'Copy failed' : 'Nothing yet'; b.classList.toggle('done', ok);
      clearTimeout(b.timer); b.timer = setTimeout(() => { b.textContent = 'Copy'; b.classList.remove('done'); }, 1500);
    }, 'copy');
    b.title = 'Copy to clipboard'; return b;
  }
  // `bar`: the button gets its own row above (diffs, whose first line would sit under it) instead of floating over the corner.
  function withCopy(node, getText, bar) { const w = el('div', bar ? 'cw bar' : 'cw'); w.append(node, copyBtn(getText)); if (bar) w.prepend(w.lastChild); return w; }
  function copyLine(line, text) { line.append(copyBtn(() => text)); }

  function working() {
    let w = $('feed').querySelector('.working');
    if (!busy()) { if (w) w.remove(); return; }
    if (!w) { w = el('div', 'working'); w.append(reactor(), el('div', 'words')); }
    $('feed').append(w);                                     // always last
    const evs = [...$('feed').querySelectorAll('.step')];
    const lastStep = evs.length ? (evs[evs.length - 1].dataset.label || '') : '';
    const words = w.querySelector('.words'); words.replaceChildren();
    const who = detail.side === 'review' ? 'The second opinion is reading' : detail.side === 'checks' ? 'Running the checks' : `Apex is working · ${NAME[detail.engine]}`;
    words.append(el('b', '', who), el('span', 'clock'));
    if (lastStep) words.append(el('div', 'last', lastStep));
    tickClock();
  }
  // The clock ticks every second on its own, between updates from Apex.
  function tickClock() {
    const c = $('feed').querySelector('.working .clock');
    if (c && detail && detail.since) c.textContent = ` · ${clock(Math.max(0, Date.now() / 1000 - detail.since))}`;
  }
  setInterval(tickClock, 1000);

  // ---------------------------------------------------------------- polling
  function schedule(ms) { clearTimeout(pollTimer); pollTimer = setTimeout(tick, ms); }
  async function tick() {
    try {
      if (current) await pull();
      if (Date.now() - lastOverview > (current ? 15000 : 6000)) await loadOverview();
    } catch (err) { console.warn('[Code] update failed, retrying:', err); }   // offline for a moment, or a bug: never silent
    schedule(document.hidden ? 10000 : stream.on ? 5000 : busy() ? 1000 : 4000);
  }
  const REFRESH = new Set(['done', 'checkpoint', 'review', 'checks', 'kept', 'discarded', 'conflict', 'undo', 'file', 'review_started', 'checks_started', 'you', 'term_done', 'pushed', 'owner_evidence']);
  // The proof costs Apex some git work too: read again only after what can change it, not every edit.
  let waitingDue = false;
  const PROOF_AFTER = new Set(['done', 'checkpoint', 'review', 'checks', 'kept', 'discarded', 'undo', 'term_done', 'owner_evidence']);
  async function pull() {
    const id = current;
    const {events} = await api(`/api/code/sessions/${id}/events?after=${lastId}`);
    if (id !== current) return;
    const refresh = take(events);
    // The change list costs Apex some git work: fetch it when something changed, and now and then.
    if (refresh || (busy() && ++quiet % 5 === 0)) await refreshDetail();
    working();
    if (!stream.on && busy()) { try { const lv = await api(`/api/code/sessions/${id}/live`); if (id === current) showLive(lv); } catch (_) {} }
  }
  // New steps, from the stream or a poll: drawn once each, in order.
  function take(events) {
    const nearBottom = $('feed').scrollHeight - $('feed').scrollTop - $('feed').clientHeight < 160;
    let refresh = false;
    for (const e of events) {
      if (e.id <= lastId) continue;
      lastId = e.id;
      render(e);
      if (!initial) narrateStep(e);                      // said out loud as it happens, never the history
      if (REFRESH.has(e.kind)) refresh = true;
      if (PROOF_AFTER.has(e.kind)) proofDue = true;
      if (e.kind === 'done' || (e.kind === 'tool' && e.tool === 'memory')) waitingDue = true;
    }
    if (waitingDue) { waitingDue = false; loadWaiting(); }   // a memory it suggested shows without waiting for the next poll
    if (events.length) { placeLive(); if (nearBottom) $('feed').scrollTop = $('feed').scrollHeight; }
    return refresh;
  }
  async function refreshDetail() {
    const id = current, withProof = proofDue;
    proofDue = false;
    const [d, p] = await Promise.all([api(`/api/code/sessions/${id}`),
      withProof ? api(`/api/code/sessions/${id}/proof`).catch(err => { console.warn('[Code] the proof did not load:', err.message); return undefined; }) : undefined]);
    if (id !== current) return;
    detail = d; if (p !== undefined) proof = p;
    renderDetail(); renderEngine();
  }

  // ---------------------------------------------------------------- the side panel
  function renderDetail() {
    const s = detail;
    $('s-title').textContent = s.title;
    const meta = $('s-meta'); meta.replaceChildren();
    meta.append(el('span', '', s.project), el('code', '', s.branch || ''), el('span', 'badge ' + s.engine, NAME[s.engine]));
    if (s.mode === 'full') meta.append(el('span', 'badge full', 'FULL'));
    if (s.origin === 'night') meta.append(nightBadge());
    if (s.status !== 'ready') meta.append(el('span', 'badge ' + (s.status === 'kept' ? 'kept' : ''), s.status === 'kept' ? 'KEPT' : 'THROWN AWAY'));
    const changed = s.changes.files;
    $('c-count').textContent = changed.length ? `${changed.length} · +${s.changes.plus} −${s.changes.minus}` : '';
    $('side-count').textContent = changed.length ? String(changed.length) : '';
    const list = $('c-files'); list.replaceChildren();
    if (!changed.length) list.append(el('p', 'fine', s.status === 'discarded' ? 'Thrown away.' : 'No changes yet.'));
    for (const f of changed) {
      const b = el('button', 'frow ' + f.change); b.type = 'button'; b.title = f.path;
      b.append(el('span', 'k', f.change === 'add' ? '+' : f.change === 'delete' ? '−' : '●'), el('span', 'p', f.path),
        el('span', 'plus', f.plus == null ? 'bin' : `+${f.plus}`), el('span', 'minus', f.minus == null ? '' : `−${f.minus}`));
      b.onclick = () => openDiff(f.path);
      list.append(b);
    }
    // Second opinion
    const r = $('r-body'); r.replaceChildren();
    const other = OTHER[s.engine];
    if (s.review_state === 'working') r.append(el('p', 'verdict', `Your ${NAME[s.review_engine]} is reading the change…`));
    else if (s.review_state === 'done') {
      const score = el('div', 'score');
      if (s.review_rating != null) score.append(ring10(s.review_rating, 64));
      const verdict = (s.review_text.match(/verdict:\s*(.+)/i) || [, s.review_text.split('\n').find(l => l.trim() && !/rating/i.test(l)) || ''])[1];
      const w = el('div'); w.append(el('b', '', `${NAME[s.review_engine]} says`), el('p', 'verdict', verdict.replace(/\*\*/g, '')));
      score.append(w); r.append(score);
      const rv = proof && proof.review && proof.review.engine === s.review_engine ? proof.review : null;
      if (rv) r.append(el('p', 'indep ' + rv.independence, rv.label));
      const d = el('details', 'more full'); d.append(el('summary', '', 'Full review')); const p = el('div', 'prose'); p.append(md(s.review_text)); d.append(p);
      if (rv && rv.citations.length) {                     // each file:line it cites, checked against the copy
        const ul = el('ul', 'cites');
        for (const c of rv.citations) { const li = el('li', 'cite ' + c.kind); li.append(el('code', '', c.cite), el('span', '', c.label)); ul.append(li); }
        d.append(ul);
      }
      r.append(d);
    } else if (s.review_state === 'failed') r.append(el('p', 'verdict', 'The last review did not finish. ' + (s.review_text || '').slice(0, 200)));
    else if (s.review_state === 'skipped') r.append(el('p', 'verdict', (s.review_text || 'No independent review overnight.') + ' Ask for one now if you want it.'));
    else r.append(el('p', 'verdict', `Your ${NAME[other]} reads the change, rates it out of 10, and tells you what's wrong.`));
    $('r-go').textContent = s.review_state === 'done' ? `Ask again (${NAME[other]})` : `Get a second opinion (${NAME[other]})`;
    // Checks
    const proj = ov && ov.projects.find(p => p.id === s.project_id);
    if (document.activeElement !== $('k-cmd')) $('k-cmd').value = proj ? proj.checks : '';
    $('k-save').hidden = !proj || $('k-cmd').value === proj.checks;
    $('k-exit').checked = !!(proj && proj.checks_exit_ok);
    $('k-exit').disabled = !proj;
    const ks = $('k-state'), why = s.check_evidence || '';
    const older = s.check_state === 'passed' && proof && proof.checks && proof.checks.stale;
    ks.className = 'k-state' + (older ? ' warn' : {passed: ' ok', failed: ' bad', unknown: ' warn'}[s.check_state] || '');
    ks.textContent = s.check_state === 'unknown' ? `? Unknown: ${why}` : older ? '✓ Passed on an older checkpoint'
      : ({running: 'Running…', passed: '✓ Passed', failed: '✗ Failed'}[s.check_state] || 'Not run yet.') + (why && /^(passed|failed)$/.test(s.check_state) ? ` · ${why}` : '');
    $('k-out').hidden = !s.check_output; $('k-out').textContent = s.check_output || '';
    // Actions
    const ready = s.status === 'ready', idle = ready && !busy();
    $('a-keep').disabled = !idle || !changed.length || !!s.conflict;
    $('a-push').disabled = $('a-keep').disabled;
    $('s-tokens').textContent = s.tokens ? `${tokens(s.tokens)} tokens` : '';
    $('t-where').textContent = s.worktree || '';
    $('t-cmd').disabled = !ready;
    $('a-keep').textContent = s.status === 'kept' ? '✓ Kept' : `✓ Keep it${proj && proj.branch ? ` → ${proj.branch}` : ''}`;
    $('a-undo').disabled = !idle; $('a-catchup').disabled = !idle; $('a-discard').disabled = !ready;
    $('r-go').disabled = !idle || !changed.length; $('k-go').disabled = !idle || !ready;
    $('a-folder').disabled = !ready;
    $('a-where').textContent = ready ? `Working copy: ${s.worktree}` : s.status === 'kept' ? `Merged as ${s.kept_commit.slice(0, 10)}.` : '';
    renderWaiting();
    renderProof();
  }

  // ---------------------------------------------------------------- proof: what it said vs what Apex saw (code_studio.proof)
  // Before Keep: the sentences where the agent says it tested its work, word for word, next
  // to what Apex saw for itself. Only Apex's own checks, passed on what is there now, prove
  // it; a check that couldn't decide is "unknown", never a tick. Output you paste is yours.
  let proof = null, proofDue = true;
  const VERDICT = {proved: ['✓ Proved', 'ok'], unverified: ['? Unverified', 'warn'], contradicted: ['✗ Contradicted', 'bad']};
  const SAW = {true: ['✓', 'ok'], false: ['✗', 'bad'], null: ['?', 'warn']}, mark = v => SAW[v] || SAW.null;
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  function sawRow(who, [sign, cls], command, evidence, where, old) {   // who; the command on its own line; what it showed
    const row = el('div', 'saw' + (old ? ' old' : '')), res = el('div', 'res');
    row.append(el('span', 'who', who));
    if (command) { const c = el('code', '', command); c.title = command; row.append(c); }
    res.append(el('span', 'mark ' + cls, sign), ' ', el('span', 'ev', evidence));
    if (where) res.append(el('span', 'where', ` · ${where}`));
    row.append(res);
    return row;
  }
  function renderProof() {
    const p = proof, box = $('box-proof');
    box.hidden = !p || !detail;
    if (box.hidden) return;
    const [label, cls] = VERDICT[p.verdict] || VERDICT.unverified;
    $('p-badge').textContent = label; $('p-badge').className = 'p-badge ' + cls;
    $('p-reasons').replaceChildren(...p.reasons.map(r => el('li', '', r)));
    const said = $('p-said');                                       // exactly what it said, never paraphrased
    said.replaceChildren(...p.claims.map(c => el('q', 'claim' + (c.pass_claim ? ' pass' : ''), c.sentence)));
    if (!p.claims.length) said.append(el('p', 'fine', 'Nothing about tests or checks.'));
    const saw = $('p-saw'), k = p.checks; saw.replaceChildren();
    if (k.state !== 'none') {
      saw.append(sawRow('Apex ran', k.state === 'running' ? ['…', ''] : mark({passed: true, failed: false}[k.state] ?? null), k.command, k.state === 'running' ? 'running' : k.why || k.state,
        `${k.checkpoint ? `checkpoint ${k.checkpoint}` : k.sha ? `commit ${k.sha.slice(0, 8)}` : ''}${k.stale ? ' (older)' : ''}`, k.stale));
    }
    for (const r of p.saw_agent) saw.append(sawRow(`It ran${r.engine ? ` (${NAME[r.engine] || r.engine})` : ''}`, mark(r.verdict), r.command, r.evidence,
      `${r.checkpoint ? `step ${r.checkpoint}` : 'this step'}${r.stale ? ', files changed after' : ''}`, r.stale));
    for (const r of p.saw_you) saw.append(sawRow('You ran', mark(r.verdict), r.command, r.evidence, r.stale ? 'files changed after' : '', r.stale));
    const o = p.owner_evidence;
    if (o) saw.append(sawRow('You reported', mark(o.verdict), '', o.evidence || 'no test count', `pasted, ${o.stale ? 'files changed since' : 'not seen by Apex'}`, true));
    if (!saw.childNodes.length) saw.append(el('p', 'fine', 'Nothing yet: run the checks.'));
    const n = k.changed_since.length;
    $('p-stale').hidden = !(k.stale && n);
    $('p-stale').textContent = `${plural(n, 'file')} changed since: ${k.changed_since.slice(0, 6).join(', ')}${n > 6 ? ' …' : ''}`;
    $('p-goalpost').hidden = !p.goalpost.length;
    $('p-goalpost').textContent = `⚠ The checks passed, and the change edits tests (${p.goalpost.slice(0, 4).join(', ')}${p.goalpost.length > 4 ? ' …' : ''}): `
      + 'a pass can come from changing what is tested. Read those diffs.';
    const dis = p.review && p.review.disagreement;
    $('p-disagree').hidden = !dis; $('p-disagree').textContent = dis ? `⚖ ${dis}` : '';
    const prove = k.state === 'unknown' && detail.status === 'ready' && !!k.command;
    $('p-prove').hidden = !prove;
    if (prove && $('p-cmd').dataset.cmd !== k.command) {
      $('p-cmd').dataset.cmd = k.command;
      $('p-cmd').replaceChildren(withCopy(el('pre', 'out', k.command), () => k.command));
    }
    $('p-where').textContent = prove ? `Apex couldn't tell. Run this yourself in ${detail.worktree}, then paste what it printed: it counts as what you reported, not as Apex's check.` : '';
  }
  $('p-send').onclick = async () => {
    const output = $('p-paste').value.trim();
    if (!output) { say('Paste what the command printed first.', 'error'); $('p-paste').focus(); return; }
    $('p-send').disabled = true;
    try {
      proof = await post(`/api/code/sessions/${current}/evidence`, {output});
      $('p-paste').value = ''; renderDetail();
      say('Added as what you reported. Only Apex\'s own checks can prove it.', 'good'); schedule(200);
    } catch (err) { say(err.message, 'error'); }                          // the paste stays in the box
    $('p-send').disabled = false;
  };
  $('r-go').onclick = () => review();
  $('k-go').onclick = () => runChecks();
  $('k-cmd').oninput = () => { const proj = ov && detail && ov.projects.find(p => p.id === detail.project_id); $('k-save').hidden = !proj || $('k-cmd').value === proj.checks; };
  $('k-exit').onchange = async () => {
    const on = $('k-exit').checked;
    try {
      await api(`/api/code/projects/${detail.project_id}`, {method: 'PATCH', body: JSON.stringify({exit_ok: on})}); await loadOverview(); renderDetail();
      say(on ? 'Exit 0 now counts as a pass for this project, even without a test count.' : 'Exit 0 without a test count is Unknown again.', 'good');
    } catch (err) { $('k-exit').checked = !on; say(err.message, 'error'); }
  };
  $('k-save').onclick = async () => {
    try { await api(`/api/code/projects/${detail.project_id}`, {method: 'PATCH', body: JSON.stringify({checks: $('k-cmd').value})}); await loadOverview(); renderDetail(); say('Checks command saved.', 'good'); }
    catch (err) { say(err.message, 'error'); }
  };
  async function act(action, okText) {
    try { const r = await post(`/api/code/sessions/${current}/${action}`); if (okText) say(typeof okText === 'function' ? okText(r) : okText, 'good'); schedule(200); return r; }
    catch (err) { say(err.message, 'error'); }
  }
  async function review() { try { detail = await post(`/api/code/sessions/${current}/review`, {}); renderDetail(); renderEngine(); schedule(200); } catch (err) { say(err.message, 'error'); } }
  const runChecks = () => act('checks');
  async function sendText(text) {
    try { detail = await post(`/api/code/sessions/${current}/messages`, {prompt: text, engine, mode, model, effort, plan: false}); renderEngine(); schedule(200); return true; }
    catch (err) { say(err.message, 'error'); return false; }
  }
  // Keep reads the proof first. Proved: the usual question, with what proves it. Otherwise
  // it says so plainly, and keeps it only on "Keep anyway" (the server asks the same).
  const unproved = (p, base) => confirmBox('Keep it unproved?',
    `${(VERDICT[p.verdict] || VERDICT.unverified)[0]}. ${p.reasons.join(' ')}\n\n${base}`, 'Keep anyway');
  // `info`: the session's project_id, project name and how many files it changes. Also
  // used by the morning's "While you slept" strip, for a session that isn't open.
  async function keepFlow(sid, push, info) {
    const proj = ov.projects.find(p => p.id === info.project_id), n = info.files;
    const apex = proj && proj.name === 'Apex', branch = proj && proj.branch ? proj.branch : 'your branch';
    const base = push ? `This merges the session into ${branch} of ${info.project}, then runs git push.`
      : `This merges ${n} file${n === 1 ? '' : 's'} into ${branch} of ${info.project}.${apex ? ' Restart Apex afterwards to run it.' : ''}`;
    const shown = p => { if (sid === current) { proof = p; renderDetail(); } return p; };
    let p;
    try { p = shown(await api(`/api/code/sessions/${sid}/proof`)); } catch (err) { say(err.message, 'error'); return false; }
    let body = push ? {push: true} : {};
    if (p.verdict === 'proved') {
      if (!await confirmBox(push ? 'Keep and push?' : 'Keep it?', `${base}\n\n✓ Proved: ${p.reasons[0]}`, push ? 'Keep & push' : 'Keep it')) return false;
    } else {
      if (!await unproved(p, base)) return false;
      body = {...body, unverified_ok: true};
    }
    try { await post(`/api/code/sessions/${sid}/keep`, body); }
    catch (err) {
      if (err.status !== 409 || !err.body || !err.body.proof) { say(err.message, 'error'); return false; }
      p = shown(err.body.proof);                                       // something changed after it was read
      if (!await unproved(p, base)) return false;
      try { await post(`/api/code/sessions/${sid}/keep`, {...body, unverified_ok: true}); } catch (err2) { say(err2.message, 'error'); return false; }
    }
    say(push ? 'Kept. Pushing…' : apex ? 'Kept. Restart Apex to run it.' : 'Kept. It\'s in your project now.', 'good');
    return true;
  }
  async function keepIt(push) {
    if (!await keepFlow(current, push, {project_id: detail.project_id, project: detail.project, files: detail.changes.files.length})) return;
    proofDue = true; await loadOverview(); await refreshDetail(); schedule(200);
  }
  $('a-keep').onclick = () => keepIt(false);
  // Throw away asks why (or nothing): the reason goes into the project's decision log and the
  // outcomes ledger, and "changed my mind" is left out of what the track record counts.
  function discardBox() {
    const dlg = $('discard-dialog'), chips = [...$('discard-reasons').querySelectorAll('[data-reason]')];
    let reason = '';
    for (const b of chips) {
      b.setAttribute('aria-checked', 'false');
      b.onclick = () => { reason = reason === b.dataset.reason ? '' : b.dataset.reason; for (const x of chips) x.setAttribute('aria-checked', String(x.dataset.reason === reason)); };
    }
    dlg.returnValue = '';                                // Esc must never count as the last "Throw away"
    dlg.showModal();
    return new Promise(res => dlg.addEventListener('close', () => res(dlg.returnValue === 'ok' ? reason : null), {once: true}));
  }
  // ---------------------------------------------------------------- while you slept (agent/code_studio.overnight)
  // The morning brief's link opens this strip: what the night shift built, each with what
  // Apex saw (the proof's badge), the second opinion's rating, and Keep / Throw away / Open.
  let night = null;
  const NIGHT_WHY = {none: 'no checks ran', unknown: "checks couldn't run", failed: 'checks failed', passed: 'checks are out of date', running: 'checks still running'};
  function nightLine(r) {
    if (r.working) return 'Still working on it.';
    if (r.last_status !== 'done') return `Stopped: ${STOP_TEXT[r.last_status] || r.last_status || 'it did not start'}.`;
    if (!r.files_changed) return 'Nothing changed.';
    if (r.verdict === 'proved') return `Ready to Keep: checks passed${r.why ? ` (${r.why.replace(/\.$/, '')})` : ''}.`;
    if (r.verdict === 'contradicted') return `It says it works, but Apex saw it fail${r.why ? ` (${r.why.replace(/\.$/, '')})` : ''}.`;
    return `Not verified: ${NIGHT_WHY[r.checks] || 'no checks ran'}${r.claimed ? '; it claimed the tests pass, Apex didn\'t see it' : ''}.`;
  }
  async function loadOvernight(sayIt) {
    try { night = (await api('/api/code/overnight')).sessions; }
    catch (err) { say(err.message, 'error'); return; }
    renderOvernight();
    // Milestones read the morning out loud in counts only: a title could be private.
    if (sayIt && narrate !== 'off' && N && N.overnight) { const line = N.overnight(night); if (line) speakNow(line, () => chime('done')); }
  }
  function renderOvernight() {
    const box = $('overnight'), rows = night || [];
    box.hidden = !rows.length;
    $('overnight-rows').replaceChildren(...rows.map(r => {
      const row = el('div', 'nrow' + (r.working ? ' working' : '')), body = el('div', 'grow');
      const v = VERDICT[r.verdict] || VERDICT.unverified;
      const head = el('div', 't'); head.append(el('span', 'p-badge ' + (r.working ? 'warn' : v[1]), r.working ? '… Working' : v[0]), r.title);
      const meta = [r.project, r.task && r.task.title && r.task.title !== r.title ? `task: ${r.task.title}` : '', `${r.files_changed} file${r.files_changed === 1 ? '' : 's'}`,
        r.rating != null ? `${r.rating}/10 by ${NAME[r.review_engine] || r.review_engine}` : r.review_state === 'skipped' ? 'no independent review' : ''].filter(Boolean).join(' · ');
      body.append(head, el('div', 'why', nightLine(r)), el('div', 'm', meta));
      row.append(body);
      if (r.rating != null) row.append(ring10(r.rating, 38));
      const acts = el('div', 'acts');
      if (!r.working && r.files_changed && r.last_status === 'done') {
        acts.append(button('Keep', () => nightKeep(r), 'primary'), button('Throw away', () => nightDiscard(r), 'danger'));
      } else if (!r.working) acts.append(button('Throw away', () => nightDiscard(r), 'danger'));
      acts.append(button('Open', () => open(r.id)));
      row.append(acts);
      return row;
    }));
  }
  async function nightKeep(r) {
    if (!await keepFlow(r.id, false, {project_id: r.project_id, project: r.project, files: r.files_changed})) return;
    night = night.filter(x => x.id !== r.id); renderOvernight(); loadOverview().catch(() => {});
  }
  async function nightDiscard(r) {
    const reason = await discardBox();
    if (reason === null) return;
    try { await post(`/api/code/sessions/${r.id}/discard`, {reason}); say(reason ? 'Thrown away. Apex noted why.' : 'Thrown away.', 'good'); }
    catch (err) { say(err.message, 'error'); return; }
    night = night.filter(x => x.id !== r.id); renderOvernight(); loadOverview().catch(() => {});
  }

  $('a-discard').onclick = async () => {
    const reason = await discardBox();
    if (reason === null) return;
    try { await post(`/api/code/sessions/${current}/discard`, {reason}); say(reason ? 'Thrown away. Apex noted why.' : 'Thrown away.', 'good'); schedule(200); }
    catch (err) { say(err.message, 'error'); return; }
    await loadOverview(); await refreshDetail();
  };
  $('a-undo').onclick = async () => {
    if (!await confirmBox('Undo the last step?', 'The files go back to how they were before Apex\'s last step. Apex is told, so it won\'t assume its work is still there.', 'Undo it')) return;
    await act('undo', 'Undone.'); await refreshDetail();
  };
  $('a-catchup').onclick = () => act('catch-up', r => r.caught_up === 'already' ? 'Already up to date with your latest work.'
    : r.caught_up === 'conflict' ? `Caught up, with clashes in ${r.conflicts.length} file(s): ask Apex to fix them.` : 'Caught up with your latest work.');
  $('a-folder').onclick = async () => {
    const path = detail.worktree;
    try { await navigator.clipboard.writeText(path); } catch (_) {}
    say(`Path copied: ${path}`, 'good', {text: 'Open in VS Code', href: 'vscode://file/' + path.replace(/\\/g, '/')});
  };

  // ---------------------------------------------------------------- the diff viewer
  let diffPath = null;
  async function openDiff(path) {
    if (!current) return;
    diffPath = path;
    const f = detail && detail.changes.files.find(x => x.path === path);
    $('diff-path').textContent = path;
    $('diff-stat').textContent = f ? (f.plus == null ? 'binary' : `+${f.plus} −${f.minus}`) : '';
    $('diff-body').replaceChildren(el('p', 'fine', 'Loading…'));
    if (!$('diff').open) $('diff').showModal();
    try { const text = await api(`/api/code/sessions/${current}/diff?path=${encodeURIComponent(path)}`, {}, 'text'); $('diff-body').replaceChildren(withCopy(renderDiff(text), () => text, true)); }
    catch (err) { $('diff-body').replaceChildren(el('p', 'fine', err.message)); }
  }
  function renderDiff(text) {
    const box = el('div'); let a = 0, b = 0;
    for (const line of text.split('\n')) {
      let cls = 'ctx', l = '', r = '';
      if (line.startsWith('@@')) { const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/); if (m) { a = +m[1]; b = +m[2]; } cls = 'hunk'; }
      else if (line.startsWith('diff --git ')) { cls = 'file'; l = ''; r = ''; const row = el('div', 'dl file'); row.append(el('span', 'ln'), el('span', 'ln'), el('span', 'tx', line.replace(/^diff --git a\/(.*) b\/.*$/, '$1'))); box.append(row); continue; }
      else if (/^(index |--- |\+\+\+ |new file mode|deleted file mode|similarity|rename |old mode|new mode)/.test(line)) continue;
      else if (line.startsWith('+')) { cls = 'add'; r = b++; }
      else if (line.startsWith('-')) { cls = 'del'; l = a++; }
      else if (line.startsWith('\\')) cls = 'hunk';
      else if (line.startsWith('new file ')) cls = 'hunk';
      else { l = a++; r = b++; }
      const row = el('div', 'dl ' + cls); row.append(el('span', 'ln', String(l)), el('span', 'ln', String(r)), el('span', 'tx', line || ' '));
      box.append(row);
    }
    return box;
  }
  function stepDiff(dir) {
    const files = detail ? detail.changes.files : []; const i = files.findIndex(f => f.path === diffPath);
    if (files.length) openDiff(files[(i + dir + files.length) % files.length].path);
  }
  $('diff-prev').onclick = () => stepDiff(-1); $('diff-next').onclick = () => stepDiff(1);
  $('diff-close').onclick = () => $('diff').close();
  $('diff').addEventListener('keydown', e => { if (e.key === 'ArrowDown' || e.key === 'j') stepDiff(1); if (e.key === 'ArrowUp' || e.key === 'k') stepDiff(-1); });

  // ---------------------------------------------------------------- live: the stream, typing, running output
  // One request that stays open and brings every step as it happens (one JSON per
  // line), read with fetch so it carries the token. If it can't connect, polling
  // takes over, so nothing depends on it.
  const stream = {on: false, gen: 0, fails: 0, ctrl: null};
  function disconnect() { stream.gen++; stream.on = false; if (stream.ctrl) { try { stream.ctrl.abort(); } catch (_) {} } stream.ctrl = null; }
  async function connect(id) {
    disconnect();
    const gen = stream.gen;
    if (!window.ReadableStream || !window.AbortController) return;
    while (gen === stream.gen && current === id) {
      stream.ctrl = new AbortController();
      try {
        const r = await fetch(`/api/code/sessions/${id}/stream?after=${lastId}`, {headers: auth(), signal: stream.ctrl.signal});
        if (!r.ok || !r.body) throw new Error(`stream ${r.status}`);
        stream.on = true; stream.fails = 0;
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = '';
        for (;;) {
          const {value, done} = await reader.read();
          if (done || gen !== stream.gen || current !== id) break;
          buf += dec.decode(value, {stream: true});
          let nl;
          while ((nl = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
            if (line.trim()) onStream(JSON.parse(line), id);
          }
        }
      } catch (err) { if (gen !== stream.gen) return; stream.fails++; console.warn('[Code] live stream dropped, retrying:', err && err.message); }
      stream.on = false;
      if (gen !== stream.gen || stream.fails > 3) return;                   // polling carries on alone
      await new Promise(r => setTimeout(r, 400 * (stream.fails + 1)));
    }
  }
  let refreshSoon = null;
  function onStream(m, id) {
    if (id !== current) return;                         // another session's stream, still draining
    if (m.t === 'event') {
      const {t, ...e} = m;
      if (take([e])) { clearTimeout(refreshSoon); refreshSoon = setTimeout(() => refreshDetail().then(working).catch(() => {}), 250); }
      working();
    } else if (m.t === 'live') showLive(m);
  }
  function showLive(lv) {
    if (!lv || lv.v <= liveSeen) return;               // never go back to an older snapshot
    liveSeen = lv.v; live = lv;
    const nearBottom = $('feed').scrollHeight - $('feed').scrollTop - $('feed').clientHeight < 160;
    let box = $('feed').querySelector('.typing');
    if (lv.text || lv.thinking) {
      if (!box) { box = el('div', 'typing'); box.append(el('div', 'think'), el('div', 'prose')); $('feed').append(box); }
      box.querySelector('.think').textContent = lv.thinking ? '✻ ' + lv.thinking.slice(-280) : '';
      const p = box.querySelector('.prose'); p.replaceChildren(md(lv.text, true));
      (p.lastElementChild && !/^(PRE|UL)$/.test(p.lastElementChild.tagName) ? p.lastElementChild : p).append(el('span', 'cursor', '▍'));   // right after the last word
    } else if (box) { box.remove(); box = null; }
    for (const [ref, out] of Object.entries(lv.outputs || {})) {
      const s = (feed && feed.turn && feed.turn.tools[ref]) || $('feed').querySelector(`[data-ref="${CSS.escape(ref)}"]`);
      if (!s) continue;
      let lo = s.querySelector('.live-out');
      if (!lo) { const o = el('pre', 'out'); lo = withCopy(o, () => o.textContent); lo.classList.add('live-out'); s.append(lo); }
      const pre = lo.firstChild;
      pre.textContent = out; pre.scrollTop = pre.scrollHeight;
    }
    placeLive();
    if (nearBottom) $('feed').scrollTop = $('feed').scrollHeight;
  }
  function placeLive() {                                  // typing, then "working", always last
    const box = $('feed').querySelector('.typing'); if (box) $('feed').append(box);
    const w = $('feed').querySelector('.working'); if (w) $('feed').append(w);
  }
  const tokens = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  let audio = null;
  function chime(status) {                                // a soft two-note chime when a step ends
    if (store.get('apex.code.sound', 'on') !== 'on') return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const notes = status === 'done' ? [660, 880] : [440, 330];
      notes.forEach((f, i) => {
        const o = audio.createOscillator(), g = audio.createGain(), t0 = audio.currentTime + i * 0.12;
        o.frequency.value = f; o.type = 'sine'; g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(0.06, t0 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.25);
        o.connect(g).connect(audio.destination); o.start(t0); o.stop(t0 + 0.3);
      });
    } catch (_) {}
  }

  // ---------------------------------------------------------------- inline diffs
  function inlineDiff(text) {
    const lines = text.split('\n').length;
    const box = el('div', 'inline-diff'); box.append(renderDiff(text));
    const cw = withCopy(box, () => text, true);
    if (lines > 24) {
      box.classList.add('folded');
      const more = button(`Show all ${lines} lines`, () => { box.classList.remove('folded'); more.remove(); }, 'small more-lines');
      const wrap = el('div'); wrap.append(cw, more); return wrap;
    }
    return cw;
  }

  // ---------------------------------------------------------------- allow, terminal
  async function allowCmd(command, always) {
    try {
      detail = await post(`/api/code/sessions/${current}/allow`, {command, always});
      say(always ? `Allowed from now on in this project: ${command}` : `Allowed once: ${command}`, 'good');
      renderEngine(); schedule(200);
    } catch (err) { say(err.message, 'error'); }
  }
  // ---------------------------------------------------------------- away mode: answer from your phone
  // A notification's link (/code#allow=…): Safe mode stopped a command while you were
  // away. Allow it once or say no, from any signed-in device, even one whose own token
  // can't open the rest of Apex Code. Always allow stays on this page, at your PC.
  let asking = '';
  const hhmm = ts => new Date(ts * 1000).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});
  function allowResult(text, cls = '') { const r = $('allow-result'); r.textContent = text; r.className = 'allow-result ' + cls; r.hidden = !text; }
  function renderAsk(r) {
    const code = el('code', '', r.command);
    $('allow-text').replaceChildren(`Your ${NAME[r.engine] || 'plan'} wants to run `, code, ` in "${r.title}" (${r.project}).`);
    const open = !r.answered && r.expires > Date.now() / 1000;
    const said = {once: 'Already answered: you allowed it once.', no: 'Already answered: you said no.',
      pc: 'Already answered on the Code page at your PC.'};
    $('allow-when').textContent = r.answered ? (said[r.choice] || 'Already answered.')
      : open ? `Answer by ${hhmm(r.expires)}. Always allow is only on the Code page at your PC.`
      : `This request expired at ${hhmm(r.expires)}. Answer it on the Code page at your PC.`;
    $('allow-acts').hidden = !open; $('allow-done').hidden = open;
  }
  async function askAllow(token) {
    asking = token; allowResult('');
    $('allow-text').replaceChildren('Reading the request…'); $('allow-when').textContent = '';
    $('allow-acts').hidden = true; $('allow-done').hidden = true;
    if (!$('allow-dialog').open) $('allow-dialog').showModal();
    try { renderAsk(await api(`/api/code/allow/${encodeURIComponent(token)}`)); }
    catch (err) {
      $('allow-text').replaceChildren(err.status === 404 ? "Apex doesn't know this request. Open Apex Code at your PC to see the session." : err.message);
      $('allow-done').hidden = false;
    }
  }
  async function answerAllow(choice) {
    $('allow-acts').hidden = true;
    try {
      await post(`/api/code/allow/${encodeURIComponent(asking)}`, {choice});
      allowResult(choice === 'once' ? 'Done: it carries on.' : 'Done: it will find another way, or stop and explain.', 'good');
      $('allow-when').textContent = ''; $('allow-done').hidden = false;
      if (current) schedule(200);
    } catch (err) {
      allowResult(err.message, 'bad');                       // say, still working at your PC: the link still works
      try { renderAsk(await api(`/api/code/allow/${encodeURIComponent(asking)}`)); } catch (_) { $('allow-done').hidden = false; }
    }
  }
  $('allow-once').onclick = () => answerAllow('once');
  $('allow-no').onclick = () => answerAllow('no');
  $('allow-close').onclick = () => $('allow-dialog').close();
  $('allow-dialog').addEventListener('close', () => {
    if (/^#allow=/.test(location.hash)) history.replaceState(null, '', location.pathname + (current ? `#s=${current}` : ''));
  });
  async function runTerminal(command) {
    if (!command) return;
    try { await post(`/api/code/sessions/${current}/terminal`, {command}); termHistory.unshift(command); termAt = -1; schedule(200); }
    catch (err) { say(err.message, 'error'); }
  }
  const termHistory = []; let termAt = -1;
  function termLog(e) {
    const log = $('t-log');
    if (e.kind === 'term') {
      const row = el('div', 't-row'); row.dataset.ref = e.ref;
      const out = el('pre', 'out', '…'); out.dataset.raw = '';       // Copy gives the output alone, not the … or the exit line
      row.append(el('div', 't-cmd', '❯ ' + e.command), withCopy(out, () => out.dataset.raw));
      log.prepend(row);
    } else {
      const row = log.querySelector(`[data-ref="${CSS.escape(e.ref)}"]`);
      if (row) { const pre = row.querySelector('pre'); pre.dataset.raw = e.output || ''; pre.textContent = (e.output || '(no output)') + `\n[exit ${e.exit_code ?? '?'} · ${clock(e.seconds || 0)}]`; row.classList.toggle('bad', e.exit_code !== 0); }
    }
  }
  $('t-form').onsubmit = e => { e.preventDefault(); const c = $('t-cmd').value.trim(); $('t-cmd').value = ''; runTerminal(c); };
  $('t-cmd').addEventListener('keydown', e => {
    if (e.key === 'ArrowUp' && termHistory.length) { termAt = Math.min(termAt + 1, termHistory.length - 1); $('t-cmd').value = termHistory[termAt]; e.preventDefault(); }
    if (e.key === 'ArrowDown') { termAt = Math.max(termAt - 1, -1); $('t-cmd').value = termAt < 0 ? '' : termHistory[termAt]; e.preventDefault(); }
  });

  // ---------------------------------------------------------------- slash commands
  const SLASH = [
    ['/plan', 'Plan first: it reads and writes a plan, changes nothing', 'task'],
    ['/review', 'Second opinion from the other plan, out of 10'],
    ['/test', 'Run the project checks in this session'],
    ['/undo', 'Undo the last step'],
    ['/keep', 'Keep it: merge into your branch'],
    ['/push', 'Keep it and git push'],
    ['/catchup', 'Bring in your latest commits'],
    ['/discard', 'Throw this session away'],
    ['/model', 'Choose the model (fable, opus, sonnet, haiku…)', 'name'],
    ['/effort', 'low, medium, high or max', 'level'],
    ['/claude', 'Next messages on your Claude plan'],
    ['/chatgpt', 'Next messages on your ChatGPT plan'],
    ['/safe', 'Safe mode'], ['/full', 'Full mode'],
    ['/rule', 'Make a rule for this project: every session follows it', 'text'],
    ['/celine', 'Ask Celine about this session (out loud: 🎙 or Ctrl+Shift+Space)', 'question'],
    ['/files', 'Browse the project files'], ['/history', 'Checkpoints of this session'],
    ['/terminal', 'Open the terminal (or type !command)'],
    ['/new', 'Start a new session'], ['/look', 'Switch the Terminal / Studio look'], ['/sound', 'Chime on or off'],
  ];
  async function slash(text) {
    const [cmd, ...rest] = text.split(/\s+/); const arg = rest.join(' ').trim();
    const need = () => { if (!current) { say('Open a session first.', 'error'); return false; } return true; };
    switch (cmd) {
      case '/plan': planFirst = true; renderEngine(); if (arg) { $('prompt').value = arg; $('brief-form').requestSubmit(); } else $('prompt').focus(); break;
      case '/review': if (need()) review(); break;
      case '/test': if (need()) runChecks(); break;
      case '/undo': if (need()) $('a-undo').click(); break;
      case '/keep': if (need()) $('a-keep').click(); break;
      case '/push': if (need()) $('a-push').click(); break;
      case '/catchup': if (need()) $('a-catchup').click(); break;
      case '/discard': if (need()) $('a-discard').click(); break;
      case '/model': model = arg === 'default' ? '' : arg; saveChoice(); renderEngine(); say(model ? `Model: ${model}` : 'Model: the plan\'s default', 'good'); break;
      case '/effort': if (['', 'low', 'medium', 'high', 'xhigh', 'max'].includes(arg)) { effort = arg; saveChoice(); renderEngine(); say(`Effort: ${arg || 'default'}`, 'good'); } else say('Effort is low, medium, high, xhigh or max.', 'error'); break;
      case '/claude': case '/chatgpt': switchEngine(cmd.slice(1)); break;
      case '/safe': case '/full': mode = cmd.slice(1); store.set('apex.code.mode', mode); renderEngine(); break;
      case '/rule':
        if (!arg) { if (need()) tab('rules'); break; }
        if (!rulesPid()) { say('Add a project first.', 'error'); break; }
        if (arg.length > RULE_MAX) { say(`A rule is at most ${RULE_MAX} characters.`, 'error'); $('prompt').value = text; break; }
        try { await addRule(rulesPid(), arg, 'project'); say(RULE_SAVED.project, 'good'); }
        catch (err) { say(err.message, 'error'); $('prompt').value = text; }                    // your text is kept
        break;
      case '/celine': if (need()) askCeline(arg); break;
      case '/files': if (need()) tab('files'); break;
      case '/history': if (need()) tab('history'); break;
      case '/terminal': if (need()) { tab('terminal'); $('t-cmd').focus(); } break;
      case '/new': home(); $('prompt').focus(); break;
      case '/look': toggleLook(); break;
      case '/sound': store.set('apex.code.sound', store.get('apex.code.sound', 'on') === 'on' ? 'off' : 'on'); say(`Chime ${store.get('apex.code.sound', 'on')}.`); break;
      default: say(`Unknown command ${cmd}. Type / to see them all.`, 'error');
    }
  }

  // ---------------------------------------------------------------- @ files and / commands, as you type
  async function loadFiles() {
    const key = current ? `s${current}` : `p${project}`;
    if (files && filesFor === key) return files;
    const r = await api(current ? `/api/code/sessions/${current}/tree` : `/api/code/projects/${project}/tree`);
    files = r.files; filesFor = key; return files;
  }
  function fuzzy(list, q, n = 40) {
    q = q.toLowerCase();
    if (!q) return list.slice(0, n);
    const scored = [];
    for (const item of list) {
      const s = item.toLowerCase(); let i = 0, score = 0, last = -1;
      for (const ch of s) { if (i < q.length && ch === q[i]) { score += last === -1 ? 0 : (s.indexOf(ch, last) - last === 1 ? 3 : 1); last = s.indexOf(ch, last + 1); i++; } }
      if (i === q.length) scored.push([score + (s.endsWith(q) ? 5 : 0) + (s.includes(q) ? 10 : 0) - s.length / 100, item]);
    }
    return scored.sort((a, b) => b[0] - a[0]).slice(0, n).map(x => x[1]);
  }
  let sugg = {items: [], at: 0, kind: '', start: 0};
  function hideSuggest() { $('suggest').hidden = true; sugg.items = []; }
  async function suggest() {
    const p = $('prompt'), upto = p.value.slice(0, p.selectionStart);
    const at = upto.match(/(?:^|\s)@([\w./\\-]*)$/), sl = upto.match(/^\/(\w*)$/);
    if (sl) {
      sugg = {kind: 'slash', start: 0, at: 0, items: SLASH.filter(([c]) => c.startsWith('/' + sl[1])).map(([c, d]) => ({value: c, label: c, hint: d}))};
    } else if (at) {
      let list = [];
      try { list = await loadFiles(); } catch (_) { return hideSuggest(); }
      sugg = {kind: 'file', start: upto.length - at[1].length - 1, at: 0, items: fuzzy(list, at[1], 12).map(f => ({value: '@' + f, label: f, hint: ''}))};
    } else return hideSuggest();
    const box = $('suggest'); box.replaceChildren();
    if (!sugg.items.length) return hideSuggest();
    sugg.items.forEach((it, i) => {
      const r = el('div', 'sg' + (i === sugg.at ? ' on' : '')); r.setAttribute('role', 'option');
      r.append(el('b', '', it.label), el('span', '', it.hint));
      r.onmousedown = ev => { ev.preventDefault(); sugg.at = i; pick(); };
      box.append(r);
    });
    box.hidden = false;
  }
  function pick() {
    const it = sugg.items[sugg.at]; if (!it) return;
    const p = $('prompt'), end = p.selectionStart;
    const before = sugg.kind === 'slash' ? '' : p.value.slice(0, sugg.start), after = p.value.slice(end);
    p.value = before + it.value + ' ' + after.replace(/^\S*/, '');
    const pos = (before + it.value + ' ').length; p.setSelectionRange(pos, pos); hideSuggest(); autosize();
  }
  $('prompt').addEventListener('input', () => { suggest(); });
  $('prompt').addEventListener('keydown', e => {
    if ($('suggest').hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); sugg.at = (sugg.at + (e.key === 'ArrowDown' ? 1 : -1) + sugg.items.length) % sugg.items.length;
      [...$('suggest').children].forEach((c, i) => c.classList.toggle('on', i === sugg.at));
    } else if ((e.key === 'Tab' || e.key === 'Enter') && !e.ctrlKey && !e.metaKey) { e.preventDefault(); pick(); }
    else if (e.key === 'Escape') { e.stopPropagation(); hideSuggest(); }
  }, true);
  $('prompt').addEventListener('blur', () => setTimeout(hideSuggest, 150));

  // ---------------------------------------------------------------- side tabs: files, terminal, history
  function tab(name) {
    for (const b of document.querySelectorAll('[data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
    for (const p of document.querySelectorAll('[data-pane]')) p.hidden = p.dataset.pane !== name;
    if (innerWidth <= 1100) { $('side').classList.add('open'); $('scrim').hidden = false; }
    if (name === 'files') renderFiles();
    if (name === 'history') renderHistory();
    if (name === 'rules') renderRules();
  }
  for (const b of document.querySelectorAll('[data-tab]')) b.onclick = () => tab(b.dataset.tab);
  async function renderFiles() {
    const list = $('f-list');
    try { await loadFiles(); } catch (err) { list.replaceChildren(el('p', 'fine', err.message)); return; }
    const shown = fuzzy(files, $('f-filter').value.trim(), 300);
    list.replaceChildren(...shown.map(f => {
      const b = el('button', 'frow'); b.type = 'button'; b.title = f;
      b.append(el('span', 'k', '·'), el('span', 'p', f)); b.onclick = () => openFile(f); return b;
    }));
    $('f-count').textContent = `${files.length.toLocaleString()} files${shown.length < files.length ? ` · showing ${shown.length}` : ''}`;
  }
  $('f-filter').addEventListener('input', renderFiles);
  async function renderHistory() {
    const list = $('h-list');
    try {
      const {checkpoints} = await api(`/api/code/sessions/${current}/history`);
      list.replaceChildren(...(checkpoints.length ? checkpoints.map((c, i) => {
        const b = el('button', 'frow' + (c.undone ? ' undone' : '')); b.type = 'button';
        b.append(el('span', 'k', c.undone ? '↶' : '◆'), el('span', 'p', `${c.catch_up ? 'Caught up' : `Step ${checkpoints.length - i}`} · ${c.files} file${c.files === 1 ? '' : 's'}${c.undone ? ' · undone' : ''}`),
          el('span', 'muted', c.short));
        b.onclick = () => openCommit(c); return b;
      }) : [el('p', 'fine', 'No checkpoints yet: one is saved after every step.')]));
    } catch (err) { list.replaceChildren(el('p', 'fine', err.message)); }
  }
  async function openCommit(c) {
    $('diff-path').textContent = `Checkpoint ${c.short}`; $('diff-stat').textContent = `${c.files} file${c.files === 1 ? '' : 's'}`;
    $('diff-body').replaceChildren(el('p', 'fine', 'Loading…')); if (!$('diff').open) $('diff').showModal();
    try { $('diff-body').replaceChildren(renderDiff(await api(`/api/code/sessions/${current}/commit?sha=${c.sha}`, {}, 'text'))); }
    catch (err) { $('diff-body').replaceChildren(el('p', 'fine', err.message)); }
  }

  // ---------------------------------------------------------------- the code viewer, with colours
  const KW = {
    python: 'and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield self',
    js: 'async await break case catch class const continue default delete do else export extends false finally for function if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while yield',
    css: '', html: '', json: 'true false null', md: '', yaml: 'true false null', toml: 'true false', shell: 'if then else fi for do done while case esac echo set call goto exit', sql: 'select from where insert update delete create table into values and or not null join on group by order limit',
  };
  function highlight(text, lang) {
    const frag = document.createDocumentFragment();
    const kws = new Set((KW[lang] || '').split(' ').filter(Boolean));
    const comment = {python: '#[^\\n]*', yaml: '#[^\\n]*', toml: '#[^\\n]*', shell: '(?:#|::|REM )[^\\n]*', sql: '--[^\\n]*',
      js: '//[^\\n]*|/\\*[\\s\\S]*?\\*/', css: '/\\*[\\s\\S]*?\\*/', html: '<!--[\\s\\S]*?-->', json: '(?!)', md: '(?!)'}[lang] || '(?!)';
    const re = new RegExp(`(${comment})|("""[\\s\\S]*?"""|'''[\\s\\S]*?'''|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*'|\`(?:\\\\.|[^\`\\\\])*\`)|(\\b\\d[\\d_.xXa-fA-F]*\\b)|([A-Za-z_$][\\w$-]*)`, 'g');
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) frag.append(text.slice(last, m.index));
      if (m[1]) frag.append(el('span', 'tk-c', m[1]));
      else if (m[2]) frag.append(el('span', 'tk-s', m[2]));
      else if (m[3]) frag.append(el('span', 'tk-n', m[3]));
      else if (kws.has(m[4])) frag.append(el('span', 'tk-k', m[4]));
      else if (lang === 'css' && /^[a-z-]+$/.test(m[4]) && text[re.lastIndex] === ':') frag.append(el('span', 'tk-p', m[4]));
      else frag.append(m[4]);
      last = re.lastIndex;
    }
    if (last < text.length) frag.append(text.slice(last));
    return frag;
  }
  let viewing = null;
  async function openFile(path) {
    viewing = path;
    $('viewer-path').textContent = path; $('viewer-meta').textContent = '';
    $('viewer-body').replaceChildren(el('p', 'fine', 'Loading…'));
    if (!$('viewer').open) $('viewer').showModal();
    try {
      const f = await api(current ? `/api/code/sessions/${current}/file?path=${encodeURIComponent(path)}` : `/api/code/projects/${project}/file?path=${encodeURIComponent(path)}`);
      if (f.image && ['image/png', 'image/jpeg', 'image/webp'].includes(f.image.mime)) {
        const image = el('img'); image.alt = path;
        image.src = `data:${f.image.mime};base64,${f.image.base64}`;
        image.style.maxWidth = '100%'; image.style.maxHeight = '80vh';
        $('viewer-body').replaceChildren(image); $('viewer-meta').textContent = `${(f.size / 1024).toFixed(0)} KB`;
        return;
      }
      if (f.binary || f.too_big) { $('viewer-body').replaceChildren(el('p', 'fine', f.binary ? 'A binary file.' : `Too big to show here (${(f.size / 1024).toFixed(0)} KB).`)); return; }
      const lines = f.text.split('\n'); const shown = lines.slice(0, 6000);
      $('viewer-meta').textContent = `${lines.length.toLocaleString()} lines${f.lang ? ' · ' + f.lang : ''}`;
      const gutter = el('pre', 'gutter', shown.map((_, i) => i + 1).join('\n'));
      const code = el('pre', 'code'); code.append(highlight(shown.join('\n'), f.lang));
      $('viewer-body').replaceChildren(gutter, code);
    } catch (err) { $('viewer-body').replaceChildren(el('p', 'fine', err.message)); }
  }
  $('viewer-close').onclick = () => $('viewer').close();
  $('viewer-mention').onclick = () => {
    const p = $('prompt'); p.value = (p.value.trimEnd() + ' @' + viewing + ' ').trimStart(); $('viewer').close(); closeDrawers(); p.focus(); autosize();
  };

  // ---------------------------------------------------------------- Ctrl+K: everything you can do
  let palItems = [], palAt = 0;
  function paletteItems() {
    const items = [];
    if (current && detail && detail.status === 'ready') {
      items.push(['⚖ Second opinion', () => review()], ['▶ Run checks', () => runChecks()], ['↶ Undo last step', () => $('a-undo').click()],
        ['✓ Keep it', () => $('a-keep').click()], ['⇡ Keep & push', () => $('a-push').click()], ['⇣ Catch up', () => $('a-catchup').click()],
        ['❯ Terminal', () => { tab('terminal'); $('t-cmd').focus(); }], ['◆ History', () => tab('history')], ['🗂 Files', () => tab('files')],
        ['✕ Throw away', () => $('a-discard').click()]);
    }
    if (current && detail) items.push(['📏 Rules for this project', () => tab('rules')], ['🎙 Ask Celine about this session', () => askCeline()]);
    items.push(['＋ New session', () => { home(); $('prompt').focus(); }], ['📋 Plan first', () => { planFirst = true; renderEngine(); $('prompt').focus(); }],
      ['◐ Switch look', toggleLook], [`Use your ${NAME[OTHER[engine]]}`, () => switchEngine(OTHER[engine])],
      ['🧠 What Apex knows about me', openBrain]);
    for (const s of (ov ? ov.sessions : []).slice(0, 30)) items.push([`Session: ${s.title}`, () => open(s.id)]);
    for (const f of (files || []).slice(0, 4000)) items.push([`File: ${f}`, () => openFile(f)]);
    return items;
  }
  function openPalette() {
    if (!files && (current || project)) loadFiles().then(() => { if ($('palette').open) renderPalette(); }).catch(() => {});
    $('palette-q').value = ''; palAt = 0; renderPalette(); $('palette').showModal(); $('palette-q').focus();
  }
  function renderPalette() {
    const all = paletteItems(), labels = fuzzy(all.map(x => x[0]), $('palette-q').value.trim(), 14);
    palItems = labels.map(l => all.find(x => x[0] === l));
    palAt = Math.min(palAt, Math.max(0, palItems.length - 1));
    $('palette-list').replaceChildren(...palItems.map(([label, fn], i) => {
      const r = el('div', 'sg' + (i === palAt ? ' on' : ''), label); r.setAttribute('role', 'option');
      r.onclick = () => { $('palette').close(); fn(); }; return r;
    }));
  }
  $('palette-q').addEventListener('input', () => { palAt = 0; renderPalette(); });
  $('palette-q').addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); palAt = (palAt + (e.key === 'ArrowDown' ? 1 : -1) + palItems.length) % Math.max(1, palItems.length); renderPalette(); }
    if (e.key === 'Enter' && palItems[palAt]) { e.preventDefault(); const fn = palItems[palAt][1]; $('palette').close(); fn(); }
  });
  $('palette-btn').onclick = openPalette;

  // ---------------------------------------------------------------- the look
  function applyLook() {
    const term = store.get('apex.code.look', 'term') === 'term';
    document.body.classList.toggle('term', term);
    $('look-btn').textContent = term ? 'Studio look' : 'Terminal look';
  }
  function toggleLook() { store.set('apex.code.look', store.get('apex.code.look', 'term') === 'term' ? 'studio' : 'term'); applyLook(); }
  $('look-btn').onclick = toggleLook;
  $('a-push').onclick = () => keepIt(true);

  // ---------------------------------------------------------------- dialogs, voice out, drawers, keys
  function confirmBox(title, text, ok) {
    $('confirm-title').textContent = title; $('confirm-text').textContent = text; $('confirm-ok').textContent = ok;
    $('confirm').returnValue = '';                    // Esc must never count as the last "OK"
    $('confirm').showModal();
    return new Promise(res => $('confirm').addEventListener('close', () => res($('confirm').returnValue === 'ok'), {once: true}));
  }
  // ---------------------------------------------------------------- voice: narration and Celine
  // One queue for the page: a milestone, "Read it to me" and Celine's answer each wait their
  // turn. Said by your Voicebox voice (/api/speak), sentence by sentence (speech_queue.js);
  // Voicebox makes one reply at a time for all of Apex, so "already generating" waits for
  // this queue, tries once more, then falls back to the chime.
  const Q = window.ApexSpeechQueue, N = window.ApexNarration;
  const RETRY_MS = 2500;
  const voice = {tail: Promise.resolve(), pending: 0, epoch: 0, audio: null, pace: N ? N.pacer() : null};
  let narrate = ['off', 'milestones', 'play'].includes(store.get('apex.code.narrate')) ? store.get('apex.code.narrate') : 'milestones';
  $('narrate').value = narrate;
  $('narrate').onchange = () => { narrate = $('narrate').value; store.set('apex.code.narrate', narrate); if (narrate === 'off') hush(); };
  async function makeSpeech(text) {
    const r = await fetch('/api/speak', {method: 'POST', headers: {'Content-Type': 'application/json', ...auth()},
      body: JSON.stringify({text, engine: 'voicebox', profile: store.get('apex.voicebox.profile')})});
    if (!r.ok) {
      const b = await r.json().catch(() => ({})), err = new Error(b.error || b.detail || `voice failed (${r.status})`);
      err.busy = r.status === 503 && /already generating/i.test(err.message); throw err;
    }
    if (!(r.headers.get('content-type') || '').startsWith('audio')) throw new Error('voice is not available');
    return r.blob();
  }
  function playSpeech(blob) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob), a = new Audio(url);
      const end = () => { URL.revokeObjectURL(url); if (voice.audio === a) voice.audio = null; resolve(); };
      voice.audio = a; a.onended = end; a.onerror = end;
      Promise.resolve(a.play()).catch(err => { URL.revokeObjectURL(url); voice.audio = null; reject(err); });
    });
  }
  // Say `text` after whatever is already being said. `onFail(err)` if it can't be said.
  function speakNow(text, onFail) {
    text = N ? N.redact(text) : String(text || '');          // never a key out loud, nor to be voiced
    const parts = Q ? Q.chunks(plain(text).slice(0, 3900)) : [plain(text).slice(0, 3900)].filter(Boolean);
    if (!parts.length) return voice.tail;
    const epoch = voice.epoch;
    let done = 0;
    const play = (blob, i) => playSpeech(blob).then(() => { done = i + 1; });
    const sayFrom = from => {
      const rest = parts.slice(from), stop = () => voice.epoch !== epoch, base = from;
      if (Q) return Q.run(rest, makeSpeech, (blob, i) => play(blob, base + i), stop);
      return makeSpeech(rest[0]).then(blob => stop() ? null : play(blob, base));
    };
    voice.pending++;
    const job = voice.tail.then(async () => {
      if (voice.epoch !== epoch) return;
      try { await sayFrom(0); }
      catch (err) {
        if (!err.busy) throw err;                         // Voicebox is busy with another reply: wait, then once more
        await new Promise(r => setTimeout(r, RETRY_MS));
        if (voice.epoch === epoch) await sayFrom(done);
      }
    }).catch(err => { if (onFail) onFail(err); }).finally(() => { voice.pending--; });
    voice.tail = job;
    return job;
  }
  function hush() {                                       // stop talking now, and drop what was waiting
    voice.epoch++;
    if (voice.audio) { try { voice.audio.pause(); } catch (_) {} voice.audio = null; }
  }
  const speak = text => speakNow(text, err => say('Could not read it out: ' + err.message, 'error'));
  // A step as it arrives (never the ones already there when the session opened).
  function narrateStep(e) {
    if (narrate === 'off' || !N) return;
    const m = N.milestone(e);
    if (m) { speakNow(m, () => chime(e.kind === 'done' ? e.status : e.kind === 'checks' && e.state === 'failed' ? 'failed' : 'done')); return; }
    if (narrate !== 'play') return;
    const phrase = voice.pace.offer(N.playByPlay(e), Date.now(), voice.pending > 0 || !!voice.audio);
    if (phrase) speakNow(phrase);                         // a missed phrase is just skipped
  }

  // Celine: ask out loud (🎙 or Ctrl+Shift+Space) or typed (/celine …) about the open session.
  // Her answer comes from the companion turn engine (/api/companion/chat, workspace 'code'),
  // shows in her card, and is said in the voice you chose for her.
  const celine = {busy: false, ctrl: null, dismissed: false};
  // ✕, Quiet, another session or home: her answer stops streaming, never reopens her card
  // and is never said.
  function celineStop() {
    celine.dismissed = true;
    if (celine.ctrl) { try { celine.ctrl.abort(); } catch (_) {} }
    celine.ctrl = null; hush();
  }
  const celineKey = sid => `apex.code.celine.${sid}`;
  const turnId = () => 'code-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  function celineCard(question, answer, state) {
    $('celine-card').hidden = false;
    if (question != null) $('celine-q').textContent = question;
    if (answer != null) { $('celine-a').replaceChildren(md(answer, state === 'thinking')); }
    $('celine-state').textContent = state === 'listening' ? 'listening…' : state === 'thinking' ? 'thinking…' : state === 'error' ? 'could not answer' : '';
    $('celine-card').dataset.state = state || '';
  }
  async function askCeline(typed) {
    if (rec) { if ($('celine-btn').classList.contains('rec')) rec.stop(); else say('Finish the recording you started first.'); return; }
    if (!current) { say('Open a session first: Celine answers about one session.', 'error'); return; }
    if (celine.busy) { say('Celine is still answering.'); return; }
    const sid = current;
    let question = String(typed || '').trim();
    if (!question) {
      const blob = await recordOnce($('celine-btn'), 'Celine is listening… press 🎙 again (or Ctrl+Shift+Space) when you\'re done.');
      if (!blob) return;
      celineCard('…', '', 'listening');
      try { question = await transcribe(blob); } catch (err) { celineCard('', 'Could not transcribe: ' + err.message, 'error'); return; }
      if (!question) { celineCard('', 'I didn\'t catch that. Try again, a little closer.', 'error'); return; }
    }
    if (current !== sid) return;                          // you moved on while she listened
    const ctrl = new AbortController();
    celine.busy = true; celine.ctrl = ctrl; celine.dismissed = false; $('celine-btn').disabled = true;
    const wanted = () => current === sid && !celine.dismissed && celine.ctrl === ctrl;
    celineCard(question, '', 'thinking');
    try {
      const answer = await celineChat(sid, question, true, ctrl, wanted);
      if (wanted()) {
        celineCard(question, answer, '');
        if (answer.trim()) speakNow(answer, err => say('Celine\'s answer is on screen; she couldn\'t say it: ' + err.message, 'error'));
      }
    } catch (err) { if (err.name !== 'AbortError' && wanted()) celineCard(question, err.message, 'error'); }
    finally { if (celine.ctrl === ctrl) celine.ctrl = null; celine.busy = false; $('celine-btn').disabled = false; }
  }
  async function celineChat(sid, question, retry, ctrl, wanted) {
    const thread = Number(store.get(celineKey(sid))) || null;
    const body = {message: question, turn_id: turnId(), mode: 'discuss', workspace: 'code', code_session: sid,
      voice: 'voicebox', voice_profile: store.get('apex.voicebox.profile'), ...(thread ? {thread_id: thread} : {})};
    const r = await fetch('/api/companion/chat', {method: 'POST', headers: {'Content-Type': 'application/json', ...auth()}, body: JSON.stringify(body),
      signal: ctrl && ctrl.signal});
    if (r.status === 401) { $('login').showModal(); throw new Error('Enter your Apex token.'); }
    if (!r.ok) {
      const b = await r.json().catch(() => ({})), why = typeof b.detail === 'string' ? b.detail : `Celine couldn't answer (${r.status})`;
      if (retry && thread && /no longer exists/i.test(why)) { store.set(celineKey(sid), ''); return celineChat(sid, question, false, ctrl, wanted); }
      throw new Error(why);
    }
    let text = '', buffer = '', final = null;
    const take = line => {
      if (!line.trim()) return;
      let ev; try { ev = JSON.parse(line); } catch (_) { return; }
      if (ev.type === 'start' && ev.thread_id) store.set(celineKey(sid), String(ev.thread_id));
      else if (ev.type === 'token') { text += ev.text || ''; if (!wanted || wanted()) celineCard(null, text, 'thinking'); }
      else if (ev.type === 'done') final = ev.text != null ? ev.text : text;
      else if (ev.type === 'error') throw new Error(ev.text || 'Celine stopped with an error.');
    };
    if (r.body && r.body.getReader) {
      const reader = r.body.getReader(), dec = new TextDecoder();
      for (;;) {
        const {value, done} = await reader.read();
        if (done || (wanted && !wanted())) break;
        buffer += dec.decode(value, {stream: true});
        const lines = buffer.split('\n'); buffer = lines.pop();
        lines.forEach(take);
      }
      take(buffer);
    } else (await r.text()).split('\n').forEach(take);
    return String(final != null ? final : text);
  }
  $('celine-btn').onclick = () => askCeline();
  $('celine-quiet').onclick = celineStop;
  $('celine-close').onclick = () => { celineStop(); $('celine-card').hidden = true; };
  $('add-project').onclick = () => { $('add-form').reset(); $('add-dialog').returnValue = ''; $('add-dialog').showModal(); };
  $('add-dialog').addEventListener('close', async () => {
    if ($('add-dialog').returnValue !== 'ok') return;
    try {
      const p = await post('/api/code/projects', {path: $('add-path').value, name: $('add-name').value});
      project = p.id; store.set('apex.code.project', project); await loadOverview(); loadBrain(); say(`Added ${p.name}.`, 'good');
    } catch (err) { say(err.message, 'error'); }
  });
  $('token-save').onclick = () => { store.set('apex_token', $('token').value.trim()); boot(); };
  function closeDrawers() { $('rail').classList.remove('open'); $('side').classList.remove('open'); $('scrim').hidden = true; }
  $('open-rail').onclick = () => { $('rail').classList.add('open'); $('scrim').hidden = false; };
  $('open-side').onclick = () => { $('side').classList.add('open'); $('scrim').hidden = false; };
  $('close-side').onclick = closeDrawers; $('scrim').onclick = closeDrawers;
  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    const dialog = [...document.querySelectorAll('dialog')].some(d => d.open);
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); if (!$('palette').open) openPalette(); return; }
    if (e.ctrlKey && e.shiftKey && (e.code === 'Space' || e.key === ' ')) { e.preventDefault(); if (current && !dialog) askCeline(); return; }
    if (e.ctrlKey && e.key === '`' && current) { e.preventDefault(); tab('terminal'); $('t-cmd').focus(); return; }
    if (e.key === 'Escape' && busy() && !dialog) { e.preventDefault(); stop(); }
    else if (e.key === '/' && !typing && !dialog) { e.preventDefault(); $('prompt').focus(); }
  });
  addEventListener('hashchange', () => {
    const ask = location.hash.match(/^#allow=([\w-]+)/);
    if (ask) { askAllow(ask[1]); return; }                  // a notification tapped while this page was open
    if (location.hash === '#overnight') {                 // the morning brief, tapped while this page was open
      home(); loadOverview().catch(err => say(err.message, 'error')); loadOvernight(true); return;
    }
    const m = location.hash.match(/^#s=(\d+)/); if (m && Number(m[1]) !== current) open(Number(m[1])); else if (!m && current) home();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(100); });

  async function boot() {
    // A phone's link to answer a stopped command comes first: a device token can answer
    // it, but can't read the rest (403), and that owner-only message isn't for this.
    const ask = location.hash.match(/^#allow=([\w-]+)/);
    if (ask) await askAllow(ask[1]);
    try { await loadOverview(); }
    catch (err) {
      if (ask && err.status === 403) return;
      say(/owner only/i.test(err.message) ? 'Apex Code is for the owner: open Apex with your master token.' : err.message, 'error'); return;
    }
    const saved = choices[engine] || {model: store.get(`apex.code.model.${engine}`, ''), effort: store.get(`apex.code.effort.${engine}`, '')};
    model = saved.model; effort = saved.effort; renderEngine(); loadModels();
    const m = location.hash.match(/^#s=(\d+)/), morning = location.hash === '#overnight';
    if (m) await open(Number(m[1])); else home();
    if (morning) await loadOvernight(true);                // the morning brief's link: what was built overnight
    schedule(1000);
  }
  applyLook(); show('home'); boot();
})();
