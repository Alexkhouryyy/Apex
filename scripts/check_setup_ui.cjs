const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),vm=require('node:vm');
const dir=path.join(__dirname,'../dashboard/static');
const dom=new JSDOM(fs.readFileSync(path.join(dir,'setup.html'),'utf8'),{url:'https://apex.test/setup',runScripts:'outside-only'});
const w=dom.window,$=id=>w.document.getElementById(id),tick=()=>new Promise(r=>setTimeout(r,10));
const secret='test-key-never-echoed';let saved,connected,checks=0;
const row={id:'test-map',title:'Test map',unlocks:'3D map',getUrl:'https://provider.test',envVars:['GOOGLE_MAPS_API_KEY'],set:false,clientExposed:true};
w.fetch=async(url,options)=>{
  const body=options.body&&JSON.parse(options.body);let data={};
  if(url==='/api/setup/status')data={engine:{installed:true,revision:'a'.repeat(40)},voice:{state:'unavailable',detail:'Start Celine.'},model:{name:'test-model',configured:false},apps:{configured:false},updates:{}};
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
  dom.window.close();console.log('Setup: native provider saves, secret clearing, Composio setup, explicit update checks, unavailable voice and private-cache bypass pass.');
})().catch(error=>{dom.window.close();console.error(error);process.exitCode=1;});
