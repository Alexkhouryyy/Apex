// Real control code with a Cesium stub; test interaction/race/error behavior.
const {JSDOM} = require('jsdom');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const dir = path.join(__dirname, '..', 'dashboard', 'static');
const html = fs.readFileSync(path.join(dir, 'world.html'), 'utf8');
const code = fs.readFileSync(path.join(dir, 'world-view.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 10));

function open(state, failMap = false, noEngine = false) {
  const dom = new JSDOM(html, {url:'https://apex.test/world', runScripts:'outside-only'});
  const w = dom.window, $ = id => w.document.getElementById(id);
  if (state) w.localStorage.setItem('apex.world.view.v1', state);
  w.matchMedia = () => ({matches: true});
  // Avoid holding the check open for map timeout timers.
  w.setTimeout = (fn, ms) => {const timer = setTimeout(fn, ms); timer.unref(); return timer;};
  let viewer, clicks, moves, destroyed = false, layers = [], flights = [];
  const event = () => ({addEventListener(fn){this.fn = fn;return () => {this.fn = null;};}});
  const radians = n => n * Math.PI / 180;
  w.Cesium = {
    Ion:{}, Math:{toDegrees:n => n * 180 / Math.PI},
    Color:{CYAN:'cyan', BLACK:'black', fromCssColorString:s => s},
    Cartesian3:{fromDegrees:(lng,lat,height) => ({lng,lat,height})},
    Cartographic:{fromCartesian:p => p}, ScreenSpaceEventType:{LEFT_CLICK:'click'},
    EllipsoidTerrainProvider:class{}, Viewer:class {
      constructor() {
        viewer = this; this.useDefaultRenderLoop = true;
        this.camera = {positionCartographic:{latitude:0,longitude:0,height:19000000}, heading:0,pitch:-Math.PI/2,roll:0,
          moveEnd: event(), cancelFlight(){}, pickEllipsoid:() => ({latitude:radians(34),longitude:radians(35)}),
          setView(v) {this.positionCartographic = {latitude:radians(v.destination.lat),longitude:radians(v.destination.lng),height:v.destination.height}; Object.assign(this,v.orientation);},
          flyTo(v) {flights.push(v);this.setView({...v,orientation:{heading:0,pitch:-Math.PI/2,roll:0}});this.moveEnd.fn();}};
        this.scene = {globe:{ellipsoid:{},tilesLoaded:true}, screenSpaceCameraController:{}, requestRender(){}, renderError:event(),
          postRender:{addEventListener(fn){queueMicrotask(fn);return () => {};}}};
        this.imageryLayers = {removeAll(){layers=[];},addImageryProvider(p){layers.push(p);}};
        this.entities = {removeAll(){},add(){}};
        this.screenSpaceEventHandler = {setInputAction(fn){clicks=fn;}};
      }
      resize(){} isDestroyed(){return destroyed;} destroy(){destroyed=true;}
    },
  };
  const provider = () => ({errorEvent:event()});
  w.ApexWorldImagery = {satellite:async () => {if(failMap)throw Error('offline');return provider();},streets:provider};
  w.fetch = async () => new Response(JSON.stringify({results:[]}));
  const cesium = w.Cesium;
  if (noEngine) delete w.Cesium;
  w.eval(code);
  return {w,$,dom,cesium,get viewer(){return viewer;},get destroyed(){return destroyed;},get layers(){return layers;},flights,click:() => clicks({position:{}})};
}

(async () => {
  const p = open(); await tick();
  assert.equal(p.$('world-loading').hidden, true);
  assert.equal(p.$('world-search-button').disabled, false);
  p.w.document.querySelector('[data-place="Byblos"]').click();
  assert.equal(p.$('world-selected-name').textContent, 'Byblos');
  assert.equal(p.flights.at(-1).destination.lat, 34.123);
  assert.equal(p.flights.at(-1).duration, 0);
  let saved = JSON.parse(p.w.localStorage.getItem('apex.world.view.v1'));
  assert.equal(saved.selected.label, 'Byblos');
  const restored = open(JSON.stringify(saved)); await tick();
  assert.equal(restored.$('world-selected-name').textContent, 'Byblos');
  assert.ok(Math.abs(restored.viewer.camera.positionCartographic.latitude - saved.camera.lat * Math.PI / 180) < 1e-10);
  p.$('world-query').value = '91, 20'; p.$('world-search').dispatchEvent(new p.w.Event('submit'));
  assert.match(p.$('world-status').textContent, /Latitude/);
  p.$('world-query').value = '34.2, 35.7'; p.$('world-search').dispatchEvent(new p.w.Event('submit'));
  assert.equal(p.flights.at(-1).destination.lat, 34.2);
  p.click(); assert.equal(p.$('world-selected-name').textContent, 'Selected location');

  // An older provider response must not replace a newer local selection.
  let finish;
  p.w.fetch = () => new Promise(resolve => {finish=resolve;});
  p.$('world-query').value = 'Some place'; p.$('world-search').dispatchEvent(new p.w.Event('submit'));
  p.w.document.querySelector('[data-place="Rome"]').click();
  finish(Response.json({results:[{label:'Old result',lat:12,lng:13}]})); await tick();
  assert.equal(p.$('world-results').children.length, 0);
  assert.equal(p.$('world-selected-name').textContent, 'Rome');
  p.w.fetch = async () => Response.json({results:[{label:'<img src=x onerror=alert(1)>',lat:12,lng:13}]});
  p.$('world-query').value = 'Different place'; p.$('world-search').dispatchEvent(new p.w.Event('submit')); await tick();
  assert.equal(p.$('world-results').querySelector('img'), null);
  p.$('world-results').querySelector('button').click();
  assert.equal(p.$('world-selected-name').querySelector('img'), null);
  p.w.fetch = async () => new Response('', {status:401});
  p.$('world-query').value = 'Another place'; p.$('world-search').dispatchEvent(new p.w.Event('submit')); await tick();
  assert.match(p.$('world-status').textContent, /Sign in/);

  const fallback = open(null, true); await tick();
  assert.equal(fallback.$('world-map').value, 'streets');
  assert.match(fallback.$('world-status').textContent, /unavailable/);
  fallback.$('world-map').value = 'outline'; fallback.$('world-map').dispatchEvent(new fallback.w.Event('change')); await tick();
  assert.equal(fallback.layers.length, 0);
  const badState = open('{"version":1,"camera":{"lat":999}}'); await tick();
  assert.ok(Number.isFinite(badState.viewer.camera.positionCartographic.latitude));
  const missingEngine = open(null,false,true);
  missingEngine.w.document.querySelector('script[src$="Cesium.js"]').onerror(); await tick();
  assert.equal(missingEngine.$('world-retry').hidden,false);
  assert.equal(missingEngine.$('world-search-button').disabled,true);
  missingEngine.w.Cesium = missingEngine.cesium;
  missingEngine.$('world-retry').click();
  missingEngine.w.document.querySelector('script[src$="Cesium.js"]').onload();
  missingEngine.w.document.querySelector('link[href$="Widgets/widgets.css"]').onload(); await tick();
  assert.equal(missingEngine.$('world-search-button').disabled,false);
  Object.defineProperty(p.w.document,'hidden',{configurable:true,value:true});
  p.w.document.dispatchEvent(new p.w.Event('visibilitychange'));
  assert.equal(p.viewer.useDefaultRenderLoop,false);
  Object.defineProperty(p.w.document,'hidden',{configurable:true,value:false});
  p.w.document.dispatchEvent(new p.w.Event('visibilitychange'));
  assert.equal(p.viewer.useDefaultRenderLoop,true);
  p.w.dispatchEvent(new p.w.PageTransitionEvent('pagehide',{persisted:false}));
  assert.equal(p.destroyed,true);
  // Command loads no Cesium assets until World View is opened.
  const command = new JSDOM(fs.readFileSync(path.join(dir,'index.html'),'utf8'),{url:'https://apex.test/',runScripts:'outside-only'});
  const c = command.window;
  c.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  c.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new c.Event('close'));};
  c.eval(fs.readFileSync(path.join(dir,'world-launch.js'),'utf8'));
  assert.equal(c.document.querySelectorAll('iframe').length,0);
  c.document.getElementById('world-open').click();
  const frame = c.document.querySelector('iframe'), dialog=c.document.querySelector('.world-dialog');
  assert.equal(frame.getAttribute('src'),'/world');
  c.dispatchEvent(new c.MessageEvent('message',{origin:'https://evil.test',source:frame.contentWindow,data:{type:'apex.world.close'}}));
  assert.equal(dialog.open,true);
  c.dispatchEvent(new c.MessageEvent('message',{origin:'https://apex.test',source:frame.contentWindow,data:{type:'apex.world.close'}}));
  assert.equal(dialog.open,false);
  assert.equal(c.document.querySelectorAll('iframe').length,0);
  assert.equal(c.document.activeElement.id,'world-open');
  c.close();
  for(const x of [p,restored,fallback,badState,missingEngine])x.dom.window.close();
  console.log('World View controls: navigation, state, search races, text escaping, failure recovery and disposal pass.');
})().catch(error => {console.error(error);process.exitCode=1;});
