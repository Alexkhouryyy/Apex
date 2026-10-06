// The Work page (dashboard/static/work.js) against a stubbed API: views switch,
// quick add files under the chosen area, the board moves a dropped card, the
// detail panel saves and hands a task to Apex with its spending cap.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const dom=new JSDOM(fs.readFileSync(path.join(base,'work.html'),'utf8'),{url:'http://localhost:7860/work',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.confirm=()=>true;
const today=new Date(),iso=n=>{const x=new Date(today);x.setDate(x.getDate()+n);return new Date(x-x.getTimezoneOffset()*60000).toISOString().slice(0,10);};
let tasks=[{id:1,title:'late report',area:'studies',due:iso(-2),priority:1,status:'todo',project_id:null,notes:'',waiting_on:'',apex_state:null},
  {id:2,title:'invoice Karim',area:'job',due:iso(0),priority:2,status:'todo',project_id:7,notes:'',waiting_on:'',apex_state:null},
  {id:3,title:'laptop research',area:'business',due:null,priority:2,status:'review',project_id:null,notes:'',waiting_on:'',apex_state:'done',apex_summary:'Wrote result.md',apex_cost:0.04,apex_folder:'C:\\ApexWork\\3'}];
const calls=[];
const overview=()=>({areas:['job','studies','business','software','other'].map(id=>({id,name:id})),projects:[{id:7,name:'Website',area:'job',client:'Karim'}],tasks,
  today:{overdue:tasks.filter(t=>t.id===1&&t.status!=='done'),today:tasks.filter(t=>t.id===2&&t.status!=='done'),week:[],review:tasks.filter(t=>t.status==='review'),apex_working:[],waiting:[],doing:[],counts:{job:1,studies:1,business:1}}});
w.fetch=async(url,opts={})=>{
  calls.push({url,method:opts.method||'GET',body:opts.body?JSON.parse(opts.body):null});
  if(url==='/api/work')return Response.json(overview());
  if(url==='/api/work/tasks'&&opts.method==='POST'){const b=JSON.parse(opts.body);const t={id:9,title:b.quick,area:b.area||'other',due:null,priority:2,status:'todo',project_id:null};tasks.push(t);return Response.json(t);}
  let m=url.match(/^\/api\/work\/tasks\/(\d+)$/);
  if(m&&opts.method==='PATCH'){const t=tasks.find(x=>x.id===+m[1]);Object.assign(t,JSON.parse(opts.body));return Response.json(t);}
  if(m)return Response.json({...tasks.find(x=>x.id===+m[1]),files:['result.md']});
  if(/\/apex$/.test(url))return Response.json({});
  throw Error(url);
};
w.eval(fs.readFileSync(path.join(base,'work.js'),'utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
  await tick();await tick();
  // Today: the headline and sections reflect the work.
  assert.match($('headline').textContent,/1 overdue, 1 due today/);
  assert.match($('subline').textContent,/Apex finished 1 task/);
  assert.match($('view-today').textContent,/late report.*2 days late/s);
  assert.equal($('view-board').hidden,true);
  // Area filter, then quick add files the task there.
  [...$('areas').children].find(b=>b.textContent.startsWith('business')).click();await tick();
  assert.doesNotMatch($('view-today').textContent,/invoice Karim/,'the filter hides other areas');
  $('quick-text').value='order shelves';$('quick').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();await tick();
  assert.deepEqual(calls.find(c=>c.url==='/api/work/tasks').body,{quick:'order shelves',area:'business'});
  [...$('areas').children][0].click();await tick();
  // Board: dropping a card on a column changes its status.
  d.querySelector('[data-view=board]').click();await tick();
  assert.equal($('view-board').hidden,false);assert.equal($('view-today').hidden,true);
  const doing=d.querySelector('.col[data-status=doing]');
  const drop=new w.Event('drop',{bubbles:true,cancelable:true});drop.dataTransfer={getData:()=> '2'};doing.dispatchEvent(drop);await tick();await tick();
  assert.deepEqual(calls.filter(c=>c.method==='PATCH').at(-1),{url:'/api/work/tasks/2',method:'PATCH',body:{status:'doing'}});
  // Detail: Apex's result is shown; Give to Apex sends the chosen cap.
  d.querySelector('[data-view=today]').click();await tick();
  [...d.querySelectorAll('.t-main')].find(e=>e.textContent.includes('laptop research')).click();await tick();await tick();
  assert.equal($('detail').open,true);assert.equal($('d-apex-summary').textContent,'Wrote result.md');
  assert.match($('d-apex-files').textContent,/result\.md/);
  $('detail').close();
  [...d.querySelectorAll('.t-main')].find(e=>e.textContent.includes('late report')).click();await tick();
  $('d-budget').value='1.5';$('d-give').click();await tick();await tick();
  assert.deepEqual(calls.find(c=>/\/apex$/.test(c.url)).body,{budget_usd:1.5});assert.equal($('detail').open,false);
  // Ticking a task done.
  d.querySelector('.task .done').click();await tick();
  assert.equal(calls.filter(c=>c.method==='PATCH').at(-1).body.status,'done');
  console.log('PASS: Today headline and sections, area filter and quick add into it, views switch, drag to a column changes status, the detail shows Apex\'s result and files, Give to Apex sends its cap, and ticking marks done.');
  process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
