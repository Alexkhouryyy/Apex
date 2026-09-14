/* Task state is server-owned. Text is rendered without HTML interpretation. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const roles = ['researcher', 'coder', 'reviewer'];
  const names = {researcher:'Research', coder:'Coding', reviewer:'Review'};
  let pending = null;
  try { pending = JSON.parse(sessionStorage.getItem('apex_team_pending')); } catch (_) {}
  const expanded = new Set();
  const evidenceExpanded = new Set();
  if (pending) $('team-start').textContent = 'Recover submitted task';
  let loading = false, modelsLoaded = false;
  function node(tag, text) { const el = document.createElement(tag); el.textContent = text; return el; }
  async function api(path, body) {
    const token = localStorage.getItem('apex_token');
    const r = await fetch(path, {method:body ? 'POST' : 'GET', headers:{...(token ? {Authorization:`Bearer ${token}`} : {}), ...(body ? {'Content-Type':'application/json'} : {})}, ...(body ? {body:JSON.stringify(body)} : {})});
    const data = await r.json();
    if (!r.ok) { const error = new Error(r.status === 401 ? 'Sign in to the dashboard to view tasks.' : data.detail || 'Request failed.'); error.status = r.status; throw error; }
    return data;
  }
  for (const role of roles) {
    const label = node('label', names[role] + ' ');
    const check = document.createElement('input'); check.type='checkbox'; check.checked=true; check.id='team-use-'+role;
    const model = document.createElement('input'); model.id='team-model-'+role; model.setAttribute('list','team-model-options'); model.placeholder='Current startup model'; model.setAttribute('aria-label',names[role]+' model');
    label.prepend(check); label.append(model); $('team-roles').append(label);
  }
  if (pending) {
    $('team-task').value=pending.task; $('team-context').value=pending.context;
    $('team-budget').value=pending.budget_usd;
    for (const r of roles) $('team-use-'+r).checked=pending.roles.includes(r);
    for (const r of [...roles,'apex']) $('team-model-'+r).value=pending.models[r] || '';
  }
  async function loadModels() {
    if (modelsLoaded) return;
    const data=await api('/api/models');
    $('team-model-options').replaceChildren();
    for (const model of data.models || []) {
      if (model.available) { const option=document.createElement('option'); option.value=model.model; $('team-model-options').append(option); }
    }
    modelsLoaded=true;
  }
  function render(runs) {
    $('team-runs').replaceChildren();
    for (const run of runs) {
      const item = document.createElement('details'); item.open=expanded.has(run.id);
      item.addEventListener('toggle', () => item.open ? expanded.add(run.id) : expanded.delete(run.id));
      item.append(node('summary', `${run.task.slice(0,100)} · ${run.status} · ~$${run.cost_usd.toFixed(4)} · ${run.calls} calls`));
      item.append(node('p', run.context || 'No additional project context.'));
      if (run.error) item.append(node('p', run.error));
      if (['queued','running','stopping'].includes(run.status)) {
        const stop = node('button','Stop task'); stop.type='button';
        stop.onclick=async () => { try { await api(`/api/team/${run.id}/stop`,{}); stop.textContent='Stop requested'; stop.disabled=true; } catch(e) { $('team-error').textContent=e.message; } };
        item.append(stop);
      }
      for (const step of run.steps) {
        const section = document.createElement('section');
        section.append(node('h3',`${names[step.role] || 'Apex'} · ${step.status}`));
        section.append(node('p',`${step.model} · ~$${step.cost_usd.toFixed(4)} · ${step.calls} calls`));
        const result=node('pre',step.result || ''); result.style.whiteSpace='pre-wrap'; section.append(result);
        for (const [index, ev] of step.evidence.entries()) {
          const detail = document.createElement('details');
          const key = `${run.id}:${step.role}:${index}`; detail.open=evidenceExpanded.has(key);
          detail.addEventListener('toggle', () => detail.open ? evidenceExpanded.add(key) : evidenceExpanded.delete(key));
          detail.append(node('summary',`${ev.tool} · ${ev.status}`));
          const text=node('pre',ev.result); text.style.whiteSpace='pre-wrap'; detail.append(text); section.append(detail);
        }
        item.append(section);
      }
      $('team-runs').append(item);
    }
  }
  async function refresh() {
    if (loading || document.hidden || !$('tab-constellation').classList.contains('active')) return;
    loading=true;
    try { render((await api('/api/team')).runs); await loadModels(); } catch(e) { $('team-error').textContent=e.message; }
    finally { loading=false; }
  }
  $('team-form').onsubmit=async e => {
    e.preventDefault(); $('team-start').disabled=true; $('team-error').textContent='';
    try {
      if (!pending) {
        pending={id:crypto.randomUUID(), task:$('team-task').value, context:$('team-context').value, roles:roles.filter(r=>$('team-use-'+r).checked), models:Object.fromEntries([...roles,'apex'].map(r=>[r,$('team-model-'+r).value.trim()])), budget_usd:Number($('team-budget').value)};
        sessionStorage.setItem('apex_team_pending',JSON.stringify(pending));
      }
      const run=await api('/api/team',pending); expanded.add(run.id); pending=null; sessionStorage.removeItem('apex_team_pending'); await refresh();
    } catch(e) {
      $('team-error').textContent=e.message + ' If the connection failed, submit again to recover the same task.';
      // Keep the identifier for network retries. Validation errors may be corrected.
      if ([400,409,413].includes(e.status)) { pending=null; sessionStorage.removeItem('apex_team_pending'); }
    } finally { $('team-start').disabled=false; $('team-start').textContent=pending ? 'Recover submitted task' : 'Start task'; }
  };
  setInterval(refresh,3000); refresh();
})();
