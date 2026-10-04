// "Apex appears as: Orb / Character" in the companion (companion.js + avatar.js).
// The 3D drawing is stubbed here; check_avatar_browser.cjs draws it for real.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const store={};

async function page({webgl=true, preload=false, custom=false}={}){
  const dom=new JSDOM(fs.readFileSync(path.join(base,'companion.html'),'utf8'),{url:'http://localhost:7860/companion',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,$=id=>d.getElementById(id);
  for(const [k,v] of Object.entries(store))w.localStorage.setItem(k,v);
  const s={clock:1000,created:[],disposed:0,utterance:null};
  Object.defineProperty(w.performance,'now',{value:()=>s.clock});w.TextDecoder=TextDecoder;
  w.speechSynthesis={speaking:false,cancel(){},speak(u){s.utterance=u;}};w.SpeechSynthesisUtterance=class{constructor(t){this.text=t;}};
  w.fetch=async(url)=>{
    if(url==='/api/status')return Response.json({agent_ready:true});
    if(url==='/static/avatars/apex.glb')return new Response(null,{status:custom?200:404});
    if(url==='/api/companion/chat')return new Response([{type:'start',thread_id:2},{type:'token',text:'Hi'},{type:'done',text:'Hi'}].map(v=>JSON.stringify(v)+'\n').join(''));
    throw Error(url);               // like the other checks: no instant answers that let polling loops spin
  };
  const stub={create(host,opts){s.created.push({host,opts});if(!webgl)return null;return {dispose(){s.disposed++;}};}};
  if(preload)w.ApexAvatarCharacter=stub;
  for(const f of ['speech_queue.js','handsfree.js','companion.js'])w.eval(fs.readFileSync(path.join(base,f),'utf8'));
  const tick=()=>new Promise(r=>setTimeout(r,25));await tick();
  return Object.assign(s,{w,d,$,tick,stub,root:$('companion'),
    async choose(v){$('presence-look').value=v;$('presence-look').dispatchEvent(new w.Event('change'));await tick();},
    save(){for(let i=0;i<w.localStorage.length;i++){const k=w.localStorage.key(i);if(k!=='apex_companion_thread')store[k]=w.localStorage.getItem(k);}},
    close(){dom.window.close();}});
}

(async()=>{
  // 1. The orb by default: nothing is drawn until you choose the character.
  let p=await page();
  assert.equal(p.$('presence-look').value,'orb');assert.equal(p.root.dataset.look,undefined);assert.equal(p.created.length,0);

  // 2. Chosen before the 3D code has loaded: the orb stays until it is ready.
  await p.choose('character');
  assert.equal(p.root.dataset.look,undefined,'no blank space while the character loads');
  p.w.ApexAvatarCharacter=p.stub;p.w.dispatchEvent(new p.w.Event('apex-avatar-ready'));await p.tick();
  assert.equal(p.created.length,1);assert.equal(p.created[0].host,p.$('avatar'));
  assert.equal(p.created[0].opts.model,null,'no model of your own: the built-in suit');
  assert.equal(p.root.dataset.look,'character');
  assert.equal(p.w.localStorage.getItem('apex.presence.look'),'character','the choice is remembered');

  // 3. It is told what Apex is doing.
  const {state,level}=p.created[0].opts;
  for(const name of ['listening','thinking','speaking','']){p.root.className=name;assert.equal(state(),name);}

  // 4. The device voice has no audio to measure: its word boundaries move the mouth.
  p.$('voice').value='browser';p.$('message').value='Hello';p.$('send').click();
  for(let i=0;i<20&&!p.utterance;i++)await p.tick();
  assert.ok(p.utterance,'a spoken reply started');
  p.root.className='speaking';p.w.speechSynthesis.speaking=true;
  p.utterance.onstart();p.utterance.onboundary();
  const onWord=level();p.clock+=500;const between=level();
  assert.ok(onWord>0.7&&between<0.25&&between>0,`a word opens the mouth, then it relaxes (${onWord.toFixed(2)} → ${between.toFixed(2)})`);
  p.w.speechSynthesis.speaking=false;p.root.className='';assert.equal(level(),0,'quiet when not speaking');

  // 5. Back to the orb: the character is released.
  await p.choose('orb');assert.equal(p.disposed,1);assert.equal(p.root.dataset.look,undefined);
  await p.choose('character');assert.equal(p.created.length,2,'drawn again when chosen again');
  p.save();p.close();

  // 6. Next visit: the remembered character, drawn at once when the code is ready.
  p=await page({preload:true});
  assert.equal(p.$('presence-look').value,'character');assert.equal(p.created.length,1);assert.equal(p.root.dataset.look,'character');p.close();

  // 7. A model of your own in /static/avatars/apex.glb is used instead of the suit.
  p=await page({preload:true,custom:true});
  assert.equal(p.created[0].opts.model,'/static/avatars/apex.glb');p.close();

  // 8. No WebGL: the orb stays and the page says why.
  p=await page({preload:true,webgl:false});
  assert.equal(p.root.dataset.look,undefined);assert.equal(p.$('presence-look').value,'orb');
  const option=p.$('presence-look').querySelector('option[value=character]');
  assert.ok(option.disabled&&/needs WebGL/.test(option.textContent));assert.match(p.$('presence-look').title,/no WebGL/);p.close();
  console.log('PASS: the orb by default; the character is remembered, waits for its code, follows the companion state, moves its mouth on the device voice, is released on Orb, uses your own model when there is one, and without WebGL the orb stays with a reason.');
})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1);});
