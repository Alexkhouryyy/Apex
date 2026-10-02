// Exercise real launch code and Command globe hooks with a renderer stub.
const {JSDOM}=require('jsdom');
const fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict');
const dir=path.join(__dirname,'..','dashboard','static');
const html=fs.readFileSync(path.join(dir,'index.html'),'utf8');
const launch=fs.readFileSync(path.join(dir,'world-launch.js'),'utf8');
const command=fs.readFileSync(path.join(dir,'app.js'),'utf8');
const css=fs.readFileSync(path.join(dir,'world-launch.css'),'utf8');
const dom=new JSDOM(html,{url:'https://apex.test/',runScripts:'outside-only'});
const w=dom.window, $=id=>w.document.getElementById(id);
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
w.eval(launch);
const dialog=$('apex-world-dialog');
const globe=$('globe');
let hit,paused=0,resumed=0;
const ctrl={};
const render=new Proxy({}, {get(_t,k){
  if(k==='controls')return ()=>ctrl;
  if(k==='pauseAnimation')return ()=>paused++;
  if(k==='resumeAnimation')return ()=>resumed++;
  if(k==='onGlobeClick')return fn=>{hit=fn;return render;};
  return ()=>render;
}});
w.Globe=()=>()=>render;
function section(from,to){return command.slice(command.indexOf(from),command.indexOf(to,command.indexOf(from)));}
w.eval(`let globeInstance=null; let currentBody='earth'; const globeArcs=[]; const HUBS=[];
const SOLAR_BODIES={earth:{label:'Earth',texture:'earth',atm:'#5fd8ff',speed:1},mars:{label:'Mars',texture:'mars',atm:'#ff5522',speed:1}};
${section('function switchBody(key)', 'function renderPlanetSelector()')}
${section('function initGlobe()', '// Optional graphics must not delay dashboard controls')}
initGlobe();`);
function pointer(type,id=1,x=100,y=100,button=0){
  const e=new w.MouseEvent(type,{bubbles:true,clientX:x,clientY:y,button});
  Object.defineProperty(e,'pointerId',{value:id});globe.dispatchEvent(e);
}
function close(){dialog.close();}
function tap(){pointer('pointerdown');pointer('pointerup');hit({},new w.MouseEvent('click'));}
assert.equal(w.document.querySelector('iframe'),null,'No world resources loaded before entry');
tap(); assert.equal(dialog.open,true,'Earth click opens World View');
assert.equal(paused,1,'Command globe sleeps while world is open');
assert.equal(w.document.querySelector('iframe').src,'https://apex.test/world');
assert.equal(w.document.querySelector('iframe').allow,'microphone; autoplay; fullscreen; usb','Permissions survive the outer Command frame');
hit({},new w.MouseEvent('click'));assert.equal(w.document.querySelectorAll('iframe').length,1,'Only one renderer');
close();assert.equal(resumed,1);assert.equal(w.document.activeElement.id,'world-open');
pointer('pointerdown');pointer('pointermove',1,140,100);pointer('pointerup',1,100,100);
hit({},new w.MouseEvent('click'));assert.equal(dialog.open,false,'Drag out-and-back does not enter');
pointer('pointerdown');pointer('pointerdown',2);pointer('pointerup',1);pointer('pointerup',2);
hit({},new w.MouseEvent('click'));assert.equal(dialog.open,false,'Multi-touch does not enter');
pointer('pointerdown');pointer('pointercancel');hit({},new w.MouseEvent('click'));
assert.equal(dialog.open,false,'Cancelled gesture does not enter');
pointer('pointerdown',1,100,100,2);pointer('pointerup',1,100,100,2);hit({},new w.MouseEvent('click',{button:2}));
assert.equal(dialog.open,false,'Right-click does not enter');
w.switchBody('mars');tap();assert.equal(dialog.open,false,'Another planet cannot masquerade as Earth');
assert.equal($('world-entry-hint').textContent,'Explore Earth');
$('world-command-open').click();assert.equal(dialog.open,true,'Header shortcut works from any planet');
close();assert.equal(w.document.activeElement.id,'world-command-open');
w.ApexWorldView.openFromEarthSelector();assert.equal(dialog.open,true,'Selecting Earth enters its World View');close();
w.switchBody('earth');assert.equal($('world-entry-hint').textContent,'Click Earth to explore');
// Native link keyboard activation and modifiers retain their browser behavior.
$('world-open').click();assert.equal(dialog.open,true);close();
const modified=new w.MouseEvent('click',{bubbles:true,cancelable:true,ctrlKey:true});
$('world-open').dispatchEvent(modified);assert.equal(modified.defaultPrevented,false);assert.equal(dialog.open,false);
tap(); const frame=w.document.querySelector('iframe');
w.dispatchEvent(new w.MessageEvent('message',{origin:'https://evil.test',source:frame.contentWindow,data:{type:'apex.world.close'}}));
assert.equal(dialog.open,true,'Untrusted close message is ignored');
w.dispatchEvent(new w.MessageEvent('message',{origin:'https://apex.test',source:w,data:{type:'apex.world.close'}}));
assert.equal(dialog.open,true,'Wrong-frame message is ignored');
w.dispatchEvent(new w.MessageEvent('message',{origin:'https://apex.test',source:frame.contentWindow,data:{type:'apex.world.close'}}));
assert.equal(dialog.open,false);assert.equal(w.document.querySelectorAll('iframe').length,0);
assert.match(css,/@media \(prefers-reduced-motion: reduce\)/);
assert.match(css,/var\(--accent/,'Entry follows the existing theme');
assert.equal($('world-open').getAttribute('aria-controls'),dialog.id);
dom.window.close();
console.log('World entry: Earth click, drag/multi-touch rejection, planet identity, shortcuts, renderer pause, focus, modifiers and close-message validation pass.');
