// Talking over Celine: a deterministic simulation of the microphone level and
// the clock — not an acoustic test. It proves the page's logic: talking over
// a spoken or still-being-written reply stops it and sends what you said; a
// short noise does not; an "interruption" that was her own voice is dropped;
// and detection-to-stop fits Pillar 1's 0.3 s. Requires jsdom on NODE_PATH.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
let clock=0,volume=0,cancels=0,spoken=[],chats=[],cancelRequests=0,transcript='Actually tell me about Paris instead';
let holdChat=null;
const intervals=[];w.setInterval=(fn,ms)=>{const x={fn,ms,active:true};intervals.push(x);return x;};w.clearInterval=x=>{if(x)x.active=false;};
Object.defineProperty(w.performance,'now',{value:()=>clock});w.TextDecoder=TextDecoder;
const tick=()=>new Promise(r=>setTimeout(r,25));
const advance=(ms,v)=>{volume=v;for(let i=0;i<ms;i+=50){clock+=50;for(const t of [...intervals])if(t.active&&t.ms===50)t.fn();}};
const recorders=[];
w.MediaRecorder=class{
 constructor(){this.state='inactive';this.mimeType='audio/webm';recorders.push(this);}
 start(){this.state='recording';}
 stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new w.Blob(['audio'])});this.onstop?.();});}
};
const track=()=>({readyState:'live',stop(){},addEventListener(){}});
Object.defineProperty(w.navigator,'mediaDevices',{value:{getUserMedia:async()=>{const t=track();return {getTracks:()=>[t],getAudioTracks:()=>[t]};}}});
w.AudioContext=class{constructor(){this.state='running';}resume(){return Promise.resolve();}close(){return Promise.resolve();}createMediaStreamSource(){return {connect(){},disconnect(){}};}createAnalyser(){return {fftSize:2048,getFloatTimeDomainData(a){a.fill(volume);}};}};
let utterance=null,playing=0;
w.Audio=class{play(){playing++;setTimeout(()=>this.onplaying?.(),1);return Promise.resolve();}pause(){playing--;}};
w.URL.createObjectURL=()=>'blob:x';w.URL.revokeObjectURL=()=>{};
w.speechSynthesis={cancel(){cancels++;const u=utterance;utterance=null;u?.onerror?.({error:'interrupted'});},speak(u){utterance=u;spoken.push(u.text);}};
w.SpeechSynthesisUtterance=class{constructor(t){this.text=t;}};
const REPLY='The Eiffel Tower was finished in eighteen eighty nine for the world fair in Paris.';
w.fetch=async(url,opts={})=>{
 if(url==='/api/status')return Response.json({agent_ready:true});
 if(url==='/api/voicebox/profiles')return Response.json({profiles:[]});
 if(url==='/api/speak/stream')return Response.json({streaming:false});
 if(url.startsWith('/api/companion/look')){await new Promise(r=>setTimeout(r,5000));return Response.json({item:null,seq:0});}
 if(url.startsWith('/api/companion/transcribe'))return Response.json({text:transcript});
 if(url.startsWith('/api/companion/cancel/')){cancelRequests++;holdChat?.();return Response.json({cancel_requested:true});}
 if(url==='/api/companion/timing')return Response.json({ok:true});
 if(url==='/api/speak')return new Response(new Blob(['RIFF']));
 if(url==='/api/companion/chat'){
  chats.push(JSON.parse(opts.body).message);
  const enc=s=>new TextEncoder().encode(JSON.stringify(s)+'\n');
  const slow=chats.length===3;
  return new Response(new ReadableStream({async start(c){
   c.enqueue(enc({type:'start',thread_id:2}));
   if(slow){await new Promise(r=>holdChat=r);c.enqueue(enc({type:'done',text:'Partial answer',interrupted:true}));c.close();return;}
   c.enqueue(enc({type:'done',text:REPLY}));c.close();}}));
 }
 throw Error(url);
};
w.eval(fs.readFileSync(path.join(base,'speech_queue.js'),'utf8'));w.eval(fs.readFileSync(path.join(base,'handsfree.js'),'utf8'));w.eval(fs.readFileSync(path.join(base,'companion.js'),'utf8'));
const say=async(ms=500)=>{advance(ms,.1);advance(1300,0);for(let i=0;i<6;i++)await tick();};
(async()=>{
 await tick();$('voice').value='browser';$('hands-free').checked=true;$('hands-free').dispatchEvent(new w.Event('change'));await tick();
 assert.equal($('barge-in').checked,true,'interrupting by talking is on by default');

 // 1. A normal turn: she is now speaking.
 transcript='Tell me about the Eiffel Tower';await say();
 assert.equal(chats.length,1);assert.ok(utterance,'she should be speaking');

 // 2. A short noise (a cough) over her: not an interruption.
 const c0=cancels;advance(100,.2);advance(300,0);await tick();
 assert.equal(cancels,c0,'a 0.1 s noise must not stop her');assert.ok(utterance,'she keeps speaking');

 // 3. Quieter than the barge bar (her voice leaking through speakers at
 //    the normal threshold): not an interruption either.
 advance(1000,.03);await tick();assert.ok(utterance,'a level just over the normal threshold must not stop her');

 // 4. You talk over her: she stops within 0.3 s and what you said is sent.
 transcript='Actually tell me about Paris instead';
 const onsetClock=clock;advance(250,.2);
 assert.equal(utterance,null,'talking over her must stop her');
 const ms=Number(/stopped ([0-9.]+)s/.exec($('voice-timing').textContent)[1])*1000;
 assert.ok(ms<=300,`stopped ${ms} ms after you started talking — Pillar 1 says 300`);
 assert.ok(clock-onsetClock<=300);
 advance(800,.2);advance(1300,0);for(let i=0;i<10;i++)await tick();
 assert.equal(chats.length,2,'what you said over her must be sent');
 assert.equal(chats[1],'Actually tell me about Paris instead');

 // 5. Talking over a reply that is still being WRITTEN cancels the turn.
 utterance?.onend?.();await new Promise(r=>setTimeout(r,450));
 transcript='Something long please';await say();
 assert.equal(chats.length,3);
 transcript='Never mind, stop';advance(300,.2);for(let i=0;i<6;i++)await tick();
 assert.equal(cancelRequests,1,'the turn being written must be cancelled on the server');
 advance(600,.2);advance(1300,0);for(let i=0;i<30;i++)await tick();
 assert.equal(chats.length,4,'the interruption is sent once the cancelled turn has wound down');
 assert.equal(chats[3],'Never mind, stop');

 // 6. An "interruption" that transcribes as her own words is echo: dropped.
 utterance?.onend?.();await new Promise(r=>setTimeout(r,450));
 transcript='Tell me about the Eiffel Tower';await say();
 const before=chats.length;
 transcript='Eiffel Tower was finished in eighteen eighty';advance(300,.2);advance(500,.2);advance(1300,0);for(let i=0;i<10;i++)await tick();
 assert.equal(chats.length,before,'her own voice must not be sent as the user');
 assert.match($('error').textContent,/own voice/);

 // 7. Turned off: talking over her does nothing until she finishes.
 utterance?.onend?.();await new Promise(r=>setTimeout(r,450));
 transcript='Tell me about the Eiffel Tower';$('error').textContent='';await say();
 $('barge-in').checked=false;advance(1000,.2);await tick();
 assert.ok(utterance,'with the setting off she keeps speaking');

 // 8. Celine's voice (the local voice server, spoken as it is written):
 //    the same, while the reply is still being played.
 $('barge-in').checked=true;utterance?.onend?.();await new Promise(r=>setTimeout(r,450));
 $('voice').value='voicebox';$('spoken').checked=true;$('stream-speech').checked=true;
 transcript='Tell me about the Eiffel Tower';await say();for(let i=0;i<10;i++)await tick();
 assert.equal(playing,1,'Celine should be playing');
 const n=chats.length;transcript='Wait, a different question';
 advance(250,.2);for(let i=0;i<4;i++)await tick();
 assert.equal(playing,0,'talking over Celine must stop her audio');
 advance(600,.2);advance(1300,0);for(let i=0;i<30;i++)await tick();
 assert.equal(chats.length,n+1);assert.equal(chats.at(-1),'Wait, a different question');

 console.log('PASS: talking over a spoken reply stops it within 0.3 s and sends what you said; talking over a reply being written cancels it; '
  +'short noises and speaker-level leak do not interrupt; echo of her own words is dropped with a reason; the setting turns it off.');
 dom.window.close();process.exit(0);
})().catch(e=>{console.error(e);dom.window.close();process.exit(1);});
