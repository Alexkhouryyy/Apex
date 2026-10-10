/* Real Theia APIs and UI in a disposable project. No mocks of language/debug adapters. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const base=process.env.APEX_IDE_TEST_URL, sid=process.env.APEX_IDE_TEST_SESSION;
const output=process.env.APEX_IDE_TEST_OUTPUT;
async function poll(check,description,timeout=45000){
 const deadline=Date.now()+timeout;
 while(Date.now()<deadline){if(await check())return;await new Promise(r=>setTimeout(r,200));}
 throw new Error('Timed out: '+description);
}
(async()=>{
 const options={headless:true};if(process.env.APEX_CHROMIUM)options.executablePath=process.env.APEX_CHROMIUM;
 if(process.platform==='linux')options.args=['--no-sandbox'];
 const browser=await chromium.launch(options);
 let page;
 try{
  page=await browser.newPage({viewport:{width:1800,height:1100}});
  await page.addInitScript(({token,origin})=>{try{if(location.origin===origin)localStorage.setItem('apex_token',token)}catch{}},
   {token:process.env.APEX_IDE_TEST_TOKEN,origin:new URL(base).origin});
  page.on('pageerror',e=>console.error('Browser error:',e.message));
  page.on('console',m=>{if(m.type()==='error')console.error('Console error:',m.text().slice(0,1500));});
  page.on('requestfailed',r=>console.error('Request failed:',r.url(),r.failure()?.errorText));
  await page.goto(base+'/code#s='+sid);
  await page.waitForFunction(()=>document.body.classList.contains('view-session')&&!document.getElementById('workspace-command').disabled);
  await page.locator('[data-workspace-view="ide"]').click();
  const frame=page.frameLocator('#ide-frames iframe'),body=frame.locator('body');
  await frame.locator('#theia-app-shell').waitFor({timeout:120000});
  await poll(()=>body.evaluate(()=>document.body.innerText.includes('Yes, I trust the authors')),'workspace trust');
  await frame.getByRole('button',{name:'Yes, I trust the authors'}).click();
  await body.evaluate(()=>{
   const c=theia.container, keys=[...c._bindingDictionary._map.keys()];
   const get=method=>c.get(keys.find(k=>typeof k==='function'&&typeof k.prototype?.[method]==='function'));
   window.acceptance={manager:get('registerSelectionResolver'),registry:get('registerCommand'),
    debug:get('startConfiguration'),configs:get('findConfiguration'),problems:get('getProblemStat'),
    breakpoints:get('setBreakpoints'),scm:get('registerScmProvider')};
  });
  const shot=async name=>{fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,name+'.png')});};
  async function open(name){
   await poll(()=>page.locator('#ide-status').textContent().then(s=>/^(Ready|Unsaved edits) ·/.test(s)),'IDE ready');
   assert.equal(await page.evaluate(name=>ApexCodeIDE.openFile(name),name),true);
   await frame.locator('.monaco-editor:visible').first().click();
   await poll(()=>body.evaluate((_,name)=>acceptance.manager.currentEditor?.editor.uri.path.toString().endsWith('/'+name),name),'open '+name);
   await body.evaluate(()=>{acceptance.editor=acceptance.manager.currentEditor.editor.getControl();acceptance.editor.focus();});
  }
  await open('language.py');
  await body.evaluate(()=>{const c=acceptance.editor,m=c.getModel();acceptance.python=m.getValue();m.setValue(m.getValue()+'\nanswer.');c.setPosition({lineNumber:m.getLineCount(),column:8});c.trigger('acceptance','editor.action.triggerSuggest',{});});
  await frame.locator('.suggest-widget.visible').waitFor({timeout:45000});
  await poll(async()=>/bit_length/.test(await frame.locator('.suggest-widget.visible').innerText()),'Python int completion');
  await shot('python-completion');await page.keyboard.press('Escape');
  await body.evaluate(async()=>{acceptance.editor.getModel().setValue(acceptance.python);acceptance.editor.setPosition({lineNumber:4,column:10});await acceptance.registry.executeCommand('editor.action.revealDefinition');});
  await poll(()=>body.evaluate(()=>acceptance.editor.getPosition().lineNumber===1),'Python definition');
  console.log('PASS Python semantic completion and Go to Definition');
  async function debug(name,line){
   await body.evaluate((_,line)=>{const e=acceptance.manager.currentEditor.editor;acceptance.breakpoints.setBreakpoints(e.uri,[{id:'acceptance-'+line,uri:e.uri.toString(),enabled:true,raw:{line}}]);},line);
   assert(await body.evaluate(async (_,name)=>!!await acceptance.debug.start([...acceptance.configs.all].find(c=>c.name===name)),name),'debug adapter started');
   await poll(()=>body.evaluate((_,line)=>acceptance.debug.currentSession?.state===3&&acceptance.debug.currentSession.currentFrame?.raw.line===line,line),'breakpoint stack frame');
   assert.equal((await body.evaluate(()=>acceptance.debug.currentSession.evaluate('answer','watch'))).result,'42');
   await body.evaluate(()=>acceptance.debug.currentSession.currentThread.stepOver());
   await poll(()=>body.evaluate((_,line)=>acceptance.debug.currentSession?.state===3&&acceptance.debug.currentSession.currentFrame?.raw.line===line+1,line),'step to next line');
   assert.equal((await body.evaluate(()=>acceptance.debug.currentSession.evaluate('final','watch'))).result,'43');
   await shot(name.startsWith('Python')?'python-debug':'node-debug');
   await body.evaluate(async()=>{for(const s of acceptance.debug.sessions)await acceptance.debug.terminateSession(s);});
   await poll(()=>body.evaluate(()=>acceptance.debug.sessions.length===0),'debugger shutdown');
   console.log('PASS '+name+' breakpoint, Variables/Watch and stepping');
  }
  await debug('Python acceptance',5);
  await open('language.ts');
  await poll(()=>body.evaluate(()=>acceptance.problems.findMarkers().some(m=>String(m.data.code)==='2322')),'TypeScript type mismatch diagnostic');
  assert((await body.evaluate(()=>acceptance.problems.findMarkers().map(m=>m.data.message))).some(m=>m.includes('not assignable')));
  await body.evaluate(()=>{const c=acceptance.editor,m=c.getModel();acceptance.ts=m.getValue();m.setValue(m.getValue()+'\nmessage.');c.setPosition({lineNumber:m.getLineCount(),column:9});c.trigger('acceptance','editor.action.triggerSuggest',{});});
  await frame.locator('.suggest-widget.visible').waitFor({timeout:45000});
  await poll(async()=>/charAt/.test(await frame.locator('.suggest-widget.visible').innerText()),'TypeScript string completion');
  await shot('typescript-diagnostics');await page.keyboard.press('Escape');
  await body.evaluate(()=>acceptance.editor.getModel().setValue(acceptance.ts));
  await body.evaluate(()=>{acceptance.editor.setPosition({lineNumber:2,column:12});acceptance.editor.focus();acceptance.editor.trigger('acceptance','editor.action.rename',{});});
  const rename=frame.locator('input.rename-input:visible');await rename.fill('welcome');await rename.press('Enter');
  await poll(()=>body.evaluate(()=>/function welcome\(/.test(acceptance.editor.getModel().getValue())&&/message = welcome\(/.test(acceptance.editor.getModel().getValue())),'TypeScript symbol rename');
  await body.evaluate(()=>acceptance.editor.getModel().setValue(acceptance.ts));
  console.log('PASS TypeScript semantic diagnostics, string completion and symbol rename');
  await open('language.js');await debug('Node acceptance',3);
  const found=await body.evaluate(()=>{
   const c=theia.container,keys=[...c._bindingDictionary._map.keys()];
   const key=keys.find(k=>typeof k==='function'&&typeof k.prototype?.searchWithCallback==='function');
   return new Promise((resolve,reject)=>{const matches=[];c.get(key).search('answer',{
    onResult:(_,result)=>matches.push(result.fileUri),onDone:(_,error)=>error?reject(Error(error)):resolve(matches)
   },{maxResults:100}).catch(reject);});
  });
  assert(found.some(uri=>uri.endsWith('/language.py'))&&found.some(uri=>uri.endsWith('/language.js')));
  await body.evaluate(async()=>{
   const c=theia.container,keys=[...c._bindingDictionary._map.keys()];
   const service=c.get(keys.find(k=>String(k)==='Symbol(TerminalService)'));
   acceptance.terminal=await service.newTerminal({title:'Acceptance terminal',destroyTermOnClose:true});
   await service.open(acceptance.terminal);await acceptance.terminal.start();
   await acceptance.terminal.executeCommand({args:['node','terminal-input.js']});
  });
  const terminalText=()=>body.evaluate(()=>{const b=acceptance.terminal.buffer;return b.getLines(0,b.length,true).join('\n');});
  await poll(async()=>/Your name:/.test(await terminalText()),'interactive terminal prompt');
  await body.evaluate(()=>acceptance.terminal.sendText('Apex\r'));
  await poll(async()=>/hello Apex/.test(await terminalText()),'interactive terminal input');
  await body.evaluate(async()=>{
   const c=theia.container,keys=[...c._bindingDictionary._map.keys()];
   const key=keys.find(k=>typeof k==='function'&&typeof k.prototype?.startUserAction==='function');
   const tasks=c.get(key),token=tasks.startUserAction(),task=(await tasks.getTasks(token)).find(t=>t.label==='Acceptance task');
   if(!task)throw Error('Configured task is missing');
   await tasks.runConfiguredTask(token,task._scope,task.label);
  });
  await poll(async()=>{
   const response=await page.request.get(base+`/api/code/sessions/${sid}/file?path=task-result.txt`,{headers:{Authorization:'Bearer '+process.env.APEX_IDE_TEST_TOKEN}});
   return response.ok()&&(await response.json()).text==='task works';
  },'task output on disk');
  await shot('terminal-and-task');
  await poll(()=>body.evaluate(()=>acceptance.scm.repositories.some(r=>r.provider.id==='git')),'Git source control provider');
  await poll(()=>body.evaluate(()=>acceptance.scm.repositories.some(r=>r.provider.groups.some(g=>g.resources.some(f=>f.sourceUri.path.base==='task-result.txt')))),'Git detects task output');
  console.log('PASS project search, interactive terminal input, configured process task and Git working copy status');
  await body.evaluate(async (_,source)=>{const c=theia.container,k=[...c._bindingDictionary._map.keys()].find(k=>String(k)==='Symbol(PluginServer)');acceptance.plugins=c.get(k);await acceptance.plugins.install(source);},process.env.APEX_IDE_TEST_VSIX);
  await poll(()=>body.evaluate(()=>!!acceptance.registry.getCommand('apex.acceptance.echo')),'extension activation');
  assert.equal(await body.evaluate(()=>acceptance.registry.executeCommand('apex.acceptance.echo')),'extension host works');
  await body.evaluate(()=>acceptance.registry.executeCommand('apex.acceptance.webview'));
  await poll(async()=>page.frames().some(f=>f.url().includes('/webview/index.html')),'isolated webview');
  await poll(async()=>{for(const f of page.frames()){if(await f.getByRole('heading',{name:'Extension webview works'}).count())return true;}return false;},'extension webview content');
  const content=page.frames().find(f=>f.url().includes('vscode-webview'))||await (async()=>{for(const f of page.frames()){if(await f.getByRole('heading',{name:'Extension webview works'}).count())return f;}})();
  await content.getByRole('button',{name:'Run'}).click();assert.equal(await content.getByRole('button').textContent(),'42');
  assert.notEqual(new URL(content.url()).origin,new URL(base).origin);
  const isolation=await content.evaluate(()=>{try{return {token:localStorage.getItem('apex_token'),parentReadable:!!parent.parent.theia};}catch{return {parentReadable:false};}});
  assert.equal(isolation.parentReadable,false,'Webview cannot inspect IDE or Apex globals');
  assert.equal(isolation.token??null,null,'No Apex credential in the webview origin');
  await shot('extension-webview');
  await body.evaluate(()=>acceptance.plugins.uninstall('apex-acceptance.echo@1.0.0'));
  console.log('PASS VSIX installation, real extension command, isolated scripted webview and uninstall');
  await open('language.py');
  await body.evaluate(()=>{const m=acceptance.editor.getModel();m.setValue(m.getValue()+'\n# Saved through Apex Save All\n');});
  await poll(()=>page.locator('#ide-status').textContent().then(s=>s.includes('Unsaved')),'host dirty state');
  assert.equal(await page.evaluate(()=>ApexWorkspace.beforeOperation()),true);
  const read=await page.request.get(base+`/api/code/sessions/${sid}/file?path=language.py`,{headers:{Authorization:'Bearer '+process.env.APEX_IDE_TEST_TOKEN}});
  assert((await read.json()).text.includes('Saved through Apex Save All'));
  await page.locator('#ide-focus').click();assert(await page.locator('body').evaluate(b=>b.classList.contains('ide-focus')));
  assert.equal(await page.evaluate(()=>getComputedStyle(document.body,'::after').display),'none','Focus IDE removes the scanline overlay from the editor');
  await shot('full-ide-focus');
  await page.locator('#ide-close').click();await poll(()=>page.locator('#ide-frames iframe').count().then(n=>n===0),'save and close');
  console.log('PASS Apex Save All, disk checkpoint, focus layout and runtime close');
 }catch(e){
  if(page){
   fs.mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});
   for(const f of page.frames())console.error('Frame:',f.url(),(await f.locator('body').innerText().catch(()=>'' )).slice(0,1800));
   for(const f of page.frames())if(f.url().includes('/code/ide/'))console.error('Editor service:',await f.evaluate(()=>({current:acceptance?.manager.currentEditor?.editor.uri.toString(),keys:Object.keys(acceptance?.manager||{}),widgets:acceptance?.manager.all?.map(w=>w.editor.uri.toString())})).catch(()=>null));
  }
  throw e;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
