// Optional real WebGL + server check of importing a model into the study
// library, using the real OpenMotor CAD file. Requires Playwright/Chromium and
// Apex's Python dependencies (APEX_TEST_PYTHON / APEX_CHROMIUM_PATH as in
// check_study_browser.cjs). Everything lives in a temporary folder; the note
// drafter is a labelled stand-in, so no AI call is made.
const {chromium}=require('playwright');
const {spawn}=require('child_process'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path'),zlib=require('zlib');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'apex-import-check-'));
const token='import-browser-test-only',origin='http://127.0.0.1:8768',studyDir=path.join(temp,'study');
const file=path.join(temp,'CIAG_125_25_motor.glb');
fs.writeFileSync(file,zlib.gunzipSync(fs.readFileSync(path.join(root,'dashboard/static/models/openmotor.glb.gz'))));
const boot=`import sys, json
sys.path.insert(0,sys.argv[1])
import config
config.DASHBOARD_TOKEN='import-browser-test-only'
from agent import longterm, provider
longterm.DB_PATH=sys.argv[2]
longterm.init_db()
provider.complete=lambda model, system, user, max_tokens=0: json.dumps({'parts': {c['id']: {'purpose': 'Stand-in note for '+c['name']+'.'} for c in json.loads(user)['components']}})
from dashboard.server import app
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8768)`;
let server,browser;
(async()=>{try{
  server=spawn(process.env.APEX_TEST_PYTHON||'python',['-c',boot,root,path.join(temp,'db.sqlite')],{stdio:['ignore','ignore','pipe'],env:{...process.env,APEX_STUDY_DIR:studyDir}});
  await new Promise((resolve,reject)=>{let log='';const t=setTimeout(()=>reject(Error('Server startup timeout: '+log)),15000);
    server.stderr.on('data',c=>{log+=c;if(log.includes('Uvicorn running')){clearTimeout(t);resolve();}});server.once('exit',()=>{clearTimeout(t);reject(Error('Server stopped: '+log));});});
  browser=await chromium.launch({headless:true,...(process.env.APEX_CHROMIUM_PATH?{executablePath:process.env.APEX_CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto(origin+'/study?model=heart&token='+token);
  await page.waitForFunction(()=>document.querySelectorAll('.component').length===16);
  assert.equal(await page.locator('#import-tools').isHidden(),true,'built-in subjects have no import tools');
  await page.locator('#import-open').click();
  // A wrong file type is explained before anything is uploaded.
  const stl=path.join(temp,'part.stl');fs.writeFileSync(stl,'solid part');
  await page.setInputFiles('#import-file',stl);
  await page.waitForFunction(()=>/not a \.glb or \.gltf/.test(document.querySelector('#import-message').textContent));
  await page.setInputFiles('#import-file',file);
  await page.fill('#import-name','Axial flux motor');await page.selectOption('#import-detail','fine');
  await page.fill('#import-source','https://github.com/eMotres/OpenMotor-Hardware');await page.fill('#import-license','CERN-OHL-W-2.0');
  await page.locator('#import-go').click();
  await page.waitForURL(/\/study\?.*model=axial-flux-motor-[0-9a-f]{6}/,{timeout:30000});
  await page.waitForFunction(()=>document.querySelectorAll('.component').length===26,null,{timeout:30000});
  assert.equal(await page.locator('h1').textContent(),'Axial flux motor');
  assert.match(await page.locator('#import-about').textContent(),/parts named from the file/);
  assert.match(await page.locator('#sources').textContent(),/CERN-OHL-W-2\.0/);
  await page.locator('.component',{hasText:'Magnet ×28'}).click();
  await page.waitForFunction(()=>document.querySelector('#part-name').textContent==='Magnet ×28');
  await page.getByRole('button',{name:'Take apart',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#separation').value==='100');
  await page.locator('#draft-notes').click();
  await page.waitForFunction(()=>/AI-drafted/.test(document.querySelector('#import-about').textContent),null,{timeout:30000});
  await page.locator('.component',{hasText:'Magnet ×28'}).click();
  await page.waitForFunction(()=>document.querySelector('#part-purpose').textContent==='Stand-in note for Magnet ×28.');
  assert.match(await page.locator('#study-caption').textContent(),/AI-drafted notes, not reviewed/);
  assert.equal(fs.readdirSync(studyDir).length,3,'file, manifest and notes live in the study folder');
  await page.locator('#remove-import').click();
  await page.waitForURL(/model=jet-engine/);
  assert.equal(fs.readdirSync(studyDir).length,0,'removing deletes everything it stored');
  assert.deepEqual(errors,[]);
  console.log('PASS: imported the real OpenMotor CAD file in a browser (26 parts, named from the file), took it apart, drafted notes (labelled AI-drafted), and removed it cleanly; wrong file types are explained before upload.');
}finally{await browser?.close();if(server&&server.exitCode===null){server.kill('SIGTERM');}fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exit(1);});
