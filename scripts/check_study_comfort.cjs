const fs=require('fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('dashboard/static/study-comfort.js','utf8').replaceAll('export ','');
const {StudyReach,StudyModeHover}=new Function(source+';return {StudyReach,StudyModeHover};')();
const r=new StudyReach(),h={id:1,x:.6,y:.65,pinched:false,fingertips:{index:[.6,.65]}};
assert.equal(r.hand(h).x,h.x,'default leaves familiar reach unchanged');
r.center={x:.6,y:.65};r.gain=2;
assert.equal(r.hand(h).x,.5);assert.equal(r.hand(h).y,.5,'relaxed position maps to centre');
assert(Math.abs(r.hand({...h,x:.7}).x-.7)<1e-9,'half the hand travel covers the same view');
assert.equal(r.hand({...h,x:1}).x,1,'reach clamps at viewport edge');
assert.equal(r.hand({...h,x:2}).x,2,'invalid camera coordinates remain invalid for input gate');
assert.deepEqual(r.hand(h).fingertips.index,[.5,.5]);assert.equal(r.hand(h).rawX,.6,'raw sample retained for jump guard');
const d=new StudyModeHover();assert(!d.feed(h,'move',0).activate);
for(const time of [100,200,300,400,500,600])assert(!d.feed(h,'move',time).activate);
assert(d.feed(h,'move',660).activate);assert(!d.feed(h,'move',800).activate,'one switch until hand leaves');
d.feed(h,null,810);assert(!d.feed(h,'turn',820).activate);
assert(!d.feed({...h,pinched:true},'turn',1500).activate,'pinched hand never switches');
d.feed(h,'turn',1510);assert(!d.feed({...h,id:2},'turn',2180).activate,'identity changes require new dwell');
d.feed(h,'turn',2200);assert(!d.feed(h,'turn',3000).activate,'a tracking gap is not dwell time');
// Exercise real gesture gate with mapped input: scaling must not turn a safe
// small movement into a tracking-jump cancellation, or mask a real raw jump.
const ctrlSource=fs.readFileSync('dashboard/static/study-hands.js','utf8').replace('export ','');
const Controller=new Function(ctrlSource+';return StudyHandController;')();
let routes=0,cancels=0,moves=0,seq=0;
const c=new Controller({hit:()=> 'shaft',begin:()=>true,move:()=>moves++,cancel:()=>cancels++,commit:()=>{},paint(){},route:()=>{routes++;return null;}});
const feed=(hand,t)=>c.feed({sequence:++seq,tracking:true,age_ms:0,hands:[r.hand(hand)]},t);
feed(h,0);feed(h,210);feed({...h,pinched:true},230);const count=routes;
feed({...h,pinched:true,x:.75},260);assert.equal(moves,1);assert.equal(cancels,0);assert.equal(routes,count,'mode bar cannot intercept a held component');
feed({...h,pinched:true,x:1},300);feed({...h,pinched:true,x:.65},340);assert.equal(cancels,1,'raw jumps still cancel even at clipped edges');
console.log('PASS: comfort reach, raw jump protection, deliberate mode dwell, identity/gap reset, and no mode switch during holds.');
