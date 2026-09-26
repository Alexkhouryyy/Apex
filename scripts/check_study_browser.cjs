// Optional real WebGL + server regression. Requires Playwright/Chromium and
// Apex's Python dependencies. Set APEX_TEST_PYTHON and APEX_CHROMIUM_PATH when
// those runtimes are outside PATH. All data is isolated in a temporary folder.
const {chromium}=require('playwright');
const {spawn}=require('child_process'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'apex-study-check-'));
const token='study-browser-test-only',origin='http://127.0.0.1:8767';
const boot=`import sys
sys.path.insert(0,sys.argv[1])
import config
config.DASHBOARD_TOKEN='study-browser-test-only'
from agent import longterm
longterm.DB_PATH=sys.argv[2]
longterm.init_db()
from dashboard.server import app
from fastapi import Request
from agent import handtrack
class FakeTracker:
 def __init__(self):self.hands=[];self.seq=0
 def study_sample(self):
  self.seq+=1
  return dict(sequence=self.seq,age_ms=0,hands=self.hands)
fake=FakeTracker()
handtrack.active_tracker=lambda:fake
@app.post('/api/test/hands')
async def test_hands(request:Request):
 fake.hands=(await request.json())['hands']
 return {'ok':True}
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8767)`;
let server,browser;
async function start(){
  server=spawn(process.env.APEX_TEST_PYTHON||'python',['-c',boot,root,path.join(temp,'study.sqlite')],{stdio:['ignore','ignore','pipe']});
  await new Promise((resolve,reject)=>{let log='';const timeout=setTimeout(()=>reject(Error('Server startup timeout: '+log)),15000);
    server.stderr.on('data',c=>{log+=c;if(log.includes('Uvicorn running')){clearTimeout(timeout);resolve();}});
    server.once('exit',()=>{clearTimeout(timeout);reject(Error('Server stopped: '+log));});
  });
}
async function stop(){if(!server||server.exitCode!==null)return;await new Promise(resolve=>{server.once('exit',resolve);server.kill('SIGTERM');});server=null;}
(async()=>{try{
  await start();browser=await chromium.launch({headless:true,...(process.env.APEX_CHROMIUM_PATH?{executablePath:process.env.APEX_CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto(origin+'/study?token='+token);
  await page.waitForFunction(()=>document.querySelectorAll('.component').length===14);
  await page.getByRole('button',{name:'Take apart',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#separation').value==='100');
  await page.locator('[data-part="commutator"]').click();
  await page.waitForFunction(()=>document.querySelector('#part-name').textContent==='Segmented commutator');
  await page.locator('#study-notes').fill('Why is the commutator segmented? Compare this illustration with a real winding diagram.');
  await page.getByRole('button',{name:'Isolate',exact:true}).click();
  await page.getByRole('button',{name:'Exit isolation',exact:true}).waitFor();
  await page.getByRole('button',{name:'Exit isolation',exact:true}).click();
  await page.waitForTimeout(800);
  await page.getByRole('button',{name:'Projects',exact:true}).click();
  await page.locator('#project-name').fill('Commutator study');
  await page.getByRole('button',{name:'Save study',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#project-message').textContent.includes('Saved version 1'));
  await page.waitForTimeout(500);
  assert.match(await page.locator('#project-status').innerText(),/ · Saved$/);
  const headers={Authorization:'Bearer '+token};
  const saved=(await (await page.request.get(origin+'/api/study/projects',{headers})).json()).projects[0];
  const original=await (await page.request.post(origin+'/api/study/projects/'+saved.id+'/open',{headers})).json();
  assert.equal(original.workspace.notes.commutator,await page.locator('#study-notes').inputValue());
  // Restart the real process: fresh memory, same test database.
  await stop();await start();
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.component').length===14);
  // The saved project in the URL restores the notebook automatically.
  await page.waitForFunction(()=>document.querySelector('#part-name').textContent==='Segmented commutator');
  assert.equal(await page.locator('#study-notes').inputValue(),original.workspace.notes.commutator);
  assert.equal(await page.locator('#separation').inputValue(),'100');
  await page.waitForTimeout(800);
  assert.match(await page.locator('#project-status').innerText(),/ · Saved$/);
  // Re-saving the restored view must preserve its camera and rotor angle.
  await page.getByRole('button',{name:'Projects',exact:true}).click();
  await page.getByRole('button',{name:'Save study',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#project-message').textContent.includes('Saved version 2'));
  const reopened=await (await page.request.post(origin+'/api/study/projects/'+saved.id+'/open',{headers})).json();
  assert.deepEqual(reopened.workspace.camera,original.workspace.camera);
  assert.equal(reopened.workspace.rotor_angle,original.workspace.rotor_angle);
  await page.getByRole('button',{name:'Close projects',exact:true}).click();
  if(process.env.APEX_STUDY_SCREENSHOT)await page.screenshot({path:process.env.APEX_STUDY_SCREENSHOT});
  await page.getByText('Engineering readiness',{exact:true}).click();
  assert.match(await page.locator('#validation-list').innerText(),/No independent engineering review/);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.getByRole('button',{name:'Projects',exact:true}).click();
  assert.equal(await page.evaluate(()=>document.querySelector('#projects').getBoundingClientRect().width<=innerWidth),true);
  await page.getByRole('button',{name:'Close projects',exact:true}).click();
  await page.setViewportSize({width:1600,height:1000});
  await page.goto(origin+'/study?model=openmotor-125&token='+token);
  await page.waitForFunction(()=>document.querySelectorAll('.component').length===135,{},{timeout:30000});
  await page.locator('[data-part="node-2"]').click();
  await page.waitForFunction(()=>document.querySelector('#part-name').textContent==='Core:1');
  assert.match(await page.locator('#part-note').innerText(),/mm/);
  await page.getByRole('button',{name:'Isolate',exact:true}).click();
  await page.getByRole('button',{name:'Exit isolation',exact:true}).waitFor();
  await page.locator('#interaction').selectOption('move');
  // Search the rendered canvas for a pickable point, then perform a real drag.
  const canvas=await page.locator('#model').boundingBox();
  const sid=new URL(page.url()).searchParams.get('session');
  const stateUrl=origin+'/api/study/session/'+sid;
  let moved=false, point=null;
  for(const [x,y] of [[.5,.5],[.45,.5],[.55,.5],[.5,.4],[.5,.6]]){
    await page.mouse.move(canvas.x+canvas.width*x,canvas.y+canvas.height*y);
    await page.mouse.down();await page.mouse.move(canvas.x+canvas.width*x+55,canvas.y+canvas.height*y+15,{steps:8});await page.mouse.up();
    await page.waitForTimeout(200);
    const state=await (await page.request.get(stateUrl,{headers})).json();
    if(state.transforms['node-2']){moved=true;point={x,y};break;}
  }
  assert(moved,'a rendered CAD component must accept mouse movement');
  await page.getByRole('button',{name:'Undo study action',exact:true}).click();
  await page.waitForTimeout(200);
  assert.deepEqual((await (await page.request.get(stateUrl,{headers})).json()).transforms,{});
  // Synthetic detector frames through the real ownership endpoint and viewer.
  // This verifies plumbing, not physical-camera gesture quality.
  const feed=async(pinched,x=point.x)=>{await page.request.post(origin+'/api/test/hands',{headers,data:{hands:[{id:7,x,y:point.y,pinched}]}});};
  await page.getByRole('button',{name:'Hands off',exact:true}).click();
  await page.getByRole('button',{name:'Hands on',exact:true}).waitFor();
  const before=(await (await page.request.get(stateUrl,{headers})).json()).revision;
  await feed(false);await page.waitForFunction(()=>document.querySelector('#hand-status').textContent.includes('Ready'),{},{timeout:5000});await feed(true);await page.waitForFunction(()=>document.querySelector('#hand-status').textContent.includes('Holding'));await page.waitForTimeout(120);
  await feed(true,point.x+.06);await page.waitForTimeout(200);await feed(false,point.x+.06);await page.waitForTimeout(250);
  const after=await (await page.request.get(stateUrl,{headers})).json();
  assert.equal(after.revision,before+1,'a hand release must commit exactly once');
  assert(after.transforms['node-2'],'hand movement must reach the actual assembly session');
  await page.getByRole('button',{name:'Hands on',exact:true}).click();
  await page.getByRole('button',{name:'Undo study action',exact:true}).click();
  await page.getByRole('button',{name:'Exit isolation',exact:true}).click();
  await page.getByRole('button',{name:'Take apart',exact:true}).click();
  await page.waitForTimeout(800);
  if(process.env.APEX_CAD_SCREENSHOT)await page.screenshot({path:process.env.APEX_CAD_SCREENSHOT});
  assert.deepEqual(errors,[]);
  console.log('PASS: real WebGL, saved notes/views across restart, mobile layout, 135 CAD components, mouse drag/undo and synthetic hand frames through the real input endpoint.');
}finally{if(browser)await browser.close();await stop();fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
