/* SGP4 estimates from public OMM elements, never labeled live observations. */
(() => {
  'use strict';
  const KEY = 'apex.world.satellites.v1', $ = id => document.getElementById(id);
  const num = (v, lo, hi) => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
  const utc = ms => new Date(ms).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC');
  const STALE_ELEMENTS = 3 * 86400000, MAX_ELEMENTS = 14 * 86400000;
  let enginePromise = null;
  function engine() {
    if (window.satellite?.json2satrec) return Promise.resolve(window.satellite);
    if (enginePromise) return enginePromise;
    enginePromise = new Promise((resolve, reject) => {
      const script = document.createElement('script'); script.src = '/static/world/satellite-6.0.2.min.js';
      const timer = setTimeout(fail, 10000);
      function fail() {clearTimeout(timer);script.remove();enginePromise = null;reject(new Error('Orbit engine could not load. Retry this layer.'));}
      script.onerror = fail;
      script.onload = () => {if (!window.satellite?.json2satrec) return fail();clearTimeout(timer);resolve(window.satellite);};
      document.head.append(script);
    });
    return enginePromise;
  }
  function normalize(data) {
    const now = Date.now();
    if (!data || data.source !== 'CelesTrak' || data.group !== 'stations' || !Array.isArray(data.satellites) ||
        !num(data.fetched_at, 946684800000, now + 30000)) throw new Error('Invalid station snapshot.');
    const bounds = {MEAN_MOTION:[0.1,20], ECCENTRICITY:[0,0.99], INCLINATION:[0,180],
      RA_OF_ASC_NODE:[0,360], ARG_OF_PERICENTER:[0,360], MEAN_ANOMALY:[0,360],
      BSTAR:[-1,1], MEAN_MOTION_DOT:[-10,10], MEAN_MOTION_DDOT:[-10,10]};
    const records = new Map();
    for (const r of data.satellites.slice(0,64)) {
      if (!r || typeof r.id !== 'string' || !/^[1-9][0-9]{0,8}$/.test(r.id) || typeof r.name !== 'string' ||
          !num(r.epoch_at,946684800000,now+86400000) || !r.omm || r.omm.NORAD_CAT_ID !== Number(r.id) ||
          typeof r.omm.EPOCH !== 'string' || !r.omm.EPOCH.endsWith('Z') ||
          Math.abs(Date.parse(r.omm.EPOCH)-r.epoch_at)>1 || !Number.isFinite(Date.parse(r.omm.EPOCH)) ||
          Object.entries(bounds).some(([key,[lo,hi]])=>!num(r.omm[key],lo,hi))) continue;
      if (!records.has(r.id) || records.get(r.id).epoch_at < r.epoch_at)
        records.set(r.id,{...r,name:r.name.slice(0,120)});
    }
    return {...data,satellites:[...records.values()]};
  }
  window.ApexSatellites = {create({viewer, Cesium, selectLocation, updateLocation = () => {}, reportState = () => {}}) {
    const toggle=$('sat-toggle'), refreshButton=$('sat-refresh');
    const source=new Cesium.CustomDataSource('Apex satellites'); viewer.dataSources.add(source);
    let enabled=false, visible=!document.hidden, disposed=false, busy=false, library=null, snapshot=null;
    let records=new Map(), positions=new Map(), selection=null, orbitAt=0, forcedStale=false, notice='';
    let generation=0, controller=null, timeout=null, fetchTimer=null, positionTimer=null;
    try {enabled=localStorage.getItem(KEY)==='on';} catch (_) {}
    toggle.checked=enabled;toggle.disabled=false;
    const oldElements = r => Date.now()-r.epoch_at>STALE_ELEMENTS;
    const stale = () => Boolean(snapshot && (forcedStale || snapshot.stale || snapshot.refresh_failed ||
      Date.now()-snapshot.fetched_at>21600000));
    function calculate(r, date) {
      if (Math.abs(date.getTime()-r.epoch_at)>MAX_ELEMENTS) return null;
      try {
        const result=library.propagate(r.satrec,date);
        if (!result?.position || !result?.velocity || r.satrec.error) return null;
        const g=library.eciToGeodetic(result.position,library.gstime(date));
        const speed=Math.hypot(result.velocity.x,result.velocity.y,result.velocity.z);
        const lat=library.degreesLat(g.latitude),lng=library.degreesLong(g.longitude);
        if (!num(lat,-90,90)||!num(lng,-180,180)||!num(g.height,80,100000)||!num(speed,0,20)) return null;
        return {lat,lng,height:g.height,speed,time:date.getTime()};
      } catch (_) {return null;}
    }
    const locationOf=(r,p)=>({lat:p.lat,lng:p.lng,label:`${r.name} · satellite estimate`});
    function details() {
      $('sat-details').hidden=!selection;if(!selection)return;
      const r=selection,p=positions.get(r.id);
      $('sat-id').textContent=r.id;$('sat-epoch').textContent=utc(r.epoch_at);
      $('sat-epoch').dateTime=new Date(r.epoch_at).toISOString();
      $('sat-age').textContent=`${Math.max(0,(Date.now()-r.epoch_at)/3600000).toFixed(1)} hours`;
      $('sat-period').textContent=`${(1440/r.omm.MEAN_MOTION).toFixed(1)} min`;
      $('sat-altitude').textContent=p?`${p.height.toFixed(1)} km · calculated`:'Unavailable';
      $('sat-speed').textContent=p?`${p.speed.toFixed(2)} km/s · inertial`:'Unavailable';
      $('sat-calculated').textContent=p?utc(p.time):'Unavailable';
      $('sat-detail-status').dataset.state=stale()||oldElements(r)||!enabled||!p?'stale':'current';
      $('sat-detail-status').textContent=!enabled?'Layer is off · last calculation retained.':!records.has(r.id)?'No longer in the station feed.':
        !p?'Position unavailable · elements expired or propagation failed.':stale()||oldElements(r)?
        'SGP4 estimate · stale source or old elements. Accuracy degrades with element age.':'SGP4 estimate · calculated from orbit elements, not a live observation.';
      $('sat-source').href='https://celestrak.org/NORAD/elements/gp.php?CATNR='+r.id+'&FORMAT=JSON';
    }
    function updateStatus() {
      refreshButton.disabled=!enabled||busy||!visible;
      const old=snapshot?.satellites.some(oldElements);
      reportState('Satellites',!enabled?'':stale()||old?'stale':!snapshot&&notice&&!busy?'unavailable':'on');
      $('sat-status').dataset.state=stale()||old?'stale':'current';
      $('sat-status').textContent=!enabled?'Off · enable calculated station positions.':busy?'Loading station elements…':
        snapshot?`${stale()?'Stale source':'SGP4 estimates'} · ${positions.size} positioned / ${snapshot.satellites.length} records${old?' · old elements present':''}${notice?' · '+notice:''}`:notice||'Waiting for elements…';
      $('sat-times').textContent=enabled&&snapshot?`Elements fetched ${utc(snapshot.fetched_at)} · source refresh no more than every 2 hours`:'';
      details();
    }
    function clearSelection() {selection=null;source.entities.removeById('satellite:orbit');details();viewer.scene.requestRender();}
    function drawOrbit(r,now) {
      source.entities.removeById('satellite:orbit');orbitAt=now;
      $('sat-orbit-note').textContent='Orbit path unavailable · propagation failed or elements expired.';
      const points=[],period=86400000/r.omm.MEAN_MOTION;
      for(let i=0;i<=120;i++) {
        const p=calculate(r,new Date(now+period*i/120));if(!p)return;
        points.push(Cesium.Cartesian3.fromDegrees(p.lng,p.lat,p.height*1000));
      }
      source.entities.add({id:'satellite:orbit',polyline:{positions:points,width:1.5,
        material:Cesium.Color.fromCssColorString('#c2a6ff').withAlpha(0.7),arcType:Cesium.ArcType.NONE}});
      $('sat-orbit-note').textContent=`Next orbit · predicted path · starts ${utc(now)} · recalculated each minute`;
    }
    function inspect(id,fly=true) {
      const r=records.get(id),p=positions.get(id);if(!enabled||!r||!p)return false;
      selectLocation(locationOf(r,p),false);selection=r;drawOrbit(r,Date.now());details();
      if(fly)viewer.camera.flyTo({destination:Cesium.Cartesian3.fromDegrees(p.lng,p.lat,Math.max(1800000,p.height*4000)),
        duration:matchMedia('(prefers-reduced-motion: reduce)').matches?0:1.2});
      viewer.scene.requestRender();return true;
    }
    function tickPositions() {
      if(disposed||!enabled||!visible||!snapshot||!library)return;
      const now=Date.now();positions=new Map();
      for(const r of records.values()) {
        const p=calculate(r,new Date(now)),id='satellite:'+r.id;let e=source.entities.getById(id);
        if(!p){source.entities.removeById(id);continue;}positions.set(r.id,p);
        const position=Cesium.Cartesian3.fromDegrees(p.lng,p.lat,p.height*1000);
        const color=Cesium.Color.fromCssColorString(oldElements(r)||stale()?'#ffcf86':'#c2a6ff');
        if(!e)e=source.entities.add({id,position,point:{pixelSize:8,color,outlineColor:Cesium.Color.BLACK,outlineWidth:1}});
        else {e.position=position;e.point.color=color;}
      }
      for(const b of $('sat-events').querySelectorAll('button'))b.disabled=!positions.has(b.dataset.id);
      if(selection&&records.has(selection.id)) {
        selection=records.get(selection.id);const p=positions.get(selection.id);
        if(p){updateLocation(locationOf(selection,p));if(now-orbitAt>=60000)drawOrbit(selection,now);}
        else {source.entities.removeById('satellite:orbit');$('sat-orbit-note').textContent='Orbit path unavailable · propagation failed or elements expired.';}
      }
      updateStatus();viewer.scene.requestRender();clearTimeout(positionTimer);
      positionTimer=setTimeout(tickPositions,1000);
    }
    function render() {
      records=new Map();source.entities.removeAll();
      for(const r of snapshot.satellites) {try{records.set(r.id,{...r,satrec:library.json2satrec(r.omm)});}catch(_){}}
      $('sat-events').replaceChildren();
      for(const r of [...records.values()].slice(0,8)) {
        const b=document.createElement('button');b.type='button';b.textContent=r.name;b.dataset.id=r.id;
        b.addEventListener('click',()=>inspect(r.id));$('sat-events').append(b);
      }
      orbitAt=0;tickPositions();
    }
    function stop() {++generation;clearTimeout(fetchTimer);clearTimeout(positionTimer);clearTimeout(timeout);
      fetchTimer=positionTimer=timeout=null;controller?.abort();controller=null;busy=false;}
    async function refresh(manual=false) {
      if(disposed||!enabled||!visible||busy)return;
      clearTimeout(fetchTimer);const id=++generation;busy=true;notice='';updateStatus();
      const requestController=new AbortController();controller=requestController;let abortTimer=null;
      try {
        library=await engine();if(disposed||id!==generation)return;
        abortTimer=setTimeout(()=>requestController.abort(),15000);timeout=abortTimer;
        let token='';try{token=localStorage.getItem('apex_token')||'';}catch(_){}
        const response=await fetch('/api/world/layers/satellites'+(manual?'/retry':''),{
          method:manual?'POST':'GET',signal:requestController.signal,headers:token?{Authorization:'Bearer '+token}:{},cache:'no-store'});
        if(disposed||id!==generation)return;
        if(response.status===401)throw new Error('Sign in through Command, then retry this layer.');
        if(!response.ok)throw new Error('Station source unavailable or paused. Retry source after its cooldown.');
        const data=normalize(await response.json());if(disposed||id!==generation)return;
        snapshot=data;forcedStale=false;notice=data.source_paused?'source paused; Retry source after the cooldown':'';render();
      } catch(e) {
        if(disposed||id!==generation)return;forcedStale=true;
        notice=e.name==='AbortError'?'Station request timed out; last elements retained.':e.message;
      } finally {
        clearTimeout(abortTimer);if(!disposed&&id===generation){busy=false;controller=null;timeout=null;updateStatus();
          if(enabled&&visible&&!snapshot?.source_paused)fetchTimer=setTimeout(()=>refresh(),7200000);}
      }
    }
    const manualRefresh=()=>refresh(true);
    function toggleLayer() {
      enabled=toggle.checked;stop();notice='';try{localStorage.setItem(KEY,enabled?'on':'off');}catch(_){}
      if(!enabled){source.entities.removeAll();$('sat-events').replaceChildren();}
      else if(snapshot&&library)render();updateStatus();viewer.scene.requestRender();if(enabled)refresh();
    }
    toggle.addEventListener('change',toggleLayer);refreshButton.addEventListener('click',manualRefresh);updateStatus();if(enabled)refresh();
    function ownedStation(picked) {
      const e=picked?.id;
      return enabled&&e&&typeof e.id==='string'&&/^satellite:[1-9][0-9]{0,8}$/.test(e.id)&&
        source.entities.getById(e.id)===e ? e : null;
    }
    return {clearSelection,pickStack(picks) {
      const entities=picks.map(ownedStation).filter(Boolean);
      let chosen=entities.find(e=>selection&&e.id==='satellite:'+selection.id);
      // Docked vehicles can have identical elements and exceed the pick stack.
      // Retain the selected object when the picked station occupies its position.
      const current=selection&&positions.get(selection.id);
      if(!chosen&&current&&entities.some(e=>{
        const p=positions.get(e.id.slice(10));
        return p&&Math.abs(p.lat-current.lat)<0.000001&&Math.abs(p.lng-current.lng)<0.000001&&Math.abs(p.height-current.height)<0.001;
      }))chosen=source.entities.getById('satellite:'+selection.id);
      chosen=chosen||entities[0];
      return chosen?inspect(chosen.id.slice(10),false):false;
    },pick(picked) {
      const e=picked?.id;if(!enabled||!e||typeof e.id!=='string'||!e.id.startsWith('satellite:')||source.entities.getById(e.id)!==e)return false;
      return inspect(e.id.slice(10),false);
    },setVisible(next) {
      if(disposed||visible===next)return;visible=next;
      if(!visible)stop();else if(enabled){tickPositions();refresh();}updateStatus();
    },destroy() {
      if(disposed)return;disposed=true;stop();reportState('Satellites','');
      toggle.removeEventListener('change',toggleLayer);refreshButton.removeEventListener('click',manualRefresh);
      toggle.disabled=refreshButton.disabled=true;if(!viewer.isDestroyed())viewer.dataSources.remove(source,true);
    }};
  }};
})();
