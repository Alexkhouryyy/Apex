// World View panel (dashboard/static/world/live.js) with the renderer stubbed:
// Ask Celine sends the layers' own selection, voice commands flip the real
// layer switches, saved notes/routes persist with undo, terrain ignores a late
// provider, and 3D buildings never invent a token.
const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dir=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(dir,'world.html'),'utf8'),{url:'https://apex.test/world',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,$=id=>w.document.getElementById(id),tick=()=>new Promise(r=>setTimeout(r,10));
w.matchMedia=()=>({matches:true});w.AbortController=AbortController;
const timers=new Set();w.setTimeout=(fn,ms)=>{const t=setTimeout(fn,ms);t.unref();timers.add(t);return t;};w.clearTimeout=clearTimeout;
const set=new Map();let terrainResolve,requests=[];
w.Cesium={Math:{toDegrees:n=>n},Color:{BLACK:'black',WHITE:'white',YELLOW:'yellow',fromCssColorString:s=>s},
 Cartesian3:{fromDegrees:(...v)=>v,fromDegreesArray:v=>v},Credit:class{},EllipsoidTerrainProvider:class{},
 CesiumTerrainProvider:{fromUrl:()=>new Promise(r=>terrainResolve=r)},IonResource:{fromAssetId:async()=>({})},
 Cesium3DTileset:{fromUrl:async()=>({destroy(){}})}};
const viewer={scene:{requestRender(){},primitives:{remove(){},add:x=>x}},entities:{get values(){return [...set.values()];},
 add(v){set.set(v.id,v);return v;},remove(v){set.delete(v.id);}}};
w.fetch=async(url,options)=>{requests.push({url,options});
 if(url==='/api/chat')return new Response(JSON.stringify({response:'That flight is MEA313.',thread_id:7}));
 throw Error('unexpected '+url);};
w.eval(fs.readFileSync(path.join(dir,'world/live.js'),'utf8'));
// The three layer modules answer through these hooks (wired in world-view.js).
const toggled=[];let scene={layers:['flights'],entity:'flights:abc123'};
const api=w.ApexWorldLive(viewer,{scene:()=>scene,toggle:(layer,on)=>{toggled.push([layer,on]);return true;}});
(async()=>{
 assert.equal(requests.length,0,'Nothing is fetched until asked');
 assert.equal($('world-entity-list'),null,'The duplicate entity finder is gone; layers list their own records');
 // Ask Celine: the selection goes as an id the server resolves, never as the record itself.
 $('world-question').value='What is this flight?';$('world-ask').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 const body=JSON.parse(requests.find(r=>r.url==='/api/chat').options.body);
 assert.deepEqual([body.world_entity_id,body.world_layer_ids],['flights:abc123',['flights']]);
 assert.equal(body.world_context,undefined);assert.equal($('world-answer').textContent,'That flight is MEA313.');
 scene={layers:[],entity:null};requests.length=0;$('world-question').value='Anything nearby?';
 $('world-ask').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 assert.equal('world_entity_id' in JSON.parse(requests[0].options.body),false,'No selection, no id');
 // Voice commands flip the same switches (aircraft/stations are understood too).
 for(const q of ['show flights','hide satellites','show aircraft','show earthquakes.']){$('world-question').value=q;$('world-ask').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();}
 assert.deepEqual(toggled,[['flights',true],['satellites',false],['flights',true],['earthquakes',true]]);
 assert.equal(requests.filter(r=>r.url==='/api/chat').length,1,'Commands are not sent to the model');
 // Saved notes and routes, with undo; a bad project is refused.
 api.mark({lat:34,lng:35});$('world-note-label').value='Saved note';$('world-add-note').click();
 assert.equal(JSON.parse(w.localStorage.getItem('apex.world.project.v1')).items.length,1);
 $('world-route').click();api.mark({lat:34,lng:35});api.mark({lat:34.1,lng:35.1});$('world-route').click();
 assert.equal(JSON.parse(w.localStorage.getItem('apex.world.project.v1')).items[1].type,'route');
 $('world-project-undo').click();assert.equal(JSON.parse(w.localStorage.getItem('apex.world.project.v1')).items.length,1);
 assert.equal(api.validProject({version:1,items:[{type:'note',label:'bad',points:[[999,0]]}]}),false);
 // Terrain: switched off before it loaded, the late provider is ignored.
 $('world-terrain').checked=true;$('world-terrain').dispatchEvent(new w.Event('change'));
 $('world-terrain').checked=false;$('world-terrain').dispatchEvent(new w.Event('change'));const flat=viewer.terrainProvider;
 terrainResolve({id:'late terrain'});await tick();assert.equal(viewer.terrainProvider,flat,'Disabled terrain ignores late provider');
 $('world-buildings').checked=true;$('world-buildings').dispatchEvent(new w.Event('change'));assert.equal($('world-buildings').checked,false,'No secret token fabricated');
 api.dispose();for(const t of timers)clearTimeout(t);dom.window.close();
 console.log('World View panel: Celine gets the layers\' selection as ids, voice commands flip the real switches, notes/routes/undo, terrain race and no invented token pass.');
})().catch(e=>{console.error(e);dom.window.close();process.exit(1);});
