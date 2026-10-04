// Hands-free with the speech model (dashboard/static/handsfree.js + speech-model.js).
// Deterministic simulation: loudness and the model's speech probability are
// scripted per 50 ms tick. It checks the decisions, not the model's accuracy.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');

async function session({installed=true, attachFails=false, detector='model'}={}){
  const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,$=id=>d.getElementById(id);
  const s={clock:0,volume:0,prob:0,running:true,uploads:0,timings:[],utterance:null,attached:0,destroyed:0,recorders:0};
  const intervals=[];w.setInterval=(fn,ms)=>{const x={fn,ms,active:true};intervals.push(x);return x;};w.clearInterval=x=>{if(x)x.active=false;};
  Object.defineProperty(w.performance,'now',{value:()=>s.clock});w.TextDecoder=TextDecoder;
  const handle={at:0,take(){return s.prob;},destroy(){s.destroyed++;}};
  s.advance=(ms,volume,prob)=>{s.volume=volume;s.prob=prob;for(let i=0;i<ms;i+=50){s.clock+=50;if(s.running)handle.at=s.clock;for(const t of [...intervals])if(t.active&&t.ms===50)t.fn();}};
  w.MediaRecorder=class{constructor(){this.state='inactive';this.mimeType='audio/webm';s.recorders++;}start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new w.Blob(['audio'])});this.onstop?.();});}};
  const track=()=>({readyState:'live',stop(){},addEventListener(){}});
  Object.defineProperty(w.navigator,'mediaDevices',{value:{getUserMedia:async()=>{const t=track();return {getTracks:()=>[t],getAudioTracks:()=>[t]};}}});
  w.AudioContext=class{constructor(){this.state='running';}resume(){return Promise.resolve();}close(){return Promise.resolve();}createMediaStreamSource(){return {connect(){},disconnect(){}};}createAnalyser(){return {fftSize:2048,getFloatTimeDomainData(a){a.fill(s.volume);}};}};
  w.speechSynthesis={cancel(){},speak(u){s.utterance=u;}};w.SpeechSynthesisUtterance=class{constructor(t){this.text=t;}};
  w.fetch=async(url,opts={})=>{
    if(url==='/api/status')return Response.json({agent_ready:true});
    if(url==='/api/companion/speech-model')return Response.json(installed?{installed:true,version:'v-test',problem:null}:{installed:false,version:null,problem:'Not installed. Run Setup-Apex-Speech-Model.cmd.'});
    if(url.startsWith('/api/companion/transcribe')){s.uploads++;return Response.json({text:'Hello Apex'});}
    if(url==='/api/companion/timing'){s.timings.push(JSON.parse(opts.body));return Response.json({stored:{}});}
    if(url==='/api/companion/chat')return new Response([{type:'start',thread_id:2},{type:'token',text:'Hi'},{type:'done',text:'Hi'}].map(v=>JSON.stringify(v)+'\n').join(''));
    throw Error(url);
  };
  for(const f of ['speech_queue.js','handsfree.js','companion.js'])w.eval(fs.readFileSync(path.join(base,f),'utf8'));
  // The real speech-model.js loads ONNX in a browser; here its attach() is scripted.
  w.ApexSpeechModel={attach:async({version})=>{assert.equal(version,'v-test');s.attached++;if(attachFails)throw Error('WebAssembly is disabled');return handle;}};
  const tick=()=>new Promise(r=>setTimeout(r,25));
  await tick();$('speech-detector').value=detector;$('voice').value='browser';$('hands-free').checked=true;$('hands-free').dispatchEvent(new w.Event('change'));await tick();await tick();
  return Object.assign(s,{w,$,tick,close:()=>dom.window.close()});
}

(async()=>{
  // 1. Installed: the model decides, and a turn ends 0.7 s after the last speech frame.
  let s=await session();
  assert.equal(s.attached,1);assert.match(s.$('mic-note').textContent,/speech model/);
  s.advance(3000,.2,.05);await s.tick();                       // a loud fan: not speech
  s.advance(500,.05,.9);s.advance(650,0,.05);await s.tick();
  assert.equal(s.uploads,0,'0.65 s after speech is still inside the turn');
  s.advance(100,0,.05);await s.tick();await s.tick();
  assert.equal(s.uploads,1,'the turn ends after 0.7 s with the model');
  s.utterance.onend();await new Promise(r=>setTimeout(r,450));
  assert.equal(s.timings.at(-1).detector,'model');assert.equal(s.timings.at(-1).mode,'hands_free');
  // Soft word endings: probability between the keep (.25) and start (.5) bars keeps the turn open.
  s.advance(400,.05,.9);s.advance(1000,.001,.3);await s.tick();assert.equal(s.uploads,1);
  s.advance(800,0,.05);await s.tick();await s.tick();assert.equal(s.uploads,2);
  // 2. The model stalls: the same session falls back to loudness and its 1.2 s wait.
  s.utterance.onend();await new Promise(r=>setTimeout(r,450));
  s.running=false;s.advance(500,.05,0);s.advance(1000,0,0);await s.tick();assert.equal(s.uploads,2,'loudness waits 1.2 s');
  s.advance(300,0,0);await s.tick();await s.tick();assert.equal(s.uploads,3);
  s.$('stop').click();assert.equal(s.destroyed,1,'the model is released with the microphone');s.close();

  // 3. Barge-in while she speaks needs both: loud enough AND speech.
  s=await session();
  s.advance(500,.05,.9);s.advance(800,0,.05);await s.tick();await s.tick();assert.equal(s.uploads,1);
  assert.ok(s.utterance,'reply is being spoken');
  const before=s.recorders;
  s.advance(400,.2,.1);await s.tick();assert.equal(s.uploads,1,'loud non-speech (a door) does not cut her off');
  assert.equal(s.recorders,before,'it does not even start recording');
  s.advance(400,.05,.95);await s.tick();assert.equal(s.uploads,1,'quiet speech (her echo level) does not either');
  s.advance(50,.2,.9);s.advance(400,.2,.1);s.advance(1500,0,.01);await s.tick();await s.tick();
  assert.equal(s.uploads,1,'one loud word, then a loud non-speech noise, is not someone talking over her');
  s.close();

  // 4. Not installed, or the model fails to start: loudness, and the page says why.
  for(const opts of [{installed:false},{attachFails:true}]){
    s=await session(opts);
    assert.match(s.$('mic-note').textContent,/loudness \(speech model off\)/);
    assert.ok(s.$('mic-note').title.length>5,'the reason is on hover');
    s.advance(500,.05,0);s.advance(1300,0,0);await s.tick();await s.tick();assert.equal(s.uploads,1);
    s.utterance.onend();await new Promise(r=>setTimeout(r,450));
    assert.equal(s.timings.at(-1).detector,'loudness');s.close();
  }
  // 5. Choosing Loudness never loads the model.
  s=await session({detector:'loudness'});assert.equal(s.attached,0);assert.doesNotMatch(s.$('mic-note').textContent,/speech model/);s.close();
  console.log('PASS: the speech model ends a turn 0.7 s after speech (loudness 1.2 s), ignores loud non-speech, keeps soft endings, falls back to loudness when stalled, missing or failing, needs loud speech to barge in, tags timing with the detector, and is released on Stop.');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1);});
