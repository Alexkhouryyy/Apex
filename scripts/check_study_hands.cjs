const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../dashboard/static/study-hands.js'),'utf8');
const Controller=new Function(source.replace('export class StudyHandController','class StudyHandController')+';return StudyHandController;')();
const events=[];const c=new Controller({hit:()=> 'shaft',begin:(h,p)=>{events.push('begin');return true;},move:()=>events.push('move'),commit:()=>events.push('commit'),cancel:()=>events.push('cancel'),paint:()=>{}});
let seq=0;const h=(id,pinched,x=.5)=>({id,pinched,x,y:.5});const feed=(hands,t,age=0)=>c.feed({tracking:true,sequence:++seq,age_ms:age,hands},t);
(async()=>{
 feed([h(1,true)],0);assert.equal(events.length,0,'a hand already pinched cannot grab');
 feed([h(1,false)],10);feed([h(1,false)],300);feed([h(1,true)],310);assert.deepEqual(events,['begin']);
 feed([h(2,true,.4),h(1,true,.51)],330);assert.equal(events.at(-1),'move','ordering does not change owner');
 feed([h(2,true)],350);assert.equal(events.at(-1),'cancel','losing owner cancels rather than assigning another hand');
 feed([h(1,false)],400);feed([h(1,false)],700);feed([h(1,true)],720);feed([h(1,false)],740);await Promise.resolve();await Promise.resolve();
 assert.equal(events.filter(e=>e==='commit').length,1,'one release is one commit');
 feed([h(1,false)],800);feed([h(1,false)],1100);feed([h(1,true)],1120);feed([h(1,true)],1200,500);assert.equal(events.at(-1),'cancel','stale detector data cancels');
 feed([h(1,false)],1300);feed([h(1,false)],1600);feed([h(1,true)],1620);feed([h(1,true,.9)],1630);assert.equal(events.at(-1),'cancel','jumping identity cancels');
 console.log('PASS: open-hand dwell, stable identities, single release commit, missing/stale hands and jumps cancel.');
})().catch(e=>{console.error(e);process.exitCode=1;});
