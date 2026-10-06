// "Apex appears as: Video (photoreal)" in the companion (companion.js +
// video-avatar.js). The avatar is stubbed here; check_video_avatar_browser.cjs
// plays real clips from a real avatar server in Chromium.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');

async function page({available=true, clipFails=false, renderFails=false, voice='openai', streamOk=false}={}){
  const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,$=id=>d.getElementById(id);
  w.localStorage.setItem('apex.presence.look','video');
  const s={rendered:[],played:[],audios:0,streams:0,speaks:0,disposed:0};
  w.Audio=class{constructor(){s.audios++;this.paused=false;}play(){queueMicrotask(()=>this.onended?.());return Promise.resolve();}pause(){}};
  w.URL.createObjectURL=()=> 'blob:x';w.URL.revokeObjectURL=()=>{};
  w.speechSynthesis={cancel(){},speak(){}};w.SpeechSynthesisUtterance=class{};
  w.fetch=async(url,opts={})=>{
    if(url==='/api/status')return Response.json({agent_ready:true,voices:{openai:true}});
    if(url==='/api/voicebox/profiles')return Response.json({profiles:[],streaming:streamOk});
    if(url==='/api/companion/chat')return new Response([{type:'start',thread_id:2},{type:'token',text:'First sentence here. Second one now.'},{type:'done',text:'First sentence here. Second one now.'}].map(v=>JSON.stringify(v)+'\n').join(''));
    if(url==='/api/speak'){s.speaks++;return new Response('RIFF'+s.speaks,{headers:{'Content-Type':'audio/wav'}});}
    if(url==='/api/speak/stream'){if(opts.method!=='POST')return Response.json({streaming:streamOk});s.streams++;return new Response(null,{status:404});}
    if(url==='/api/companion/timing')return Response.json({stored:{}});
    throw Error(url);
  };
  w.ApexVideoAvatar=class{
    constructor(host,request){s.host=host;this.request=request;}
    async start(){if(!available)throw Error('The video avatar is not running. Start it with Start-Apex-Video-Avatar.cmd.');this.ready=true;}
    render(blob){s.rendered.push(blob);return renderFails?Promise.reject(Error('502')):Promise.resolve(new w.Blob(['clip']));}
    play(clip,onStart){s.played.push(clip);if(clipFails)return Promise.reject(Error('decode'));onStart?.();return new Promise(r=>setTimeout(r,20));}
    stop(){} dispose(){s.disposed++;this.ready=false;}
  };
  for(const f of ['speech_queue.js','handsfree.js','companion.js'])w.eval(fs.readFileSync(path.join(base,f),'utf8'));
  const tick=(ms=25)=>new Promise(r=>setTimeout(r,ms));await tick();await tick();
  $('voice').value=voice;$('spoken').checked=true;
  return Object.assign(s,{w,$,tick,root:$('companion'),
    async ask(){$('message').value='Hi';$('send').click();for(let i=0;i<80&&!(s.played.length>=2||s.audios>=2);i++)await tick();await tick(100);},
    close(){dom.window.close();}});
}

(async()=>{
  // 1. Running: the face takes the orb's place, and each section plays as a clip.
  let p=await page();
  assert.equal(p.root.dataset.look,'video');assert.equal(p.host,p.$('avatar'));
  await p.ask();
  assert.equal(p.rendered.length,2,'both sections were sent to the avatar');
  assert.equal(p.played.length,2,'and played as clips');assert.equal(p.audios,0,'no separate audio');
  assert.equal(p.rendered[0].type,'audio/wav');p.close();

  // 2. The face needs each section whole: Celine's streaming is off in video mode.
  p=await page({voice:'voicebox',streamOk:true});
  await p.ask();assert.equal(p.streams,0,'no PCM streaming while the face speaks');assert.ok(p.speaks>=1&&p.played.length>=1);p.close();

  // 3. A clip that can't play: the same section plays as audio. Never silent.
  p=await page({clipFails:true});
  await p.ask();assert.equal(p.played.length,2);assert.equal(p.audios,2,'each failed clip fell back to its audio');p.close();

  p=await page({renderFails:true});
  await p.ask();assert.equal(p.played.length,0);assert.equal(p.audios,2,'a clip the server could not make: the audio plays');p.close();

  // 4. Avatar server off: the orb stays, and the setting says why.
  p=await page({available:false});
  assert.equal(p.root.dataset.look,undefined);assert.equal(p.$('presence-look').value,'orb');
  assert.match(p.$('presence-look').title,/Start-Apex-Video-Avatar/);
  assert.match(p.$('presence-look').querySelector('option[value=video]').textContent,/server off/);
  await p.ask();assert.equal(p.rendered.length,0);assert.equal(p.audios,2,'plain audio, as before');p.close();

  // 5. Switching away releases it.
  p=await page();p.$('presence-look').value='orb';p.$('presence-look').dispatchEvent(new p.w.Event('change'));await p.tick();
  assert.equal(p.disposed,1);assert.equal(p.root.dataset.look,undefined);p.close();
  console.log('PASS: the video avatar replaces the orb, plays each section as a lip-synced clip, turns PCM streaming off, falls back to audio when a clip cannot be made or played, keeps the orb with a reason when its server is off, and is released when switched away.');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1);});
