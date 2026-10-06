// The live photoreal face (Simli) in the companion: live-avatar.js and how
// companion.js routes speech to it. Simli itself is stubbed; the bundle is
// loaded for real in check_live_avatar_browser.cjs.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');

// --- 1. live-avatar.js on its own -------------------------------------------
async function unit(){
  const dom=new JSDOM('<div id=host></div>',{url:'http://localhost/',runScripts:'outside-only'});
  const w=dom.window;let clock=0;Object.defineProperty(w.performance,'now',{value:()=>clock});
  w.eval(fs.readFileSync(path.join(base,'live-avatar.js'),'utf8'));
  const R=w.ApexLiveAvatar.Resampler;
  // 24 kHz -> 16 kHz: two thirds the samples, and the same result however the stream is cut.
  const tone=Int16Array.from({length:24000},(_,i)=>Math.round(10000*Math.sin(2*Math.PI*220*i/24000)));
  const whole=new R(24000).push(tone);
  assert.ok(Math.abs(whole.length-16000)<=1,`length ${whole.length}`);
  const r=new R(24000),parts=[];let i=0;for(const n of [1,777,3001,2,9999]){parts.push(...r.push(tone.slice(i,i+n)));i+=n;}parts.push(...r.push(tone.slice(i)));
  assert.equal(parts.length,whole.length);assert.ok(parts.every((v,k)=>Math.abs(v-whole[k])<=1),'chunked resampling matches one pass');
  // The tone survives: same frequency at the new rate (zero crossings per second).
  const crossings=a=>{let c=0;for(let k=1;k<a.length;k++)if((a[k-1]<0)!==(a[k]<0))c++;return c;};
  assert.ok(Math.abs(crossings(whole)-440)<=4,'220 Hz is still 220 Hz');
  // say(): 16 kHz bytes to Simli; finished() waits for the queued speech and the voice to go quiet.
  const sent=[];let cleared=0,level=0;
  const a=new w.ApexLiveAvatar(w.document.getElementById('host'),null);
  a.ready=true;a.client={sendAudioData:b=>sent.push(b.length),ClearBuffer:()=>cleared++};a.level=()=>level;
  a.say(Int16Array.from({length:24000}),24000);                      // 1 s of speech
  assert.equal(sent[0],16000*2);
  let done=false,heard=0;const fin=a.finished(()=>heard++).then(()=>done=true);
  const step=async ms=>{clock+=ms;await new Promise(r=>setTimeout(r,60));};
  await step(300);level=0.2;await step(200);assert.equal(heard,1,'the voice is heard');
  await step(500);assert.ok(!done,'still talking');level=0;await step(200);assert.ok(!done,'a short pause is not the end');
  await step(600);assert.ok(done,'done once the queued second has played and the voice is quiet');
  // A pause mid-speech is not the end while queued speech remains.
  a.say(Int16Array.from({length:16000*3}),16000);                   // 3 s queued
  done=false;heard=0;const fin2=a.finished(()=>heard++).then(()=>done=true);
  level=0.2;await step(300);level=0;await step(900);
  assert.ok(heard===1&&!done,'a 0.9 s pause with 2 s still queued is not the end');
  level=0;await step(2500);assert.ok(done);
  a.say(Int16Array.from({length:16000}),16000);a.interrupt();assert.equal(cleared,1);assert.equal(a.speechEnds,0);
  // ensure(): one session at a time however many callers; park() hangs up and keeps the face's last frame;
  // Simli ending the session is a hang-up (parked), not a failure.
  let sessions=0;const handlers={};
  w.ApexSimli={LogLevel:{ERROR:0},SimliClient:class{constructor(){this.stopped=0;}on(e,f){handlers[e]=f;}start(){return new Promise(r=>setTimeout(r,30));}stop(){this.stopped++;}sendAudioData(){}ClearBuffer(){}}};
  const b=new w.ApexLiveAvatar(w.document.getElementById('host'),async url=>{sessions++;return {json:async()=>({available:true,session_token:'t',ice_servers:[]})};});
  await Promise.all([b.ensure(),b.ensure(),b.ensure()]);
  assert.equal(sessions,1,'three callers, one paid session');assert.ok(b.ready);
  const c1=b.client;b.park();assert.ok(!b.ready&&b.parked&&c1.stopped===1);assert.ok(b.host.classList.contains('avatar-parked'));
  await b.ensure();assert.equal(sessions,2);assert.ok(b.ready&&!b.host.classList.contains('avatar-parked'));
  let lost=null;b.onLost=r=>lost=r;handlers.disconnected();assert.ok(!b.ready&&b.parked&&lost===null,'a Simli hang-up parks the face');
  dom.window.close();
}

// --- 2. companion.js routing speech to the live face ------------------------
async function page({fails=false, streamOk=false, voice='openai', talkMs=20, ensureFails=false, parkMs=0, connectMs=0}={}){
  const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,$=id=>d.getElementById(id);
  w.localStorage.setItem('apex.presence.look','live');if(parkMs)w.ApexLiveParkMs=parkMs;
  const s={said:[],interrupts:0,audios:0,finishes:0,disposed:0,instances:[],ensures:0,parks:0};
  w.Audio=class{constructor(){s.audios++;}play(){queueMicrotask(()=>this.onended?.());return Promise.resolve();}pause(){}};
  w.URL.createObjectURL=()=> 'blob:x';w.URL.revokeObjectURL=()=>{};
  w.speechSynthesis={cancel(){},speak(){}};w.SpeechSynthesisUtterance=class{};
  w.OfflineAudioContext=class{constructor(c,l,rate){this.rate=rate;}async decodeAudioData(){return {getChannelData:()=>new Float32Array(this.rate/2).fill(0.25)};}};
  w.AudioContext=class{constructor(){throw Error('no local playback in live mode');}};
  w.fetch=async(url,opts={})=>{
    if(url==='/api/status')return Response.json({agent_ready:true,voices:{openai:true}});
    if(url==='/api/voicebox/profiles')return Response.json({profiles:[]});
    if(url==='/api/companion/chat')return new Response([{type:'start',thread_id:2},{type:'token',text:'One sentence here.'},{type:'done',text:'One sentence here.'}].map(v=>JSON.stringify(v)+'\n').join(''));
    if(url==='/api/speak')return new Response('RIFFdata',{headers:{'Content-Type':'audio/wav'}});
    if(url==='/api/speak/stream'){
      if(opts.method!=='POST')return Response.json({streaming:streamOk});
      const pieces=[new Uint8Array(4801),new Uint8Array(4799),new Uint8Array(2400)];        // odd sizes: a sample split across pieces
      return new Response(new ReadableStream({start(c){for(const p of pieces)c.enqueue(p);c.close();}}),{headers:{'X-Sample-Rate':'24000'}});
    }
    if(url==='/api/companion/timing')return Response.json({stored:{}});
    throw Error(url);
  };
  w.ApexLiveAvatar=class{
    constructor(host){this.host=host;s.instances.push(this);}
    async start(){if(fails)throw Error('Add SIMLI_API_KEY and SIMLI_FACE_ID to Apex settings.');this.ready=true;}
    ensure(){s.ensures++;if(ensureFails)return Promise.reject(Error('Simli could not be reached.'));
      if(this.ready)return Promise.resolve();this.pending=this.pending||new Promise(r=>setTimeout(()=>{this.ready=true;this.pending=null;r();},connectMs));return this.pending;}
    park(){s.parks++;this.ready=false;}
    say(pcm,rate){s.said.push({n:pcm.length,rate});}
    endStream(){} interrupt(){s.interrupts++;}
    finished(onSound){s.finishes++;onSound?.();return new Promise(r=>setTimeout(r,talkMs));}
    dispose(){s.disposed++;this.ready=false;}
  };
  for(const f of ['speech_queue.js','handsfree.js','companion.js'])w.eval(fs.readFileSync(path.join(base,f),'utf8'));
  const tick=(ms=25)=>new Promise(r=>setTimeout(r,ms));await tick();await tick();
  $('voice').value=voice;$('spoken').checked=true;
  return Object.assign(s,{w,$,tick,root:$('companion'),
    async ask(){$('message').value='Hi';$('send').click();for(let i=0;i<80&&!s.finishes&&!s.audios;i++)await tick();await tick(80);},
    close(){dom.window.close();}});
}

(async()=>{
  await unit();
  // A clip voice: decoded to 16 kHz and sent to the face; nothing plays locally.
  let p=await page();
  assert.equal(p.root.dataset.look,'live');
  await p.ask();assert.deepEqual(p.said,[{n:8000,rate:16000}]);assert.equal(p.audios,0);assert.equal(p.finishes,1);p.close();
  // Celine streamed: each piece goes to the face as it arrives, at its own rate, with no byte lost.
  p=await page({voice:'voicebox',streamOk:true});
  await p.ask();
  assert.ok(p.said.length>=3&&p.said.every(x=>x.rate===24000),JSON.stringify(p.said));
  assert.equal(p.said.reduce((n,x)=>n+x.n,0),(4801+4799+2400)/2,'every sample delivered');p.close();
  // Stop interrupts the face.
  p=await page({talkMs:2000});p.$('message').value='Hi';p.$('send').click();for(let i=0;i<40&&!p.finishes;i++)await p.tick();
  p.$('stop').click();await p.tick();assert.ok(p.interrupts>=1,'Stop clears what the face has queued');p.close();
  // Stop during Celine's streamed voice interrupts the face too.
  p=await page({voice:'voicebox',streamOk:true,talkMs:2000});p.$('message').value='Hi';p.$('send').click();
  for(let i=0;i<40&&!p.finishes;i++)await p.tick();
  p.$('stop').click();await p.tick();assert.ok(p.interrupts>=1,'Stop clears a streamed reply too');p.close();
  // Not set up: the orb stays, the setting says why, and speech is plain audio.
  p=await page({fails:true});
  assert.equal(p.root.dataset.look,undefined);assert.equal(p.$('presence-look').value,'orb');
  assert.match(p.$('presence-look').title,/SIMLI_API_KEY/);
  await p.ask();assert.equal(p.said.length,0);assert.ok(p.audios>=1);p.close();
  // Lost mid-session: back to the orb, and the next reply is plain audio.
  p=await page();p.instances[0].onLost('The live face disconnected.');
  assert.equal(p.root.dataset.look,undefined);assert.equal(p.disposed,1);
  await p.ask();assert.equal(p.said.length,0);assert.ok(p.audios>=1);p.close();
  // Quiet for a while: the face hangs up. The next reply reconnects it first, then speaks.
  p=await page({parkMs:150});await p.ask();await p.tick(300);
  assert.ok(p.parks>=1,'parked after the quiet spell');assert.equal(p.instances[0].ready,false);
  const before=p.ensures;p.said.length=0;p.finishes=0;await p.ask();
  assert.ok(p.ensures>before,'reconnected');assert.equal(p.said.length,1,'and spoke through the face');p.close();
  // The reply is ready before the reconnect finishes: it waits for the face rather than skipping it.
  p=await page({connectMs:300});p.instances[0].park();p.said.length=0;
  p.$('message').value='Hi';p.$('send').click();for(let i=0;i<60&&!p.finishes&&!p.audios;i++)await p.tick();await p.tick(100);
  assert.equal(p.said.length,1,'spoke through the face once it connected');assert.equal(p.audios,0);p.close();
  // Typing warms it up before the reply exists.
  p=await page();p.instances[0].park();const e0=p.ensures;
  p.$('message').value='h';p.$('message').dispatchEvent(new p.w.Event('input'));await p.tick();
  assert.ok(p.ensures>e0&&p.instances[0].ready,'typing reconnects the face');p.close();
  // Can't reconnect: this reply is plain audio, and the live look stays for next time.
  p=await page({ensureFails:true});p.instances[0].park();
  await p.ask();assert.equal(p.said.length,0);assert.ok(p.audios>=1);assert.equal(p.$('presence-look').value,'live');
  assert.match(p.$('presence-look').title,/could not be reached/);p.close();
  // Page hidden while quiet: hang up at once.
  p=await page();Object.defineProperty(p.w.document,'hidden',{value:true,configurable:true});
  p.w.document.dispatchEvent(new p.w.Event('visibilitychange'));assert.equal(p.parks,1);p.close();
  console.log('PASS: 16 kHz resampling is exact across chunk boundaries and keeps pitch, the face is done only when its queued speech has played and gone quiet, clip voices and streamed voices reach the face with no sample lost, Stop clears it, and when Simli is not set up or drops, the orb returns and speech plays as audio; it hangs up when quiet or hidden, keeps one session however many callers, reconnects on typing or before a reply, and speaks as plain audio when it cannot reconnect.');
  process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
