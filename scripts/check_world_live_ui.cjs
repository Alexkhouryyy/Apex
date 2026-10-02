// Exercise real live-layer logic with provider and renderer boundaries stubbed.
const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dir=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(dir,'world.html'),'utf8'),{url:'https://apex.test/world',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,$=id=>w.document.getElementById(id),tick=()=>new Promise(r=>setTimeout(r,10));
w.matchMedia=()=>({matches:true});w.AbortController=AbortController;
const timers=new Set();w.setTimeout=(fn,ms)=>{const t=setTimeout(fn,ms);t.unref();timers.add(t);return t;};w.clearTimeout=clearTimeout;
const set=new Map();let terrainResolve,hit,requests=[],offline=false,lateResolve;
w.Cesium={Math:{toDegrees:n=>n},Color:{BLACK:'black',WHITE:'white',YELLOW:'yellow',fromCssColorString:s=>s},
 Cartesian3:{fromDegrees:(...v)=>v,fromDegreesArray:v=>v},Credit:class{},EllipsoidTerrainProvider:class{},
 CesiumTerrainProvider:{fromUrl:()=>new Promise(r=>terrainResolve=r)},IonResource:{fromAssetId:async()=>({})},
 Cesium3DTileset:{fromUrl:async()=>({destroy(){}})}};
const viewer={scene:{pick:()=>hit,requestRender(){},primitives:{remove(){},add:x=>x}},
 camera:{positionCartographic:{latitude:34,longitude:35}},flyTo(){},entities:{get values(){return [...set.values()];},
 add(v){set.set(v.id,v);return v;},remove(v){set.delete(v.id);}}};
const row={id:'flights:abc123',label:'<img src=x onerror=alert(1)>',lat:34,lng:35,altitude_m:2000,kind:'observed',observed_at:Date.now()/1000};
w.fetch=async(url,options)=>{
 requests.push({url,options});
 if(url==='/api/chat')return new Response(JSON.stringify({response:'Selected aircraft response',thread_id:7}));
 if(offline)throw Error('offline');
 if(lateResolve===true)return new Promise(r=>{lateResolve=r;});
 return new Response(JSON.stringify({source:'Test provider',status:'fresh',records:[row],coverage:'test region',fetched_at:Date.now()/1000}));
};
w.eval(fs.readFileSync(path.join(dir,'world/live.js'),'utf8'));
const api=w.ApexWorldLive(viewer);
(async()=>{
 assert.equal(requests.length,0,'No feeds before explicit enable');
 api.enable('flights',true);await tick();assert.equal(set.size,1,$('world-feed-flights').textContent);assert.equal(api.records.size,1);
 hit={id:set.get(row.id)};assert.equal(api.pick({position:{}}),true);
 assert.equal($('world-entity-name').textContent,row.label);assert.equal($('world-entity').querySelectorAll('img').length,0,'Provider text is not HTML');
 $('world-follow').click();assert.equal(viewer.trackedEntity.id,row.id);
 offline=true;await api.refresh('flights');assert.equal(viewer.trackedEntity,undefined);assert.match($('world-feed-flights').textContent,/STALE/);
 assert.equal(set.size,1,'Retain stale entities during outage');
 offline=false;await api.refresh('flights');
 api.records.get(row.id).observed_at=Date.now()/1000-300;api.pick({position:{}});$('world-follow').click();assert.equal(viewer.trackedEntity,undefined,'Old position cannot be followed');
 api.records.get(row.id).observed_at=Date.now()/1000;
 $('world-question').value='Explain this selection';$('world-ask').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 const chat=requests.find(r=>r.url==='/api/chat');assert.equal(JSON.parse(chat.options.body).world_entity_id,row.id);
 assert.equal($('world-answer').textContent,'Selected aircraft response');
 api.mark({lat:34,lng:35});$('world-note-label').value='Saved note';$('world-add-note').click();
 assert.equal(JSON.parse(w.localStorage.getItem('apex.world.project.v1')).items.length,1);
 $('world-route').click();api.mark({lat:34,lng:35});api.mark({lat:34.1,lng:35.1});$('world-route').click();
 assert.equal(JSON.parse(w.localStorage.getItem('apex.world.project.v1')).items[1].type,'route');
 $('world-project-undo').click();assert.equal(JSON.parse(w.localStorage.getItem('apex.world.project.v1')).items.length,1);
 assert.equal(api.validProject({version:1,items:[{type:'note',label:'bad',points:[[999,0]]}]}),false);
 $('world-terrain').checked=true;$('world-terrain').dispatchEvent(new w.Event('change'));
 $('world-terrain').checked=false;$('world-terrain').dispatchEvent(new w.Event('change'));const flat=viewer.terrainProvider;
 terrainResolve({id:'late terrain'});await tick();assert.equal(viewer.terrainProvider,flat,'Disabled terrain ignores late provider');
 $('world-buildings').checked=true;$('world-buildings').dispatchEvent(new w.Event('change'));assert.equal($('world-buildings').checked,false,'No secret token fabricated');
 lateResolve=true;const pending=api.refresh('flights');await tick();const pendingCall=requests.at(-1);api.enable('flights',false);
 assert.equal(pendingCall.options.signal.aborted,true);lateResolve(new Response(JSON.stringify({records:[row]})));await pending;
 assert.equal(api.records.size,0);assert.equal($('world-entity').hidden,true);assert.equal(set.size,1,'Disabling feeds retains project notes');
 api.dispose();assert.equal(requests.filter(r=>r.url.includes('earthquakes')).length,0);
 for(const t of timers)clearTimeout(t);dom.window.close();
 console.log('Live World View: selection, tracking/freshness, context, outage retention, cancellation, projects and terrain races pass.');
})().catch(e=>{console.error(e);dom.window.close();process.exit(1);});
