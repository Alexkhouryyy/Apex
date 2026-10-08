// The Work page (dashboard/static/work.js) against a stubbed API: views switch,
// quick add files under the chosen area, the board moves a dropped card, the
// detail panel saves and hands a task to Apex on the chosen plan, and the
// Always on panel orders the plans, shows which are resting, and saves (with the
// night shift's end); a software project links to an Apex Code project, and a task
// the night shift coded opens its session in Apex Code instead of a folder.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=path.join(__dirname,'..','dashboard','static');
const dom=new JSDOM(fs.readFileSync(path.join(base,'work.html'),'utf8'),{url:'http://localhost:7860/work',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.confirm=()=>true;
const today=new Date(),iso=n=>{const x=new Date(today);x.setDate(x.getDate()+n);return new Date(x-x.getTimezoneOffset()*60000).toISOString().slice(0,10);};
let tasks=[{id:1,title:'late report',area:'studies',due:iso(-2),priority:1,status:'todo',project_id:null,notes:'',waiting_on:'',apex_state:null},
  {id:2,title:'invoice Karim',area:'job',due:iso(0),priority:2,status:'todo',project_id:7,notes:'',waiting_on:'',apex_state:null},
  {id:4,title:'draft proposal',area:'job',due:null,priority:2,status:'doing',project_id:null,notes:'',waiting_on:'',apex_state:'running',apex_run:'cli-claude-4-1',apex_folder:'C:\\ApexWork\\4'},
  {id:3,title:'laptop research',area:'business',due:null,priority:2,status:'review',project_id:null,notes:'',waiting_on:'',apex_state:'done',apex_summary:'Wrote result.md',apex_cost:0.04,apex_folder:'C:\\ApexWork\\3'},
  {id:5,title:'fix login',area:'software',due:null,priority:2,status:'doing',project_id:8,notes:'',waiting_on:'',apex_state:'running',apex_run:'code-41',apex_summary:'',apex_folder:''}];
const calls=[];
let agent={enabled:false,auto_work:false,engines:['claude','chatgpt'],plan_runs:8,rest_hours:5,daily_budget:2,task_budget:0.5,brief_time:'08:30',evening_time:'18:00',night_until:'06:30',
  spent_today:0,plan_runs_today:2,eligible:1,running:true,events:[{ts:Date.now()/1000,kind:'plan',task_id:null,text:'Your Claude plan reached its usage limit.'}],
  plans:[{id:'claude',name:'Claude plan',installed:true,unavailable:'reached its usage limit, resting until 15:00',how:''},{id:'chatgpt',name:'ChatGPT plan',installed:true,unavailable:null,how:''},{id:'api',name:'API credits',installed:true,unavailable:null}]};
const overview=()=>({areas:['job','studies','business','software','other'].map(id=>({id,name:id})),projects:[{id:7,name:'Website',area:'job',client:'Karim'},{id:8,name:'NI',area:'software',client:'',code_project_id:null}],tasks,
  today:{overdue:tasks.filter(t=>t.id===1&&t.status!=='done'),today:tasks.filter(t=>t.id===2&&t.status!=='done'),week:[],review:tasks.filter(t=>t.status==='review'),apex_working:[],waiting:[],doing:[],counts:{job:1,studies:1,business:1}}});
w.fetch=async(url,opts={})=>{
  calls.push({url,method:opts.method||'GET',body:opts.body?JSON.parse(opts.body):null});
  if(url==='/api/work')return Response.json(overview());
  if(url==='/api/code')return Response.json({projects:[{id:1,name:'Apex'},{id:2,name:'NI code'}]});
  if(url==='/api/work/projects/8'&&opts.method==='PATCH')return Response.json({id:8,...JSON.parse(opts.body)});
  if(url==='/api/work/agent'&&opts.method==='PUT'){const b=JSON.parse(opts.body);delete b.clear_limits;Object.assign(agent,b);return Response.json(agent);}
  if(url==='/api/work/agent/check'){agent.plans[1].unavailable='is signed in with an API key, which is not your ChatGPT plan';agent.plans[1].how='Run `codex logout`, then `codex login`.';return Response.json(agent);}
  if(url==='/api/work/agent')return Response.json(agent);
  if(/\/apex\/stop$/.test(url)){const t=tasks.find(x=>x.id===4);t.apex_state='stopped';t.status='todo';return Response.json(t);}
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
  assert.equal($('d-engine').value,'claude','the first plan in the order is the default');
  assert.equal($('d-budget-label').hidden,true,'no spending cap on a plan');
  $('d-engine').value='api';$('d-engine').dispatchEvent(new w.Event('change'));assert.equal($('d-budget-label').hidden,false);
  $('d-budget').value='1.5';$('d-give').click();await tick();await tick();
  assert.deepEqual(calls.find(c=>/\/apex$/.test(c.url)).body,{budget_usd:1.5,engine:'api'});assert.equal($('detail').open,false);
  // While Apex works, Stop is offered and ends it.
  [...d.querySelectorAll('.t-main')].find(e=>e.textContent.includes('draft proposal')).click();await tick();await tick();
  assert.equal($('d-stop').hidden,false);assert.equal($('d-give').disabled,true);
  $('d-stop').click();await tick();await tick();
  assert.ok(calls.some(c=>c.url==='/api/work/tasks/4/apex/stop'&&c.method==='POST'));assert.match($('message').textContent,/Stopped/);
  // A task the night shift coded opens its session in Apex Code, not a folder.
  d.querySelector('[data-view=board]').click();await tick();
  [...d.querySelectorAll('.t-main')].find(e=>e.textContent.includes('fix login')).click();await tick();await tick();
  const link=$('d-apex-files').querySelector('a');
  assert.equal(link.getAttribute('href'),'/code#s=41');assert.equal(link.textContent,'Open the session in Apex Code');
  assert.ok(!calls.some(c=>c.url==='/api/work/tasks/5'&&c.method==='GET'),'no folder to list');
  $('detail').close();d.querySelector('[data-view=today]').click();await tick();
  // "Apex can take this" saves with the task.
  [...d.querySelectorAll('.t-main')].find(e=>e.textContent.includes('invoice Karim')).click();await tick();
  $('d-apex-ok').checked=true;$('d-save').click();await tick();
  assert.equal(calls.filter(c=>c.method==='PATCH').at(-1).body.apex_ok,true);
  // Ticking a task done.
  d.querySelector('.task .done').click();await tick();
  assert.equal(calls.filter(c=>c.method==='PATCH').at(-1).body.status,'done');
  // Projects: a software project links to an Apex Code project (the owner's list, from /api/code).
  d.querySelector('[data-view=projects]').click();await tick();await tick();
  const sel=$('project-list').querySelector('.code-link select');
  assert.ok(sel,'a Code project select on the software project only');assert.equal($('project-list').querySelectorAll('.code-link').length,1);
  assert.deepEqual([...sel.options].map(o=>o.textContent),['No code project','Apex','NI code']);
  sel.value='2';sel.dispatchEvent(new w.Event('change'));await tick();await tick();
  assert.deepEqual(calls.filter(c=>c.url==='/api/work/projects/8').at(-1).body,{code_project_id:2});
  assert.match($('message').textContent,/coded overnight/);
  // Always on: plans in order with why one is resting; reorder, switch on, save.
  d.querySelector('[data-view=agent]').click();await tick();await tick();
  assert.equal($('view-agent').hidden,false);
  const plans=()=>[...$('a-plans').children];
  assert.deepEqual(plans().map(li=>li.querySelector('.name').textContent),['1. Claude plan','2. ChatGPT plan','API credits']);
  assert.match(plans()[0].textContent,/resting until 15:00/);assert.ok(plans()[2].classList.contains('off'));
  assert.match($('a-events').textContent,/usage limit/);
  plans()[1].querySelector('button').click();
  assert.deepEqual(plans().map(li=>li.querySelector('.name').textContent),['1. ChatGPT plan','2. Claude plan','API credits']);
  $('a-enabled').checked=true;$('a-auto').checked=true;
  $('agent-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();await tick();
  const put=calls.filter(c=>c.method==='PUT').at(-1).body;
  assert.deepEqual([put.enabled,put.auto_work,put.engines,put.plan_runs],[true,true,['chatgpt','claude'],8]);
  assert.equal(put.night_until,'06:30','the night shift\'s end is kept');
  assert.ok($('agent-dot').classList.contains('on'));
  plans()[0].querySelector('input').click();plans()[0].querySelector('input').click();   // untick both plans
  $('agent-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();
  assert.match($('message').textContent,/at least one/);
  $('a-clear').click();await tick();
  // Check sign-in: a plan signed in with an API key is flagged, with what to do.
  $('a-check').click();await tick();await tick();
  assert.match($('message').textContent,/ChatGPT plan is signed in with an API key/);assert.equal($('message').className,'message error');
  assert.match(plans().find(li=>li.textContent.includes('ChatGPT')).querySelector('.how').textContent,/codex login/);
  console.log('PASS: Today headline and sections, area filter and quick add into it, views switch, drag to a column changes status, the detail shows Apex\'s result and files, ticking marks done, Give to Apex uses the chosen plan (cap only for API credits), "Apex can take this" saves, and Stop ends a running task, and Always on orders plans, shows resting ones, saves, and Check sign-in flags a plan that would bill credits; a software project links to an Apex Code project, and a coded task opens its session.');
  process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
