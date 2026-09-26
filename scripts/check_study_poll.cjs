// Exercise the shipping polling/ownership lifecycle with a controlled network.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('dashboard/static/study.js','utf8');
const code=source.slice(source.indexOf('function pauseHands('),source.indexOf("$('study-hands').onclick"));
let now=0,id=0;const timers=new Map(),requests=[],feeds=[],errors=[],elements={};
const ctx=vm.createContext({performance:{now:()=>now},AbortSignal,
  setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
  document:{hidden:false,querySelector:()=>null,activeElement:{tagName:'BODY'}},
  $:id=>elements[id]||(elements[id]={hidden:true,setAttribute(){}}),
  api:(url,opts)=>{
    const action=JSON.parse(opts.body).action;
    if(action!=='sample')return Promise.resolve({});
    return new Promise((resolve,reject)=>requests.push({resolve,reject}));
  },
  hands:{reset(){},feed:data=>feeds.push(data)},cancelManipulation(){},status:s=>errors.push(s),
  diagnostics:{metrics:{sample(){},event:e=>errors.push(e)}}});
vm.runInContext(`let current={},session='s',handEnabled=false,handEpoch=0,handTimer=null,handOwner='owner',mouseUntil=0;\n${code}\nglobalThis.start=enableHands;globalThis.stop=pauseHands;globalThis.enabled=()=>handEnabled;`,ctx);
const flush=async()=>{for(let i=0;i<5;i++)await Promise.resolve();};
function next(){assert.equal(timers.size,1);const [id,t]=timers.entries().next().value;timers.delete(id);now+=t.delay;t.fn();return t;}
(async()=>{
  await ctx.start();assert.equal(requests.length,1);assert.equal(timers.size,0,'never queue overlapping requests');
  now=4;requests[0].resolve({sequence:1,age_ms:3});await flush();
  assert.equal(feeds[0].age_ms,7,'network delay contributes to input freshness');
  assert(Math.abs([...timers.values()][0].delay-(1000/30-4))<.001,'request time counts toward the 30 Hz interval');
  next();assert.equal(requests.length,2);assert.equal(timers.size,0);
  now+=50;requests[1].resolve({sequence:2});await flush();
  assert.equal([...timers.values()][0].delay,0,'slow requests add no fixed delay and no catch-up burst');
  next();assert.equal(requests.length,3);
  ctx.stop();await ctx.start();assert.equal(requests.length,4);
  requests[2].reject(Error('old timeout'));await flush();
  assert(ctx.enabled(),'an obsolete error cannot pause the new controller');assert.deepEqual(errors,[]);
  requests[3].resolve({sequence:4});await flush();assert.equal(timers.size,1);
  next();ctx.stop();requests[4].resolve({sequence:5});await flush();
  assert.equal(timers.size,0);assert.deepEqual(feeds.map(x=>x.sequence),[1,2,4],'no late movement after pause');
  console.log('PASS: study targets 30 Hz including RTT, never overlaps requests, and ignores obsolete results/errors after pause or restart.');
})().catch(e=>{console.error(e);process.exitCode=1;});
