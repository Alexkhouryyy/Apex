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
  assert.deepEqual(errors,[]);
  console.log('PASS: real WebGL, named save, component notes, server restart, restored view/camera, revision update, engineering status and mobile layout.');
}finally{if(browser)await browser.close();await stop();fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
