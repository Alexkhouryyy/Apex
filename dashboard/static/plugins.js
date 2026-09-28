/* Executable plugins: review immutable source, install, configure, activate. */
(() => {
  const $=id=>document.getElementById(id);
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const post=(action,body)=>api('/api/environment/plugins/'+action,{method:'POST',body});
  const status=text=>{$('plugin-status').textContent=String(text)};
  let items=[], reviewSerial=0, loadSerial=0;
  function field(key,spec,settings){
    const value=settings[key]??spec.default??'',type=spec.type||'str',base=`name="${esc(key)}" ${spec.required?'required':''}`;
    if(type==='secret'||spec.secret)return `<p>${esc(key)} requires a secret-setting adapter. Configure secrets through environment variables.</p>`;
    let input;
    if(type==='bool'||type==='boolean')input=`<input ${base} type="checkbox" ${value?'checked':''}>`;
    else if(['int','integer','float','number'].includes(type))input=`<input ${base} type="number" step="${['int','integer'].includes(type)?'1':'any'}" value="${esc(value)}">`;
    else if(['dict','object','list','array'].includes(type))input=`<textarea ${base}>${esc(value===''?'':JSON.stringify(value,null,2))}</textarea>`;
    else input=`<input ${base} value="${esc(value)}">`;
    return `<label>${esc(key)}${input}<small>${esc(spec.description||'')}</small></label>`;
  }
  async function load(){
    const serial=++loadSerial;
    try{
      const d=await api('/api/environment/plugins');if(serial!==loadSerial)return;items=d.plugins||[];
      $('plugin-bundled').innerHTML=(d.bundled||[]).map(p=>`<article class="env-card"><h3>${esc(p.name)} <small>${esc(p.version)}</small></h3><p>${esc(p.description)}</p><button data-plugin-bundled="${esc(p.name)}">Review bundled plugin</button></article>`).join('')||'<p>No bundled plugins available.</p>';
      $('plugin-installed').innerHTML=items.map(p=>{
        const m=p.package.manifest;
        return `<article class="env-card" data-plugin-card="${esc(p.name)}"><h3>${esc(p.name)} <small>${esc(m.version||'unversioned')}</small></h3><p>${esc(m.description||'')}</p><p>${p.enabled?(p.loaded?'Active':'Enabled · loads when next used'):'Disabled'} · ${esc(p.package.repo)} @ ${esc(p.package.revision.slice(0,12))}</p>
          ${(p.issues||[]).map(x=>`<p>${esc(x)}</p>`).join('')}${p.error?`<p>Last error: ${esc(p.error)}</p>`:''}
          <p>Tools: ${esc((p.tools?.length?p.tools:m.provides_tools||[]).join(', ')||'none')} · Hooks: ${esc((p.hooks?.length?p.hooks:m.provides_hooks||[]).join(', ')||'none')}</p>
          ${p.commands?.length?`<p>Commands: ${p.commands.map(c=>`<code>/${esc(p.name)}:${esc(c)}</code>`).join(' ')}</p>`:''}
          ${!p.enabled?'<label><input type="checkbox" data-plugin-trust> I trust this plugin to run code with Apex host permissions.</label>':''}
          <div class="controls"><button data-plugin-enable="${esc(p.name)}" data-enabled="${!p.enabled}">${p.enabled?'Disable':'Enable'}</button><button data-plugin-update="${esc(p.name)}">Check update</button><button data-plugin-action="rollback" data-name="${esc(p.name)}" ${p.history?'':'disabled'}>Roll back</button><button data-plugin-action="remove" data-name="${esc(p.name)}">Remove</button></div>
          ${Object.keys(m.config_schema||{}).length?`<details><summary>Settings${p.enabled?' · disable to edit':''}</summary><form data-plugin-settings="${esc(p.name)}" class="add-form vertical"><fieldset ${p.enabled?'disabled':''}>${Object.entries(m.config_schema).map(([key,spec])=>field(key,spec,p.settings)).join('')}<button>Save settings</button></fieldset></form></details>`:''}</article>`;
      }).join('')||'<p>No executable plugins installed. Review a GitHub or bundled plugin above.</p>';
      $('plugin-providers').innerHTML=['memory','context'].map(kind=>{
        const selected=d.selected?.[kind]||'';
        const options=items.filter(p=>p.enabled).flatMap(p=>(p.providers||[]).filter(x=>x.startsWith(kind+':')).map(x=>p.name+':'+x.split(':')[1]));
        if(selected&&!options.includes(selected))options.push(selected);
        return `<form data-plugin-provider="${kind}" class="add-form"><label>${kind==='memory'?'Memory provider':'Context engine'}<select name="selected"><option value="">Apex built-in</option>${options.map(x=>`<option value="${esc(x)}" ${x===selected?'selected':''}>${esc(x)}</option>`).join('')}</select></label><button>Use provider</button></form>`;
      }).join('')+'<p>Memory providers handle remember/recall. Context engines summarize older conversation messages. Existing memory records stay with their provider; switching does not migrate data.</p>';
    }catch(e){status(e.message||e)}
  }
  async function review(action,body){
    const serial=++reviewSerial;$('plugin-review').replaceChildren();$('plugin-source').hidden=true;status('Downloading and inspecting the pinned package…');
    try{
      const p=await post(action,body);if(serial!==reviewSerial)return;
      const existing=items.find(x=>x.name===p.manifest.name);
      const replace=p.replace||existing?.package.digest;
      status(action==='update'&&!p.changed?'Already at the reviewed version.':'Review ready. No plugin code has run.');
      $('plugin-review').innerHTML=`<article class="env-card"><h2>Review ${esc(p.manifest.name)}</h2><p>${esc(p.repo)} @ ${esc(p.revision)}</p><p>${esc(p.notice)}</p>${existing?`<p>Installed revision: ${esc(existing.package.revision.slice(0,12))}. Applying this version disables it until you enable it again.</p>`:''}${(p.issues||[]).map(x=>`<p>${esc(x)}</p>`).join('')}<details><summary>Manifest and requested features</summary><pre>${esc(JSON.stringify(p.manifest,null,2))}</pre></details><details><summary>Source files (${p.files.length})</summary>${p.files.map(f=>`<button data-plugin-file="${esc(f.path)}" data-review="${esc(p.id)}">${esc(f.path)} (${f.bytes} bytes)</button>`).join(' ')}</details><button id="plugin-install" ${action==='update'&&!p.changed?'disabled':''}>${existing?'Apply reviewed update':'Install disabled'}</button></article>`;
      $('plugin-install').addEventListener('click',async e=>{e.target.disabled=true;try{const r=await post('install',{id:p.id,replace});if(serial===reviewSerial){status(r.message);$('plugin-review').replaceChildren()}await load()}catch(err){if(serial===reviewSerial)status(err.message||err);e.target.disabled=false}});
    }catch(e){if(serial===reviewSerial)status(e.message||e)}
  }
  $('plugin-intake').addEventListener('submit',e=>{e.preventDefault();const f=new FormData(e.target);review('preview',{source:f.get('source'),ref:f.get('ref')||'HEAD',subdir:f.get('subdir')||''})});
  $('plugin-intake').addEventListener('dragover',e=>e.preventDefault());
  $('plugin-intake').addEventListener('drop',e=>{e.preventDefault();$('plugin-intake').elements.source.value=(e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain')).split('\n').find(x=>x&&!x.startsWith('#'))?.trim()||''});
  document.addEventListener('click',async e=>{
    if(e.target.closest('[data-plugin-refresh]')){load();return}
    const bundled=e.target.closest('[data-plugin-bundled]');if(bundled){review('bundled',{name:bundled.dataset.pluginBundled});return}
    const update=e.target.closest('[data-plugin-update]');if(update){review('update',{name:update.dataset.pluginUpdate});return}
    const file=e.target.closest('[data-plugin-file]');if(file){const serial=reviewSerial;try{const r=await post('file',{id:file.dataset.review,path:file.dataset.pluginFile});if(serial!==reviewSerial)return;$('plugin-source').textContent=r.path+'\n\n'+r.content;$('plugin-source').hidden=false}catch(err){if(serial===reviewSerial)status(err.message||err)}return}
    const toggle=e.target.closest('[data-plugin-enable]');if(toggle){
      const enabled=toggle.dataset.enabled==='true',trust=!!toggle.closest('[data-plugin-card]').querySelector('[data-plugin-trust]')?.checked;
      if(enabled&&!trust){status('Review the source and check the trust box before enabling.');return}
      toggle.disabled=true;try{const r=await post('enabled',{name:toggle.dataset.pluginEnable,enabled,trust});status(r.message);await load()}catch(err){status(err.message||err);toggle.disabled=false}return;
    }
    const action=e.target.closest('[data-plugin-action]');if(action){action.disabled=true;try{const r=await post('change',{name:action.dataset.name,action:action.dataset.pluginAction});status(r.message);await load()}catch(err){status(err.message||err);action.disabled=false}}
  });
  document.addEventListener('submit',async e=>{
    const form=e.target;if(!form.matches('[data-plugin-settings],[data-plugin-provider]'))return;e.preventDefault();
    const button=form.querySelector('button');button.disabled=true;
    try{
      let r;
      if(form.dataset.pluginProvider)r=await post('provider',{kind:form.dataset.pluginProvider,selected:new FormData(form).get('selected')});
      else{
        const p=items.find(x=>x.name===form.dataset.pluginSettings),settings={};
        for(const [key,spec] of Object.entries(p.package.manifest.config_schema||{})){
          const input=form.elements.namedItem(key);if(!input)continue;const t=spec.type||'str';
          if(['bool','boolean'].includes(t))settings[key]=input.checked;
          else if(input.value!=='')settings[key]=['int','integer','float','number'].includes(t)?Number(input.value):['list','array','dict','object'].includes(t)?JSON.parse(input.value):input.value;
        }
        r=await post('settings',{name:p.name,settings});
      }
      status(r.message);await load();
    }catch(err){status(err.message||err)}finally{button.disabled=false}
  });
  window.ApexPlugins={load,review};
})();
