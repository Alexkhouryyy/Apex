// Parts mode on /board: P asks for it on the selected model (and says why
// when it cannot), the label names the part in your hand, and the live
// preview moves and highlights ONLY that part — scaled about its own centre.
// The board's own script with three.js stubbed. Requires jsdom on NODE_PATH.
const {JSDOM} = require('jsdom');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard', 'static', 'board.html'), 'utf8');
const dom = new JSDOM(html, {url: 'https://apex.example/board', runScripts: 'outside-only'});
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
const tick = () => new Promise(r => setTimeout(r, 10));
w.localStorage.setItem('apex_token', 't');
w.requestAnimationFrame = () => 0;
const posts = [];
let refuse = null;
w.fetch = async (url, opts = {}) => {
  if (url === '/api/board/parts') {
    posts.push(JSON.parse(opts.body));
    if (refuse) return new Response(JSON.stringify({detail: refuse}), {status: 400, headers: {'Content-Type': 'application/json'}});
    return Response.json({parts_mode: null});
  }
  return Response.json({files: [], selection: null});
};
const sockets = [];
w.WebSocket = class { constructor() { sockets.push(this); } close() {} };
class V { constructor(x=0,y=0,z=0){this.x=x;this.y=y;this.z=z;} set(x,y,z){Object.assign(this,{x,y,z});} setScalar(v){this.x=this.y=this.z=v;} sub(){return this;} }
class G { constructor(){this.position=new V();this.scale=new V(1,1,1);this.rotation={};this.children=[];this.userData={};}
  add(c){this.children.push(c);} remove(){} traverse(fn){fn(this);this.children.forEach(c=>c.traverse?.(fn));} }
w.THREE = {LoadingManager: class {setURLModifier(){}}, WebGLRenderer: class {setPixelRatio(){}setSize(){}render(){}},
  Scene: G, Group: G, Vector3: V, OrthographicCamera: class extends G {updateProjectionMatrix(){}},
  AmbientLight: G, DirectionalLight: G, Box3: class {setFromObject(){return this;} getSize(v){v.set(1,1,1);return v;} getCenter(v){return v;}}};
const loads = [];
w.GLTFLoader = class { setRequestHeader(){} load(url, ok){ loads.push(ok); } };
const code = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*;$/gm, '');
w.eval(code);
const key = k => w.dispatchEvent(new w.KeyboardEvent('keydown', {key: k, bubbles: true}));
const frame = (cards, selection = null, events = []) =>
  sockets[0].onmessage({data: JSON.stringify({cards, selection, cursors: [], tracking: true, events})});

(async () => {
  const card = {id: 'r1', kind: 'model', title: 'Rocket', body: '', src: 'created/rocket/v1.glb', x: .5, y: .5, scale: 1, rot: 0};

  // 1. P with nothing selected: says what to do, asks the server nothing.
  frame([card], card);
  assert.match($('selection-label').textContent, /whole object — P to take it apart/);
  frame([card]);
  key('p'); await tick();
  assert.equal(posts.length, 0); assert.match($('toast').textContent, /Select a model first/);

  // 2. Selected: P asks for parts mode on it; P again (mode on) turns it off.
  frame([card], card);
  key('P'); await tick();
  assert.equal(JSON.stringify(posts.at(-1)), JSON.stringify({id: 'r1'}));
  frame([{...card, parts_mode: true}], card, [{type: 'parts_mode', id: 'r1', title: 'Rocket', on: true}]);
  assert.match($('toast').textContent, /Parts mode: pinch a part of Rocket/);
  const label = [...d.querySelectorAll('.card')].find(el => el.textContent.includes('Rocket'));
  assert.match(label.textContent, /Rocket · parts/); assert.ok(label.classList.contains('parts'));
  assert.match($('selection-label').textContent, /Rocket · parts — pinch one/);
  key('p'); await tick();
  assert.equal(JSON.stringify(posts.at(-1)), JSON.stringify({id: null}), 'P in parts mode turns it off');

  // 3. The server's reason reaches the user, not "Request failed (400)".
  frame([card], card);
  refuse = "'Rocket' wasn't built from parts, so it can only be moved whole.";
  key('p'); await tick(); await tick();
  assert.match($('toast').textContent, /wasn't built from parts/);
  refuse = null;

  // 4. The live preview: the model loads with two parts; holding the nose
  //    moves and scales ONLY the nose, about its own centre, and lights it.
  const mesh = (part, centre) => {
    const m = new G(); m.isMesh = true; m.userData = {part, centre};
    m.material = {emissive: {r: 0, g: 0, b: 0, setRGB(r, g, b) { Object.assign(this, {r, g, b}); }}};
    return m;
  };
  const body = mesh(0, [0, .3, 0]), nose = mesh(1, [0, .725, 0]);
  const scene = new G(); scene.add(body); scene.add(nose);
  frame([{...card, parts_mode: true}], card);
  loads.at(-1)({scene});
  frame([{...card, parts_mode: true, part: {index: 1, name: 'nose', offset: [0, .1, 0], scale: 2}}], card);
  assert.equal(nose.scale.x, 2);
  assert.ok(Math.abs(nose.position.y - (.725 * (1 - 2) + .1)) < 1e-9, 'scaled about its own centre, then moved');
  assert.equal(body.scale.x, 1); assert.equal(body.position.y, 0, 'the body must not move');
  assert.ok(nose.material.emissive.b > 0 && body.material.emissive.b === 0, 'only the held part lights up');
  assert.match(label.textContent, /Rocket · parts · nose/);
  // Let go: back to normal (the saved version replaces the file anyway).
  frame([{...card, parts_mode: true}], card, [{type: 'part_saved', id: 'r1', title: 'Rocket', part: 'nose', version: 2}]);
  assert.equal(nose.scale.x, 1); assert.equal(nose.position.y, 0); assert.equal(nose.material.emissive.b, 0);
  assert.match($('toast').textContent, /Saved the nose — Rocket v2/);

  console.log('PASS: P asks for parts mode on the selected model (or says to select one, or shows the server\'s reason), '
    + 'the label names the part in hand, and the live preview moves, scales and lights only that part.');
  w.close(); process.exit(0);
})().catch(e => { console.error(e); w.close(); process.exit(1); });
