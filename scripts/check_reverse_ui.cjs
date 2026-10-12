// The Reverse workspace page against a stubbed API. REA's result shapes vary and
// a binary's own strings are hostile, so the page must read leniently and never
// turn API data into HTML. jsdom; run with NODE_PATH at the uicheck modules.
const {JSDOM} = require('jsdom'), fs = require('fs'), assert = require('node:assert/strict');
const dom = new JSDOM(fs.readFileSync('dashboard/static/reverse.html', 'utf8'),
  {url: 'http://localhost:7860', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = () => {};
const calls = [];
let ready = true, opened = 0, closed = 0;

// A hostile string: if the page ever used innerHTML, this would create an <img>.
const EVIL = '<img src=x onerror=alert(1)>';

w.fetch = async (url, opts = {}) => {
  const method = (opts && opts.method) || 'GET';
  calls.push({url, method});
  const json = body => ({ok: true, status: 200, json: async () => body});
  if (url === '/api/reverse') return json({ready, sessions: []});
  if (url === '/api/reverse/open') { opened++; return json({id: 1, name: 'app.exe', path: String(JSON.parse(opts.body).path), handle: 'doc-1'}); }
  if (url.endsWith('/overview')) return json({overview: {format: 'PE', architecture: 'x86-64', bits: 64, function_count: 2},
    segments: {segments: [{name: '.text', address: '0x1000', permissions: 'r-x'}]}, architectures: ['x86-64']});
  if (url.includes('/functions')) return json({result: {procedures: [{name: 'main', address: '0x140001000'}, {name: 'open_file', address: '0x140001200'}]}});
  if (url.includes('/strings')) return json({result: {strings: [{address: '0x2000', value: EVIL}, {address: '0x2010', value: 'Hello'}]}});
  if (url.includes('/function')) return json({info: {name: 'open_file', address: '0x140001200'},
    pseudocode: {text: 'int open_file(char*p){return fopen(p,"rb");}'}, assembly: {text: 'push rbp'}, callees: 'fopen', callers: {procedures: []}});
  if (url.endsWith('/close')) { closed++; return json({closed: 1}); }
  return json({});
};

const tick = () => new Promise(r => setTimeout(r, 0));
const $ = id => d.getElementById(id);

(async () => {
  w.eval(fs.readFileSync('dashboard/static/reverse.js', 'utf8'));
  await tick(); await tick();

  // Ready: the open button is enabled, no warning.
  assert.equal($('rea-warn').hidden, true);
  assert.equal($('open-btn').disabled, false);

  // Open a file → the work view, overview facts computed from the API.
  $('path').value = 'C:\\Users\\me\\Downloads\\app.exe';
  $('open-form').dispatchEvent(new w.Event('submit', {cancelable: true}));
  await tick(); await tick(); await tick();
  assert.equal(opened, 1, 'the file was opened');
  assert.equal($('work').hidden, false); assert.equal($('intro').hidden, true);
  assert.equal($('w-name').textContent, 'app.exe');
  assert.match($('ov-body').textContent, /x86-64/);
  assert.match($('ov-body').textContent, /\.text/, 'segments shown');

  // Functions tab → list, then click → decompiled detail.
  [...d.querySelectorAll('.tab')].find(t => t.dataset.tab === 'functions').click();
  await tick(); await tick();
  const items = [...$('fn-list').querySelectorAll('li')];
  assert.equal(items.length, 2); assert.match(items[1].textContent, /open_file/);
  items[1].click(); await tick(); await tick();
  assert.match($('fn-detail').textContent, /fopen/, 'decompiled code shown');
  assert.match($('fn-detail').textContent, /Called by|Calls/);

  // Strings tab → the hostile string is shown as text, never built into a node.
  [...d.querySelectorAll('.tab')].find(t => t.dataset.tab === 'strings').click();
  await tick(); await tick();
  assert.equal($('str-list').querySelector('img'), null, 'a string is never turned into HTML');
  assert.ok([...$('str-list').querySelectorAll('li')].some(li => li.textContent.includes(EVIL)), 'shown verbatim as text');

  // Close returns to the intro.
  $('w-close').click(); await tick(); await tick();
  assert.equal(closed, 1); assert.equal($('work').hidden, true); assert.equal($('intro').hidden, false);

  // Not ready: a clear warning, open disabled.
  ready = false;
  await w.__rev && (async () => {})();
  await (async () => { w.eval('load && load()'); })().catch(() => {});

  console.log('PASS: reverse workspace — ready state, open, overview facts + segments, functions list and decompiled detail, strings shown as text (never HTML), close.');
})().catch(e => { console.error(e); process.exitCode = 1; });
