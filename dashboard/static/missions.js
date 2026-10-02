// Missions page (dashboard/missions.py, agent/missions.py): start a mission
// with checks Apex can run, and watch it work round by round.
(() => {
  const $ = id => document.getElementById(id);
  const token = () => { try { return localStorage.getItem('apex_token') || ''; } catch (_) { return ''; } };
  async function api(path, options = {}) {
    const r = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...(token() ? {Authorization: 'Bearer ' + token()} : {})}});
    if (r.status === 401) { $('login').showModal(); throw new Error('Enter your Apex token.'); }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
    return body;
  }
  const say = (text, kind = '') => { $('message').textContent = text; $('message').className = 'message ' + kind; };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const LABEL = {running: 'Working', complete: 'Done ✓', paused: 'Paused', needs_you: 'Needs you', stuck: 'Stuck', out_of_budget: 'Out of budget', stopped: 'Stopped'};

  function addCheck(kind = 'file_exists', spec = '', detail = '') {
    const row = el('div', 'check-row'), sel = el('select'), s = el('input'), d = el('input'), rm = el('button', '', '×');
    for (const [v, t] of [['command', 'Command passes'], ['file_exists', 'File exists'], ['contains', 'File contains']]) { const o = el('option', '', t); o.value = v; sel.append(o); }
    sel.value = kind; s.value = spec; d.value = detail; rm.type = 'button'; rm.title = 'Remove';
    const sync = () => { s.placeholder = {command: 'python -m pytest -q', file_exists: 'C:\\path\\to\\result.md', contains: 'C:\\path\\to\\file.txt'}[sel.value];
      d.placeholder = sel.value === 'contains' ? 'text it must include' : '(not needed)'; d.disabled = sel.value !== 'contains'; };
    sel.onchange = sync; sync(); rm.onclick = () => row.remove();
    row.append(sel, s, d, rm); $('checks').append(row);
  }
  $('add-check').onclick = () => addCheck();
  addCheck('command', 'python -m pytest -q');

  $('new').onsubmit = async e => {
    e.preventDefault();
    const checks = [...$('checks').children].map(r => { const [k, s, d] = r.querySelectorAll('select,input'); return {kind: k.value, spec: s.value.trim(), detail: d.value.trim()}; }).filter(c => c.spec);
    $('start').disabled = true; say('Starting…');
    try {
      await api('/api/missions', {method: 'POST', body: JSON.stringify({title: $('title').value, task: $('task').value, context: $('context').value,
        checks, budget_usd: Number($('budget').value), max_rounds: Number($('rounds').value)})});
      say('Mission started. It runs on this computer even if you close this page.', 'good'); $('new').reset(); $('checks').replaceChildren(); addCheck('command', 'python -m pytest -q');
      refresh();
    } catch (err) { say(err.message, 'error'); } finally { $('start').disabled = false; }
  };

  async function act(m, action) {
    let body = {};
    if (action === 'resume' && ['stuck', 'out_of_budget'].includes(m.status)) {
      const rounds = Number(prompt('How many more rounds?', '3') || 0), money = Number(prompt('How much more budget ($)?', m.status === 'out_of_budget' ? '2' : '0') || 0);
      body = {extra_rounds: Math.max(0, Math.round(rounds)), extra_budget: Math.max(0, money)};
    }
    if (action === 'stop' && !confirm('Stop this mission for good? Work already done stays done.')) return;
    try { await api(`/api/missions/${m.id}/${action}`, {method: 'POST', body: JSON.stringify(body)}); refresh(); }
    catch (err) { alert(err.message); }
  }

  const open = new Set();
  function render(list) {
    const root = $('list'); root.replaceChildren();
    if (!list.length) { root.append(el('p', 'muted', 'No missions yet.')); return; }
    for (const m of list) {
      const card = el('article', 'mission'), head = el('div', 'm-head');
      head.append(el('b', '', m.title), el('span', 'pill ' + m.status, LABEL[m.status] || m.status));
      const bar = el('div', 'spend'), fill = el('i'); fill.style.width = Math.min(100, 100 * m.spent_usd / m.budget_usd) + '%'; bar.append(fill);
      const meta = el('div', 'meta', `Round ${m.rounds.length} of ${m.max_rounds} · $${m.spent_usd.toFixed(2)} of $${m.budget_usd.toFixed(2)} · ${m.checks.length} check${m.checks.length === 1 ? '' : 's'}`);
      card.append(head, el('p', 'reason', m.reason), bar, meta);
      const actions = el('div', 'm-actions');
      const button = (text, action) => { const b = el('button', '', text); b.onclick = () => act(m, action); actions.append(b); };
      if (m.status === 'running') button('Pause', 'pause');
      if (['paused', 'needs_you', 'stuck', 'out_of_budget'].includes(m.status)) button('Resume', 'resume');
      if (!['complete', 'stopped', 'stuck', 'out_of_budget'].includes(m.status)) button('Stop', 'stop');
      if (actions.children.length) card.append(actions);
      if (m.rounds.length) {
        const det = el('details'); det.open = open.has(m.id); det.ontoggle = () => det.open ? open.add(m.id) : open.delete(m.id);
        det.append(el('summary', '', 'Rounds'));
        for (const r of [...m.rounds].reverse()) {
          const box = el('div', 'round');
          box.append(el('div', '', `Round ${r.n} · ${r.status}${r.verified ? ' · checks passed' : ''} · $${r.cost_usd.toFixed(2)}${r.error ? ' · ' + r.error : ''}`));
          for (const c of r.checks) box.append(el('div', c.passed ? 'ok' : 'no', `${c.passed ? '✓' : '✗'} [${c.kind}] ${c.spec} → ${c.evidence}`));
          if (r.summary) box.append(el('pre', '', r.summary));
          det.append(box);
        }
        card.append(det);
      }
      root.append(card);
    }
  }
  async function refresh() { try { render((await api('/api/missions')).missions); } catch (err) { $('list').replaceChildren(el('p', 'muted', err.message)); } }
  $('login-form').onsubmit = e => { e.preventDefault(); try { localStorage.setItem('apex_token', $('token').value.trim()); } catch (_) {} $('login').close(); refresh(); };
  refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 3000);
})();
