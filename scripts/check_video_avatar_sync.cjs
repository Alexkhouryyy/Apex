// The video avatar's frame sync (dashboard/static/video-avatar.js): clips start
// on the frame the idle loop will be showing, follow on from each other, and
// hand back to the loop where they end, so the face never jumps.
const {JSDOM}=require('jsdom'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const dom=new JSDOM('<div id=host></div>',{url:'http://localhost/',runScripts:'outside-only'});
const w=dom.window;let clock=0;Object.defineProperty(w.performance,'now',{value:()=>clock});
w.URL.createObjectURL=()=> 'blob:x';w.URL.revokeObjectURL=()=>{};
// Media elements: time is set by the check; play() reports playing, and ended() is called by hand.
const videos=[];
Object.defineProperty(w.HTMLMediaElement.prototype,'currentTime',{get(){return this._t||0;},set(v){this._t=v;},configurable:true});
w.HTMLMediaElement.prototype.play=function(){videos.push(this);queueMicrotask(()=>this.onplaying?.());return Promise.resolve();};
w.HTMLMediaElement.prototype.pause=function(){};
w.HTMLMediaElement.prototype.canPlayType=()=> 'probably';
const asked=[];let renderMs=600;
const request=async url=>{
  if(url==='/api/avatar/status')return Response.json({available:true,engine:'still',fps:25,frames:50});
  if(url.startsWith('/api/avatar/idle'))return new Response('idle');
  const start=Number(new URL(url,'http://x').searchParams.get('start'));asked.push(start);
  clock+=renderMs;                                                     // the render takes this long
  return new Response('clip',{headers:{'X-End-Frame':String((start+25)%50),'X-Render-Ms':String(renderMs)}});
};
w.eval(fs.readFileSync(path.join(__dirname,'..','dashboard','static','video-avatar.js'),'utf8'));
const tick=()=>new Promise(r=>setTimeout(r,5));
(async()=>{
  const a=new w.ApexVideoAvatar(w.document.getElementById('host'),request);await a.start();
  const idle=w.document.querySelector('.avatar-idle');
  // 1. First clip of a reply: the loop is on frame 25 (1.0 s) and clips take about 0.8 s, so start 20 frames ahead.
  idle.currentTime=1.0;
  const one=await a.render(new w.Blob(['a']));
  assert.equal(one.start,45);assert.equal(one.end,20);assert.equal(one.renderMs,600);
  // 2. The next clip, asked for while the first waits to play, follows on from it.
  const two=await a.render(new w.Blob(['b']));
  assert.equal(two.start,20,'the second clip starts where the first ends');
  // 3. Playing: when a clip ends, the loop carries on from its last frame.
  const p1=a.play(one);await tick();assert.ok(a.host.classList.contains('avatar-talking'));
  a.speaking.onended();await p1;assert.equal(idle.currentTime,20/25);
  const p2=a.play(two);await tick();a.speaking.onended();await p2;assert.equal(idle.currentTime,45/25);
  assert.ok(!a.host.classList.contains('avatar-talking'));
  // 4. A new reply later: back to the loop's own frame, with the learned latency (0.6 s now).
  clock+=5000;idle.currentTime=0.4;                                    // frame 10
  const three=await a.render(new w.Blob(['c']));
  const ahead=Math.round(a.latency/1000*25);
  assert.ok(Math.abs(three.start-(10+ahead))<=1&&three.start!==two.end,`a fresh reply starts from the loop (start ${three.start})`);
  assert.ok(a.latency<800&&a.latency>600,'it learns how long clips take');
  // 5. Stop: nothing waiting to play, so the next reply starts from the loop again.
  const four=await a.render(new w.Blob(['d']));a.stop();                // rendered, then Stop: it will never play
  clock+=2000;idle.currentTime=1.2;                                    // frame 30
  const five=await a.render(new w.Blob(['e']));
  assert.notEqual(five.start,four.end,'after Stop, the next reply does not chain from a clip that never played');
  console.log('PASS: the first clip starts on the frame the loop will be showing when it arrives, the next follows on from it, the loop resumes from each clip\'s last frame, a new reply starts from the loop, and render time is learned.');
  process.exit(0);
})().catch(e=>{console.error(e);process.exit(1);});
