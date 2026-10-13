// Native bridge and providers are simulated; real Windows painting is checked by pytest.
const {JSDOM}=require('jsdom');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
async function scenario(native,noMicrophone=false){
 const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{
  url:'http://127.0.0.1:7860/companion'+(native?'?desktop=1':''),runScripts:'outside-only'});
 const w=dom.window,$=id=>w.document.getElementById(id);
 const style=w.document.createElement('style');style.textContent=fs.readFileSync(path.join(base,'companion.css'),'utf8');w.document.head.append(style);
 let ready=0,hidden=0,mics=0,stops=0,expanded=0,streams=[],requests=[];
 Object.defineProperty(w.navigator,'userActivation',{value:{hasBeenActive:false}});
 w.TextDecoder=TextDecoder;
 w.speechSynthesis={cancel(){},speak(){}};
 const nativeApi={connect:async()=>({desktop:true,token:'owner-token',hotkey:'<f8>'}),
                  choose_mode:async()=>true,sketch:async()=>({open:true}),
                  ready:async()=>{ready++;},hide:async()=>{hidden++;},expand:async()=>{expanded++;}};
 w.pywebview=native?{api:{}}:undefined;
 w.ApexHandsFree=class{
  constructor(opts){this.opts=opts;this.enabled=false;this.epoch=1;}
  async start(){mics++;if(noMicrophone)throw new Error('Microphone permission denied');this.enabled=true;this.opts.onState('Listening · pause to send');}
  pause(){}resume(){}stop(){stops++;this.enabled=false;this.epoch++;}
 };
 w.fetch=async(url,opts={})=>{
  requests.push({url,opts});
  if(url==='/api/status')return Response.json({agent_ready:true,voices:{}});
  if(url==='/api/voicebox/profiles')return Response.json({profiles:[{id:'celine',name:'Celine'}]});
  if(url==='/api/speak/stream')return Response.json({error:'not streamed'},{status:404});
  if(url.includes('/api/chat/threads'))return Response.json({messages:[]});
  if(url.includes('/api/companion/look'))return new Promise(()=>{});
  if(url.includes('/cancel/'))return Response.json({cancel_requested:true});
  if(url==='/api/companion/chat'){
   let controller;const body=new ReadableStream({start(c){controller=c;}});streams.push(controller);
   return new Response(body,{headers:{'Content-Type':'application/x-ndjson'}});
  }
  throw new Error(url);
 };
 w.eval(fs.readFileSync(path.join(base,'speech_queue.js'),'utf8'));
 w.eval(fs.readFileSync(path.join(base,'companion.js'),'utf8'));
 if(native){
  await tick();assert.equal(ready,0,'an empty injected API is not the completed bridge');
  w.pywebview.api=nativeApi;w.dispatchEvent(new w.Event('pywebviewready'));
 }
 await tick();await tick();
 assert.equal(mics,0,'starting the app must not begin microphone capture');
 if(native){
  assert.equal(ready,1);assert.equal(w.localStorage.getItem('apex_token'),'owner-token');
  assert.match(w.document.querySelector('.desktop-hotkey-hint').textContent,/F8/);
  assert($('desktop-task'));assert($('desktop-draw'));
  assert(!requests.some(r=>r.url.includes('/api/companion/look')),'a browser poll must not steal a native hotkey');
  w.ApexDesktop.activate({kind:'look',source:'hotkey',seq:4});
  await tick();await tick();
  assert.equal(mics,1,'native hotkey works without a previous browser click');
  assert.notEqual(w.getComputedStyle($('desktop-task').parentElement).display,'none','Draw and the action picker remain available during screen conversation');
  if(noMicrophone)assert.equal(expanded,1,'denied microphone must leave a usable full text window');
  const call=requests.find(r=>r.url==='/api/companion/chat');assert(call);
  const body=JSON.parse(call.opts.body);
  assert.equal(body.look_id,4);assert.equal(body.mode,'discuss','screen help preserves Discuss');
  assert.equal(call.opts.headers.Authorization,'Bearer owner-token');
  w.ApexDesktop.activate({kind:'stop',source:'hotkey'});
  await tick();assert(hidden>0);assert(stops>0);
  assert(requests.some(r=>r.url.includes('/cancel/')));
  assert.equal($('hands-free').checked,false);
  // A new hotkey waiting for the stopped reply must not restart the mic
  // later, after the user has already stopped that new invocation as well.
  w.ApexDesktop.activate({kind:'look',source:'hotkey',seq:5});await tick();
  w.ApexDesktop.activate({kind:'stop',source:'hotkey'});await tick();
  const event={type:'done',text:'Earlier stopped reply.',interrupted:true};
  streams[0].enqueue(new TextEncoder().encode(JSON.stringify(event)+'\n'));streams[0].close();
  await new Promise(resolve=>setTimeout(resolve,180));
  assert.equal(mics,1,'a cancelled pending hotkey must never restart listening');
  assert.equal(requests.filter(r=>r.url==='/api/companion/chat').length,1);
 }else{
  w.ApexDesktop.activate({kind:'look',source:'hotkey',seq:4});await tick();
  assert.equal(mics,0,'the ordinary browser cannot claim native activation');
  assert.equal(ready,0);
 }
 dom.window.close();
}
(async()=>{
 await scenario(true);await scenario(true,true);await scenario(false);
 console.log('PASS: silent native boot, F8 hint, owner authentication, click-free native activation, Discuss guidance, no competing browser claim, and stop/hide/cancel.');
})().catch(error=>{console.error(error);process.exitCode=1;});
