const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const origin='http://127.0.0.1:8792';
const token='world-browser-test-only';
const shots=process.env.APEX_WORLD_SCREENSHOTS;
if(shots)fs.mkdirSync(shots,{recursive:true});
const boot=`import sys
sys.path.insert(0,sys.argv[1])
import config
config.DASHBOARD_TOKEN='world-browser-test-only'
from dashboard.server import app
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8792)`;
let server;
async function start(){
 server=spawn(process.env.APEX_TEST_PYTHON||'python',['-c',boot,root],{stdio:['ignore','ignore','pipe']});
 await new Promise((resolve,reject)=>{
  let log='';const timer=setTimeout(()=>reject(Error('Server startup timeout: '+log)),15000);
  server.stderr.on('data',chunk=>{log+=chunk;if(log.includes('Uvicorn running')){clearTimeout(timer);resolve();}});
  server.once('exit',()=>{clearTimeout(timer);reject(Error('Server stopped: '+log));});
 });
}
async function stop(){if(server&&server.exitCode===null)await new Promise(resolve=>{server.once('exit',resolve);server.kill('SIGTERM');});}
const assert=require('node:assert/strict');
// Optional real-engine check. Requires Playwright, Chromium, Python dependencies
// and internet for Cesium/map assets. No real assistant requests are made.
(async()=>{
let b;
try{
await start();
b=await chromium.launch({...(process.env.APEX_CHROMIUM_PATH?{executablePath:process.env.APEX_CHROMIUM_PATH}:{}),args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const ctx=await b.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
 const cache=new Map();
 // Optional Node proxy transport uses the same TLS verification and runtime
 // proxy policy as fetch. Native Chromium network is the default.
 if(process.env.APEX_TEST_PROXY_FETCH==='1')await ctx.route('https://**/*',async route=>{
  const url=route.request().url();
  try{
   let data=cache.get(url);
   if(!data){const response=await fetch(url);data={status:response.status,headers:Object.fromEntries([...response.headers].filter(([key])=>!['content-encoding','content-length'].includes(key))),body:Buffer.from(await response.arrayBuffer())};cache.set(url,data);}
   await route.fulfill(data);
  }catch(e){console.log('Fetch failed',new URL(url).hostname,e.message);await route.abort();}
 });
 await ctx.addInitScript(t=>localStorage.setItem('apex_token',t),token);
 const page=await ctx.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')console.log('Console:',m.text().slice(0,500));});page.on('requestfailed',r=>console.log('Failed request:',r.url().split('?')[0],r.failure()?.errorText));
 await ctx.route('**/static/world-view.js*',route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(root,'dashboard/static/world-view.js'),'utf8').replace('viewer = new Cesium.Viewer','viewer = window.__viewer = new Cesium.Viewer')}));
 await page.goto(origin+'/world');await page.bringToFront();
 await page.locator('#world-reset:enabled').waitFor({timeout:60000});
 await page.waitForFunction(()=>window.__viewer.scene.globe.tilesLoaded,null,{timeout:60000});
 assert.equal(await page.locator('#world-loading').isVisible(),false);
 if(shots)await page.screenshot({path:path.join(shots,'world-view-desktop.png')});
 await page.getByRole('button',{name:'Byblos',exact:true}).click();
 await page.waitForFunction(()=>window.__viewer.camera.positionCartographic.height<100000,null,{timeout:10000});await page.waitForFunction(()=>window.__viewer.scene.globe.tilesLoaded,null,{timeout:60000});
 console.log('Selected',await page.locator('#world-selected-coords').innerText());
 if(shots)await page.screenshot({path:path.join(shots,'world-view-byblos.png')});
 await page.selectOption('#world-map','outline');
 await page.reload();
 await page.locator('#world-reset:enabled').waitFor({timeout:60000});
 await page.waitForFunction(()=>window.__viewer.scene.globe.tilesLoaded,null,{timeout:60000});
 assert.equal(await page.locator('#world-map').inputValue(),'outline');
 assert.equal(await page.locator('#world-selected-name').innerText(),'Byblos');
 console.log('Restored',await page.locator('#world-map').inputValue(),await page.locator('#world-selected-name').innerText());
 assert.deepEqual(errors,[]);console.log('Errors',JSON.stringify(errors));
 assert.equal(await page.locator('canvas').count(),1);
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.body.scrollWidth<=innerWidth),true);
 if(shots)await page.screenshot({path:path.join(shots,'world-view-mobile.png')});
 await ctx.route('**/api/**',route=>{const u=new URL(route.request().url());if(u.pathname.startsWith('/api/world/'))return route.continue();let data={};if(u.pathname==='/api/status')data={model:'test-model',tools_count:117,uptime_s:120};if(u.pathname==='/api/conversations')data={threads:[]};if(u.pathname==='/api/constellation')data={planets:[]};return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});});
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(origin+'/');
 await page.getByRole('link',{name:'Open World View'}).click();
 let iframe=page.frameLocator('iframe[title="Apex World View"]');
 await iframe.locator('#world-reset:enabled').waitFor({timeout:30000});
 console.log('Command panel opened',await page.locator('.world-dialog').evaluate(el=>el.open));
 await iframe.locator('#world-back').click();
 await page.waitForFunction(()=>!document.querySelector('.world-dialog').open);
 await page.locator('.world-dialog iframe').waitFor({state:'detached'});
 console.log('Panel unloaded',await page.locator('.world-dialog iframe').count());
 await page.getByRole('link',{name:'Open World View'}).click();
 iframe=page.frameLocator('iframe[title="Apex World View"]');
 await iframe.locator('#world-reset:enabled').waitFor({timeout:30000});
 console.log('Panel restored',await iframe.locator('#world-selected-name').innerText());
 await iframe.locator('#world-query').focus();await page.keyboard.press('Escape');
 await page.waitForFunction(()=>!document.querySelector('.world-dialog').open);
 await page.locator('.world-dialog iframe').waitFor({state:'detached'});
 assert.equal(await page.evaluate(()=>document.activeElement.id),'world-open');
 console.log('Escape closes and focus returns',await page.evaluate(()=>document.activeElement.id));
 console.log('PASS: real Earth renders; navigation, restoration, mobile fit, Command open/close, disposal and focus.');
}finally{if(b)await b.close();await stop();}
})().catch(e=>{console.error(e);process.exitCode=1;});
