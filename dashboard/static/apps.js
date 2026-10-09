/* Owner-only Apps experience. Provider text is rendered as text, never HTML. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const requestedView = new URLSearchParams(location.search).get('view');
  let state = {configured: false, connections: []}, view = ['connected', 'local'].includes(requestedView) ? requestedView : 'catalog';
  let cursor = '', generation = 0, poll = null, polling = false, localItems = [], toolItems = [], skipped = 0;
  const links = new Map();
  const labels = {not_connected:'Not connected',connected:'Connected',pending:'Awaiting sign-in',disabled:'Disabled',needs_reconnect:'Reconnect needed',disconnected:'Disconnected',disconnect_pending:'Disconnect pending'};
  function el(tag, text, cls) { const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(cls) node.className = cls; return node; }
  function notice(message, error=false) { $('notice').hidden = !message; $('notice').textContent = message; $('notice').className = error ? 'error' : ''; }
  async function api(path, body) {
    const response = await fetch('/api/apps' + path, {method:body === undefined?'GET':'POST',cache:'no-store',headers:{'Authorization':'Bearer '+(localStorage.getItem('apex_token')||''),'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const data = await response.json().catch(()=>({}));
    if(!response.ok) { if(response.status===401 || response.status===403) $('login').hidden=false; throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status}).`); }
    return data;
  }
  async function busy(button, fn) { const old = button.textContent; button.disabled=true; button.textContent='Working…'; notice(''); try { await fn(); } catch(error) { notice(error.message,true); } finally { button.disabled=false; button.textContent=old; } }
  function count() { $('connection-count').textContent=state.connections.filter(c=>c.enabled && c.status==='connected').length; }
  function sync() { count(); $('settings').hidden=state.configured; $('workspace').hidden=false; $('login').hidden=true; clearInterval(poll); if(state.connections.some(c=>c.enabled && c.status==='pending')) poll=setInterval(pollStatus,5000); }
  async function pollStatus() { if(polling || document.hidden) return; polling=true; try { const before=JSON.stringify(state.connections); state=await api('/refresh',{}); count(); if(JSON.stringify(state.connections.map(c=>[c.slug,c.status]))!==JSON.stringify(JSON.parse(before).map(c=>[c.slug,c.status]))) await render(); if(!state.connections.some(c=>c.enabled && c.status==='pending')) {clearInterval(poll);notice('Connection status updated.');} } catch(e) { clearInterval(poll);notice(e.message,true); } finally {polling=false;} }
  function empty(title, message) { const box=el('div',undefined,'empty');box.append(el('h3',title),el('p',message));$('results').append(box); }
  function action(container, title, fn, quiet=false) { const b=el('button',title,quiet?'quiet':'');b.type='button';b.addEventListener('click',()=>busy(b,()=>fn(b)));container.append(b);return b; }
  function card(item, local=false) {
    const row=state.connections.find(c=>c.slug===item.slug); if(row) item={...item,...row};
    const box=el('article',undefined,'card'),head=el('div',undefined,'card-head'),title=el('div');
    const localStatus = item.runtime?.state === 'connected' ? `Connected · ${item.runtime.tools} actions` : item.runtime?.state === 'failed' ? 'Connection failed' : item.installed ? 'Configured · not loaded' : 'Setup required';
    title.append(el('h3',item.name),el('span',local?localStatus:(labels[item.status]||'Not connected'),'badge '+(item.status||'')));
    head.append(el('div',item.name.slice(0,2).toUpperCase(),'monogram'),title);box.append(head,el('p',item.description||item.blurb||'Connect this app to make its tools available to Apex.','description'));
    if(local) { if(item.note)box.append(el('p',item.note,'small')); const fields={};for(const [key,label] of Object.entries(item.env||{})){const field=el('div'),input=el('input');input.type='password';input.autocomplete='off';input.id='credential-'+item.id+'-'+key;input.placeholder=item.missing.includes(key)?'Required':'Saved — leave blank to reuse';const l=el('label',label);l.htmlFor=input.id;field.append(l,input);box.append(field);fields[key]=input;}
      const actions=el('div',undefined,'actions');action(actions,item.installed?'Reconnect':'Connect',async()=>{const secrets={};for(const [k,i] of Object.entries(fields))if(i.value)secrets[k]=i.value;notice('Connecting… complete any sign-in opened on the Apex host. This can take up to three minutes.');const result=await api('/local/'+item.id+'/install',{secrets});Object.values(fields).forEach(i=>i.value='');await render();notice(result.note);});if(item.installed)action(actions,'Remove',async()=>{const r=await api('/local/'+item.id+'/remove',{});await render();notice(r.note);},true);if(item.docs){const a=el('a','Setup guide ↗');a.href=item.docs;a.target='_blank';a.rel='noopener noreferrer';actions.append(a);}box.append(actions);return box;
    }
    const info=(item.categories||[]).join(' · ')+(Number.isFinite(item.tool_count)?` · ${item.tool_count} actions`:'');if(info)box.append(el('p',info.replace(/^ · /,''),'small'));
    const actions=el('div',undefined,'actions'),slug=encodeURIComponent(item.slug);
    const connect=async()=>{const result=await api('/'+slug+'/connect',{});if(result.redirect_url){links.set(item.slug,result.redirect_url);window.open(result.redirect_url,'_blank','noopener,noreferrer');notice('Finish sign-in in the new tab. If it did not open, use Continue sign-in on the app card.');}state=await api('/status');sync();await render();};
    if(item.status==='connected'&&item.enabled){action(actions,'View actions',async()=>showTools(item),true);action(actions,'Disable',async()=>{state=await api('/'+slug+'/enabled',{enabled:false});sync();await render();},true);}
    else if(item.status==='disabled'){action(actions,'Enable',async()=>{state=await api('/'+slug+'/enabled',{enabled:true});sync();await render();});}
    else if(item.status==='pending'){if(links.has(item.slug)){const a=el('a','Continue sign-in ↗');a.href=links.get(item.slug);a.target='_blank';a.rel='noopener noreferrer';actions.append(a);}action(actions,'Resume sign-in',connect,true);}
    else if(item.status!=='disconnect_pending')action(actions,item.status==='needs_reconnect'?'Reconnect':'Connect',connect);
    if(item.status&& !['not_connected','disconnected'].includes(item.status))action(actions,item.status==='disconnect_pending'?'Retry disconnect':'Disconnect',async()=>{state=await api('/'+slug+'/disconnect',{});links.delete(item.slug);sync();await render();notice('Disconnected from Apex and Composio. You can also revoke access in the app’s own account settings.');},true);
    box.append(actions);return box;
  }
  async function render(append=false) {
    const gen=++generation; $('more').hidden=true;
    document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.view===view)));
    $('category').hidden=view!=='catalog';$('list-title').textContent={catalog:'Discover apps',connected:'Your connections',local:'Local MCP servers'}[view];
    $('view-help').textContent=view==='local'?'Direct connections run on your Apex host. Use Discover for managed app sign-in.':view==='connected'?'Disable pauses Apex access. Disconnect removes the managed connection.':'Choose only the apps you want Apex to use. Each account stays under your control.';
    if(!append) {cursor='';skipped=0;$('catalog-warning').hidden=true;$('catalog-warning').textContent='';$('results').replaceChildren();$('list-meta').textContent='';}
    if(view==='catalog' && !state.configured){empty('Your catalog starts here','Add a Composio project key above to browse the live catalog, or open Local MCP.');return;}
    const q=$('query').value.trim().toLowerCase();
    if(view==='connected'){const items=state.connections.filter(c=>c.status!=='disconnected'&&(!q||(c.name+' '+c.slug).toLowerCase().includes(q)));items.forEach(c=>$('results').append(card(c)));$('list-meta').textContent=`${items.length} apps`;if(!items.length)empty('No matching connections','Find an app in Discover and connect your account.');return;}
    $('list-meta').textContent='Loading…';
    try {
      if(view==='local'){const data=await api('/local');if(gen!==generation)return;localItems=data.items.map(i=>({...i,runtime:data.runtime.servers.find(s=>s.server===i.id)}));const items=localItems.filter(i=>!q||(i.name+' '+i.blurb).toLowerCase().includes(q));items.forEach(i=>$('results').append(card(i,true)));$('list-meta').textContent=`${items.length} servers · ${data.runtime.tool_count} loaded actions`;$('view-help').textContent+=' '+data.runtime.detail;if(!items.length)empty('No matching servers','Try another search.');return;}
      const data=await api('/catalog?'+new URLSearchParams({q,category:$('category').value,cursor:append?cursor:''}));if(gen!==generation)return;
      skipped+=Number.isInteger(data.skipped_items)&&data.skipped_items>0?data.skipped_items:0;
      $('catalog-warning').hidden=!skipped;$('catalog-warning').textContent=skipped?`${skipped} app listing${skipped===1?'':'s'} could not be displayed. Try Search or Load more for other apps.`:'';
      if(data.items.length)$('results').querySelectorAll('.empty').forEach(n=>n.remove());
      data.items.forEach(i=>$('results').append(card(i)));cursor=data.next_cursor||'';$('more').hidden=!cursor;$('list-meta').textContent=Number.isFinite(data.total_items)?`${data.total_items.toLocaleString()} matching apps`:`${$('results').querySelectorAll('.card').length} apps loaded`;if(!$('results').children.length)empty(skipped?'No apps available on this page':'No matching apps',cursor?'Use Load more, or try a different name or category.':'Try a different name or category.');
    } catch(e){if(gen===generation){$('list-meta').textContent='Could not load';notice(e.message,true);if(!$('results').children.length)empty('Catalog unavailable','Use Refresh status or Search to try again.');}}
  }
  async function showTools(item){$('details-title').textContent=item.name+' actions';$('tool-list').replaceChildren(el('p','Loading…'));$('tool-filter').value='';toolItems=[];$('details').showModal();try{const data=await api('/'+encodeURIComponent(item.slug)+'/tools');toolItems=data.items;renderTools();}catch(e){$('tool-list').replaceChildren(el('p',e.message));}}
  function renderTools(){const q=$('tool-filter').value.toLowerCase();$('tool-list').replaceChildren();for(const t of toolItems.filter(t=>(t.name+' '+t.description).toLowerCase().includes(q))){const d=el('details');d.append(el('summary',t.name),el('p',t.description,'small'),el('pre',JSON.stringify(t.input_schema,null,2)));$('tool-list').append(d);}if(!$('tool-list').children.length)$('tool-list').append(el('p','No actions available. Refresh connection status and try again.'));}
  async function start(){try {state=await api('/status');sync();if(state.configured){try{const categories=await api('/categories');$('category').replaceChildren(new Option('All categories',''));categories.items.forEach(c=>$('category').append(new Option(c.name,c.id)));}catch(e){notice(e.message,true);}}await render();}catch(e){notice(e.message,true);}}
  $('login-form').addEventListener('submit',e=>{e.preventDefault();busy(e.submitter,async()=>{localStorage.setItem('apex_token',$('owner-token').value);$('owner-token').value='';await start();});});
  $('settings-form').addEventListener('submit',e=>{e.preventDefault();busy(e.submitter,async()=>{state=await api('/settings',{key:$('provider-key').value});$('provider-key').value='';await start();notice('Catalog connected. Choose an app to authorize.');});});
  $('settings-toggle').addEventListener('click',()=>{$('settings').hidden=!$('settings').hidden;});
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.view;$('query').value='';history.replaceState(null,'','/apps'+(view==='catalog'?'':'?view='+view));render();}));
  $('search-form').addEventListener('submit',e=>{e.preventDefault();render();});$('category').addEventListener('change',()=>render());$('more').addEventListener('click',e=>busy(e.currentTarget,()=>render(true)));
  $('refresh').addEventListener('click',e=>busy(e.currentTarget,async()=>{if(view==='local'){const r=await api('/local/reload',{});notice(r.note);}else{state=await api('/refresh',{});sync();}await render();}));
  $('close-details').addEventListener('click',()=>$('details').close());$('tool-filter').addEventListener('input',renderTools);window.addEventListener('pagehide',()=>clearInterval(poll));
  start();
})();
