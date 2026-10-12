/* Reverse workspace: open software you own, read how it works through REA.
   REA's result shapes aren't fixed, and a binary's own strings are untrusted,
   so everything from the API is read leniently and inserted with textContent —
   never as HTML. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  let current = null, fnActive = null, toastTimer = null;

  function say(msg, bad) {
    const t = $('toast'); t.textContent = msg; t.classList.toggle('bad', !!bad); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
  }
  async function api(url, opts) {
    const r = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
    let body = null; try { body = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error((body && body.detail) || `Request failed (${r.status})`);
    return body;
  }

  // --- leniently pull a list and the fields of an item out of whatever REA returned
  function asList(obj) {
    if (Array.isArray(obj)) return obj;
    if (obj && typeof obj === 'object') {
      for (const k of ['procedures', 'functions', 'strings', 'items', 'results', 'names', 'matches', 'entries']) {
        if (Array.isArray(obj[k])) return obj[k];
      }
    }
    return [];
  }
  const pick = (o, keys) => { if (o && typeof o === 'object') for (const k of keys) if (o[k] != null && o[k] !== '') return String(o[k]); return ''; };
  const addrOf = o => pick(o, ['address', 'addr', 'entry', 'offset', 'ea', 'location']);
  const nameOf = o => pick(o, ['name', 'label', 'symbol', 'function', 'procedure', 'display_name']);
  const textOf = o => (typeof o === 'string' ? o : pick(o, ['value', 'string', 'text', 'content']));

  function pre(value) {
    const p = el('pre');
    p.textContent = typeof value === 'string' ? value
      : (value && value.text != null && Object.keys(value).length === 1) ? String(value.text)
      : JSON.stringify(value, null, 2);
    return p;
  }

  // --- opening and the session list
  async function load() {
    let data; try { data = await api('/api/reverse'); } catch (err) { say(err.message, true); return; }
    const warn = $('rea-warn');
    if (!data.ready) {
      warn.hidden = false;
      warn.textContent = 'REA is not set up on this PC yet. Install Node.js 22.19 or newer, then run  npx rea-agents@6.4.0 setup  in a terminal and reload this page.';
      $('open-btn').disabled = true;
    } else { warn.hidden = true; $('open-btn').disabled = false; }
    const box = $('open-sessions'); box.replaceChildren();
    for (const s of data.sessions || []) {
      const row = el('div', 'sess');
      row.append(el('b', '', s.name || 'binary'), el('code', '', s.path || ''));
      const go = el('button', 'small', 'Open'); go.onclick = () => enter(s); row.append(go);
      box.append(row);
    }
  }

  $('open-form').addEventListener('submit', async e => {
    e.preventDefault();
    const btn = $('open-btn'), path = $('path').value.trim();
    if (!path) return;
    btn.disabled = true; btn.textContent = 'Opening…'; say('Importing into Ghidra — this can take a minute.');
    try { const s = await api('/api/reverse/open', { method: 'POST', body: JSON.stringify({ path }) }); enter(s); }
    catch (err) { say(err.message, true); }
    finally { btn.disabled = false; btn.textContent = 'Open'; }
  });

  function enter(s) {
    current = s; fnActive = null;
    $('intro').hidden = true; $('work').hidden = false;
    $('w-name').textContent = s.name || 'binary'; $('w-path').textContent = s.path || '';
    showTab('overview'); loadOverview();
    $('fn-list').replaceChildren(); $('str-list').replaceChildren();
    $('fn-detail').replaceChildren(el('p', 'fine', 'Pick a function to see its decompiled code.'));
    $('fn-q').value = ''; $('str-q').value = '';
  }

  $('back').onclick = () => { $('work').hidden = true; $('intro').hidden = false; load(); };
  $('w-close').onclick = async () => {
    if (!current) return;
    try { await api(`/api/reverse/sessions/${current.id}/close`, { method: 'POST', body: '{}' }); say('Closed.'); }
    catch (err) { say(err.message, true); }
    current = null; $('work').hidden = true; $('intro').hidden = false; load();
  };

  // --- tabs
  function showTab(name) {
    for (const t of document.querySelectorAll('.tab')) t.classList.toggle('active', t.dataset.tab === name);
    for (const p of document.querySelectorAll('.pane')) p.classList.toggle('active', p.dataset.pane === name);
  }
  for (const t of document.querySelectorAll('.tab')) t.onclick = () => {
    showTab(t.dataset.tab);
    if (t.dataset.tab === 'functions' && !$('fn-list').children.length) loadFunctions();
    if (t.dataset.tab === 'strings' && !$('str-list').children.length) loadStrings();
  };

  // --- overview
  async function loadOverview() {
    const body = $('ov-body'); body.replaceChildren(el('p', 'fine', 'Loading…'));
    let data; try { data = await api(`/api/reverse/sessions/${current.id}/overview`); }
    catch (err) { body.replaceChildren(el('p', 'fine', err.message)); return; }
    body.replaceChildren();
    const ov = data.overview || {};
    const facts = (ov && typeof ov === 'object' && !ov.text) ? ov : null;
    if (facts) {
      const card = el('div', 'card'); card.append(el('h3', '', 'This file'));
      let any = false;
      for (const [label, keys] of [['Format', ['format', 'file_type', 'type']], ['Architecture', ['architecture', 'arch', 'cpu']],
        ['Bits', ['bits', 'word_size']], ['Entry', ['entry', 'entry_point']], ['Functions', ['function_count', 'procedures', 'functions']],
        ['Compiler', ['compiler', 'toolchain']], ['Size', ['size', 'bytes']]]) {
        const v = pick(facts, keys); if (v) { any = true; const r = el('div', 'kv'); r.append(el('span', '', label), el('span', '', v)); card.append(r); }
      }
      if (any) body.append(card);
    }
    body.append(sectionCard('Overview', ov));
    if (data.segments) body.append(sectionCard('Segments', data.segments));
    if (data.architectures) body.append(sectionCard('Architectures', data.architectures));
  }
  function sectionCard(title, value) {
    const card = el('div', 'card'); card.append(el('h3', '', title));
    const list = asList(value);
    if (list.length) { const ul = el('div'); for (const it of list.slice(0, 60)) { const r = el('div', 'kv'); r.append(el('span', '', nameOf(it) || textOf(it) || '—'), el('span', '', addrOf(it) || pick(it, ['permissions', 'size', 'kind']) || '')); ul.append(r); } card.append(ul); }
    else card.append(pre(value));
    return card;
  }

  // --- functions
  async function loadFunctions() {
    const ul = $('fn-list'); ul.replaceChildren(el('li', '', 'Loading…')); ul.classList.remove('text');
    let data; try { data = await api(`/api/reverse/sessions/${current.id}/functions?q=${encodeURIComponent($('fn-q').value.trim())}`); }
    catch (err) { ul.replaceChildren(el('li', '', err.message)); return; }
    const items = asList(data.result); ul.replaceChildren();
    if (!items.length) { ul.append(el('li', '', 'No functions found.')); return; }
    for (const it of items) {
      const li = el('li'); const addr = addrOf(it), name = nameOf(it);
      if (addr) li.append(el('span', 'addr', addr + '  '));
      li.append(document.createTextNode(name || '(unnamed)'));
      li.onclick = () => openFunction(addr || name, li);
      ul.append(li);
    }
  }
  $('fn-go').onclick = loadFunctions;
  $('fn-q').addEventListener('keydown', e => { if (e.key === 'Enter') loadFunctions(); });

  async function openFunction(address, li) {
    if (!address) return;
    if (fnActive) fnActive.classList.remove('active'); if (li) { li.classList.add('active'); fnActive = li; }
    const d = $('fn-detail'); d.replaceChildren(el('p', 'fine', 'Decompiling…'));
    let data; try { data = await api(`/api/reverse/sessions/${current.id}/function?address=${encodeURIComponent(address)}`); }
    catch (err) { d.replaceChildren(el('p', 'fine', err.message)); return; }
    d.replaceChildren(el('h3', '', nameOf(data.info) || address));
    for (const [lbl, key] of [['Decompiled', 'pseudocode'], ['Assembly', 'assembly'], ['Calls', 'callees'], ['Called by', 'callers']]) {
      if (data[key] == null) continue;
      d.append(el('div', 'lbl', lbl)); d.append(pre(data[key]));
    }
  }

  // --- strings
  async function loadStrings() {
    const ul = $('str-list'); ul.classList.add('text'); ul.replaceChildren(el('li', '', 'Loading…'));
    let data; try { data = await api(`/api/reverse/sessions/${current.id}/strings?q=${encodeURIComponent($('str-q').value.trim())}`); }
    catch (err) { ul.replaceChildren(el('li', '', err.message)); return; }
    const items = asList(data.result); ul.replaceChildren();
    if (!items.length) { ul.append(el('li', '', 'No text found.')); return; }
    for (const it of items) {
      const li = el('li'); const addr = addrOf(it);
      if (addr) li.append(el('span', 'addr', addr + '  '));
      li.append(document.createTextNode(textOf(it) || JSON.stringify(it)));
      ul.append(li);
    }
  }
  $('str-go').onclick = loadStrings;
  $('str-q').addEventListener('keydown', e => { if (e.key === 'Enter') loadStrings(); });

  load();
  window.__rev = { asList, pick, addrOf, nameOf, textOf };   // for the page check
})();
