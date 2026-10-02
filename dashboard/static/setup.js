/* Setup reuses the existing credential stores and original provider registry. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  let audioUrl, loading = false, voiceProfile = 'celine';
  const token = () => {try{return localStorage.getItem('apex_token')||'';}catch(_){return '';}};
  const notice = text => {$('notice').hidden=!text;$('notice').textContent=text;};
  async function request(url, body, blob=false) {
    const r=await fetch(url,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
      headers:{Authorization:'Bearer '+token(),...(body===undefined?{}:{'Content-Type':'application/json'})},
      body:body===undefined?undefined:JSON.stringify(body)});
    if(!r.ok){const d=await r.json().catch(()=>({}));if(r.status===401||r.status===403)$('login').hidden=false;
      throw new Error(d.error||(typeof d.detail==='string'?d.detail:'Setup request failed. Refresh and try again.'));}
    return blob?r.blob():r.json();
  }
  async function busy(button, fn) {button.disabled=true;notice('');try{await fn();}catch(e){notice(e.message);}finally{button.disabled=false;}}
  function providerCard(key) {
    const card=el('article',undefined,'card');card.append(el('h3',key.title),el('p',key.unlocks,'description'),
      el('p',key.set?(key.managed==='external'?'Configured outside this panel':'Key saved · feed not tested'):'Key needed','key-state'));
    const link=el('a','Provider setup guide ↗');
    try{const url=new URL(key.getUrl);if(url.protocol==='https:'){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}}catch(_){}
    if(key.clientExposed)card.append(el('p','This map key is used by the browser. Restrict it to your Apex origin in the provider console.','small'));
    if(key.managed==='external'){card.append(el('p','Managed by Apex’s environment. Change it in Settings & keys or its original store.','small'));return card;}
    const form=el('form'),fields=[];
    for(const name of key.envVars){const label=el('label',name),input=el('input');input.type='password';input.autocomplete='off';input.name=name;input.placeholder=key.set?'Saved — blank keeps it':'Paste key';label.append(input);form.append(label);fields.push(input);}
    const save=el('button','Save provider');save.type='submit';form.append(save);
    form.addEventListener('submit',e=>{e.preventDefault();busy(save,async()=>{
      const updates={};for(const input of fields)if(input.value.trim())updates[input.name]=input.value.trim();
      if(!Object.keys(updates).length)throw new Error('Paste a key before saving. Existing keys were kept.');
      const pending=request('/world/engine/api/setup/keys',updates);fields.forEach(i=>i.value='');
      const saved=await pending;renderProviders(saved.status);notice('Provider saved. Map settings may take a moment to apply. Open World View to test its live layer.');
    });});card.append(form);return card;
  }
  function renderProviders(d){$('provider-cards').replaceChildren(...d.keys.map(providerCard));$('provider-status').textContent=`${d.setCount} of ${d.total} optional providers configured.`;}
  async function providers(){
    $('provider-status').textContent='Loading optional providers…';
    try{await request('/api/world/engine/session',{});const d=await request('/world/engine/api/setup/status');
      renderProviders(d);
    }catch(e){$('provider-status').textContent=e.message;}
  }
  async function load(){
    if(loading)return;loading=true;$('refresh').disabled=true;
    try{const d=await request('/api/setup/status');notice('');$('login').hidden=true;$('setup-content').hidden=false;
      $('engine-status').textContent=d.engine.installed?'Full engine installed · '+d.engine.revision.slice(0,7):'Engine setup needed. Run Setup-Apex-World.cmd once.';
      voiceProfile=d.voice.profile_id||'celine';$('voice-status').textContent=d.voice.detail;$('test-voice').disabled=d.voice.state!=='ready';
      $('model-status').textContent=d.model.name+' · '+(d.model.configured?'Provider configured · scene command not tested':'Provider key or local model setup needed');
      $('apps-status').textContent=d.apps.configured?'Catalog key saved. Open Apps to authorize your accounts.':'Add your project key to browse the live catalog.';
      $('composio-form').hidden=d.apps.configured;
      $('world-update-status').textContent=d.updates.detail||'World View uses a reviewed, pinned release. Check GitHub below.';
      if(d.engine.installed)await providers();
    }catch(e){notice(e.message);}finally{loading=false;$('refresh').disabled=false;}
  }
  $('login-form').addEventListener('submit',e=>{e.preventDefault();try{localStorage.setItem('apex_token',$('owner-token').value.trim());}catch(_){}$('owner-token').value='';load();});
  $('composio-form').addEventListener('submit',e=>{e.preventDefault();busy(e.submitter,async()=>{
    const pending=request('/api/apps/settings',{key:$('composio-key').value.trim()});$('composio-key').value='';await pending;await load();notice('Catalog connected. Open Apps and complete each account’s sign-in.');
  });});
  $('test-voice').addEventListener('click',e=>busy(e.currentTarget,async()=>{
    const blob=await request('/api/speak',{text:'Hello Alex. Celine is ready in Apex.',engine:'voicebox',profile:voiceProfile},true);
    if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(blob);$('voice-audio').src=audioUrl;$('voice-audio').hidden=false;
    try{await $('voice-audio').play();notice('Celine audio received. Confirm that you can hear it.');}catch(_){notice('Audio received. Press Play to allow browser playback.');}
  }));
  $('check-world-update').addEventListener('click',e=>busy(e.currentTarget,async()=>{
    $('world-update-status').textContent='Checking GitHub…';const d=await request('/api/setup/world-update',{});$('world-update-status').textContent=d.detail;
  }));
  $('refresh').addEventListener('click',load);
  window.addEventListener('pagehide',()=>{$('voice-audio').pause();if(audioUrl)URL.revokeObjectURL(audioUrl);});
  load();
})();
