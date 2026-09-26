import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const $ = id => document.getElementById(id);
let token = '';
try { token = localStorage.getItem('apex_token') || ''; } catch (_) {}
const query = new URLSearchParams(location.search);
if (query.has('token')) { token = query.get('token'); try { localStorage.setItem('apex_token', token); } catch (_) {} query.delete('token'); history.replaceState(null, '', location.pathname + (query.size ? '?' + query : '')); }
let current = null, manifest = null, session = query.get('session'), timer = null, busy = Promise.resolve();
let scene, camera, renderer, orbit, cameraTween = null, rotorAngle = 0, amount = 0, targetAmount = 0;
const groups = new Map(), pickables = [], raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2();
const clipping = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
function status(text) { $('status').textContent = text; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{Authorization:`Bearer ${token}`, 'Content-Type':'application/json', ...options.headers}});
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    if (response.status === 401 && !$('login').open) $('login').showModal();
    const error = new Error(message); error.status = response.status; throw error;
  }
  return response.json();
}
function command(action, extra = {}) {
  busy = busy.catch(() => {}).then(async () => {
    try { accept(await api('/api/study/session/' + session, {method:'POST',body:JSON.stringify({action,...extra})})); status('View updated · original geometry preserved'); }
    catch (error) { status(error.message); targetAmount = current?.explosion || 0; $('separation').value = String(targetAmount * 100); }
  });
  return busy;
}
function makeMaterial(color, metal = .55) { return new THREE.MeshStandardMaterial({color, metalness:metal, roughness:.36, side:THREE.DoubleSide}); }
function mesh(parent, geometry, color, position=[0,0,0], rotation=[0,0,0], metal=.55) {
  const o = new THREE.Mesh(geometry, makeMaterial(color,metal)); o.position.set(...position);o.rotation.set(...rotation);parent.add(o);return o;
}
function cylinder(parent, radius, length, color, x=0, theta=0, arc=Math.PI*2) { return mesh(parent,new THREE.CylinderGeometry(radius,radius,length,64,1,false,theta,arc),color,[x,0,0],[0,0,-Math.PI/2]); }
function annularSector(outer, inner, length, start, arc) {
  const shape = new THREE.Shape(), end = start + arc;
  shape.absarc(0,0,outer,start,end,false);
  shape.lineTo(inner*Math.cos(end),inner*Math.sin(end));
  shape.absarc(0,0,inner,end,start,true);shape.closePath();
  const geo = new THREE.ExtrudeGeometry(shape,{depth:length,bevelEnabled:false,curveSegments:48,steps:1});
  geo.rotateY(Math.PI/2);geo.translate(-length/2,0,0);return geo;
}
function ring(parent,radius,tube,color,x=0) { return mesh(parent,new THREE.TorusGeometry(radius,tube,12,80),color,[x,0,0],[0,Math.PI/2,0]); }
function part(id, offset, rotating=false) { const group = new THREE.Group();group.userData={id,offset:new THREE.Vector3(...offset),rotating};scene.add(group);groups.set(id,group);return group; }
function bearing(id,x,offset) {
  const g=part(id,offset);ring(g,.31,.065,'#718b95',x);ring(g,.16,.055,'#b5c8cd',x);
  for(let i=0;i<10;i++){const a=i*Math.PI/5;mesh(g,new THREE.SphereGeometry(.053,12,8),'#d1e4e8',[x,.238*Math.cos(a),.238*Math.sin(a)]);}
}
function buildMotor() {
  let g=part('housing',[0,2.8,-1.8]);
  const shell=mesh(g,new THREE.CylinderGeometry(1.12,1.12,2.8,80,1,true),'#6b949d',[0,0,0],[0,0,-Math.PI/2]);
  shell.material.transparent=true;shell.material.opacity=.22;shell.material.depthWrite=false;
  ring(g,1.12,.035,'#8ebbbd',-1.4);ring(g,1.12,.035,'#8ebbbd',1.4);
  for(let i=0;i<8;i++)ring(g,1.13,.01,'#759fa7',-.95+i*.27);
  for(const [id,sign] of [['magnet-n',1],['magnet-s',-1]]){
    g=part(id,[0,sign*1.65,sign*1.7]);
    // Arc surfaces describe opposed field magnets; colour denotes a teaching pole.
    const m=mesh(g,annularSector(.99,.91,2.2,sign>0?.4:Math.PI+.4,Math.PI-.8),sign>0?'#518fa5':'#bd796b');
    m.material.roughness=.7;
  }
  g=part('shaft',[-.2,0,0],true);cylinder(g,.12,5.8,'#d6e3e5');
  cylinder(g,.16,2.5,'#98aeb6',0);
  g=part('armature',[0,0,0],true);
  for(let i=0;i<27;i++)cylinder(g,.65,.052,i%2?'#607880':'#869ca3',-.8+i*.0615);
  for(let i=0;i<3;i++){const a=i*2*Math.PI/3;mesh(g,new THREE.BoxGeometry(1.6,.32,.42),'#7c9299',[0,.59*Math.cos(a),.59*Math.sin(a)],[a,0,0]);}
  g=part('windings',[0,1.8,0],true);
  for(let i=0;i<3;i++){
    const angle=i*2*Math.PI/3;
    for(let loop=0;loop<9;loop++){
      const radial=.77+loop*.012, half=.18+loop*.008;
      const points=[[-.93,-half],[-.81,-half-.045],[.81,-half-.045],[.93,-half],[.93,half],[.81,half+.045],[-.81,half+.045],[-.93,half]].map(([x,t])=>new THREE.Vector3(x,radial*Math.cos(angle)-t*Math.sin(angle),radial*Math.sin(angle)+t*Math.cos(angle)));
      const curve=new THREE.CatmullRomCurve3(points,true,'centripetal');
      mesh(g,new THREE.TubeGeometry(curve,80,.013,6,true),loop%2?'#d78d47':'#ac632d');
    }
  }
  g=part('commutator',[2.2,0,0],true);cylinder(g,.23,.55,'#242a31',1.35);
  for(let i=0;i<3;i++)mesh(g,annularSector(.34,.235,.55,i*2*Math.PI/3+.04,2*Math.PI/3-.08),'#cd8548',[1.35,0,0]);
  for(const [id,sign] of [['brush-positive',1],['brush-negative',-1]]){
    g=part(id,[2.1,sign*1.5,0]);mesh(g,new THREE.BoxGeometry(.3,.34,.26),'#28353e',[1.35,sign*.48,0],undefined,.1);
    mesh(g,new THREE.BoxGeometry(.42,.14,.4),'#a3b7bd',[1.35,sign*.72,0]);
    // A visual holder; no implied spring force or circuit simulation.
  }
  bearing('front-bearing',-1.7,[-2.2,0,0]);bearing('rear-bearing',1.88,[3.1,0,0]);
  for(const [id,x,offset] of [['front-cover',-1.55,[-3.7,0,0]],['rear-cover',1.72,[4.5,0,0]]]){
    g=part(id,offset);ring(g,.93,.1,'#698994',x);ring(g,.37,.1,'#8ea9b3',x);
    for(let i=0;i<6;i++){const a=i*Math.PI/3;mesh(g,new THREE.BoxGeometry(.13,.58,.12),'#607f8c',[x,.64*Math.cos(a),.64*Math.sin(a)],[a,0,0]);}
  }
  g=part('terminals',[4.4,1.9,0]);mesh(g,new THREE.BoxGeometry(.16,.6,.15),'#bb955b',[2,.6,.35]);mesh(g,new THREE.BoxGeometry(.16,.6,.15),'#bb955b',[2,.6,-.35]);
  for(const [id,group] of groups)group.traverse(o=>{if(o.isMesh){o.userData.part=id;pickables.push(o);}});
}
function fitCamera(animate = false) {
  const to = new THREE.Vector3(7.4,4.4,8.5).multiplyScalar(targetAmount > .2 ? 1.5 : 1);
  if (animate && !reducedMotion.matches) cameraTween = {from:camera.position.clone(),to,t:0};
  else { cameraTween=null; camera.position.copy(to); orbit.target.set(.45,0,0); orbit.update(); }
}
function initScene() {
  renderer = new THREE.WebGLRenderer({canvas:$('model'),alpha:true,antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.localClippingEnabled=true;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.55;
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(38,1,.05,100);
  scene.add(new THREE.HemisphereLight('#d5f4f4','#142330',2));
  const key=new THREE.DirectionalLight('#d1ebff',3);key.position.set(4,6,4);scene.add(key);
  const rim=new THREE.DirectionalLight('#63c6c0',2);rim.position.set(-4,2,-3);scene.add(rim);
  const grid=new THREE.GridHelper(28,28,'#25424b','#152c36');grid.position.y=-3.2;scene.add(grid);
  orbit=new OrbitControls(camera,renderer.domElement);orbit.enableDamping=true;orbit.dampingFactor=.09;orbit.minDistance=2.5;orbit.maxDistance=30;fitCamera();
  buildMotor();
  orbit.addEventListener('start',()=>{cameraTween=null;});
  const resize=()=>{const r=$('viewport').getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();};
  new ResizeObserver(resize).observe($('viewport'));resize();
  let down=null;
  $('model').addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY,id:e.pointerId};});
  $('model').addEventListener('pointercancel',()=>{down=null;});
  $('model').addEventListener('pointerup',e=>{
    if(!down||down.id!==e.pointerId||Math.hypot(e.clientX-down.x,e.clientY-down.y)>5){down=null;return;}down=null;
    const r=$('model').getBoundingClientRect();mouse.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(mouse,camera);
    const hits=raycaster.intersectObjects(pickables,false).filter(h=>groups.get(h.object.userData.part).visible&&(!current.section||clipping.distanceToPoint(h.point)>=0));
    // The transparent housing lets you pick the visible internals; select
    // the shell from the tree if it lies in front of the part you want.
    const hit=hits.find(h=>h.object.userData.part!=='housing')||hits[0];
    if(hit)command('select',{part:hit.object.userData.part});
  });
  let before=0;
  function draw(now){
    const dt=Math.min(.05,(now-before)/1000||0);before=now;
    amount=reducedMotion.matches?targetAmount:THREE.MathUtils.damp(amount,targetAmount,7,dt);
    if(current?.rotating&&!reducedMotion.matches)rotorAngle+=dt*.8;
    if(cameraTween){cameraTween.t=Math.min(1,cameraTween.t+dt/0.65);const t=cameraTween.t;camera.position.lerpVectors(cameraTween.from,cameraTween.to,t*t*(3-2*t));if(t===1)cameraTween=null;}
    for(const group of groups.values()){
      group.position.copy(group.userData.offset).multiplyScalar(amount);
      group.rotation.x=group.userData.rotating?rotorAngle:0;
    }
    orbit.update();renderer.render(scene,camera);
    const group=groups.get(current?.selected);const label=$('part-label');
    if(group?.visible){const centre=new THREE.Box3().setFromObject(group).getCenter(new THREE.Vector3()).project(camera);const r=$('viewport').getBoundingClientRect();label.hidden=centre.z< -1||centre.z>1||Math.abs(centre.x)>.92||Math.abs(centre.y)>.92;label.style.left=(centre.x+1)/2*r.width+'px';label.style.top=(-centre.y+1)/2*r.height+'px';}else label.hidden=true;
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  $('model').addEventListener('webglcontextlost',e=>{e.preventDefault();status('3D graphics paused. Reload this page to restore the view.');});
}
function renderList(){
  const list=$('component-list');list.replaceChildren();const filter=$('search').value.toLowerCase();
  for(const category of ['Stator','Rotor','Commutation','Supports']){
    const parts=manifest.parts.filter(p=>p.group===category&&p.name.toLowerCase().includes(filter));if(!parts.length)continue;
    const title=document.createElement('div');title.className='component-group';title.textContent=category.toUpperCase();list.append(title);
    for(const p of parts){const b=document.createElement('button');b.className='component';b.dataset.part=p.id;b.setAttribute('aria-pressed',String(p.id===current?.selected));b.classList.toggle('dimmed',current?.hidden.includes(p.id));const i=document.createElement('i'),t=document.createElement('span');t.textContent=p.name;b.append(i,t);b.onclick=()=>command('select',{part:p.id});list.append(b);}
  }
  if(!list.children.length){const p=document.createElement('p');p.textContent='No matching components.';list.append(p);}
}
function accept(state){
  if(current&&state.revision<current.revision)return;
  if(current&&state.revision===current.revision)return;
  const needsRoom = current && current.explosion <= .2 && state.explosion > .2;
  current=state;targetAmount=state.explosion;
  if (needsRoom && orbit) fitCamera(true);
  $('separation').value=String(state.explosion*100);$('separation-value').textContent=Math.round(state.explosion*100)+'%';
  $('section').setAttribute('aria-pressed',String(state.section));$('rotate').setAttribute('aria-pressed',String(state.rotating));
  const selected=manifest.parts.find(p=>p.id===state.selected);
  $('part-name').textContent=selected?.name||'Look inside.';$('part-group').textContent=selected?.group.toUpperCase()||'START EXPLORING';
  $('part-purpose').textContent=selected?.purpose||'Separate the assembly, select a component, and discover how it connects to the whole.';
  $('part-details').hidden=!selected;$('isolate').disabled=!selected;$('hide-part').disabled=!selected;
  $('isolate').textContent=state.isolated?'Exit isolation':'Isolate';$('part-label').textContent=selected?.name||'';
  if(selected){$('part-connection').textContent=selected.connection;$('part-note').textContent=selected.model_note;$('part-source').href=manifest.sources.find(s=>s.id===selected.source).url;}
  for(const [id,g] of groups){g.visible=!state.hidden.includes(id)&&(!state.isolated||id===state.selected);g.traverse(o=>{if(o.isMesh){o.material.emissive.setHex(id===state.selected?0x1d5c57:0);o.material.emissiveIntensity=.38;o.material.clippingPlanes=state.section?[clipping]:[];}});}
  $('study-caption').textContent='Illustrative geometry · not to scale'+(state.section?' · uncapped section':state.rotating?' · illustrative rotor motion':' · transparent housing');
  renderList();
}
async function boot(){
  clearTimeout(timer);
  manifest=await api('/api/study/model/dc-motor');
  if(!session)session=(await api('/api/study/session',{method:'POST',body:'{"model":"dc-motor"}'})).session_id;
  let s, renewed=false;
  try{s=await api('/api/study/session/'+session);}catch(e){if(e.status!==404)throw e;session=(await api('/api/study/session',{method:'POST',body:'{"model":"dc-motor"}'})).session_id;s=await api('/api/study/session/'+session);renewed=true;}
  history.replaceState(null,'','/study?session='+encodeURIComponent(session));
  if(!renderer)initScene();current=null;accept(s);
  $('part-count').textContent=String(manifest.parts.length);$('limitations').textContent=manifest.limitations;
  $('sources').replaceChildren();for(const source of manifest.sources){const a=document.createElement('a');a.href=source.url;a.textContent=source.title+' ↗';a.target='_blank';a.rel='noopener noreferrer';$('sources').append(a);}
  status(renewed?'Previous study expired; a new assembly is open.':'Ready · select a component or take the motor apart');
  async function poll(){try{if(!document.hidden)accept(await api('/api/study/session/'+session));}catch(e){status(e.message);if(e.status===401||e.status===404)return;}timer=setTimeout(poll,500);}
  timer=setTimeout(poll,500);
}
$('search').oninput=renderList;
$('explode').onclick=()=>command('explode');$('assemble').onclick=()=>command('assemble');
$('section').onclick=()=>command('section');$('rotate').onclick=()=>command('rotate');
$('isolate').onclick=()=>command('isolate');$('hide-part').onclick=()=>command('hide');$('show-all').onclick=()=>command('show_all');
$('undo').onclick=()=>command('undo');$('redo').onclick=()=>command('redo');$('reset-camera').onclick=()=>{if(orbit)fitCamera();};
$('separation').oninput=e=>{targetAmount=Number(e.target.value)/100;$('separation-value').textContent=e.target.value+'%';};
$('separation').onchange=e=>command('explode',{amount:Number(e.target.value)/100});
let partnerReady=false, askPending=false;
function askPartner(){if(!current)return;const panel=$('study-partner'),frame=panel.querySelector('iframe');panel.hidden=false;askPending=true;if(!frame.src)frame.src='/companion?workspace=assembly&study_session='+encodeURIComponent(session);if(partnerReady){frame.contentWindow.postMessage({apex:'study-ask'},location.origin);askPending=false;}}
$('ask').onclick=askPartner;
$('close-partner').onclick=()=>{const frame=$('study-partner').querySelector('iframe');frame.contentWindow?.postMessage({apex:'hush'},location.origin);frame.contentWindow?.postMessage({apex:'voice',on:false},location.origin);$('study-partner').hidden=true;};
addEventListener('message',e=>{const frame=$('study-partner').querySelector('iframe');if(e.origin!==location.origin||e.source!==frame.contentWindow)return;if(e.data?.apex==='ready'){partnerReady=true;if(askPending){frame.contentWindow.postMessage({apex:'study-ask'},location.origin);askPending=false;}}});
$('login-form').onsubmit=async e=>{e.preventDefault();token=$('token').value.trim();try{await api('/api/study/model/dc-motor');try{localStorage.setItem('apex_token',token);}catch(_){}$('login').close();await boot();}catch(error){$('login-error').textContent=error.message;}};
addEventListener('keydown',e=>{if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)||$('login').open)return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();command(e.shiftKey?'redo':'undo');}if(e.key==='Escape')$('close-partner').click();});
boot().catch(e=>status(e.message));
