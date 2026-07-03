// === Apex Agent dashboard ===

// === Token auth ===
const _TOKEN_KEY = 'apex_token';
function getToken() { return localStorage.getItem(_TOKEN_KEY) || ''; }
function setToken(t) {
  if (t) localStorage.setItem(_TOKEN_KEY, t);
  else localStorage.removeItem(_TOKEN_KEY);
}

// Stable per-device identity (used for presence + push routing)
function getDeviceId() {
  let id = localStorage.getItem('apex_device_id');
  if (!id) {
    id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
       : 'dev-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem('apex_device_id', id);
  }
  return id;
}
function deviceKind() {
  return window.matchMedia('(display-mode: standalone)').matches ? 'pwa' : 'web';
}
function deviceLabel() {
  return deviceKind() + ': ' + (navigator.platform || 'device');
}

// Pairing: a scanned QR opens  <base>/?source=pair#token=XYZ  — adopt the token.
(function handlePairing() {
  try {
    const h = new URLSearchParams((location.hash || '').replace(/^#/, ''));
    const t = h.get('token');
    if (t) {
      setToken(t);
      history.replaceState(null, '', location.pathname + location.search);
    }
  } catch (_) { /* ignore */ }
})();

function showLogin(msg = '') {
  const overlay = document.getElementById('login-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  const err = document.getElementById('login-error');
  if (err) err.textContent = msg || '';
}
function hideLogin() {
  const overlay = document.getElementById('login-overlay');
  if (overlay) overlay.style.display = 'none';
}

document.getElementById('login-form')?.addEventListener('submit', async ev => {
  ev.preventDefault();
  const t = (document.getElementById('login-token')?.value || '').trim();
  if (!t) return;
  const err = document.getElementById('login-error');
  try {
    const r = await fetch('/api/status', { headers: { 'Authorization': `Bearer ${t}` } });
    if (r.ok) {
      setToken(t);
      hideLogin();
      boot();
    } else {
      if (err) err.textContent = 'Wrong token.';
    }
  } catch (e) {
    if (err) err.textContent = 'Connection error.';
  }
});

// === API helper ===
async function api(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const r = await fetch(path, {
    headers,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) {
    showLogin('Session expired — please re-enter your token.');
    throw new Error('Unauthorized');
  }
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${path}`);
  return r.json();
}

function escapeHTML(s) {
  return String(s ?? '').replace(/[<>&"']/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
}

function fmtTs(t) {
  if (!t) return '—';
  const d = new Date(t * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function fmtDate(t) {
  if (!t) return '—';
  return new Date(t * 1000).toLocaleDateString();
}
function fmtCost(c) { return '$' + (c || 0).toFixed(4); }
function fmtNum(n) { return new Intl.NumberFormat().format(n || 0); }

// === Tab switching ===
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const prevTab = document.querySelector('.tab.active')?.id?.replace('tab-', '');
    if (prevTab === 'camera') _teardownVision();
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    document.getElementById('tab-' + tab).classList.add('active');
    loadTab(tab);
  });
});

// === Status bar ===
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    document.getElementById('status-model').textContent = s.model.replace('claude-', '');
    document.getElementById('status-tools').textContent = s.tools_count;
    const h = Math.floor(s.uptime_s / 3600), m = Math.floor((s.uptime_s % 3600) / 60);
    document.getElementById('status-uptime').textContent = `${h}h ${m}m`;
  } catch (e) {}
}
// refreshStatus and connectWS are started by boot() after auth check.

// === WebSocket live feed ===
const wsIndicator = document.getElementById('ws-indicator');
const feed = document.getElementById('event-feed');
let ws;
let _wsHeartbeat = null;
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const token = getToken();
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  params.set('device', getDeviceId());
  params.set('kind', deviceKind());
  params.set('label', deviceLabel());
  ws = new WebSocket(`${proto}://${location.host}/ws/live?${params.toString()}`);
  ws.onopen = () => {
    wsIndicator.textContent = '● live'; wsIndicator.className = 'brand-sub ws-on';
    clearInterval(_wsHeartbeat);
    _wsHeartbeat = setInterval(() => { try { if (ws && ws.readyState === 1) ws.send('ping'); } catch (_) {} }, 25000);
  };
  ws.onclose = () => {
    wsIndicator.textContent = '● offline'; wsIndicator.className = 'brand-sub ws-off';
    clearInterval(_wsHeartbeat);
    setTimeout(connectWS, 3000);
  };
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'event') addFeedItem(msg);
    if (msg.type === 'snapshot' && msg.data?.events_recent) {
      msg.data.events_recent.forEach(e => addFeedItem(e));
    }
    if (msg.type === 'notify') showNotifyToast(msg);
    if (msg.type === 'chat_token') { _chatAppendToken(msg.delta, msg.chat_id); _setApexState('speaking'); }
    if (msg.type === 'chat_done')  { _chatFinalize(msg.chat_id, msg.response, msg.session_id, msg.turn_index); _setApexState('idle'); }
    if (msg.type === 'chat_error') { _chatError(msg.error, msg.chat_id); _setApexState('idle'); }
    if (msg.type === 'council_progress')    _councilProgress(msg.message);
    if (msg.type === 'council_round_start') _councilRoundStart(msg.round, msg.members);
    if (msg.type === 'council_answer')      _councilAnswer(msg);
    if (msg.type === 'council_done')        _councilDone(msg);
    if (msg.type === 'council_error')       _councilError(msg.error);
    if (msg.type === 'constellation_start')    _cstStart(msg.planets);
    if (msg.type === 'constellation_progress') _cstProgress(msg.message);
    if (msg.type === 'constellation_answer')   _cstAnswer(msg);
    if (msg.type === 'constellation_done')     _cstDone(msg);
    if (msg.type === 'constellation_error')    _cstError(msg.error);
    // Refresh self-improvement panel when a rollback check completes
    if (msg.type === 'rollback_done' && document.getElementById('tab-reflections')?.classList.contains('active')) {
      loadReflections();
    }
    if (msg.type === 'rollback_done' && document.getElementById('tab-evolution')?.classList.contains('active')) {
      loadEvolution();
    }
    // A document was written (by the editor or the agent) — refresh the list, but
    // don't clobber the doc the user is actively editing.
    if (msg.type === 'document_saved' && document.getElementById('tab-documents')?.classList.contains('active')) {
      _docRefreshList();
      if (_docCurrent != null && msg.id === _docCurrent && _docSaveState() === 'saved') {
        _docReloadOpen();
      }
    }
  };
  setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 25000);
}
function addFeedItem(msg) {
  const div = document.createElement('div');
  div.className = 'feed-item';
  div.innerHTML = `
    <span class="ts">${fmtTs(msg.ts)}</span>
    <span class="source">${escapeHTML(msg.source || 'event')}</span>
    <span class="content">${escapeHTML(msg.content || '')}</span>`;
  feed.prepend(div);
  while (feed.children.length > 250) feed.removeChild(feed.lastChild);
  pushTicker(msg);
  spawnArc(msg.source);
  registerLiveEvent();
}
// === Tab loaders ===
async function loadTab(tab) {
  const fns = {
    overview: loadCommand,
    goals: loadGoals,
    memory: loadMemory,
    graph: loadGraph,
    reflections: loadReflections,
    evolution: loadEvolution,
    inbox: loadInbox,
    calendar: loadCalendar,
    telemetry: loadTelemetry,
    replay: loadReplay,
    briefing: loadBriefing,
    schedule: loadTasks,
    subagents: loadSubagents,
    knowledge: loadKB,
    selfmod: loadSelfMod,
    phone: loadPhone,
    camera: loadVision,
    chat: loadChat,
    council: loadCouncil,
    compare: loadCompare,
    documents: loadDocuments,
    constellation: loadConstellation,
  };
  if (fns[tab]) try { await fns[tab](); } catch (e) { console.error('loadTab', tab, e); }
}

// ============== COMMAND CENTER ==============
const FEATURES = [
  { tab: 'live',        icon: '▣', label: 'Live Feed',  badge: 'events' },
  { tab: 'goals',       icon: '◇', label: 'Goals',      badge: 'goals' },
  { tab: 'memory',      icon: '◎', label: 'Memory',     badge: 'memories' },
  { tab: 'graph',       icon: '◈', label: 'Knowledge',  badge: 'entities' },
  { tab: 'reflections', icon: '⌘', label: 'Reflect',    badge: 'reflections' },
  { tab: 'telemetry',   icon: '▤', label: 'Telemetry' },
  { tab: 'replay',      icon: '▷', label: 'Replay' },
  { tab: 'schedule',    icon: '◷', label: 'Schedule',   badge: 'tasks' },
  { tab: 'subagents',   icon: '⌥', label: 'Sub-agents', badge: 'subagents' },
  { tab: 'knowledge',   icon: '≡', label: 'Knwl Base' },
  { tab: 'selfmod',     icon: '✎', label: 'Self-Mod' },
  { tab: 'phone',       icon: '☎', label: 'Phone' },
  { tab: 'camera',      icon: '◉', label: 'Vision' },
  { tab: 'constellation', icon: '✦', label: 'Constellation' },
];

// World hub coordinates [lat, lng] — agent activity lights these up
const HUBS = [
  [37.77, -122.42], [40.71, -74.0], [51.50, -0.12], [35.68, 139.69],
  [1.35, 103.82], [-33.87, 151.21], [52.52, 13.40], [19.08, 72.88],
  [-23.55, -46.63], [55.75, 37.62], [48.85, 2.35], [25.20, 55.27],
];
const SRC_COLOR = {
  stt: '#5fd8ff', tool: '#8a7cff', awareness: '#3ddc97', error: '#ff6a6a',
  proactive: '#ffb547', event: '#5fd8ff', agent: '#8a7cff',
};

let globeInstance = null;
let globeArcs = [];
let commandInited = false;

// Solar system body definitions
const SOLAR_BODIES = {
  sun:     { label:'Sun',     icon:'☀', texture:'/static/planets/sun.jpg',     atm:'#ff9900', atmAlt:0.55, speed:0.25 },
  mercury: { label:'Mercury', icon:'☿', texture:'/static/planets/mercury.jpg', atm:'#888888', atmAlt:0.04, speed:0.10 },
  venus:   { label:'Venus',   icon:'♀', texture:'/static/planets/venus.jpg',   atm:'#dd9900', atmAlt:0.38, speed:0.06 },
  earth:   { label:'Earth',   icon:'⊕',
             texture:'//unpkg.com/three-globe/example/img/earth-night.jpg',
             bumpTexture:'//unpkg.com/three-globe/example/img/earth-topology.png',
             atm:'#5fd8ff', atmAlt:0.21, speed:0.70 },
  moon:    { label:'Moon',    icon:'☽', texture:'/static/planets/moon.jpg',    atm:'#bbbbcc', atmAlt:0.02, speed:0.05 },
  mars:    { label:'Mars',    icon:'♂', texture:'/static/planets/mars.jpg',    atm:'#ff5522', atmAlt:0.10, speed:0.65 },
  jupiter: { label:'Jupiter', icon:'♃', texture:'/static/planets/jupiter.jpg', atm:'#ddaa55', atmAlt:0.16, speed:1.30 },
  saturn:  { label:'Saturn',  icon:'♄', texture:'/static/planets/saturn.jpg',  atm:'#ddcc60', atmAlt:0.14, speed:1.10 },
  uranus:  { label:'Uranus',  icon:'♅', texture:'/static/planets/uranus.jpg',  atm:'#88ffff', atmAlt:0.22, speed:0.90 },
  neptune: { label:'Neptune', icon:'♆', texture:'/static/planets/neptune.jpg', atm:'#5588ff', atmAlt:0.24, speed:0.95 },
  pluto:   { label:'Pluto',   icon:'✦', texture:'/static/planets/pluto.jpg',   atm:'#998888', atmAlt:0.03, speed:0.03 },
};
let currentBody = 'earth';
const _bodyTextureCache = {};

function makeBodyTexture(body) {
  // Emergency fallback if a photo texture is missing.
  const w = 512, h = 256;
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = body.atm || '#888';
  ctx.fillRect(0, 0, w, h);
  return canvas.toDataURL('image/jpeg', 0.9);
}

function switchBody(key) {
  const body = SOLAR_BODIES[key];
  if (!body || !globeInstance) return;
  currentBody = key;
  document.querySelectorAll('.planet-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.body === key));
  let tex = body.texture;
  if (!tex) {
    if (!_bodyTextureCache[key]) _bodyTextureCache[key] = makeBodyTexture(body);
    tex = _bodyTextureCache[key];
  }
  globeInstance
    .globeImageUrl(tex)
    .bumpImageUrl(body.bumpTexture || '')
    .atmosphereColor(body.atm)
    .atmosphereAltitude(body.atmAlt);
  globeInstance.controls().autoRotateSpeed = body.speed;
  const glow = document.querySelector('.globe-glow');
  if (glow) {
    const c = body.atm;
    glow.style.background =
      `radial-gradient(circle, ${c}2e 0%, ${c}0d 55%, transparent 72%)`;
  }
}

function renderPlanetSelector() {
  const sel = document.getElementById('planet-selector');
  if (!sel || sel.dataset.inited) return;
  sel.dataset.inited = '1';
  sel.innerHTML = Object.entries(SOLAR_BODIES).map(([key, body]) =>
    `<button class="planet-btn${key === currentBody ? ' active' : ''}" data-body="${key}" title="${body.label}">` +
    `<span class="planet-btn-icon">${body.icon}</span>` +
    `<span class="planet-btn-label">${body.label}</span></button>`
  ).join('');
  sel.addEventListener('click', e => {
    const btn = e.target.closest('.planet-btn');
    if (btn) switchBody(btn.dataset.body);
  });
}
let liveEventTimes = [];

function registerLiveEvent() { liveEventTimes.push(Date.now()); }
function refreshEventBadge() {
  const cutoff = Date.now() - 300000;
  liveEventTimes = liveEventTimes.filter(t => t > cutoff);
  setBadge('events', liveEventTimes.length);
}
setInterval(refreshEventBadge, 15000);

function setBadge(key, val) {
  const el = document.getElementById('badge-' + key);
  if (!el) return;
  if (val && val > 0) { el.textContent = val > 99 ? '99+' : val; el.style.display = 'flex'; }
  else el.style.display = 'none';
}

function buildFeatureOrbit() {
  const orbit = document.getElementById('feature-orbit');
  if (!orbit || orbit.childElementCount) return;
  const R = 318;
  FEATURES.forEach((f, i) => {
    const ang = (-90 + i * (360 / FEATURES.length)) * Math.PI / 180;
    const node = document.createElement('div');
    node.className = 'feat-node';
    node.style.left = (R * Math.cos(ang)) + 'px';
    node.style.top = (R * Math.sin(ang)) + 'px';
    node.style.animationDelay = (i * 0.35) + 's';
    node.title = f.label;
    node.innerHTML = `
      <span class="feat-node-icon">${f.icon}</span>
      <span class="feat-node-label">${f.label}</span>
      ${f.badge ? `<span class="feat-node-badge" id="badge-${f.badge}" style="display:none">0</span>` : ''}`;
    node.addEventListener('click', () => {
      document.querySelector(`.nav-btn[data-tab="${f.tab}"]`)?.click();
    });
    orbit.appendChild(node);
  });
}

function initStarfield() {
  const cv = document.getElementById('cmd-starfield');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  let stars = [];
  function resize() {
    cv.width = cv.offsetWidth; cv.height = cv.offsetHeight;
    const n = Math.floor(cv.width * cv.height / 7000);
    stars = Array.from({ length: n }, () => ({
      x: Math.random() * cv.width, y: Math.random() * cv.height,
      r: Math.random() * 1.4 + 0.2,
      tw: Math.random() * Math.PI * 2,
      sp: Math.random() * 0.018 + 0.003,
    }));
  }
  resize();
  window.addEventListener('resize', resize);
  (function draw() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (const s of stars) {
      s.tw += s.sp;
      const a = 0.3 + Math.sin(s.tw) * 0.35;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(170,210,255,${Math.max(0, a)})`;
      ctx.fill();
    }
    requestAnimationFrame(draw);
  })();
}

function initGlobe() {
  const mount = document.getElementById('globe');
  if (!mount || globeInstance) return;
  if (typeof Globe === 'undefined') {
    mount.innerHTML = '<div class="globe-fallback"></div>';
    return;
  }
  try {
    globeInstance = Globe()(mount)
      .width(460).height(460)
      .backgroundColor('rgba(0,0,0,0)')
      .globeImageUrl('//unpkg.com/three-globe/example/img/earth-night.jpg')
      .bumpImageUrl('//unpkg.com/three-globe/example/img/earth-topology.png')
      .showAtmosphere(true)
      .atmosphereColor('#5fd8ff')
      .atmosphereAltitude(0.21)
      .arcsData(globeArcs)
      .arcColor('color')
      .arcAltitude('alt')
      .arcStroke(0.55)
      .arcDashLength(0.45)
      .arcDashGap(0.25)
      .arcDashAnimateTime(1600)
      .ringsData(HUBS.slice(0, 4).map(([lat, lng]) => ({ lat, lng })))
      .ringColor(() => (t => `rgba(95,216,255,${1 - t})`))
      .ringMaxRadius(4)
      .ringPropagationSpeed(1.6)
      .ringRepeatPeriod(1400);
    const ctrl = globeInstance.controls();
    ctrl.autoRotate = true;
    ctrl.autoRotateSpeed = 0.7;
    ctrl.enableZoom = false;
  } catch (e) {
    console.error('globe init failed', e);
    mount.innerHTML = '<div class="globe-fallback"></div>';
  }
}

function spawnArc(source) {
  if (!globeInstance) return;
  const a = HUBS[Math.floor(Math.random() * HUBS.length)];
  let b = HUBS[Math.floor(Math.random() * HUBS.length)];
  if (a === b) b = HUBS[(HUBS.indexOf(b) + 1) % HUBS.length];
  const color = SRC_COLOR[source] || '#5fd8ff';
  const arc = {
    startLat: a[0], startLng: a[1], endLat: b[0], endLng: b[1],
    color: [color, color], alt: 0.18 + Math.random() * 0.22,
  };
  globeArcs = [...globeArcs, arc].slice(-14);
  globeInstance.arcsData(globeArcs);
  setTimeout(() => {
    globeArcs = globeArcs.filter(x => x !== arc);
    if (globeInstance) globeInstance.arcsData(globeArcs);
  }, 4200);
}

// Ambient arcs keep the globe alive even when the agent is idle
setInterval(() => {
  if (globeInstance && document.getElementById('tab-overview')?.classList.contains('active')) {
    spawnArc('event');
  }
}, 3600);

function pushTicker(msg) {
  const track = document.getElementById('cmd-ticker');
  if (!track) return;
  track.querySelector('.cmd-ticker-idle')?.remove();
  const item = document.createElement('div');
  item.className = 'cmd-ticker-item';
  const txt = (msg.content || '').slice(0, 70);
  item.innerHTML = `<span class="tk-src">${escapeHTML(msg.source || 'event')}</span>${escapeHTML(txt)}`;
  track.prepend(item);
  while (track.children.length > 7) track.removeChild(track.lastChild);
}

function startCmdClock() {
  const el = document.getElementById('cmd-clock');
  if (!el) return;
  const tick = () => { el.textContent = new Date().toLocaleTimeString([], { hour12: false }); };
  tick();
  setInterval(tick, 1000);
}

async function loadCommand() {
  if (!commandInited) {
    commandInited = true;
    buildFeatureOrbit();
    initStarfield();
    initGlobe();
    renderPlanetSelector();
    startCmdClock();
    document.getElementById('hud-evo-frame')?.addEventListener('click', () => {
      document.querySelector('.nav-btn[data-tab="evolution"]')?.click();
    });
    const _qaStrip = document.getElementById('cst-quickask');
    if (_qaStrip) {
      _qaStrip.addEventListener('submit', e => {
        e.preventDefault();
        const q = _qaStrip.querySelector('input').value.trim();
        if (!q) return;
        const qinput = document.getElementById('cst-question');
        if (qinput) qinput.value = q;
        _qaStrip.querySelector('input').value = '';
        document.querySelector('.nav-btn[data-tab="constellation"]')?.click();
      });
    }
  }
  try {
    const [tel, goals, mems, tasks, refl, ents, subs, status] = await Promise.all([
      api('/api/telemetry?days=7'),
      api('/api/goals?active_only=true'),
      api('/api/memories?limit=500'),
      api('/api/tasks'),
      api('/api/reflections?status=pending'),
      api('/api/entities?limit=500'),
      api('/api/subagents'),
      api('/api/status').catch(() => ({})),
    ]);
    document.getElementById('hud-cost').textContent = fmtCost(tel.total_cost_usd);
    document.getElementById('hud-cost-meta').textContent =
      fmtNum((tel.total_input_tokens || 0) + (tel.total_output_tokens || 0)) + ' tokens';
    const cache = (tel.cache_hit_rate || 0) * 100;
    document.getElementById('hud-cache').textContent = cache.toFixed(1) + '%';
    document.getElementById('hud-cache-bar').style.width = cache + '%';
    document.getElementById('hud-calls').textContent = fmtNum(tel.total_calls);
    document.getElementById('hud-calls-meta').textContent =
      (tel.by_model || []).length + ' models active';
    document.getElementById('hud-model').textContent =
      (status.model || '—').replace('claude-', '');
    document.getElementById('hud-systems').textContent =
      `${status.tools_count || 0} tools · ${status.awareness_enabled ? 'aware' : 'idle'}`;

    const entCount = (ents.nodes || ents || []).length || 0;
    const runningSubs = Object.values(subs || {}).filter(s => s.status === 'running').length;
    setBadge('goals', goals.length);
    setBadge('memories', mems.length);
    setBadge('entities', entCount);
    setBadge('reflections', refl.length);
    setBadge('tasks', tasks.length);
    setBadge('subagents', runningSubs);

    // Evolution glance — self-improvement at a glance (non-blocking)
    api('/api/evolution?days=30').then(evo => {
      const s = evo.summary || {};
      const improvements = (s.created || 0) + (s.refined || 0);
      const big = document.getElementById('hud-evo');
      if (big) big.textContent = improvements;
      const meta = document.getElementById('hud-evo-meta');
      if (meta) meta.textContent =
        `✦${s.created || 0} forged · ↻${s.refined || 0} refined · ⤺${s.rolled_back || 0} reverted` +
        ((s.failing_now || 0) > 0 ? ` · ${s.failing_now} failing` : '');
    }).catch(() => {});
  } catch (e) { console.error('command', e); }
}

function chartOpts(scaleOverrides = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: '#8892a6', font: { size: 11 } } } },
    scales: {
      x: { ticks: { color: '#5e6878', font: { size: 11 } }, grid: { color: '#1c2230' } },
      y: { ticks: { color: '#5e6878', font: { size: 11 }, ...(scaleOverrides.y?.ticks || {}) }, grid: { color: '#1c2230' }, beginAtZero: true },
    }
  };
}

// ============== GOALS ==============
async function loadGoals() {
  const goals = await api('/api/goals?active_only=false');
  const wrap = document.getElementById('goals-list');
  wrap.innerHTML = goals.map(g => `
    <div class="card">
      <div class="card-head">
        <div>
          <div class="card-title"><span class="badge horizon">${g.horizon}</span> ${escapeHTML(g.title)} <span class="status-${g.status}">[${g.status}]</span></div>
          ${g.description ? `<div class="card-meta">${escapeHTML(g.description)}</div>` : ''}
          ${g.deadline ? `<div class="card-meta">Deadline: ${new Date(g.deadline*1000).toDateString()}</div>` : ''}
          <div class="card-meta">${g.recent_progress.length} progress notes</div>
        </div>
      </div>
      <div class="actions">
        <button onclick="updateGoal(${g.id}, 'done')">Mark done</button>
        <button onclick="updateGoal(${g.id}, 'paused')">Pause</button>
        <button onclick="addProgress(${g.id})">Add note</button>
      </div>
    </div>`).join('') || '<div class="empty-state">No goals yet.</div>';
}
async function updateGoal(id, status) { await api(`/api/goals/${id}`, { method: 'PATCH', body: { status } }); loadGoals(); }
async function addProgress(id) {
  const note = prompt('Progress note:'); if (!note) return;
  await api(`/api/goals/${id}`, { method: 'PATCH', body: { progress_note: note } });
  loadGoals();
}
document.getElementById('goal-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target;
  await api('/api/goals', { method: 'POST', body: {
    title: f.title.value, description: f.description.value,
    horizon: f.horizon.value, deadline_iso: f.deadline_iso.value || null,
  }});
  f.reset(); loadGoals();
});

// ============== MEMORY ==============
async function loadMemory() {
  const q = document.getElementById('memory-search').value;
  const mems = await api('/api/memories?q=' + encodeURIComponent(q));
  const tbody = document.querySelector('#memory-table tbody');
  tbody.innerHTML = mems.map(m => `
    <tr>
      <td>${m.id}</td>
      <td><span class="badge horizon">${m.kind}</span></td>
      <td>${'★'.repeat(Math.min(m.importance, 10))}</td>
      <td>${escapeHTML(m.content)}</td>
      <td><button onclick="forgetMem(${m.id})" style="background:var(--danger);color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px">Delete</button></td>
    </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:20px">No memories.</td></tr>';
}
async function forgetMem(id) { await api('/api/memories/' + id, { method: 'DELETE' }); loadMemory(); }
document.getElementById('memory-refresh').addEventListener('click', loadMemory);
document.getElementById('memory-search').addEventListener('keypress', e => { if (e.key === 'Enter') loadMemory(); });

// ============== KNOWLEDGE GRAPH ==============
let visNetwork = null;
const KIND_COLORS = {
  person: '#6cf', project: '#3ddc97', place: '#ffb547', concept: '#8892a6',
  tool: '#8a7cff', file: '#5e6878', event: '#ff6a6a', org: '#d29922',
};

async function loadGraph() {
  await renderGraph();
}

async function renderGraph(filter = {}) {
  const kindFilter = document.getElementById('graph-kind-filter')?.value || '';
  const search = (document.getElementById('graph-search')?.value || '').trim();

  let data;
  if (search) {
    try { data = await api('/api/entities/query?name=' + encodeURIComponent(search) + '&hops=2'); }
    catch { data = { nodes: [], edges: [] }; }
    if (data.error) { document.getElementById('graph-side').innerHTML = `<div class="graph-side-empty">${escapeHTML(data.error)}</div>`; }
  } else if (kindFilter) {
    const ents = await api('/api/entities?kind=' + kindFilter);
    data = { nodes: ents, edges: [] };
  } else {
    data = await api('/api/entities?limit=100');
  }

  const nodes = (data.nodes || []).map(n => ({
    id: n.id,
    label: n.name,
    title: `${n.kind}: ${n.name}`,
    color: { background: KIND_COLORS[n.kind] || '#8892a6', border: '#11161f', highlight: { background: KIND_COLORS[n.kind] || '#8892a6', border: '#fff' } },
    font: { color: '#fff', size: 13 },
    shape: 'dot', size: 10 + (n.importance || 5) * 1.5,
    _entity: n,
  }));
  const edges = (data.edges || []).map(e => ({
    id: e.id, from: e.from_id, to: e.to_id, label: e.kind,
    color: { color: '#2e3648', highlight: '#6cf' },
    font: { color: '#5e6878', size: 10, strokeWidth: 0 },
    arrows: { to: { enabled: true, scaleFactor: 0.6 } }, smooth: { type: 'continuous' },
  }));

  const container = document.getElementById('graph-canvas');
  if (visNetwork) visNetwork.destroy();
  visNetwork = new vis.Network(container, { nodes, edges }, {
    physics: { stabilization: true, barnesHut: { gravitationalConstant: -8000, springLength: 120 } },
    interaction: { hover: true, tooltipDelay: 200 },
    nodes: { borderWidth: 2 },
  });
  visNetwork.on('selectNode', params => {
    const node = nodes.find(n => n.id === params.nodes[0]);
    if (node) showEntitySide(node._entity);
  });
}

function showEntitySide(entity) {
  document.getElementById('graph-side').innerHTML = `
    <h4>${escapeHTML(entity.name)}</h4>
    <div class="meta"><span class="badge horizon">${entity.kind}</span> · importance ${entity.importance}</div>
    <div class="meta">Last seen: ${fmtDate(entity.last_seen)}</div>
    <div class="meta">Created: ${fmtDate(entity.created_at)}</div>
    ${Object.keys(entity.properties || {}).length ? `<pre>${escapeHTML(JSON.stringify(entity.properties, null, 2))}</pre>` : ''}
    <div style="margin-top:10px"><button onclick="deleteEntity(${entity.id})" style="background:var(--danger);color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px">Delete entity</button></div>
  `;
}
async function deleteEntity(id) {
  if (!confirm('Delete this entity and its relations?')) return;
  await api('/api/entities/' + id, { method: 'DELETE' });
  renderGraph();
  document.getElementById('graph-side').innerHTML = '<div class="graph-side-empty">Click a node to inspect.</div>';
}
document.getElementById('graph-refresh')?.addEventListener('click', () => {
  document.getElementById('graph-search').value = '';
  document.getElementById('graph-kind-filter').value = '';
  renderGraph();
});
document.getElementById('graph-search-btn')?.addEventListener('click', () => renderGraph());
document.getElementById('graph-search')?.addEventListener('keypress', e => { if (e.key === 'Enter') renderGraph(); });
document.getElementById('graph-kind-filter')?.addEventListener('change', () => renderGraph());

// ============== REFLECTIONS ==============
let reflectionsStatus = 'pending';

async function loadReflections() {
  // Fetch all data in parallel
  const [refl, fbSummary, outcomeRefls, rewrites] = await Promise.allSettled([
    api('/api/reflections?status=' + reflectionsStatus),
    api('/api/feedback/summary?days=7'),
    reflectionsStatus === 'applied' ? api('/api/outcomes/reflections?days=30') : Promise.resolve([]),
    api('/api/outcomes/rewrites?days=30'),
  ]).then(r => r.map(p => p.status === 'fulfilled' ? p.value : null));

  // --- Stat cards ---
  if (fbSummary) {
    const rate = fbSummary.approval_rate;
    const el = document.getElementById('refl-stat-approval');
    if (el) {
      el.textContent = rate != null ? (rate * 100).toFixed(0) + '%' : '—';
      el.className = 'stat-value ' + (rate == null ? '' : rate >= 0.7 ? 'val-good' : rate >= 0.5 ? 'val-warn' : 'val-bad');
    }
    const bar = document.getElementById('refl-stat-approval-bar');
    if (bar) bar.style.width = rate != null ? (rate * 100) + '%' : '0%';
    const meta = document.getElementById('refl-stat-approval-meta');
    if (meta) meta.textContent = `👍 ${fbSummary.thumbs_up}  👎 ${fbSummary.thumbs_down}`;
    const turns = document.getElementById('refl-stat-turns');
    if (turns) turns.textContent = fmtNum(fbSummary.total);
    const turnsMeta = document.getElementById('refl-stat-turns-meta');
    if (turnsMeta) {
      const src = (fbSummary.by_source || []).map(s => `${s.source}: ${s.thumbs_up + s.thumbs_down}`).join(' · ');
      if (turnsMeta) turnsMeta.textContent = src;
    }
  }

  // Pending count badge
  if (reflectionsStatus !== 'pending' || !refl) {
    try {
      const pending = await api('/api/reflections?status=pending&limit=1');
      // We can't get count directly, so show list length of fresh fetch
      const pendingEl = document.getElementById('refl-stat-pending');
      if (pendingEl) pendingEl.textContent = Array.isArray(pending) ? pending.length : '—';
    } catch (_) {}
  } else {
    const pendingEl = document.getElementById('refl-stat-pending');
    if (pendingEl) pendingEl.textContent = Array.isArray(refl) ? refl.length : '—';
  }

  // Rewrites stat
  if (rewrites) {
    const rw = document.getElementById('refl-stat-rewrites');
    if (rw) rw.textContent = rewrites.length;
    const rwMeta = document.getElementById('refl-stat-rewrites-meta');
    if (rwMeta) {
      const rb = rewrites.filter(r => r.status === 'rolled_back').length;
      const conf = rewrites.filter(r => r.status === 'confirmed').length;
      rwMeta.textContent = rb ? `${rb} rolled back` : conf === rewrites.length && rewrites.length ? 'all confirmed' : 'active';
    }
    _renderRewrites(rewrites);
  }

  // --- Reflections list ---
  const wrap = document.getElementById('reflections-list');
  if (!wrap || !refl) return;

  // Build outcome delta map (reflection_id → delta info) for applied view
  const deltaMap = {};
  if (Array.isArray(outcomeRefls)) {
    outcomeRefls.forEach(o => { deltaMap[o.reflection_id] = o; });
  }

  wrap.innerHTML = refl.map(r => {
    const outcome = deltaMap[r.id];
    const deltaHtml = outcome ? _deltaBadge(outcome.delta, outcome.pre_turns, outcome.post_turns) : '';
    return `
    <div class="card">
      <div class="card-head">
        <div style="flex:1">
          <div class="card-title">
            <span class="badge kind-${r.kind}">${r.kind}</span>
            ${escapeHTML(r.content)}
            ${deltaHtml}
          </div>
          <div class="card-meta">Confidence: ${(r.confidence * 100).toFixed(0)}% · ${fmtDate(r.ts)} · ${r.status}</div>
          <div class="confidence-bar"><div class="confidence-bar-fill" style="width:${r.confidence * 100}%"></div></div>
          ${r.action && Object.keys(r.action).length
            ? `<details style="margin-top:8px">
                 <summary style="cursor:pointer;font-size:12px;color:var(--text-mute)">Action</summary>
                 <pre style="margin-top:6px;background:var(--bg-3);padding:8px;border-radius:4px;font-size:11.5px;color:var(--text-mute)">${escapeHTML(JSON.stringify(r.action, null, 2))}</pre>
               </details>` : ''}
        </div>
      </div>
      ${r.status === 'pending' ? `
        <div class="actions">
          <button class="accept" onclick="applyRefl(${r.id}, true)">Accept</button>
          <button class="reject" onclick="applyRefl(${r.id}, false)">Reject</button>
        </div>` : ''}
    </div>`;
  }).join('') || `<div class="empty-state">No ${reflectionsStatus} reflections.</div>`;
}

function _deltaBadge(delta, preTurns, postTurns) {
  if (delta == null) return `<span class="delta-badge delta-neutral" title="${preTurns} pre-turns, ${postTurns} post-turns">Δ n/a</span>`;
  const sign = delta > 0 ? '+' : '';
  const cls = delta > 0.05 ? 'delta-good' : delta < -0.05 ? 'delta-bad' : 'delta-neutral';
  return `<span class="delta-badge ${cls}" title="${preTurns} pre-turns → ${postTurns} post-turns">${sign}${(delta * 100).toFixed(0)}pp</span>`;
}

function _renderRewrites(rewrites) {
  const wrap = document.getElementById('rewrites-list');
  const summary = document.getElementById('rewrites-summary');
  if (!wrap) return;
  if (summary) {
    const rb = rewrites.filter(r => r.status === 'rolled_back').length;
    summary.textContent = rb ? `${rb} auto-rolled back` : '';
  }
  if (!rewrites.length) {
    wrap.innerHTML = '<div class="empty-state" style="padding:12px 0">No skill rewrites in the last 30 days.</div>';
    return;
  }
  wrap.innerHTML = `
    <table class="rewrite-table">
      <thead><tr>
        <th>Skill</th><th>Trigger</th><th>Pre rate</th><th>Post rate</th><th>Delta</th><th>Status</th><th>Date</th>
      </tr></thead>
      <tbody>
        ${rewrites.map(r => {
          const pre = r.pre_approval_rate != null ? (r.pre_approval_rate * 100).toFixed(0) + '%' : '—';
          const post = r.post_approval_rate != null ? (r.post_approval_rate * 100).toFixed(0) + '%' : '—';
          const d = r.delta;
          const deltaStr = d != null ? (d > 0 ? '+' : '') + (d * 100).toFixed(0) + 'pp' : '—';
          const deltaCls = d == null ? '' : d > 0.05 ? 'delta-good' : d < -0.05 ? 'delta-bad' : 'delta-neutral';
          const statusCls = 'rewrite-status-' + (r.status || 'active');
          const reason = r.rollback_reason ? ` title="${escapeHTML(r.rollback_reason)}"` : '';
          return `<tr>
            <td><code>${escapeHTML(r.name)}</code></td>
            <td><span class="badge">${escapeHTML(r.trigger || 'manual')}</span></td>
            <td>${pre}</td>
            <td>${post}</td>
            <td><span class="delta-badge ${deltaCls}">${deltaStr}</span></td>
            <td><span class="rewrite-status ${statusCls}"${reason}>${r.status}</span></td>
            <td>${fmtDate(r.ts)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

async function applyRefl(id, accept) {
  await api(`/api/reflections/${id}/apply`, { method: 'POST', body: { accept } });
  loadReflections();
}
['pending','applied','rejected'].forEach(s => {
  document.getElementById('refl-tab-' + s).addEventListener('click', e => {
    document.querySelectorAll('#tab-reflections .filter-btn').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    reflectionsStatus = s;
    loadReflections();
  });
});
document.getElementById('refl-run').addEventListener('click', async () => {
  const btn = document.getElementById('refl-run');
  btn.textContent = 'Running…'; btn.disabled = true;
  try {
    const r = await api('/api/reflections/run', { method: 'POST', body: { hours: 24 } });
    const rb = r.rollback || {};
    const rbMsg = rb.rolled_back ? ` · ${rb.rolled_back} skill(s) rolled back` : '';
    alert(`Created ${r.created} reflections, auto-applied ${r.applied}, pending ${r.pending || 0}.${rbMsg}`);
  } catch (e) { alert('Failed: ' + e.message); }
  btn.textContent = 'Run consolidation now'; btn.disabled = false;
  loadReflections();
});
document.getElementById('refl-check-rollback')?.addEventListener('click', async () => {
  const btn = document.getElementById('refl-check-rollback');
  btn.textContent = 'Checking…'; btn.disabled = true;
  try {
    const r = await api('/api/outcomes/check-rollback', { method: 'POST', body: {} });
    const msg = `Checked ${r.checked} rewrites — ${r.rolled_back} rolled back, ${r.confirmed} confirmed, ${r.skipped_not_enough_data} waiting for more data.`;
    alert(msg);
  } catch (e) { alert('Check failed: ' + e.message); }
  btn.textContent = 'Check rollbacks now'; btn.disabled = false;
  loadReflections();
});

// ============== EVOLUTION ==============
const _EVO_META = {
  created:     { icon: '✦', cls: 'evo-created',  verb: 'Forged' },
  refined:     { icon: '↻', cls: 'evo-refined',  verb: 'Refined' },
  rolled_back: { icon: '⤺', cls: 'evo-rolled',   verb: 'Rolled back' },
};

async function loadEvolution() {
  let data;
  try {
    data = await api('/api/evolution?days=30');
  } catch (e) {
    document.getElementById('evo-timeline').innerHTML =
      `<div class="evo-empty">Failed to load: ${escapeHTML(e.message)}</div>`;
    return;
  }
  const s = data.summary || {};
  const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
  setText('evo-installed', s.installed);
  setText('evo-created', s.created);
  setText('evo-refined', s.refined);
  setText('evo-rolled', s.rolled_back);
  setText('evo-failing', s.failing_now);
  const rolledEl = document.getElementById('evo-rolled');
  if (rolledEl) rolledEl.className = 'stat-value ' + ((s.rolled_back || 0) > 0 ? 'val-warn' : '');
  const failEl = document.getElementById('evo-failing');
  if (failEl) failEl.className = 'stat-value ' + ((s.failing_now || 0) > 0 ? 'val-bad' : 'val-good');
  const win = document.getElementById('evo-window');
  if (win) win.textContent = `last ${s.window_days || 30} days`;

  // Timeline
  const tl = document.getElementById('evo-timeline');
  const events = data.events || [];
  if (!events.length) {
    tl.innerHTML = `<div class="evo-empty">No changes yet. Apex refines failing skills nightly at 3am — the ledger fills as it learns.</div>`;
  } else {
    tl.innerHTML = events.map(ev => {
      const m = _EVO_META[ev.kind] || { icon: '•', cls: '', verb: ev.kind };
      let delta = '';
      if (ev.delta != null) {
        const cls = ev.delta > 0 ? 'delta-good' : ev.delta < 0 ? 'delta-bad' : 'delta-neutral';
        const sign = ev.delta > 0 ? '+' : '';
        delta = `<span class="delta-badge ${cls}">${sign}${(ev.delta * 100).toFixed(0)}%</span>`;
      }
      const net = ev.needs_network ? '<span class="badge badge-net">network</span>' : '';
      return `<div class="evo-event ${m.cls}">
        <div class="evo-dot">${m.icon}</div>
        <div class="evo-body">
          <div class="evo-line"><span class="evo-verb">${m.verb}</span>
            <code>${escapeHTML(ev.name || '?')}</code>${delta}${net}</div>
          <div class="evo-detail">${escapeHTML(ev.detail || '')}</div>
        </div>
        <div class="evo-ts">${fmtDate(ev.ts)}</div>
      </div>`;
    }).join('');
  }

  // Failing / workbench
  const fl = document.getElementById('evo-failing-list');
  const failing = data.failing || [];
  if (!failing.length) {
    fl.innerHTML = `<div class="evo-empty">Nothing failing — all skills healthy. ✓</div>`;
  } else {
    fl.innerHTML = failing.map(f => {
      const errs = (f.errors || []).slice(0, 2).map(e => escapeHTML(e)).join(' · ');
      return `<div class="evo-fail">
        <div class="evo-fail-head"><code>${escapeHTML(f.name)}</code>
          <span class="badge badge-bad">${f.failures}/${f.total} failed</span></div>
        ${errs ? `<div class="evo-fail-err">${errs}</div>` : ''}
      </div>`;
    }).join('');
  }
}

// ============== INBOX ==============
async function loadInbox() {
  const unreadOnly = document.getElementById('inbox-unread-only')?.checked || false;
  const status = document.getElementById('inbox-status');
  const list = document.getElementById('inbox-list');
  if (status) status.textContent = 'Loading…';
  let data;
  try {
    data = await api('/api/email/inbox?limit=25&unread_only=' + unreadOnly);
  } catch (e) {
    list.innerHTML = `<div class="evo-empty">Failed: ${escapeHTML(e.message)}</div>`;
    if (status) status.textContent = '';
    return;
  }
  if (!data.configured) {
    list.innerHTML = `<div class="evo-empty">Email not configured. Add <code>EMAIL_ADDRESS</code> and
      <code>EMAIL_PASSWORD</code> (an app-specific password) to your .env and restart Apex.</div>`;
    if (status) status.textContent = 'not configured';
    document.getElementById('inbox-count').textContent = '';
    loadInboxDrafts();
    return;
  }
  const msgs = data.messages || [];
  if (msgs[0]?.error) {
    list.innerHTML = `<div class="evo-empty">${escapeHTML(msgs[0].error)}</div>`;
  } else if (!msgs.length) {
    list.innerHTML = `<div class="evo-empty">Inbox clear. ✓</div>`;
  } else {
    list.innerHTML = msgs.map(m => `
      <div class="inbox-msg ${m.unread ? 'inbox-unread' : ''}" data-uid="${escapeHTML(m.uid)}">
        <div class="inbox-msg-top">
          <span class="inbox-from">${m.unread ? '<span class="inbox-dot"></span>' : ''}${escapeHTML(m.from || '')}</span>
          <span class="inbox-date">${escapeHTML((m.date || '').slice(0, 22))}</span>
        </div>
        <div class="inbox-subject">${escapeHTML(m.subject || '(no subject)')}</div>
      </div>`).join('');
    list.querySelectorAll('.inbox-msg').forEach(el =>
      el.addEventListener('click', () => openMessage(el.dataset.uid)));
  }
  document.getElementById('inbox-count').textContent = `${msgs.length} message(s)`;
  if (status) status.textContent = '';
  loadInboxDrafts();
}

async function loadInboxDrafts() {
  const wrap = document.getElementById('inbox-drafts');
  if (!wrap) return;
  try {
    const data = await api('/api/staged-writes');
    const drafts = (data.writes || []).filter(w => w.kind === 'email');
    if (!drafts.length) { wrap.innerHTML = `<div class="evo-empty">No drafts staged.</div>`; return; }
    wrap.innerHTML = drafts.map(d => `
      <div class="inbox-draft">
        <div class="inbox-draft-head"><code>${escapeHTML(d.payload.to || '')}</code></div>
        <div class="inbox-draft-subj">${escapeHTML(d.payload.subject || '')}</div>
        <div class="inbox-draft-body">${escapeHTML((d.payload.body || '').slice(0, 280))}</div>
        <div class="inbox-draft-actions">
          <button class="accept" onclick="sendDraft(${d.id})">Approve &amp; send</button>
          <button class="reject" onclick="discardDraft(${d.id})">Discard</button>
        </div>
      </div>`).join('');
  } catch (e) {
    wrap.innerHTML = `<div class="evo-empty">Failed to load drafts: ${escapeHTML(e.message)}</div>`;
  }
}

async function openMessage(uid) {
  const reader = document.getElementById('inbox-reader');
  reader.style.display = 'block';
  reader.innerHTML = `<div class="evo-empty">Loading message…</div>`;
  reader.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  try {
    const m = await api('/api/email/message/' + encodeURIComponent(uid));
    if (m.error) { reader.innerHTML = `<div class="evo-empty">${escapeHTML(m.error)}</div>`; return; }
    reader.innerHTML = `
      <div class="inbox-reader-head">
        <button class="inbox-reader-close" onclick="document.getElementById('inbox-reader').style.display='none'">✕</button>
        <div class="inbox-reader-subj">${escapeHTML(m.subject || '')}</div>
        <div class="inbox-reader-meta">From ${escapeHTML(m.from || '')} · ${escapeHTML(m.date || '')}</div>
      </div>
      <pre class="inbox-reader-body">${escapeHTML(m.body || '(empty)')}</pre>
      <div class="inbox-reader-actions">
        <button class="btn-primary inbox-reply-btn">Ask Apex to draft a reply</button>
      </div>`;
    // Bind via closure — never interpolate attacker-controlled email headers
    // (from_email/message_id) into markup, which was a stored-XSS → token-theft vector.
    const _replyBtn = reader.querySelector('.inbox-reply-btn');
    if (_replyBtn) _replyBtn.addEventListener('click',
      () => askApexToReply(m.from_email || '', m.subject || '', m.message_id || ''));
  } catch (e) {
    reader.innerHTML = `<div class="evo-empty">Failed: ${escapeHTML(e.message)}</div>`;
  }
}

function askApexToReply(toEmail, subject, messageId) {
  // Hand off to the chat tab with a pre-filled instruction; Apex will stage a draft.
  const prompt = `Draft a reply to the email from ${toEmail} (subject: "${subject}"). ` +
    `Use email_draft with in_reply_to "${messageId}" so it threads. Keep it concise.`;
  document.querySelector('.nav-btn[data-tab="chat"]')?.click();
  const input = document.getElementById('chat-input');
  if (input) { input.value = prompt; input.focus(); }
}

async function sendDraft(id) {
  if (!confirm('Approve and send this email?')) return;
  try {
    const r = await api(`/api/staged-writes/${id}/approve`, { method: 'POST', body: {} });
    alert(r.result || 'Sent.');
  } catch (e) { alert('Send failed: ' + e.message); }
  loadInboxDrafts();
}

async function discardDraft(id) {
  try {
    await api(`/api/staged-writes/${id}/reject`, { method: 'POST', body: {} });
  } catch (e) { alert('Failed: ' + e.message); }
  loadInboxDrafts();
}

document.getElementById('inbox-refresh')?.addEventListener('click', loadInbox);
document.getElementById('inbox-unread-only')?.addEventListener('change', loadInbox);
document.getElementById('inbox-triage')?.addEventListener('click', async () => {
  const btn = document.getElementById('inbox-triage');
  const report = document.getElementById('inbox-triage-report');
  btn.textContent = 'Triaging…'; btn.disabled = true;
  try {
    const r = await api('/api/email/triage', { method: 'POST', body: { limit: 12, unread_only: true } });
    report.style.display = 'block';
    report.textContent = r.report || 'No report.';
  } catch (e) {
    report.style.display = 'block';
    report.textContent = 'Triage failed: ' + e.message;
  }
  btn.textContent = 'AI triage unread'; btn.disabled = false;
});

// ============== CALENDAR ==============
async function loadCalendar() {
  const list = document.getElementById('cal-list');
  const status = document.getElementById('cal-status');
  const days = document.getElementById('cal-range')?.value || 7;
  if (status) status.textContent = 'Loading…';
  let data;
  try {
    data = await api('/api/calendar/events?days_ahead=' + days);
  } catch (e) {
    list.innerHTML = `<div class="evo-empty">Failed: ${escapeHTML(e.message)}</div>`;
    if (status) status.textContent = '';
    return;
  }
  if (status) status.textContent = '';
  if (!data.configured) {
    list.innerHTML = `<div class="evo-empty">Calendar not configured. Add <code>CALDAV_URL</code>,
      <code>CALDAV_USERNAME</code>, and <code>CALDAV_PASSWORD</code> to your .env, run
      <code>pip install caldav</code>, and restart Apex.</div>`;
    return;
  }
  const events = data.events || [];
  if (events[0]?.error) {
    list.innerHTML = `<div class="evo-empty">${escapeHTML(events[0].error)}</div>`;
    return;
  }
  if (!events.length) {
    list.innerHTML = `<div class="evo-empty">Nothing scheduled. ✓</div>`;
    return;
  }
  // Group by day.
  const groups = {};
  events.forEach(ev => {
    const d = new Date(ev.start);
    const key = d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
    (groups[key] = groups[key] || []).push(ev);
  });
  list.innerHTML = Object.entries(groups).map(([day, evs]) => `
    <div class="cal-day">
      <div class="cal-day-label">${escapeHTML(day)}</div>
      ${evs.map(ev => {
        const start = new Date(ev.start);
        const time = ev.all_day ? 'all day'
          : start.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
        const soon = !ev.all_day && ev.starts_in_min > 0 && ev.starts_in_min <= 60;
        return `<div class="cal-event ${soon ? 'cal-soon' : ''}">
          <div class="cal-time">${escapeHTML(time)}</div>
          <div class="cal-body">
            <div class="cal-summary">${escapeHTML(ev.summary || '(no title)')}</div>
            ${ev.location ? `<div class="cal-loc">${escapeHTML(ev.location)}</div>` : ''}
          </div>
          ${soon ? `<span class="badge badge-net">in ${ev.starts_in_min}m</span>` : ''}
        </div>`;
      }).join('')}
    </div>`).join('');
}
document.getElementById('cal-refresh')?.addEventListener('click', loadCalendar);
document.getElementById('cal-range')?.addEventListener('change', loadCalendar);

// ============== TELEMETRY ==============
let telCostChart = null, telModelChart = null;
async function loadTelemetry() {
  loadBudget();
  loadGuardian();
  loadTimeCapsule();
  loadDevices();
  loadTokens();
  const days = parseInt(document.getElementById('tel-window').value);
  const t = await api('/api/telemetry?days=' + days);
  document.getElementById('tel-cost').textContent = fmtCost(t.total_cost_usd);
  document.getElementById('tel-calls').textContent = fmtNum(t.total_calls);
  document.getElementById('tel-cache').textContent = (t.cache_hit_rate * 100).toFixed(1) + '%';
  document.getElementById('tel-out').textContent = fmtNum(t.total_output_tokens);

  if (telCostChart) telCostChart.destroy();
  telCostChart = new Chart(document.getElementById('tel-cost-chart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: t.by_day.map(d => new Date(d.day * 1000).toLocaleDateString([], {month:'short', day:'numeric'})),
      datasets: [{ label: 'Cost (USD)', data: t.by_day.map(d => d.cost_usd), backgroundColor: '#6cf', borderRadius: 4 }],
    },
    options: chartOpts({ y: { ticks: { callback: v => '$' + v } } })
  });

  if (telModelChart) telModelChart.destroy();
  telModelChart = new Chart(document.getElementById('tel-model-chart').getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: t.by_model.map(m => m.model.replace('claude-', '')),
      datasets: [{
        data: t.by_model.map(m => m.input_tokens + m.cache_read_tokens + m.output_tokens),
        backgroundColor: ['#6cf', '#8a7cff', '#3ddc97', '#ffb547', '#ff6a6a'],
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'right', labels: { color: '#8892a6', font: { size: 11 } } } },
    }
  });

  const tbody = document.querySelector('#tel-model-table tbody');
  tbody.innerHTML = t.by_model.map(m => `
    <tr>
      <td>${m.model.replace('claude-', '')}</td>
      <td>${fmtNum(m.calls)}</td>
      <td>${fmtNum(m.input_tokens)}</td>
      <td>${fmtNum(m.cache_read_tokens)}</td>
      <td>${fmtNum(m.output_tokens)}</td>
      <td>${m.avg_latency_ms} ms</td>
      <td>${fmtCost(m.cost_usd)}</td>
    </tr>`).join('') || '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:20px">No usage yet.</td></tr>';
}
document.getElementById('tel-refresh').addEventListener('click', loadTelemetry);
document.getElementById('tel-window').addEventListener('change', loadTelemetry);

// ============== BUDGET ==============
async function loadBudget() {
  const b = await api('/api/budget');
  const pct = (spent, limit) => limit > 0 ? Math.min(spent / limit * 100, 100).toFixed(1) : 0;
  const lbl = (spent, limit) => '$' + spent.toFixed(2) + ' / $' + limit.toFixed(2);

  const dailyBar = document.getElementById('budget-bar-daily');
  const sessBar  = document.getElementById('budget-bar-session');
  dailyBar.style.width = pct(b.today_spend, b.daily_usd) + '%';
  sessBar.style.width  = pct(b.session_spend, b.session_usd) + '%';
  dailyBar.classList.toggle('over', b.daily_usd > 0 && b.today_spend / b.daily_usd >= 0.9);
  sessBar.classList.toggle('over',  b.session_usd > 0 && b.session_spend / b.session_usd >= 0.9);
  document.getElementById('budget-label-daily').textContent   = lbl(b.today_spend, b.daily_usd);
  document.getElementById('budget-label-session').textContent = lbl(b.session_spend, b.session_usd);

  const checkbox = document.getElementById('budget-enabled');
  checkbox.checked = b.enabled;
  document.getElementById('budget-enabled-label').textContent = b.enabled ? 'Enabled' : 'Disabled';
  document.getElementById('budget-daily-input').value   = b.daily_usd;
  document.getElementById('budget-session-input').value = b.session_usd;
}

document.getElementById('budget-enabled').addEventListener('change', async (e) => {
  await api('/api/budget', { method: 'POST', body: { enabled: e.target.checked } });
  document.getElementById('budget-enabled-label').textContent = e.target.checked ? 'Enabled' : 'Disabled';
});

document.getElementById('budget-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const daily   = parseFloat(document.getElementById('budget-daily-input').value);
  const session = parseFloat(document.getElementById('budget-session-input').value);
  if (isNaN(daily) || isNaN(session) || daily < 0 || session < 0) return;
  await api('/api/budget', { method: 'POST', body: { daily_usd: daily, session_usd: session } });
  await loadBudget();
});

// ============== GUARDIAN ANGEL ==============
async function loadGuardian() {
  const g = await api('/api/guardian');
  const checkbox = document.getElementById('guardian-enabled');
  if (!checkbox) return;
  checkbox.checked = g.enabled;
  document.getElementById('guardian-enabled-label').textContent = g.enabled ? 'Active' : 'Paused';

  const logEl = document.getElementById('guardian-log');
  if (!logEl) return;
  if (!g.log || g.log.length === 0) {
    logEl.innerHTML = '<div class="guardian-empty">No interventions yet — Guardian Angel is watching.</div>';
    return;
  }
  const KIND_ICONS = {
    angry_message: '😤', night_purchase: '🛒', destructive_commit: '⚠️',
    calendar_conflict: '📅', big_purchase: '💳', sensitive_paste: '🔑',
  };
  logEl.innerHTML = g.log.map(e => {
    const icon = KIND_ICONS[e.kind] || '👁';
    const d = new Date(e.ts * 1000);
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return `<div class="guardian-entry">
      <span class="guardian-icon">${icon}</span>
      <div class="guardian-body">
        <div class="guardian-verdict">${escapeHTML(e.verdict)}</div>
        <div class="guardian-meta">${escapeHTML(e.description)} · ${time}</div>
      </div>
    </div>`;
  }).join('');
}

document.getElementById('guardian-enabled')?.addEventListener('change', async (e) => {
  await api('/api/guardian/toggle', { method: 'POST', body: { enabled: e.target.checked } });
  document.getElementById('guardian-enabled-label').textContent = e.target.checked ? 'Active' : 'Paused';
});

// ============== TIME CAPSULE ==============
async function loadTimeCapsule() {
  const t = await api('/api/timecapsule');
  const checkbox = document.getElementById('timecapsule-enabled');
  if (!checkbox) return;
  checkbox.checked = t.enabled;
  document.getElementById('timecapsule-enabled-label').textContent = t.enabled ? 'Active' : 'Paused';

  const logEl = document.getElementById('timecapsule-log');
  if (!logEl) return;
  if (!t.log || t.log.length === 0) {
    logEl.innerHTML = '<div class="capsule-empty">No capsules yet — mention a goal or how you feel and Apex will remember.</div>';
    return;
  }
  const KIND_ICONS = {
    goal: '🎯', aspiration: '✨', commitment: '🤝', concern: '💭', reflection: '🪞',
  };
  logEl.innerHTML = t.log.map(e => {
    const icon = KIND_ICONS[e.kind] || '🕰️';
    const captured = (e.action === 'surfaced') ? 'surfaced' : 'captured';
    const when = new Date(e.ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
    const due = e.callback_date
      ? new Date(e.callback_date * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })
      : '';
    const meta = (e.action === 'surfaced')
      ? `surfaced · ${when}`
      : `captured ${when} · check back ${due}`;
    const text = e.verdict ? e.verdict : e.statement;
    return `<div class="capsule-entry ${e.action === 'surfaced' ? 'capsule-surfaced' : ''}">
      <span class="capsule-icon">${icon}</span>
      <div class="capsule-body">
        <div class="capsule-statement">${escapeHTML(text)}</div>
        <div class="capsule-meta">${escapeHTML(meta)}</div>
      </div>
    </div>`;
  }).join('');
}

document.getElementById('timecapsule-enabled')?.addEventListener('change', async (e) => {
  await api('/api/timecapsule/toggle', { method: 'POST', body: { enabled: e.target.checked } });
  document.getElementById('timecapsule-enabled-label').textContent = e.target.checked ? 'Active' : 'Paused';
});

// ============== REPLAY ==============
async function loadReplay() {
  const sessions = await api('/api/telemetry/sessions');
  const list = document.getElementById('replay-sessions');
  list.innerHTML = sessions.map(s => `
    <div class="replay-session-item" onclick="showReplay(${s.id})" id="sess-${s.id}">
      <div><strong>Session #${s.id}</strong></div>
      <div class="sess-meta">${fmtDate(s.started_at)} · ${s.calls} calls · ${fmtCost(s.cost_usd)}</div>
    </div>`).join('') || '<div class="empty-state">No sessions.</div>';
}
async function showReplay(sessionId) {
  document.querySelectorAll('.replay-session-item').forEach(el => el.classList.remove('active'));
  document.getElementById('sess-' + sessionId)?.classList.add('active');
  const r = await api('/api/replay/' + sessionId);
  const detail = document.getElementById('replay-detail');
  const turns = (r.turns || []).map(t => {
    const content = typeof t.content === 'object' ? JSON.stringify(t.content, null, 2) : String(t.content);
    const tools = (t.tool_calls || []).length ? `<div class="card-meta" style="margin-top:4px">tools: ${t.tool_calls.map(tc => tc.name || tc.tool || '?').join(', ')}</div>` : '';
    return `
      <div class="replay-turn role-${t.role}">
        <div class="turn-head"><span class="turn-role">${t.role}</span><span class="turn-ts">${fmtTs(t.ts)}</span></div>
        <pre>${escapeHTML(content)}</pre>
        ${tools}
      </div>`;
  }).join('');
  const usageRows = (r.usage || []).map(u => `
    <tr>
      <td>${u.turn_index}</td><td>${u.call_site}</td><td>${u.model.replace('claude-', '')}</td>
      <td>${fmtNum(u.input_tokens)} / ${fmtNum(u.cache_read_tokens)} / ${fmtNum(u.output_tokens)}</td>
      <td>${u.latency_ms} ms</td><td>${fmtCost(u.cost_usd)}</td>
    </tr>`).join('');
  detail.innerHTML = `
    <h3 style="margin-top:0">Session #${r.session.id}</h3>
    <div class="card-meta">${fmtDate(r.session.started_at)} → ${r.session.ended_at ? fmtDate(r.session.ended_at) : 'open'} · ${(r.usage || []).length} API calls</div>
    <h4 style="margin-top:20px">Per-call usage</h4>
    <table class="data-table">
      <thead><tr><th>Turn</th><th>Site</th><th>Model</th><th>In / Cache / Out</th><th>Latency</th><th>Cost</th></tr></thead>
      <tbody>${usageRows || '<tr><td colspan="6" style="text-align:center;color:var(--text-dim)">No usage rows.</td></tr>'}</tbody>
    </table>
    <h4 style="margin-top:24px">Turns</h4>
    ${turns || '<div class="empty-state">No turn data captured for this session.</div>'}
  `;
}

// ============== SCHEDULE ==============
async function loadTasks() {
  const tasks = await api('/api/tasks');
  const wrap = document.getElementById('tasks-list');
  wrap.innerHTML = tasks.map(t => `
    <div class="card">
      <div class="card-head">
        <div>
          <div class="card-title">${escapeHTML(t.description)}</div>
          <div class="card-meta">${t.trigger_type}: <code>${escapeHTML(JSON.stringify(t.trigger_params))}</code></div>
          <div class="card-meta">Runs: ${t.run_count} · Last: ${t.last_run ? new Date(t.last_run*1000).toLocaleString() : 'never'}</div>
        </div>
      </div>
      <div class="actions"><button onclick="cancelTask('${t.id}')">Cancel</button></div>
    </div>`).join('') || '<div class="empty-state">No scheduled tasks.</div>';
}
async function cancelTask(id) { await api('/api/tasks/' + id, { method: 'DELETE' }); loadTasks(); }
document.getElementById('task-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target;
  let params; try { params = JSON.parse(f.trigger_params.value); }
  catch { alert('trigger_params must be valid JSON'); return; }
  await api('/api/tasks', { method: 'POST', body: {
    description: f.description.value, trigger_type: f.trigger_type.value, trigger_params: params,
  }});
  f.reset(); loadTasks();
});

// ============== SUB-AGENTS ==============
async function loadSubagents() {
  const subs = await api('/api/subagents');
  const wrap = document.getElementById('subagents-list');
  const entries = Object.entries(subs);
  wrap.innerHTML = entries.map(([id, s]) => `
    <div class="card">
      <div class="card-head">
        <div>
          <div class="card-title">${id} <span class="badge horizon">${s.role}</span> <span class="status-${s.status}">[${s.status}]</span></div>
          <div class="card-meta">${escapeHTML(s.task)}</div>
        </div>
      </div>
      ${s.result_preview ? `<pre style="white-space:pre-wrap;font-size:12px;margin-top:8px;background:var(--bg-3);padding:10px;border-radius:6px;color:var(--text-mute)">${escapeHTML(s.result_preview)}</pre>` : ''}
      ${s.error ? `<div class="status-error" style="margin-top:6px">${escapeHTML(s.error)}</div>` : ''}
    </div>`).join('') || '<div class="empty-state">No sub-agents running.</div>';
}

// ============== KNOWLEDGE BASE ==============
async function loadKB() {
  try {
    const s = await api('/api/knowledge/stats');
    document.getElementById('kb-stats').textContent = `Indexed: ${s.files} files · ${s.chunks} chunks`;
  } catch { document.getElementById('kb-stats').textContent = '—'; }
}
document.getElementById('kb-reindex-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target;
  const paths = f.paths.value.split(',').map(s => s.trim()).filter(Boolean);
  document.getElementById('kb-stats').textContent = 'Indexing...';
  const r = await api('/api/knowledge/reindex', { method: 'POST', body: { paths, force: f.force.checked }});
  alert(r.result); loadKB();
});
document.getElementById('kb-search-btn').addEventListener('click', async () => {
  const q = document.getElementById('kb-search-input').value; if (!q) return;
  const results = await api('/api/knowledge/search?q=' + encodeURIComponent(q));
  document.getElementById('kb-results').innerHTML = results.map(r => `
    <div class="card">
      <div class="card-meta">${escapeHTML(r.path)} #${r.chunk_index} — score ${r.score}</div>
      <pre style="white-space:pre-wrap;font-size:12.5px;background:var(--bg-3);padding:10px;border-radius:6px;margin-top:6px">${escapeHTML(r.content)}</pre>
    </div>`).join('') || '<div class="empty-state">No matches.</div>';
});

// ============== SELF-MOD ==============
async function loadSelfMod() {
  const s = await api('/api/selfmod');
  document.getElementById('selfmod-state').innerHTML = `
    <div class="panel" style="margin-bottom:14px">
      <div class="panel-head"><h3>Prompt overlay</h3><span class="panel-sub">${s.prompt_addition_chars} chars</span></div>
      ${s.prompt_addition_preview ? `<pre style="white-space:pre-wrap;font-size:12.5px;color:var(--text-mute);margin:0">${escapeHTML(s.prompt_addition_preview)}</pre>` : '<div class="card-meta">(empty)</div>'}
      <div class="card-meta" style="margin-top:10px">Dynamic tools: ${s.dynamic_tools.join(', ') || '(none)'}</div>
    </div>`;
}
document.getElementById('prompt-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target;
  await api('/api/selfmod/prompt', { method: 'POST', body: { addition: f.addition.value, replace: f.replace.checked }});
  f.reset(); loadSelfMod();
});
document.getElementById('revert-btn').addEventListener('click', async () => {
  if (!confirm('Revert ALL self-modifications?')) return;
  await api('/api/selfmod/revert', { method: 'POST', body: {} });
  loadSelfMod();
});

// ============== BRIEFING ==============
async function loadBriefing() {
  try {
    const b = await api('/api/briefing');
    document.getElementById('briefing-enabled').checked   = b.enabled === 'true' || b.enabled === true;
    document.getElementById('briefing-time').value        = b.time || '08:00';
    document.getElementById('briefing-timezone').value    = b.timezone || 'America/New_York';
    document.getElementById('briefing-location').value    = b.location || '';
    document.getElementById('briefing-topics').value      = b.news_topics || '';

    const st = document.getElementById('briefing-status');
    if (b.task) {
      const last = b.task.last_run
        ? new Date(b.task.last_run * 1000).toLocaleString()
        : 'Never';
      st.innerHTML = `<div class="card-meta">Task ID: <code>${b.task.id}</code></div>
        <div class="card-meta">Last run: ${last}</div>
        <div class="card-meta">Run count: ${b.task.run_count}</div>`;
    } else {
      st.innerHTML = '<div class="card-meta">No briefing task scheduled yet. Enable and save to activate.</div>';
    }
  } catch (e) {
    document.getElementById('briefing-status').textContent = 'Could not load briefing config.';
  }
  // Wire form (once)
  const form = document.getElementById('briefing-form');
  if (form._wired) return;
  form._wired = true;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = form.querySelector('[type=submit]');
    btn.textContent = 'Saving…';
    try {
      const r = await api('/api/briefing', { method: 'POST', body: {
        enabled:     document.getElementById('briefing-enabled').checked ? 'true' : 'false',
        time:        document.getElementById('briefing-time').value,
        timezone:    document.getElementById('briefing-timezone').value,
        location:    document.getElementById('briefing-location').value,
        news_topics: document.getElementById('briefing-topics').value,
      }});
      btn.textContent = 'Saved!';
      setTimeout(() => { btn.textContent = 'Save & schedule'; }, 2000);
      loadBriefing();
    } catch (err) {
      btn.textContent = 'Error — retry';
    }
  });
  document.getElementById('briefing-run-now').addEventListener('click', async () => {
    const btn = document.getElementById('briefing-run-now');
    btn.textContent = 'Starting…';
    btn.disabled = true;
    try {
      const r = await api('/api/briefing/run_now', { method: 'POST', body: {} });
      btn.textContent = 'Running!';
      setTimeout(() => { btn.textContent = 'Run now'; btn.disabled = false; }, 3000);
    } catch (err) {
      btn.textContent = 'Error'; btn.disabled = false;
    }
  });
}

// ============== PHONE ==============
async function loadPhone() {
  try {
    const p = await api('/api/phone/status');
    document.getElementById('phone-state').innerHTML = `
      <div class="panel-head"><h3>Status</h3><span class="${p.configured ? 'status-active' : 'status-failed'}">${p.configured ? '● Configured' : '● Not configured'}</span></div>
      <div class="card-meta">From: ${p.from_number || '—'}</div>
      <div class="card-meta">Allowed inbound: ${(p.allowed_numbers || []).join(', ') || '(any)'}</div>
    `;
  } catch (e) {
    document.getElementById('phone-state').innerHTML = '<div class="card-meta">Unavailable.</div>';
  }
}
document.getElementById('sms-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target;
  document.getElementById('sms-result').textContent = 'Sending...';
  try {
    const r = await api('/api/phone/sms', { method: 'POST', body: { to: f.to.value, body: f.body.value }});
    document.getElementById('sms-result').textContent = r.result;
  } catch (err) { document.getElementById('sms-result').textContent = err.message; }
});

// Refresh sub-agents periodically
setInterval(() => {
  if (document.querySelector('.nav-btn.active')?.dataset.tab === 'subagents') loadSubagents();
}, 3000);

// Refresh command center every 20s
setInterval(() => {
  if (document.querySelector('.nav-btn.active')?.dataset.tab === 'overview') loadCommand();
}, 20000);

// ============== CHAT ==============
let activeChatId = null;
let currentAgentBubble = null;
let currentAgentText = '';

// ============== CHAT HISTORY (sessionStorage) ==============
const _HIST_KEY = 'apex_chat_history';
const _HIST_MAX = 30;

function _historyPush(role, text) {
  try {
    const arr = JSON.parse(sessionStorage.getItem(_HIST_KEY) || '[]');
    arr.push({ role, text });
    if (arr.length > _HIST_MAX) arr.splice(0, arr.length - _HIST_MAX);
    sessionStorage.setItem(_HIST_KEY, JSON.stringify(arr));
  } catch(e) {}
}

function _historyLoad() {
  try {
    const arr = JSON.parse(sessionStorage.getItem(_HIST_KEY) || '[]');
    arr.forEach(m => _appendChatMsg(m.role, m.text, { skipHistory: true }));
  } catch(e) {}
}

function _historyClear() {
  try { sessionStorage.removeItem(_HIST_KEY); } catch(e) {}
  const msgs = document.getElementById('chat-messages');
  if (msgs) msgs.innerHTML = '';
}

function loadChat() {
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');
  loadModelPicker();
  if (input._chatWired) return;
  input._chatWired = true;
  sendBtn.addEventListener('click', sendChat);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
  });
  document.getElementById('chat-mic')?.addEventListener('click', toggleMic);
  document.getElementById('chat-council')?.addEventListener('click', () => {
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    document.querySelector('.nav-btn[data-tab="council"]')?.click();
    const q = document.getElementById('council-question');
    if (q) q.value = text;
    runCouncil();
  });
  document.getElementById('chat-clear')?.addEventListener('click', _historyClear);
  // Smart scroll: track if user scrolls up
  const msgs = document.getElementById('chat-messages');
  if (msgs) {
    msgs.addEventListener('scroll', () => {
      _userScrolledUp = (msgs.scrollHeight - msgs.clientHeight - msgs.scrollTop) > 80;
    });
  }
  // Restore previous session's messages
  _historyLoad();
}

// ============== MODEL PICKER ==============
async function loadModelPicker() {
  const sel = document.getElementById('chat-model');
  if (!sel || sel._wired) {
    if (sel) refreshModelSelection();
    return;
  }
  sel._wired = true;
  try {
    const data = await api('/api/models');
    sel.innerHTML = '';
    data.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.model;
      opt.textContent = m.available ? m.model : `${m.model} (no API key)`;
      opt.disabled = !m.available;
      sel.appendChild(opt);
    });
    sel.value = data.current;
  } catch (e) { console.error('loadModelPicker', e); }
  sel.addEventListener('change', async () => {
    const msg = document.getElementById('chat-model-msg');
    try {
      const r = await api('/api/model', { method: 'POST', body: { model: sel.value } });
      if (msg) { msg.textContent = r.message; msg.className = 'chat-model-msg ' + (r.ok ? 'ok' : 'err'); }
      refreshStatus();
    } catch (e) {
      if (msg) { msg.textContent = 'Switch failed: ' + e.message; msg.className = 'chat-model-msg err'; }
    }
  });
}
async function refreshModelSelection() {
  const sel = document.getElementById('chat-model');
  if (!sel) return;
  try { sel.value = (await api('/api/models')).current; } catch (e) {}
}

// Track whether user has manually scrolled up during a stream
let _userScrolledUp = false;

function _appendChatMsg(role, text, { skipHistory } = {}) {
  const msgs = document.getElementById('chat-messages');
  if (!msgs) return null;
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  const rendered = role === 'user' ? escapeHTML(text) : renderMarkdown(text);
  div.innerHTML = `<div class="chat-msg-role">${role === 'user' ? 'You' : 'Agent'}</div>` +
                  `<div class="chat-msg-content">${rendered}</div>`;
  msgs.appendChild(div);
  if (!_userScrolledUp) msgs.scrollTop = msgs.scrollHeight;
  if (!skipHistory) _historyPush(role, text);
  return div;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');
  const text = (input?.value || '').trim();
  if (!text || activeChatId) return;

  input.value = '';
  input.style.height = 'auto';
  _userScrolledUp = false;
  _appendChatMsg('user', text);

  _setApexState('thinking');
  currentAgentText = '';
  currentAgentBubble = _appendChatMsg('agent', '', { skipHistory: true });
  currentAgentBubble.classList.add('streaming');
  currentAgentBubble.dataset.prompt = text;
  // Show thinking indicator until first token arrives
  const _thinkEl = currentAgentBubble.querySelector('.chat-msg-content');
  if (_thinkEl) _thinkEl.innerHTML = '<span class="chat-thinking">thinking…</span>';
  sendBtn.disabled = true;

  // Generate chat_id client-side so WS tokens are routed before POST returns
  const chatId = Math.random().toString(36).slice(2, 10);
  activeChatId = chatId;

  try {
    await api('/api/chat', { method: 'POST', body: { message: text, chat_id: chatId } });
  } catch (e) {
    if (activeChatId === chatId) _chatError(`HTTP error: ${e.message}`, chatId);
    return;
  }
  // Fallback: finalize if chat_done already arrived via WS and cleared activeChatId
  if (activeChatId === chatId) _chatFinalize(chatId);
}

function _chatAppendToken(delta, chatId) {
  if (activeChatId !== chatId || !currentAgentBubble) return;
  const firstToken = currentAgentText === '';
  currentAgentText += delta;
  const el = currentAgentBubble.querySelector('.chat-msg-content');
  if (el) {
    if (firstToken) {
      // Remove thinking indicator; switch to plain text during streaming
      el.innerHTML = '';
    }
    el.textContent = currentAgentText;
  }
  const msgs = document.getElementById('chat-messages');
  if (msgs && !_userScrolledUp) msgs.scrollTop = msgs.scrollHeight;
}

function _chatFinalize(chatId, response, sessionId, turnIndex) {
  if (activeChatId !== chatId) return;
  if (currentAgentBubble) {
    currentAgentBubble.classList.remove('streaming');
    // Render final markdown now that streaming is complete
    const el = currentAgentBubble.querySelector('.chat-msg-content');
    if (el && currentAgentText) el.innerHTML = renderMarkdown(currentAgentText);
    if (sessionId != null && turnIndex != null) {
      currentAgentBubble.dataset.sessionId = String(sessionId);
      currentAgentBubble.dataset.turnIndex = String(turnIndex);
    }
    if (currentAgentBubble.dataset.prompt) {
      const footer = document.createElement('div');
      footer.className = 'chat-msg-footer';
      const fbDisabled = (sessionId == null || turnIndex == null) ? 'disabled' : '';
      footer.innerHTML =
        '<button type="button" class="chat-feedback-btn fb-up" data-rating="1" ' + fbDisabled +
        ' title="Mark this response helpful">👍</button>' +
        '<button type="button" class="chat-feedback-btn fb-down" data-rating="-1" ' + fbDisabled +
        ' title="Mark this response unhelpful">👎</button>' +
        '<button type="button" class="chat-second-opinion-btn" ' +
        'title="Send the original question to the council">' +
        '<span class="cso-icon">⚖</span> Second opinion</button>';
      currentAgentBubble.appendChild(footer);
    }
    // Persist to session history
    _historyPush('agent', currentAgentText);
  }
  const spoken = response || currentAgentText;
  currentAgentBubble = null;
  activeChatId = null;
  const sendBtn = document.getElementById('chat-send');
  if (sendBtn) sendBtn.disabled = false;
  if (spoken) speakText(spoken);
}

// One delegated handler covers every chat bubble — historical and new.
document.addEventListener('click', e => {
  const btn = e.target.closest('.chat-second-opinion-btn');
  if (!btn) return;
  const bubble = btn.closest('.chat-msg');
  const prompt = bubble?.dataset.prompt;
  if (!prompt) return;
  document.querySelector('.nav-btn[data-tab="council"]')?.click();
  const q = document.getElementById('council-question');
  if (q) q.value = prompt;
  runCouncil();
});

// 👍/👎 feedback buttons on chat bubbles
document.addEventListener('click', async e => {
  const btn = e.target.closest('.chat-feedback-btn');
  if (!btn || btn.disabled) return;
  const bubble = btn.closest('.chat-msg');
  const sessionId = bubble?.dataset.sessionId;
  const turnIndex = bubble?.dataset.turnIndex;
  if (sessionId == null || turnIndex == null) return;
  const rating = parseInt(btn.dataset.rating, 10);
  try {
    await api('/api/feedback', {
      method: 'POST',
      body: {
        rating, session_id: parseInt(sessionId, 10),
        turn_index: parseInt(turnIndex, 10), source: 'dashboard',
      },
    });
    // Mark the chosen one selected; clear the other.
    const footer = btn.parentElement;
    footer?.querySelectorAll('.chat-feedback-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
  } catch (err) {
    console.error('feedback failed', err);
  }
});

function _chatError(error, chatId) {
  if (activeChatId !== chatId) return;
  const prompt = currentAgentBubble?.dataset.prompt || '';
  if (currentAgentBubble) {
    const el = currentAgentBubble.querySelector('.chat-msg-content');
    if (el) { el.textContent = error; el.classList.add('chat-error-text'); }
    currentAgentBubble.classList.remove('streaming');
    if (prompt) {
      const footer = document.createElement('div');
      footer.className = 'chat-msg-footer';
      footer.innerHTML = '<button type="button" class="chat-retry-btn">↩ Retry</button>';
      currentAgentBubble.appendChild(footer);
    }
    currentAgentBubble = null;
  }
  activeChatId = null;
  const sendBtn = document.getElementById('chat-send');
  if (sendBtn) sendBtn.disabled = false;
}

// Retry handler — delegated so it works on any error bubble
document.addEventListener('click', e => {
  const btn = e.target.closest('.chat-retry-btn');
  if (!btn || activeChatId) return;
  const bubble = btn.closest('.chat-msg');
  const prompt = bubble?.dataset.prompt;
  if (!prompt) return;
  bubble.remove();
  const input = document.getElementById('chat-input');
  if (input) input.value = prompt;
  sendChat();
});

// ============== VOICE ==============
let mediaRecorder = null;
let audioChunks = [];

async function toggleMic() {
  const micBtn = document.getElementById('chat-mic');
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    alert('Microphone unavailable or permission denied.');
    return;
  }
  audioChunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = e => { if (e.data.size) audioChunks.push(e.data); };
  mediaRecorder.onstop = async () => {
    stream.getTracks().forEach(t => t.stop());
    micBtn?.classList.remove('recording');
    const type = mediaRecorder.mimeType || 'audio/webm';
    await transcribeAndSend(new Blob(audioChunks, { type }), type);
  };
  mediaRecorder.start();
  micBtn?.classList.add('recording');
}

async function transcribeAndSend(blob, type) {
  const micBtn = document.getElementById('chat-mic');
  const input = document.getElementById('chat-input');
  micBtn?.classList.add('transcribing');
  const ext = (type.includes('mp4') || type.includes('mpeg') || type.includes('m4a')) ? 'mp4' : 'webm';
  const fd = new FormData();
  fd.append('file', blob, 'speech.' + ext);
  try {
    const r = await fetch('/api/transcribe', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'transcription failed');
    if (data.text) {
      input.value = data.text;
      sendChat();
    }
  } catch (e) {
    alert('Voice input failed: ' + e.message);
  } finally {
    micBtn?.classList.remove('transcribing');
  }
}

function speakText(text) {
  if (!document.getElementById('voice-output')?.checked) return;
  const engine = document.getElementById('voice-engine')?.value || 'browser';
  if (engine === 'openai') speakOpenAI(text);
  else speakBrowser(text);
}
function speakBrowser(text) {
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    // No audio-stream access for speechSynthesis → drive a procedural talk
    // cycle on the avatar via boundary/start/end events.
    _lipSyncActive = false;
    u.onstart = () => _setApexState('speaking');
    u.onend   = () => _setApexState('idle');
    window.speechSynthesis.speak(u);
  } catch (e) {}
}
async function speakOpenAI(text) {
  try {
    const r = await fetch('/api/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) { speakBrowser(text); return; }
    const audio = new Audio(URL.createObjectURL(await r.blob()));
    audio.crossOrigin = 'anonymous';
    _setApexState('speaking');
    // Real lip-sync: drive the avatar mouth from the live audio amplitude.
    const wired = _attachLipSync(audio);
    if (!wired) audio.addEventListener('ended', () => _setApexState('idle'));
    audio.play().catch(() => {});
  } catch (e) { speakBrowser(text); }
}

// ============== COUNCIL ==============
let councilRunning = false;

async function loadCouncil() {
  const form = document.getElementById('council-form');
  if (!form || form._wired) return;
  form._wired = true;
  form.addEventListener('submit', e => { e.preventDefault(); runCouncil(); });
  try {
    const data = await api('/api/council/roster');
    const panel = document.getElementById('council-panel');
    if (panel) {
      panel.innerHTML = (data.roster || []).map(m =>
        `<label class="council-pick${m.available ? '' : ' disabled'}">` +
        `<input type="checkbox" value="${escapeHTML(m.model)}" ${m.available ? 'checked' : 'disabled'}>` +
        `<span>${escapeHTML(m.label)}${m.available ? '' : ' · no API key'}</span></label>`
      ).join('');
    }
    const preset = document.getElementById('council-preset');
    if (preset) {
      preset.innerHTML = (data.presets || []).map(p =>
        `<option value="${escapeHTML(p.id)}">${escapeHTML(p.label)}</option>`).join('');
    }
  } catch (e) { /* roster fetch failed — council still works with backend defaults */ }
}

async function runCouncil() {
  if (councilRunning) return;
  const q = document.getElementById('council-question').value.trim();
  if (!q) return;
  const rounds = parseInt(document.getElementById('council-rounds').value, 10);
  const presetEl = document.getElementById('council-preset');
  const preset = presetEl ? presetEl.value : 'general';
  const picks = Array.from(document.querySelectorAll('#council-panel input'));
  const panel = picks.filter(c => c.checked).map(c => c.value);
  if (picks.length && panel.length < 2) {
    _councilError('Pick at least 2 members for the council.');
    return;
  }
  const btn = document.getElementById('council-convene');
  const progress = document.getElementById('council-progress');
  const verdict = document.getElementById('council-verdict');
  const transcript = document.getElementById('council-transcript');

  councilRunning = true;
  btn.disabled = true;
  btn.textContent = 'Council in session…';
  verdict.innerHTML = '';
  transcript.innerHTML = '';
  progress.innerHTML = '<div class="council-step">Convening the council…</div>';

  const body = { question: q, rounds, preset };
  if (panel.length) body.panel = panel;
  try {
    const result = await api('/api/council', { method: 'POST', body });
    if (councilRunning) _councilDone(result);  // fallback if the WS event didn't arrive
  } catch (e) {
    _councilError('Request failed: ' + e.message);
  }
}

function _councilProgress(msg) {
  const progress = document.getElementById('council-progress');
  if (progress) {
    const d = document.createElement('div');
    d.className = 'council-step';
    d.textContent = msg;
    progress.appendChild(d);
  }
}

function _councilRoundGroup(r) {
  const transcript = document.getElementById('council-transcript');
  if (!transcript) return null;
  let group = transcript.querySelector(`.council-round[data-round="${r}"]`);
  if (!group) {
    group = document.createElement('div');
    group.className = 'council-round';
    group.dataset.round = r;
    const label = document.createElement('div');
    label.className = 'council-round-label';
    label.textContent = r === 0 ? 'Opening statements' : `Debate round ${r}`;
    const cards = document.createElement('div');
    cards.className = 'cards';
    group.appendChild(label);
    group.appendChild(cards);
    transcript.appendChild(group);
  }
  return group;
}

function _councilEntry(group, label) {
  const sel = `.council-entry[data-label="${CSS.escape(label)}"]`;
  let entry = group.querySelector(sel);
  if (!entry) {
    entry = document.createElement('div');
    entry.className = 'council-entry';
    entry.dataset.label = label;
    entry.innerHTML =
      `<div class="council-entry-head">${escapeHTML(label)}` +
      `<span class="council-pill thinking">thinking…</span></div>` +
      `<div class="council-entry-body"></div>`;
    group.querySelector('.cards').appendChild(entry);
  }
  return entry;
}

function _councilRoundStart(round, members) {
  const group = _councilRoundGroup(round);
  if (!group) return;
  (members || []).forEach(label => _councilEntry(group, label));
}

function _councilAnswer(data) {
  const group = _councilRoundGroup(data.round);
  if (!group) return;
  const entry = _councilEntry(group, data.label);
  const pill = entry.querySelector('.council-pill');
  if (pill) pill.remove();
  const body = entry.querySelector('.council-entry-body');
  if (body) body.innerHTML = renderMarkdown(data.text || '');
  entry.classList.add('council-entry-done');
}

function _councilDone(data) {
  if (!councilRunning) return;  // already rendered (WS + POST both fired)
  councilRunning = false;
  const btn = document.getElementById('council-convene');
  if (btn) { btn.disabled = false; btn.textContent = 'Convene council'; }
  const verdict = document.getElementById('council-verdict');
  const transcript = document.getElementById('council-transcript');

  if (verdict) {
    const conf = (data.confidence || '').toLowerCase();
    const confNote = data.confidence_note ? ` — ${escapeHTML(data.confidence_note)}` : '';
    const confBadge = conf
      ? `<span class="council-confidence ${conf}">Confidence: ${conf}${confNote}</span>`
      : '';
    const dis = (data.disagreement || '').trim();
    const disagreed = dis && dis.toLowerCase() !== 'the council agreed';
    const disBlock = disagreed
      ? `<div class="council-disagreement"><div class="council-disagreement-head">Where the council split</div><div class="council-disagreement-body">${renderMarkdown(dis)}</div></div>`
      : '';
    verdict.innerHTML =
      `<div class="council-verdict-head">Verdict <span class="council-members">${(data.members || []).map(escapeHTML).join(' · ')}</span>${confBadge}</div>` +
      `<div class="council-verdict-body">${renderMarkdown(data.final_answer || '')}</div>` +
      disBlock;
  }
  // Transcript is normally built live from council_answer events. Only rebuild
  // it here if those events never arrived (POST-only fallback).
  if (transcript && !transcript.children.length) {
    (data.transcript || []).forEach(e => _councilAnswer(e));
  }
  // Any cards still showing "thinking…" failed silently — mark them so the
  // pill stops pulsing forever.
  document.querySelectorAll('.council-entry .council-pill.thinking').forEach(p => {
    p.classList.remove('thinking');
    p.classList.add('skipped');
    p.textContent = 'no response';
  });
}

function _councilError(err) {
  councilRunning = false;
  const btn = document.getElementById('council-convene');
  if (btn) { btn.disabled = false; btn.textContent = 'Convene council'; }
  const progress = document.getElementById('council-progress');
  if (progress) {
    const d = document.createElement('div');
    d.className = 'council-step council-step-err';
    d.textContent = 'Council failed: ' + err;
    progress.appendChild(d);
  }
}

// ============== COMPARE — blind side-by-side model testing ==============
let compareRunning = false;
let _compareId = null;

async function loadCompare() {
  const form = document.getElementById('compare-form');
  if (form && !form._wired) {
    form._wired = true;
    form.addEventListener('submit', e => { e.preventDefault(); runCompare(); });
    try {
      const data = await api('/api/compare/roster');
      const panel = document.getElementById('compare-panel');
      if (panel) {
        panel.innerHTML = (data.roster || []).map(m =>
          `<label class="council-pick${m.available ? '' : ' disabled'}">` +
          `<input type="checkbox" value="${escapeHTML(m.model)}" ${m.available ? 'checked' : 'disabled'}>` +
          `<span>${escapeHTML(m.label)}${m.available ? '' : ' · no API key'}</span></label>`
        ).join('');
      }
    } catch (e) { /* roster optional */ }
  }
  loadLeaderboard();
}

async function runCompare() {
  if (compareRunning) return;
  const q = document.getElementById('compare-question').value.trim();
  if (!q) return;
  const picks = Array.from(document.querySelectorAll('#compare-panel input'));
  const panel = picks.filter(c => c.checked).map(c => c.value);
  if (picks.length && panel.length < 2) { _compareStatus('Pick at least 2 models.', true); return; }

  const btn = document.getElementById('compare-run');
  const arena = document.getElementById('compare-arena');
  const result = document.getElementById('compare-result');
  compareRunning = true; _compareId = null;
  btn.disabled = true; btn.textContent = 'Models answering…';
  result.innerHTML = ''; arena.innerHTML = '';
  _compareStatus('Asking every model in parallel — answers shown blind…');

  const body = { question: q };
  if (panel.length) body.panel = panel;
  try {
    const data = await api('/api/compare/run', { method: 'POST', body });
    if (data.error) { _compareStatus(data.error, true); return; }
    _compareId = data.compare_id;
    _renderArena(data.entries);
    _compareStatus(`${data.count} answers — read them, then pick the best. Labels are hidden.`);
  } catch (e) {
    _compareStatus('Request failed: ' + e.message, true);
  } finally {
    compareRunning = false;
    btn.disabled = false; btn.textContent = 'Run blind comparison';
  }
}

function _renderArena(entries) {
  const arena = document.getElementById('compare-arena');
  arena.innerHTML = (entries || []).map(e =>
    `<div class="cmp-card" data-slot="${escapeHTML(e.slot)}">` +
      `<div class="cmp-card-head"><span class="cmp-slot">${escapeHTML(e.slot)}</span>` +
      `<button class="cmp-pick-btn" data-slot="${escapeHTML(e.slot)}">Pick ${escapeHTML(e.slot)} ✓</button></div>` +
      `<div class="cmp-card-body">${renderMarkdown(e.text)}</div>` +
    `</div>`
  ).join('');
  arena.querySelectorAll('.cmp-pick-btn').forEach(b =>
    b.addEventListener('click', () => pickWinner(b.dataset.slot)));
  const bar = document.createElement('div');
  bar.className = 'cmp-arena-actions';
  bar.innerHTML = '<button id="cmp-synth" class="ghost-btn">Synthesize best of all</button>';
  arena.appendChild(bar);
  const synth = document.getElementById('cmp-synth');
  if (synth) synth.addEventListener('click', synthesizeCompare);
}

async function pickWinner(slot) {
  if (!_compareId) return;
  try {
    const res = await api('/api/compare/pick', { method: 'POST', body: { compare_id: _compareId, slot } });
    if (res.error) { _compareStatus(res.error, true); return; }
    const map = {};
    (res.reveal || []).forEach(r => { map[r.slot] = r.label; });
    document.querySelectorAll('#compare-arena .cmp-card').forEach(card => {
      const s = card.dataset.slot;
      const head = card.querySelector('.cmp-card-head');
      const tag = document.createElement('span');
      tag.className = 'cmp-reveal' + (s === slot ? ' cmp-winner' : '');
      tag.textContent = (map[s] || '?') + (s === slot ? ' · your pick' : '');
      head.appendChild(tag);
      const btn = card.querySelector('.cmp-pick-btn');
      if (btn) btn.remove();
      if (s === slot) card.classList.add('cmp-card-won');
    });
    _compareStatus(`You picked ${res.winner.label}. Logged to your leaderboard.`);
    _compareId = null;
    loadLeaderboard();
  } catch (e) { _compareStatus('Pick failed: ' + e.message, true); }
}

async function synthesizeCompare() {
  if (!_compareId) { _compareStatus('Pick or re-run — synthesis needs a live comparison.', true); return; }
  const result = document.getElementById('compare-result');
  result.innerHTML = '<div class="council-step">Chair synthesizing the best of all answers…</div>';
  try {
    const res = await api('/api/compare/synthesize', { method: 'POST', body: { compare_id: _compareId } });
    if (res.error) { result.innerHTML = ''; _compareStatus(res.error, true); return; }
    result.innerHTML = '<div class="cmp-synth-card"><div class="cmp-synth-title">Synthesis — best of all</div>' +
      '<div class="cmp-synth-body">' + renderMarkdown(res.synthesis) + '</div></div>';
  } catch (e) { result.innerHTML = ''; _compareStatus('Synthesis failed: ' + e.message, true); }
}

async function loadLeaderboard() {
  const el = document.getElementById('compare-leaderboard');
  if (!el) return;
  try {
    const data = await api('/api/compare/leaderboard');
    if (!data.rows || !data.rows.length) {
      el.innerHTML = '<div class="cmp-lb-empty">No comparisons yet — run one above to start ranking models.</div>';
      return;
    }
    const max = Math.max(...data.rows.map(r => r.win_rate), 1);
    el.innerHTML = `<div class="cmp-lb-total">${data.total} comparison${data.total === 1 ? '' : 's'} judged</div>` +
      data.rows.map(r =>
        `<div class="cmp-lb-row">` +
          `<span class="cmp-lb-name">${escapeHTML(r.label)}</span>` +
          `<span class="cmp-lb-bar"><span class="cmp-lb-fill" style="width:${Math.round(100 * r.win_rate / max)}%"></span></span>` +
          `<span class="cmp-lb-stat">${r.win_rate}% · ${r.wins}/${r.appearances}</span>` +
        `</div>`
      ).join('');
  } catch (e) { el.innerHTML = ''; }
}

function _compareStatus(msg, isErr) {
  const s = document.getElementById('compare-status');
  if (s) s.innerHTML = `<div class="council-step${isErr ? ' council-step-err' : ''}">${escapeHTML(msg)}</div>`;
}

// ============== DOCUMENTS — writing-first editor with AI edits ==============
let _docCurrent = null;       // currently open document id
let _docSaveTimer = null;
let _docWired = false;

async function loadDocuments() {
  if (!_docWired) { _wireDocuments(); _docWired = true; }
  await _docRefreshList();
}

async function _docRefreshList() {
  const listEl = document.getElementById('doc-list');
  if (!listEl) return;
  try {
    const d = await api('/api/documents');
    const docs = d.documents || [];
    if (!docs.length) {
      listEl.innerHTML = '<div class="doc-list-empty">No documents yet.</div>';
      return;
    }
    listEl.innerHTML = docs.map(doc =>
      `<div class="doc-item${doc.id === _docCurrent ? ' active' : ''}" data-id="${doc.id}">` +
        `<div class="doc-item-title">${escapeHTML(doc.title || 'Untitled')}</div>` +
        `<div class="doc-item-meta">${doc.words} words · ${fmtDate(doc.updated_at)}</div>` +
        `<div class="doc-item-snip">${escapeHTML(doc.snippet || '')}</div>` +
      `</div>`
    ).join('');
    listEl.querySelectorAll('.doc-item').forEach(el =>
      el.addEventListener('click', () => openDoc(parseInt(el.dataset.id, 10))));
  } catch (e) { listEl.innerHTML = '<div class="doc-list-empty">Could not load documents.</div>'; }
}

function _wireDocuments() {
  document.getElementById('doc-new')?.addEventListener('click', newDoc);
  document.getElementById('doc-delete')?.addEventListener('click', deleteDoc);
  document.getElementById('doc-preview-toggle')?.addEventListener('click', toggleDocPreview);
  document.getElementById('doc-to-vault')?.addEventListener('click', exportDocToVault);

  const title = document.getElementById('doc-title');
  const body = document.getElementById('doc-body');
  title?.addEventListener('input', _docDirty);
  body?.addEventListener('input', () => { _docDirty(); _docSyncPreview(); });
  body?.addEventListener('mouseup', _docUpdateScope);
  body?.addEventListener('keyup', _docUpdateScope);

  document.querySelectorAll('.doc-ai-act').forEach(b =>
    b.addEventListener('click', () => aiEditDoc(b.dataset.preset, '')));
  const custom = document.getElementById('doc-ai-custom');
  custom?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && custom.value.trim()) { e.preventDefault(); aiEditDoc('', custom.value.trim()); }
  });
}

async function newDoc() {
  try {
    const doc = await api('/api/documents', { method: 'POST', body: { title: 'Untitled', content: '' } });
    await _docRefreshList();
    openDoc(doc.id);
  } catch (_) {}
}

async function openDoc(id) {
  try {
    const doc = await api('/api/documents/' + id);
    if (doc.error) return;
    _docCurrent = doc.id;
    document.getElementById('doc-empty').style.display = 'none';
    document.getElementById('doc-editor').style.display = 'flex';
    document.getElementById('doc-title').value = doc.title || '';
    document.getElementById('doc-body').value = doc.content || '';
    _docSyncPreview();
    _docSetSaveState('saved');
    _docUpdateScope();
    document.querySelectorAll('.doc-item').forEach(el =>
      el.classList.toggle('active', parseInt(el.dataset.id, 10) === id));
  } catch (_) {}
}

function _docDirty() {
  _docSetSaveState('unsaved');
  if (_docSaveTimer) clearTimeout(_docSaveTimer);
  _docSaveTimer = setTimeout(saveDoc, 800);  // debounced autosave
}

async function saveDoc() {
  if (_docCurrent == null) return;
  const title = document.getElementById('doc-title').value;
  const content = document.getElementById('doc-body').value;
  _docSetSaveState('saving');
  try {
    await api('/api/documents/' + _docCurrent, { method: 'PUT', body: { title, content } });
    _docSetSaveState('saved');
    _docRefreshList();
  } catch (_) { _docSetSaveState('unsaved'); }
}

async function exportDocToVault() {
  if (_docCurrent == null) return;
  await saveDoc();  // flush any pending edits first
  _docSetSaveState('exporting…');
  try {
    const res = await api(`/api/documents/${_docCurrent}/to-vault`, { method: 'POST', body: {} });
    _docSetSaveState(res.error ? ('vault: ' + res.error) : '✓ saved to vault');
  } catch (e) { _docSetSaveState('vault export failed'); }
  setTimeout(() => _docSetSaveState('saved'), 2500);
}

async function deleteDoc() {
  if (_docCurrent == null) return;
  if (!confirm('Delete this document? This cannot be undone.')) return;
  try {
    await api('/api/documents/' + _docCurrent, { method: 'DELETE' });
    _docCurrent = null;
    document.getElementById('doc-editor').style.display = 'none';
    document.getElementById('doc-empty').style.display = 'block';
    _docRefreshList();
  } catch (_) {}
}

function _docSelection() {
  const body = document.getElementById('doc-body');
  if (!body) return { text: '', whole: true, start: 0, end: 0 };
  const start = body.selectionStart, end = body.selectionEnd;
  if (end > start) return { text: body.value.slice(start, end), whole: false, start, end };
  return { text: body.value, whole: true, start: 0, end: body.value.length };
}

function _docUpdateScope() {
  const scope = document.getElementById('doc-ai-scope');
  if (!scope) return;
  const sel = _docSelection();
  scope.textContent = sel.whole ? 'whole doc' : `selection (${sel.text.length} chars)`;
}

async function aiEditDoc(preset, instruction) {
  if (_docCurrent == null) return;
  const body = document.getElementById('doc-body');
  const sel = _docSelection();
  const isContinue = preset === 'continue';
  const text = isContinue ? body.value : sel.text;
  if (!text.trim() && !isContinue) return;

  _docSetSaveState('AI working…');
  const custom = document.getElementById('doc-ai-custom');
  try {
    const res = await api('/api/documents/ai-edit', {
      method: 'POST', body: { text, preset, instruction },
    });
    if (res.error) { _docSetSaveState('AI: ' + res.error); return; }
    if (isContinue) {
      body.value = body.value + (body.value.endsWith('\n') ? '' : '\n') + res.result;
    } else if (sel.whole) {
      body.value = res.result;
    } else {
      body.value = body.value.slice(0, sel.start) + res.result + body.value.slice(sel.end);
    }
    if (custom) custom.value = '';
    _docSyncPreview();
    _docDirty();
  } catch (e) { _docSetSaveState('AI failed'); }
}

function _docSyncPreview() {
  const pv = document.getElementById('doc-preview');
  if (pv && pv.style.display !== 'none') {
    pv.innerHTML = renderMarkdown(document.getElementById('doc-body').value || '');
  }
}

function toggleDocPreview() {
  const pv = document.getElementById('doc-preview');
  const body = document.getElementById('doc-body');
  const btn = document.getElementById('doc-preview-toggle');
  if (!pv) return;
  const showing = pv.style.display !== 'none';
  if (showing) {
    pv.style.display = 'none'; body.classList.remove('split'); btn.textContent = 'Preview';
  } else {
    pv.style.display = 'block'; body.classList.add('split'); btn.textContent = 'Edit only';
    _docSyncPreview();
  }
}

function _docSetSaveState(s) {
  const el = document.getElementById('doc-save-state');
  if (el) el.textContent = s;
}

function _docSaveState() {
  return document.getElementById('doc-save-state')?.textContent || '';
}

async function _docReloadOpen() {
  if (_docCurrent == null) return;
  try {
    const doc = await api('/api/documents/' + _docCurrent);
    if (doc.error) return;
    // Only swap in if the user still has no unsaved local edits.
    if (_docSaveState() !== 'saved') return;
    document.getElementById('doc-title').value = doc.title || '';
    document.getElementById('doc-body').value = doc.content || '';
    _docSyncPreview();
  } catch (_) {}
}

// ============== THE CONSTELLATION ==============
let cstRunning = false;
let _cstPlanets = [];
let _cstChatKey = null;
let _cstChatHistory = [];

async function loadConstellation() {
  const form = document.getElementById('cst-form');
  if (form && !form._wired) {
    form._wired = true;
    form.addEventListener('submit', e => { e.preventDefault(); runConstellation(); });
    document.getElementById('cst-chat-form').addEventListener('submit', e => { e.preventDefault(); _cstSendChat(); });
    document.getElementById('cst-chat-close').addEventListener('click', _cstCloseChat);
  }
  if (!_cstPlanets.length) {
    try {
      const data = await api('/api/constellation/roster');
      _cstPlanets = data.planets || [];
      _cstRenderOrbit(_cstPlanets);
    } catch (e) { /* roster fetch failed — try again on next visit */ }
  }
}

// Each pack rides its own concentric orbital band, revolving at its own speed.
const _CST_BANDS = {
  mind:  { factor: 0.32, dur: 48 },   // inner
  life:  { factor: 0.52, dur: 72 },   // mid
  maker: { factor: 0.72, dur: 96 },   // outer
};

function _cstRenderOrbit(planets) {
  const orbit = document.getElementById('cst-orbit');
  if (!orbit) return;
  orbit.querySelectorAll('.cst-orbiter').forEach(n => n.remove());

  // Group by pack so each planet knows its index within its band.
  const byPack = {};
  planets.forEach(p => { (byPack[p.pack] = byPack[p.pack] || []).push(p); });

  planets.forEach(p => {
    const band = _CST_BANDS[p.pack] || _CST_BANDS.life;
    const siblings = byPack[p.pack];
    const m = siblings.length;
    const k = siblings.indexOf(p);

    const orbiter = document.createElement('div');
    orbiter.className = 'cst-orbiter';
    orbiter.dataset.pack = p.pack;
    orbiter.style.setProperty('--dur', band.dur + 's');
    // negative delay = pre-advanced animation → planets start spread around the band
    orbiter.style.setProperty('--phase', `calc(${band.dur}s / -${m} * ${k})`);

    const arm = document.createElement('div');
    arm.className = 'cst-arm';

    const node = document.createElement('button');
    node.type = 'button';
    node.className = `cst-planet pack-${p.pack}`;
    node.dataset.key = p.key;
    node.title = `${p.display} (${p.codename}) — ${p.domain}\nClick to chat`;
    node.innerHTML =
      `<span class="cst-glyph">${p.glyph || '✦'}</span>` +
      `<span class="cst-name">${escapeHTML(p.display)}</span>`;
    node.addEventListener('click', () => _cstOpenChat(p.key));

    arm.appendChild(node);
    orbiter.appendChild(arm);
    orbit.appendChild(orbiter);
  });

  _cstSizeOrbits();
  if (!orbit._cstResizeObs) {
    orbit._cstResizeObs = new ResizeObserver(() => _cstSizeOrbits());
    orbit._cstResizeObs.observe(orbit);
  }

  // Upgrade to the real 3D solar system when WebGL + the module are available.
  // On success the CSS orbit is hidden; on any failure it stays as the fallback.
  if (window.Cst3D && window.Cst3D.build(planets)) {
    document.querySelector('.cst-stage')?.classList.add('has-3d');
  }
}

// Radii are pixel-based so they track the square container down to mobile.
function _cstSizeOrbits() {
  const orbit = document.getElementById('cst-orbit');
  if (!orbit) return;
  const half = orbit.clientWidth / 2;
  orbit.querySelectorAll('.cst-orbiter').forEach(orbiter => {
    const band = _CST_BANDS[orbiter.dataset.pack] || _CST_BANDS.life;
    const arm = orbiter.querySelector('.cst-arm');
    if (arm) arm.style.setProperty('--orbit', (half * band.factor) + 'px');
  });
}

function _cstSetAll(state) {
  document.querySelectorAll('.cst-planet').forEach(node => {
    node.classList.remove('active', 'dim', 'thinking', 'done');
    if (state === 'dim') node.classList.add('dim');
  });
  window.Cst3D?.setAll(state);
}

async function runConstellation() {
  if (cstRunning) return;
  const q = document.getElementById('cst-question').value.trim();
  if (!q) return;
  cstRunning = true;
  document.getElementById('cst-verdict').innerHTML = '';
  document.getElementById('cst-takes').innerHTML = '';
  document.getElementById('cst-progress').innerHTML = '<div class="council-step">Routing to the right experts…</div>';
  _cstSetAll('idle');
  const btn = document.getElementById('cst-convene');
  btn.disabled = true; btn.textContent = 'Convening…';
  document.getElementById('cst-sun').classList.add('active');
  document.getElementById('cst-orbit')?.classList.add('convening');
  window.Cst3D?.setConvening(true);
  try {
    const result = await api('/api/constellation', { method: 'POST', body: { question: q } });
    if (cstRunning) _cstDone(result);  // fallback if WS event didn't arrive
  } catch (e) {
    _cstError('Request failed: ' + e.message);
  }
}

function _cstProgress(msg) {
  const progress = document.getElementById('cst-progress');
  if (progress) {
    const d = document.createElement('div');
    d.className = 'council-step';
    d.textContent = msg;
    progress.appendChild(d);
  }
}

function _cstStart(planets) {
  _cstSetAll('dim');
  window.Cst3D?.setStates((planets || []).map(p => p.key), 'thinking');
  const takes = document.getElementById('cst-takes');
  if (takes) takes.innerHTML = '';
  (planets || []).forEach(p => {
    const node = document.querySelector(`.cst-planet[data-key="${CSS.escape(p.key)}"]`);
    if (node) { node.classList.remove('dim'); node.classList.add('active', 'thinking'); }
    if (takes) {
      const card = document.createElement('div');
      card.className = `cst-take pack-${p.pack || (_cstPlanets.find(x => x.key === p.key) || {}).pack || ''}`;
      card.dataset.key = p.key;
      card.innerHTML =
        `<div class="cst-take-head"><span class="cst-take-glyph">${p.glyph || '✦'}</span>` +
        `<span>${escapeHTML(p.display)}</span>` +
        `<span class="cst-take-code">${escapeHTML(p.codename || '')}</span>` +
        `<span class="council-pill thinking">thinking…</span></div>` +
        `<div class="cst-take-body"></div>`;
      takes.appendChild(card);
    }
  });
}

function _cstAnswer(data) {
  const node = document.querySelector(`.cst-planet[data-key="${CSS.escape(data.key)}"]`);
  if (node) { node.classList.remove('thinking'); node.classList.add('done'); }
  window.Cst3D?.setState(data.key, 'done');
  const card = document.querySelector(`.cst-take[data-key="${CSS.escape(data.key)}"]`);
  if (card) {
    const pill = card.querySelector('.council-pill');
    if (pill) pill.remove();
    card.querySelector('.cst-take-body').innerHTML = renderMarkdown(data.text || '');
  }
}

function _cstDone(data) {
  if (!cstRunning) return;  // already rendered (WS + POST both fired)
  cstRunning = false;
  const btn = document.getElementById('cst-convene');
  if (btn) { btn.disabled = false; btn.textContent = 'Convene'; }
  document.getElementById('cst-sun').classList.remove('active');
  document.getElementById('cst-orbit')?.classList.remove('convening');
  window.Cst3D?.setConvening(false);

  const verdict = document.getElementById('cst-verdict');
  if (verdict) {
    const conf = (data.confidence || '').toLowerCase();
    const confNote = data.confidence_note ? ` — ${escapeHTML(data.confidence_note)}` : '';
    const confBadge = conf ? `<span class="council-confidence ${conf}">Confidence: ${conf}${confNote}</span>` : '';
    const dis = (data.disagreement || '').trim();
    const disagreed = dis && dis.toLowerCase() !== 'the council agreed';
    const disBlock = disagreed
      ? `<div class="council-disagreement"><div class="council-disagreement-head">Where the experts split</div><div class="council-disagreement-body">${renderMarkdown(dis)}</div></div>`
      : '';
    verdict.innerHTML =
      `<div class="council-verdict-head">The Sun’s verdict <span class="council-members">${(data.experts || []).map(escapeHTML).join(' · ')}</span>${confBadge}</div>` +
      `<div class="council-verdict-body">${renderMarkdown(data.final_answer || '')}</div>` +
      disBlock;
  }
  // Fallback: build take cards from POST result if the WS stream never arrived.
  const takes = document.getElementById('cst-takes');
  if (takes && !takes.children.length && (data.takes || []).length) {
    _cstStart(data.takes);
    (data.takes || []).forEach(t => _cstAnswer(t));
  }
  document.querySelectorAll('.cst-planet.thinking').forEach(n => n.classList.remove('thinking'));
  document.querySelectorAll('.cst-take .council-pill.thinking').forEach(p => {
    p.classList.remove('thinking'); p.classList.add('skipped'); p.textContent = 'no response';
  });
  window.Cst3D?.clearStates();
}

function _cstError(err) {
  cstRunning = false;
  const btn = document.getElementById('cst-convene');
  if (btn) { btn.disabled = false; btn.textContent = 'Convene'; }
  document.getElementById('cst-sun')?.classList.remove('active');
  document.getElementById('cst-orbit')?.classList.remove('convening');
  window.Cst3D?.setConvening(false);
  const progress = document.getElementById('cst-progress');
  if (progress) {
    const d = document.createElement('div');
    d.className = 'council-step council-step-err';
    d.textContent = 'Constellation failed: ' + err;
    progress.appendChild(d);
  }
}

// --- 1:1 expert chat ---
function _cstOpenChat(key) {
  const p = _cstPlanets.find(x => x.key === key);
  if (!p) return;
  window.Cst3D?.select(key);
  _cstChatKey = key;
  _cstChatHistory = [];
  document.getElementById('cst-chat-title').innerHTML =
    `${p.glyph || '✦'} ${escapeHTML(p.display)}<span class="cst-chat-sub">${escapeHTML(p.codename || '')} · ${escapeHTML(p.domain || '')}</span>`;
  document.getElementById('cst-chat-log').innerHTML =
    `<div class="cst-msg planet"><div class="cst-msg-body">I'm the ${escapeHTML(p.display)} expert. Ask me anything in my domain — I remember our past conversations.</div></div>`;
  document.getElementById('cst-chat').classList.remove('hidden');
  document.querySelectorAll('.cst-planet').forEach(n => n.classList.toggle('selected', n.dataset.key === key));
  const input = document.getElementById('cst-chat-input');
  input.value = '';
  input.focus();
  document.getElementById('cst-chat').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function _cstCloseChat() {
  document.getElementById('cst-chat').classList.add('hidden');
  document.querySelectorAll('.cst-planet.selected').forEach(n => n.classList.remove('selected'));
  window.Cst3D?.select(null);
  _cstChatKey = null;
}

// Exposed so the 3D module's raycaster can open the 1:1 chat on planet click.
window._cstOpenChat = _cstOpenChat;

async function _cstSendChat() {
  const input = document.getElementById('cst-chat-input');
  const msg = input.value.trim();
  if (!msg || !_cstChatKey) return;
  input.value = '';
  const log = document.getElementById('cst-chat-log');
  log.insertAdjacentHTML('beforeend', `<div class="cst-msg user"><div class="cst-msg-body">${escapeHTML(msg)}</div></div>`);
  const thinking = document.createElement('div');
  thinking.className = 'cst-msg planet';
  thinking.innerHTML = '<div class="cst-msg-body"><span class="council-pill thinking">thinking…</span></div>';
  log.appendChild(thinking);
  log.scrollTop = log.scrollHeight;

  const planetForCall = _cstChatKey;
  const priorHistory = _cstChatHistory.slice();  // history BEFORE this message
  _cstChatHistory.push({ role: 'user', content: msg });
  try {
    const r = await api('/api/constellation/chat', {
      method: 'POST',
      body: { planet: planetForCall, message: msg, history: priorHistory },
    });
    thinking.remove();
    const reply = r.reply || '(no reply)';
    log.insertAdjacentHTML('beforeend', `<div class="cst-msg planet"><div class="cst-msg-body">${renderMarkdown(reply)}</div></div>`);
    _cstChatHistory.push({ role: 'assistant', content: reply });
    log.scrollTop = log.scrollHeight;
  } catch (e) {
    thinking.remove();
    log.insertAdjacentHTML('beforeend', `<div class="cst-msg planet err"><div class="cst-msg-body">Failed: ${escapeHTML(e.message)}</div></div>`);
    log.scrollTop = log.scrollHeight;
  }
}

// === Boot — probe auth before starting the app ===
let _booted = false;
async function boot() {
  if (_booted) return;
  const token = getToken();
  try {
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    const r = await fetch('/api/status', { headers });
    if (r.status === 401) {
      if (token) setToken('');  // stored token is no longer valid
      showLogin(token ? 'Token invalid or changed — please re-enter.' : '');
      return;
    }
  } catch (_) { /* network error — proceed anyway */ }
  _booted = true;
  hideLogin();
  refreshStatus();
  setInterval(refreshStatus, 5000);
  connectWS();
  loadTab('overview');
}
boot();

// ========================== PWA: service worker + install ==========================
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((e) => console.warn('SW register failed', e));
  });
}

let _deferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  _deferredInstallPrompt = e;
  if (localStorage.getItem('apex_install_dismissed') === '1') return;
  document.getElementById('install-banner')?.classList.add('show');
});
document.getElementById('install-yes')?.addEventListener('click', async () => {
  document.getElementById('install-banner')?.classList.remove('show');
  if (!_deferredInstallPrompt) return;
  _deferredInstallPrompt.prompt();
  try { await _deferredInstallPrompt.userChoice; } catch (_) {}
  _deferredInstallPrompt = null;
});
document.getElementById('install-no')?.addEventListener('click', () => {
  document.getElementById('install-banner')?.classList.remove('show');
  localStorage.setItem('apex_install_dismissed', '1');
});
window.addEventListener('appinstalled', () => {
  document.getElementById('install-banner')?.classList.remove('show');
});

// ========================== Notifications: in-app toast + Web Push ==========================
const NOTIFY_ICONS = {
  guardian: '🛡️', timecapsule: '🕰️', briefing: '☀️', schedule: '⏰', info: '🔔',
};
function showNotifyToast(msg) {
  let wrap = document.getElementById('notify-toasts');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'notify-toasts';
    document.body.appendChild(wrap);
  }
  const el = document.createElement('div');
  el.className = 'notify-toast' + (msg.priority === 'high' ? ' high' : '');
  const icon = NOTIFY_ICONS[msg.kind] || NOTIFY_ICONS.info;
  el.innerHTML =
    `<span class="nt-icon">${icon}</span>` +
    `<div class="nt-body"><div class="nt-title">${escapeHTML(msg.title || 'Apex')}</div>` +
    `<div class="nt-text">${escapeHTML(msg.body || '')}</div></div>`;
  el.addEventListener('click', () => {
    if (msg.url && msg.url !== '/') {
      try {
        const u = new URL(msg.url, location.origin);
        const tab = new URLSearchParams(u.search).get('tab');
        if (tab) document.querySelector(`.nav-btn[data-tab="${tab}"]`)?.click();
      } catch (_) {}
    }
    el.remove();
  });
  wrap.appendChild(el);
  setTimeout(() => { el.classList.add('fade'); setTimeout(() => el.remove(), 400); },
    msg.priority === 'high' ? 12000 : 7000);
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function refreshPushState() {
  const btn = document.getElementById('push-enable-btn');
  const status = document.getElementById('push-status');
  if (!btn) return;
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    btn.disabled = true; if (status) status.textContent = 'Not supported on this browser.';
    return;
  }
  try {
    const { enabled } = await api('/api/push/vapid');
    if (!enabled) { btn.disabled = true; if (status) status.textContent = 'Server has no VAPID keys — run scripts/gen_vapid_keys.py.'; return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) { btn.textContent = 'Disable notifications'; btn.dataset.on = '1'; if (status) status.textContent = 'This device will receive Apex notifications.'; }
    else { btn.textContent = 'Enable notifications'; btn.dataset.on = ''; if (status) status.textContent = 'Get Guardian, Time Capsule and briefings on this device.'; }
  } catch (e) { if (status) status.textContent = 'Push unavailable: ' + e.message; }
}

async function togglePush() {
  const btn = document.getElementById('push-enable-btn');
  if (!btn) return;
  btn.disabled = true;
  try {
    const reg = await navigator.serviceWorker.ready;
    if (btn.dataset.on === '1') {
      const sub = await reg.pushManager.getSubscription();
      if (sub) { await api('/api/push/unsubscribe', { method: 'POST', body: { endpoint: sub.endpoint } }); await sub.unsubscribe(); }
    } else {
      const { publicKey } = await api('/api/push/vapid');
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') { alert('Notification permission denied.'); return; }
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
      await api('/api/push/subscribe', { method: 'POST', body: { subscription: sub.toJSON(), device_label: deviceLabel(), device_id: getDeviceId() } });
    }
  } catch (e) { alert('Could not change notifications: ' + e.message); }
  finally { btn.disabled = false; refreshPushState(); }
}
document.getElementById('push-enable-btn')?.addEventListener('click', togglePush);
document.getElementById('push-test-btn')?.addEventListener('click', () => api('/api/push/test', { method: 'POST' }));
setTimeout(refreshPushState, 1200);

// Returning focus to this device marks it active (so normal nudges route here).
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && ws && ws.readyState === 1) { try { ws.send('ping'); } catch (_) {} }
});

// ========================== Devices panel (pairing + presence) ==========================
async function loadDevices() {
  const listEl = document.getElementById('devices-list');
  if (!listEl) return;
  try {
    const d = await api('/api/devices');
    if (!d.devices || d.devices.length === 0) {
      listEl.innerHTML = '<div class="guardian-empty">No devices yet — scan the code to add your phone.</div>';
    } else {
      const KIND = { pwa: '📱', web: '🖥️', extension: '🧩' };
      listEl.innerHTML = d.devices.map((dev) => {
        const ico = KIND[dev.kind] || '🖥️';
        const dot = dev.online ? '<span class="dev-dot on"></span>' : '<span class="dev-dot"></span>';
        const me = dev.device_id === getDeviceId() ? ' <span class="dev-me">this device</span>' : '';
        return `<div class="dev-row">${dot}<span class="dev-ico">${ico}</span>` +
          `<span class="dev-label">${escapeHTML(dev.label || dev.kind)}${me}</span></div>`;
      }).join('');
    }
  } catch (_) {}
  const qr = document.getElementById('pair-qr');
  if (qr && !qr.src) qr.src = '/api/pair/qr?ts=' + Date.now();
}
document.getElementById('pair-show-btn')?.addEventListener('click', () => {
  document.getElementById('pair-box')?.classList.toggle('show');
});

// ============== ACCESS TOKENS (per-device, revocable) ==============
async function loadTokens() {
  const listEl = document.getElementById('tokens-list');
  if (!listEl) return;
  try {
    const d = await api('/api/auth/tokens');
    const toks = d.tokens || [];
    if (!toks.length) {
      listEl.innerHTML = '<div class="guardian-empty">No device tokens yet — mint one to give a device its own revocable key.</div>';
      return;
    }
    listEl.innerHTML = toks.map(t => {
      const used = t.last_used ? 'last used ' + fmtDate(t.last_used) : 'never used';
      const state = t.revoked
        ? '<span class="tok-revoked">revoked</span>'
        : `<button class="tok-revoke" data-id="${t.id}">Revoke</button>`;
      return `<div class="dev-row tok-row${t.revoked ? ' tok-row-off' : ''}">` +
        `<span class="dev-ico">🔑</span>` +
        `<span class="dev-label">${escapeHTML(t.label || 'device')}<span class="tok-meta"> · ${used}</span></span>` +
        state + `</div>`;
    }).join('');
    listEl.querySelectorAll('.tok-revoke').forEach(b =>
      b.addEventListener('click', () => revokeToken(b.dataset.id)));
  } catch (e) {
    // 403 → not the master token; hide the management panel entirely.
    const panel = document.getElementById('access-panel');
    if (panel) panel.style.display = 'none';
  }
}

async function createToken() {
  const labelEl = document.getElementById('token-label');
  const label = (labelEl?.value || '').trim();
  const reveal = document.getElementById('token-reveal');
  try {
    const res = await api('/api/auth/tokens', { method: 'POST', body: { label } });
    if (res.error) { reveal.innerHTML = `<div class="tok-err">${escapeHTML(res.error)}</div>`; return; }
    reveal.innerHTML =
      `<div class="tok-card"><div class="tok-card-head">New token — copy it now, it won't be shown again</div>` +
      `<code class="tok-value">${escapeHTML(res.token)}</code>` +
      `<button class="ghost-btn tok-copy">Copy</button>` +
      `<div class="tok-hint">On the new device, open <code>${escapeHTML(res.pair_url)}</code> or paste the token into the login screen.</div></div>`;
    reveal.querySelector('.tok-copy')?.addEventListener('click', () => {
      navigator.clipboard?.writeText(res.token);
      reveal.querySelector('.tok-copy').textContent = 'Copied ✓';
    });
    if (labelEl) labelEl.value = '';
    document.getElementById('token-new-box').style.display = 'none';
    loadTokens();
  } catch (e) { reveal.innerHTML = `<div class="tok-err">Failed: ${escapeHTML(e.message)}</div>`; }
}

async function revokeToken(id) {
  try {
    await api(`/api/auth/tokens/${id}/revoke`, { method: 'POST', body: {} });
    loadTokens();
  } catch (_) {}
}

document.getElementById('token-new-btn')?.addEventListener('click', () => {
  const box = document.getElementById('token-new-box');
  if (box) box.style.display = box.style.display === 'none' ? 'flex' : 'none';
});
document.getElementById('token-create')?.addEventListener('click', createToken);


// ========================== VISION / CAMERA ==========================

let _cameraFeedInterval = null;
let _apexAvatarRaf = null;
let _apexState = 'idle'; // idle | thinking | speaking

function _setApexState(state) {
  _apexState = state;
  window.__apexState = state;   // consumed by the 3D avatar module
  const pill = document.getElementById('apex-state-pill');
  if (pill) {
    pill.textContent = state;
    pill.className = 'apex-state-pill apex-state-' + state;
  }
}

// Avatar priority chain:
//   1. Custom portrait image (dashboard/static/apex/apex.png|jpg) — a living,
//      voice-reactive render of Apex (the user's own art)
//   2. Ready Player Me 3D head (three.js)
//   3. 2D canvas face (always works, no network)
let _avatarChosen = false;
const _PORTRAIT_VIDEOS = ['/static/apex/apex.mp4', '/static/apex/apex.webm'];
const _PORTRAIT_SRCS   = ['/static/apex/apex.png',  '/static/apex/apex.jpg', '/static/apex/apex.webp'];

function _ensureApexFace() {
  if (_avatarChosen) return;
  _tryVideo(0);
}

// Check for a looping video first — more alive than a still image.
function _tryVideo(i) {
  if (_avatarChosen) return;
  if (i >= _PORTRAIT_VIDEOS.length) return _tryPortrait(0);
  const src = _PORTRAIT_VIDEOS[i] + '?cb=' + Date.now();
  const v = document.createElement('video');
  v.preload = 'metadata';
  v.onloadedmetadata = () => { if (!_avatarChosen) _enableVideo(_PORTRAIT_VIDEOS[i]); };
  v.onerror = () => _tryVideo(i + 1);
  v.src = src;
}

function _enableVideo(src) {
  _avatarChosen = true;
  const stage = document.getElementById('apex-portrait');
  const vid = document.getElementById('apex-portrait-video');
  const img = document.getElementById('apex-portrait-img');
  const d3 = document.getElementById('apex-avatar-3d');
  const c2 = document.getElementById('apex-avatar');
  if (d3) d3.style.display = 'none';
  if (c2) c2.style.display = 'none';
  if (img) img.style.display = 'none';
  if (vid) { vid.src = src; vid.style.display = 'block'; }
  if (stage) stage.style.display = 'flex';
  _startPortraitLoop(vid);
}

function _tryPortrait(i) {
  if (_avatarChosen) return;
  if (i >= _PORTRAIT_SRCS.length) return _try3DFace();   // no portrait → 3D
  const probe = new Image();
  probe.onload = () => { if (!_avatarChosen) _enablePortrait(_PORTRAIT_SRCS[i]); };
  probe.onerror = () => _tryPortrait(i + 1);
  probe.src = _PORTRAIT_SRCS[i] + '?cb=' + Date.now();
}

function _enablePortrait(src) {
  _avatarChosen = true;
  const stage = document.getElementById('apex-portrait');
  const img = document.getElementById('apex-portrait-img');
  const d3 = document.getElementById('apex-avatar-3d');
  const c2 = document.getElementById('apex-avatar');
  if (d3) d3.style.display = 'none';
  if (c2) c2.style.display = 'none';
  if (img) img.src = src;
  if (stage) stage.style.display = 'flex';
  _startPortraitLoop(null);
}

// Voice-reactive "living portrait": the aura behind Apex swells and brightens
// with its real speaking amplitude; the image/video breathes via CSS + filter.
let _portraitCur = 0;
function _startPortraitLoop(videoEl) {
  const aura  = document.getElementById('apex-portrait-aura');
  const img   = document.getElementById('apex-portrait-img');
  const stage = document.getElementById('apex-portrait');
  const vid   = videoEl || null;
  let last = null;
  function loop(ts) {
    if (!stage || stage.style.display === 'none') { requestAnimationFrame(loop); return; }
    const dt = last === null ? 0 : Math.min(0.05, (ts - last) / 1000);
    last = ts;
    const t = ts / 1000;
    const st = window.__apexState || 'idle';
    let target;
    if (st === 'speaking') {
      target = window.__apexAudioActive
        ? (window.__apexMouth || 0)
        : 0.3 + 0.35 * Math.abs(Math.sin(t * 8));
    } else if (st === 'thinking') {
      target = 0.12 + 0.08 * Math.sin(t * 3);
    } else {
      target = 0.1 + 0.05 * Math.sin(t * 1.1);
    }
    _portraitCur += (target - _portraitCur) * Math.min(1, dt * 16 || 0.3);
    if (aura) {
      aura.style.opacity = (0.28 + 0.62 * _portraitCur).toFixed(3);
      aura.style.transform = `translate(-50%,-50%) scale(${(1 + 0.22 * _portraitCur).toFixed(3)})`;
    }
    const filterVal = `brightness(${(1 + 0.18 * _portraitCur).toFixed(3)}) saturate(${(1 + 0.25 * _portraitCur).toFixed(3)})`;
    if (vid) {
      vid.style.filter = filterVal;
      vid.classList.toggle('apex-portrait-speaking', st === 'speaking');
    } else if (img) {
      img.style.filter = filterVal;
      img.classList.toggle('apex-portrait-speaking', st === 'speaking');
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
}

// Start the 3D Ready Player Me face; fall back to the 2D canvas if three.js
// or the avatar GLB fails to load.
let _avatar2dStarted = false;
function _try3DFace() {
  if (_avatarChosen) return;
  _avatarChosen = true;
  const d3 = document.getElementById('apex-avatar-3d');
  if (d3) d3.style.display = 'flex';
  let tries = 0;
  const tick = () => {
    if (window.ApexAvatar && !window.ApexAvatar.failed) {
      try { window.ApexAvatar.start(); } catch (e) {}
      if (window.ApexAvatar.ready) return;
    }
    if (window.ApexAvatar?.failed) return _start2DFallback();
    if (++tries < 50) setTimeout(tick, 150);
    else _start2DFallback();   // module never loaded (offline, blocked CDN)
  };
  tick();
}
function _start2DFallback() {
  if (_avatar2dStarted) return;
  _avatar2dStarted = true;
  const d3 = document.getElementById('apex-avatar-3d');
  const c2 = document.getElementById('apex-avatar');
  if (d3) d3.style.display = 'none';
  if (c2) c2.style.display = 'block';
  _startApexAvatar();
}

// ── Vision panel state ──
let _visionRefreshInterval = null;
let _visionCostChart = null;
let _visionSkillsChart = null;
let _visionInited = false;

async function loadVision() {
  if (!_visionInited) {
    _visionInited = true;
    _wireVisionCamera();
    _ensureApexFace();
  }
  await _refreshVisionData();
  if (!_visionRefreshInterval)
    _visionRefreshInterval = setInterval(_refreshVisionData, 30_000);
}

function _teardownVision() {
  if (_visionRefreshInterval) { clearInterval(_visionRefreshInterval); _visionRefreshInterval = null; }
  _stopCameraFeed();
}

async function _wireVisionCamera() {
  let data;
  try { data = await api('/api/camera/status'); } catch (e) { return; }
  const toggle = document.getElementById('camera-toggle');
  const hint   = document.getElementById('camera-install-hint');
  if (hint) hint.style.display = data.cv2_available ? 'none' : 'block';
  if (toggle && !toggle._wired) {
    toggle._wired = true;
    toggle.addEventListener('change', async () => {
      try {
        const r = await api('/api/camera/toggle', { method: 'POST', body: { enabled: toggle.checked } });
        _updateCameraStatus(r.enabled);
        if (r.enabled) _startCameraFeed(); else _stopCameraFeed();
      } catch (e) { toggle.checked = !toggle.checked; }
    });
  }
  if (toggle) toggle.checked = data.enabled;
  _updateCameraStatus(data.enabled);
  if (data.enabled) _startCameraFeed();
}

async function _refreshVisionData() {
  const settled = await Promise.allSettled([
    api('/api/goals?active_only=true'),
    api('/api/memories?limit=8'),
    api('/api/telemetry?days=7'),
    api('/api/reflections?status=pending&limit=3'),
    api('/api/outcomes/skills?days=7'),
    api('/api/feedback/summary?days=7'),
  ]);
  const [goals, mems, tel, refl, skillOutcomes, fbSummary] = settled.map(p => p.status === 'fulfilled' ? p.value : null);
  _renderVpGoals(goals);
  _renderVpMemories(mems);
  _renderVpTelemetry(tel, fbSummary);
  _renderVpInsights(refl);
  _renderVpSkills(skillOutcomes);
}

function _setVpEl(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function _renderVpGoals(goals) {
  const countEl = document.getElementById('vp-goals-count');
  const listEl  = document.getElementById('vp-goals-list');
  if (!listEl) return;
  const items = Array.isArray(goals) ? goals : (goals?.goals || []);
  if (countEl) countEl.textContent = items.length || '0';
  listEl.innerHTML = items.slice(0, 5).map(g => {
    const score = g.recent_progress?.[0]?.score;
    const bar = score != null
      ? `<div class="vp-goal-bar"><div class="vp-goal-bar-fill" style="width:${score * 10}%"></div></div>`
      : '';
    const dl = g.deadline
      ? `<span class="vp-goal-deadline">${new Date(g.deadline * 1000).toLocaleDateString([], {month:'short', day:'numeric'})}</span>`
      : '';
    return `<div class="vp-goal-item">
      <div class="vp-goal-top"><span class="badge">${escapeHTML(g.horizon || '')}</span>${dl}</div>
      <div class="vp-goal-title">${escapeHTML(g.title || '')}</div>${bar}
    </div>`;
  }).join('') || '<div class="vp-empty">No active goals — add one in the Goals tab.</div>';
}

function _renderVpMemories(mems) {
  const listEl = document.getElementById('vp-memories-list');
  if (!listEl) return;
  const items = Array.isArray(mems) ? mems : (mems?.memories || []);
  _setVpEl('vp-intel-memories', String(items.length || '—'));
  listEl.innerHTML = items.slice(0, 5).map(m => {
    const stars = '★'.repeat(Math.min(Math.round(m.importance || 0), 5));
    const text  = (m.content || '').slice(0, 80) + ((m.content || '').length > 80 ? '…' : '');
    return `<div class="vp-memory-item">
      <span class="badge">${escapeHTML(m.kind || 'note')}</span>
      <span class="vp-memory-text">${escapeHTML(text)}</span>
      <span class="vp-memory-imp">${stars}</span>
    </div>`;
  }).join('') || '<div class="vp-empty">No memories yet.</div>';
}

function _renderVpTelemetry(tel, fbSummary) {
  if (!tel) return;
  const cache = ((tel.cache_hit_rate || 0) * 100).toFixed(1) + '%';
  _setVpEl('vp-intel-cache', cache);
  _setVpEl('vp-intel-calls', fmtNum(tel.total_calls));
  _setVpEl('vp-spend-total', fmtCost(tel.total_cost_usd));

  const rate = fbSummary?.approval_rate;
  _setVpEl('vp-intel-approval', rate != null ? Math.round(rate * 100) + '%' : '—');
  const bar = document.getElementById('vp-intel-approval-bar');
  if (bar && rate != null) bar.style.width = (rate * 100) + '%';

  const ctx = document.getElementById('vp-cost-chart')?.getContext('2d');
  if (!ctx) return;
  const days = Array.isArray(tel.by_day) ? tel.by_day : [];
  if (_visionCostChart) { _visionCostChart.destroy(); _visionCostChart = null; }
  _visionCostChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: days.map(d => new Date(d.day * 1000).toLocaleDateString([], {weekday:'short'})),
      datasets: [{
        data: days.map(d => d.cost_usd || 0),
        backgroundColor: 'rgba(95,216,255,0.35)',
        borderColor: 'rgba(95,216,255,0.7)',
        borderWidth: 1, borderRadius: 3,
        hoverBackgroundColor: 'rgba(95,216,255,0.6)',
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => '$' + (c.raw || 0).toFixed(4) } } },
      scales: {
        x: { ticks: { color: '#5e6878', font: { size: 10 } }, grid: { display: false } },
        y: { display: false, beginAtZero: true },
      },
    },
  });
}

function _renderVpInsights(refl) {
  const listEl  = document.getElementById('vp-insights-list');
  const countEl = document.getElementById('vp-pending-count');
  if (!listEl) return;
  const items = Array.isArray(refl) ? refl : (refl?.reflections || []);
  if (countEl) countEl.textContent = items.length ? items.length + ' pending' : '';
  listEl.innerHTML = items.slice(0, 3).map(r => {
    const text = (r.content || '').slice(0, 90) + ((r.content || '').length > 90 ? '…' : '');
    const conf = Math.round((r.confidence || 0) * 100);
    return `<div class="vp-insight-item">
      <span class="badge">${escapeHTML(r.kind || 'insight')}</span>
      <div class="vp-insight-text">${escapeHTML(text)}</div>
      <div class="vp-conf-bar"><div class="vp-conf-bar-fill" style="width:${conf}%"></div></div>
    </div>`;
  }).join('') || '<div class="vp-empty">No pending insights.</div>';
}

function _renderVpSkills(skillOutcomes) {
  const ctx   = document.getElementById('vp-skills-chart')?.getContext('2d');
  const legEl = document.getElementById('vp-skills-legend');
  if (!ctx) return;
  const items = Array.isArray(skillOutcomes) ? skillOutcomes : (skillOutcomes?.skills || []);
  const top = items.slice(0, 5);
  if (!top.length) { if (legEl) legEl.innerHTML = '<div class="vp-empty">No skill data yet.</div>'; return; }
  const COLORS = ['#5fd8ff', '#8a7cff', '#3ddc97', '#ffb547', '#ff6a6a'];
  if (_visionSkillsChart) { _visionSkillsChart.destroy(); _visionSkillsChart = null; }
  _visionSkillsChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: top.map(s => s.name),
      datasets: [{ data: top.map(s => s.total_runs || 1), backgroundColor: COLORS, borderWidth: 0, hoverOffset: 4 }],
    },
    options: {
      responsive: false, cutout: '68%',
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.label}: ${c.raw} runs` } } },
    },
  });
  if (legEl) {
    legEl.innerHTML = top.map((s, i) => {
      const rate = s.approval_rate != null ? Math.round(s.approval_rate * 100) + '%' : '—';
      return `<div class="vp-skill-row">
        <span class="vp-skill-dot" style="background:${COLORS[i]}"></span>
        <span class="vp-skill-name">${escapeHTML(s.name)}</span>
        <span class="vp-skill-rate">${rate}</span>
      </div>`;
    }).join('');
  }
}

function _updateCameraStatus(enabled) {
  const el = document.getElementById('camera-status-text');
  if (el) el.textContent = enabled ? 'Camera on' : 'Camera off';
}

function _startCameraFeed() {
  _stopCameraFeed();
  _fetchCameraFrame();
  _cameraFeedInterval = setInterval(_fetchCameraFrame, 2000);
}

function _stopCameraFeed() {
  if (_cameraFeedInterval) { clearInterval(_cameraFeedInterval); _cameraFeedInterval = null; }
  const img = document.getElementById('webcam-img');
  const ph  = document.getElementById('webcam-placeholder');
  if (img) img.style.display = 'none';
  if (ph)  ph.style.display  = 'flex';
  const meta = document.getElementById('webcam-meta');
  if (meta) meta.textContent = '';
}

async function _fetchCameraFrame() {
  try {
    const data = await api('/api/camera/frame');
    if (!data.ok) return;
    const img = document.getElementById('webcam-img');
    const ph  = document.getElementById('webcam-placeholder');
    if (img) { img.src = 'data:image/jpeg;base64,' + data.image; img.style.display = 'block'; }
    if (ph)  ph.style.display = 'none';
    const meta = document.getElementById('webcam-meta');
    if (meta) meta.textContent = data.width + '\xd7' + data.height + ' \xb7 live';
  } catch (e) {}
}

// ---------- Apex avatar: expressive talking face + lip-sync ----------

// Mouth openness, 0 (closed) .. 1 (wide). Driven by real audio amplitude
// when Apex speaks through TTS; falls back to a procedural talk cycle.
let _apexMouth = 0;          // target
let _apexMouthRender = 0;    // smoothed value the renderer reads
let _audioCtx = null;
let _lipSyncActive = false;
let _lipSyncRaf = null;

function _ensureAudioCtx() {
  if (!_audioCtx) {
    try { _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch (e) { _audioCtx = null; }
  }
  if (_audioCtx && _audioCtx.state === 'suspended') _audioCtx.resume().catch(() => {});
  return _audioCtx;
}

// Route a playing <audio> element through an analyser and drive _apexMouth
// from its live loudness. Returns true if wired successfully.
function _attachLipSync(audioEl) {
  const ctx = _ensureAudioCtx();
  if (!ctx) return false;
  let analyser;
  try {
    const src = ctx.createMediaElementSource(audioEl);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    src.connect(analyser);
    analyser.connect(ctx.destination);
  } catch (e) { return false; }

  const buf = new Uint8Array(analyser.frequencyBinCount);
  _lipSyncActive = true;
  window.__apexAudioActive = true;

  function sample() {
    if (!_lipSyncActive) return;
    analyser.getByteTimeDomainData(buf);
    // RMS around the 128 midpoint → 0..~1 loudness
    let sum = 0;
    for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
    const rms = Math.sqrt(sum / buf.length);
    _apexMouth = Math.min(1, rms * 3.2);   // scale up — speech RMS is small
    window.__apexMouth = _apexMouth;       // consumed by 2D + 3D avatars
    _lipSyncRaf = requestAnimationFrame(sample);
  }
  sample();

  const stop = () => {
    _lipSyncActive = false;
    window.__apexAudioActive = false;
    if (_lipSyncRaf) cancelAnimationFrame(_lipSyncRaf);
    _apexMouth = 0;
    window.__apexMouth = 0;
    _setApexState('idle');
  };
  audioEl.addEventListener('ended', stop);
  audioEl.addEventListener('pause', stop);
  return true;
}

function _startApexAvatar() {
  const canvas = document.getElementById('apex-avatar');
  if (!canvas) return;
  if (_apexAvatarRaf) { cancelAnimationFrame(_apexAvatarRaf); _apexAvatarRaf = null; }

  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const cx = W / 2, cy = H / 2;
  let t = 0;
  let lastTs = null;
  let nextBlink = 2 + Math.random() * 3;
  let blink = 0; // 0 open .. 1 closed

  function frame(ts) {
    const dt = lastTs === null ? 0 : (ts - lastTs) / 1000;
    if (lastTs !== null) t += dt;
    lastTs = ts;
    const st = _apexState;

    // --- blink scheduling ---
    if (t > nextBlink && blink === 0) blink = 0.001;
    if (blink > 0) {
      blink += dt * 9;
      if (blink >= 2) { blink = 0; nextBlink = t + 2 + Math.random() * 4; }
    }
    const eyeOpen = blink === 0 ? 1 : Math.abs(1 - (blink > 1 ? 2 - blink : blink));

    // --- mouth target ---
    let mouthTarget;
    if (st === 'speaking') {
      // Use real audio amplitude when lip-sync is live; else procedural talk
      mouthTarget = _lipSyncActive
        ? _apexMouth
        : 0.25 + 0.45 * Math.abs(Math.sin(t * 9)) * (0.5 + 0.5 * Math.sin(t * 3.3));
    } else if (st === 'thinking') {
      mouthTarget = 0.04;
    } else {
      mouthTarget = 0.06 + 0.02 * Math.sin(t * 1.2); // gentle breathing
    }
    _apexMouthRender += (mouthTarget - _apexMouthRender) * Math.min(1, dt * 18 || 0.4);

    // gaze: drifts while thinking, centered otherwise
    const gazeX = st === 'thinking' ? 4 * Math.sin(t * 1.6) : 1.5 * Math.sin(t * 0.5);
    const gazeY = st === 'thinking' ? -2 + 1.5 * Math.cos(t * 1.1) : 0;
    const browLift = st === 'speaking' ? 2 + 2 * _apexMouthRender
                   : st === 'thinking' ? -2 : 0;

    // ===== render =====
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#05060d';
    ctx.fillRect(0, 0, W, H);

    // ambient glow behind head
    const halo = ctx.createRadialGradient(cx, cy, 30, cx, cy, 160);
    const haloI = st === 'speaking' ? 0.16 + 0.12 * _apexMouthRender : 0.10;
    halo.addColorStop(0, `rgba(95,216,255,${haloI})`);
    halo.addColorStop(1, 'rgba(95,216,255,0)');
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, W, H);

    // --- head ---
    const hw = 92, hh = 112;
    const skin = ctx.createLinearGradient(cx, cy - hh, cx, cy + hh);
    skin.addColorStop(0, '#27405c');
    skin.addColorStop(0.5, '#1b2e46');
    skin.addColorStop(1, '#0f1c2e');
    _ellipse(ctx, cx, cy + 4, hw, hh, skin);
    // rim light
    ctx.save();
    _ellipsePath(ctx, cx, cy + 4, hw, hh);
    ctx.clip();
    const rim = ctx.createLinearGradient(cx - hw, cy, cx + hw, cy);
    rim.addColorStop(0, 'rgba(95,216,255,0.22)');
    rim.addColorStop(0.5, 'rgba(95,216,255,0)');
    rim.addColorStop(1, 'rgba(160,120,255,0.18)');
    ctx.fillStyle = rim;
    ctx.fillRect(cx - hw, cy - hh, hw * 2, hh * 2);
    ctx.restore();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = 'rgba(95,216,255,0.35)';
    _ellipsePath(ctx, cx, cy + 4, hw, hh); ctx.stroke();

    // --- eyebrows ---
    const eyeY = cy - 18, eyeDX = 34;
    ctx.strokeStyle = 'rgba(140,200,255,0.7)';
    ctx.lineWidth = 4; ctx.lineCap = 'round';
    for (const s of [-1, 1]) {
      const ex = cx + s * eyeDX;
      ctx.beginPath();
      ctx.moveTo(ex - 15, eyeY - 20 - browLift);
      ctx.quadraticCurveTo(ex, eyeY - 26 - browLift, ex + 15, eyeY - 20 - browLift + 1);
      ctx.stroke();
    }

    // --- eyes ---
    for (const s of [-1, 1]) {
      const ex = cx + s * eyeDX;
      // sclera
      ctx.save();
      ctx.translate(ex, eyeY);
      ctx.scale(1, Math.max(0.06, eyeOpen));
      _ellipse(ctx, 0, 0, 18, 13, 'rgba(225,240,255,0.92)');
      ctx.restore();
      if (eyeOpen > 0.25) {
        // iris + pupil
        const ix = ex + gazeX, iy = eyeY + gazeY;
        const irisGrad = ctx.createRadialGradient(ix, iy, 1, ix, iy, 9);
        irisGrad.addColorStop(0, '#7fe3ff');
        irisGrad.addColorStop(1, '#1f86b8');
        _ellipse(ctx, ix, iy, 9, 9, irisGrad);
        _ellipse(ctx, ix, iy, 4, 4, '#06121d');
        _ellipse(ctx, ix - 2.5, iy - 2.5, 2, 2, 'rgba(255,255,255,0.85)');
      }
      // upper lid shadow
      ctx.strokeStyle = 'rgba(10,20,34,0.6)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.ellipse(ex, eyeY, 18, 13 * Math.max(0.06, eyeOpen), 0, Math.PI, Math.PI * 2);
      ctx.stroke();
    }

    // --- nose ---
    ctx.strokeStyle = 'rgba(95,216,255,0.18)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx - 1, eyeY + 12);
    ctx.lineTo(cx - 6, cy + 22);
    ctx.quadraticCurveTo(cx, cy + 28, cx + 6, cy + 22);
    ctx.stroke();

    // --- mouth (lip-synced) ---
    const my = cy + 50;
    const open = _apexMouthRender;            // 0..1
    const mW = 26 + 6 * open;
    const mH = 2 + 26 * open;
    // lips fill
    const lipGrad = ctx.createLinearGradient(cx, my - mH, cx, my + mH);
    lipGrad.addColorStop(0, '#3a1320');
    lipGrad.addColorStop(1, '#7a2438');
    _ellipse(ctx, cx, my, mW, mH, lipGrad);
    // inner mouth / dark cavity when open
    if (open > 0.12) {
      _ellipse(ctx, cx, my + mH * 0.18, mW * 0.78, mH * 0.7, '#160206');
      // teeth hint at top when wide
      if (open > 0.35) _ellipse(ctx, cx, my - mH * 0.42, mW * 0.6, mH * 0.18, 'rgba(240,245,255,0.85)');
    }
    // lip outline
    ctx.strokeStyle = 'rgba(95,216,255,0.30)';
    ctx.lineWidth = 1.4;
    _ellipsePath(ctx, cx, my, mW, mH); ctx.stroke();

    // thinking scan-line shimmer
    if (st === 'thinking') {
      const sy = cy - hh + ((t * 70) % (hh * 2));
      ctx.save();
      _ellipsePath(ctx, cx, cy + 4, hw, hh); ctx.clip();
      ctx.strokeStyle = 'rgba(95,216,255,0.08)';
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(cx - hw, sy); ctx.lineTo(cx + hw, sy); ctx.stroke();
      ctx.restore();
    }

    _apexAvatarRaf = requestAnimationFrame(frame);
  }

  _apexAvatarRaf = requestAnimationFrame(frame);
}

function _ellipsePath(ctx, x, y, rx, ry) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
}
function _ellipse(ctx, x, y, rx, ry, fill) {
  _ellipsePath(ctx, x, y, rx, ry);
  ctx.fillStyle = fill;
  ctx.fill();
}

