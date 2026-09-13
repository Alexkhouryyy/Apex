// DOM simulation only: mocks media, floating windows, and providers.
// Requires jsdom available on NODE_PATH; does not verify real browser/media support.
const {JSDOM}=require('jsdom');
const fs=require('fs'); const assert=require('node:assert/strict');
const base=require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;
const dom=new JSDOM(fs.readFileSync(base+'companion.html','utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only'});
const w=dom.window, d=w.document; const $=id=>d.getElementById(id);
const tick=()=>new Promise(r=>setTimeout(r,15));
let requests=[], streams=[], stopped=0, spoken=0, ttsResolve, transcribeResolve;
w.TextDecoder=TextDecoder;
w.speechSynthesis={cancel(){},speak(u){spoken++;u.onend?.();}};
w.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
w.fetch=async(path,opts={})=>{
 requests.push({path,opts});
 if(path==='/api/status')return Response.json({});
 if(path.startsWith('/api/chat/threads'))return Response.json({messages:[]});
 if(path==='/api/transcribe')return new Promise(r=>{transcribeResolve=r;});
 if(path==='/api/speak')return new Promise(r=>{ttsResolve=r;});
 if(path.includes('/cancel/'))return Response.json({cancel_requested:true});
 if(path==='/api/companion/chat'){
   let controller;const s=new ReadableStream({start(c){controller=c;}}); streams.push(controller);
   return new Response(s,{headers:{'Content-Type':'application/x-ndjson'}});
 }
 throw Error(path);
};
const emit=(c,data)=>c.enqueue(new TextEncoder().encode(JSON.stringify(data)+'\n'));
function finish(text='A tested reply.'){const c=streams.at(-1);emit(c,{type:'start',thread_id:71});emit(c,{type:'token',text});emit(c,{type:'tool',name:'recall',phase:'result',result:'<script>untrusted result</script>'});emit(c,{type:'done',text,interrupted:false});c.close();}
w.eval(fs.readFileSync(base+'companion.js','utf8'));
(async()=>{
 await tick(); assert.match($('status').textContent,/Ready/);
 $('spoken').checked=false;
 $('message').value='First question';$('send').click();await tick();
 assert.equal($('send').disabled,true);finish();await tick();
 assert.equal($('send').disabled,false);assert.match($('messages').textContent,/tested reply/);
 assert.equal($('messages').querySelectorAll('script').length,0);
 $('mode').value='work';$('mode').dispatchEvent(new w.Event('change'));
 $('message').value='Second question';$('send').click();await tick();
 let body=JSON.parse(requests.filter(r=>r.path==='/api/companion/chat').at(-1).opts.body);
 assert.equal(body.thread_id,71);assert.equal(body.mode,'work');assert.equal(body.screen_image,null);
 finish();await tick();
 const track={readyState:'live',stop(){stopped++;this.readyState='ended';},addEventListener(){}};
 Object.defineProperty(w.navigator,'mediaDevices',{value:{getDisplayMedia:async()=>({getTracks:()=>[track],getVideoTracks:()=>[track]}),getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})},configurable:true});
 Object.defineProperty($('preview'),'videoWidth',{value:1440});Object.defineProperty($('preview'),'videoHeight',{value:900});
 $('preview').play=async()=>{};w.HTMLCanvasElement.prototype.getContext=()=>({drawImage(){}});w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/jpeg;base64,test-frame';
 $('share').click();await tick();assert.equal($('share').getAttribute('aria-pressed'),'true');
 $('review').click();await tick();body=JSON.parse(requests.filter(r=>r.path==='/api/companion/chat').at(-1).opts.body);
 assert.equal(body.screen_image,'data:image/jpeg;base64,test-frame');finish();await tick();
 $('share').click();assert.equal(stopped,1);assert.equal($('review').disabled,true);
 $('message').value='Stop this turn';$('send').click();await tick();$('stop').click();await tick();finish('Interrupted result');await tick();
 assert.equal(requests.filter(r=>r.path.includes('/cancel/')).length,2);
 assert.match($('status').textContent,/Stopped/);
 $('spoken').checked=true;$('voice').value='openai';$('message').value='Speak this';$('send').click();await tick();finish();await tick();
 assert.ok(ttsResolve);$('spoken').checked=false;$('spoken').dispatchEvent(new w.Event('change'));ttsResolve(new Response(new Blob(['audio'])));await tick();assert.equal(spoken,0);
 w.MediaRecorder=class{constructor(){this.state='inactive';this.mimeType='audio/webm';}start(){this.state='recording';}stop(){this.state='inactive';this.ondataavailable?.({data:new w.Blob(['voice'])});this.onstop?.();}};
 $('mic').click();await tick();assert.equal($('mic').getAttribute('aria-pressed'),'true');$('mic').click();await tick();
 assert.ok(transcribeResolve);const before=requests.filter(r=>r.path==='/api/companion/chat').length;
 $('stop').click();transcribeResolve(Response.json({text:'Do not send after stop'}));await tick();
 assert.equal(requests.filter(r=>r.path==='/api/companion/chat').length,before);
 const pipDom=new JSDOM('<html><head></head><body></body></html>',{url:'http://localhost:7860'});
 w.documentPictureInPicture={requestWindow:async()=>pipDom.window};pipDom.window.close=()=>pipDom.window.dispatchEvent(new pipDom.window.Event('pagehide'));
 $('float').click();await tick();assert.equal(d.getElementById('companion'),null);assert.ok(pipDom.window.document.getElementById('companion'));
 pipDom.window.document.getElementById('float').click();await tick();assert.ok($('companion'));assert.equal($('float').textContent,'Float ↗');
 $('new').click();assert.ok($('welcome'));assert.equal(w.localStorage.getItem('apex_companion_thread'),null);
 console.log('PASS: client streaming, stable thread, mode selection, tool text escaping, snapshot attachment, capture stop, cancellation handshake, stale TTS suppression, stopped transcription, floating DOM transfer and new chat.');
 dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
