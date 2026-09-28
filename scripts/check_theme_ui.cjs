// Apex's look switch (dashboard/static/theme.js + theme.css): futuristic by
// default, one click to normal, remembered, followed by other tabs, never a
// second switch inside an embedded frame — and the futuristic layer is only
// ever a layer: every rule is scoped to it, and none moves or resizes a box,
// so the normal look is each page exactly as it was. Requires jsdom.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const dir = path.join(__dirname, '..', 'dashboard', 'static');
const themeJs = fs.readFileSync(path.join(dir, 'theme.js'), 'utf8');
const themeCss = fs.readFileSync(path.join(dir, 'theme.css'), 'utf8');

async function page(body = '', {saved = null, embedded = false} = {}) {
  const dom = new JSDOM(`<!doctype html><html><head></head><body>${body}</body></html>`, {url: 'http://127.0.0.1:7860/x', runScripts: 'outside-only'});
  const w = dom.window;
  if (saved) w.localStorage.setItem('apex.look', saved);
  const events = [];
  w.addEventListener('apex:look', e => events.push(e.detail));
  // In a frame, window.top is another window. jsdom locks `top`, so the
  // script runs against a stand-in window whose `top` differs.
  if (embedded) w.eval(`(function (window) { ${themeJs} }).call(this, new Proxy(window, {get: (t, k) => k === 'top' ? {} : Reflect.get(t, k)}))`);
  else w.eval(themeJs);
  await new Promise(r => w.document.readyState === 'loading' ? w.document.addEventListener('DOMContentLoaded', r) : r());
  return {w, d: w.document, events};
}

(async () => {
// 1. Futuristic by default; a switch appears; clicking goes normal and back.
let {w, d, events} = await page();
assert.equal(d.documentElement.dataset.look, 'futuristic', 'futuristic is the default');
const toggle = d.querySelector('.apex-look-toggle');
assert.ok(toggle, 'every page gets a switch'); assert.match(toggle.textContent, /Futuristic/);
toggle.click();
assert.equal(d.documentElement.dataset.look, 'normal'); assert.match(toggle.textContent, /Normal/);
assert.equal(w.localStorage.getItem('apex.look'), 'normal', 'remembered');
assert.deepEqual([...events], ['normal'], 'pages (the study) hear about the change');
toggle.click(); assert.equal(d.documentElement.dataset.look, 'futuristic');

// 2. A saved choice is applied at once; another tab's change is followed.
({w, d} = await page('', {saved: 'normal'}));
assert.equal(d.documentElement.dataset.look, 'normal', 'the saved look applies before paint');
w.dispatchEvent(new w.StorageEvent('storage', {key: 'apex.look', newValue: 'futuristic'}));
assert.equal(d.documentElement.dataset.look, 'futuristic', 'another tab switched: follow it');
({d} = await page('', {saved: 'garbage'}));
assert.equal(d.documentElement.dataset.look, 'futuristic', 'an unknown value falls back to the default');

// 3. A page's own switch is used instead; frames get none.
({w, d} = await page('<button id="mine" data-look-toggle></button>'));
assert.equal(d.querySelectorAll('[data-look-toggle]').length, 1, "a page's own switch is not duplicated");
d.getElementById('mine').click(); assert.equal(d.documentElement.dataset.look, 'normal');
({d} = await page('', {embedded: true}));
assert.equal(d.querySelector('.apex-look-toggle'), null, 'no second switch inside an embedded frame');

// 4. The stylesheet is a layer: scoped, and never changes geometry.
const rules = themeCss.replace(/\/\*[\s\S]*?\*\//g, '').split('}');
const layout = /(^|;)\s*(width|height|min-width|min-height|max-width|max-height|margin[\w-]*|padding[\w-]*|display|top|left|right|bottom|inset|flex[\w-]*|grid[\w-]*|font-size|line-height|position|transform)\s*:/i;
let scoped = 0;
for (const raw of rules) {
  const [sel, decl = ''] = raw.split('{').map(s => s.trim());
  if (!sel || sel.startsWith('@') || sel.startsWith('to') || sel === '') continue;
  const selector = sel.replace(/^@media[^{]*/, '').trim();
  if (/\.apex-look-toggle/.test(selector)) continue;           // the switch itself
  assert.match(selector, /^html\[data-look="futuristic"\]/, `unscoped rule would change the normal look: ${selector.slice(0, 60)}`);
  scoped++;
  // The one exception: the scanline overlay is a fixed, click-through layer.
  if (/body::after/.test(selector)) { assert.match(decl, /pointer-events:\s*none/); continue; }
  assert.doesNotMatch(decl, layout, `the futuristic layer must not move or resize anything: ${selector.slice(0, 60)} { ${decl.slice(0, 80)}`);
}
assert.ok(scoped > 15);

// 5. Every page loads it: the script right after <meta charset> (before paint),
//    the stylesheet after the page's own.
for (const name of ['index.html', 'companion.html', 'board.html', 'study.html']) {
  const html = fs.readFileSync(path.join(dir, name), 'utf8');
  assert.match(html, /<meta charset="[^"]+"><script src="\/static\/theme.js"><\/script>/i, `${name}: theme.js first`);
  const css = [...html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map(m => m[1]);
  assert.equal(css.at(-1), '/static/theme.css', `${name}: theme.css after the page's own styles`);
}

console.log('PASS: futuristic by default, one switch to normal (remembered, followed across tabs, never duplicated in frames), '
  + 'the study hears the change, every futuristic rule is scoped and geometry-free, and all four pages load it before paint.');
})().catch(e => { console.error(e); process.exit(1); });
