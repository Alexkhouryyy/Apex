(() => {
  'use strict';
  const $=id=>document.getElementById(id),say=text=>{$('notice').hidden=!text;$('notice').textContent=text;};
  const token=()=>{try{return localStorage.getItem('apex_token')||'';}catch(_){return '';}};
  let ready=false,busy=false,session=null,dirty=false,library='';
  const fields=['brief','decisions','artifacts','next_step'];
  const savedProject=()=>{try{return localStorage.getItem('apex_offline_project')||'';}catch(_){return '';}};
  function controls(){
    $('send').disabled=!ready||!session||busy;
    $('project').disabled=!session||busy;
    $('refresh').disabled=busy;
    for(const id of [...fields,'save-handoff','reload-handoff'])$(id).disabled=!session||busy;
  }
  function renderHistory(rows){
    $('history').replaceChildren();
    for(const row of rows){const block=document.createElement('div'),label=document.createElement('strong'),text=document.createElement('p');block.className='chat-entry';label.textContent=row.role==='user'?'You':'Apex';text.textContent=row.text;block.append(label,text);$('history').append(block);}
    $('history').scrollTop=$('history').scrollHeight;
  }
  async function loadSession(wid='',preserveDraft=false){
    const d=await(await request('/session'+(wid?'?workspace_id='+encodeURIComponent(wid):''))).json();
    const same=session&&session.project.id===d.project.id;
    const oldRevision=session&&session.project.revision;
    session=d;
    session.savedRevision=d.project.revision;
    $('project').replaceChildren();
    for(const p of d.projects){const option=document.createElement('option');option.value=p.id;option.textContent=p.name;option.selected=p.id===d.project.id;$('project').append(option);}
    if(!(preserveDraft&&same&&dirty)){
      for(const key of fields)$(key).value=d.project.data[key];dirty=false;
    }else session.project.revision=oldRevision;
    renderHistory(d.messages);
    $('session-status').textContent=d.project.name+' · '+d.messages.length+' recent saved messages · saved handoff version '+session.savedRevision+(dirty?' · draft has unsaved changes':'');
    try{localStorage.setItem('apex_offline_project',d.project.id);}catch(_){}
    controls();
  }
  async function request(path,options={}){
    const saved=token();let r;
    try{r=await fetch('/api/apocalypse'+path,{...options,credentials:'same-origin',cache:'no-store',headers:{...(saved?{Authorization:'Bearer '+saved}:{}),...options.headers}});}
    catch(_){$('mode').textContent='SESSION UNREACHABLE';throw Error('Cannot reach this Apex session. Keep Start-Apex-Apocalypse.cmd running, then refresh this page.');}
    if(r.status===401||r.status===403){
      $('login').hidden=false;$('content').hidden=true;ready=false;$('send').disabled=true;
      $('mode').textContent='OWNER SIGN-IN REQUIRED';
      throw Error(saved?'That token could not unlock this page. Use your Apex owner dashboard token.':'Enter your Apex owner dashboard token to unlock this page.');
    }
    if(!r.ok){const d=await r.json().catch(()=>({}));const error=Error(d.detail||d.error||'This request is temporarily unavailable. Check the launcher and refresh.');error.status=r.status;throw error;}
    return r;
  }
  async function load(){
    if(busy)return;busy=true;ready=false;controls();
    try{const d=await (await request('/status')).json();$('login').hidden=true;$('content').hidden=false;say('');
      $('mode').textContent=d.active?'APOCALYPSE SESSION · LOCAL MODE':'PREPARATION VIEW · NORMAL APEX';
      ready=d.active&&d.model.downloaded;
      $('model-status').textContent=d.model.selected+' · '+(d.model.downloaded?'Downloaded locally · test a fresh response':d.model.server_ready?'Model not downloaded yet':'Local model server is not running');
      library=d.storage.path+'\\documents';$('storage').textContent=library;$('free').textContent=d.storage.free_bytes===null?'Storage drive unavailable':Math.floor(d.storage.free_bytes/1024**3)+' GB available';
      $('nomad-status').textContent=d.nomad.ready?'NOMAD Command Center answered. Open it to check each installed app and its downloaded content.':d.nomad.docker_available?'NOMAD is not running. Prepare it while online, then start the cached stack.':'NOMAD needs Docker with Linux containers. Its Windows setup guide is included; native Apex chat does not require Docker.';
      $('nomad-open').hidden=!d.nomad.ready;
      $('upstream').textContent='Project NOMAD source pinned at '+d.nomad.revision.slice(0,7)+' · Apache-2.0 · content and apps retain their own licenses.';
      $('voice-status').textContent=d.voice.server_ready?'Local voice server answered. Playback and microphone still need a live test.':'Local Celine server is not running. Start your cached voice server if needed.';
      const p=d.preparation||{exists:false};
      $('preparation-status').textContent=p.error||(!p.exists?'Run Download-Apex-Apocalypse.cmd while online to prepare English knowledge and worldwide maps.':p.downloaded+' of '+p.total+' archives downloaded and checksum verified · '+(p.bytes/1024**3).toFixed(1)+' GiB in the full plan · last saved progress'+(p.needs_attention?' · '+p.needs_attention+' downloads need attention':''));
      $('preparation-pending').replaceChildren();
      for(const row of p.remaining_setup||[]){const li=document.createElement('li');li.textContent=row.id+': '+row.reason;$('preparation-pending').append(li);}
      $('documents').replaceChildren();
      for(const row of d.documents){const li=document.createElement('li'),a=document.createElement('a');a.href='#';a.textContent=row.name;
        a.addEventListener('click',async e=>{e.preventDefault();try{const blob=await (await request('/documents/'+encodeURIComponent(row.name))).blob(),url=URL.createObjectURL(blob),download=document.createElement('a');download.href=url;download.download=row.name;download.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}catch(e){say(e.message);}});li.append(a);
        if(/\.(txt|md|pdf)$/i.test(row.name)){const use=document.createElement('button');use.className='document-use';use.textContent='Use in chat';use.addEventListener('click',()=>{$('message').value='Read the local document at '+library+'\\'+row.name+'. Summarize it with a source reference, then help me decide the next step.';$('message').focus();});li.append(use);}
        $('documents').append(li);}
      if(!d.documents.length){const li=document.createElement('li');li.textContent='No local documents added yet.';$('documents').append(li);}
      try{await loadSession(session?session.project.id:savedProject(),true);}catch(e){if(e.status===400){await loadSession();}else throw e;}
    }catch(e){ready=false;say(e.message);}finally{busy=false;controls();}
  }
  $('login').addEventListener('submit',e=>{e.preventDefault();try{localStorage.setItem('apex_token',$('token').value.trim());}catch(_){}$('token').value='';load();});
  $('refresh').addEventListener('click',load);
  $('project').addEventListener('change',async()=>{
    if(dirty&&!confirm('Discard the unsaved handoff draft and open this project?')){$('project').value=session.project.id;return;}
    const old=session.project.id;busy=true;controls();
    try{await loadSession($('project').value);$('answer').textContent='';say('');}catch(e){$('project').value=old;say(e.message);}finally{busy=false;controls();}
  });
  for(const key of fields)$(key).addEventListener('input',()=>{dirty=true;$('session-status').textContent='Handoff draft has unsaved changes.';});
  $('handoff').addEventListener('submit',async e=>{
    e.preventDefault();if(!session||busy)return;busy=true;controls();
    try{const d=await(await request('/projects/'+encodeURIComponent(session.project.id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:Object.fromEntries(fields.map(k=>[k,$(k).value])),revision:session.project.revision})})).json();session.project.revision=session.savedRevision=d.revision;dirty=false;say('Project handoff saved.');$('session-status').textContent=session.project.name+' · handoff version '+d.revision;}catch(e){say(e.message);}finally{busy=false;controls();}
  });
  $('reload-handoff').addEventListener('click',async()=>{if(dirty&&!confirm('Discard your draft and reload the saved handoff?'))return;try{await loadSession(session.project.id);say('Saved handoff reloaded.');}catch(e){say(e.message);}});
  $('chat').addEventListener('submit',async e=>{
    e.preventDefault();if(!ready||!session||busy)return;busy=true;controls();say('');$('answer').textContent='Apex is thinking locally…';
    let failure='';const previousRevision=session.savedRevision;
    try{await request('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:$('message').value,workspace_id:session.project.id})});$('message').value='';}catch(e){failure=e.message;}
    try{await loadSession(session.project.id,true);}catch(e){failure=failure||e.message;}
    $('answer').textContent='';say(failure||('Chat saved. Project handoff version '+session.savedRevision+(session.savedRevision===previousRevision?' is unchanged. Save the handoff below if needed.':' was updated.')));busy=false;controls();
  });
  $('upload').addEventListener('change',async()=>{const file=$('upload').files[0];if(!file)return;if(file.size>8*1024*1024){say('Copy files larger than 8 MB directly to the documents folder.');$('upload').value='';return;}try{await request('/documents/'+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});await load();say('Document saved locally.');}catch(e){say(e.message);}finally{$('upload').value='';}});
  load();
})();
