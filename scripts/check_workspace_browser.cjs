// Optional real server/browser check. Uses the same runtime overrides as check_study_browser.cjs.
const {chromium}=require('playwright');
const {spawn}=require('child_process'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'apex-workspace-check-'));
const token='workspace-browser-test-only',origin='http://127.0.0.1:8768';
const boot=`import sys
sys.path.insert(0,sys.argv[1])
import config
config.DASHBOARD_TOKEN='workspace-browser-test-only'
from agent import longterm, board, handtrack
longterm.DB_PATH=sys.argv[2]
longterm.init_db()
board.init_db()
handtrack.active_tracker=lambda:None
from dashboard.server import app
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8768)`;
let server,browser;
async function start(){
  server=spawn(process.env.APEX_TEST_PYTHON||'python',['-c',boot,root,path.join(temp,'board.sqlite')],{stdio:['ignore','ignore','pipe']});
  await new Promise((resolve,reject)=>{let log='';const timeout=setTimeout(()=>reject(Error('Server startup timeout: '+log)),15000);
    server.stderr.on('data',c=>{log+=c;if(log.includes('Uvicorn running')){clearTimeout(timeout);resolve();}});
    server.once('exit',()=>{clearTimeout(timeout);reject(Error('Server stopped: '+log));});
  });
}
async function stop(){if(!server||server.exitCode!==null)return;await new Promise(resolve=>{server.once('exit',resolve);server.kill('SIGTERM');});server=null;}
(async()=>{try{
  await start();browser=await chromium.launch({headless:true,...(process.env.APEX_CHROMIUM_PATH?{executablePath:process.env.APEX_CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  page.on('request',r=>{if(r.url().startsWith('https://example.com'))external.push(r.url());});
  await page.goto(origin+'/board?token='+token);
  await page.waitForFunction(()=>document.querySelector('#hint-body').textContent!=='Connecting to your workspace…');
  const submit=async()=>{
    const response=page.waitForResponse(r=>r.url()===origin+'/api/board/workspace'&&r.request().method()==='POST');
    await page.locator('#save-note').click();return response;
  };
  await page.locator('#add-note').click();await page.locator('#note-title').fill('Apex work');await page.locator('#note-body').fill('Plan the next task');
  const created=await (await submit()).json();assert(created.card);
  await page.locator('#note-dialog').waitFor({state:'hidden'});
  await page.locator('#edit-content').click();await page.locator('#note-body').fill('Updated task');
  const edited=await (await submit()).json();
  await page.waitForFunction(()=>document.querySelector('.card p')?.textContent==='Updated task');
  await page.locator('#undo-board').click();await page.waitForFunction(()=>document.querySelector('.card p')?.textContent==='Plan the next task');
  await page.locator('#redo-board').click();await page.waitForFunction(()=>document.querySelector('.card p')?.textContent==='Updated task');
  await page.locator('#edit-content').click();await page.locator('#note-body').fill('My unsaved draft');
  const headers={Authorization:'Bearer '+token};
  const response=await page.request.post(origin+'/api/board/workspace',{headers,data:{action:'edit_content',kind:'card',id:edited.card.id,expected_revision:edited.card.content_revision,title:'Apex work',body:'Other window'}});
  assert.equal(response.status(),200);
  assert.equal((await submit()).status(),409);assert.equal(await page.locator('#note-body').inputValue(),'My unsaved draft');
  await page.locator('#note-copy').click();await page.locator('#note-dialog').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelectorAll('.card').length===2);
  await page.locator('#add-link').click();await page.locator('#note-title').fill('Project reference');await page.locator('#note-url').fill('https://example.com/reference');
  assert.equal((await submit()).status(),200);
  await page.locator('#note-dialog').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('#open-link').getAttribute('href')==='https://example.com/reference');
  assert.equal(await page.locator('#open-link').getAttribute('rel'),'noopener noreferrer');
  assert.equal(external.length,0,'saving/selecting a link does not fetch it');
  await stop();await start();await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll('.card').length===3);
  const text=await page.locator('#stage').innerText();
  assert.match(text,/Other window/);assert.match(text,/My unsaved draft/);assert.match(text,/Project reference/);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#dock-note').click();await page.locator('#note-kind').selectOption('link');
  assert(await page.locator('#note-url').isVisible());
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('#close-note').click();
  assert.deepEqual(errors,[]);
  console.log('PASS: real workspace note edits, undo/redo, concurrent edit recovery, copy, safe links, restart persistence and mobile creation.');
}finally{if(browser)await browser.close();await stop();fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
