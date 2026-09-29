import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {setupStudyProjects} from './study-projects.js';
import {setupStudyImport, autoExplode} from './study-import.js';
import {StudyHandController} from './study-hands.js';
import {setupStudyComfort} from './study-comfort.js';
import {setupStudyMirror} from './study-mirror.js';
import {setupStudyDiagnostics} from './study-diagnostics.js';
import {setupHoloScene, HoloHand, HoloSound, isSoftwareRenderer} from './study-holo.js';
import {TwoHandStretch, Spring, Coast, STRETCH_START_MS} from './study-gestures.js';
import {hasTracks, advance, sampleTrack, phaseAt} from './study-motion.js';
const $ = id => document.getElementById(id);
let token = '';
try { token = localStorage.getItem('apex_token') || ''; } catch (_) {}
const query = new URLSearchParams(location.search);
if (query.has('token')) { token = query.get('token'); try { localStorage.setItem('apex_token', token); } catch (_) {} query.delete('token'); history.replaceState(null, '', location.pathname + (query.size ? '?' + query : '')); }
let current = null, manifest = null, session = query.get('session'), timer = null, busy = Promise.resolve();
let scene, camera, renderer, orbit, cameraTween = null, rotorAngle = 0, amount = 0, targetAmount = 0;
// Sampled motion (study-motion.js): where in its cycle the subject is. It pauses in place.
let motionU = 0, ghosted = false, shownPhase = null;
let manipulation=null, handEnabled=false, handTimer=null, handEpoch=0, mouseUntil=0;
// Phase 2a: the hologram look (study-holo.js). Presentation only.
let holo=null, readyKey=null;
const holoHand=new HoloHand($('holo-hand')), sound=new HoloSound();
// Phase 2b: two-hand pull-apart (springy), spin momentum, and the part you point at.
const stretch=new TwoHandStretch(), spring=new Spring(0), coast=new Coast();
let springActive=false, pointed=null;
const handOwner=crypto.randomUUID();
const groups = new Map(), pickables = [], raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2();
const clipping = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const diagnostics=setupStudyDiagnostics({model:()=>manifest?.id,beforeStart:()=>{hands.reset('Recording started · hover again');cancelManipulation();}});
setupStudyMirror({token:()=>token});
// Round insignificant OrbitControls drift out of the unsaved-change indicator.
const coordinates = vector => vector.toArray().map(n=>Math.round(n*1e5)/1e5);
const notebook = setupStudyProjects({
  api, ready:()=>!!current,
  capture:()=>({session_id:session, revision:current?.revision, model_hash:manifest?.model_hash,
    view:current && {selected:current.selected,hidden:current.hidden,isolated:current.isolated,explosion:current.explosion,section:current.section,rotating:current.rotating,transforms:current.transforms},
    camera:camera && {position:coordinates(camera.position),target:coordinates(orbit.target)},
    rotor_angle:Math.min(Math.PI*2,Math.round(((hasTracks(manifest?.motion)?motionU*Math.PI*2:rotorAngle)%(Math.PI*2))*1e5)/1e5)}),
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
    rotorAngle=result.workspace.rotor_angle;motionU=hasTracks(manifest.motion)?rotorAngle/(Math.PI*2)%1:0;amount=targetAmount;
    history.replaceState(null,'','/study?session='+encodeURIComponent(session)+'&model='+manifest.id);
    status('Saved study restored · rotor motion paused');schedulePoll();
  }
});
function status(text) { $('status').textContent = text; }
const importer = setupStudyImport({token:()=>token, api, status, beforeLeave:()=>pauseHands()});
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
    try { if(!accept(await api('/api/study/session/' + session, {method:'POST',body:JSON.stringify({action,...extra})})))status('View updated · original geometry preserved'); return true; }
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
  coast.stop();
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
  let glName='';try{const gl=renderer.getContext(),dbg=gl.getExtension('WEBGL_debug_renderer_info');glName=dbg?gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL):'';}catch(_){}
  holo=setupHoloScene({THREE,scene,camera,renderer,groups,reducedMotion,software:isSoftwareRenderer(glName),look:window.ApexLook?.get()||'futuristic',
    onBloomPaused:()=>status('Glow paused · this device renders slowly, and responsive hands come first. Edges stay on.'),
    onHoloPaused:()=>{syncHoloButtons();status('3D hologram paused for this session · rendering is too slow for responsive hands. Switch the look to Normal and back to retry.');}});
  if(holo.startedOff)setTimeout(()=>status('3D hologram off · no graphics acceleration detected, and responsive hands come first. The rest of the futuristic look stays.'),0);
  await buildLoadedModel(prefetched);holo.buildEdges();syncHoloButtons();
  orbit.addEventListener('start',()=>{cameraTween=null;coast.stop();});
  const resize=()=>{const r=$('viewport').getBoundingClientRect();renderer.setSize(r.width,r.height,false);holo.resize(r.width,r.height);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();};
  new ResizeObserver(resize).observe($('viewport'));resize();
  let down=null;
  $('model').addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY,id:e.pointerId};});
  $('model').addEventListener('pointercancel',()=>{down=null;});
  $('model').addEventListener('pointerup',e=>{
    if($('interaction').value!=='select')return;
    if(!down||down.id!==e.pointerId||Math.hypot(e.clientX-down.x,e.clientY-down.y)>5){down=null;return;}down=null;
    const r=$('model').getBoundingClientRect();mouse.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(mouse,camera);
    const hits=raycaster.intersectObjects(pickables,false).filter(h=>groups.get(h.object.userData.part).visible&&!groups.get(h.object.userData.part).userData.ghosted&&(!current.section||clipping.distanceToPoint(h.point)>=0));
    // The transparent housing lets you pick the visible internals; select
    // the shell from the tree if it lies in front of the part you want.
    const hit=hits.find(h=>h.object.userData.part!=='housing')||hits[0];
    if(hit)command('select',{part:hit.object.userData.part});
  });
  let before=0;
  function draw(now){
    diagnostics.metrics.frame(now,!document.hidden);
    if(document.hidden){before=now;requestAnimationFrame(draw);return;}
    const dt=Math.min(.05,(now-before)/1000||0),realDt=Math.min(.25,(now-before)/1000||0);before=now;
    if(reducedMotion.matches){amount=targetAmount;springActive=false;}
    // The spring sub-steps itself, so it can follow real time even on a slow renderer.
    else if(springActive){amount=Math.max(0,spring.step(targetAmount,realDt));if(!stretch.engaged&&spring.settled(targetAmount)){springActive=false;amount=targetAmount;}}
    else amount=THREE.MathUtils.damp(amount,targetAmount,7,realDt);
    const spin=reducedMotion.matches?null:coast.step(dt);
    if(spin&&!manipulation){const sphere=new THREE.Spherical().setFromVector3(camera.position.clone().sub(orbit.target));sphere.theta+=spin.theta;sphere.phi+=spin.phi;sphere.makeSafe();camera.position.copy(orbit.target).add(new THREE.Vector3().setFromSpherical(sphere));}
    if(current?.rotating&&!reducedMotion.matches){if(hasTracks(manifest.motion))motionU=advance(motionU,realDt,manifest.motion);else rotorAngle+=dt*.8;}
    // Timed animations run on real time, so a slow renderer does not stretch them (and block hands) for seconds.
    if(cameraTween){cameraTween.t=Math.min(1,cameraTween.t+realDt/0.65);const t=cameraTween.t;camera.position.lerpVectors(cameraTween.from,cameraTween.to,t*t*(3-2*t));if(t===1)cameraTween=null;}
    holo.update(dt);
    const live=tracksLive();
    for(const group of groups.values())pose(group,live);
    paintMotion(live);
    orbit.update();holo.render();holoHand.draw(now);
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
  if(stretch.engaged&&current&&state.revision!==current.revision){stretch.cancel();$('hand-status').textContent='Study changed · separation cancelled';}
  const needsRoom = current && current.explosion <= .2 && state.explosion > .2;
  const started = !!current && !current.rotating && state.rotating;
  current=state;if(!stretch.engaged)targetAmount=state.explosion;
  if (needsRoom && orbit) fitCamera(true);
  $('separation').value=String(state.explosion*100);$('separation-value').textContent=Math.round(state.explosion*100)+'%';
  $('section').setAttribute('aria-pressed',String(state.section));$('rotate').setAttribute('aria-pressed',String(state.rotating));
  const selected=manifest.parts.find(p=>p.id===state.selected);
  $('part-name').textContent=selected?.name||'Look inside.';$('part-group').textContent=selected?.group.toUpperCase()||'START EXPLORING';
  $('part-purpose').textContent=selected?.purpose||'Separate the assembly, select a component, and discover how it connects to the whole.';
  $('part-details').hidden=!selected;$('isolate').disabled=!selected;$('hide-part').disabled=!selected;$('reset-part').disabled=!selected;
  $('isolate').textContent=state.isolated?'Exit isolation':'Isolate';$('part-label').textContent=selected?.name||'';
  if(selected){$('part-connection').textContent=selected.connection;$('part-note').textContent=selected.model_note;const url=manifest.sources.find(s=>s.id===selected.source)?.url;$('part-source').hidden=!url;if(url)$('part-source').href=url;}
  for(const [id,g] of groups){g.visible=!state.hidden.includes(id)&&(!state.isolated||id===state.selected);g.traverse(o=>{if(o.isMesh){restEmissive(o,id===state.selected);o.userData.motionLit=false;o.material.clippingPlanes=state.section?[clipping]:[];}});}
  holo?.rebase();
  shownPhase=null;$('study-caption').textContent=captionText(null);
  $('rotate').disabled=!hasMotion();
  renderList();
  notebook.selection(state.selected,selected?.name);
  // Starting a subject's motion says what to watch (true: the caller keeps it on screen).
  if(started&&manifest.motion?.hint&&!state.section){status(manifest.motion.hint);return true;}
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
  status(renewed?'Previous study expired; a new one is open.':'Ready · select a component or take it apart');
  await notebook.initialize(query.get('project'));schedulePoll();
}
const hasMotion=()=>manifest?.id==='dc-motor'||!!manifest?.motion;
// The study library: every subject in data/assemblies, grouped by category.
let library=null;
async function fillLibrary(){
  if(library)return;
  try{library=(await api('/api/study/models')).models;}catch(_){return;}
  const select=$('model-choice'),groups=new Map();select.replaceChildren();
  for(const m of library){
    if(!groups.has(m.category)){const g=document.createElement('optgroup');g.label=m.category;groups.set(m.category,g);select.append(g);}
    const o=document.createElement('option');o.value=m.id;o.textContent=m.title+' · '+m.parts+' parts';o.title=m.summary||m.subtitle;groups.get(m.category).append(o);
  }
}
async function loadModel(id){
  if(manifest?.id===id && renderer)return;
  const data=await api('/api/study/model/'+id);
  let prefetched=null;
  if(data.asset){
    status('Loading detailed source geometry…');
    const loader=new GLTFLoader();loader.setRequestHeader({Authorization:'Bearer '+token});prefetched=await loader.loadAsync(data.asset);
    const ids=new Set();prefetched.scene.traverse(o=>{const index=prefetched.parser.associations.get(o)?.nodes;if(index!==undefined)ids.add(index);});
    if(data.parts.some(p=>(p.nodes||[p.node]).some(n=>!ids.has(n))))throw new Error('Source geometry does not match its component list.');
  }
  if(scene){for(const group of groups.values()){group.traverse(o=>{if(o.isMesh){o.geometry.dispose();o.material.dispose();}});scene.remove(group);}groups.clear();pickables.length=0;}
  diagnostics.stop();manifest=data;rotorAngle=0;motionU=0;ghosted=false;shownPhase=null;
  if(!renderer)await initScene(prefetched);else {await buildLoadedModel(prefetched);holo.buildEdges();}
  document.querySelector('h1').textContent=manifest.title;document.querySelector('.view-title p').textContent=manifest.subtitle;await fillLibrary();$('model-choice').value=manifest.id;
  $('rotate').textContent=manifest.motion?.label||(manifest.id==='dc-motor'?'Rotor motion':'Motion');document.title=manifest.title+' · Apex study';
  $('part-count').textContent=String(manifest.parts.length);$('limitations').textContent=manifest.limitations;
  $('sources').replaceChildren();for(const source of manifest.sources){const label=source.title+(source.license?' · '+source.license:'');
    if(!source.url){const span=document.createElement('p');span.textContent=label;$('sources').append(span);continue;}
    const a=document.createElement('a');a.href=source.url;a.textContent=label+' ↗';a.target='_blank';a.rel='noopener noreferrer';$('sources').append(a);}
  importer.show(manifest);
  $('model-revision').textContent='Model revision '+manifest.revision;
  $('validation-list').replaceChildren();for(const [label,value] of Object.entries(manifest.validation)){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('validation-list').append(dt,dd);}
}
$('search').oninput=renderList;
$('explode').onclick=()=>command('explode');$('assemble').onclick=()=>command('assemble');
$('section').onclick=()=>command('section');$('rotate').onclick=()=>command('rotate');
$('isolate').onclick=()=>command('isolate');$('hide-part').onclick=()=>command('hide');$('show-all').onclick=()=>command('show_all');
$('undo').onclick=()=>command('undo');$('redo').onclick=()=>command('redo');$('reset-camera').onclick=()=>{hands.reset('View reset · hover again');cancelManipulation();if(orbit)fitCamera();};
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

// Motion runs on the assembled subject; exploding, isolating or moving a part
// shows its rest pose (a paused cycle comes back when you reassemble).
function tracksLive(){
  return hasTracks(manifest?.motion)&&!!current&&(current.rotating||motionU>0)&&!current.explosion&&!current.isolated&&!Object.keys(current.transforms||{}).length;
}
function pose(group,live=tracksLive()){
  const t=manipulation?.part===group.userData.id?manipulation.value:current?.transforms?.[group.userData.id];
  const spin=group.userData.spin,track=live?group.userData.track:null,center=group.userData.center;
  // The motion moves the part (turn, uniform scale about a pivot, offset); your
  // own turn then happens about where the motion put the part's centre.
  const base=new THREE.Quaternion();let s=1;const moved=center.clone();
  if(spin){base.setFromAxisAngle(spin.axis,group.userData.rotating?rotorAngle*(spin.speed||1):0);moved.sub(spin.pivot).applyQuaternion(base).add(spin.pivot);}
  else if(track){const m=sampleTrack(track.data,motionU,manifest.motion.samples);base.setFromAxisAngle(track.axis,m.angle);s=m.scale;
    moved.sub(track.pivot).multiplyScalar(s).applyQuaternion(base).add(track.pivot).add(new THREE.Vector3(...m.offset).multiplyScalar(track.unit));}
  const user=new THREE.Quaternion().setFromEuler(new THREE.Euler(...(t?.rotation||[0,0,0])));
  group.quaternion.copy(user).multiply(base);group.scale.setScalar(s);
  group.position.copy(group.userData.offset).multiplyScalar(amount).add(new THREE.Vector3(...(t?.position||[0,0,0])))
    .add(moved).sub(center.clone().multiplyScalar(s).applyQuaternion(group.quaternion));
  // Hologram: a part ready to grab rises toward you (study-holo.js). View only.
  const lift=group.userData.holoLift||0;
  if(lift>1e-4)group.position.add(camera.position.clone().sub(group.position.clone().add(center)).normalize().multiplyScalar(lift));
}
// A part's resting glow: the selection tint, else whatever its file gave it.
function restEmissive(o,selected){
  const look=o.userData.look;
  if(selected){o.material.emissive.setHex(0x1d5c57);o.material.emissiveIntensity=.38;}
  else{o.material.emissive.setHex(look?.emissive??0);o.material.emissiveIntensity=look?.intensity??.38;}
}
// While the motion shows: parts glow on cue (a piston's power stroke, an open
// valve), the casings the subject names turn see-through, and the caption says
// what is happening.
function paintMotion(live){
  const motion=manifest?.motion;if(!hasTracks(motion))return;
  for(const [id,g] of groups){const track=g.userData.track;if(!track?.data.glow)continue;
    const v=live?sampleTrack(track.data,motionU,motion.samples).glow:0;
    g.traverse(o=>{if(!o.isMesh||o.userData.holoEdge)return;
      if(v>.01){o.material.emissive.copy(track.glow).multiplyScalar(Math.min(1,v));o.material.emissiveIntensity=1.4;o.userData.motionLit=true;}
      else if(o.userData.motionLit){restEmissive(o,id===current?.selected);o.userData.motionLit=false;}});}
  if(live!==ghosted){ghosted=live;
    for(const id of motion.ghost||[]){const g=groups.get(id);if(!g)continue;g.userData.ghosted=live;
      g.traverse(o=>{if(!o.isMesh||o.userData.holoEdge)return;const look=o.userData.look;
        o.material.transparent=live||!!look?.transparent;o.material.opacity=live?.13:look?.opacity??1;o.material.depthWrite=!live&&(look?.depthWrite??true);o.material.needsUpdate=true;});}}
  const phase=live?phaseAt(motion,motionU):null;
  if(phase!==shownPhase){shownPhase=phase;$('study-caption').textContent=captionText(phase);}
}
function captionText(phase){
  const base=manifest.caption||(manifest.id==='dc-motor'?'Illustrative geometry · not to scale':'Source CAD · engineering review pending');
  if(phase)return phase+(current?.section?' · section':'')+(current?.rotating?'':' · paused');
  if(current?.section)return base+' · uncapped section';
  return base+(current?.rotating?' · illustrative motion':'');
}
function pick(x,y){
  if(!renderer||!current)return null;
  scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);
  raycaster.setFromCamera(new THREE.Vector2(x*2-1,1-y*2),camera);
  const hits=raycaster.intersectObjects(pickables,false).filter(h=>groups.get(h.object.userData.part).visible&&!groups.get(h.object.userData.part).userData.ghosted&&(!current.section||clipping.distanceToPoint(h.point)>=0));
  return hits.find(h=>h.object.userData.part!=='housing')||hits[0]||null;
}
// A small screen-space target margin for fingers; mouse picking stays exact.
// During pinch closure prefer the already armed component within that margin.
function pickHand(x,y,preferred=null){
  const direct=pick(x,y);
  if(direct&&(!preferred||direct.object.userData.part===preferred))return direct;
  const r=$('model').getBoundingClientRect();
  if(!r.width||!r.height)return direct;
  let nearest=direct;
  for(const radius of [10,20])for(let i=0;i<8;i++){
    const angle=i*Math.PI/4,nx=x+Math.cos(angle)*radius/r.width,ny=y+Math.sin(angle)*radius/r.height;
    if(nx<0||nx>1||ny<0||ny>1)continue;
    const hit=pick(nx,ny);if(!hit)continue;
    if(!preferred||hit.object.userData.part===preferred)return hit;
    nearest??=hit;
  }
  return nearest;
}
function beginManipulation(h,part,input='mouse'){
  if(!current||manipulation||current.rotating||cameraTween||Math.abs(amount-targetAmount)>.02){status('Wait for motion to stop before moving a component.');return false;}
  coast.stop();
  const mode=part==='@spin'?'orbit':$('interaction').value;
  if(mode==='orbit'||mode==='zoom'){
    const damping=orbit.enableDamping;orbit.enableDamping=false;orbit.update();orbit.enabled=false;
    manipulation={mode,view:true,revision:current.revision,start:{...h},position:camera.position.clone(),target:orbit.target.clone(),damping,
      spherical:new THREE.Spherical().setFromVector3(camera.position.clone().sub(orbit.target))};
    return true;
  }
  const hit=input==='hand'?pickHand(h.x,h.y,part):pick(h.x,h.y);if(!hit||hit.object.userData.part!==part)return false;
  const value=JSON.parse(JSON.stringify(current.transforms?.[part]||{position:[0,0,0],rotation:[0,0,0]}));
  manipulation={part,mode,revision:current.revision,start:{...h},base:JSON.parse(JSON.stringify(value)),value,
    plane:new THREE.Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()),hit.point),point:hit.point.clone(),damping:orbit.enableDamping};
  // The assisted hit can be beside the fingertip. Start at the fingertip's
  // projection onto the same plane so the first motion cannot snap the part.
  raycaster.setFromCamera(new THREE.Vector2(h.x*2-1,1-h.y*2),camera);
  raycaster.ray.intersectPlane(manipulation.plane,manipulation.point);
  orbit.enabled=false;orbit.enableDamping=false;orbit.update();return true;
}
function moveManipulation(h){
  const m=manipulation;if(!m||m.committing)return;
  if(m.view){
    const sphere=m.spherical.clone();
    if(m.mode==='orbit'){sphere.theta-=(h.x-m.start.x)*Math.PI*2;sphere.phi-=(h.y-m.start.y)*Math.PI;sphere.makeSafe();if(m.input==='hand')coast.track(sphere.theta,sphere.phi,performance.now());}
    else sphere.radius=Math.max(orbit.minDistance,Math.min(orbit.maxDistance,sphere.radius*Math.exp((h.y-m.start.y)*3)));
    camera.position.copy(m.target).add(new THREE.Vector3().setFromSpherical(sphere));orbit.update();
  }else if(m.mode==='move'){
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
  if(manipulation?.input==='hand'&&!manipulation.committing){diagnostics.metrics.event('cancelled',manipulation.recording);sound.play('cancel');}
  if(manipulation?.view&&!manipulation.committing){camera.position.copy(manipulation.position);orbit.target.copy(manipulation.target);orbit.update();}
  if(manipulation&&orbit){orbit.enabled=true;orbit.enableDamping=manipulation.damping;}
  manipulation=null;if(reason)status(reason);
}
async function commitManipulation(){
  const m=manipulation;if(!m)return;m.committing=true;
  if(m.input==='hand')sound.play('release');
  try{
    if(m.view){if(m.input==='hand')diagnostics.metrics.event('applied',m.recording);
      const spinning=m.input==='hand'&&m.mode==='orbit'&&!reducedMotion.matches&&coast.release(performance.now());
      status(spinning?'Spinning · it slows on its own; pinch or drag to stop':'View adjusted · component positions unchanged');return;}
    if(m.mode==='select')cancelManipulation();
    const applied=await (m.mode==='select'?command('select',{part:m.part}):command('transform',{part:m.part,transform:m.value,expected_revision:m.revision}));
    if(m.input==='hand')diagnostics.metrics.event(applied?'applied':'failed',m.recording);
  }finally{cancelManipulation();}
}
let fingerGuide=false;
const tipNodes=new Map();
for(const name of ['thumb','index','middle','ring','pinky']){
  const node=document.createElement('span');node.className='finger-tip';node.textContent=name[0].toUpperCase();node.hidden=true;$('finger-guide').append(node);tipNodes.set(name,node);
}
function paintFingers(h){
  $('finger-guide').hidden=!fingerGuide||!h;
  for(const [name,node] of tipNodes){const point=h?.fingertips?.[name];node.hidden=!point;if(point){node.style.left=point[0]*100+'%';node.style.top=point[1]*100+'%';}}
  $('pinch-feedback').textContent=!h?'Show your hand to the camera.':h.ratio==null?'Finger positions unclear · face your palm toward the camera.':`${h.pinched?'Pinch recognised':'Fingers detected'} · gap ${h.ratio.toFixed(2)} / pinch below ${h.threshold?.toFixed(2)??'—'}`;
}
$('finger-guide-toggle').onclick=()=>{fingerGuide=!fingerGuide;$('finger-guide-toggle').textContent=fingerGuide?'Hide finger guide':'Show finger guide';$('finger-guide-toggle').setAttribute('aria-pressed',String(fingerGuide));if(!fingerGuide)$('finger-guide').hidden=true;};
const hands=new StudyHandController({
  route:(h,now)=>comfort.route(h,now),
  // Pinching empty space spins the view (with momentum); parts stay as they are.
  hit:(x,y,preferred)=>['orbit','zoom'].includes($('interaction').value)?'@view':pickHand(x,y,preferred==='@spin'?null:preferred)?.object.userData.part||'@spin',
  begin:(h,part)=>{const ok=beginManipulation(h,part,'hand');if(ok){sound.play('grab');if(!manipulation.view)holo?.pulse(part);manipulation.input='hand';manipulation.startedAt=performance.now();manipulation.recording=diagnostics.metrics.active?diagnostics.metrics.data:null;diagnostics.metrics.event('grabs');}return ok;},move:moveManipulation,commit:commitManipulation,cancel:cancelManipulation,
  paint:(h,label,target={})=>{
    const dot=$('hand-cursor');dot.hidden=!h;
    if(h){dot.style.left=h.x*100+'%';dot.style.top=h.y*100+'%';dot.dataset.state=h.pinched?'pinched':target.progress===1?'ready':'tracking';}
    const held=manipulation&&!manipulation.view&&manipulation.input==='hand'?manipulation.part:null;
    holo?.setFocus(held||target.part,held?1:target.progress,!!held);
    holoHand.set(h,held||manipulation?.view&&manipulation.input==='hand'?'held':h?.pinched?'pinched':'tracking',performance.now());
    const key=target.progress===1&&!held&&!target.part?.startsWith('@')?target.part:null;
    if(key&&key!==readyKey)sound.play('ready');readyKey=key;
    const name=target.part==='@spin'?'empty space · pinch and drag to spin':manifest?.parts.find(p=>p.id===target.part)?.name;
    if(target.part&&target.progress===1&&!target.part.startsWith('@'))pointed=target.part;
    $('hand-status').textContent=handEnabled?`${$('interaction').selectedOptions[0].textContent} · ${label}${name?' · '+name:''}`:'Hand controls are paused · choose Enable hands';
    paintFingers(h);
  }
});
const comfort=setupStudyComfort({enabled:()=>handEnabled,reset:reason=>{hands.reset(reason);cancelManipulation();},paint:(h,label)=>hands.cb.paint(h,label),setMode});
function setMode(mode){
  sound.play('mode');
  hands.reset('Mode changed · hover again');cancelManipulation();$('interaction').value=mode;comfort.syncMode(mode);
  hands.cb.paint(null,'Mode selected · hover open, then pinch');
}
function feedStudyHands(data,now){const mapped=comfort.prepare(data,now);if(mapped&&!handleStretch(mapped,now))hands.feed(mapped,now);}
// Two hands pinched together: pull apart to separate the model, push together
// to reassemble; the model follows on a spring. Let go to keep it; a fist cancels.
function handleStretch(sample,now){
  const fresh=sample.tracking&&sample.age_ms!=null&&sample.age_ms<=350;
  if(!fresh){if(stretch.engaged){stretch.cancel();targetAmount=current?.explosion||0;sound.play('cancel');}return false;}
  // The camera's zoom-out after separating may still be running; that must not block the next pull.
  // Real hands never pinch in the same instant: the first pinch starts a one-hand
  // grab (a part, or spinning the view) a moment before the second arrives. A
  // one-hand grab that began moments ago may be taken over — it is cancelled and
  // put back — so the two-hand pull works without delaying one-hand grabs.
  const freshGrab=!!manipulation&&manipulation.input==='hand'&&!manipulation.committing&&performance.now()-(manipulation.startedAt||0)<=STRETCH_START_MS;
  const allowed=!!current&&!current.rotating&&(!manipulation&&!hands.held||freshGrab);
  const r=stretch.feed(sample.hands,now,current?.explosion||0,allowed);
  if(!r)return false;
  if(r.state==='start'){if(manipulation){hands.reset('Two hands · one-hand grab handed over');cancelManipulation();}else hands.reset('Two hands');coast.stop();spring.set(amount);springActive=true;sound.play('grab');}
  // Make room while pulling, not only after letting go (the camera eases out once).
  if(r.amount>.2&&targetAmount<=.2&&(current?.explosion||0)<=.2){targetAmount=r.amount;fitCamera(true);}
  targetAmount=r.amount;springActive=true;
  $('separation').value=String(Math.round(r.amount*100));$('separation-value').textContent=Math.round(r.amount*100)+'%';
  const h=(sample.hands||[]).find(h=>h.pinched)||sample.hands?.[0];
  holoHand.set(h,r.state==='commit'||r.state==='cancel'?'tracking':'held',performance.now());
  $('hand-status').textContent='Two hands · '+r.label;
  if(r.state==='commit'){sound.play('release');if(Math.abs(r.amount-(current?.explosion||0))>.005)command('explode',{amount:r.amount});}
  if(r.state==='cancel')sound.play('cancel');
  // The hand line updates every frame; keep the outcome readable on the status line.
  if(r.state==='commit'||r.state==='cancel')status(r.label);
  return true;
}
function pauseHands(reason='Hands paused'){
  comfort.stop();
  if(stretch.engaged){stretch.cancel();targetAmount=current?.explosion||0;}coast.stop();pointed=null;
  const sid=session;const was=handEnabled;handEnabled=false;handEpoch++;clearTimeout(handTimer);hands.reset(reason);cancelManipulation();holoHand.clear();holo?.setFocus(null,0,false);
  $('study-hands').textContent='Enable hands';$('study-hands').setAttribute('aria-pressed','false');
  if(was)api('/api/study/session/'+sid+'/hands',{method:'POST',body:JSON.stringify({action:'release',owner:handOwner}),keepalive:true}).catch(()=>{});
}
async function enableHands(){
  if(!current)return;
  const epoch=++handEpoch;
  try{
    await api('/api/study/session/'+session+'/hands',{method:'POST',body:JSON.stringify({action:'claim',owner:handOwner}),signal:AbortSignal.timeout(2000)});
    if(epoch!==handEpoch)return;
    handEnabled=true;comfort.show();$('study-hands').textContent='Pause hands';$('study-hands').setAttribute('aria-pressed','true');
    async function sample(){
      if(!handEnabled||epoch!==handEpoch)return;
      const requestStarted=performance.now();
      try{
        const data=await api('/api/study/session/'+session+'/hands',{method:'POST',body:JSON.stringify({action:'sample',owner:handOwner,pointed:takePointed()}),signal:AbortSignal.timeout(1000)});
        if(!handEnabled||epoch!==handEpoch)return;
        const blocked=!!(document.hidden||document.querySelector('dialog[open]')||!$('study-partner').hidden||/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)||performance.now()<mouseUntil);
        diagnostics.metrics.sample(data,performance.now()-requestStarted,blocked);
        if(blocked){comfort.blocked();hands.reset('Hands waiting · finish the current input');}
        else feedStudyHands({...data,age_ms:Number.isFinite(data.age_ms)?data.age_ms+(performance.now()-requestStarted):null},performance.now());
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
// The part the open hand is hovering over, sent once per sample so Céline can
// resolve "this" (agent/assembly.py keeps it with its age).
function takePointed(){const p=pointed;pointed=null;return p;}
function syncHoloButtons(){
  document.body.dataset.holo=holo?.on?'on':'off';
  $('sound-toggle').setAttribute('aria-pressed',String(sound.on));$('sound-toggle').textContent=sound.on?'Sound on':'Sound off';
}
// The header's look switch is Apex-wide (theme.js); the 3D hologram follows it.
addEventListener('apex:look',e=>{if(!holo)return;holo.setLook(e.detail);syncHoloButtons();
  if(e.detail==='futuristic'&&holo.software)status('3D hologram off · no graphics acceleration detected. The rest of the futuristic look is on.');});
$('sound-toggle').onclick=()=>{sound.toggle();sound.unlock();syncHoloButtons();};
// Audio may start only after the page is clicked or a key pressed.
for(const kind of ['pointerdown','keydown'])addEventListener(kind,()=>sound.unlock(),{once:false,passive:true});
syncHoloButtons();
$('reset-part').onclick=()=>command('reset_part');
$('interaction').onchange=()=>{setMode($('interaction').value);$('interaction').blur();};
let mouseDrag=null;
function normalized(e){const r=$('model').getBoundingClientRect();return {x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height};}
$('model').addEventListener('pointerdown',e=>{
  hands.reset('Mouse in use');mouseUntil=performance.now()+1000;
  if(e.button!==0||$('interaction').value==='select')return;
  const h=normalized(e),part=['orbit','zoom'].includes($('interaction').value)?'@view':pick(h.x,h.y)?.object.userData.part;
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
  const built=[];
  for(const p of manifest.parts){
    // A component is one node of the file, or several (imported repeats: "Bolt ×12").
    const members=(p.nodes||[p.node]).map(n=>nodes.get(n));if(members.some(n=>!n))throw new Error('Source component missing: '+p.name);
    const g=part(p.id,[0,0,0]);
    for(const node of members)node.traverse(o=>{if(!o.isMesh)return;
      const geo=o.geometry.clone().applyMatrix4(o.matrixWorld);geo.translate(-center.x,-center.y,-center.z);geo.scale(scale,scale,scale);
      const material=o.material.clone();material.side=THREE.DoubleSide;
      const item=new THREE.Mesh(geo,material);item.userData.part=p.id;g.add(item);pickables.push(item);
      item.userData.look={emissive:material.emissive.getHex(),intensity:material.emissive.getHex()?material.emissiveIntensity:.38,transparent:material.transparent,opacity:material.opacity,depthWrite:material.depthWrite};
    });
    g.userData.center=new THREE.Box3().setFromObject(g).getCenter(new THREE.Vector3());
    // A subject can say where each part goes when taken apart; otherwise it moves out from the centre.
    if(Array.isArray(p.explode))g.userData.offset.set(...p.explode).multiplyScalar(scale);
    else g.userData.offset.copy(g.userData.center).multiplyScalar(1.1);
    built.push(g);
    const speed=manifest.motion?.parts?.[p.id];
    if(speed){g.userData.rotating=true;g.userData.spin={speed,axis:new THREE.Vector3(...(manifest.motion.axis||[1,0,0])).normalize(),
      pivot:new THREE.Vector3(...(manifest.motion.pivot||[0,0,0])).sub(center).multiplyScalar(scale)};}
    const track=hasTracks(manifest.motion)&&manifest.motion.tracks[p.id];
    if(track)g.userData.track={data:track,unit:scale,glow:new THREE.Color(track.glow_color||'#ffffff'),
      axis:new THREE.Vector3(...(track.axis||[1,0,0])).normalize(),pivot:new THREE.Vector3(...(track.pivot||[0,0,0])).sub(center).multiplyScalar(scale)};
  }
  if(manifest.auto_explode)autoExplode(THREE,built.map(g=>({center:g.userData.center,offset:g.userData.offset})),size.clone().multiplyScalar(scale));
}
$('model-choice').onchange=()=>{pauseHands();location.assign('/study?model='+encodeURIComponent($('model-choice').value));};
