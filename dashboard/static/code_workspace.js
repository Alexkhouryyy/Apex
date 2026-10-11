/* Everyday project work. All writes go through the owner-only Code API;
   local drafts stay separate from the version read from disk. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const button = (text, fn) => {const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=fn;return b;};
  const normal = text => text.replace(/\r\n/g,'\n');
  let api, put, post, say, context, refresh, confirm, runCommand, stopCommand;
  let editor=null, editorFile=null, active=null, activeContext=null, loadId=0, action='create', view='agent';
  const workspaces=new Map();
  let native=null, nativeId=null, shellOffset=0, shellTimer=null, shellGeneration=0;
  const state=()=>{const c=context();if(!workspaces.has(c.id))workspaces.set(c.id,{files:new Map(),active:null});return workspaces.get(c.id);};
  const editable=()=>{const c=context();return c.id&&c.detail?.status==='ready'&&!c.busy;};
  const dirty=f=>normal(f.text)!==normal(f.base);
  const currentFile=()=>active&&workspaces.get(activeContext)?.files.get(active);
  function status(text,error=false){$('editor-status').textContent=text;$('editor-status').classList.toggle('error',error);}
  function chooseView(next){
    view=next;
    $('editor-workbench').hidden=next!=='editor';$('workspace-preview').hidden=next!=='preview';
    document.getElementById('workspace-ide').hidden=next!=='ide';
    $('feed').hidden=next!=='agent';$('session-slot').hidden=next!=='agent';
    $('code-path').classList.toggle('workspace-path-hidden',next!=='agent');
    document.body.classList.toggle('workspace-editing',next!=='agent');
    if(next!=='ide'){document.body.classList.remove('ide-focus');document.getElementById('ide-focus').textContent='Focus IDE';}
    for(const b of document.querySelectorAll('[data-workspace-view]'))b.setAttribute('aria-pressed',String(b.dataset.workspaceView===next));
    if(next==='editor')editor?.focus();
    if(next==='ide')window.ApexCodeIDE?.open();
  }
  function tabs(){
    const c=context(),s=state();$('editor-tabs').replaceChildren();
    for(const [path,f] of s.files){
      const item=document.createElement('div');item.className='editor-tab';
      const b=button(path.split('/').pop()+(dirty(f)?' ●':''),()=>activate(path));b.title=path;b.setAttribute('role','tab');b.setAttribute('aria-selected',String(path===active));
      const close=button('×',()=>closeFile(path));close.setAttribute('aria-label',`Close ${path}`);item.append(b,close);$('editor-tabs').append(item);
    }
    const f=currentFile(),allowed=editable();
    $('editor-save').disabled=!f||!dirty(f)||!allowed||f.saving||f.readonly;
    $('editor-save-all').disabled=!allowed||![...s.files.values()].some(f=>dirty(f)&&!f.saving&&!f.readonly);
    $('editor-reload').disabled=!f||f.saving;
    $('editor-rename').disabled=!f||!allowed||f.saving||f.readonly;
    $('editor-delete').disabled=$('editor-rename').disabled;
    $('editor-new').disabled=!allowed;
    $('files-new').disabled=!allowed;
    $('workspace-command').disabled=!c.id||c.detail?.status!=='ready'||c.busy;
    $('workspace-terminal-stop').hidden=!c.detail?.terminal;
    $('workspace-terminal-where').textContent=c.detail?.worktree?`Working copy: ${c.detail.worktree}`:'';
  }
  function dispose(){if(editor){if(editorFile&&editor.snapshot)editorFile.editorState=editor.snapshot();editor.destroy();editor=null;editorFile=null;}}
  function activate(path,line){
    dispose();active=path;activeContext=context().id;const s=state();s.active=path;const f=s.files.get(path);
    $('editor-surface').replaceChildren();
    if(!f){$('editor-surface').textContent='Choose a file from Files, or press Ctrl+P.';tabs();return;}
    if(f.image){const img=document.createElement('img');img.alt=path;img.src=`data:${f.image.mime};base64,${f.image.base64}`;$('editor-surface').append(img);}
    else if(f.binary||f.too_big){$('editor-surface').textContent=f.binary?'Binary file. Open it with its own app.':'This file is too large to edit here (limit 512 KB).';}
    else if(window.ApexEditor){
      editorFile=f;
      editor=window.ApexEditor.create($('editor-surface'),{...f,readonly:f.readonly,state:f.editorState},text=>{f.text=text;tabs();status(`${path}${dirty(f)?' · Unsaved changes':' · Saved'}`);},()=>saveFile(path));
      if(line)editor.selectLine(line);
    }else{
      const text=document.createElement('textarea');text.className='editor-fallback';text.value=f.text;text.spellcheck=false;text.readOnly=!!f.readonly;text.setAttribute('aria-label',`Edit ${path}`);
      text.oninput=()=>{f.text=text.value;tabs();};$('editor-surface').append(text);
      editor={destroy(){},focus:()=>text.focus(),value:()=>text.value};
    }
    status(`${path}${f.readonly?' · Read only':dirty(f)?' · Unsaved changes':' · Saved'}`);tabs();chooseView('editor');
  }
  async function openFile(path,line){
    if(view==='ide'&&window.ApexCodeIDE?.openFile(path))return true;
    const c=context(),id=++loadId;if(!c.id)return false;
    const existing=state().files.get(path);
    if(existing&&(dirty(existing)||existing.saving)){activate(path,line);return true;}
    try{
      const f=await api(`/api/code/sessions/${c.id}/file?path=${encodeURIComponent(path)}`);
      if(id!==loadId||context().id!==c.id)return false;
      state().files.set(path,{...f,base:f.text,text:normal(f.text),readonly:f.editable===false||f.binary||f.too_big||c.detail?.status!=='ready'});
      activate(path,line);return true;
    }catch(e){say(e.message,'error');return false;}
  }
  async function saveFile(path=active){
    const c=context(),s=state(),f=s.files.get(path);if(!f||!dirty(f))return true;
    if(!editable()||f.readonly||f.saving){status('Wait for the current operation before saving. Your draft is kept.',true);return false;}
    const text=f.text;f.saving=true;tabs();status(`Saving ${path}…`);
    try{
      const saved=await put(`/api/code/sessions/${c.id}/file`,{path,text,revision:f.revision});
      f.base=normal(saved.text);f.revision=saved.revision;f.saving=false;
      if(context().id===c.id){tabs();status(saved.warning||`${path} saved${dirty(f)?' · Newer edits are still unsaved':''}`,!!saved.warning);if(saved.warning)say(saved.warning,'error');await refresh();}
      return true;
    }catch(e){f.saving=false;if(context().id===c.id){tabs();status(e.message,true);say(e.message,'error');}return false;}
  }
  async function saveAll(){const sid=context().id;for(const [path,f] of state().files){if(context().id!==sid)return false;if(dirty(f)&&!await saveFile(path))return false;}return context().id===sid;}
  async function closeFile(path){
    const sid=context().id;
    const f=state().files.get(path);if(f?.saving)return;
    if(f&&dirty(f)&&!await confirm('Close unsaved file?',`${path} has unsaved edits. Close it and discard this draft?`,'Discard draft'))return;
    if(context().id!==sid)return;
    const wasActive=path===active;if(wasActive)dispose();state().files.delete(path);
    if(wasActive)activate(state().files.keys().next().value||null);else tabs();
  }
  async function reloadFile(){
    const sid=context().id;
    const path=active,f=currentFile();if(!f)return;
    if(dirty(f)&&!await confirm('Reload from disk?',`Your unsaved draft of ${path} will be discarded. The disk version will be loaded.`,'Reload file'))return;
    if(context().id!==sid||active!==path)return;
    dispose();state().files.delete(path);await openFile(path);
  }
  function fileDialog(kind){
    if(!editable())return say('Open an idle workspace first.','error');
    action=kind;$('workspace-file-title').textContent=kind==='create'?'New file':'Rename file';
    $('workspace-file-submit').textContent=kind==='create'?'Create file':'Rename';
    $('workspace-file-path').value=kind==='rename'?active||'':'';$('workspace-file-error').hidden=true;
    $('workspace-file-dialog').showModal();$('workspace-file-path').focus();
  }
  async function changeFile(event){
    event.preventDefault();const c=context(),path=$('workspace-file-path').value.trim(),submit=$('workspace-file-submit');submit.disabled=true;
    try{
      if(!editable())throw new Error('Wait for the current operation.');
      let f;
      if(action==='rename'){
        const old=currentFile();if(!old)throw new Error('Open a file first.');if(dirty(old)&&!await saveFile(active))return;
        if(context().id!==c.id)return;
        f=await post(`/api/code/sessions/${c.id}/file-rename`,{path:active,destination:path,revision:old.revision});
        if(context().id!==c.id)return;
        dispose();state().files.delete(active);active=null;
      }else f=await put(`/api/code/sessions/${c.id}/file`,{path,text:'',create:true});
      $('workspace-file-dialog').close();
      if(context().id!==c.id)return;
      state().files.set(f.path,{...f,base:f.text,text:normal(f.text),readonly:false});activate(f.path);if(f.warning)say(f.warning,'error');await refresh();
    }catch(e){$('workspace-file-error').textContent=e.message;$('workspace-file-error').hidden=false;}
    finally{submit.disabled=false;}
  }
  async function removeFile(){
    const c=context(),f=currentFile(),path=active;if(!f||!editable())return;
    if(!await confirm('Delete this file?',`Remove ${path} from this workspace? Undo last step can restore it. Your original project is unchanged until Keep.`,'Delete file'))return;
    if(context().id!==c.id)return;
    try{const result=await post(`/api/code/sessions/${c.id}/file-delete`,{path,revision:f.revision});if(context().id!==c.id)return;dispose();state().files.delete(path);active=null;activate(state().files.keys().next().value||null);if(result.warning)say(result.warning,'error');await refresh();}
    catch(e){say(e.message,'error');}
  }
  function terminal(show=true){$('workspace-terminal').hidden=!show;if(show){if(window.ApexTerminal)startShell();else $('workspace-command').focus();}}
  function detachShell(){shellGeneration++;clearTimeout(shellTimer);native?.destroy();native=null;nativeId=null;shellOffset=0;$('workspace-pty').replaceChildren();$('workspace-pty').hidden=true;$('workspace-shell-close').hidden=true;}
  async function startShell(){
    const c=context();if(!c.id||c.detail?.status!=='ready')return;
    if(native&&nativeId===c.id){native.focus();return;}
    detachShell();const generation=shellGeneration;
    $('workspace-pty').hidden=false;$('workspace-shell-start').disabled=true;nativeId=c.id;
    let pending='',sending=false,ready=false;
    async function sendInput(){
      if(sending||!ready)return;
      sending=true;
      try{while(pending){let text=pending.slice(0,16000);if(text.length<pending.length&&/[\uD800-\uDBFF]$/.test(text))text=text.slice(0,-1);pending=pending.slice(text.length);await post(`/api/code/sessions/${c.id}/shell-input`,{text});}}
      catch(e){pending='';say(`Terminal input failed: ${e.message}. Check the terminal before typing again.`,'error');}
      finally{sending=false;}
    }
    native=window.ApexTerminal.create($('workspace-pty'),text=>{
      pending+=text;setTimeout(sendInput,10);
    },(rows,cols)=>{if(ready&&generation===shellGeneration)post(`/api/code/sessions/${c.id}/shell-resize`,{rows,cols}).catch(()=>{});});
    try{
      const s=await post(`/api/code/sessions/${c.id}/shell`,native.size());
      if(generation!==shellGeneration||context().id!==c.id)return;
      ready=true;sendInput();native.write(s.data);shellOffset=s.offset;$('workspace-shell-close').hidden=false;native.focus();pollShell(c.id,generation);
    }catch(e){if(generation===shellGeneration){detachShell();say(e.message,'error');}}
    finally{$('workspace-shell-start').disabled=false;}
  }
  async function pollShell(sid,generation){
    if(generation!==shellGeneration)return;
    try{const s=await api(`/api/code/sessions/${sid}/shell?after=${shellOffset}`);if(generation!==shellGeneration)return;
      if(s.reset)native.reset();if(s.data)native.write(s.data);shellOffset=s.offset;
      if(!s.running){native.write('\r\n[Terminal ended. Use Start shell to reconnect.]\r\n');nativeId=null;return;}
    }catch(e){if(generation!==shellGeneration)return;say(e.message,'error');}
    shellTimer=setTimeout(()=>pollShell(sid,generation),$('workspace-terminal').hidden?2000:200);
  }
  async function closeShell(){const sid=nativeId||context().id;if(!sid)return;
    try{await api(`/api/code/sessions/${sid}/shell`,{method:'DELETE'});detachShell();}catch(e){say(e.message,'error');}}
  async function search(event){
    event.preventDefault();const c=context(),q=$('workspace-search-query').value;$('workspace-search-results').textContent='Searching…';
    try{const found=await api(`/api/code/sessions/${c.id}/search?q=${encodeURIComponent(q)}`);if(context().id!==c.id)return;
      const root=$('workspace-search-results');root.replaceChildren();const meta=document.createElement('p');meta.textContent=`${found.matches.length} matches · ${found.scanned} files searched${found.cut?' · Search limit reached':''}`;root.append(meta);
      for(const hit of found.matches){const b=button(`${hit.path}:${hit.line} — ${hit.text}`,()=>{$('workspace-search-dialog').close();openFile(hit.path,hit.line);});root.append(b);}
    }catch(e){$('workspace-search-results').textContent=e.message;}
  }
  function searchDialog(){if(!context().id)return; $('workspace-search-dialog').showModal();$('workspace-search-query').focus();}
  function preview(){
    try{const url=new URL($('preview-url').value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.origin===location.origin)throw new Error('Enter your running app’s HTTP URL on its own port.');
      $('preview-frame').src=url.href;$('preview-frame').hidden=false;$('preview-external').href=url.href;$('preview-external').hidden=false;
    }catch(e){say(e.message||'Enter a complete app URL.','error');}
  }
  window.ApexWorkspace={
    init(options){({api,put,post,say,context,refresh,confirm,runCommand,stopCommand}=options);
      for(const b of document.querySelectorAll('[data-workspace-view]'))b.onclick=()=>chooseView(b.dataset.workspaceView);
      $('editor-save').onclick=()=>saveFile();$('editor-save-all').onclick=saveAll;$('editor-reload').onclick=reloadFile;
      $('editor-new').onclick=$('files-new').onclick=()=>fileDialog('create');$('editor-rename').onclick=()=>fileDialog('rename');$('editor-delete').onclick=removeFile;
      $('workspace-file-form').onsubmit=changeFile;$('workspace-file-cancel').onclick=()=>$('workspace-file-dialog').close();
      $('files-refresh').onclick=refresh;$('files-search').onclick=searchDialog;
      $('workspace-search-form').onsubmit=search;$('workspace-search-close').onclick=()=>$('workspace-search-dialog').close();
      $('preview-open').onclick=preview;
      $('workspace-terminal-toggle').onclick=()=>terminal();$('workspace-shell-start').onclick=startShell;$('workspace-shell-close').onclick=closeShell;
      $('workspace-terminal-hide').onclick=()=>terminal(false);$('workspace-terminal-stop').onclick=stopCommand;
      $('workspace-terminal-form').onsubmit=async e=>{e.preventDefault();const text=$('workspace-command').value;if(await runCommand(text))$('workspace-command').value='';};
      addEventListener('beforeunload',e=>{if([...workspaces.values()].some(s=>[...s.files.values()].some(dirty))){e.preventDefault();e.returnValue='';}});
      document.addEventListener('keydown',e=>{if(!context().id)return;
        if(e.defaultPrevented)return;
        if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();e.shiftKey?saveAll():saveFile();}
        if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='f'){e.preventDefault();searchDialog();}
        if(e.ctrlKey&&e.key==='`'){e.preventDefault();terminal();}
      });
    },
    openFile,chooseView,terminal,searchDialog,saveAll,
    update(){window.ApexCodeIDE?.update();if(context().id){tabs();editor?.readonly?.(!!currentFile()?.readonly||context().detail?.status!=='ready');}},
    switchContext(){loadId++;dispose();detachShell();active=null;if(context().id){const s=state();activate(s.active||null);}chooseView('agent');},
    termOutput(text){$('workspace-terminal-output').textContent=text;},
    async beforeOperation(){const sid=context().id;if(![...state().files.values()].some(dirty))return window.ApexCodeIDE?.save()??true;
      if(!await confirm('Save your edits first?','Save your open files before asking the agent, running checks or changing this workspace.','Save all'))return false;
      if(context().id!==sid)return false;
      return await saveAll() && (await window.ApexCodeIDE?.save()??true);},
  };
})();
