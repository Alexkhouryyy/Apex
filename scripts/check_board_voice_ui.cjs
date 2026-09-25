// Voice on /board: talk to Celine while your hands are on the board. The
// board's own script, with three.js stubbed (it draws nothing here), a fake
// websocket for gesture events, and a fake partner window standing in for
// the companion iframe. Requires jsdom on NODE_PATH.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
// Messages are built in the page's realm; compare their content.
const same = (a, b, m) => assert.equal(JSON.stringify(a), JSON.stringify(b), m);
const read = f => fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'static', f), 'utf8');
const html = read('board.html');
const dom = new JSDOM(html, {url: 'https://apex.example/board', runScripts: 'outside-only'});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
w.localStorage.setItem('apex_token', 't');
w.requestAnimationFrame = () => 0;
w.fetch = async () => Response.json({files: [], selection: null});
const sockets = [];
w.WebSocket = class { constructor() { sockets.push(this); } close() {} };
class V { constructor(){this.x=this.y=this.z=0;} set(){} setScalar(){} sub(){return this;} }
class G { constructor(){this.position=new V();this.scale=new V();this.rotation={};this.children=[];this.userData={};} add(){} remove(){} traverse(){} }
w.THREE = {LoadingManager: class {setURLModifier(){}}, WebGLRenderer: class {setPixelRatio(){}setSize(){}render(){}},
  Scene: G, Group: G, Vector3: V, OrthographicCamera: class extends G {updateProjectionMatrix(){}},
  AmbientLight: G, DirectionalLight: G, Box3: class {setFromObject(){return this;} getSize(v){return v;} getCenter(v){return v;}}};
w.GLTFLoader = class { setRequestHeader(){} load(){} };
let activated = false;
Object.defineProperty(w.navigator, 'userActivation', {value: {get hasBeenActive() { return activated; }}});

// The companion inside the panel, as the board sees it: a window it posts to.
const frame = d.querySelector('#partner-panel iframe');
const sent = [];
const partner = {postMessage(msg, origin) { assert.equal(origin, 'https://apex.example', 'post only to our own origin'); sent.push(msg); }};
Object.defineProperty(frame, 'contentWindow', {value: partner});
const fromPartner = (data, source = partner, origin = 'https://apex.example') =>
  w.dispatchEvent(new w.MessageEvent('message', {data, source, origin}));
const gesture = type => sockets[0].onmessage({data: JSON.stringify({cards: [], selection: null, cursors: [], tracking: true, events: [{type}]})});
const key = k => w.dispatchEvent(new w.KeyboardEvent('keydown', {key: k, bubbles: true}));

const code = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*;$/gm, '');
w.eval(code);
(async () => {
  const button = $('voice-toggle'), panel = $('partner-panel');
  assert.match(frame.getAttribute('allow'), /microphone/); assert.match(frame.getAttribute('allow'), /autoplay/,
    'the companion must be allowed to play her voice');

  // 1. The button: panel opens as a strip; the request waits for the page.
  button.click();
  assert.equal(panel.hidden, false); assert.ok(panel.classList.contains('voice'));
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.equal(frame.src, 'https://apex.example/companion?workspace=board');
  assert.equal(sent.length, 0, 'nothing may be posted before the companion says it is ready');
  fromPartner({apex: 'ready'});
  same(sent, [{apex: 'voice', on: true}]);

  // 2. Another page (or a cross-origin sender) cannot drive the board.
  fromPartner({apex: 'voice-state', on: false}, w, 'https://apex.example');
  fromPartner({apex: 'voice-state', on: false}, partner, 'https://evil.example');
  assert.equal(button.getAttribute('aria-pressed'), 'true', 'only the partner frame, same origin, is listened to');

  // 3. Swipe down: she stops speaking, voice stays on.
  gesture('hush');
  same(sent.at(-1), {apex: 'hush'}); assert.equal(button.getAttribute('aria-pressed'), 'true');

  // 4. She left by herself (Esc inside, no mic): the board follows.
  fromPartner({apex: 'voice-state', on: false});
  assert.equal(button.getAttribute('aria-pressed'), 'false'); assert.ok(!panel.classList.contains('voice'));
  const n = sent.length; gesture('hush'); assert.equal(sent.length, n, 'no hush when voice is off');

  // 5. A gesture before any click or key: the browser would block the
  //    speaker, so it says what to do instead of failing silently.
  gesture('summon');
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.match($('toast').textContent, /press V once/);
  // After a click or key it starts voice: swipe up, then pinch-and-hold.
  activated = true;
  gesture('summon');
  assert.equal(button.getAttribute('aria-pressed'), 'true'); same(sent.at(-1), {apex: 'voice', on: true});
  fromPartner({apex: 'voice-state', on: false});
  gesture('listen');
  assert.equal(button.getAttribute('aria-pressed'), 'true');

  // 6. V toggles, Esc leaves; typing in a field does neither.
  key('Escape'); assert.equal(button.getAttribute('aria-pressed'), 'false'); same(sent.at(-1), {apex: 'voice', on: false});
  key('v'); assert.equal(button.getAttribute('aria-pressed'), 'true');
  const input = d.createElement('input'); d.body.append(input);
  input.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'v', bubbles: true}));
  assert.equal(button.getAttribute('aria-pressed'), 'true', 'a v typed into a field is a letter, not a command');

  // 7. "Talk to Apex" with voice on: the whole conversation, voice off.
  $('partner-toggle').click();
  assert.equal(button.getAttribute('aria-pressed'), 'false'); assert.equal(panel.hidden, false);
  assert.ok(!panel.classList.contains('voice'));

  console.log('PASS: board voice opens Celine as a strip once the companion is ready, swipe down hushes her, swipe up / pinch-hold start her '
    + '(after one click or key, with a toast before), V and Esc toggle, she can leave by herself, and only the partner frame is listened to.');
  w.close(); process.exit(0);
})().catch(e => { console.error(e); w.close(); process.exit(1); });
