// Real inspector module: conflicts, retained drafts, scope and late responses.
const {JSDOM}=require('jsdom');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const dom=new JSDOM('<header id="board-bar"><div class="header-actions"></div></header>',{url:'http://apex.test/board?design=1',runScripts:'outside-only'});
const w=dom.window,d=w.document,$=id=>d.getElementById(id);
w.eval(fs.readFileSync(path.join(__dirname,'../dashboard/static/board-design.js'),'utf8').replace('export class','class')+'\nwindow.DesignWorkbench=DesignWorkbench;');
const source=v=>'created/block/v'+v+'.glb';
const data=(v=1,epoch='a')=>({workspace:{id:'space',epoch},card:{id:'block',kind:'model',title:'<img src=x onerror=alert(1)>',src:source(v)},source:source(v),version:v,extent_mm:[40,12,30],parts:[{index:0,name:'Base',shape:'box',size_mm:[40,12,30],position_mm:[0,0,0],rotation_deg:[0,0,0]},{index:1,name:'Lid',shape:'box',size_mm:[40,2,30],position_mm:[0,20,0],rotation_deg:[0,0,0]}]});
const frame=s=>({workspace:s.workspace,cards:[s.card],selection:s.card});
let current=data(),pendingGet=null,pendingPost=null,pendingSelect=null,fail=false;
const calls=[],selections=[],messages=[];
const api=async(url,opts)=>{
 if(!opts){if(pendingGet)return pendingGet;return Response.json(current);}
 const body=JSON.parse(opts.body);calls.push(body);
 if(fail){const e=new Error('stale model');e.status=409;throw e;}
 if(pendingPost)return pendingPost;
 if(body.action==='edit'){current=data(2);current.parts[body.part]={...current.parts[body.part],...body.changes};return Response.json(current);}
 return Response.json({source:current.source,scope:'Base',version:current.version,verdict:'unverified',ok:false,description:'Thickness not measured'});
};
const tool=new w.DesignWorkbench({api,select:async(...s)=>{selections.push(s);return pendingSelect||{id:s[0],selected_part:s[1]};},workspace:()=>({workspace:current.workspace}),partner:m=>messages.push(m),clearReference:()=>messages.push({clear:true}),photo:()=>'/9j/AA=='});
const flush=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
 await tool.update(frame(current));
 assert.equal($('dw-title').textContent,current.card.title);assert.equal($('dw-title').children.length,0,'titles are text');
 assert.equal($('dw-size-y').value,'12');
 let finishSelection;pendingSelect=new Promise(r=>finishSelection=r);const choosing=tool.choose(1);
 assert.equal($('dw-size-x').disabled,true,'measurements wait for the selected component');
 const beforeSelection=calls.length;await tool.edit();await tool.fabricate(true);assert.equal(calls.length,beforeSelection,'pending selection cannot mutate or export the previous part');
 finishSelection({id:'block',selected_part:{mesh:1}});await choosing;pendingSelect=null;
 assert.equal($('dw-size-y').value,'2');assert.equal($('dw-size-x').disabled,false);
 let finishOldSelection;pendingSelect=new Promise(r=>finishOldSelection=r);const oldChoosing=tool.choose(0);pendingSelect=null;
 await tool.choose(1);finishOldSelection({id:'block',selected_part:{mesh:0}});await oldChoosing;
 assert.equal(tool.part,1,'an older selection acknowledgement cannot replace the newest choice');
 await tool.choose(1);assert.equal(selections.at(-1)[1].mesh,1);assert.equal(selections.at(-1)[1].src,source(1));
 await tool.choose(0);
 $('dw-size-x').value='55';$('dw-size-x').dispatchEvent(new w.Event('input',{bubbles:true}));
 await tool.choose(1);await tool.choose(0);assert.equal($('dw-size-x').value,'55','switching components retains drafts');
 current=data(2);await tool.update(frame(current));assert.equal($('dw-size-x').value,'55');assert.equal($('dw-apply').disabled,true,'external geometry changes cannot overwrite a draft');
 $('dw-reload').click();await flush();assert.equal($('dw-size-x').value,'40');assert.equal($('dw-apply').disabled,false);
 $('dw-size-x').value='42';$('dw-size-x').dispatchEvent(new w.Event('input',{bubbles:true}));
 await tool.edit();assert.equal(calls.at(-1).expected_src,source(2));assert.equal(calls.at(-1).changes.size_mm[0],42);
 // A stale frame followed by a newer server state must retry, never show a different revision as this one.
 await tool.update(frame(current));
 await tool.fabricate(true);assert.equal($('dw-download').hidden,true,'unknown checks never offer a file');
 assert.match($('dw-report').textContent,/Thickness not measured/);
 const count=calls.length;$('dw-camera').click();assert.equal(calls.length,count);assert.equal(messages.length,0,'choosing a reference never sends or starts voice');
 $('dw-share').click();assert.equal(messages.at(-1).apex,'design-reference');
 current=data(3,'other');await tool.update(frame(current));assert.equal(tool.referenceImage,null);assert.ok(messages.some(m=>m.clear),'a workspace switch clears a staged reference');
 let finish;pendingPost=new Promise(r=>finish=r);const checking=tool.fabricate(true);await flush();
 current=data(3,'third');await tool.update(frame(current));finish(Response.json({scope:'Old project',source:source(3),version:3,verdict:'manufacturable',description:'OLD',file:'created/private/v4.3mf'}));await checking;pendingPost=null;
 assert.equal($('dw-download').hidden,true,'late export cannot attach to a different workspace');assert.doesNotMatch($('dw-report').textContent,/OLD/);
 let finishDraft;pendingPost=new Promise(r=>finishDraft=r);const oldCheck=tool.fabricate(true);await flush();
 $('dw-size-x').value='43';$('dw-size-x').dispatchEvent(new w.Event('input',{bubbles:true}));
 finishDraft(Response.json({scope:'Base',version:3,verdict:'manufacturable',description:'Old saved dimensions',file:'created/block/v4.3mf'}));await oldCheck;pendingPost=null;
 assert.equal($('dw-download').hidden,true,'editing while checks run invalidates the pending download');
 assert.doesNotMatch($('dw-report').textContent,/Old saved/);
 console.log('PASS: measured dimensions, part selection, draft retention, conflict recovery, unknown checks, explicit reference sharing and stale-workspace responses.');
 w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1;});
