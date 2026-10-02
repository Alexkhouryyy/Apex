const {JSDOM}=require('jsdom'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..'),dir=path.join(root,'dashboard','static'),OMM=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/world-station-omm.json'),'utf8'));
const NOW=Date.parse('2026-10-01T17:20:00Z'),tick=()=>new Promise(r=>setTimeout(r,10));
const feed=(extra={})=>({source:'CelesTrak',group:'stations',fetched_at:NOW,satellites:[{id:'25544',name:'ISS <img src=x>',epoch_at:Date.parse(OMM.EPOCH),omm:OMM}],...extra});
function setup(saved=false){
 const dom=new JSDOM(fs.readFileSync(path.join(dir,'world.html'),'utf8'),{url:'https://apex.test/world',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,$=id=>w.document.getElementById(id),calls=[],sources=[],timers=new Map(),selections=[],updates=[],flights=[];let now=NOW,seq=0,runtime,removed=0;
 w.Date.now=()=>now;w.matchMedia=()=>({matches:true});w.setTimeout=(fn,ms)=>{timers.set(++seq,{fn,ms});return seq;};w.clearTimeout=id=>timers.delete(id);
 w.localStorage.setItem('apex_token','token');if(saved)w.localStorage.setItem('apex.world.satellites.v1','on');
 const append=w.document.head.append.bind(w.document.head);
 w.document.head.append=(script)=>{append(script);assert.equal(script.getAttribute('src'),'/static/world/satellite-6.0.2.min.js');w.eval(fs.readFileSync(path.join(dir,'world/satellite-6.0.2.min.js'),'utf8'));queueMicrotask(()=>script.onload());};
 const Cesium={Color:{BLACK:'black',fromCssColorString:s=>({s,withAlpha:a=>({s,a})})},ArcType:{NONE:'none'},Cartesian3:{fromDegrees:(lng,lat,height)=>({lng,lat,height})},
 CustomDataSource:class{constructor(){const m=new Map();this.entities={add(e){m.set(e.id,e);return e;},removeAll(){m.clear();},removeById:id=>m.delete(id),getById:id=>m.get(id),get values(){return [...m.values()];}};sources.push(this);}}};
 const viewer={dataSources:{add(){},remove(){removed++;}},camera:{flyTo:v=>flights.push(v)},isDestroyed:()=>false,scene:{requestRender(){}}};
 w.fetch=async(url,options)=>{calls.push({url,options});return Response.json(feed());};
 for(const name of ['world-layer-status.js','world-satellites.js'])w.eval(fs.readFileSync(path.join(dir,name),'utf8'));
 runtime=w.ApexSatellites.create({viewer,Cesium,selectLocation:(loc,fly)=>{runtime?.clearSelection();selections.push({loc,fly});},updateLocation:loc=>updates.push(loc),reportState:w.ApexLayerStatus});
 function enable(value=true){$('sat-toggle').checked=value;$('sat-toggle').dispatchEvent(new w.Event('change'));}
 function timer(ms){const item=[...timers].find(([,v])=>v.ms===ms);assert.ok(item,'timer '+ms);timers.delete(item[0]);item[1].fn();}
 return {dom,w,$,calls,selections,updates,flights,runtime,enable,timer,timers,source:sources[0],get removed(){return removed;},advance:ms=>now+=ms};
}
(async()=>{
 const p=setup();assert.equal(p.calls.length,0);assert.equal(p.w.satellite,undefined,'off must not load orbit engine');
 p.enable();await tick();assert.equal(p.calls.length,1);assert.equal(p.calls[0].options.headers.Authorization,'Bearer token');
 assert.equal(p.source.entities.values.length,1);const initial=p.source.entities.values[0].position;
 assert.ok(initial.height>350000&&initial.height<500000,'real OMM propagation produces ISS altitude in meters');
 p.$('sat-events').querySelector('button').click();assert.equal(p.$('sat-details').hidden,false);assert.equal(p.$('sat-events').querySelector('img'),null);
 assert.match(p.$('sat-altitude').textContent,/km · calculated/);assert.match(p.$('sat-speed').textContent,/km\/s · inertial/);
 assert.match(p.$('sat-detail-status').textContent,/not a live observation/);assert.match(p.$('sat-orbit-note').textContent,/predicted path/);
 const orbit=p.source.entities.getById('satellite:orbit');assert.equal(orbit.polyline.positions.length,121);assert.equal(orbit.polyline.arcType,'none');
 assert.equal(p.runtime.pick({id:orbit}),false,'orbit path does not masquerade as an observed station');
 assert.equal(p.runtime.pick({id:p.source.entities.getById('satellite:25544')}),true);assert.equal(p.flights.length,1,'picking must not fly the camera');
 assert.equal(p.runtime.pick({id:{id:'satellite:25544'}}),false);
 p.advance(1000);p.timer(1000);assert.notDeepEqual(p.source.entities.getById('satellite:25544').position,initial);
 assert.equal(p.updates.length,1);assert.equal(p.flights.length,1,'position updates must not fly the camera');
 p.w.ApexLayerStatus('Flights','stale');p.$('sat-refresh').click();await tick();
 assert.equal(p.calls.at(-1).options.method,'POST');assert.equal(p.calls.at(-1).url,'/api/world/layers/satellites/retry');
 const fetched=p.$('sat-times').textContent;p.w.fetch=async()=>new Response('',{status:503});p.$('sat-refresh').click();await tick();
 assert.match(p.$('sat-status').textContent,/Stale source/);assert.equal(p.$('sat-times').textContent,fetched);
 assert.match(p.$('world-layer-summary').textContent,/Satellites stale.*Flights stale/);
 p.runtime.setVisible(false);assert.equal(p.timers.size,0,'hidden pages stop propagation and source work');
 p.runtime.setVisible(true);await tick();assert.ok([...p.timers.values()].some(t=>t.ms===1000));
 p.enable(false);assert.equal(p.source.entities.values.length,0);assert.equal(p.timers.size,0);assert.match(p.$('world-layer-summary').textContent,/Flights stale/);
 let finish,signal;p.w.fetch=(_,o)=>{signal=o.signal;return new Promise(r=>finish=r);};p.enable();await tick();p.enable(false);assert.equal(signal.aborted,true);
 p.w.fetch=async()=>Response.json(feed({satellites:[]}));p.enable();await tick();finish(Response.json(feed()));await tick();assert.equal(p.source.entities.values.length,0);
 p.w.fetch=async()=>Response.json(feed());p.enable();await tick();p.$('sat-events').querySelector('button').click();
 p.advance(4*86400000);p.timer(1000);assert.match(p.$('sat-detail-status').textContent,/stale source or old elements/);
 p.advance(15*86400000);p.timer(1000);assert.equal(p.source.entities.values.length,0);assert.match(p.$('sat-detail-status').textContent,/expired or propagation failed/);
 p.runtime.clearSelection();assert.equal(p.$('sat-details').hidden,true);p.runtime.destroy();assert.equal(p.removed,1);assert.equal(p.timers.size,0);
 const overlap=setup();overlap.w.fetch=async()=>Response.json(feed({satellites:[...feed().satellites,
  {...feed().satellites[0],id:'68837',name:'Docked craft',omm:{...OMM,NORAD_CAT_ID:68837}}]}));
 overlap.enable();await tick();overlap.$('sat-events').querySelector('button').click();
 assert.equal(overlap.runtime.pickStack([{id:overlap.source.entities.getById('satellite:68837')}]),true);
 assert.equal(overlap.$('sat-id').textContent,'25544','overlapping docked craft must not steal repeat selection');
 overlap.$('sat-events').querySelectorAll('button')[1].click();assert.equal(overlap.$('sat-id').textContent,'68837');
 overlap.runtime.destroy();overlap.dom.window.close();
 const restored=setup(true);await tick();assert.equal(restored.calls.length,1);restored.runtime.destroy();
 // OMM conversion must match the same orbit in the independently encoded TLE format.
 const lib=p.w.satellite,tle=lib.twoline2satrec('1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753','2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667');
 const omm=lib.json2satrec({NORAD_CAT_ID:5,EPOCH:'2000-06-27T18:50:19.733568Z',MEAN_MOTION:10.82419157,ECCENTRICITY:0.1859667,INCLINATION:34.2682,RA_OF_ASC_NODE:348.7242,ARG_OF_PERICENTER:331.7664,MEAN_ANOMALY:19.3264,BSTAR:0.000028098,MEAN_MOTION_DOT:0.00000023,MEAN_MOTION_DDOT:0});
 const date=new p.w.Date('2000-06-27T19:10:19.733Z'),a=lib.propagate(tle,date),b=lib.propagate(omm,date);
 for(const axis of ['x','y','z'])assert.ok(Math.abs(a.position[axis]-b.position[axis])<0.01,'OMM/TLE kilometer coordinate agreement');
 const reference=lib.sgp4(tle,0);
 for(const [axis,expected] of Object.entries({x:7022.46529266,y:-1400.08296755,z:0.03995155}))assert.ok(Math.abs(reference.position[axis]-expected)<0.00001,'Vallado reference vector '+axis);
 p.dom.window.close();restored.dom.window.close();console.log('PASS: real SGP4/reference vectors, OMM conversion, predicted orbit, age limits, independent layers, source errors, visibility, late requests and disposal.');
})().catch(e=>{console.error(e);process.exitCode=1;});
