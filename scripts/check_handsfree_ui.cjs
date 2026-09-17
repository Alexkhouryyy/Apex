// Deterministic local media simulation, not an acoustic/hardware test.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
let clock=0,wall=100000,volume=0,micRequests=0,uploads=0,chats=0,stopped=0,utterance=null,lateTranscript=null,hold=false;
const intervals=[];w.setInterval=(fn,ms)=>{const x={fn,ms,active:true};intervals.push(x);return x;};w.clearInterval=x=>{if(x)x.active=false;};
Object.defineProperty(w.performance,'now',{value:()=>clock});w.Date.now=()=>wall;w.TextDecoder=TextDecoder;
const tick=()=>new Promise(r=>setTimeout(r,25));
const advance=(ms,v)=>{volume=v;for(let i=0;i<ms;i+=50){clock+=50;for(const t of [...intervals])if(t.active&&t.ms===50)t.fn();}};
const recorders=[];
w.MediaRecorder=class{
 constructor(){this.state='inactive';this.mimeType='audio/webm';recorders.push(this);}
 start(){this.state='recording';}
 stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new w.Blob(['audio'])});this.onstop?.();});}
};
const track=()=>({readyState:'live',stop(){this.readyState='ended';stopped++;},addEventListener(){}});
const stream=()=>{const t=track();return {getTracks:()=>[t],getAudioTracks:()=>[t],getVideoTracks:()=>[t]};};
Object.defineProperty(w.navigator,'mediaDevices',{value:{getUserMedia:async()=>{micRequests++;return stream();},getDisplayMedia:async()=>stream()}});
w.AudioContext=class{constructor(){this.state='running';}resume(){return Promise.resolve();}close(){return Promise.resolve();}createMediaStreamSource(){return {connect(){},disconnect(){}};}createAnalyser(){return {fftSize:2048,getFloatTimeDomainData(a){a.fill(volume);}};}};
w.speechSynthesis={cancel(){},speak(u){utterance=u;}};w.SpeechSynthesisUtterance=class{constructor(t){this.text=t;}};
w.fetch=async(url,opts={})=>{
 if(url==='/api/status')return Response.json({agent_ready:true});
 if(url.startsWith('/api/companion/transcribe')){uploads++;if(hold)return new Promise(resolve=>lateTranscript=resolve);return Response.json({text:'Hello Apex'});}
 if(url.startsWith('/api/companion/cancel/'))return Response.json({cancel_requested:true});
 if(url==='/api/companion/chat'){
  chats++;const body=JSON.parse(opts.body);
  if(body.proactive){assert.equal(body.mode,'discuss');assert.ok(body.screen_image);}
  const text=body.proactive?'NOTHING_TO_ADD':'Hello Alex';
  return new Response([{type:'start',thread_id:2},{type:'token',text},{type:'done',text}].map(v=>JSON.stringify(v)+'\n').join(''));
 }
 throw Error(url);
};
w.eval(fs.readFileSync(path.join(base,'speech_queue.js'),'utf8'));w.eval(fs.readFileSync(path.join(base,'handsfree.js'),'utf8'));w.eval(fs.readFileSync(path.join(base,'companion.js'),'utf8'));
(async()=>{
 await tick();$('voice').value='browser';$('hands-free').checked=true;$('hands-free').dispatchEvent(new w.Event('change'));await tick();
 assert.equal(micRequests,1);assert.equal(recorders.at(-1).state,'recording');
 advance(8100,0);await tick();assert.equal(uploads,0); // silence stays local
 advance(500,.05);advance(1300,0);await tick();await tick();
 assert.equal(uploads,1);assert.equal(chats,1);assert.equal(utterance.text,'Hello Alex');
 const oldUploads=uploads;advance(3000,.2);await tick();assert.equal(uploads,oldUploads); // no echo turns
 utterance.onend();await new Promise(r=>setTimeout(r,450));
 assert.equal(recorders.at(-1).state,'recording');assert.equal(micRequests,1);
 hold=true;advance(500,.05);advance(1300,0);await tick();assert.ok(lateTranscript);
 $('stop').click();lateTranscript(Response.json({text:'Do not send this'}));await tick();
 assert.equal(chats,1);assert.equal($('hands-free').checked,false);assert.ok(stopped>0);
 Object.defineProperty($('preview'),'videoWidth',{value:1440});Object.defineProperty($('preview'),'videoHeight',{value:900});$('preview').play=async()=>{};
 w.HTMLCanvasElement.prototype.getContext=()=>({drawImage(){}});w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/jpeg;base64,test';
 $('share').click();await tick();$('check-in').checked=true;$('check-in').dispatchEvent(new w.Event('change'));
 wall+=61000;for(const t of intervals)if(t.ms===1000)t.fn();await tick();await tick();
 assert.equal(chats,2);assert.ok(!$('messages').textContent.includes('NOTHING_TO_ADD'));
 $('stop').click();assert.equal($('check-in').checked,false);
 wall+=61000;for(const t of intervals)if(t.ms===1000)t.fn();await tick();assert.equal(chats,2);
 console.log('PASS: automatic utterance sending, silence discard, microphone reuse, echo pause, automatic re-listening, stale transcript suppression, proactive snapshot, quiet result and Stop.');
 dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});

