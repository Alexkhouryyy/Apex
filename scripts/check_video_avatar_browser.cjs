// Optional real-browser check of the video avatar: a real avatar server (the
// still engine, no GPU) on a free port, the real companion page in Chromium,
// real clips played in the browser's format. Needs Python with PyAV, FastAPI
// and uvicorn, and Playwright's Chromium (CHROMIUM_PATH, or Playwright's own).
//   node scripts/check_video_avatar_browser.cjs
const {chromium}=require('playwright'),http=require('http'),fs=require('fs'),os=require('os'),path=require('path');
const {spawn,execFileSync}=require('child_process'),assert=require('node:assert/strict');
const ROOT=path.join(__dirname,'..'),STATIC=path.join(ROOT,'dashboard','static');
const TYPES={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.wasm':'application/wasm'};
const PY=process.env.PYTHON||(process.platform==='win32'?'python':'python3');

function wav(seconds=3,rate=24000){                     // syllable-shaped buzz, like speech
  const n=rate*seconds,pcm=Buffer.alloc(n*2);
  for(let i=0;i<n;i++){const t=i/rate,env=Math.max(0,Math.sin(Math.PI*4*t))**2;pcm.writeInt16LE(Math.round(9000*env*Math.sin(2*Math.PI*150*t)),i*2);}
  const h=Buffer.alloc(44);h.write('RIFF',0);h.writeUInt32LE(36+pcm.length,4);h.write('WAVEfmt ',8);h.writeUInt32LE(16,16);h.writeUInt16LE(1,20);h.writeUInt16LE(1,22);
  h.writeUInt32LE(rate,24);h.writeUInt32LE(rate*2,28);h.writeUInt16LE(2,32);h.writeUInt16LE(16,34);h.write('data',36);h.writeUInt32LE(pcm.length,40);return Buffer.concat([h,pcm]);
}
const free=()=>new Promise(r=>{const s=http.createServer().listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});

(async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'apex-avatar-'));
  const idle=path.join(dir,'apex-idle.mp4');
  execFileSync(PY,['-c',`import sys;sys.path.insert(0,${JSON.stringify(ROOT)});import numpy as np;from scripts import avatar_server as a;open(${JSON.stringify(idle)},'wb').write(a.encode_video([np.full((240,180,3),(i*9)%255,np.uint8) for i in range(50)],25))`]);
  const port=await free();
  const avatar=spawn(PY,[path.join(ROOT,'scripts','avatar_server.py'),'--engine','still','--video',idle,'--port',String(port)],{stdio:'ignore'});
  const AV=`http://127.0.0.1:${port}`;
  for(let i=0;i<100;i++){try{if((await fetch(AV+'/health')).ok)break;}catch(_){}await new Promise(r=>setTimeout(r,200));}
  const site=http.createServer((req,res)=>{
    const file=path.join(STATIC,decodeURIComponent(new URL(req.url,'http://x').pathname.replace(/^\/static\//,'')));
    if(!file.startsWith(STATIC)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    res.writeHead(200,{'Content-Type':TYPES[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(res);
  }).listen(0,'127.0.0.1');
  await new Promise(r=>site.once('listening',r));
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,args:['--autoplay-policy=no-user-gesture-required']});
  try{
    for(const mode of ['works','server-fails']){
      const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
      await page.addInitScript(()=>{localStorage.setItem('apex_token','t');localStorage.setItem('apex.presence.look','video');});
      let clips=0;
      await page.route('**/api/**',async route=>{const u=new URL(route.request().url()),p=u.pathname;
        if(p==='/api/status')return route.fulfill({json:{agent_ready:true,voices:{openai:true}}});
        if(p==='/api/avatar/status')return route.fulfill({json:{available:true,engine:'still',fps:25}});
        if(p==='/api/avatar/idle'||p==='/api/avatar/lipsync'){                    // what Apex's pass-through routes do
          if(p.endsWith('lipsync')){clips++;if(mode==='server-fails')return route.fulfill({status:502,json:{detail:'x'}});}
          const res=await fetch(AV+p.replace('/api/avatar','')+u.search,p.endsWith('lipsync')?{method:'POST',body:route.request().postDataBuffer()}:{});
          return route.fulfill({body:Buffer.from(await res.arrayBuffer()),headers:{'Content-Type':res.headers.get('content-type')}});}
        if(p==='/api/companion/chat')return route.fulfill({body:[{type:'start',thread_id:2},{type:'token',text:'Hello, I am Apex.'},{type:'done',text:'Hello, I am Apex.'}].map(v=>JSON.stringify(v)+'\n').join('')});
        if(p==='/api/speak')return route.fulfill({body:wav(),headers:{'Content-Type':'audio/wav'}});
        return route.fulfill({json:{}});});
      await page.goto(`http://127.0.0.1:${site.address().port}/static/companion.html`);
      await page.waitForFunction(()=>document.getElementById('companion').dataset.look==='video',null,{timeout:20000});
      await page.waitForTimeout(600);
      const idleState=await page.evaluate(()=>{const v=document.querySelector('.avatar-idle');return {ready:v.readyState,t:v.currentTime,muted:v.muted,paused:v.paused};});
      assert.ok(idleState.ready>=2&&idleState.t>0&&idleState.muted&&!idleState.paused,`the idle loop plays silently (${JSON.stringify(idleState)})`);
      await page.evaluate(()=>{const s=document.getElementById('voice');s.value='openai';s.dispatchEvent(new Event('change'));
        document.getElementById('spoken').checked=true;document.getElementById('message').value='Hi';document.getElementById('send').click();});
      let talked=false,spoke=false;
      for(let i=0;i<120;i++){const st=await page.evaluate(()=>({talking:document.getElementById('avatar').classList.contains('avatar-talking'),
          speaking:document.getElementById('companion').classList.contains('speaking'),t:document.querySelector('.avatar-speaking')?.currentTime||0}));
        if(st.talking&&st.t>0.3)talked=true; if(st.speaking)spoke=true; if(spoke&&!st.speaking&&i>10)break; await page.waitForTimeout(150);}
      assert.ok(spoke&&clips===1,`${mode}: the reply was spoken (${spoke}, ${clips} clip requests)`);
      assert.equal(talked,mode==='works',`${mode}: the face ${mode==='works'?'must':'must not'} play a clip`);
      assert.equal(await page.evaluate(()=>document.getElementById('avatar').classList.contains('avatar-talking')),false,'back to the idle loop');
      assert.deepEqual(errors,[]);
      await page.close();
    }
    console.log('PASS: in real Chromium the idle loop plays silently, a spoken reply plays as a real clip from a real avatar server and returns to the loop, and when the server cannot make the clip the reply is still spoken.');
  }finally{await browser.close();site.close();avatar.kill();fs.rmSync(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
