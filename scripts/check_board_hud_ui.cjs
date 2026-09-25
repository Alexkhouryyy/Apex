// The instructions on /board: shown by default, H (or the button) hides them
// and the choice is remembered; the "now" line says what to do this second;
// the pinch meter shows the number that decides a pinch; and the calibration
// runs on screen, prompt by prompt. The board's own script with three.js
// stubbed. Requires jsdom on NODE_PATH.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'static', 'board.html'), 'utf8');
const tick = () => new Promise(r => setTimeout(r, 10));

function open(stored) {
  const dom = new JSDOM(html, {url: 'https://apex.example/board', runScripts: 'outside-only'});
  const w = dom.window;
  w.localStorage.setItem('apex_token', 't');
  if (stored) w.localStorage.setItem('apex.board.hud', stored);
  w.requestAnimationFrame = () => 0;
  const posts = [];
  w.fetch = async (url, opts = {}) => {
    if (url === '/api/board/calibrate') { posts.push(JSON.parse(opts.body)); return Response.json({phase: 'ready'}); }
    return Response.json({files: [], selection: null});
  };
  const sockets = [];
  w.WebSocket = class { constructor() { sockets.push(this); } close() {} };
  class V { constructor(){this.x=this.y=this.z=0;} set(){} setScalar(){} sub(){return this;} }
  class G { constructor(){this.position=new V();this.scale=new V();this.rotation={};this.children=[];this.userData={};} add(){} remove(){} traverse(){} }
  w.THREE = {LoadingManager: class {setURLModifier(){}}, WebGLRenderer: class {setPixelRatio(){}setSize(){}render(){}},
    Scene: G, Group: G, Vector3: V, OrthographicCamera: class extends G {updateProjectionMatrix(){}},
    AmbientLight: G, DirectionalLight: G, Box3: class {setFromObject(){return this;} getSize(v){return v;} getCenter(v){return v;}}};
  w.GLTFLoader = class { setRequestHeader(){} load(){} };
  w.eval(html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*;$/gm, ''));
  const frame = (m) => sockets[0].onmessage({data: JSON.stringify({cards: [], selection: null, cursors: [], tracking: true,
    board_enabled: true, hands: [], events: [], calibration: {phase: 'idle'}, ...m})});
  return {w, $: id => w.document.getElementById(id), posts, frame};
}

(async () => {
  // 1. Shown by default; H hides it and it stays hidden next time.
  let {w, $, posts, frame} = open();
  assert.equal($('hud').hidden, false, 'the instructions are in front of you by default');
  assert.match($('hud-moves').textContent, /Pinch & hold/); assert.match($('hud-moves').textContent, /Quick tap/);
  assert.match($('hud').textContent, /Calibrate your pinch/);
  w.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'h', bubbles: true}));
  assert.equal($('hud').hidden, true); assert.equal($('hud-toggle').getAttribute('aria-pressed'), 'false');
  assert.equal(w.localStorage.getItem('apex.board.hud'), 'hidden');
  w.close();
  ({w, $, posts, frame} = open('hidden'));
  assert.equal($('hud').hidden, true, 'hidden stays hidden after a reload');
  $('hud-toggle').click();
  assert.equal($('hud').hidden, false, 'the Help button brings it back');
  $('hud-close').click(); assert.equal($('hud').hidden, true);
  w.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'H', bubbles: true})); assert.equal($('hud').hidden, false);

  // 2. The "now" line follows what is happening.
  frame({tracking: false}); assert.match($('hud-now').textContent, /Hand tracking is off — HANDTRACK_ENABLED=true/);
  frame({}); assert.match($('hud-now').textContent, /Hold a hand up/);
  const hand = (ratio, pinched) => ({id: 0, ratio, threshold: 0.7, release: 0.78, pinched});
  frame({hands: [hand(1.1, false)]}); assert.match($('hud-now').textContent, /build me a rocket/);
  const rocket = {id: 'r', kind: 'model', title: 'Rocket', body: '', x: .5, y: .5, scale: 1, rot: 0};
  frame({hands: [hand(0.3, true)], cards: [{...rocket, held: true, hands: 1}]});
  assert.match($('hud-now').textContent, /Holding Rocket — move it, open your fingers to let go/);
  frame({hands: [hand(0.3, true)], cards: [{...rocket, held: true, hands: 2}]});
  assert.match($('hud-now').textContent, /Resizing Rocket/);
  frame({hands: [hand(0.3, true)], cards: [{...rocket, parts_mode: true, part: {name: 'nose', index: 1}}]});
  assert.match($('hud-now').textContent, /Moving the nose/);

  // 3. The pinch meter: the number that decides it, and which side it is on.
  frame({hands: [hand(0.62, true)], cards: [rocket]});
  assert.match($('hud-state').textContent, /PINCHED · 0.62 \(lets go above 0.78\)/);
  assert.ok($('hud-state').classList.contains('on'));
  frame({hands: [hand(1.05, false)], cards: [rocket]});
  assert.match($('hud-state').textContent, /open · 1.05 \(pinch below 0.70\)/);
  assert.ok(!$('hud-state').classList.contains('on'));

  frame({hands: [{...hand(0.2, false), fist: true}], cards: [rocket]});
  assert.equal($('hud-state').textContent, 'fist · not a grab');

  // 3b. Not calibrated for the 3D pinch yet: the button says so, loudly.
  frame({hands: [hand(1.05, false)], pinch_calibrated: false});
  assert.match($('calibrate').textContent, /Not calibrated for your hand yet/);
  assert.ok($('calibrate').classList.contains('urgent'));
  frame({hands: [hand(1.05, false)], pinch_calibrated: true});
  assert.match($('calibrate').textContent, /Calibrate your pinch/); assert.ok(!$('calibrate').classList.contains('urgent'));

  // 4. Calibration, prompt by prompt, on screen.
  $('calibrate').click(); await tick();
  assert.equal(JSON.stringify(posts.at(-1)), JSON.stringify({action: 'start'}));
  frame({calibration: {phase: 'ready', pose: 'relaxed', step: 2, steps: 3, prompt: 'Now let it RELAX', left: 1.4}});
  assert.equal($('calib').hidden, false);
  assert.match($('calib-step').textContent, /Step 2 of 3 · get ready/);
  assert.equal($('calib-prompt').textContent, 'Now let it RELAX'); assert.equal($('calib-left').textContent, '2');
  frame({calibration: {phase: 'recording', pose: 'relaxed', step: 2, steps: 3, prompt: 'Now let it RELAX', left: 2.1, samples: 31}});
  assert.match($('calib-note').textContent, /31 readings/);
  $('calib-action').click(); await tick();
  assert.equal(JSON.stringify(posts.at(-1)), JSON.stringify({action: 'cancel'}), 'Cancel during a run cancels it');
  frame({calibration: {phase: 'done', ok: true, enter: 0.34, release: 0.45, reason: 'Cleanly separated.', saved: 'saved'}});
  assert.match($('calib-prompt').textContent, /starts below 0.34 and lets go above 0.45/);
  assert.match($('calib-note').textContent, /saved, and in use now/);
  $('calib-action').click();
  assert.equal($('calib').hidden, true);
  frame({calibration: {phase: 'done', ok: true, enter: 0.34, release: 0.45}});
  assert.equal($('calib').hidden, true, 'a closed result stays closed');
  frame({calibration: {phase: 'done', ok: false, reason: 'These cannot be separated'}});
  // A NEW run after closing shows again.
  $('calibrate').click(); await tick();
  frame({calibration: {phase: 'done', ok: false, reason: 'These cannot be separated: overlap'}});
  assert.equal($('calib').hidden, false); assert.match($('calib-prompt').textContent, /Nothing was changed/);

  console.log('PASS: the instructions are shown by default and H / Help hide them (remembered); the "now" line follows what you are doing; '
    + 'the pinch meter shows the deciding number; calibration runs prompt by prompt on screen, can be cancelled, and shows its result.');
  w.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
