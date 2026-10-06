// Work page (dashboard/work.py, agent/work.py): projects and tasks across
// job, studies, business and software; Today, Board and Projects views; and
// handing a task to Apex, whose result comes back for your review; and the
// Always on agent (agent/work_agent.py), which can work on your Claude or
// ChatGPT plan instead of API credits.
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const token = () => { try { return localStorage.getItem('apex_token') || ''; } catch (_) { return ''; } };
  async function api(path, options = {}) {
    const r = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...(token() ? {Authorization: 'Bearer ' + token()} : {})}});
    if (r.status === 401) { $('login').showModal(); throw new Error('Enter your Apex token.'); }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
    return body;
  }
  const say = (text, kind = '') => { $('message').textContent = text; $('message').className = 'message ' + kind; };

  let data = {areas: [], projects: [], tasks: [], today: {}};
  let view = 'today', area = '', open = null;
  try { view = localStorage.getItem('apex.work.view') || 'today'; area = localStorage.getItem('apex.work.area') || ''; } catch (_) {}
  const projectOf = t => data.projects.find(p => p.id === t.project_id);
  const visible = list => (list || []).filter(t => !area || t.area === area);
  const COLUMNS = [['todo', 'To do'], ['doing', 'Doing'], ['waiting', 'Waiting'], ['review', 'Review'], ['done', 'Done']];
  const APEX_ACTIVE = ['queued', 'running', 'verifying'];

  function todayISO() { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  function dueLabel(due) {
    if (!due) return null;
    const days = Math.round((new Date(due + 'T00:00') - new Date(todayISO() + 'T00:00')) / 86400000);
    if (days < 0) return {text: days === -1 ? 'yesterday' : `${-days} days late`, cls: 'late'};
    if (days === 0) return {text: 'today', cls: 'soon'};
    if (days === 1) return {text: 'tomorrow', cls: 'soon'};
    if (days < 7) return {text: new Date(due + 'T00:00').toLocaleDateString(undefined, {weekday: 'short'}), cls: ''};
    return {text: new Date(due + 'T00:00').toLocaleDateString(undefined, {day: 'numeric', month: 'short'}), cls: ''};
  }

  function taskRow(t) {
    const row = el('div', 'task' + (t.status === 'done' ? ' is-done' : ''));
    row.style.setProperty('--c', `var(--${t.area})`);
    row.draggable = true; row.dataset.id = t.id;
    row.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', String(t.id)));
    const done = el('button', 'done'); done.title = t.status === 'done' ? 'Mark not done' : 'Mark done';
    done.setAttribute('aria-label', done.title);
    done.onclick = () => setStatus(t, t.status === 'done' ? 'todo' : 'done');
    const main = el('div', 't-main'); main.onclick = () => openDetail(t);
    main.append(el('span', 't-title', t.title));
    const meta = el('div', 't-meta');
    const due = dueLabel(t.due); if (due && t.status !== 'done') meta.append(el('span', due.cls, due.text));
    const p = projectOf(t); if (p) meta.append(el('span', '', p.name));
    if (APEX_ACTIVE.includes(t.apex_state)) meta.append(el('span', 'apex', 'Apex is working…'));
    if (t.status === 'review') meta.append(el('span', 'rev', 'Apex finished · review'));
    if (t.status === 'waiting' && t.waiting_on) meta.append(el('span', '', 'waiting on ' + t.waiting_on));
    if (t.apex_ok && !t.apex_state && t.status !== 'done' && t.status !== 'review') meta.append(el('span', 'apex', '+apex'));
    main.append(meta);
    const prio = el('span', `prio p${t.priority}`); prio.title = ['', 'High', 'Normal', 'Low'][t.priority] + ' priority';
    row.append(done, prio, main);
    if (t.status !== 'done' && t.status !== 'review' && !APEX_ACTIVE.includes(t.apex_state)) {
      const give = el('button', 'give', '▶ Apex'); give.title = 'Give this task to Apex';
      give.onclick = () => openDetail(t, true);
      row.append(give);
    }
    return row;
  }

  function section(title, list, cls = '', emptyText = null) {
    const s = el('div', 'section ' + cls); const h = el('h2', '', title); h.append(el('em', '', String(list.length))); s.append(h);
    if (!list.length) { if (emptyText) s.append(el('p', 'empty', emptyText)); else return null; }
    for (const t of list) s.append(taskRow(t));
    return s;
  }

  function renderToday() {
    const v = data.today, root = $('view-today'); root.replaceChildren();
    const parts = [
      section('Overdue', visible(v.overdue), 'overdue'),
      section('Today', visible(v.today), '', 'Nothing due today.'),
      section('Apex finished · your review', visible(v.review), 'review'),
      section('Apex is working', visible(v.apex_working), 'apex'),
      section('In progress', visible(v.doing)),
      section('Next 7 days', visible(v.week)),
      section('Waiting on others', visible(v.waiting)),
    ].filter(Boolean);
    const undated = visible(data.tasks.filter(t => !t.due && t.status === 'todo'));
    if (undated.length) parts.push(section('No date', undated));
    if (!data.tasks.length) parts.push(el('p', 'empty', 'No tasks yet. Add one above, or say "add a task" to Apex in chat or by voice.'));
    root.append(...parts);
  }

  function renderBoard() {
    const root = $('view-board'); root.replaceChildren();
    for (const [status, label] of COLUMNS) {
      const col = el('div', 'col'); col.dataset.status = status;
      const list = visible(data.tasks.filter(t => t.status === status));
      col.append(el('h3', '', `${label} · ${list.length}`));
      for (const t of list) col.append(taskRow(t));
      col.addEventListener('dragover', e => { e.preventDefault(); col.classList.add('over'); });
      col.addEventListener('dragleave', () => col.classList.remove('over'));
      col.addEventListener('drop', e => {
        e.preventDefault(); col.classList.remove('over');
        const t = data.tasks.find(x => x.id === Number(e.dataTransfer.getData('text/plain')));
        if (t && t.status !== status) setStatus(t, status);
      });
      root.append(col);
    }
  }

  function renderProjects() {
    const list = $('project-list'); list.replaceChildren();
    const projects = data.projects.filter(p => !area || p.area === area);
    if (!projects.length) list.append(el('p', 'empty', 'No projects yet. Projects group tasks; use @name in quick add to file a task under one.'));
    for (const p of projects) {
      const row = el('div', 'project'); row.style.setProperty('--c', `var(--${p.area})`);
      const open = data.tasks.filter(t => t.project_id === p.id && t.status !== 'done');
      const late = open.filter(t => t.due && t.due < todayISO()).length;
      row.append(el('b', '', p.name), el('span', 'muted', [p.client, `${open.length} open`, late ? `${late} late` : ''].filter(Boolean).join(' · ')));
      const go = el('button', '', 'Board'); go.onclick = () => { area = p.area; show('board'); };
      const archive = el('button', '', 'Archive');
      archive.onclick = async () => { try { await api(`/api/work/projects/${p.id}`, {method: 'PATCH', body: JSON.stringify({status: 'archived'})}); load(); } catch (e) { say(e.message, 'error'); } };
      row.append(go, archive); list.append(row);
    }
  }

  function renderAreas() {
    const root = $('areas'); root.replaceChildren();
    const counts = data.today.counts || {};
    for (const a of [{id: '', name: 'All'}, ...data.areas]) {
      const b = el('button', 'chip', a.name); b.setAttribute('aria-pressed', String(area === a.id));
      if (a.id) { b.style.setProperty('--c', `var(--${a.id})`); if (counts[a.id]) b.append(el('i', '', String(counts[a.id]))); }
      b.onclick = () => { area = a.id; try { localStorage.setItem('apex.work.area', area); } catch (_) {} render(); };
      root.append(b);
    }
  }

  function renderHeader() {
    const v = data.today;
    $('date-line').textContent = new Date().toLocaleDateString(undefined, {weekday: 'long', day: 'numeric', month: 'long'}).toUpperCase();
    const late = visible(v.overdue).length, due = visible(v.today).length, review = visible(v.review).length;
    $('headline').textContent = late ? `${late} overdue, ${due} due today.` : due ? `${due} due today.` : 'Clear for today.';
    $('subline').textContent = review ? `Apex finished ${review} task${review > 1 ? 's' : ''} for you to review.` : 'Add tasks in one line. Hand the ones Apex can do to Apex.';
    const stats = $('stats'); stats.replaceChildren();
    for (const [n, label, cls] of [[late, 'overdue', late ? 'bad' : ''], [due, 'due today', due ? 'warn' : ''],
      [visible(v.week).length, 'this week', ''], [visible(v.apex_working).length, 'Apex working', ''], [review, 'to review', review ? 'good' : '']]) {
      const s = el('div', 'stat ' + cls); s.append(el('b', '', String(n)), el('span', '', label)); stats.append(s);
    }
  }

  function render() {
    renderHeader(); renderAreas();
    for (const name of ['today', 'board', 'projects', 'agent']) {
      $('view-' + name).hidden = view !== name;
      document.querySelector(`[data-view=${name}]`).setAttribute('aria-selected', String(view === name));
    }
    $('areas').hidden = view === 'agent';
    if (view === 'today') renderToday(); else if (view === 'board') renderBoard(); else if (view === 'agent') loadAgent(); else renderProjects();
  }
  function show(name) { view = name; try { localStorage.setItem('apex.work.view', view); } catch (_) {} render(); }
  document.querySelectorAll('[data-view]').forEach(b => { b.onclick = () => show(b.dataset.view); });

  async function load() {
    try { data = await api('/api/work'); render(); }
    catch (e) { say(e.message, 'error'); }
  }
  async function setStatus(t, status) {
    try { await api(`/api/work/tasks/${t.id}`, {method: 'PATCH', body: JSON.stringify({status})}); await load(); }
    catch (e) { say(e.message, 'error'); }
  }

  $('quick').onsubmit = async e => {
    e.preventDefault();
    const text = $('quick-text').value.trim(); if (!text) return;
    try {
      const body = {quick: text}; if (area && !/#\w/.test(text)) body.area = area;
      const t = await api('/api/work/tasks', {method: 'POST', body: JSON.stringify(body)});
      $('quick-text').value = ''; say(`Added "${t.title}"` + (t.due ? `, due ${dueLabel(t.due).text}` : '') + '.', 'good');
      await load();
    } catch (err) { say(err.message, 'error'); }
  };

  // Detail panel ------------------------------------------------------------
  function fillSelect(select, options, value) {
    select.replaceChildren(...options.map(([v, t]) => { const o = el('option', '', t); o.value = v; return o; }));
    select.value = value == null ? '' : String(value);
  }
  async function openDetail(t, focusApex = false) {
    open = t;
    $('d-title').value = t.title; $('d-due').value = t.due || ''; $('d-priority').value = String(t.priority);
    $('d-status').value = t.status; $('d-notes').value = t.notes || ''; $('d-waiting').value = t.waiting_on || '';
    fillSelect($('d-area'), data.areas.map(a => [a.id, a.name]), t.area);
    fillSelect($('d-project'), [['', 'No project'], ...data.projects.map(p => [p.id, p.name])], t.project_id);
    const working = APEX_ACTIVE.includes(t.apex_state);
    $('d-apex-state').textContent = working ? 'Apex is working on this now. You can close this; it keeps going.'
      : t.apex_state === 'done' ? `Apex finished${t.apex_cost ? ` · $${Number(t.apex_cost).toFixed(2)}` : ''}. Check the result, then mark it done.`
      : t.apex_state ? `Apex stopped (${t.apex_state}). See what it got to below, then try again or finish it yourself.`
      : t.apex_summary ? 'That plan could not take it (see below). Try the other plan, or Always on will.'
      : 'Apex can research this and draft the deliverable for you.';
    $('d-apex-ok').checked = !!t.apex_ok;
    $('d-engine').value = (agent && agent.engines && agent.engines[0]) || 'claude'; budgetShown();
    $('d-apex-summary').hidden = !t.apex_summary; $('d-apex-summary').textContent = t.apex_summary || '';
    $('d-apex-files').replaceChildren();
    $('d-stop').hidden = !working;
    $('d-give').disabled = working; $('d-give').textContent = t.apex_state && !working ? 'Ask Apex again' : 'Give to Apex';
    $('detail').showModal();
    if (focusApex) $('d-give').focus();
    if (t.apex_folder) {
      try {
        const full = await api(`/api/work/tasks/${t.id}`);
        for (const f of full.files) $('d-apex-files').append(el('li', '', `${full.apex_folder}\\${f}`));
      } catch (_) {}
    }
  }
  $('d-save').onclick = async () => {
    if (!open) return;
    try {
      await api(`/api/work/tasks/${open.id}`, {method: 'PATCH', body: JSON.stringify({
        title: $('d-title').value, due: $('d-due').value || null, priority: Number($('d-priority').value), status: $('d-status').value,
        area: $('d-area').value, project_id: $('d-project').value ? Number($('d-project').value) : null,
        notes: $('d-notes').value, waiting_on: $('d-waiting').value, apex_ok: $('d-apex-ok').checked})});
      $('detail').close(); say('Saved.', 'good'); await load();
    } catch (e) { say(e.message, 'error'); }
  };
  $('d-delete').onclick = async () => {
    if (!open || !confirm(`Delete "${open.title}"?`)) return;
    try { await api(`/api/work/tasks/${open.id}`, {method: 'DELETE'}); $('detail').close(); await load(); }
    catch (e) { say(e.message, 'error'); }
  };
  $('d-give').onclick = async () => {
    if (!open) return;
    $('d-give').disabled = true;
    try {
      const engine = $('d-engine').value;
      await api(`/api/work/tasks/${open.id}/apex`, {method: 'POST', body: JSON.stringify({budget_usd: Number($('d-budget').value), engine})});
      $('detail').close(); say(`Apex is on it, on your ${$('d-engine').selectedOptions[0].textContent}. The task comes back to you for review when it is done.`, 'good'); await load();
    } catch (e) { say(e.message, 'error'); $('d-give').disabled = false; }
  };

  $('d-stop').onclick = async () => {
    if (!open) return;
    $('d-stop').disabled = true;
    try { await api(`/api/work/tasks/${open.id}/apex/stop`, {method: 'POST', body: '{}'}); $('detail').close(); say('Stopped. Anything Apex wrote so far is in its folder.', 'good'); await load(); }
    catch (e) { say(e.message, 'error'); }
    $('d-stop').disabled = false;
  };
  function budgetShown() { $('d-budget-label').hidden = $('d-engine').value !== 'api'; }
  $('d-engine').onchange = budgetShown;

  // Always on -----------------------------------------------------------------
  let agent = null, order = [];
  const PLAN_NAMES = {claude: 'Claude plan', chatgpt: 'ChatGPT plan', api: 'API credits'};
  function renderPlans() {
    const root = $('a-plans'); root.replaceChildren();
    const plans = Object.fromEntries((agent.plans || []).map(p => [p.id, p]));
    const all = [...order, ...Object.keys(PLAN_NAMES).filter(e => !order.includes(e))];
    all.forEach(e => {
      const on = order.includes(e), p = plans[e] || {};
      const li = el('li', on ? '' : 'off');
      const box = el('input'); box.type = 'checkbox'; box.checked = on; box.setAttribute('aria-label', 'Use ' + PLAN_NAMES[e]);
      box.onchange = () => { order = box.checked ? [...order, e] : order.filter(x => x !== e); renderPlans(); };
      li.append(box, el('span', 'name', (on ? `${order.indexOf(e) + 1}. ` : '') + PLAN_NAMES[e]));
      li.append(el('span', 'why' + (p.unavailable ? '' : ' ok'), p.unavailable || (e === 'api' ? 'uses credits, within the caps below' : 'ready')));
      if (on && order.indexOf(e) > 0) {
        const up = el('button', '', '↑'); up.setAttribute('aria-label', 'Move ' + PLAN_NAMES[e] + ' up');
        up.onclick = () => { const i = order.indexOf(e); order.splice(i, 1); order.splice(i - 1, 0, e); renderPlans(); };
        li.append(up);
      }
      if (p.unavailable && p.how) li.append(el('span', 'how', p.how));
      root.append(li);
    });
  }
  function renderAgent() {
    $('agent-dot').className = 'dot' + (agent.enabled ? ' on' : '');
    if (view !== 'agent' || $('agent-form').contains(document.activeElement)) return;
    $('a-enabled').checked = agent.enabled; $('a-auto').checked = agent.auto_work;
    $('a-runs').value = agent.plan_runs; $('a-rest').value = agent.rest_hours;
    $('a-brief').value = agent.brief_time; $('a-evening').value = agent.evening_time;
    $('a-task').value = agent.task_budget; $('a-day').value = agent.daily_budget;
    order = [...agent.engines]; renderPlans();
    $('a-summary').textContent = !agent.enabled ? 'Off.' : `${agent.plan_runs_today} of ${agent.plan_runs} plan tasks today · ${agent.eligible} marked +apex` +
      (agent.engines.includes('api') ? ` · $${Number(agent.spent_today).toFixed(2)} of API credits today` : '');
    const ev = $('a-events'); ev.replaceChildren();
    if (!agent.events.length) ev.append(el('p', 'empty', 'Nothing yet. Turn on Always on, and Apex will note everything it does here.'));
    for (const e of agent.events) {
      const row = el('div', 'event');
      row.append(el('time', '', new Date(e.ts * 1000).toLocaleString(undefined, {weekday: 'short', hour: '2-digit', minute: '2-digit'})), el('span', '', e.text));
      ev.append(row);
    }
  }
  async function loadAgent() {
    try { agent = await api('/api/work/agent'); renderAgent(); } catch (e) { say(e.message, 'error'); }
  }
  async function saveAgent(extra = {}) {
    if (!order.length) { say('Choose at least one way for Apex to work.', 'error'); return; }
    try {
      agent = await api('/api/work/agent', {method: 'PUT', body: JSON.stringify({
        enabled: $('a-enabled').checked, auto_work: $('a-auto').checked, engines: order,
        plan_runs: Number($('a-runs').value), rest_hours: Number($('a-rest').value),
        brief_time: $('a-brief').value, evening_time: $('a-evening').value,
        task_budget: Number($('a-task').value), daily_budget: Number($('a-day').value), ...extra})});
      document.activeElement && document.activeElement.blur();
      renderAgent(); say(agent.enabled ? 'Saved. Apex is always on.' : 'Saved. Always on is off.', 'good');
    } catch (e) { say(e.message, 'error'); }
  }
  $('agent-form').onsubmit = e => { e.preventDefault(); saveAgent(); };
  $('a-clear').onclick = () => saveAgent({clear_limits: true});
  $('a-check').onclick = async () => {
    $('a-check').disabled = true; say('Asking Claude Code and Codex how they are signed in…');
    try {
      agent = await api('/api/work/agent/check', {method: 'POST', body: '{}'}); renderAgent();
      const bad = agent.plans.filter(p => p.id !== 'api' && agent.engines.includes(p.id) && p.unavailable);
      say(bad.length ? bad.map(p => `${p.name} ${p.unavailable}.`).join(' ') : 'Your plans are signed in and ready.', bad.length ? 'error' : 'good');
    } catch (e) { say(e.message, 'error'); }
    $('a-check').disabled = false;
  };

  // Projects -----------------------------------------------------------------
  $('new-project').onsubmit = async e => {
    e.preventDefault();
    try {
      await api('/api/work/projects', {method: 'POST', body: JSON.stringify({name: $('project-name').value, area: $('project-area').value, client: $('project-client').value})});
      $('new-project').reset(); await load();
    } catch (err) { say(err.message, 'error'); }
  };

  $('token-save').onclick = () => { try { localStorage.setItem('apex_token', $('token').value.trim()); } catch (_) {} load(); };
  (async () => {
    await load();
    loadAgent();
    fillSelect($('project-area'), data.areas.map(a => [a.id, a.name]), area || 'job');
  })();
  // Apex's progress shows up without reloading.
  setInterval(() => { if (!document.hidden && !$('detail').open && data.tasks.some(t => APEX_ACTIVE.includes(t.apex_state))) load(); }, 5000);
  setInterval(() => { if (!document.hidden && view === 'agent') loadAgent(); }, 30000);
})();
