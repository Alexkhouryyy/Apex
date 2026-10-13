// Real Companion JS; microphone, native input and providers are bounded fakes.
const {JSDOM}=require('jsdom');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
async function scenario(failed=false,cancelled=false,restarted=false){
 const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{
  url:'http://127.0.0.1:7860/companion?desktop=1',runScripts:'outside-only'});
 const w=dom.window,$=id=>w.document.getElementById(id);
 let hands,expanded=0,drawn=0,insertions=[],requests=[],resolveTranscript;
 w.TextDecoder=TextDecoder;w.speechSynthesis={cancel(){},speak(){throw Error('Dictation must not speak');}};
 w.pywebview={api:{connect:async()=>({desktop:true,token:'owner',hotkey:'<f8>'}),ready:async()=>true,
  choose_mode:async()=>true,hide:async()=>true,expand:async()=>{expanded++;},sketch:async()=>{drawn++;return {open:true};},
  dictate:async(text,generation)=>{if(failed)throw Error('The selected field changed.');insertions.push({text,generation});}}};
 w.ApexHandsFree=class{
  constructor(opts){this.opts=opts;this.enabled=false;this.epoch=1;hands=this;}
  async start(){this.enabled=true;}stop(){this.enabled=false;this.epoch++;}pause(){}resume(){}
 };
 w.fetch=async(url,opts={})=>{
  requests.push(url);
  if(url==='/api/status')return Response.json({agent_ready:true,voices:{}});
  if(url==='/api/voicebox/profiles')return Response.json({profiles:[]});
  if(url.includes('/api/chat/threads'))return Response.json({messages:[]});
  if(url.includes('/api/companion/transcribe')){
   if(cancelled)await new Promise(resolve=>{resolveTranscript=resolve;});
   return Response.json({text:'Hello café 😀'});
  }
  throw Error(`Unexpected request ${url}`);
 };
 w.eval(fs.readFileSync(path.join(base,'speech_queue.js'),'utf8'));
 w.eval(fs.readFileSync(path.join(base,'companion.js'),'utf8'));
 await tick();await tick();
 assert.equal(hands,undefined,'Boot remains silent');
 $('desktop-task').value='dictate';await $('desktop-task').onchange();
 w.ApexDesktop.activate({kind:'dictate',generation:17,target:{title:'Notes'}});
 await tick();await tick();assert(hands.enabled);
 assert.match($('screen-status').textContent,/snapshots off/);
 const task=hands.opts.onSegment(new w.Blob(['voice']),hands.epoch);
 if(cancelled){
  await tick();w.ApexDesktop.activate({kind:'stop'});
  if(restarted){w.ApexDesktop.activate({kind:'dictate',generation:18,target:{title:'New field'}});await tick();}
  resolveTranscript();
 }
 await task;await tick();
 assert(!requests.some(url=>url.includes('/api/companion/chat')||url.includes('/api/companion/look')),
        'Dictation must never request screen capture or an agent reply');
 if(failed){
  assert.equal(insertions.length,0);assert.equal(expanded,1);assert.equal($('message').value,'Hello café 😀');
  assert.match($('error').textContent,/field changed/);assert(!hands.enabled);
 }else if(cancelled){assert.equal(insertions.length,0,'A late transcript cannot write after Stop');}
 else{assert.deepEqual(insertions,[{text:'Hello café 😀 ',generation:17}]);assert(hands.enabled);}
 w.ApexDesktop.activate({kind:'stop'});await tick();assert(!hands.enabled);
 assert.notEqual(w.document.getElementById('companion').dataset.view,'voice','Stop restores Chat');
 await $('desktop-draw').onclick();assert.equal(drawn,1);
 dom.window.close();
}
(async()=>{await scenario();await scenario(true);await scenario(false,true);await scenario(false,true,true);
 console.log('PASS: explicit dictation, Unicode transcript insertion, no chat/screen/TTS, changed-field recovery, late transcript cancellation, and drawing entry.');
})().catch(error=>{console.error(error);process.exitCode=1;});
