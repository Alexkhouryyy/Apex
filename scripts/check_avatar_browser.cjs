// Optional real-browser check of Apex's character (avatar.js) in the companion.
// Requires Playwright's Chromium (CHROMIUM_PATH, or Playwright's own). Serves
// dashboard/static itself, fakes the Apex API, and plays a generated
// speech-like sound (syllable-shaped bursts), so it needs no network or keys.
//   node scripts/check_avatar_browser.cjs
const {chromium}=require('playwright'),http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const STATIC=path.join(__dirname,'..','dashboard','static');
const TYPES={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.wasm':'application/wasm'};

function voiceLike(rate=24000,seconds=4){            // 4 syllables a second, a vowel-ish buzz in each
  const n=rate*seconds,pcm=new Int16Array(n);
  for(let i=0;i<n;i++){const t=i/rate,env=Math.max(0,Math.sin(Math.PI*4*t))**2;
    pcm[i]=Math.round(9000*env*(Math.sin(2*Math.PI*140*t)+0.5*Math.sin(2*Math.PI*280*t)+0.3*Math.sin(2*Math.PI*720*t))/1.8);}
  return Buffer.from(pcm.buffer);
}
function wav(pcm,rate=24000){
  const h=Buffer.alloc(44);h.write('RIFF',0);h.writeUInt32LE(36+pcm.length,4);h.write('WAVEfmt ',8);h.writeUInt32LE(16,16);
  h.writeUInt16LE(1,20);h.writeUInt16LE(1,22);h.writeUInt32LE(rate,24);h.writeUInt32LE(rate*2,28);h.writeUInt16LE(2,32);
  h.writeUInt16LE(16,34);h.write('data',36);h.writeUInt32LE(pcm.length,40);return Buffer.concat([h,pcm]);
}

(async()=>{
  const server=http.createServer((req,res)=>{
    const file=path.join(STATIC,decodeURIComponent(new URL(req.url,'http://x').pathname.replace(/^\/static\//,'')));
    if(!file.startsWith(STATIC)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    res.writeHead(200,{'Content-Type':TYPES[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(res);
  }).listen(0,'127.0.0.1');
  await new Promise(r=>server.once('listening',r));
  const origin=`http://127.0.0.1:${server.address().port}`,pcm=voiceLike();
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,
    args:['--use-angle=swiftshader','--enable-unsafe-swiftshader','--autoplay-policy=no-user-gesture-required']});
  try{
    for(const voice of ['openai','voicebox']){        // a recorded clip, and Celine-style streamed PCM
      const page=await browser.newPage({viewport:{width:900,height:900}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
      await page.addInitScript(()=>{localStorage.setItem('apex_token','t');localStorage.setItem('apex.presence.look','character');});
      await page.route('**/api/**',route=>{const u=new URL(route.request().url()).pathname;
        if(u==='/api/status')return route.fulfill({json:{agent_ready:true,voices:{openai:true}}});
        if(u==='/api/companion/chat')return route.fulfill({body:[{type:'start',thread_id:2},{type:'token',text:'Hello there, I am Apex.'},{type:'done',text:'Hello there, I am Apex.'}].map(v=>JSON.stringify(v)+'\n').join('')});
        if(u==='/api/speak')return route.fulfill({body:wav(pcm),headers:{'Content-Type':'audio/wav'}});
        if(u==='/api/speak/stream')return route.fulfill({body:pcm,headers:{'Content-Type':'application/octet-stream','X-Sample-Rate':'24000'}});
        return route.fulfill({json:{}});});
      await page.goto(origin+'/static/companion.html');
      await page.waitForFunction(()=>document.getElementById('companion').dataset.look==='character',null,{timeout:20000});
      const drawn=await page.evaluate(()=>{const c=document.querySelector('#avatar canvas');return c?c.width*c.height:0;});
      assert.ok(drawn>0,'the character has a canvas');
      await page.evaluate(v=>{const s=document.getElementById('voice');s.value=v;s.dispatchEvent(new Event('change'));
        document.getElementById('spoken').checked=true;const m=document.getElementById('message');m.value='Hi';document.getElementById('send').click();},voice);
      await page.waitForFunction(()=>document.getElementById('companion').classList.contains('speaking'),null,{timeout:20000});
      const levels=[];for(let i=0;i<40;i++){levels.push(await page.evaluate(()=>window.ApexVoice.level()));await page.waitForTimeout(25);}
      const open=levels.filter(l=>l>0.2).length,shut=levels.filter(l=>l<0.1).length;
      assert.ok(open>=5&&shut>=3,`${voice}: the mouth must open on syllables and close between them (open ${open}, shut ${shut} of 40)`);
      assert.deepEqual(errors,[],`${voice}: page errors`);
      await page.close();
    }
    console.log('PASS: in real Chromium the character draws, and its mouth opens on syllables and closes between them for both a recorded clip and streamed voice.');
  }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exit(1);});
