import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {setupStudyProjects} from './study-projects.js';
import {StudyHandController} from './study-hands.js';
import {setupStudyDiagnostics} from './study-diagnostics.js';
const $ = id => document.getElementById(id);
let token = '';
try { token = localStorage.getItem('apex_token') || ''; } catch (_) {}
const query = new URLSearchParams(location.search);
if (query.has('token')) { token = query.get('token'); try { localStorage.setItem('apex_token', token); } catch (_) {} query.delete('token'); history.replaceState(null, '', location.pathname + (query.size ? '?' + query : '')); }
let current = null, manifest = null, session = query.get('session'), timer = null, busy = Promise.resolve();
let scene, camera, renderer, orbit, cameraTween = null, rotorAngle = 0, amount = 0, targetAmount = 0;
let manipulation=null, handEnabled=false, handTimer=null, handEpoch=0, mouseUntil=0;
const handOwner=crypto.randomUUID();
const groups = new Map(), pickables = [], raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2();
const clipping = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const diagnostics=setupStudyDiagnostics({model:()=>manifest?.id,beforeStart:()=>{hands.reset('Recording started · hover again');cancelManipulation();}});
// Round insignificant OrbitControls drift out of the unsaved-change indicator.
const coordinates = vector => vector.toArray().map(n=>Math.round(n*1e5)/1e5);
const notebook = setupStudyProjects({
  api, ready:()=>!!current,
  capture:()=>({session_id:session, revision:current?.revision, model_hash:manifest?.model_hash,
    view:current && {selected:current.selected,hidden:current.hidden,isolated:current.isolated,explosion:current.explosion,section:current.section,rotating:current.rotating,transforms:current.transforms},
    camera:camera && {position:coordinates(camera.position),target:coordinates(orbit.target)},
    rotor_angle:Math.min(Math.PI*2,Math.round((rotorAngle%(Math.PI*2))*1e5)/1e5)}),
  prepareSave:async()=>{pauseHands();cancelManipulation();await busy;if(current?.rotating)await command('rotate');if(current?.rotating)throw new Error('Pause rotor motion before saving.');cameraTween=null;},
  restore:async result=>{
    clearTimeout(timer);$('close-partner').click();
    await loadModel(result.state.model);
    const frame=$('study-partner').querySelector('iframe');frame.removeAttribute('src');partnerReady=false;askPending=false;
    session=result.state.session_id;current=null;accept(result.state);
    cameraTween=null;camera.position.fromArray(result.workspace.camera.position);orbit.target.fromArray(result.workspace.camera.target);
    // Clear residual orbit damping before setting the saved camera again.
    const damping=orbit.enableDamping;orbit.enableDamping=false;orbit.update();
    camera.position.fromArray(result.workspace.camera.position);orbit.target.fromArray(result.workspace.camera.target);orbit.update();orbit.enableDamping=damping;
    rotorAngle=result.workspace.rotor_angle;amount=targetAmount;
    history.replaceState(null,'','/study?session='+encodeURIComponent(session)+'&model='+manifest.id);
    status('Saved study restored · rotor motion paused');schedulePoll();
  }
});
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
  if(action!=='transform'){hands.reset('View changed · hover again');cancelManipulation();}
  busy = busy.catch(() => {}).then(async () => {
    try { accept(await api('/api/study/session/' + session, {method:'POST',body:JSON.stringify({action,...extra})})); status('View updated · original geometry preserved'); return true; }
    catch (error) { status(error.message); targetAmount = current?.explosion || 0; $('separation').value = String(targetAmount * 100); return false; }
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
  for(const [id,group] of groups){group.userData.center=new THREE.Box3().setFromObject(group).getCenter(new THREE.Vector3());group.traverse(o=>{if(o.isMesh){o.userData.part=id;pickables.push(o);}});}
}
function fitCamera(animate = false) {
  const to = new THREE.Vector3(7.4,4.4,8.5).multiplyScalar(targetAmount > .2 ? 1.5 : 1);
  if (animate && !reducedMotion.matches) cameraTween = {from:camera.position.clone(),to,t:0};
  else { cameraTween=null; camera.position.copy(to); orbit.target.set(.45,0,0); orbit.update(); }
}
async function initScene(prefetched = null) {
  renderer = new THREE.WebGLRenderer({canvas:$('model'),alpha:true,antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.localClippingEnabled=true;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.55;
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(38,1,.05,100);
  scene.add(new THREE.HemisphereLight('#d5f4f4','#142330',2));
  const key=new THREE.DirectionalLight('#d1ebff',3);key.position.set(4,6,4);scene.add(key);
  const rim=new THREE.DirectionalLight('#63c6c0',2);rim.position.set(-4,2,-3);scene.add(rim);
  const grid=new THREE.GridHelper(28,28,'#25424b','#152c36');grid.position.y=-3.2;scene.add(grid);
  orbit=new OrbitControls(camera,renderer.domElement);orbit.enableDamping=true;orbit.dampingFactor=.09;orbit.minDistance=2.5;orbit.maxDistance=30;fitCamera();
  await buildLoadedModel(prefetched);
  orbit.addEventListener('start',()=>{cameraTween=null;});
  const resize=()=>{const r=$('viewport').getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();};
  new ResizeObserver(resize).observe($('viewport'));resize();
  let down=null;
  $('model').addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY,id:e.pointerId};});
  $('model').addEventListener('pointercancel',()=>{down=null;});
  $('model').addEventListener('pointerup',e=>{
    if($('interaction').value!=='select')return;
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
    diagnostics.metrics.frame(now,!document.hidden);
    if(document.hidden){before=now;requestAnimationFrame(draw);return;}
    const dt=Math.min(.05,(now-before)/1000||0);before=now;
    amount=reducedMotion.matches?targetAmount:THREE.MathUtils.damp(amount,targetAmount,7,dt);
    if(current?.rotating&&!reducedMotion.matches)rotorAngle+=dt*.8;
    if(cameraTween){cameraTween.t=Math.min(1,cameraTween.t+dt/0.65);const t=cameraTween.t;camera.position.lerpVectors(cameraTween.from,cameraTween.to,t*t*(3-2*t));if(t===1)cameraTween=null;}
    for(const group of groups.values())pose(group);
    orbit.update();renderer.render(scene,camera);
    const group=groups.get(current?.selected);const label=$('part-label');
    if(group?.visible){const centre=group.userData.center.clone().applyMatrix4(group.matrixWorld).project(camera);const r=$('viewport').getBoundingClientRect();label.hidden=centre.z< -1||centre.z>1||Math.abs(centre.x)>.92||Math.abs(centre.y)>.92;label.style.left=(centre.x+1)/2*r.width+'px';label.style.top=(-centre.y+1)/2*r.height+'px';}else label.hidden=true;
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  $('model').addEventListener('webglcontextlost',e=>{e.preventDefault();status('3D graphics paused. Reload this page to restore the view.');});
}
function renderList(){
  const list=$('component-list');list.replaceChildren();const filter=$('search').value.toLowerCase();
  for(const category of [...new Set(manifest.parts.map(p=>p.group))]){
    const parts=manifest.parts.filter(p=>p.group===category&&p.name.toLowerCase().includes(filter));if(!parts.length)continue;
    const title=document.createElement('div');title.className='component-group';title.textContent=category.toUpperCase();list.append(title);
    for(const p of parts){const b=document.createElement('button');b.className='component';b.dataset.part=p.id;b.title=(p.hierarchy||[p.name]).join(' / ');b.setAttribute('aria-pressed',String(p.id===current?.selected));b.classList.toggle('dimmed',current?.hidden.includes(p.id));const i=document.createElement('i'),t=document.createElement('span');t.textContent=p.name;b.append(i,t);b.onclick=()=>command('select',{part:p.id});list.append(b);}
  }
  if(!list.children.length){const p=document.createElement('p');p.textContent='No matching components.';list.append(p);}
}
function accept(state){
  if(current&&state.revision<current.revision)return;
  if(current&&state.revision===current.revision)return;
  if(manipulation && state.revision!==manipulation.revision){hands.reset('Study changed · movement cancelled');cancelManipulation();}
  const needsRoom = current && current.explosion <= .2 && state.explosion > .2;
  current=state;targetAmount=state.explosion;
  if (needsRoom && orbit) fitCamera(true);
  $('separation').value=String(state.explosion*100);$('separation-value').textContent=Math.round(state.explosion*100)+'%';
  $('section').setAttribute('aria-pressed',String(state.section));$('rotate').setAttribute('aria-pressed',String(state.rotating));
  const selected=manifest.parts.find(p=>p.id===state.selected);
  $('part-name').textContent=selected?.name||'Look inside.';$('part-group').textContent=selected?.group.toUpperCase()||'START EXPLORING';
  $('part-purpose').textContent=selected?.purpose||'Separate the assembly, select a component, and discover how it connects to the whole.';
  $('part-details').hidden=!selected;$('isolate').disabled=!selected;$('hide-part').disabled=!selected;$('reset-part').disabled=!selected;
  $('isolate').textContent=state.isolated?'Exit isolation':'Isolate';$('part-label').textContent=selected?.name||'';
  if(selected){$('part-connection').textContent=selected.connection;$('part-note').textContent=selected.model_note;$('part-source').href=manifest.sources.find(s=>s.id===selected.source).url;}
  for(const [id,g] of groups){g.visible=!state.hidden.includes(id)&&(!state.isolated||id===state.selected);g.traverse(o=>{if(o.isMesh){o.material.emissive.setHex(id===state.selected?0x1d5c57:0);o.material.emissiveIntensity=.38;o.material.clippingPlanes=state.section?[clipping]:[];}});}
  $('study-caption').textContent=(manifest.id==='dc-motor'?'Illustrative geometry · not to scale':'Source CAD · engineering review pending')+(state.section?' · uncapped section':state.rotating?' · illustrative rotor motion':'');
  $('rotate').disabled=manifest.id!=='dc-motor';
  renderList();
  notebook.selection(state.selected,selected?.name);
}
function schedulePoll(){
  clearTimeout(timer);
  async function poll(){const sid=session;try{if(!document.hidden){const s=await api('/api/study/session/'+sid);if(sid===session)accept(s);}}catch(e){if(sid!==session)return;status(e.message);if(e.status===401||e.status===404)return;}if(sid===session)timer=setTimeout(poll,500);}
  timer=setTimeout(poll,500);
}
async function boot(){
  clearTimeout(timer);
  const model=query.get('model')||'dc-motor';
  if(!session)session=(await api('/api/study/session',{method:'POST',body:JSON.stringify({model})})).session_id;
  let s, renewed=false;
  try{s=await api('/api/study/session/'+session);}catch(e){if(e.status!==404)throw e;session=(await api('/api/study/session',{method:'POST',body:JSON.stringify({model})})).session_id;s=await api('/api/study/session/'+session);renewed=true;}
  await loadModel(s.model);
  history.replaceState(null,'','/study?session='+encodeURIComponent(session)+'&model='+manifest.id);
  current=null;accept(s);
  status(renewed?'Previous study expired; a new assembly is open.':'Ready · select a component or take the motor apart');
  await notebook.initialize(query.get('project'));schedulePoll();
}
async function loadModel(id){
  if(manifest?.id===id && renderer)return;
  const data=await api('/api/study/model/'+id);
  let prefetched=null;
  if(data.asset){
    status('Loading detailed source geometry…');
    const loader=new GLTFLoader();loader.setRequestHeader({Authorization:'Bearer '+token});prefetched=await loader.loadAsync(data.asset);
    const ids=new Set();prefetched.scene.traverse(o=>{const index=prefetched.parser.associations.get(o)?.nodes;if(index!==undefined)ids.add(index);});
    if(data.parts.some(p=>!ids.has(p.node)))throw new Error('Source geometry does not match its component list.');
  }
  if(scene){for(const group of groups.values()){group.traverse(o=>{if(o.isMesh){o.geometry.dispose();o.material.dispose();}});scene.remove(group);}groups.clear();pickables.length=0;}
  diagnostics.stop();manifest=data;rotorAngle=0;
  if(!renderer)await initScene(prefetched);else await buildLoadedModel(prefetched);
  document.querySelector('h1').textContent=manifest.title;document.querySelector('.view-title p').textContent=manifest.subtitle;$('model-choice').value=manifest.id;
  $('part-count').textContent=String(manifest.parts.length);$('limitations').textContent=manifest.limitations;
  $('sources').replaceChildren();for(const source of manifest.sources){const a=document.createElement('a');a.href=source.url;a.textContent=source.title+(source.license?' · '+source.license:'')+' ↗';a.target='_blank';a.rel='noopener noreferrer';$('sources').append(a);}
  $('model-revision').textContent='Model revision '+manifest.revision;
  $('validation-list').replaceChildren();for(const [label,value] of Object.entries(manifest.validation)){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('validation-list').append(dt,dd);}
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
addEventListener('keydown',e=>{if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)||$('login').open||$('projects').open)return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();command(e.shiftKey?'redo':'undo');}if(e.key==='Escape')$('close-partner').click();});
boot().catch(e=>status(e.message));

function pose(group){
  const t=manipulation?.part===group.userData.id?manipulation.value:current?.transforms?.[group.userData.id];
  const base=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),group.userData.rotating?rotorAngle:0);
  const user=new THREE.Quaternion().setFromEuler(new THREE.Euler(...(t?.rotation||[0,0,0])));
  group.quaternion.copy(user).multiply(base);
  const center=group.userData.center;
  group.position.copy(group.userData.offset).multiplyScalar(amount).add(new THREE.Vector3(...(t?.position||[0,0,0])))
    .add(center.clone().applyQuaternion(base)).sub(center.clone().applyQuaternion(group.quaternion));
}
function pick(x,y){
  if(!renderer||!current)return null;
  scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);
  raycaster.setFromCamera(new THREE.Vector2(x*2-1,1-y*2),camera);
  const hits=raycaster.intersectObjects(pickables,false).filter(h=>groups.get(h.object.userData.part).visible&&(!current.section||clipping.distanceToPoint(h.point)>=0));
  return hits.find(h=>h.object.userData.part!=='housing')||hits[0]||null;
}
function beginManipulation(h,part){
  if(!current||manipulation||current.rotating||cameraTween||Math.abs(amount-targetAmount)>.02){status('Wait for motion to stop before moving a component.');return false;}
  const hit=pick(h.x,h.y);if(!hit||hit.object.userData.part!==part)return false;
  const mode=$('interaction').value;
  const value=JSON.parse(JSON.stringify(current.transforms?.[part]||{position:[0,0,0],rotation:[0,0,0]}));
  manipulation={part,mode,revision:current.revision,start:{...h},base:JSON.parse(JSON.stringify(value)),value,
    plane:new THREE.Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()),hit.point),point:hit.point.clone(),damping:orbit.enableDamping};
  orbit.enabled=false;orbit.enableDamping=false;orbit.update();return true;
}
function moveManipulation(h){
  const m=manipulation;if(!m||m.committing)return;
  if(m.mode==='move'){
    raycaster.setFromCamera(new THREE.Vector2(h.x*2-1,1-h.y*2),camera);
    const p=raycaster.ray.intersectPlane(m.plane,new THREE.Vector3());
    if(p)m.value.position=new THREE.Vector3(...m.base.position).add(p.sub(m.point)).toArray().map(n=>Math.max(-20,Math.min(20,n)));
  }else if(m.mode==='depth'){
    m.value.position=new THREE.Vector3(...m.base.position).add(camera.getWorldDirection(new THREE.Vector3()).multiplyScalar((h.y-m.start.y)*12)).toArray().map(n=>Math.max(-20,Math.min(20,n)));
  }else if(m.mode==='turn'){
    const wrap=n=>Math.atan2(Math.sin(n),Math.cos(n));
    m.value.rotation=[wrap(m.base.rotation[0]+(h.y-m.start.y)*Math.PI*2),wrap(m.base.rotation[1]+(h.x-m.start.x)*Math.PI*2),m.base.rotation[2]];
  }
}
function cancelManipulation(reason){
  if(manipulation?.input==='hand'&&!manipulation.committing)diagnostics.metrics.event('cancelled',manipulation.recording);
  if(manipulation&&orbit){orbit.enabled=true;orbit.enableDamping=manipulation.damping;}
  manipulation=null;if(reason)status(reason);
}
async function commitManipulation(){
  const m=manipulation;if(!m)return;m.committing=true;
  try{
    if(m.mode==='select')cancelManipulation();
    const applied=await (m.mode==='select'?command('select',{part:m.part}):command('transform',{part:m.part,transform:m.value,expected_revision:m.revision}));
    if(m.input==='hand')diagnostics.metrics.event(applied?'applied':'failed',m.recording);
  }finally{cancelManipulation();}
}
const hands=new StudyHandController({
  hit:(x,y)=>pick(x,y)?.object.userData.part,
  begin:(h,part)=>{const ok=beginManipulation(h,part);if(ok){manipulation.input='hand';manipulation.recording=diagnostics.metrics.active?diagnostics.metrics.data:null;diagnostics.metrics.event('grabs');}return ok;},move:moveManipulation,commit:commitManipulation,cancel:cancelManipulation,
  paint:(h,label)=>{const dot=$('hand-cursor');dot.hidden=!h;if(h){dot.style.left=h.x*100+'%';dot.style.top=h.y*100+'%';}$('hand-status').textContent=handEnabled?label:'';}
});
function pauseHands(reason='Hands paused'){
  const sid=session;const was=handEnabled;handEnabled=false;handEpoch++;clearTimeout(handTimer);hands.reset(reason);cancelManipulation();
  $('study-hands').textContent='Hands off';$('study-hands').setAttribute('aria-pressed','false');
  if(was)api('/api/study/session/'+sid+'/hands',{method:'POST',body:JSON.stringify({action:'release',owner:handOwner}),keepalive:true}).catch(()=>{});
}
async function enableHands(){
  if(!current)return;
  const epoch=++handEpoch;
  try{
    await api('/api/study/session/'+session+'/hands',{method:'POST',body:JSON.stringify({action:'claim',owner:handOwner}),signal:AbortSignal.timeout(2000)});
    if(epoch!==handEpoch)return;
    handEnabled=true;$('study-hands').textContent='Hands on';$('study-hands').setAttribute('aria-pressed','true');
    async function sample(){
      if(!handEnabled||epoch!==handEpoch)return;
      const requestStarted=performance.now();
      try{
        const data=await api('/api/study/session/'+session+'/hands',{method:'POST',body:JSON.stringify({action:'sample',owner:handOwner}),signal:AbortSignal.timeout(1000)});
        if(!handEnabled||epoch!==handEpoch)return;
        const blocked=!!(document.hidden||document.querySelector('dialog[open]')||!$('study-partner').hidden||/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)||performance.now()<mouseUntil);
        diagnostics.metrics.sample(data,performance.now()-requestStarted,blocked);
        if(blocked)hands.reset('Hands waiting · finish the current input');
        else hands.feed(data,performance.now());
      }catch(e){
        // An old request may fail after pause/re-enable. It must not stop the
        // new controller or pollute its diagnostics.
        if(!handEnabled||epoch!==handEpoch)return;
        diagnostics.metrics.event('connection_errors');pauseHands();status('Hand connection stopped. '+e.message);return;
      }
      // Target 30 samples/sec including request time, with only one request
      // in flight. Slow connections naturally lower the rate, never queue it.
      if(handEnabled&&epoch===handEpoch)handTimer=setTimeout(sample,Math.max(0,1000/30-(performance.now()-requestStarted)));
    }sample();
  }catch(e){status(e.message);}
}
$('study-hands').onclick=()=>handEnabled?pauseHands():enableHands();
$('reset-part').onclick=()=>command('reset_part');
$('interaction').onchange=()=>{hands.reset('Mode changed · hover again');cancelManipulation();$('interaction').blur();};
let mouseDrag=null;
function normalized(e){const r=$('model').getBoundingClientRect();return {x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height};}
$('model').addEventListener('pointerdown',e=>{
  hands.reset('Mouse in use');mouseUntil=performance.now()+1000;
  if(e.button!==0||$('interaction').value==='select')return;
  const h=normalized(e),part=pick(h.x,h.y)?.object.userData.part;
  if(part&&beginManipulation(h,part)){mouseDrag=e.pointerId;$('model').setPointerCapture(e.pointerId);e.stopImmediatePropagation();e.preventDefault();}
},true);
$('model').addEventListener('pointermove',e=>{if(mouseDrag===e.pointerId){mouseUntil=performance.now()+1000;moveManipulation(normalized(e));}},true);
$('model').addEventListener('pointerup',e=>{if(mouseDrag===e.pointerId){mouseDrag=null;mouseUntil=performance.now()+1000;commitManipulation();e.stopImmediatePropagation();}},true);
$('model').addEventListener('pointercancel',()=>{mouseDrag=null;cancelManipulation('Movement cancelled');});
addEventListener('blur',()=>{pauseHands();mouseDrag=null;cancelManipulation();});
addEventListener('pagehide',()=>pauseHands());
addEventListener('visibilitychange',()=>{if(document.hidden){diagnostics.metrics.frame(performance.now(),false);pauseHands();}});
addEventListener('keydown',e=>{if(e.key==='Escape'){hands.reset('Movement cancelled');mouseDrag=null;cancelManipulation('Movement cancelled');}});

async function buildLoadedModel(prefetched = null){
  if(manifest.id==='dc-motor'){buildMotor();return;}
  status('Loading detailed source geometry…');
  const loader=new GLTFLoader();loader.setRequestHeader({Authorization:'Bearer '+token});
  const gltf=prefetched||await loader.loadAsync(manifest.asset);gltf.scene.updateMatrixWorld(true);
  const box=new THREE.Box3().setFromObject(gltf.scene),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
  const scale=4.5/Math.max(size.x,size.y,size.z);
  const nodes=new Map();gltf.scene.traverse(o=>{const index=gltf.parser.associations.get(o)?.nodes;if(index!==undefined)nodes.set(index,o);});
  for(const p of manifest.parts){
    const node=nodes.get(p.node);if(!node)throw new Error('Source component missing: '+p.name);
    const g=part(p.id,[0,0,0]);
    node.traverse(o=>{if(!o.isMesh)return;
      const geo=o.geometry.clone().applyMatrix4(o.matrixWorld);geo.translate(-center.x,-center.y,-center.z);geo.scale(scale,scale,scale);
      const material=o.material.clone();material.side=THREE.DoubleSide;
      const item=new THREE.Mesh(geo,material);item.userData.part=p.id;g.add(item);pickables.push(item);
    });
    g.userData.center=new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3());
    g.userData.offset.copy(g.userData.center).multiplyScalar(1.1);
  }
}
$('model-choice').onchange=()=>{pauseHands();location.assign('/study?model='+encodeURIComponent($('model-choice').value));};
