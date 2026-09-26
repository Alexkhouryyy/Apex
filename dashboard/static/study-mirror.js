// Opt-in preview of the camera already owned by Apex. No second capture,
// recording, or gesture-control changes. One bounded request at a time.
export function setupStudyMirror({token}) {
  const button=document.getElementById('camera-mirror-toggle'),panel=document.getElementById('camera-mirror');
  const img=document.getElementById('camera-mirror-image'),label=document.getElementById('camera-mirror-status');
  let enabled=false,epoch=0,timer=null,request=null,url=null;
  function clear(){clearTimeout(timer);request?.abort();request=null;img.removeAttribute('src');if(url)URL.revokeObjectURL(url);url=null;}
  function refresh(){
    clear();const generation=++epoch;
    if(!enabled||document.hidden)return;
    async function sample(){
      if(!enabled||generation!==epoch||document.hidden)return;
      const started=performance.now(),controller=new AbortController();request=controller;
      const timeout=setTimeout(()=>controller.abort(),2000);
      try{
        const r=await fetch('/api/study/camera',{headers:{Authorization:'Bearer '+token()},signal:controller.signal,cache:'no-store'});
        if(!r.ok){let message='Camera preview unavailable';try{message=(await r.json()).detail||message;}catch(_){}throw Error(message);}
        const blob=await r.blob();
        if(!enabled||generation!==epoch||document.hidden)return;
        const next=URL.createObjectURL(blob),old=url;img.src=next;url=next;if(old)URL.revokeObjectURL(old);
        label.textContent='Live mirror';
      }catch(e){
        if(generation!==epoch||!enabled)return;
        img.removeAttribute('src');if(url)URL.revokeObjectURL(url);url=null;
        label.textContent=e.name==='AbortError'?'Camera preview timed out':e.message;
      }finally{clearTimeout(timeout);if(request===controller)request=null;}
      if(enabled&&generation===epoch&&!document.hidden)timer=setTimeout(sample,Math.max(0,(url?100:1000)-(performance.now()-started)));
    }
    label.textContent='Opening camera mirror…';sample();
  }
  button.onclick=()=>{
    enabled=!enabled;button.textContent=enabled?'Hide camera':'Camera mirror';button.setAttribute('aria-pressed',String(enabled));panel.hidden=!enabled;refresh();
  };
  document.addEventListener('visibilitychange',refresh);
  addEventListener('pagehide',()=>{enabled=false;++epoch;clear();});
}
