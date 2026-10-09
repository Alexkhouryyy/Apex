const {JSDOM}=require('jsdom'),fs=require('fs'),assert=require('node:assert/strict');
const dom=new JSDOM(fs.readFileSync('dashboard/static/index.html','utf8'),{url:'http://localhost:7860',runScripts:'outside-only'});
const w=dom.window,d=w.document;
let records=[{id:'unit',text:'<img src=x onerror=alert(1)>',domain:'work',purposes:['answer'],source:'owner-input',inferred:false,provenance:['raw'],expires_at:null}],failed=false;
w.api=async(url,options={})=>{
 if(options.method==='POST'){
  if(failed)throw Error('Disconnected; evidence remains saved');
  if(url.endsWith('/forget')){records=[];return {deleted:['unit','derived']};}
  return {id:'new'};
 }
 return records;
};
w.eval(fs.readFileSync('dashboard/static/scoped-memory.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 await w.ApexScopedMemory.load();
 const root=d.getElementById('scoped-memory-list');
 assert.equal(root.querySelector('img'),null);assert.match(root.textContent,/Observed.*owner-input/);
 failed=true;root.querySelector('button').click();await tick();
 assert.equal(root.querySelector('button').disabled,false);assert.match(d.getElementById('scoped-memory-status').textContent,/Disconnected/);
 failed=false;root.querySelector('button').click();await tick();
 assert.equal(records.length,0);assert.match(d.getElementById('scoped-memory-status').textContent,/Deleted 2/);
 console.log('Scoped memory UI: escaping, source visibility, failed delete and deletion receipt passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
