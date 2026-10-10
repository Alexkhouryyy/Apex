const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),vm=require('node:vm');
const dir=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(dir,'setup.html'),'utf8'),{url:'https://apex.test/setup',runScripts:'outside-only'});
const w=dom.window,$=id=>w.document.getElementById(id),tick=()=>new Promise(r=>setTimeout(r,10));
const secret='test-key-never-echoed';let saved,connected,checks=0;
async function accessRecovery(){
  const d=new JSDOM(fs.readFileSync(path.join(dir,'setup.html'),'utf8'),{url:'https://apex.test/setup',runScripts:'outside-only'});
  const win=d.window,get=id=>win.document.getElementById(id);
  let configured=false,status=401,diagnostics=0;
  win.fetch=async url=>{
    if(url==='/api/setup/access')return {ok:true,json:async()=>({token_configured:configured})};
    diagnostics++;return {ok:status===200,status,json:async()=>status===200?{engine:{installed:false},voice:{state:'unavailable'},model:{name:'local',configured:false},apps:{configured:false},updates:{}}:{detail:'denied'}};
  };
  win.eval(fs.readFileSync(path.join(dir,'setup.js'),'utf8'));
  try{
    await tick();assert.equal(diagnostics,0);assert.equal(get('owner-token').disabled,true);
    assert.equal(get('setup-content').hidden,true);assert.match(get('access-help').textContent,/DASHBOARD_TOKEN/);
    assert.equal(win.document.querySelector('.owner-guide').open,true);
    configured=true;get('refresh').click();await tick();
    assert.equal(get('owner-token').disabled,false);assert.match(get('notice').textContent,/not accepted/);
    status=403;get('refresh').click();await tick();assert.match(get('notice').textContent,/paired-device token/);
    status=200;get('owner-token').value='test-master';get('login-form').dispatchEvent(new win.Event('submit',{bubbles:true,cancelable:true}));await tick();
    assert.equal(get('owner-token').value,'');assert.equal(get('login').hidden,true);assert.equal(get('setup-content').hidden,false);
    status=401;get('refresh').click();await tick();assert.equal(get('setup-content').hidden,true,'Expired access hides owner controls');
  }finally{win.close();}
}
const row={id:'test-map',title:'Test map',unlocks:'3D map',getUrl:'https://provider.test',envVars:['GOOGLE_MAPS_API_KEY'],set:false,clientExposed:true};
w.fetch=async(url,options)=>{
  const body=options.body&&JSON.parse(options.body);let data={};
  if(url==='/api/setup/access')data={token_configured:true};
  else if(url==='/api/setup/status')data={engine:{installed:true,revision:'a'.repeat(40)},voice:{state:'unavailable',detail:'Start Celine.'},model:{name:'test-model',configured:false},apps:{configured:false},updates:{}};
  else if(url==='/world/engine/api/setup/status')data={keys:[row],setCount:0,total:1};
  else if(url==='/world/engine/api/setup/keys'){saved=body;data={status:{keys:[{...row,set:true}],setCount:1,total:1}};}
  else if(url==='/api/apps/settings'){connected=body;data={};}
  else if(url==='/api/setup/world-update'){checks++;data={state:'update_available',detail:'A newer reviewed integration is needed.'};}
  else assert.equal(url,'/api/world/engine/session');
  return {ok:true,json:async()=>data};
};
w.eval(fs.readFileSync(path.join(dir,'setup.js'),'utf8'));
(async()=>{
  await tick();assert.equal($('setup-content').hidden,false);assert.equal($('test-voice').disabled,true);
  assert.equal(checks,0,'No upstream check or code installation on page load');
  const input=w.document.querySelector('[name="GOOGLE_MAPS_API_KEY"]');input.value=secret;
  input.closest('form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
  assert.deepEqual(saved,{GOOGLE_MAPS_API_KEY:secret},'Original provider endpoint receives its native payload');
  assert.equal(w.document.querySelector('[name="GOOGLE_MAPS_API_KEY"]').value,'');
  assert.equal(w.document.body.textContent.includes(secret),false);
  assert.match($('provider-status').textContent,/1 of 1/);
  assert.match($('notice').textContent,/may take a moment/);
  $('composio-key').value=secret;
  $('composio-form').dispatchEvent(new w.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:$('composio-form').querySelector('button')}));await tick();
  assert.deepEqual(connected,{key:secret});assert.equal($('composio-key').value,'');
  $('check-world-update').click();await tick();assert.equal(checks,1);assert.match($('world-update-status').textContent,/newer/);
  // Private engine URLs must bypass the offline cache, including nested APIs.
  let fetchHandler;
  vm.runInNewContext(fs.readFileSync(path.join(dir,'sw.js'),'utf8'),{self:{location:{origin:'https://apex.test'},addEventListener:(name,fn)=>{if(name==='fetch')fetchHandler=fn;}},URL});
  for(const suffix of ['', 'api/setup/status', 'apex/assistant', 'assets/map.js']){
    fetchHandler({request:{method:'GET',url:'https://apex.test/world/engine/'+suffix,mode:'navigate'},respondWith:()=>assert.fail('Private engine response was cached')});
  }
  await accessRecovery();
  dom.window.close();console.log('Setup: provider saves, secret clearing, Composio setup, explicit update checks, unavailable voice, cache bypass and tokenless/wrong-token/device-token recovery pass.');
})().catch(error=>{dom.window.close();console.error(error);process.exitCode=1;});
