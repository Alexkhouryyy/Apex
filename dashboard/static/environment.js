/* Apex capability management. API calls use the dashboard's owner credentials. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const say=(id,text)=>{$(id).textContent=String(text)};
  let serial=0;
  const post=(url,body)=>api(url,{method:'POST',body});
  function open(tab){document.querySelector('.nav-btn[data-tab="'+tab+'"]')?.click();}
  document.addEventListener('click',e=>{
    const link=e.target.closest('[data-open-tab]');if(link)open(link.dataset.openTab);
    if(e.target.closest('[data-environment-refresh]'))load();
  });
  $('sidebar-restart').addEventListener('click',()=>{open('system');$('ctl-restart').click()});
  // Keep one instance of the existing pairing controls and their event handlers.
  for(const id of ['devices-panel','access-panel']){const el=$(id);if(el)$('env-pairing').append(el)}

  async function load(){
    window.ApexPlugins?.load();
    try{
      const d=await api('/api/environment');
      const mcp=await api('/api/control/mcp').catch(()=>({servers:[],detail:'MCP status unavailable'}));
      $('env-plugins').innerHTML='<h2>Instruction bundles</h2>'+d.procedures.map(p=>`<article class="env-card"><h3>${esc(p.name)}</h3><p>${p.available?'Available':'Disabled or changed since review'} · ${p.imported?'Imported bundle':'Local procedure'}</p>${p.metadata.repo?`<p>${esc(p.metadata.repo)} @ ${esc(p.metadata.revision?.slice(0,12))}</p>`:''}${p.imported?`<button data-skill-toggle="${esc(p.name)}" data-enabled="${!p.metadata.enabled}">${p.metadata.enabled?'Disable':'Enable'}</button>`:''}</article>`).join('')||'';
      if(!d.procedures.length)$('env-plugins').innerHTML='<p>No instruction bundles installed. Add one from Repositories.</p>';
      $('env-plugins').innerHTML+='<h2>Runtime connections (MCP)</h2>'+(mcp.servers||[]).map(s=>`<article class="env-card"><h3>${esc(s.server)}</h3><p>${esc(s.state)} · ${s.enabled===false?'Disabled':'Enabled'} · ${s.tools||0} tools</p><button data-open-tab="mcp">Manage connection</button></article>`).join('')+`<p>${esc(mcp.detail||'Configure additional runtime capabilities in MCP or Apps.')}</p>`;
      $('env-skills').innerHTML=[...d.procedures.map(p=>({name:p.name,description:p.imported?'Imported procedure':'Local procedure'})),...d.executable].map(p=>`<article class="env-card"><h3>${esc(p.name)}</h3><p>${esc(p.description)}</p><button data-skill-read="${esc(p.name)}">View source</button></article>`).join('')||'<p>No skills installed yet.</p>';
      $('env-skills').innerHTML+=`<p>${d.forged.filter(x=>x.status==='pending').length} forged tools awaiting review. ${d.failures.length} skills have repeated recent failures.</p>`+d.failures.map(f=>`<p>${esc(f.name)}: ${f.failures} failures in ${f.total} runs. Select it above to develop an improvement.</p>`).join('');
      const selected=$('skill-existing').value;
      $('skill-existing').innerHTML='<option value="">Create a new skill</option>'+d.executable.map(p=>`<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('');
      if(d.executable.some(p=>p.name===selected))$('skill-existing').value=selected;
      $('env-repositories').innerHTML=d.repositories.map(r=>`<article class="env-card"><h3>${esc(r.repo)}</h3><p>${esc(r.revision.slice(0,12))} · ${r.skill_count} skill bundles · indexed, not installed</p><button data-repo-source="${esc(r.repo)}" data-repo-ref="${esc(r.revision)}">Open pinned index</button></article>`).join('')||'<p>No repositories indexed yet.</p>';
    }catch(e){for(const id of ['env-plugins','env-skills','env-repositories'])say(id,e.message||e)}
  }
  async function inspect(source,ref){
    const request=++serial;$('repo-review').replaceChildren();$('repo-result').replaceChildren();say('repo-status','Inspecting GitHub repository…');
    try{
      const r=await post('/api/environment/repositories',{source,ref});if(request!==serial)return;
      say('repo-status','Indexed '+r.repo+' at '+r.revision.slice(0,12)+'. Nothing has been installed.');
      $('repo-result').innerHTML=`<h2>${esc(r.repo)}</h2><p>${esc(r.note)}</p><p>Showing ${r.skills.length} of ${r.skill_count} discovered skills.</p><div>${r.skills.map(path=>`<button class="repo-skill" data-source="${esc(r.repo)}" data-revision="${esc(r.revision)}" data-path="${esc(path)}">Preview ${esc(path)}</button>`).join('')||'<p>No SKILL.md bundles found. Use the repository documentation to plan an Apex adapter or MCP integration.</p>'}</div><details><summary>Runtime manifests (${r.manifests.length})</summary><pre>${esc(r.manifests.join('\n'))}</pre></details><details><summary>README · repository content</summary><pre>${esc(r.readme||'No root README.md found.')}</pre></details>`;
      for(const path of r.manifests.filter(p=>p.endsWith('plugin.yaml'))){
        const button=document.createElement('button');button.className='repo-plugin';button.textContent='Review plugin: '+path;
        button.addEventListener('click',()=>{open('plugins');window.ApexPlugins.review('preview',{source:r.repo,ref:r.revision,subdir:path.includes('/')?path.slice(0,path.lastIndexOf('/')):''})});
        $('repo-result').append(button);
      }
      await load();
    }catch(e){if(request===serial)say('repo-status',e.message||e)}
  }
  $('repo-intake').addEventListener('submit',e=>{e.preventDefault();const f=new FormData(e.target);inspect(f.get('source'),f.get('ref')||'HEAD')});
  $('repo-intake').addEventListener('dragover',e=>e.preventDefault());
  $('repo-intake').addEventListener('drop',e=>{e.preventDefault();const text=e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain');$('repo-intake').elements.source.value=text.trim().split('\n').find(x=>!x.startsWith('#'))||'';});
  document.addEventListener('click',async e=>{
    const repo=e.target.closest('[data-repo-source]');if(repo){open('repositories');inspect(repo.dataset.repoSource,repo.dataset.repoRef);return}
    const preview=e.target.closest('.repo-skill');if(preview){
      const request=++serial;$('repo-review').replaceChildren();say('repo-status','Loading the pinned skill files…');
      try{
        const p=await post('/api/environment/preview',{source:preview.dataset.source,revision:preview.dataset.revision,path:preview.dataset.path});if(request!==serial)return;
        say('repo-status','Preview ready. Review the files and required tools before installing.');
        $('repo-review').innerHTML=`<h2>Skill preview</h2><p>${esc(p.review_help)}</p>${Object.entries(p.files).map(([name,text])=>`<details><summary>${esc(name)}</summary><pre>${esc(text)}</pre></details>`).join('')}<details><summary>Upstream license</summary><pre>${esc(p.license)}</pre></details>${p.unsupported.length?`<p>Requires adaptation: ${esc(p.unsupported.join(', '))}. Installation is unavailable for this bundle.</p>`:`<form id="repo-install" class="add-form vertical"><label>Install as<input name="name" required pattern="[A-Za-z0-9_-]+" maxlength="80" value="${esc(p.path.split('/').pop()||p.repo.split('/').pop())}"></label><label>Compatibility notes<textarea name="notes" required minlength="20" maxlength="2000" placeholder="Required tools, platform, dependencies and compatibility checks"></textarea></label><button type="submit">Install reviewed skill</button></form>`}`;
        $('repo-install')?.addEventListener('submit',async ev=>{ev.preventDefault();const f=new FormData(ev.target),button=ev.target.querySelector('button');button.disabled=true;
          try{const out=await post('/api/home/skills/install',{id:p.id,name:f.get('name'),notes:f.get('notes')});if(request===serial)say('repo-status','Installed '+out.name+'. Available to Apex on its next turn.');await load();}
          catch(err){if(request===serial)say('repo-status',err.message||err);button.disabled=false;}
        });
      }catch(err){if(request===serial)say('repo-status',err.message||err)}return;
    }
    const toggle=e.target.closest('[data-skill-toggle]');if(toggle){toggle.disabled=true;try{await post('/api/home/skills/enabled',{name:toggle.dataset.skillToggle,enabled:toggle.dataset.enabled==='true'});await load()}catch(err){toggle.disabled=false;say('env-plugins',err.message||err)}return}
    const read=e.target.closest('[data-skill-read]');if(read){try{const s=await api('/api/environment/skills/'+encodeURIComponent(read.dataset.skillRead));$('skill-source').hidden=false;say('skill-source',s.content)}catch(err){say('skill-status',err.message||err)}}
  });
  $('skill-develop').addEventListener('submit',async e=>{
    e.preventDefault();const button=e.target.querySelector('button');if(button.disabled)return;button.disabled=true;
    const f=new FormData(e.target);say('skill-status','Developing and validating the skill. This may take a few minutes…');
    try{const r=await post('/api/environment/develop',{description:f.get('description'),existing:f.get('existing')||null,needs_network:f.get('needs_network')==='on'});say('skill-status',r.result);await load()}
    catch(err){say('skill-status',err.message||err)}finally{button.disabled=false}
  });
  async function models(){try{const d=await api('/api/models');$('env-model').innerHTML=d.models.map(m=>`<option value="${esc(m.model)}" ${m.model===d.current?'selected':''} ${m.available?'':'disabled'}>${esc(m.model)} · ${esc(m.provider)}${m.available?'':' (key missing)'}</option>`).join('');say('env-model-status','Current: '+d.current)}catch(e){say('env-model-status',e.message||e)}}
  $('env-model-form').addEventListener('submit',async e=>{e.preventDefault();try{const r=await post('/api/model',{model:$('env-model').value});say('env-model-status',r.message||JSON.stringify(r));refreshStatus()}catch(err){say('env-model-status',err.message||err)}});
  async function channels(){try{const d=await api('/api/control/settings');const keys=d.settings.filter(s=>/TELEGRAM|DISCORD|SLACK|WHATSAPP|SIGNAL|WEBHOOK/.test(s.key));$('env-channels').innerHTML=keys.map(s=>`<p><strong>${esc(s.key)}</strong> · ${s.set?'Configured':'Not configured'}</p>`).join('')||'<p>No channel settings available.</p>'}catch(e){say('env-channels',e.message||e)}}
  window.ApexEnvironment={load,models,channels};
})();
