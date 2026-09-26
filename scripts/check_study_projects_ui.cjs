// No WebGL required: verify notebook save errors, edits during save, conflicts,
// safe rendering and failed opens against the actual project UI module.
const {JSDOM}=require('jsdom');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const dom=new JSDOM(fs.readFileSync(path.join(base,'study.html'),'utf8'),{url:'http://localhost/study',runScripts:'outside-only'});
const w=dom.window,$=id=>w.document.getElementById(id),sleep=ms=>new Promise(r=>setTimeout(r,ms));
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
const calls=[],pins=[];let rejectSave=false,rejectOpen=false,rejectPin=false,release=null;
let captured={session_id:'s1',revision:0,model_hash:'hash',camera:{position:[8,4,9],target:[0,0,0]},rotor_angle:0};
const api=async(url,opts)=>{
  if(url==='/api/board/workspaces')return {active:{id:'space'},workspaces:[{id:'space',name:'Motor workspace'}]};
  if(url==='/api/board/workspaces/space/studies'){if(rejectPin)throw Error('Study changed elsewhere');pins.push(JSON.parse(opts.body));return {ok:true};}
  if(url==='/api/study/projects'&&!opts)return {projects:[{id:'p1',name:'<img src=x onerror=alert(1)>',version:1,updated_at:1,compatible:true}]};
  if(url.endsWith('/open')){if(rejectOpen)throw Error('Open failed');return {project:{id:'p1',name:'Saved study',version:1},workspace:{notes:{shaft:'Saved shaft note'}},state:{}};}
  calls.push(JSON.parse(opts.body));
  if(rejectSave)throw Error('Project changed elsewhere. Save a copy.');
  if(release!==null)await new Promise(r=>{release=r;});
  return {id:'p1',name:calls.at(-1).name,version:calls.length};
};
w.eval(fs.readFileSync(path.join(base,'study-projects.js'),'utf8').replace('export function setupStudyProjects','window.setupStudyProjects = function'));
let app;app=w.setupStudyProjects({api,capture:()=>captured,prepareSave:async()=>{},restore:async()=>{app.selection('shaft','Output shaft');},ready:()=>true});app.initialize();app.selection('shaft','Output shaft');
const note=text=>{$('study-notes').value=text;$('study-notes').dispatchEvent(new w.Event('input'));};
const save=()=>{$('project-form').dispatchEvent(new w.Event('submit',{cancelable:true}));};
(async()=>{
  note('First note');$('projects-open').click();await sleep(10);
  assert.equal($('saved-projects').querySelector('img'),null,'saved names must be plain text');
  rejectSave=true;save();await sleep(10);
  assert.match($('project-message').textContent,/Not saved/);assert.equal($('study-notes').value,'First note');
  rejectSave=false;release=true;save();await sleep(10);note('Edited while saving');release();await sleep(10);release=null;
  assert.match($('project-status').textContent,/Unsaved changes/);assert.equal(calls.at(-1).workspace.notes.shaft,'First note');
  save();await sleep(10);assert.match($('project-status').textContent,/ · Saved$/);assert.equal(calls.at(-1).workspace.notes.shaft,'Edited while saving');assert.equal(calls.at(-1).version,2);
  note('Keep this');let confirms=0;w.confirm=()=>{confirms++;return false;};$('saved-projects').querySelector('button').click();await sleep(10);assert.equal(confirms,1);assert.equal($('study-notes').value,'Keep this');
  w.confirm=()=>true;rejectOpen=true;$('saved-projects').querySelector('button').click();await sleep(10);assert.equal($('study-notes').value,'Keep this');assert.match($('project-message').textContent,/Could not open/);
  rejectOpen=false;$('saved-projects').querySelector('button').click();await sleep(10);assert.equal($('study-notes').value,'Saved shaft note');assert.match($('project-status').textContent,/ · Saved$/);
  $('projects-open').click();await sleep(10);
  note('Unsaved edit');assert.equal($('pin-study').disabled,true,'unsaved studies cannot be added');
  note('Saved shaft note');assert.equal($('pin-study').disabled,false);
  rejectPin=true;$('pin-study').click();await sleep(10);assert.match($('pin-message').textContent,/Not added/);assert.equal($('study-notes').value,'Saved shaft note');
  rejectPin=false;$('pin-study').click();await sleep(10);assert.deepEqual(pins[0],{action:'pin',project_id:'p1',version:1});
  assert.match($('pin-message').textContent,/Added to Motor workspace/);
  console.log('PASS: failed saves/opens preserve notes; edits during save remain dirty; versions travel with updates; untrusted names stay text; unsaved open can be cancelled.');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1;});
