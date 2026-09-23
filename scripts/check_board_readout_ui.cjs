// DOM simulation of the board's live readout. Requires jsdom on NODE_PATH.
//
// The board's module imports three.js from a CDN, so the whole script fails to
// execute anywhere without internet — including here. Rather than stub three
// (which takes a fake that grows a method every time the page calls one), this
// evaluates the readout's own functions against the real markup. What it
// covers is the part that is pure DOM: which reason gets named, and whether a
// card title can inject markup.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'dashboard', 'static', 'board.html'), 'utf8');

// The panel markup must exist, and start hidden: it is an instrument, not
// furniture, and a permanently-visible debug panel is one people stop reading.
assert.ok(html.includes('id="diag"'), 'the readout panel is missing from board.html');
assert.match(html, /<div id="diag" hidden>/, 'the readout must start hidden');
assert.ok(html.includes('id="diag-close"'), 'no way to dismiss it');

// Pull the two functions out of the module and run them in a DOM. Extracted by
// name rather than by line number so moving them does not silently skip this.
const grab = name => {
  const start = html.indexOf(`function ${name}(`);
  assert.ok(start > -1, `${name} is gone from board.html`);
  let depth = 0, i = html.indexOf('{', start);
  const from = i;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}' && --depth === 0) break;
  }
  return html.slice(start, i + 1);
};
// esc() spans two lines and ends at `}[ch]));` — sliced to that marker rather
// than to the next semicolon, which lands inside the replacement object.
const escStart = html.indexOf('const esc =');
assert.ok(escStart > -1, 'esc() is gone from board.html');
const escEnd = html.indexOf('}[ch]));', escStart);
assert.ok(escEnd > -1, 'esc() no longer ends the way this check expects');
const escSrc = html.slice(escStart, escEnd + '}[ch]));'.length);

const dom = new JSDOM('<div id="diag"><div id="diag-body"></div></div>',
  {runScripts: 'outside-only'});
const w = dom.window;
w.eval(`${escSrc}\n${grab('whyNot')}\n${grab('renderDiag')}\nglobalThis._w = whyNot; globalThis._r = renderDiag; globalThis._e = esc;`);
const whyNot = w._w, renderDiag = w._r;

const hand = o => Object.assign(
  {label: 'Right', x: .5, y: .5, ratio: 0.83, threshold: 0.70,
   pinched: false, open_palm: false}, o);
const grabOf = o => Object.assign(
  {hand: 0, state: 'idle', pinched: false, open_palm: false, holding: null,
   nearest: 'Phone Stand', distance: 0.02, reach: 0.14, in_reach: true,
   nearest_is_full: false, dwell_needed: 0.12, arming: false}, o);

// Each cause names itself, and names the NUMBER that has to change.
assert.match(whyNot(hand({}), grabOf({})), /not pinched/);
assert.match(whyNot(hand({}), grabOf({})), /0\.830/, 'the measured ratio must be shown');
assert.match(whyNot(hand({}), grabOf({})), /0\.700/, 'the threshold must be shown');

assert.match(whyNot(hand({open_palm: true}), grabOf({})), /open palm/);
assert.match(whyNot(hand({ratio: null}), grabOf({})), /unreadable/);

const far = whyNot(hand({pinched: true, ratio: .4}),
                   grabOf({pinched: true, distance: 0.566, in_reach: false}));
assert.match(far, /0\.566/, 'the distance must be shown');
assert.match(far, /0\.14/, 'the reach must be shown alongside it');

assert.match(whyNot(hand({pinched: true, ratio: .4}),
                    grabOf({pinched: true, state: 'armed', arming: true})), /arming/);
assert.match(whyNot(hand({pinched: true, ratio: .4}),
                    grabOf({pinched: true, nearest_is_full: true})), /two hands/);
assert.match(whyNot(hand({pinched: true, ratio: .4}),
                    grabOf({pinched: true, holding: 'Phone Stand'})), /holding Phone Stand/);
assert.match(whyNot(hand({pinched: true, ratio: .4}),
                    grabOf({pinched: true, nearest: null, distance: null})),
             /nothing on the board/);
assert.match(whyNot(null, null), /no hand/);

// The hysteresis band must be visible: a hand reading 0.74 while PINCHED is
// otherwise indistinguishable from a broken threshold.
renderDiag({tracking: true, hands: [hand({pinched: true, ratio: .74, release: 0.78})],
            grabs: [grabOf({pinched: true, state: 'grabbed', holding: 'Grab Me'})]});
{
  const b = dom.window.document.getElementById('diag-body');
  assert.match(b.textContent, /held until above/, 'the release threshold is not shown');
  assert.match(b.textContent, /0\.780/);
  assert.equal(b.querySelectorAll('.diag-release').length, 1, 'no release marker on the bar');
}

// A card title is user- and model-supplied, and this panel builds markup.
// Everywhere else on the page a title goes in via textContent; here it does not.
const HOSTILE = '<img src=x onerror=alert(1)>';
renderDiag({tracking: true, hands: [hand({pinched: true, ratio: .3})],
            grabs: [grabOf({pinched: true, state: 'grabbed', holding: HOSTILE})]});
const body = dom.window.document.getElementById('diag-body');
assert.equal(body.querySelectorAll('img').length, 0,
  'a card title injected an element into the readout');
assert.ok(body.textContent.includes(HOSTILE), 'it should still be readable as text');

// The three states the panel itself has to distinguish.
renderDiag({tracking: false});
assert.match(body.textContent, /tracking is off/);
renderDiag({tracking: true, hands: [], grabs: []});
assert.match(body.textContent, /no hands in frame/);
renderDiag({tracking: true, diag_error: 'BoomError: nope'});
assert.match(body.textContent, /readout failed/,
  'a broken readout must say so rather than showing a stale frame');

// --- board events: throw, summon, swipe ------------------------------------
// Extracted by name from the real page, like the readout functions above.
{
  const dom2 = new JSDOM(
    '<div id="toast"></div><aside id="partner-panel" hidden><iframe data-src="/companion"></iframe></aside>' +
    '<button id="partner-toggle" aria-expanded="false"></button>', {runScripts: 'outside-only'});
  const w2 = dom2.window;
  w2.eval(`let toastTimer = null;\n${grab('toast')}\n${grab('openPartner')}\n${grab('handleBoardEvents')}\n` +
          `globalThis._h = handleBoardEvents;`);
  const doc = w2.document;

  w2._h([{seq: 1, type: 'thrown', id: 'x', title: '<img src=x onerror=alert(1)>'}]);
  assert.equal(doc.querySelectorAll('#toast img').length, 0,
    'a thrown card title injected markup into the toast');
  assert.match(doc.getElementById('toast').textContent, /undo/,
    'a throw must tell the user how to get it back');

  assert.ok(doc.getElementById('partner-panel').hidden);
  w2._h([{seq: 2, type: 'summon'}]);
  assert.ok(!doc.getElementById('partner-panel').hidden, 'swipe-up did not summon Apex');
  assert.equal(doc.getElementById('partner-toggle').getAttribute('aria-expanded'), 'true');
  w2._h([{seq: 3, type: 'summon'}]);
  assert.ok(!doc.getElementById('partner-panel').hidden,
    'a second summon must not toggle the panel shut');

  w2._h([{seq: 4, type: 'selected', id: 'a', title: 'Calendar'}]);
  assert.match(doc.getElementById('toast').textContent, /Selected Calendar/);
  w2._h(undefined);                      // a frame with no events is normal
  dom2.window.close();
}
// The pointed ring must be distinct from the selection ring: they can be
// different objects, and Apex is told both.
assert.ok(html.includes('.card.pointed'), 'no style for the pointed card');
assert.ok(/classList\.toggle\('pointed', c\.id === pointedId\)/.test(html),
  'renderCards no longer marks the pointed card');

console.log('PASS: board events (throw, summon, select) render safely; '
  + 'every refusal names its number, titles cannot inject markup, '
  + 'and off / no-hands / broken are three distinct messages.');
