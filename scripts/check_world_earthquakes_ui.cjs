// Actual layer module; deterministic feed, clock and Cesium collection.
const {JSDOM}=require('jsdom');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const dir=path.join(__dirname,'..','dashboard','static');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
const NOW=Date.now();
const feed=(events,extra={})=>({source:'USGS',generated_at:NOW,fetched_at:NOW,stale:false,
  refresh_failed:false,events:events??[{id:'us-example',lat:34.12,lng:35.65,depth_km:12.3,magnitude:4.2,
    place:'<img src=x onerror=alert(1)>',time:NOW-60000,updated:NOW,review_status:'reviewed',url:'javascript:alert(1)'}],...extra});
function setup(saved=false){
 const dom=new JSDOM(fs.readFileSync(path.join(dir,'world.html'),'utf8'),{url:'https://apex.test/world',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,$=id=>w.document.getElementById(id),timers=new Map(),calls=[],selections=[];
 let now=NOW,sequence=0,removed=false,source,runtime;
 w.Date.now=()=>now;
 w.setTimeout=(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id;};
 w.clearTimeout=id=>timers.delete(id);
 w.localStorage.setItem('apex_token','test-token');if(saved)w.localStorage.setItem('apex.world.earthquakes.v1','on');
 const Cesium={Color:{BLACK:'black',fromCssColorString:s=>s},Cartesian3:{fromDegrees:(lng,lat,height)=>({lng,lat,height})},
  CustomDataSource:class{constructor(){source=this;const entries=new Map();this.entities={add(e){entries.set(e.id,e);return e;},removeAll(){entries.clear();},getById(id){return entries.get(id);},get values(){return [...entries.values()];}};}}};
 const viewer={dataSources:{add(){},remove(){removed=true;}},scene:{requestRender(){}},isDestroyed:()=>false};
 w.fetch=async(url,options)=>{calls.push({url,options});return Response.json(feed());};
 w.eval(fs.readFileSync(path.join(dir,'world-earthquakes.js'),'utf8'));
 runtime=w.ApexEarthquakes.create({viewer,Cesium,selectLocation:(loc,fly)=>{runtime?.clearSelection();selections.push({loc,fly});}});
 function enable(value=true){$('quake-toggle').checked=value;$('quake-toggle').dispatchEvent(new w.Event('change'));}
 function poll(){const item=[...timers].find(([,v])=>v.ms===60000);assert.ok(item,'a visible enabled layer schedules a refresh');timers.delete(item[0]);item[1].fn();}
 return {dom,w,$,calls,selections,runtime,enable,poll,timers,get source(){return source;},get removed(){return removed;},advance:ms=>{now+=ms;}};
}
(async()=>{
 const p=setup();
 assert.equal(p.calls.length,0,'off means no request');assert.equal(p.$('quake-toggle').disabled,false);
 p.enable();await tick();
 assert.equal(p.calls.length,1);assert.equal(p.calls[0].url,'/api/world/layers/earthquakes');
 assert.equal(p.calls[0].options.headers.Authorization,'Bearer test-token');
 assert.equal(p.source.entities.values.length,1);assert.match(p.$('quake-status').textContent,/Current snapshot · 1/);
 assert.match(p.$('world-layer-summary').textContent,/Earthquakes on/);
 assert.equal(p.$('quake-events').querySelector('img'),null);
 p.$('quake-events').querySelector('button').click();
 assert.equal(p.selections.at(-1).fly,true);assert.equal(p.$('quake-details').hidden,false);
 assert.equal(p.$('quake-magnitude').textContent,'4.2');assert.equal(p.$('quake-depth').textContent,'12.3 km');
 assert.match(p.$('quake-occurred').textContent,/UTC$/);assert.equal(p.$('quake-review').textContent,'reviewed');
 assert.equal(p.$('quake-source').href,'https://earthquake.usgs.gov/earthquakes/eventpage/us-example');
 const entity=p.source.entities.values[0];assert.equal(p.runtime.pick({id:entity}),true);
 assert.equal(p.selections.at(-1).fly,false);assert.equal(p.runtime.pick({id:{id:entity.id}}),false);
 const timestamps=p.$('quake-times').textContent;
 p.w.fetch=async()=>new Response('',{status:503});p.$('quake-refresh').click();await tick();
 assert.match(p.$('quake-status').textContent,/Stale snapshot/);assert.equal(p.$('quake-times').textContent,timestamps);
 assert.equal(p.source.entities.values.length,1);assert.equal(p.$('quake-detail-status').dataset.state,'stale');
 assert.match(p.$('world-layer-summary').textContent,/stale/,'collapsed controls must still expose stale data');
 p.w.fetch=async()=>Response.json(feed(feed().events.map(e=>({...e,magnitude:5.1,lat:35}))));p.$('quake-refresh').click();await tick();
 assert.match(p.$('quake-status').textContent,/Current snapshot/);
 assert.equal(p.$('quake-magnitude').textContent,'5.1');assert.equal(p.selections.at(-1).loc.lat,35);
 assert.match(p.selections.at(-1).loc.label,/M 5.1/);assert.equal(p.selections.at(-1).fly,false,'revisions update the pin without moving the camera');
 p.advance(360000);p.poll();await tick();
 assert.match(p.$('quake-status').textContent,/Stale snapshot/,'aged timestamps must be stale even if the API flag is false');
 p.enable(false);assert.equal(p.source.entities.values.length,0);assert.equal(p.timers.size,0);
 assert.equal(p.$('world-layer-summary').textContent,'Live layers');
 assert.match(p.$('quake-detail-status').textContent,/layer is off/);
 let finish,signal;p.w.fetch=(_,options)=>{signal=options.signal;return new Promise(resolve=>{finish=resolve;});};
 p.enable();assert.equal(signal.aborted,false);p.enable(false);assert.equal(signal.aborted,true);
 p.w.fetch=async()=>Response.json(feed([]));p.enable();await tick();
 finish(Response.json(feed()));await tick();assert.equal(p.source.entities.values.length,0,'late response cannot replace a newer snapshot after re-enabling');
 p.w.fetch=async()=>Response.json(feed());p.enable();await tick();
 p.runtime.setVisible(false);assert.equal(p.timers.size,0);assert.equal(p.$('quake-refresh').disabled,true);
 p.runtime.setVisible(true);await tick();assert.ok(p.timers.size>0);
 p.w.fetch=async()=>Response.json(feed([],{generated_at:NOW+360000,fetched_at:NOW+360000}));p.$('quake-refresh').click();await tick();
 assert.equal(p.source.entities.values.length,0);assert.match(p.$('quake-status').textContent,/Current snapshot · 0/);
 assert.match(p.$('quake-detail-status').textContent,/no longer in the current feed/);
 p.runtime.clearSelection();assert.equal(p.$('quake-details').hidden,true);
 p.runtime.destroy();assert.equal(p.removed,true);assert.equal(p.timers.size,0);assert.equal(p.$('quake-toggle').disabled,true);
 const saved=setup(true);await tick();assert.equal(saved.calls.length,1);assert.equal(saved.$('quake-toggle').checked,true);
 saved.w.fetch=async()=>new Response('',{status:401});saved.$('quake-refresh').click();await tick();
 assert.match(saved.$('quake-status').textContent,/Sign in through Command/);
 saved.w.fetch=async()=>Response.json(feed(undefined,{generated_at:'bad'}));saved.$('quake-refresh').click();await tick();
 assert.match(saved.$('quake-status').textContent,/Invalid USGS snapshot/);assert.equal(saved.source.entities.values.length,1);
 saved.runtime.destroy();p.dom.window.close();saved.dom.window.close();
 console.log('PASS: opt-in auth, event inspection, escaping, safe source links, cache aging, outage/recovery, late-response isolation, visibility pause and disposal.');
})().catch(e=>{console.error(e);process.exitCode=1;});
