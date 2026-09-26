const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../dashboard/static/study-hands.js'),'utf8');
const Controller=new Function(source.replace('export class StudyHandController','class StudyHandController')+';return StudyHandController;')();
const h=(id,pinched,x=.5)=>({id,pinched,x,y:.5});
function setup(){
 const events=[],targets=[];let seq=0;
 const c=new Controller({hit:(x,y,p)=>{targets.push(p);return 'shaft';},begin:()=>{events.push('begin');return true;},move:()=>events.push('move'),commit:()=>events.push('commit'),cancel:()=>events.push('cancel'),paint:()=>{}});
 const feed=(hands,t,age=0)=>c.feed({tracking:true,sequence:++seq,age_ms:age,hands},t);
 const grab=()=>{feed([h(1,false)],0);feed([h(1,false)],210);feed([h(1,true)],230);assert.deepEqual(events,['begin']);};
 return {c,events,targets,feed,grab};
}
(async()=>{
 let t=setup();t.feed([h(1,true)],0);assert.deepEqual(t.events,[],'already pinched hands cannot grab');
 t=setup();t.grab();assert.equal(t.targets.at(-1),'shaft','pinch closure retains the armed target');
 t.feed([h(2,true,.4),h(1,true,.51)],260);assert.equal(t.events.at(-1),'move');
 t.feed([h(2,true)],280);assert.equal(t.events.at(-1),'move','brief loss freezes without cancelling or moving');
 t.feed([h(1,true,.52)],340);assert.equal(t.events.at(-1),'move','same hand recovers a held pose');
 t.feed([h(1,false)],360);t.feed([h(1,true,.53)],390);assert(!t.events.includes('commit'),'one noisy open frame cannot release');
 t.feed([h(1,false)],420);t.feed([h(1,false)],480);await new Promise(setImmediate);
 assert.equal(t.events.filter(x=>x==='commit').length,1,'confirmed release commits once');
 t.feed([h(1,false)],500);assert.equal(t.events.filter(x=>x==='commit').length,1);
 t=setup();t.grab();t.feed([],250);t.feed([h(2,true)],440);assert.equal(t.events.at(-1),'cancel','persistent loss never transfers to another hand');
 t=setup();t.grab();t.feed([],250);t.feed([h(1,false)],290);assert.equal(t.events.at(-1),'cancel','unseen release cannot commit');
 t=setup();t.grab();t.feed([{...h(1,false),fist:true}],250);assert.equal(t.events.at(-1),'cancel','fist is cancel, not apply');
 t=setup();t.grab();t.feed([h(1,true)],260,500);assert.equal(t.events.at(-1),'cancel','stale input cancels immediately');
 t=setup();t.grab();t.feed([h(1,true,.9)],260);assert.equal(t.events.at(-1),'cancel','tracking jumps cancel');
 t=setup();t.grab();t.c.reset('window hidden');t.feed([h(1,true)],250);assert.equal(t.events.filter(e=>e==='begin').length,1,'reset requires a fresh open hover');
 console.log('PASS: deliberate grabs, armed target, brief-loss freeze/recovery, confirmed releases, fist/loss/stale/jump cancellation, and no hand reassignment.');
})().catch(e=>{console.error(e);process.exitCode=1;});
