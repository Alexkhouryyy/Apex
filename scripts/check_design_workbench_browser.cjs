// A disposable real backend, real GLB/3MF files and real browser controls.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'apex-design-browser-'));
const artifacts=process.env.APEX_DESIGN_ARTIFACTS||path.join(scratch,'artifacts');fs.mkdirSync(artifacts,{recursive:true});
const token='design-browser-'+require('node:crypto').randomUUID();
const python=`import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.run_tests import test_environment
folder=Path(sys.argv[2]);clean=test_environment(folder,dict(os.environ))
for k in set(os.environ)-set(clean): os.environ.pop(k,None)
os.environ.update(clean)
import config
config.DASHBOARD_TOKEN=sys.argv[4]
from agent import props,board,longterm
folder.joinpath('props').mkdir()
props.props_root=lambda:folder/'props'
longterm.init_db();board.init_db()
b=board.Board();b.persist=False;b.hands_enabled=False;board._board=b
from dashboard.server import app
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[3]),log_level='warning')`;
const port=()=>new Promise((resolve,reject)=>{const server=net.createServer();server.on('error',reject);server.listen(0,'127.0.0.1',()=>{const n=server.address().port;server.close(()=>resolve(n));});});
let child,browser,page,logs='';
(async()=>{
 const p=await port(),url='http://127.0.0.1:'+p;
 child=spawn(process.env.APEX_TEST_PYTHON||'python',['-u','-c',python,root,scratch,String(p),token],{cwd:root,stdio:['ignore','pipe','pipe']});
 child.stdout.on('data',d=>{logs=(logs+d).slice(-12000);});child.stderr.on('data',d=>{logs=(logs+d).slice(-12000);});
 const deadline=Date.now()+45000;let ready=false;
 while(Date.now()<deadline){if(child.exitCode!==null)throw Error('Preview server exited: '+logs);try{if((await fetch(url+'/health')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
 assert.ok(ready,'Preview server did not start: '+logs);
 browser=await chromium.launch({headless:true,...(process.env.APEX_BROWSER_EXECUTABLE?{executablePath:process.env.APEX_BROWSER_EXECUTABLE}:{}),args:['--enable-unsafe-swiftshader','--use-angle=swiftshader']});
 page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});page.setDefaultTimeout(45000);const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(t=>localStorage.setItem('apex_token',t),token);
 await page.goto(url+'/board?design=1');
 await page.waitForFunction(()=>document.getElementById('status').textContent.toLowerCase().includes('connected'));
 await page.click('#dw-new');await page.fill('#dw-create-title','Measured enclosure');
 const created=page.waitForResponse(r=>r.url()===url+'/api/board/design'&&r.request().method()==='POST');
 await page.click('#dw-create-go');let design=await(await created).json();
 await page.waitForSelector('#dw-edit:not([hidden])');
 assert.equal(await page.locator('.dw-component').count(),3);
 await page.click('#dw-frame');await page.waitForTimeout(300);
 await page.screenshot({path:path.join(artifacts,'design-workbench.png')});
 await page.getByRole('button',{name:/^Lid/}).click();
 await page.waitForFunction(()=>document.querySelector('.dw-component[aria-pressed="true"]')?.textContent.startsWith('Lid')&&!document.getElementById('dw-size-x').disabled);
 await page.fill('#dw-size-x','42');await page.fill('#dw-size-z','42');
 const edited=page.waitForResponse(r=>r.url()===url+'/api/board/design'&&r.request().method()==='POST');
 await page.click('#dw-apply');design=await(await edited).json();
 assert.equal(design.parts[2].size_mm[0],42);assert.equal(design.version,2);
 await page.waitForFunction(()=>document.getElementById('dw-version').textContent.startsWith('Saved v2')&&!document.getElementById('dw-export').disabled);
 const exporting=page.waitForResponse(r=>r.url()===url+'/api/board/design'&&r.request().method()==='POST'&&r.request().postDataJSON()?.action==='export');
 await page.click('#dw-export');const exported=await exporting;assert.equal(exported.status(),200,await exported.text());assert.ok((await exported.json()).file,'export response must contain a checked file');await page.waitForSelector('#dw-download:not([hidden])');
 const downloadPromise=page.waitForEvent('download');await page.click('#dw-download');const download=await downloadPromise;
 const saved=path.join(artifacts,download.suggestedFilename());await download.saveAs(saved);
 assert.equal(fs.readFileSync(saved).subarray(0,2).toString(),'PK','3MF must be a real ZIP package');
 await page.screenshot({path:path.join(artifacts,'design-checked-export.png')});
 await page.click('#undo-board');await page.waitForFunction(()=>document.getElementById('dw-version').textContent.startsWith('Saved v1'));
 assert.equal(await page.inputValue('#dw-size-x'),'40');
 await page.fill('#dw-size-x','45');
 const response=await page.request.post(url+'/api/board/design',{headers:{Authorization:'Bearer '+token},data:{action:'edit',id:design.card.id,expected_src:design.card.src,part:2,changes:{name:'New lid'}}});
 // Undo restored v1, so a caller still holding v2 must be refused.
 assert.equal(response.status(),409);
 const fresh=await(await page.request.get(url+'/api/board/design/'+design.card.id,{headers:{Authorization:'Bearer '+token}})).json();
 const changed=await page.request.post(url+'/api/board/design',{headers:{Authorization:'Bearer '+token},data:{action:'edit',id:design.card.id,expected_src:fresh.source,part:2,changes:{name:'New lid'}}});assert.equal(changed.status(),200);
 await page.waitForFunction(()=>document.getElementById('dw-apply').disabled);
 assert.equal(await page.inputValue('#dw-size-x'),'45','another client must not erase the draft');
 await page.click('#dw-reload');await page.waitForFunction(()=>!document.getElementById('dw-apply').disabled);
 assert.equal(await page.inputValue('#dw-size-x'),'40');
 await page.locator('#design-workbench details summary').click();
 await page.locator('#dw-photo').setInputFiles({name:'reference.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==','base64')});
 await page.waitForSelector('#dw-preview:not([hidden])');
 let chats=0;await page.route('**/api/companion/chat',route=>{chats++;return route.abort();});
 await page.click('#dw-share');
 const companion=page.frameLocator('#partner-panel iframe');
 await companion.locator('#design-reference-note').waitFor();assert.equal(chats,0,'staging a photo must not submit a prompt');
 await companion.getByRole('button',{name:'Remove',exact:true}).click();await companion.locator('#design-reference-note').waitFor({state:'detached'});
 // Close the partner and verify the reference controls remain usable on smaller screens.
 await page.click('#partner-toggle');
 for(const viewport of[{width:1366,height:768},{width:390,height:844}]){
  await page.setViewportSize(viewport);await page.screenshot({path:path.join(artifacts,'design-'+viewport.width+'.png')});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal overflow at '+viewport.width);
 }
 assert.deepEqual(errors,[]);console.log('PASS: real create → component edit → checked 3MF download → Undo → stale-window recovery → explicit reference staging; desktop and narrow layouts.');
})().catch(async e=>{console.error(e);if(page){console.error(await page.evaluate(()=>({status:document.getElementById('dw-status')?.textContent,report:document.getElementById('dw-report')?.textContent,version:document.getElementById('dw-version')?.textContent,selected:document.querySelector('.dw-component[aria-pressed="true"]')?.textContent,busy:document.getElementById('dw-edit')?.getAttribute('aria-busy')})).catch(()=>null));await page.screenshot({path:path.join(artifacts,'design-failure.png')}).catch(()=>{});}if(logs)console.error(logs);process.exitCode=1;}).finally(async()=>{
 await browser?.close();if(child&&child.exitCode===null){child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,4000))]);if(child.exitCode===null)child.kill('SIGKILL');}
 if(!process.env.APEX_DESIGN_ARTIFACTS)console.log('Artifacts:',artifacts);
});
