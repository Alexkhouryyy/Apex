/* Draft, conflict, response-order and slow-terminal regressions. Real editor
   interaction and subprocess execution are also exercised in browser acceptance. */
const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dir=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(dir,'code.html'),'utf8'),{url:'https://apex.test/code',runScripts:'outside-only'});
const w=dom.window,$=id=>w.document.getElementById(id),tick=()=>new Promise(r=>setTimeout(r,20));
for(const d of w.document.querySelectorAll('dialog')){d.showModal=()=>{d.open=true;};d.close=()=>{d.open=false;};}
let ctx={id:1,detail:{status:'ready'},busy:false},approve=true,saveGate=null,shellGate=null,inputGate=null;
let edit,input;const sent=[],messages=[];
const files=new Map([['a.py',{text:'a = 1\n',revision:'v1'}],['b.py',{text:'b = 2\n',revision:'v1'}]]);
const disk=key=>({path:key,lang:'python',editable:true,...files.get(key)});
const api=async(url,options)=>{
  if(url.endsWith('/shell'))return {data:'',offset:0,running:true};
  return disk(new URL(url,'https://apex.test').searchParams.get('path'));
};
const put=async(url,body)=>{
  if(saveGate)await saveGate.promise;
  const f=files.get(body.path);if(f.revision!==body.revision)throw new Error('This file changed on disk. Your draft is kept.');
  files.set(body.path,{text:body.text,revision:f.revision+'n'});return disk(body.path);
};
const post=async(url,body)=>{
  if(url.endsWith('/shell')){if(shellGate)await shellGate.promise;return {data:'',offset:0,running:true};}
  if(url.endsWith('/shell-input')){sent.push(body.text);if(inputGate)await inputGate.promise;return {sent:true};}
  return {};
};
// Preserve saved editor state exactly as CodeMirror does, including its text.
w.ApexEditor={create(parent,file,onChange){
  const text=w.document.createElement('textarea');text.value=file.state?.text??file.text;parent.append(text);
  edit=value=>{text.value=value;onChange(value);};
  return {snapshot:()=>({text:text.value}),destroy:()=>text.remove(),focus:()=>text.focus(),readonly(){}};
}};
w.ApexTerminal={create(parent,onInput){input=onInput;return {size:()=>({rows:24,cols:80}),write(){},reset(){},destroy(){},focus(){}};}};
const gate=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
w.eval(fs.readFileSync(path.join(dir,'code_workspace.js'),'utf8'));
w.ApexWorkspace.init({api,put,post,say:m=>messages.push(m),context:()=>ctx,refresh:async()=>{},confirm:async()=>approve,runCommand:async()=>true,stopCommand(){}});
(async()=>{
  await w.ApexWorkspace.openFile('a.py');edit('a = 3\n');
  await w.ApexWorkspace.openFile('b.py');await w.ApexWorkspace.openFile('a.py');
  assert.equal($('editor-surface').querySelector('textarea').value,'a = 3\n','Draft survives tab switching');
  await w.ApexWorkspace.saveAll();assert.equal(files.get('a.py').text,'a = 3\n');
  files.set('a.py',{text:'external = 4\n',revision:'external'});
  await w.ApexWorkspace.openFile('a.py');
  assert.equal($('editor-surface').querySelector('textarea').value,'external = 4\n','Fresh disk content cannot be replaced by disposed editor state');
  edit('draft = 5\n');files.set('a.py',{text:'external = 6\n',revision:'external2'});
  assert.equal(await w.ApexWorkspace.saveAll(),false);
  assert.equal(files.get('a.py').text,'external = 6\n','Conflict preserves the disk edit');
  assert.equal($('editor-surface').querySelector('textarea').value,'draft = 5\n','Conflict preserves the owner draft');
  approve=false;assert.equal(await w.ApexWorkspace.beforeOperation(),false);
  $('editor-reload').click();await tick();assert.equal($('editor-surface').querySelector('textarea').value,'draft = 5\n');
  approve=true;$('editor-reload').click();await tick();assert.equal($('editor-surface').querySelector('textarea').value,'external = 6\n');
  edit('first save\n');saveGate=gate();const saving=w.ApexWorkspace.saveAll();await tick();edit('newer draft\n');saveGate.resolve();await saving;saveGate=null;
  assert.equal(files.get('a.py').text,'first save\n');assert.equal($('editor-save').disabled,false,'Edits made during a save stay dirty');
  await w.ApexWorkspace.saveAll();assert.equal(files.get('a.py').text,'newer draft\n');
  await w.ApexWorkspace.openFile('b.py');
  $('editor-tabs').querySelector('[aria-label="Close a.py"]').click();await tick();
  assert.equal($('editor-surface').querySelector('textarea').value,'b = 2\n','Closing an inactive tab leaves the active file alone');
  shellGate=gate();w.ApexWorkspace.terminal();input('echo ');input('one\r');await tick();assert.equal(sent.length,0,'Input waits for shell startup');
  inputGate=gate();shellGate.resolve();await tick();assert.equal(sent[0],'echo one\r','Typing is batched, in order');
  input('echo ');input('two\r');await tick();assert.equal(sent.length,1,'Slow requests cannot be overtaken');
  inputGate.resolve();await tick();assert.equal(sent.join(''),'echo one\recho two\r');
  ctx={id:2,detail:{status:'ready'},busy:false};w.ApexWorkspace.switchContext();await tick();
  assert.equal($('editor-surface').textContent.includes('newer draft'),false,'Workspace drafts remain separate');
  assert(messages.some(m=>m.includes('changed on disk')));
  console.log('Code workspace: drafts, fresh reloads, conflicts, save ordering, tab closing, startup input gating and slow terminal batching pass.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>dom.window.close());
