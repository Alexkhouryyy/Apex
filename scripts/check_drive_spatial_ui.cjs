// DOM/network/Three simulations. Real car media and GPU rendering need hardware checks.
const {JSDOM} = require('jsdom');
const fs = require('fs'), assert = require('node:assert/strict'), path = require('path');
const base = path.join(__dirname, '..', 'dashboard', 'static');
const read = name => fs.readFileSync(path.join(base, name), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 30));

async function drive() {
  const dom = new JSDOM(read('companion.html'), {url:'https://apex.example/drive', runScripts:'outside-only'});
  const w = dom.window, $ = id => w.document.getElementById(id);
  let posts = [], executions = 0, polls = 0;
  w.localStorage.setItem('apex_token', 'test-token');
  w.TextDecoder = TextDecoder;
  w.setInterval = () => 0;
  w.setTimeout = fn => setTimeout(fn, 10);
  w.fetch = async (url, opts = {}) => {
    assert.equal(opts.headers.Authorization, 'Bearer test-token');
    if (url === '/api/status') return Response.json({agent_ready:true});
    if (url === '/api/companion/jobs' && opts.method !== 'POST') return Response.json({jobs:[]});
    if (url === '/api/companion/jobs') {
      const body = JSON.parse(opts.body); posts.push(body);
      if (posts.length === 1) { executions++; throw Error('Network lost after acceptance'); }
      assert.equal(body.turn_id, posts[0].turn_id);
      return Response.json({id:body.turn_id, thread_id:81, status:'running'});
    }
    if (url.startsWith('/api/companion/jobs/')) {
      polls++;
      return Response.json({id:posts[0].turn_id, thread_id:81, status:polls === 1 ? 'running' : 'done', text:'Checks finished: 1 passed.', evidence:[{type:'tool', phase:'result', name:'bash', result:'<script>not executable</script>'}]});
    }
    throw Error(url);
  };
  try {
    w.eval(read('companion.js')); await tick();
    assert.equal($('remote-panel').hidden, false);
    assert.match($('capabilities').textContent, /unavailable/);
    $('spoken').checked = false;
    $('message').value = 'Run my checks'; $('mode').value = 'work'; $('send').click(); await tick();
    assert.equal(posts.length, 1); assert.equal($('recover').hidden, false);
    assert.equal($('send').disabled, true);
    assert.ok(w.localStorage.getItem('apex_remote_pending'));
    $('recover').click(); await tick(); await tick();
    assert.equal(executions, 1); assert.equal(posts.length, 2);
    assert.equal(w.localStorage.getItem('apex_companion_thread'), '81');
    assert.equal(w.localStorage.getItem('apex_remote_pending'), null);
    assert.equal($('send').disabled, false);
    assert.match($('messages').textContent, /Checks finished: 1 passed/);
    assert.equal($('messages').querySelectorAll('script').length, 0);
  } finally { w.close(); }
}

async function spatial() {
  const dom = new JSDOM(read('board.html'), {url:'https://apex.example/board', runScripts:'outside-only'});
  const w = dom.window, d = w.document;
  const loads = [], requests = [], sockets = [];
  w.localStorage.setItem('apex_token', 'test-token');
  w.requestAnimationFrame = () => 0;
  w.fetch = async (url, opts={}) => {
    requests.push({url,opts});
    return Response.json(url === '/api/forge/exports' ? {files:['part.stl']} : {selection:null});
  };
  w.WebSocket = class { constructor() {sockets.push(this);} close() {} };
  class Vector {
    constructor(x=0,y=0,z=0) { this.x=x;this.y=y;this.z=z; }
    set(x,y,z) { Object.assign(this,{x,y,z}); }
    setScalar(v) {this.x=this.y=this.z=v;}
    sub() {return this;}
  }
  class Group {
    constructor(){this.position=new Vector();this.scale=new Vector(1,1,1);this.rotation={};this.children=[];}
    add(child){this.children.push(child);}
    remove(child){this.children=this.children.filter(x=>x!==child);}
    traverse(fn){fn(this);this.children.forEach(child=>child.traverse?.(fn));}
  }
  w.THREE = {
    LoadingManager: class {setURLModifier(fn){this.modify=fn;}},
    WebGLRenderer: class {setPixelRatio(){}setSize(){}render(){}},
    Scene: Group, Group, Vector3:Vector,
    OrthographicCamera: class extends Group {updateProjectionMatrix(){}},
    AmbientLight:Group, DirectionalLight:Group,
    Box3:class {setFromObject(){return this;}getSize(v){v.set(1,1,1);return v;}getCenter(v){return v;}}
  };
  w.GLTFLoader = class {
    constructor(manager){this.manager=manager;w.testLoader=this;}
    setRequestHeader(headers){this.headers=headers;}
    load(url, success, progress, failure){this.manager.modify(url);loads.push({url,success,failure});}
  };
  try {
    const code = read('board.html').match(/<script type="module">([\s\S]*?)<\/script>/)[1]
      .replace(/^import .*;$/gm,'');
    w.eval(code);
    assert.equal(w.testLoader.headers.Authorization, 'Bearer test-token');
    assert.throws(()=>w.testLoader.manager.modify('https://evil.example/model.bin'), /props folder/);
    assert.throws(()=>w.testLoader.manager.modify('/api/private'), /props folder/);
    const card={id:'one',kind:'model',title:'Model <unsafe>',body:'',src:'v1.glb',x:.5,y:.5,scale:1,rot:0};
    const frame=c=>sockets[0].onmessage({data:JSON.stringify({cards:[c],selection:c,cursors:[],tracking:false})});
    frame(card);
    assert.equal(loads.length,1);
    d.querySelector('.model-label').click(); await tick();
    assert.equal(JSON.parse(requests.find(x=>x.url==='/api/board/select').opts.body).id,'one');
    assert.match(d.getElementById('selection-label').textContent,/Model <unsafe>/);
    assert.equal(d.querySelectorAll('unsafe').length,0);
    frame({...card,src:'v2.glb'}); assert.equal(loads.length,2);
    loads[0].success({scene:new Group()}); // obsolete response must not replace v2
    loads[1].success({scene:new Group()});
    frame({...card,src:'v2.glb'}); assert.equal(loads.length,2);
    frame({...card,src:'v3.glb'}); assert.equal(loads.length,3);
    d.getElementById('partner-toggle').click();
    assert.equal(d.getElementById('partner-panel').hidden,false);
    assert.match(d.querySelector('iframe').src,/workspace=board/);
    d.getElementById('clear-selection').click();await tick();
    assert.equal(JSON.parse(requests.filter(x=>x.url==='/api/board/select').at(-1).opts.body).id,null);
  } finally {w.close();}
}

(async()=>{
  await drive();await spatial();
  console.log('PASS: car lost-response recovery/idempotency, saved results, media fallback, selection, voice panel, authenticated models, jailed dependencies, stale model reloads.');
})().catch(error=>{console.error(error);process.exitCode=1;});
