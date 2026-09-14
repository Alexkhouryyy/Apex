// Offline DOM simulation; no real provider or device calls.
const {JSDOM}=require('jsdom');
const fs=require('fs'), assert=require('node:assert/strict'), path=require('path');
const base=path.join(__dirname,'..','dashboard','static');
const dom=new JSDOM(fs.readFileSync(path.join(base,'index.html'),'utf8'), {url:'http://localhost:7860',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
const tick=()=>new Promise(r=>setTimeout(r,20));
let posted=[],runs=[],stopCount=0,failOnce=true,refresh;
w.setInterval=fn=>{refresh=fn;return 1;};
$('tab-constellation').classList.add('active');
w.localStorage.setItem('apex_token','local-test');
w.fetch=async(url,opts={})=>{
 assert.equal(opts.headers.Authorization,'Bearer local-test');
 if(url==='/api/models')return Response.json({models:[{model:'deepseek-flash',available:true}]});
 if(url.endsWith('/stop')){stopCount++;return Response.json({stop_requested:true});}
 if(url==='/api/team' && opts.method==='POST') {
   const body=JSON.parse(opts.body);posted.push(body);
   if(failOnce){failOnce=false;throw Error('Network disconnected');}
   runs=[{...body,status:'running',cost_usd:.01,calls:2,steps:[{role:'researcher',model:'deepseek-flash',status:'done',calls:2,cost_usd:.01,result:'<img src=x onerror=alert(1)>',evidence:[{tool:'read_file',status:'returned',result:'<script>unsafe</script>'}]}]}];
   return Response.json(runs[0]);
 }
 if(url==='/api/team')return Response.json({runs});
 throw Error(url);
};
w.eval(fs.readFileSync(path.join(base,'team.js'),'utf8'));
(async()=>{
 await tick();
 $('team-task').value='Implement a small fix';$('team-context').value='C:/project';
 $('team-model-coder').value='deepseek-flash';
 $('team-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 assert.match($('team-start').textContent,/Recover/);
 assert.ok(w.sessionStorage.getItem('apex_team_pending'));
 $('team-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
 assert.equal(posted[0].id,posted[1].id);
 assert.deepEqual(posted[0].roles,['researcher','coder','reviewer']);
 assert.equal(posted[0].models.coder,'deepseek-flash');
 assert.equal(w.sessionStorage.getItem('apex_team_pending'),null);
 assert.ok($('team-runs').textContent.includes('<img'));
 assert.equal($('team-runs').querySelectorAll('img,script').length,0);
 $('team-runs').querySelector('button').click();await tick();assert.equal(stopCount,1);
 assert.match($('team-runs').querySelector('button').textContent,/requested/);
 runs[0].status='done';await refresh();
 assert.equal($('team-runs').querySelectorAll('button').length,0);
 assert.match($('team-runs').textContent,/done/);
 assert.equal($('team-model-options').children.length,1);
 console.log('PASS: authenticated task submission, retry identity, evidence escaping, status refresh, model choices and stop controls.');
 dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
