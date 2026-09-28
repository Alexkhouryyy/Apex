// Optional real WebGL + server check of the Phase 2b hand gestures, with
// synthetic detector frames through the real hand-ownership endpoint: two-hand
// pull-apart and push-together, spinning the view from empty space with
// momentum, and the pointed part reaching Céline's context. It checks the
// plumbing and behaviour, not how it feels with a physical camera.
// Requires Playwright/Chromium and Apex's Python dependencies
// (APEX_TEST_PYTHON / APEX_CHROMIUM_PATH as in check_study_browser.cjs).
const {chromium}=require('playwright');
const {spawn}=require('child_process'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'apex-gesture-check-'));
const token='gesture-browser-test-only',origin='http://127.0.0.1:8769',headers={Authorization:'Bearer '+token};
const boot=`import sys
sys.path.insert(0,sys.argv[1])
import config
config.DASHBOARD_TOKEN='gesture-browser-test-only'
from agent import longterm, handtrack, assembly
longterm.DB_PATH=sys.argv[2]
longterm.init_db()
from dashboard.server import app
from fastapi import Request
class FakeTracker:
 def __init__(self):self.hands=[];self.seq=0
 def latest_cursors(self):return []
 def latest_hands(self):return []
 def latest_jpeg(self):return None
 def study_sample(self):
  self.seq+=1
  return dict(sequence=self.seq,age_ms=0,hands=self.hands)
fake=FakeTracker()
handtrack.active_tracker=lambda:fake
@app.post('/api/test/hands')
async def test_hands(request:Request):
 fake.hands=(await request.json())['hands']
 return {'ok':True}
@app.get('/api/test/pointed/{sid}')
async def test_pointed(sid:str):
 return {'pointed':assembly.pointed(sid)}
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8769)`;
let server,browser;
(async()=>{try{
  server=spawn(process.env.APEX_TEST_PYTHON||'python',['-c',boot,root,path.join(temp,'db.sqlite')],{stdio:['ignore','ignore','pipe']});
  await new Promise((resolve,reject)=>{let log='';const t=setTimeout(()=>reject(Error('Server startup timeout: '+log)),15000);
    server.stderr.on('data',c=>{log+=c;if(log.includes('Uvicorn running')){clearTimeout(t);resolve();}});server.once('exit',()=>{clearTimeout(t);reject(Error('Server stopped: '+log));});});
  browser=await chromium.launch({headless:true,...(process.env.APEX_CHROMIUM_PATH?{executablePath:process.env.APEX_CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('apex.look','normal'));
  await page.goto(origin+'/study?model=jet-engine&token='+token);
  await page.waitForFunction(()=>document.querySelectorAll('.component').length===20);
  const sid=new URL(page.url()).searchParams.get('session'),stateUrl=origin+'/api/study/session/'+sid;
  const state=async()=>(await (await page.request.get(stateUrl,{headers})).json());
  const hand=(id,x,y,pinched,extra={})=>({id,x,y,pinched,ratio:pinched?.2:.8,threshold:.35,...extra});
  const send=async hands=>{await page.request.post(origin+'/api/test/hands',{headers,data:{hands}});await page.waitForTimeout(140);};
  await page.getByRole('button',{name:'Enable hands',exact:true}).click();
  await page.getByRole('button',{name:'Pause hands',exact:true}).waitFor();

  // 1. Two hands: pinch together, pull apart, let go -> the model stays apart.
  await send([hand(1,.42,.5,false),hand(2,.58,.5,false)]);
  await send([hand(1,.42,.5,true),hand(2,.58,.5,true)]);
  await page.waitForFunction(()=>/Two hands/.test(document.querySelector('#hand-status').textContent));
  for(const [a,b] of [[.36,.64],[.28,.72],[.2,.8],[.12,.88]])await send([hand(1,a,.5,true),hand(2,b,.5,true)]);
  await page.waitForFunction(()=>document.querySelector('#separation').value==='100');
  assert.equal((await state()).explosion,0,'nothing is saved while the hands are still pulling');
  await send([hand(1,.12,.5,false),hand(2,.88,.5,false)]);await send([hand(1,.12,.5,false),hand(2,.88,.5,false)]);
  await page.waitForFunction(async url=>(await (await fetch(url,{headers:{Authorization:'Bearer gesture-browser-test-only'}})).json()).explosion===1,stateUrl);

  // 2. Push together -> reassembled.
  await send([hand(1,.2,.5,false),hand(2,.8,.5,false)]);
  await send([hand(1,.2,.5,true),hand(2,.8,.5,true)]);
  for(const [a,b] of [[.3,.7],[.4,.6],[.46,.54]])await send([hand(1,a,.5,true),hand(2,b,.5,true)]);
  await send([hand(1,.46,.5,false),hand(2,.54,.5,false)]);await send([hand(1,.46,.5,false),hand(2,.54,.5,false)]);
  await page.waitForFunction(async url=>(await (await fetch(url,{headers:{Authorization:'Bearer gesture-browser-test-only'}})).json()).explosion===0,stateUrl);

  // 3. A fist during a pull cancels: nothing changes.
  const revision=(await state()).revision;
  await send([hand(1,.42,.5,false),hand(2,.58,.5,false)]);
  await send([hand(1,.42,.5,true),hand(2,.58,.5,true)]);
  await send([hand(1,.25,.5,true),hand(2,.75,.5,true)]);
  await send([hand(1,.25,.5,true),hand(2,.75,.5,true,{fist:true})]);
  await page.waitForFunction(()=>/Closed fist · separation cancelled/.test(document.querySelector('#status').textContent));
  await send([]);await page.waitForTimeout(300);
  assert.equal((await state()).revision,revision,'a cancelled pull writes nothing');

  // 4. Empty space: hover, pinch, drag fast, let go -> the view keeps spinning.
  const empty=[.06,.1];
  for(let i=0;i<3;i++)await send([hand(3,empty[0],empty[1],false)]);
  await page.waitForFunction(()=>/empty space/.test(document.querySelector('#hand-status').textContent));
  await send([hand(3,empty[0],empty[1],true)]);
  // A real flick is continuous: frames every ~30 ms, still moving when the fingers open.
  const quick=async hands=>{await page.request.post(origin+'/api/test/hands',{headers,data:{hands}});await page.waitForTimeout(30);};
  let x=empty[0];
  for(let i=0;i<14;i++)await quick([hand(3,x+=.02,empty[1],true)]);
  for(let i=0;i<3;i++)await quick([hand(3,x+=.02,empty[1],false)]);
  await page.waitForFunction(()=>/Spinning/.test(document.querySelector('#status').textContent));
  await send([]);
  const view=page.locator('#model');
  const a=await view.screenshot();await page.waitForTimeout(400);const b=await view.screenshot();
  assert.notDeepEqual(a,b,'the view is still turning after the hand let go');
  assert.equal((await state()).revision,revision,'spinning the view changes no component');

  // 5. Pointing: an open hand resting on a part is remembered for Céline.
  let found=null;
  for(const [x,y] of [[.5,.5],[.45,.5],[.55,.5],[.5,.45],[.5,.55]]){
    for(let i=0;i<4;i++)await send([hand(4,x,y,false)]);
    await page.waitForTimeout(200);
    found=(await (await page.request.get(origin+'/api/test/pointed/'+sid,{headers})).json()).pointed;
    if(found)break;
  }
  assert.ok(found&&found.name&&found.seconds_ago<5,'the part under an open hand reaches the study context');
  // 6. Real hands pinch one after the other: the first grabs a part on its own,
  //    the second joins 150 ms later. The pull must take over and put the part back.
  const before=(await state());
  for(let i=0;i<4;i++)await send([hand(5,found?.x??.5,.5,false)]);
  let target=null;
  for(const [x,y] of [[.5,.5],[.45,.5],[.55,.5],[.5,.45],[.5,.55]]){
    for(let i=0;i<4;i++)await send([hand(5,x,y,false),hand(6,.85,.5,false)]);
    if(/Ready · pinch/.test(await page.locator('#hand-status').textContent())){target=[x,y];break;}
  }
  assert.ok(target,'a part becomes ready under the first hand');
  await send([hand(5,target[0],target[1],true),hand(6,.85,.5,false)]);
  await page.waitForFunction(()=>/Holding/.test(document.querySelector('#hand-status').textContent));
  await page.waitForTimeout(10);
  await send([hand(5,target[0],target[1],true),hand(6,.85,.5,true)]);
  await page.waitForFunction(()=>/Two hands/.test(document.querySelector('#hand-status').textContent));
  for(const [a,b] of [[target[0]-.1,.9],[target[0]-.25,.95],[.05,.95]])await send([hand(5,a,target[1],true),hand(6,b,.5,true)]);
  await send([hand(5,.05,target[1],false),hand(6,.95,.5,false)]);await send([hand(5,.05,target[1],false),hand(6,.95,.5,false)]);
  await page.waitForFunction(async url=>(await (await fetch(url,{headers:{Authorization:'Bearer gesture-browser-test-only'}})).json()).explosion>0.5,stateUrl);
  const after=await state();
  assert.deepEqual(after.transforms,before.transforms,'the handed-over one-hand grab moved nothing');
  assert.deepEqual(errors,[]);
  console.log(`PASS: two-hand pull-apart saves only on release (0 → 100%), push-together reassembles, a fist cancels without writing, `
    +`a flick on empty space keeps the view spinning without touching components, and pointing at “${found.name}” reaches Céline's context; a pull that starts as a one-hand grab (second hand 150 ms later) takes over and moves no part.`);
}finally{await browser?.close();if(server&&server.exitCode===null)server.kill('SIGTERM');fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exit(1);});
