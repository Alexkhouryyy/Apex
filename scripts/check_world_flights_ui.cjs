const {JSDOM}=require('jsdom'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const dir=path.join(__dirname,'..','dashboard','static'),NOW=Date.now(),tick=()=>new Promise(r=>setTimeout(r,10));
const aircraft=(extra={})=>({id:'abc123',callsign:'<img src=x>',registration:'OD-TEST',aircraft_type:'A320',lat:34.1,lng:35.6,
 position_at:NOW-1500,on_ground:false,altitude_ft:36000,altitude_kind:'geometric',speed_knots:440,track_deg:180,position_source:'adsb_icao',...extra});
const feed=(records=[aircraft()],extra={})=>({source:'ADSB.lol',generated_at:NOW,fetched_at:NOW,area:{lat:34.1,lng:35.6,radius_nm:250},aircraft:records,...extra});
(async()=>{
 const dom=new JSDOM(fs.readFileSync(path.join(dir,'world.html'),'utf8'),{url:'https://apex.test/world',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,$=id=>w.document.getElementById(id),timers=new Map(),calls=[],selections=[],sources=[];let now=NOW,seq=0,runtime,removed=0;
 w.Date.now=()=>now;w.setTimeout=(fn,ms)=>{timers.set(++seq,{fn,ms});return seq;};w.clearTimeout=id=>timers.delete(id);
 w.localStorage.setItem('apex_token','token');
 const Cesium={Color:{WHITE:'white',BLACK:'black',fromCssColorString:s=>({withAlpha:a=>({s,a})})},
 Cartesian3:Object.assign(class {constructor(x,y,z){Object.assign(this,{x,y,z});}}, {UNIT_Z:'z',fromDegrees:(lng,lat,height)=>({lng,lat,height})}),
 CustomDataSource:class{constructor(name){this.name=name;const m=new Map();this.entities={add(e){m.set(e.id,e);return e;},removeAll(){m.clear();},getById:id=>m.get(id),get values(){return [...m.values()];}};sources.push(this);}}};
 const viewer={dataSources:{add(){},remove(){removed++;}},isDestroyed:()=>false,scene:{requestRender(){}}};
 w.fetch=async(url,options)=>{calls.push({url,options});return Response.json(feed());};
 for(const name of ['world-layer-status.js','world-flights.js'])w.eval(fs.readFileSync(path.join(dir,name),'utf8'));
 runtime=w.ApexFlights.create({viewer,Cesium,selectLocation:(loc,fly)=>{runtime?.clearSelection();selections.push({loc,fly});},getArea:()=>({lat:10,lng:20}),reportState:w.ApexLayerStatus});
 const source=sources[0],enable=(value=true)=>{$('flight-toggle').checked=value;$('flight-toggle').dispatchEvent(new w.Event('change'));};
 assert.equal(calls.length,0);enable();await tick();assert.equal(calls.length,1);
 assert.equal(calls[0].url,'/api/world/layers/flights?lat=34.1&lng=35.6');assert.equal(calls[0].options.headers.Authorization,'Bearer token');
 assert.equal(source.entities.values.length,1);assert.ok(Math.abs(source.entities.values[0].position.height-10972.8)<1e-6);
 assert.equal($('flight-events').querySelector('img'),null);
 $('flight-events').querySelector('button').click();assert.equal($('flight-details').hidden,false);
 assert.equal($('flight-speed').textContent,'440 kn · ground speed');assert.match($('flight-altitude').textContent,/36,000 ft · geometric/);
 assert.match($('flight-position-time').textContent,/UTC$/);assert.equal(selections.at(-1).fly,true);
 assert.equal(runtime.pick({id:source.entities.values[0]}),true);assert.equal(selections.at(-1).fly,false);
 assert.equal(runtime.pick({id:{id:'flight:abc123'}}),false);
 w.ApexLayerStatus('Earthquakes','stale');assert.match($('world-layer-summary').textContent,/Flights on.*Earthquakes stale/);
 const times=$('flight-times').textContent;w.fetch=async()=>new Response('',{status:503});$('flight-refresh').click();await tick();
 assert.match($('flight-status').textContent,/Stale snapshot/);assert.equal($('flight-times').textContent,times);assert.equal(source.entities.values.length,1);
 assert.equal($('flight-detail-status').dataset.state,'stale');assert.match($('world-layer-summary').textContent,/Flights stale.*Earthquakes stale/);
 w.fetch=async()=>Response.json(feed([aircraft({lat:34.2,altitude_ft:null,speed_knots:null,track_deg:null})]));$('flight-refresh').click();await tick();
 assert.equal($('flight-altitude').textContent,'Unknown');assert.equal($('flight-speed').textContent,'Unknown');assert.equal(selections.at(-1).loc.lat,34.2);
 assert.equal(selections.at(-1).fly,false,'position revision must not fly the camera');
 now+=45000;const poll=[...timers].find(([,t])=>t.ms===30000);timers.delete(poll[0]);poll[1].fn();await tick();
 assert.match($('flight-detail-status').textContent,/stale/,'individual position age matters even with a current feed');
 enable(false);assert.equal(source.entities.values.length,0);assert.equal(timers.size,0);assert.match($('world-layer-summary').textContent,/Earthquakes stale/);
 assert.doesNotMatch($('world-layer-summary').textContent,/Flights/);assert.match($('flight-detail-status').textContent,/layer is off/);
 let finish,signal;w.fetch=(_,o)=>{signal=o.signal;return new Promise(r=>finish=r);};enable();runtime.setVisible(false);assert.equal(signal.aborted,true);assert.equal(timers.size,0);
 w.fetch=async()=>Response.json(feed([]));runtime.setVisible(true);await tick();finish(Response.json(feed()));await tick();
 assert.equal(source.entities.values.length,0,'late response cannot overwrite a new request');
 w.fetch=async()=>Response.json(feed([aircraft()],{area:{lat:10,lng:20,radius_nm:250}}));$('flight-area').click();await tick();
 assert.match($('flight-region').textContent,/10.0°, 20.0°/);assert.equal(source.entities.values.length,1);
 w.fetch=async()=>Response.json(feed());$('flight-refresh').click();await tick();assert.match($('flight-status').textContent,/another area/);
 assert.match($('flight-region').textContent,/10.0°, 20.0°/);runtime.clearSelection();assert.equal($('flight-details').hidden,true);
 runtime.destroy();assert.equal(removed,1);assert.equal(timers.size,0);assert.equal($('flight-toggle').disabled,true);
 dom.window.close();console.log('PASS: regional aircraft, units, position aging, layer coexistence, outage, region isolation, late responses and disposal.');
})().catch(e=>{console.error(e);process.exitCode=1;});
