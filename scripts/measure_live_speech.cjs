// Before/after for "Speak as it writes" and "Start on the first phrase", measured with the real companion code
// and the real Pillar 1 timing instrument, against SIMULATED delays.
//
//   NODE_PATH=<jsdom> node scripts/measure_live_speech.cjs
//
// What this can and cannot tell you: the page code, the speech queue and the
// timing marks are the real ones; the server, the model and the voice are fakes
// with the delays printed below. So it measures what the CHANGE does to the
// shape of a turn — not how fast your laptop is. Your own numbers come from
// `python -m agent.voice_timing` after real turns with the setting off and on.
const {JSDOM} = require('jsdom');
const fs = require('fs');
const base = require('path').join(__dirname, '..', 'dashboard', 'static') + require('path').sep;

// Real-time delays, divided by SCALE so a run takes seconds, not minutes.
const SCALE = 20;
const DELAYS = {
  transcribe: 600,          // ms, local Whisper on a short utterance
  firstToken: 900,          // ms, model time to first token
  perWord: 60,              // ms, model writing one word (~17 words/s)
  toolCall: 6000,           // ms, one tool call mid-reply
  ttsBase: 800, ttsPerChar: 25,   // ms, synthesising one section (local, non-streaming)
};
const SCENARIOS = {
  'short answer (2 sentences)': {sentences: ['Your dentist is on Tuesday.', 'It is at three in the afternoon.']},
  'long answer (6 sentences)': {sentences: ['There are three things to know.', 'First, the build passed this morning.',
    'Second, two tests are slow.', 'Third, the relay is not deployed yet.', 'I would start with the relay.',
    'It is the gate for everything else.']},
  'tool call mid-reply': {pre: ['Let me check your calendar.'], tool: true,
    sentences: ['You have the dentist on Tuesday at three.', 'Nothing else that day.']},
  'long first sentence': {sentences: ['Your dentist appointment is on Tuesday, at three in the afternoon, '
    + 'with Dr. Lee at the new office on Main Street.', 'Bring your insurance card.']},
};
const MODES = {
  'whole reply': {stream: false, phrase: false},
  'as it writes': {stream: true, phrase: false},
  '+ first phrase': {stream: true, phrase: true},
};
const sleep = ms => new Promise(r => setTimeout(r, ms / SCALE));

async function measure(scenario, mode) {
  const dom = new JSDOM(fs.readFileSync(base + 'companion.html', 'utf8'),
    {url: 'http://localhost:7860/companion', runScripts: 'outside-only'});
  const w = dom.window, $ = id => w.document.getElementById(id);
  w.TextDecoder = TextDecoder;
  w.localStorage.setItem('apex_token', 'tok');
  w.localStorage.setItem('apex.speech.stream', mode.stream ? '1' : '0');
  w.localStorage.setItem('apex.speech.firstPhrase', mode.phrase ? '1' : '0');
  w.speechSynthesis = {cancel() {}, speak() {}};
  w.SpeechSynthesisUtterance = class {};
  w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
  w.navigator.mediaDevices = {getUserMedia: async () => ({getTracks: () => [{stop() {}}]})};
  w.MediaRecorder = class { constructor() { this.state = 'inactive'; }
    start() { this.state = 'recording'; } stop() { this.state = 'inactive'; this.ondataavailable?.({data: new w.Blob(['x'])}); this.onstop?.(); } };
  // The page's clock must run at the same scaled speed as the fakes.
  const t0 = performance.now();
  w.performance.now = () => (performance.now() - t0) * SCALE;
  w.Audio = class { constructor() {} pause() {}
    play() { setTimeout(() => { this.onplaying?.(); setTimeout(() => this.onended?.(), 2000 / SCALE); }, 1); return Promise.resolve(); } };
  let record = null;
  const lines = [];
  w.fetch = async (path, opts = {}) => {
    if (path === '/api/status') return Response.json({agent_ready: true});
    if (path === '/api/voicebox/profiles') return Response.json({profiles: []});
    if (path.startsWith('/api/companion/transcribe')) { await sleep(DELAYS.transcribe); return Response.json({text: 'q'}); }
    if (path === '/api/companion/chat') {
      return new Response(new ReadableStream({async start(c) {
        const send = o => c.enqueue(new TextEncoder().encode(JSON.stringify(o) + '\n'));
        send({type: 'start', thread_id: 1});
        await sleep(DELAYS.firstToken);
        // Word by word, as a model streams — a whole sentence at once would
        // hide exactly what starting on the first phrase is for.
        const write = async text => { for (const word of text.split(' ')) {
          send({type: 'token', text: word + ' '}); await sleep(DELAYS.perWord); } };
        for (const s of scenario.pre || []) await write(s);
        if (scenario.tool) { send({type: 'tool', phase: 'start', name: 'calendar'}); await sleep(DELAYS.toolCall); }
        for (const s of scenario.sentences) await write(s);
        send({type: 'done', text: scenario.sentences.join(' ')}); c.close();
      }}));
    }
    if (path === '/api/speak') {
      const text = JSON.parse(opts.body).text;
      await sleep(DELAYS.ttsBase + DELAYS.ttsPerChar * text.length);
      return new Response(new Blob(['RIFF']));
    }
    if (path === '/api/companion/timing') { record = JSON.parse(opts.body); return Response.json({ok: true}); }
    throw Error(path);
  };
  w.eval(fs.readFileSync(base + 'speech_queue.js', 'utf8'));
  w.eval(fs.readFileSync(base + 'companion.js', 'utf8'));
  await sleep(200);
  $('mic').click(); await sleep(100); $('mic').click();
  for (let i = 0; i < 2000 && !record; i++) await new Promise(r => setTimeout(r, 10));
  dom.window.close();
  if (!record) throw Error('no timing record for ' + JSON.stringify(scenario));
  return record.stages;
}

(async () => {
  const f = ms => (ms / 1000).toFixed(1).padStart(5) + 's';
  console.log('Simulated delays (real time):', JSON.stringify(DELAYS));
  console.log('\n  first sound after you stop talking');
  console.log('  ' + 'scenario'.padEnd(30) + Object.keys(MODES).map(k => k.padStart(16)).join(''));
  for (const [name, sc] of Object.entries(SCENARIOS)) {
    const row = [];
    for (const mode of Object.values(MODES)) row.push((await measure(sc, mode)).first_sound);
    console.log('  ' + name.padEnd(30) + row.map(v => f(v).padStart(16)).join(''));
  }
  console.log('\nNot your laptop: these delays are made up to show the shape of the change. '
    + 'Your real before/after: python -m agent.voice_timing');
})().catch(e => { console.error(e); process.exit(1); });
