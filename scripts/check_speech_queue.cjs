const assert = require('node:assert/strict');
const {chunks, run} = require('../dashboard/static/speech_queue.js');
const defer = () => { let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
const tick = () => new Promise(r=>setImmediate(r));
(async () => {
  assert.deepEqual(chunks('Hi Alex. Ready?'), ['Hi Alex.', 'Ready?']);
  assert.ok(chunks('word '.repeat(200)).every(x=>x.length<=180));
  assert.ok(!chunks('Here is code. ```secret()``` Done.').join(' ').includes('secret'));
  const first = defer(), second = defer(), playback = defer();
  const requested = [], played = []; let cancelled = false;
  const job = run(['first', 'second', 'third'], text => {
    requested.push(text); return text==='first' ? first.promise : second.promise;
  }, value => { played.push(value); return playback.promise; }, () => cancelled);
  await tick(); assert.deepEqual(requested,['first']);
  first.resolve('audio1'); await tick();
  assert.deepEqual(played,['audio1']); assert.deepEqual(requested,['first','second']);
  cancelled=true; playback.resolve(); let done=false; job.then(()=>done=true);
  await tick(); assert.equal(done,false); // don't unlock while server is generating
  second.resolve('stale'); await job;
  assert.deepEqual(played,['audio1']); assert.deepEqual(requested,['first','second']);
  await assert.rejects(run(['bad'], async()=>{throw Error('synthesis failed');},()=>assert.fail(),()=>false),/synthesis failed/);
  await assert.rejects(run(['ok'], async()=>1, async()=>{throw Error('autoplay');},()=>false),/autoplay/);
  console.log('PASS: sentence bounds, early playback, single prefetch, stop/drain, synthesis and playback failures.');
})().catch(e=>{console.error(e);process.exitCode=1;});
