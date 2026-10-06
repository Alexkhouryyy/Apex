// Optional real-browser check of the live face: the vendored Simli bundle loads
// and builds a client, and when Simli can't be reached (no network, no key, a
// bad token) the companion goes back to the orb with the reason and keeps
// speaking as plain audio. Needs Playwright's Chromium (CHROMIUM_PATH, or its own).
//   node scripts/check_live_avatar_browser.cjs
const {chromium}=require('playwright'),http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const STATIC=path.join(__dirname,'..','dashboard','static');
const TYPES={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'};
(async()=>{
  const site=http.createServer((req,res)=>{
    const file=path.join(STATIC,decodeURIComponent(new URL(req.url,'http://x').pathname.replace(/^\/static\//,'')));
    if(!file.startsWith(STATIC)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    res.writeHead(200,{'Content-Type':TYPES[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(res);
  }).listen(0,'127.0.0.1');
  await new Promise(r=>site.once('listening',r));
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,args:['--autoplay-policy=no-user-gesture-required']});
  try{
    const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.addInitScript(()=>{localStorage.setItem('apex_token','t');localStorage.setItem('apex.presence.look','live');});
    // Simli itself is out of reach here (as with no network): every request to it fails.
    await page.route(/simli\.ai|livekit/,r=>r.abort());
    await page.route('**/api/**',r=>{const p=new URL(r.request().url()).pathname;
      if(p==='/api/status')return r.fulfill({json:{agent_ready:true}});
      if(p==='/api/avatar/live/session')return r.fulfill({json:{available:true,session_token:'test-token',ice_servers:[{urls:['stun:stun.l.google.com:19302']}]}});
      return r.fulfill({json:{}});});
    await page.goto(`http://127.0.0.1:${site.address().port}/static/companion.html`);
    // The bundle really loads and builds a client.
    await page.waitForFunction(()=>window.ApexSimli,null,{timeout:20000});
    const built=await page.evaluate(()=>{const c=new window.ApexSimli.SimliClient('t',document.createElement('video'),document.createElement('audio'),[{urls:['stun:stun.l.google.com:19302']}],window.ApexSimli.LogLevel.ERROR,'livekit');
      return ['start','stop','sendAudioData','ClearBuffer'].every(k=>typeof c[k]==='function');});
    assert.ok(built,'SimliClient has start, stop, sendAudioData and ClearBuffer');
    // Unreachable: back to the orb, with the reason on the setting.
    await page.waitForFunction(()=>document.getElementById('presence-look').value==='orb'&&document.getElementById('presence-look').title,null,{timeout:60000});
    const look=await page.evaluate(()=>({look:document.getElementById('companion').dataset.look||null,why:document.getElementById('presence-look').title,
      option:document.querySelector('#presence-look option[value=live]').textContent}));
    assert.equal(look.look,null);assert.match(look.option,/not available/);assert.ok(look.why.length>5,look.why);
    console.log(`PASS: the Simli bundle loads and builds a client in real Chromium, and when Simli can't be reached the companion goes back to the orb ("${look.why.slice(0,60)}").`);
  }finally{await browser.close();site.close();}
})().catch(e=>{console.error(e);process.exit(1);});
