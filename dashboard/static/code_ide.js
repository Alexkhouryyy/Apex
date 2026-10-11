/* Theia stays attached while views change, preserving unsaved editor buffers. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id), sessions=new Map(), pending=new Map();
  let post,api,say,context,refresh,beforeOperation,serial=0;
  const status=(text,error=false)=>{const s=$('ide-status');s.textContent=text;s.classList.toggle('error',error);};
  const current=()=>sessions.get(context().id);
  async function checkpoint(sid){
    const result=await post(`/api/code/sessions/${sid}/ide-checkpoint`,{});
    if(result.warning){say(result.warning,'error');return false;}
    if(context().id===sid)await refresh();
    return true;
  }
  function save(){
    const s=current();if(!s)return Promise.resolve(true);
    if(s.savePromise)return s.savePromise;
    s.savePromise=saveSession().finally(()=>{s.savePromise=null;});
    return s.savePromise;
  }
  async function saveSession(){
    const sid=context().id,s=sessions.get(sid);if(!s)return true;
    if(context().busy&&!s.dirty)return true;
    if(context().busy){say('Wait for the current operation before saving. Your IDE edits are kept.','error');return false;}
    if(s.online===false){say('Reconnect the IDE before saving or changing this workspace.','error');return false;}
    if(!s.ready){say('Wait for the IDE to finish opening before changing this workspace.','error');return false;}
    const id=++serial;
    s.saving=true;
    const ok=await new Promise(resolve=>{
      const timer=setTimeout(()=>{pending.delete(id);say('The IDE did not confirm saving. Check its save dialog before continuing.','error');resolve(false);},15000);
      pending.set(id,{source:s.frame.contentWindow,resolve,value:sid,timer});
      s.frame.contentWindow.postMessage({channel:'apex-ide',type:'save',id},location.origin);
    });
    if(!ok||context().id!==sid){s.saving=false;return false;}
    try{return await checkpoint(sid);}catch(e){say(e.message,'error');return false;}
    finally{s.saving=false;}
  }
  async function open(){
    const c=context();if(!c.id||c.detail?.status!=='ready'||c.busy)return status('Open an idle workspace first.',true);
    if(!current()&&!await beforeOperation())return;
    $('ide-retry').disabled=true;status('Starting your IDE…');
    try{
      const result=await post(`/api/code/sessions/${c.id}/ide`,{});
      if(context().id!==c.id)return;
      if(current()){current().online=true;update();return;}
      const frame=document.createElement('iframe');frame.title='Apex Code IDE';frame.referrerPolicy='same-origin';
      sessions.set(c.id,{frame,ready:false,dirty:false,online:true,url:result.url});$('ide-frames').append(frame);
      frame.src=result.url;$('ide-close').hidden=false;$('ide-retry').hidden=false;
      status('Loading the editor and language extensions…');
    }catch(e){status(e.message,true);say(e.message,'error');}
    finally{$('ide-retry').disabled=false;}
  }
  async function close(){
    const sid=context().id;if(!await save())return;
    try{await api(`/api/code/sessions/${sid}/ide`,{method:'DELETE'});remove(sid);update();}
    catch(e){say(e.message,'error');}
  }
  function remove(sid){const s=sessions.get(sid);s?.frame.remove();sessions.delete(sid);}
  function openFile(path){
    const c=context(),s=current();if(!s?.ready)return false;
    const root=c.detail.worktree.replace(/\\/g,'/').replace(/^\/+|\/+$/g,'');
    const encoded=(root+'/'+path).split('/').map(encodeURIComponent).join('/').replace(/^([A-Za-z])%3A\//,'$1:/');
    s.frame.contentWindow.postMessage({channel:'apex-ide',type:'open-file',uri:'file:///'+encoded},location.origin);
    return true;
  }
  function update(){
    const c=context();for(const [sid,s] of sessions){s.frame.hidden=sid!==c.id;}
    const s=current();
    if(s&&c.detail&&c.detail.status!=='ready'){remove(c.id);}
    $('ide-close').hidden=!current();$('ide-retry').hidden=false;$('ide-retry').textContent=current()?'Reconnect IDE':'Open IDE';
    if(current()?.online===false)status('IDE disconnected. Reconnect to continue; your editor buffers are still here.',true);
    else if(current()?.ready)status(`${current().dirty?'Unsaved edits':'Ready'} · Python · JavaScript / TypeScript · Run and Debug · Extensions`);
    else if(!current())status('Open Full IDE for language intelligence, debugging and extensions.');
  }
  window.ApexCodeIDE={
    init(options){({post,api,say,context,refresh,beforeOperation}=options);
      $('ide-retry').onclick=open;$('ide-close').onclick=close;
      $('ide-focus').onclick=()=>{const focused=document.body.classList.toggle('ide-focus');$('ide-focus').textContent=focused?'Show Apex panels':'Focus IDE';};
      addEventListener('message',e=>{
        if(e.origin!==location.origin||e.data?.channel!=='apex-ide')return;
        const entry=[...sessions.entries()].find(([,s])=>s.frame.contentWindow===e.source);if(!entry)return;
        const [sid,s]=entry;
        if(e.data.type==='state'||e.data.type==='ready'){
          const wasDirty=s.dirty;s.dirty=e.data.dirty===true;
          if(e.data.type==='ready')s.ready=true;
          if(context().id===sid)update();
          // A manual Ctrl+S becomes an Undo checkpoint once the IDE reports clean.
          if(wasDirty&&!s.dirty&&!s.saving&&!context().busy&&![...pending.values()].some(p=>p.value===sid))
            checkpoint(sid).catch(error=>say(error.message,'error'));
        } else if(e.data.type==='saved'){
          const p=pending.get(e.data.id);if(!p||p.source!==e.source)return;
          clearTimeout(p.timer);pending.delete(e.data.id);
          if(!e.data.ok)say(e.data.error||'The IDE could not save.','error');p.resolve(e.data.ok===true);
        }
      });
      addEventListener('beforeunload',e=>{if([...sessions.values()].some(s=>s.dirty)){e.preventDefault();e.returnValue='';}});
      setInterval(async()=>{for(const [sid,s] of sessions){try{const result=await api(`/api/code/sessions/${sid}/ide`);s.online=result.running;if(context().id===sid)update();}catch{}}},5000);
    },open,save,update,openFile,
    dirty:()=>[...sessions.values()].some(s=>s.dirty),
    beforeLeaving:save
  };
})();
