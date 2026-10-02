(() => {
  'use strict';
  const $=id=>document.getElementById(id),say=text=>{$('notice').hidden=!text;$('notice').textContent=text;};
  const token=()=>{try{return localStorage.getItem('apex_token')||'';}catch(_){return '';}};
  let ready=false;
  async function request(path,options={}){
    const r=await fetch('/api/apocalypse'+path,{...options,credentials:'same-origin',cache:'no-store',headers:{Authorization:'Bearer '+token(),...options.headers}});
    if(!r.ok){if(r.status===401||r.status===403)$('login').hidden=false;const d=await r.json().catch(()=>({}));throw Error(d.detail||'Request failed. Check the local launcher.');}
    return r;
  }
  async function load(){
    $('refresh').disabled=true;
    try{const d=await (await request('/status')).json();$('login').hidden=true;$('content').hidden=false;say('');
      $('mode').textContent=d.active?'APOCALYPSE SESSION · LOCAL MODE':'PREPARATION VIEW · NORMAL APEX';
      ready=d.active&&d.model.downloaded;
      $('send').disabled=!ready;
      $('model-status').textContent=d.model.selected+' · '+(d.model.downloaded?'Downloaded locally · test a fresh response':d.model.server_ready?'Model not downloaded yet':'Local model server is not running');
      $('storage').textContent=d.storage.path+'\\documents';$('free').textContent=d.storage.free_bytes===null?'Storage drive unavailable':Math.floor(d.storage.free_bytes/1024**3)+' GB available';
      $('nomad-status').textContent=d.nomad.ready?'NOMAD Command Center answered. Open it to check each installed app and its downloaded content.':d.nomad.docker_available?'NOMAD is not running. Prepare it while online, then start the cached stack.':'NOMAD needs Docker with Linux containers. Its Windows setup guide is included; native Apex chat does not require Docker.';
      $('nomad-open').hidden=!d.nomad.ready;
      $('upstream').textContent='Project NOMAD source pinned at '+d.nomad.revision.slice(0,7)+' · Apache-2.0 · content and apps retain their own licenses.';
      $('voice-status').textContent=d.voice.server_ready?'Local voice server answered. Playback and microphone still need a live test.':'Local Celine server is not running. Start your cached voice server if needed.';
      $('documents').replaceChildren();
      for(const row of d.documents){const li=document.createElement('li'),a=document.createElement('a');a.href='#';a.textContent=row.name;
        a.addEventListener('click',async e=>{e.preventDefault();try{const blob=await (await request('/documents/'+encodeURIComponent(row.name))).blob(),url=URL.createObjectURL(blob),download=document.createElement('a');download.href=url;download.download=row.name;download.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}catch(e){say(e.message);}});li.append(a);$('documents').append(li);}
      if(!d.documents.length){const li=document.createElement('li');li.textContent='No local documents added yet.';$('documents').append(li);}
    }catch(e){say(e.message);}finally{$('refresh').disabled=false;}
  }
  $('login').addEventListener('submit',e=>{e.preventDefault();try{localStorage.setItem('apex_token',$('token').value.trim());}catch(_){}$('token').value='';load();});
  $('refresh').addEventListener('click',load);
  $('chat').addEventListener('submit',async e=>{e.preventDefault();if(!ready)return;$('send').disabled=true;say('');$('answer').textContent='Apex is thinking locally…';try{const d=await (await request('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:$('message').value})})).json();$('answer').textContent=d.answer;}catch(e){$('answer').textContent='';say(e.message);}finally{$('send').disabled=!ready;}});
  $('upload').addEventListener('change',async()=>{const file=$('upload').files[0];if(!file)return;if(file.size>8*1024*1024){say('Copy files larger than 8 MB directly to the documents folder.');$('upload').value='';return;}try{await request('/documents/'+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});await load();say('Document saved locally.');}catch(e){say(e.message);}finally{$('upload').value='';}});
  load();
})();
