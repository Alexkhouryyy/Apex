// Daily workspace DOM checks: editing, shortcuts and error paths.
// Hand setup is hidden by default; H toggles it
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
  w.confirm = () => true;
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
  const {w, $, frame} = open();
  const d = w.document, requests = [];
  $('note-dialog').showModal = function () { this.open = true; };
  $('note-dialog').close = function () { this.open = false; };
  w.fetch = async (url, opts = {}) => {
    requests.push({url, body: opts.body ? JSON.parse(opts.body) : null});
    if (url === '/api/board/workspace') {
      if (requests.at(-1).body.action === 'note') return new Response(JSON.stringify({detail: 'Storage unavailable'}), {status: 400});
      return Response.json({message: 'Undone', hands_enabled: false});
    }
    return Response.json({files: []});
  };
  const note = {id:'n',kind:'card',title:'<script>unsafe</script>',body:'Actual note',x:.5,y:.5,scale:1,rot:0,content_revision:'original'};
  frame({cards:[note],selection:note});
  assert.equal(d.querySelector('#object-list .object-item span:last-child').textContent, note.title);
  assert.equal(d.querySelector('#object-list script'), null);
  assert.equal($('selection-tools').hidden, false);
  $('hands-toggle').click(); await tick();
  assert.deepEqual(requests.at(-1).body, {action:'hands',enabled:false});
  frame({cards:[note],selection:note,hands_enabled:false,cursors:[{x:.5,y:.5,p:true}]});
  assert.equal($('hands-toggle').textContent, 'Hands paused');
  assert.equal(d.querySelectorAll('.ring').length, 0);
  $('focus-toggle').click(); assert.ok(d.body.classList.contains('focus-workspace'));
  assert.equal(w.localStorage.getItem('apex.workspace.focus'), 'true');
  $('camera-toggle').click(); assert.ok(d.body.classList.contains('camera-workspace'));
  $('undo-board').click(); await tick(); assert.equal(requests.at(-1).body.action,'undo');
  w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'z',ctrlKey:true,shiftKey:true}));
  await tick(); assert.equal(requests.at(-1).body.action,'redo');
  const pointer = (el,type,x,y) => { const ev=new w.Event(type,{bubbles:true}); Object.assign(ev,{button:0,pointerId:1,clientX:x,clientY:y});el.dispatchEvent(ev); };
  const card=d.querySelector('.card');
  const before=requests.length;
  pointer(card,'pointerdown',300,300);pointer(card,'pointermove',400,320);
  assert.equal(requests.length,before,'preview must not write on each pointer move');
  pointer(card,'pointerup',400,320);await tick();
  assert.equal(requests.at(-1).body.action,'transform');
  assert.ok(requests.at(-1).body.changes.x>.5);
  const count=requests.length;
  pointer(card,'pointerdown',300,300);pointer(card,'pointermove',400,320);pointer(card,'pointercancel',400,320);await tick();
  assert.equal(requests.length,count,'cancel never commits');
  $('add-note').click(); $('note-title').value='New idea';
  $('note-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await tick();
  assert.equal($('note-dialog').open,true,'failed creation keeps the draft open');
  assert.match($('note-error').textContent,/Storage unavailable/);
  assert.equal($('save-note').disabled,false);
  $('close-note').click();
  $('edit-content').click();assert.equal($('note-title').value,note.title);
  assert.equal($('note-kind').disabled,true);
  $('note-title').value='My edited draft';
  w.fetch=async(url,opts)=>{
    const body=JSON.parse(opts.body);requests.push({url,body});
    if(body.action==='edit_content')return new Response(JSON.stringify({detail:'Changed in another window'}),{status:409});
    return Response.json({card:{...note,...body}});
  };
  // Incoming updates must not overwrite the open draft or its expected revision.
  const changed={...note,title:'Someone else',content_revision:'new'};
  frame({cards:[changed],selection:changed});
  $('note-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await tick();
  assert.equal(requests.at(-1).body.expected_revision,'original');
  assert.equal($('note-dialog').open,true);assert.equal($('note-title').value,'My edited draft');
  assert.equal($('note-copy').hidden,false);
  $('note-copy').click();await tick();assert.equal(requests.at(-1).body.action,'note');
  assert.equal(requests.at(-1).body.id,undefined);assert.equal($('note-dialog').open,false);
  const link={...note,id:'link',kind:'link',title:'Reference',src:'https://example.com/project'};
  frame({cards:[link],selection:link});assert.equal($('open-link').href,link.src);
  assert.equal($('open-link').target,'_blank');assert.match($('open-link').rel,/noopener/);
  frame({cards:[{...link,src:'javascript:alert(1)'}],selection:link});
  assert.equal($('open-link').hidden,true);assert.equal($('open-link').hasAttribute('href'),false);
  $('add-link').click();assert.equal($('note-kind').value,'link');assert.equal($('note-url-label').hidden,false);
  $('note-title').value='Docs';$('note-url').value='https://example.com';
  let complete;
  w.fetch=(url,opts)=>new Promise(resolve=>{requests.push({url,body:JSON.parse(opts.body)});complete=resolve;});
  $('note-form').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await tick();
  assert.equal($('note-title').disabled,true);$('close-note').click();assert.equal($('note-dialog').open,true);
  complete(Response.json({card:link}));await tick();
  assert.equal($('note-dialog').open,false);assert.equal($('note-title').disabled,false);
  assert.equal(requests.at(-1).body.action,'link');
  console.log('PASS: workspace controls, edit conflicts preserve drafts, save a copy, safe links, pending-save guards and creation error recovery.');
  w.close();process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
