const assert = require('node:assert/strict');
const {chunks, run} = require('../dashboard/static/speech_queue.js');
const defer = () => { let resolve, reject; const promise = new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
const tick = () => new Promise(r=>setImmediate(r));
(async () => {
  assert.deepEqual(chunks('Hi Alex. Ready?'), ['Hi Alex.', 'Ready?']);
  assert.deepEqual(chunks('It costs 3.5 dollars. Fine.'), ['It costs 3.5 dollars.', 'Fine.'],
    'a decimal point is not the end of a sentence');
  assert.deepEqual(chunks('1. Buy milk. 2. Call Sam.'), ['1. Buy milk.', '2. Call Sam.']);
  assert.deepEqual(chunks('Ask Dr. Lee and J. Smith, e.g. on Tuesday. Done.'),
    ['Ask Dr. Lee and J. Smith, e.g. on Tuesday.', 'Done.'], 'titles and initials do not end a sentence');
  assert.deepEqual(chunks('Really? Yes! Fine.'), ['Really?', 'Yes!', 'Fine.']);
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

  // --- take(): what is safe to speak before the reply has finished ---------
  const {take, live} = require('../dashboard/static/speech_queue.js');
  assert.deepEqual(take('Hi Alex. Rea'), {ready: ['Hi Alex.'], rest: ' Rea'});
  assert.deepEqual(take('It costs 3.'), {ready: [], rest: 'It costs 3.'},
    'a full stop with nothing after it may be "3.5" still arriving');
  assert.deepEqual(take('It costs 3.5 dollars. Next').ready, ['It costs 3.5 dollars.']);
  assert.deepEqual(take('See Dr. Lee'), {ready: [], rest: 'See Dr. Lee'}, 'streaming must not split a title');
  assert.deepEqual(take('1. Buy milk\n2. Call').ready, ['1. Buy milk'],
    'a bare list marker is not a sentence; a line break ends a list item');
  const fence = take('Run this. ```x = 1. y = 2. ');
  assert.deepEqual(fence.ready, ['Run this.']);
  assert.ok(fence.rest.includes('```x = 1.'), 'text inside an open code block must be held back');
  assert.ok(!take('Here. ```a. b.``` Done. ').ready.join(' ').includes('a.'), 'code is never read out');

  // --- live(): order, one synthesis in flight, drain on stop ---------------
  {
    const gens = [], plays = [], g = {}, p = {};
    const q = live(text => { gens.push(text); g[text] = defer(); return g[text].promise; },
                   value => { plays.push(value); p[value] = defer(); return p[value].promise; });
    q.push('First one. Sec'); await tick();
    assert.deepEqual(gens, ['First one.'], 'the first sentence must start before the reply ends');
    q.push('ond one. Third'); await tick();
    assert.deepEqual(gens, ['First one.'], 'only one synthesis in flight');
    g['First one.'].resolve('A1'); await tick(); await tick();
    assert.deepEqual(plays, ['A1']);
    assert.deepEqual(gens, ['First one.', 'Second one.'], 'next synthesis overlaps playback');
    g['Second one.'].resolve('A2'); await tick(); await tick();
    assert.deepEqual(plays, ['A1'], 'plays in order, never over the top');
    q.end(); await tick();
    assert.deepEqual(gens, ['First one.', 'Second one.'], 'at most one section ready ahead');
    p.A1.resolve(); await tick(); await tick();
    assert.deepEqual(plays, ['A1', 'A2']);
    assert.deepEqual(gens.at(-1), 'Third', 'end() flushes the unfinished last sentence');
    g.Third.resolve('A3'); await tick(); p.A2.resolve(); await tick(); await tick();
    p.A3.resolve(); await q.done;
    assert.deepEqual(plays, ['A1', 'A2', 'A3']);
  }
  {
    // end(tail): text the stream never carried (an error reply) is spoken too.
    const gens = [];
    const q = live(async t => { gens.push(t); return t; }, async () => {});
    q.end('Something went wrong.'); await q.done;
    assert.deepEqual(gens, ['Something went wrong.']);
  }
  {
    // Cancel while a synthesis runs: nothing more plays, but done waits for it.
    const g = defer(), plays = [];
    const q = live(() => g.promise, v => { plays.push(v); return Promise.resolve(); });
    q.push('Long one. More. '); await tick();
    q.cancel(); let finished = false; q.done.then(() => finished = true); await tick();
    assert.equal(finished, false, 'must not release while the server is still generating');
    g.resolve('stale'); await q.done;
    assert.deepEqual(plays, [], 'a cancelled reply must not play');
  }
  await assert.rejects(async () => { const q = live(async () => { throw Error('synthesis failed'); }, async () => {});
    q.push('Hi. '); q.end(); await q.done; }, /synthesis failed/);
  await assert.rejects(async () => { const q = live(async () => 1, async () => { throw Error('autoplay'); });
    q.push('Hi. '); q.end(); await q.done; }, /autoplay/);

  // --- the first phrase: start the voice before the first sentence ends ----
  const {firstClause} = require('../dashboard/static/speech_queue.js');
  const opening = 'Your dentist appointment is on Tuesday, at three in the afternoon with Dr. Lee';
  assert.deepEqual(firstClause(opening).ready, ['Your dentist appointment is on Tuesday,'],
    'a long opening sentence is started at its first phrase');
  assert.equal(firstClause('Sure, it is on Tuesday at three with the usual dentist in town. '), null,
    '"Sure," is too short to say on its own');
  assert.equal(firstClause('It is Tuesday, at three. More.'), null,
    'a short first sentence is not split — nothing to gain, and a break in the voice');
  assert.deepEqual(firstClause('There are three things to know here - the build, the tests and the relay. ').ready,
    ['There are three things to know here -']);
  assert.equal(firstClause('Run this: ```x = compute(a, b, c, d, e, f, g)```'), null, 'never near code');
  assert.equal(firstClause('Still writing the first sentence and no pause in it yet'), null);
  {
    // Only the first section is a phrase; everything after is whole sentences.
    const gens = [];
    const q = live(async t => { gens.push(t); return t; }, async () => {}, {firstPhrase: true});
    q.push('Your dentist appointment is on Tuesday, at three');
    await tick(); await tick();
    assert.deepEqual(gens, ['Your dentist appointment is on Tuesday,'],
      'the first phrase must go before its sentence has finished');
    q.push(' in the afternoon, with Dr. Lee, who moved offices last spring. Bring your card, the new one. ');
    q.end(); await q.done;
    assert.deepEqual(gens, ['Your dentist appointment is on Tuesday,',
      'at three in the afternoon, with Dr. Lee, who moved offices last spring.',
      'Bring your card, the new one.']);
  }
  {
    // Off by default: the same text, whole sentences only.
    const gens = [];
    const q = live(async t => { gens.push(t); return t; }, async () => {});
    q.push('Your dentist appointment is on Tuesday, at three in the afternoon. ');
    q.end(); await q.done;
    assert.deepEqual(gens, ['Your dentist appointment is on Tuesday, at three in the afternoon.']);
  }

  console.log('PASS: sentence bounds, early playback, single prefetch, stop/drain, synthesis and playback failures; '
    + 'live: first sentence before the reply ends, decimals/lists/code held correctly, order, one in flight, '
    + 'one ahead, tail spoken, cancel drains, failures surface; first phrase: long openings start at a clause, '
    + 'short ones and code are never split, only the first section.');
})().catch(e=>{console.error(e);process.exitCode=1;});
