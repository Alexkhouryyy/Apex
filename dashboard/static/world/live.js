/* World View panel: terrain, 3D buildings, saved notes and routes, and Ask
   Celine. The live layers themselves are world-earthquakes.js, world-flights.js
   and world-satellites.js; this panel only asks them (through `hooks`) what is
   on and selected, so Celine's answer is grounded in the same records the
   server fetched, never in renderer state. */
(() => {
  const $ = id => document.getElementById(id);
  window.ApexWorldLive = function(viewer, hooks = {}) {
    const C = window.Cesium;
    // On a phone the panel starts folded to one bar, so it never covers a selection's details.
    const narrow = window.matchMedia?.('(max-width: 760px)');
    const fold = () => { if (narrow?.matches && $('world-tools')) $('world-tools').open = false; };
    fold(); narrow?.addEventListener?.('change', fold);
    let disposed = false, thread = null, speech = null, audio = null;
    let terrainId = 0, buildings = null, buildingsId = 0, audioURL = null, chatController = null;
    let project = {version:1,items:[]}, route = [], undo = [];
    const token = () => localStorage.getItem('apex_token') || '';
    const headers = () => ({'Content-Type':'application/json', ...(token()?{Authorization:'Bearer '+token()}: {})});
    const request = (url, options={}) => fetch(url,{...options,headers:headers()});
    const say = text => {$('world-live-message').textContent=text;};
    async function deadline(promise) {
      let timer;
      try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Provider timeout')),15000);})]);}
      finally{clearTimeout(timer);}
    }
    // What Celine is told: which layers are on and which record is selected,
    // as ids the server resolves against its own feed caches.
    function sceneFields() {
      const scene = hooks.scene?.() || {};
      return {world_layer_ids: Array.isArray(scene.layers) ? scene.layers : [],
              ...(scene.entity ? {world_entity_id: scene.entity} : {})};
    }
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
      const p=window.ApexWorldLocation;
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
      const match=command.match(/^(show|hide) (flights|aircraft|satellites|stations|earthquakes)$/);
      if(match){
        const layer={aircraft:'flights',stations:'satellites'}[match[2]]||match[2], on=match[1]==='show';
        say(hooks.toggle?.(layer,on)?`${layer} ${on?'on; loading the feed':'off'}.`:'The globe is still loading; try again in a moment.');return;
      }
      if(command==='reset globe'){$('world-reset').click();say('Globe reset.');return;}
      if(chatController){say('Wait for the current answer or close World View to cancel.');return;}
      $('world-answer').textContent='Asking Apex…';chatController=new AbortController();const timer=setTimeout(()=>chatController?.abort(),90000);
      try {
        const response=await request('/api/chat',{method:'POST',signal:chatController.signal,
          body:JSON.stringify({message:question,thread_id:thread,...sceneFields()})});
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
    const api = {
      mark(location) {
        window.ApexWorldLocation=location;
        if($('world-route').dataset.drawing==='yes'&&route.length<100){route.push([location.lng,location.lat]);renderProject();}
      },
      clearSelection() {},
      dispose() {
        narrow?.removeEventListener?.('change', fold);
        disposed=true;++terrainId;++buildingsId;chatController?.abort();speech?.abort();audio?.pause();if(audioURL)URL.revokeObjectURL(audioURL);
      },
      validProject, sceneFields,
    };
    return api;
  };
})();
