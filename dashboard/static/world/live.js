/* Live layers use server records; renderer state is never sent as trusted telemetry. */
(() => {
  const $ = id => document.getElementById(id);
  window.ApexWorldLive = function(viewer, markLocation) {
    const C = window.Cesium, active = new Map(), records = new Map(), entities = new Map();
    const colors = {flights:'#6de0ff',satellites:'#bb9aff',earthquakes:'#ffb56d'};
    let picked = null, following = false, disposed = false, thread = null, speech = null, audio = null;
    let terrainId = 0, buildings = null, buildingsId = 0, audioURL = null, chatController = null;
    let project = {version:1,items:[]}, route = [], undo = [];
    const token = () => localStorage.getItem('apex_token') || '';
    const headers = () => ({'Content-Type':'application/json', ...(token()?{Authorization:'Bearer '+token()}: {})});
    const request = (url, options={}) => fetch(url,{...options,headers:headers()});
    const say = text => {$('world-live-message').textContent=text;};
    const stamp = v => Number.isFinite(v) ? new Date(v*1000).toLocaleString() : 'Unknown';
    async function deadline(promise) {
      let timer;
      try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Provider timeout')),15000);})]);}
      finally{clearTimeout(timer);}
    }
    const aged = r => r.kind==='calculated' ? Date.now()/1000-r.epoch>3*86400 : r.id.startsWith('flights:') && Date.now()/1000-r.observed_at>120;
    function entityList() {
      const filter=$('world-entity-filter').value.toLowerCase();
      const list=$('world-entity-list');list.replaceChildren();
      const hint=document.createElement('option');hint.value='';hint.textContent=`${records.size} entities · showing up to 100 matches`;list.append(hint);
      for(const record of [...records.values()].filter(r=>(r.label+' '+r.id).toLowerCase().includes(filter)).slice(0,100)){
        const option=document.createElement('option');option.value=record.id;option.textContent=record.label+' · '+record.id.split(':')[0];list.append(option);
      }
      if(picked)list.value=picked.id;
    }
    $('world-entity-filter').addEventListener('input',entityList);
    $('world-entity-list').addEventListener('change',()=>{
      const record=records.get($('world-entity-list').value);if(!record)return;
      stopFollowing();readout(record);viewer.flyTo(entities.get(record.id),{duration:matchMedia('(prefers-reduced-motion: reduce)').matches?0:1});
    });
    function readout(record) {
      picked = record;
      $('world-entity').hidden=false;
      $('world-entity-name').textContent=record.label;
      const layer = active.get(record.id.split(':')[0]);
      $('world-entity-details').textContent = [record.id,`${record.lat.toFixed(3)}°, ${record.lng.toFixed(3)}°`,
        `Altitude ${(record.altitude_m/1000).toFixed(1)} km`,record.kind==='calculated'?
        `Calculated ${stamp(record.calculated_at)} · orbital epoch ${stamp(record.epoch)}`:`Position/event ${stamp(record.observed_at)}`,
        record.speed_m_s!=null?`Speed ${record.speed_m_s.toFixed(1)} m/s`:'',
        record.magnitude!=null?`Magnitude ${record.magnitude} · depth ${record.depth_km ?? '?'} km`:'',
        `${layer?.source || ''} · ${layer?.status || 'unavailable'}${aged(record)?' · aged position/orbit data':''}`].filter(Boolean).join('\n');
    }
    function stopFollowing() {following=false;viewer.trackedEntity=undefined;$('world-follow').textContent='Follow';}
    function clearSelection() {stopFollowing();picked=null;$('world-entity').hidden=true;}
    function remove(layer) {
      const state=active.get(layer); state?.controller?.abort(); clearTimeout(state?.timer);active.delete(layer);
      for(const [id,entity] of entities) if(id.startsWith(layer+':')) {viewer.entities.remove(entity);entities.delete(id);records.delete(id);}
      if(picked?.id.startsWith(layer+':'))clearSelection();
      entityList();
      viewer.scene.requestRender();
    }
    async function refresh(layer) {
      const state = active.get(layer); if(!state || disposed || document.hidden || state.controller)return;
      const controller = new AbortController(); state.controller=controller;
      const timeout=setTimeout(()=>controller.abort(),16000);
      try {
        const camera=viewer.camera.positionCartographic;
        const response=await request(`/api/world/layers/${layer}?lat=${C.Math.toDegrees(camera.latitude)}&lng=${C.Math.toDegrees(camera.longitude)}`,{signal:controller.signal});
        if(!response.ok)throw Error(response.status===401?'Sign in through Command to load layers.':'Layer request unavailable.');
        const data=await response.json(); if(disposed || active.get(layer)!==state)return;
        if(!Array.isArray(data.records))throw Error('Invalid layer response');
        Object.assign(state,{source:data.source,status:data.status,fetched_at:data.fetched_at});
        const received=new Set();
        for(const record of data.records) {
          if(typeof record.id!=='string' || !record.id.startsWith(layer+':') || !Number.isFinite(record.lat) || !Number.isFinite(record.lng))continue;
          if(Math.abs(record.lat)>90 || Math.abs(record.lng)>180 || !Number.isFinite(record.altitude_m))continue;
          received.add(record.id); records.set(record.id,record);
          const position=C.Cartesian3.fromDegrees(record.lng,record.lat,Math.max(0,record.altitude_m));
          let entity=entities.get(record.id);
          if(entity)entity.position=position;
          else {
            entity=viewer.entities.add({id:record.id,position,point:{pixelSize:layer==='earthquakes'?10:7,
              color:C.Color.fromCssColorString(colors[layer]),outlineColor:C.Color.BLACK,outlineWidth:1,
              disableDepthTestDistance:Infinity}});entities.set(record.id,entity);
          }
        }
        for(const [id,entity] of entities)if(id.startsWith(layer+':')&&!received.has(id)){viewer.entities.remove(entity);entities.delete(id);records.delete(id);}
        if(picked?.id.startsWith(layer+':')) {
          const current=records.get(picked.id);
          if(current)readout(current); else {clearSelection();say('Selected entity is no longer in this feed/region.');}
          if(data.status!=='fresh')stopFollowing();
        }
        const text = `${data.status.toUpperCase()} · ${data.records.length} · ${data.source}\nFetched ${stamp(data.fetched_at)} · ${data.coverage || ''}${data.message?'\n'+data.message:''}`;
        $('world-feed-'+layer).textContent=text;
        entityList();
        state.delay = data.status==='fresh' ? (layer==='satellites'?30000:60000) : Math.max(60000,Math.min(86400000,(data.retry_after||60)*1000));
        viewer.scene.requestRender();
      } catch(error) {
        if(active.get(layer)!==state || disposed)return;
        state.status='stale'; if(picked?.id.startsWith(layer+':')){readout(picked);stopFollowing();}
        $('world-feed-'+layer).textContent=(records.size?'STALE':'UNAVAILABLE')+' · '+(error.name==='AbortError'?'Request timed out.':error.message)+' Retry available.';
        state.delay=60000;
      } finally {
        clearTimeout(timeout); state.controller=null;
        if(!disposed&&active.get(layer)===state)state.timer=setTimeout(()=>refresh(layer),state.delay||60000);
      }
    }
    function enable(layer, on) {
      if(on) {if(!active.has(layer))active.set(layer,{});refresh(layer);}
      else {remove(layer);$('world-feed-'+layer).textContent='Off · no requests';}
      const box=document.querySelector(`[data-world-layer="${layer}"]`);if(box)box.checked=on;
    }
    document.querySelectorAll('[data-world-layer]').forEach(box=>box.addEventListener('change',()=>enable(box.dataset.worldLayer,box.checked)));
    $('world-refresh').addEventListener('click',()=>{for(const layer of active.keys()){clearTimeout(active.get(layer).timer);refresh(layer);}say('Refreshing enabled layers; provider cache and retry limits still apply.');});
    $('world-entity-clear').addEventListener('click',clearSelection);
    $('world-follow').addEventListener('click',()=>{
      if(following){stopFollowing();return;}
      if(!picked || aged(picked) || active.get(picked.id.split(':')[0])?.status!=='fresh'){say('Refresh before following a stale entity.');return;}
      viewer.trackedEntity=entities.get(picked.id); following=true;$('world-follow').textContent='Stop following';say('Following '+picked.label+'. Positions update with the feed; no invented movement.');
    });
    $('world-terrain').addEventListener('change',async()=>{
      const id=++terrainId,on=$('world-terrain').checked;
      if(!on){viewer.terrainProvider=new C.EllipsoidTerrainProvider();say('Flat globe enabled.');return;}
      say('Loading terrain…');
      try {
        const provider=await deadline(C.CesiumTerrainProvider.fromUrl('https://terrain.reearth.land/cesium-mesh/ellipsoid',
          {credit:new C.Credit('Terrain: Re:Earth / Mapterhorn · CC BY 4.0')}));
        if(!disposed&&id===terrainId){viewer.terrainProvider=provider;viewer.scene.requestRender();say('Terrain enabled.');}
      }catch(_){if(!disposed&&id===terrainId){$('world-terrain').checked=false;say('Terrain unavailable. Flat globe retained.');}}
    });
    $('world-buildings').addEventListener('change',async()=>{
      const id=++buildingsId;
      if(buildings){viewer.scene.primitives.remove(buildings);buildings=null;}
      if(!$('world-buildings').checked)return;
      const key=$('world-ion-token').value.trim();
      if(!key){$('world-buildings').checked=false;say('Add a scoped Cesium ion token to enable OSM buildings.');return;}
      say('Loading 3D buildings…');
      try {
        const resource=await deadline(C.IonResource.fromAssetId(96188,{accessToken:key}));
        if(disposed||id!==buildingsId)return;
        const tiles=await deadline(C.Cesium3DTileset.fromUrl(resource));
        if(disposed||id!==buildingsId){tiles.destroy();return;}
        buildings=viewer.scene.primitives.add(tiles);viewer.scene.requestRender();say('OSM 3D buildings enabled where coverage exists.');
      }catch(_){if(!disposed&&id===buildingsId){$('world-buildings').checked=false;say('3D buildings unavailable. Check token permissions/coverage.');}}
    });
    function validProject(value) {
      return value?.version===1 && Array.isArray(value.items) && value.items.length<=200 && value.items.every(item=>
        ['note','route'].includes(item.type) && typeof item.label==='string' && item.label.length<=120 &&
        Array.isArray(item.points)&&item.points.length>0&&item.points.length<=100&&
        item.points.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90));
    }
    function renderProject() {
      for(const entity of [...viewer.entities.values])if(String(entity.id).startsWith('project:'))viewer.entities.remove(entity);
      project.items.forEach((item,i)=>{
        const first=item.points[0],base={id:'project:'+i,position:C.Cartesian3.fromDegrees(...first),
          label:{text:item.label,font:'13px sans-serif',fillColor:C.Color.WHITE,showBackground:true,disableDepthTestDistance:Infinity}};
        if(item.type==='route')base.polyline={positions:C.Cartesian3.fromDegreesArray(item.points.flat()),width:3,material:C.Color.YELLOW,clampToGround:true};
        else base.point={pixelSize:9,color:C.Color.YELLOW};
        viewer.entities.add(base);
      });
      $('world-project-count').textContent=`${project.items.length} saved items · ${route.length} route points`;
      try{localStorage.setItem('apex.world.project.v1',JSON.stringify(project));}catch(_){say('Browser could not save this project. Export a copy.');}
      viewer.scene.requestRender();
    }
    try{const saved=JSON.parse(localStorage.getItem('apex.world.project.v1'));if(validProject(saved))project=saved;}catch(_){}
    renderProject();
    function changeProject(next) {undo.push(JSON.stringify(project));if(undo.length>20)undo.shift();project=next;renderProject();}
    $('world-add-note').addEventListener('click',()=>{
      const text=$('world-note-label').value.trim().slice(0,120);
      if(!text || project.items.length>=200){say('Enter a note; project limit is 200 items.');return;}
      const p=picked || window.ApexWorldLocation;
      if(!p){say('Select an entity or mark a location first.');return;}
      changeProject({...project,items:[...project.items,{type:'note',label:text,points:[[p.lng,p.lat]]}]});say('Note saved.');
    });
    $('world-route').addEventListener('click',()=>{
      if($('world-route').dataset.drawing==='yes') {
        if(route.length<2){say('Mark at least two locations for a route.');return;}
        if(project.items.length>=200){say('Project limit reached.');return;}
        changeProject({...project,items:[...project.items,{type:'route',label:$('world-note-label').value.trim().slice(0,120)||'Route',points:route}]});
        route=[];$('world-route').dataset.drawing='';$('world-route').textContent='Draw route';renderProject();say('Route saved.');
      }else{$('world-route').dataset.drawing='yes';route=[];$('world-route').textContent='Save route';say('Click globe locations to add route points, then Save route.');}
    });
    $('world-project-undo').addEventListener('click',()=>{if(undo.length){project=JSON.parse(undo.pop());renderProject();say('Project change undone.');}});
    $('world-project-export').addEventListener('click',()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(project,null,2)],{type:'application/json'}));
      const link=document.createElement('a');link.href=url;link.download='apex-world-project.json';link.click();URL.revokeObjectURL(url);
    });
    $('world-project-import').addEventListener('change',async event=>{
      const file=event.target.files?.[0];if(!file)return;
      try{if(file.size>200000)throw Error();const next=JSON.parse(await file.text());if(!validProject(next))throw Error();changeProject(next);say('Project imported. Undo restores the previous project.');}
      catch(_){say('Invalid project. Existing project retained.');}finally{event.target.value='';}
    });
    async function speak(text) {
      if(!$('world-speak').checked)return;
      try {
        const response=await request('/api/speak',{method:'POST',signal:chatController?.signal,body:JSON.stringify({text:text.slice(0,4000),engine:'voicebox'})});
        if(!response.ok)throw Error();const blob=await response.blob();if(disposed)return;
        if(audio)audio.pause();if(audioURL)URL.revokeObjectURL(audioURL);
        audioURL=URL.createObjectURL(blob);audio=new Audio(audioURL);await audio.play();
      }catch(_){if(!disposed)say('Answer is available as text. Enable your local Celine/Voicebox service for speech.');}
    }
    async function ask(question) {
      const command=question.toLowerCase().replace(/[.!?]+$/,'').trim();
      const match=command.match(/^(show|hide) (flights|satellites|earthquakes)$/);
      if(match){enable(match[2],match[1]==='show');say(`${match[2]} ${match[1]==='show'?'enabled; loading provider data':'disabled'}.`);return;}
      if(command==='follow selected'||command==='follow it'){$('world-follow').click();return;}
      if(command==='stop following'){stopFollowing();say('Following stopped.');return;}
      if(command==='reset globe'){$('world-reset').click();say('Globe reset.');return;}
      if(chatController){say('Wait for the current answer or close World View to cancel.');return;}
      $('world-answer').textContent='Asking Apex…';chatController=new AbortController();const timer=setTimeout(()=>chatController?.abort(),90000);
      try {
        const response=await request('/api/chat',{method:'POST',signal:chatController.signal,
          body:JSON.stringify({message:question,thread_id:thread,world_entity_id:picked?.id,world_layer_ids:[...active.keys()]})});
        const data=await response.json();if(!response.ok)throw Error(data.detail||data.error||'Apex unavailable');
        if(disposed)return;thread=data.thread_id;$('world-answer').textContent=data.response;await speak(data.response);
      }catch(error){if(!disposed)$('world-answer').textContent=error.name==='AbortError'?'Request cancelled or timed out.':error.message;}
      finally{clearTimeout(timer);chatController=null;}
    }
    $('world-ask').addEventListener('submit',event=>{event.preventDefault();const q=$('world-question').value.trim();if(q)ask(q);});
    $('world-microphone').addEventListener('click',()=>{
      const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
      if(!Recognition){say('This browser has no speech recognition. Type your question; Celine can still speak the answer.');return;}
      if(speech){speech.stop();return;}
      speech=new Recognition();speech.lang='en-US';speech.continuous=false;
      speech.onresult=event=>{const q=event.results[0][0].transcript;$('world-question').value=q;ask(q);};
      speech.onerror=()=>say('Microphone/recognition unavailable. Type your question.');
      speech.onend=()=>{speech=null;$('world-microphone').textContent='Microphone';};
      try{speech.start();$('world-microphone').textContent='Stop listening';}catch(_){speech=null;say('Could not start microphone.');}
    });
    function visibility() {
      for(const [layer,state] of active){clearTimeout(state.timer);if(document.hidden)state.controller?.abort();else refresh(layer);}
    }
    document.addEventListener('visibilitychange',visibility);
    const ageTimer=setInterval(()=>{if(!document.hidden&&picked){readout(picked);if(aged(picked))stopFollowing();}},10000);
    const api = {
      pick(event) {
        const hit=viewer.scene.pick(event.position),record=records.get(hit?.id?.id);
        if(record){if(picked?.id!==record.id)stopFollowing();readout(record);return true;}
        clearSelection();return false;
      },
      mark(location) {
        window.ApexWorldLocation=location;
        if($('world-route').dataset.drawing==='yes'&&route.length<100){route.push([location.lng,location.lat]);renderProject();}
      },
      clearSelection,
      dispose() {
        disposed=true;++terrainId;++buildingsId;chatController?.abort();speech?.abort();audio?.pause();if(audioURL)URL.revokeObjectURL(audioURL);
        clearInterval(ageTimer);
        for(const layer of [...active.keys()])remove(layer);document.removeEventListener('visibilitychange',visibility);
      },enable,refresh,records,validProject,
    };
    return api;
  };
})();
