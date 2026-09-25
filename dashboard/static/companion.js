/* The compact client uses Apex's existing provider, memory and safety gates. */
(() => {
  'use strict';
  const els = Object.fromEntries([...document.querySelectorAll('[id]')].map(el => [el.id, el]));
  const $ = id => els[id];
  const root = $('companion');
  // --- Voice-turn timing (Pillar 1, docs/APEX_V2_PLAN.md) -------------------
  // Every stage is ms since you STOPPED TALKING, on one performance.now()
  // clock, so "Celine takes minutes" becomes a line saying which stage the
  // minutes are in. Measurement only: nothing here changes what a turn does.
  let timing = null;
  function timingStart(mode, speechEnd) {
    timing = {mode, t0: speechEnd, stages: {}, awaitingSend: true};
  }
  function mark(stage) {
    if (timing && !(stage in timing.stages)) timing.stages[stage] = Math.max(0, performance.now() - timing.t0);
  }
  function serverTiming(response, name, key) {
    if (!timing || key in timing.stages) return;
    const m = new RegExp(`(?:^|,)\\s*${name};dur=([0-9.]+)`).exec(response.headers.get('Server-Timing') || '');
    if (m) timing.stages[key] = Number(m[1]);
  }
  function timingLine(st) {
    const s = ms => ms == null ? '—' : (ms / 1000).toFixed(1) + 's';
    const head = st.first_sound != null ? `First sound after ${s(st.first_sound)}` : 'No sound this turn';
    return `${head} · transcript ${s(st.stt_done)} · first word ${s(st.first_token)} · ` +
      `reply written ${s(st.reply_done)} · voice ready ${s(st.tts_ready)}`;
  }
  async function timingFinish() {
    const t = timing; timing = null;
    if (!t || !Object.keys(t.stages).length) return;
    $('voice-timing').textContent = timingLine(t.stages); $('voice-timing').hidden = false;
    try {
      await request('/api/companion/timing', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode: t.mode, voice: $('voice').value, streamed: Boolean(t.streamed), stages: t.stages})});
    } catch (_) { /* Measurement must never break a turn. */ }
  }
  const drive = location.pathname === '/drive';
  const workspace = new URLSearchParams(location.search).get('workspace') === 'board' ? 'board' : null;
  let pendingRemote = null;
  try { pendingRemote = drive ? JSON.parse(localStorage.getItem('apex_remote_pending')) : null; } catch (_) {}
  const savePending = value => {
    pendingRemote = value;
    if (value) localStorage.setItem('apex_remote_pending', JSON.stringify(value));
    else localStorage.removeItem('apex_remote_pending');
    $('recover').hidden = !value;
  };
  let threadId = Number(localStorage.getItem('apex_companion_thread')) || null;
  let active = null, shareStream = null, recorder = null, micStream = null;
  let speechEpoch = 0, audio = null, audioUrl = null;
  let speechDraining = false, endPlayback = null;
  let recordingTimer = null, pip = null, recordingEpoch = 0;
  let speechBusy = false, hands = null, handsRequest = null, resumeTimer = null;
  let lastInteraction = Date.now(), lastCheck = Date.now();
  function resumeHands() {
    clearTimeout(resumeTimer);
    resumeTimer=setTimeout(() => {
      if (hands?.enabled && !active && !recorder && !pendingRemote && !speechBusy && !speechDraining && !audio) hands.resume();
      controls();
    }, 400);
  }
  function disableHands() {
    clearTimeout(resumeTimer); handsRequest?.abort(); handsRequest=null;
    hands?.stop(); $('hands-free').checked=false;
    $('mic-note').textContent='Tap to record, tap to send'; controls();
  }
  async function transcribeBlob(blob, signal) {
    const response=await request('/api/companion/transcribe?engine='+$('stt-engine').value,
      {method:'POST', body:blob, signal, headers:{'Content-Type':blob.type || 'audio/webm'}});
    const text = (await response.json()).text || '';
    mark('stt_done'); serverTiming(response, 'stt', 'stt_server');
    return text;
  }
  const token = () => localStorage.getItem('apex_token') || '';
  const headers = () => token() ? {Authorization: `Bearer ${token()}`} : {};
  function error(text = '') { $('error').textContent = text; $('error').hidden = !text; }
  function state(name, text) { root.className = name; $('status').textContent = text; }
  function controls() {
    const busy = speechBusy || speechDraining || Boolean(active) || Boolean(recorder) || Boolean(pendingRemote) || Boolean(hands?.busy);
    $('send').disabled = busy;
    $('new').disabled = busy;
    $('mode').disabled = busy;
    $('mic').disabled = speechBusy || speechDraining || Boolean(active) || Boolean(pendingRemote) || Boolean(hands?.enabled);
    $('review').disabled = busy || !shareStream;
    $('stop').disabled = !(busy || audio || speechBusy || hands?.enabled || $('check-in').checked || root.classList.contains('speaking'));
    $('recover').disabled = Boolean(active);
    $('jobs').disabled = Boolean(active) || Boolean(pendingRemote);
  }
  async function request(path, opts = {}) {
    const response = await fetch(path, {...opts, headers: {...headers(), ...opts.headers}});
    if (response.status === 401) {
      if (pip) pip.close();
      if (!$('login').open) $('login').showModal();
      throw new Error('Enter your Apex dashboard token to continue.');
    }
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      try { const data = await response.json(); detail = data.detail || data.error || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response;
  }
  function bubble(role, text) {
    $('welcome').remove();
    const wrapper = document.createElement('article'); wrapper.className = `message ${role}`;
    const label = document.createElement('div'); label.className = 'role'; label.textContent = role === 'user' ? 'YOU' : 'APEX';
    const content = document.createElement('div'); content.className = 'text'; content.textContent = text;
    wrapper.append(label, content); $('messages').append(wrapper); scroll();
    return {wrapper, content};
  }
  function scroll() { $('messages').scrollTop = $('messages').scrollHeight; }
  function stopSpeech() {
    speechEpoch++; speechBusy=false;
    live?.q.cancel();
    // Let an in-flight local generation finish; aborting HTTP does not stop GPU work.
    endPlayback?.(); endPlayback = null;
    window.speechSynthesis?.cancel();
    if (audio) { audio.pause(); audio = null; }
    if (audioUrl) { URL.revokeObjectURL(audioUrl); audioUrl = null; }
    if (!active && !recorder) state('', speechDraining ? 'Stopping audio · finishing the current voice section…' : 'Ready when you are.');
    resumeHands(); controls();
  }
  async function loadVoiceboxProfiles() {
    try {
      const data = await (await request('/api/voicebox/profiles')).json();
      const select = $('voicebox-profile');
      const chosen = localStorage.getItem('apex.voicebox.profile') || '';
      select.replaceChildren(new Option('Apex default voice', ''));
      for (const p of data.profiles) select.add(new Option(p.name, p.id));
      if ([...select.options].some(o => o.value === chosen)) select.value = chosen;
      $('voice-note').hidden = true;
    } catch (exc) {
      // No banner on purpose: Voicebox may simply not be open yet, and an
      // error banner on every page load would train the user to ignore the
      // banner. The list is refreshed from boot() once a token exists, and
      // again whenever the Voice dropdown changes.
      //
      // But not silent either. With no voice server the picker shows only
      // "Apex default voice", nothing speaks, and nothing said why — Celine
      // simply looked like she did not exist. A quiet note says what to start.
      // Not before login: a 401 is "sign in", not "start a server".
      const signIn = /token/i.test(exc?.message || '');
      $('voice-note').textContent = 'Voice server not reachable. For Celine, close Apex and start '
        + 'Start-Apex-Celine.cmd (or open the Voicebox app), then reload this page.';
      $('voice-note').hidden = signIn || $('voice').value !== 'voicebox';
    }
  }
  $('voicebox-profile').addEventListener('change', () => localStorage.setItem('apex.voicebox.profile', $('voicebox-profile').value));
  $('stream-speech').checked = localStorage.getItem('apex.speech.stream') !== '0';
  $('stream-speech').addEventListener('change', () => localStorage.setItem('apex.speech.stream', $('stream-speech').checked ? '1' : '0'));
  $('first-phrase').checked = localStorage.getItem('apex.speech.firstPhrase') !== '0';
  $('first-phrase').addEventListener('change', () => localStorage.setItem('apex.speech.firstPhrase', $('first-phrase').checked ? '1' : '0'));
  $('voice').addEventListener('change', loadVoiceboxProfiles);
  // Fired here for the tokenless-localhost case, and again from boot() after a
  // token is accepted. Without the second call the very first load of a
  // token-protected dashboard fetches this list BEFORE the login dialog is
  // answered, takes a 401, and never retries — so the Qwen profile dropdown
  // shows only 'Apex default' and a user's own cloned voice is invisible until
  // they happen to reload. The old comment said "retry when voice changes",
  // but Voicebox is already the DEFAULT voice, so that change never happens.
  loadVoiceboxProfiles();
  // One voice, two ways to feed it: `speak` with a finished reply, and
  // `startLiveSpeech` with one still being written. Same synthesis, same
  // playback, same timing marks — so the before/after comparison measures the
  // feeding, not two different players.
  function voiceFns(epoch) {
    const engine = $('voice').value, profile = $('voicebox-profile').value;
    const generate = async section => {
      mark('tts_start');
      const response = await request('/api/speak', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: section, engine, profile})});
      const blob = await response.blob();
      mark('tts_ready'); serverTiming(response, 'tts', 'tts_server');
      return blob;
    };
    const play = blob => new Promise((resolve, reject) => {
      if (epoch !== speechEpoch) { resolve(); return; }
      audioUrl = URL.createObjectURL(blob); audio = new Audio(audioUrl);
      const current = audio, url = audioUrl;
      const done = exc => {
        current.onended = current.onerror = null;
        if (audio === current) { audio = null; audioUrl = null; endPlayback = null; }
        URL.revokeObjectURL(url);
        if (exc) reject(exc); else resolve();
      };
      endPlayback = () => { current.pause(); done(); };
      current.onended = () => done();
      current.onplaying = () => mark('first_sound');
      current.onerror = () => done(new Error('Audio playback failed. Your reply remains on screen.'));
      state('speaking', 'Speaking · Stop ends playback'); controls();
      current.play().catch(exc => done(new Error(exc.name === 'NotAllowedError'
        ? 'Your browser blocked audio. Allow sound for this site, then send a short message.' : exc.message)));
    });
    return {generate, play};
  }
  function afterSpeech(epoch) {
    speechDraining = false;
    if (epoch === speechEpoch) stopSpeech();
    else { if (!active && !recorder) state('', 'Stopped. Ready for your next message.'); resumeHands(); controls(); }
  }
  // Whether this turn's reply can be spoken while it is written: a spoken,
  // synthesised voice, the setting on, and a turn the user asked for (a
  // proactive check-in may turn out to be NOTHING_TO_ADD, which must not be
  // half-said first).
  function canSpeakLive(automatic) {
    return !automatic && !drive && $('spoken').checked && $('stream-speech').checked
      && ['openai', 'voicebox'].includes($('voice').value);
  }
  let live = null;
  function startLiveSpeech() {
    hands?.pause(); speechBusy = true; speechDraining = true; controls();
    const epoch = speechEpoch;
    const {generate, play} = voiceFns(epoch);
    live = {epoch, q: window.ApexSpeechQueue.live(generate, play,
      {firstPhrase: $('first-phrase').checked})};
    return live;
  }
  async function finishLiveSpeech(handle) {
    try { await handle.q.done; }
    catch (exc) { if (handle.epoch === speechEpoch) error(exc.message); }
    finally { if (live === handle) live = null; afterSpeech(handle.epoch); }
  }
  async function speak(text) {
    stopSpeech();
    if (!$('spoken').checked || !text) { resumeHands(); return; }
    hands?.pause(); speechBusy=true;
    const epoch = speechEpoch;
    const finish = () => { if (epoch === speechEpoch) stopSpeech(); };
    try {
      if (['openai', 'voicebox'].includes($('voice').value)) {
        speechDraining = true;
        state('thinking', 'Preparing voice · the first section will play as soon as it is ready…'); controls();
        const {generate, play} = voiceFns(epoch);
        try {
          await window.ApexSpeechQueue.run(window.ApexSpeechQueue.chunks(text), generate, play,
            () => epoch !== speechEpoch);
        } catch (exc) {
          if (epoch === speechEpoch) error(exc.message);
        } finally {
          afterSpeech(epoch);
        }
      } else {
        if (!window.speechSynthesis) throw new Error('Device speech is unavailable. Choose OpenAI voice or read the reply.');
        const utterance = new SpeechSynthesisUtterance(text); utterance.rate = 1.02;
        utterance.onend = finish;
        utterance.onstart = () => mark('first_sound');
        utterance.onerror = event => { if (!['interrupted', 'canceled'].includes(event.error)) error('Device voice could not play.'); finish(); };
        state('speaking', 'Speaking · tap Stop or Talk to interrupt'); controls();
        window.speechSynthesis.speak(utterance);
      }
    } catch (exc) {
      if (epoch === speechEpoch) { error(exc.message); finish(); }
    }
  }
  function stopShare() {
    shareStream?.getTracks().forEach(track => track.stop()); shareStream = null;
    $('preview').srcObject = null; $('preview-panel').hidden = true;
    $('share').textContent = 'Share screen'; $('share').setAttribute('aria-pressed', 'false');
    $('screen-status').textContent = 'Screen off · nothing attached';
    $('screen-status').parentElement.classList.remove('sharing');
    $('check-in').checked = false; controls();
  }
  $('share').onclick = async () => {
    if (shareStream) { stopShare(); return; }
    error();
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw new Error('Screen sharing needs a supported desktop browser over HTTPS or localhost. Text and voice can still be used.');
      shareStream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
      shareStream.getVideoTracks()[0].addEventListener('ended', stopShare, {once: true});
      $('preview').srcObject = shareStream; await $('preview').play();
      $('preview-panel').hidden = false;
      $('share').textContent = 'Stop sharing'; $('share').setAttribute('aria-pressed', 'true');
      $('screen-status').textContent = 'Screen shared · snapshot with each message';
      $('screen-status').parentElement.classList.add('sharing'); controls();
    } catch (exc) { stopShare(); error(exc.message); }
  };
  function snapshot() {
    if (!shareStream) return null;
    const video = $('preview');
    if (!video.videoWidth || shareStream.getVideoTracks()[0].readyState !== 'live') throw new Error('The shared screen is not ready. Try again or stop sharing.');
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 1440 / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', .72);
  }
  async function refreshJobs() {
    const data = await (await request('/api/companion/jobs')).json();
    $('jobs').replaceChildren(new Option('Choose a task…', ''));
    for (const job of data.jobs) $('jobs').append(new Option(`${job.status} · ${job.message.slice(0, 70)}`, job.id));
  }
  async function remoteEvents(turn, body, event) {
    let job = body.existing
      ? await (await request(`/api/companion/jobs/${turn.id}`)).json()
      : await (await request('/api/companion/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
    event({type: 'start', thread_id: job.thread_id});
    savePending({turn_id: turn.id, existing: true, message: body.message || 'Reconnected task'});
    let seen = 0;
    while (true) {
      job = await (await request(`/api/companion/jobs/${turn.id}`)).json();
      for (const evidence of (job.evidence || []).slice(seen)) event(evidence);
      seen = (job.evidence || []).length;
      event({type: 'progress', text: job.text || 'Working on your Apex host…'});
      if (job.status !== 'running') {
        event({type: 'done', text: [job.text, job.error].filter(Boolean).join('\n\n') || 'Task ended without a reply.', interrupted: job.status !== 'done'});
        savePending(null); refreshJobs().catch(() => {}); return;
      }
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  }
  async function send(text, automatic = false, recovery = null, fromHands = false) {
    if (speechBusy || speechDraining || active || recorder || (hands?.busy && !fromHands) || (pendingRemote && !recovery) || !text.trim()) { timing = null; return; }
    // Only the send that a transcript triggered is a voice turn; a typed
    // message or a proactive check-in must not inherit a stale clock.
    if (timing?.awaitingSend && !automatic && !recovery) timing.awaitingSend = false; else timing = null;
    hands?.pause();
    if (!automatic) lastInteraction=Date.now();
    stopSpeech(); error();
    let image;
    try { image = snapshot(); } catch (exc) { error(exc.message); return; }
    const turn = {id: recovery?.turn_id || crypto.randomUUID(), stopped: false, done: false, text: '', automatic};
    active = turn; controls(); state('thinking', automatic ? 'Checking in on your screen…' : 'Thinking with you…');
    if (!automatic && !recovery) { bubble('user', text); $('message').value = ''; }
    const output = bubble('agent', automatic ? 'Checking your shared screen…' : 'Thinking…');
    // Speak as it writes: sections go to the voice as soon as a sentence is
    // complete, instead of after the whole reply (tool calls included).
    const speaking = canSpeakLive(automatic) ? startLiveSpeech() : null;
    if (timing) timing.streamed = Boolean(speaking);
    let streamed = '';
    function event(item) {
      if (item.type === 'start') {
        threadId = item.thread_id; localStorage.setItem('apex_companion_thread', String(threadId));
        if (turn.stopped) request(`/api/companion/cancel/${turn.id}`, {method: 'POST'}).catch(exc => error(exc.message));
      } else if (item.type === 'progress') {
        turn.text = item.text; output.content.textContent = item.text;
      } else if (item.type === 'token' && !turn.stopped) {
        mark('first_token');
        turn.text += item.text; if (!automatic) output.content.textContent = turn.text;
        streamed += item.text; speaking?.q.push(item.text);
      } else if (item.type === 'tool') {
        let detail = document.createElement('details');
        let summary = document.createElement('summary');
        summary.textContent = `${item.phase === 'start' ? 'Running' : 'Result'} · ${item.name}`;
        detail.append(summary);
        if (item.result) { let result = document.createElement('pre'); result.textContent = item.result; detail.append(result); }
        output.wrapper.append(detail);
      } else if (item.type === 'done') {
        mark('reply_done');
        turn.done = true; turn.stopped = turn.stopped || item.interrupted; turn.text = item.text;
        if (speaking) {
          // A reply that never streamed (an error, a fallback) is spoken whole;
          // one that did has already been fed, token by token.
          const final = (item.text || '').trim();
          if (turn.stopped) speaking.q.cancel();
          else speaking.q.end(final && !streamed.includes(final) ? final : '');
        }
        output.content.textContent = item.text;
        if (automatic && item.text.trim() === 'NOTHING_TO_ADD') output.wrapper.remove();
      } else if (item.type === 'error') { throw new Error(item.text); }
      scroll();
    }
    try {
      if (drive) {
        const body = recovery || {message: text, thread_id: threadId, turn_id: turn.id, mode: $('mode').value};
        savePending(body);
        await remoteEvents(turn, body, event);
      } else {
      const response = await request('/api/companion/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text, thread_id: threadId, turn_id: turn.id,
          screen_image: image, mode: automatic ? 'discuss' : $('mode').value, workspace, proactive: automatic})});
      const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = '';
      try {
        while (true) {
          const {value, done} = await reader.read();
          pending += done ? decoder.decode() : decoder.decode(value, {stream: true});
          let newline;
          while ((newline = pending.indexOf('\n')) >= 0) {
            const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
            if (line.trim()) event(JSON.parse(line));
          }
          if (done) break;
        }
      } finally { await reader.cancel(); reader.releaseLock(); }
      }
      if (!turn.done) throw new Error('Connection ended before Apex finished. Completed actions may still have taken effect.');
    } catch (exc) {
      output.content.textContent = (turn.text ? `${turn.text}\n\n` : '') + exc.message;
      error(drive ? `${exc.message} Reconnect to retrieve the task; do not submit it again as a new task.` : exc.message); turn.stopped = true;
      if (automatic) $('check-in').checked=false;
      if (hands?.enabled) disableHands();
      // Do not silently lose a message on a failed request.
      if (!automatic && !$('message').value) $('message').value = text;
    } finally {
      active = null;
      if (speaking && !turn.stopped && live === speaking) { state('speaking', 'Speaking · Stop ends playback'); speaking.q.end(); }
      else state('', pendingRemote ? 'Connection interrupted · task outcome not confirmed.' : turn.stopped ? 'Stopped. Ready for your next instruction.' : 'Ready when you are.');
      controls();
    }
    if (automatic && /^\[Safety\]/.test(turn.text)) $('check-in').checked=false;
    if (speaking) {
      if (turn.stopped) speaking.q.cancel();
      await finishLiveSpeech(speaking);
    } else if (!turn.stopped && turn.text.trim() !== 'NOTHING_TO_ADD') await speak(turn.text);
    else resumeHands();
    timingFinish();
  }
  async function stop() {
    disableHands(); $('check-in').checked=false;
    stopSpeech();
    if (recorder) { recordingEpoch++; if (recorder.state === 'recording') recorder.stop(); }
    if (active) {
      active.stopped = true; state('thinking', 'Stopping · an active tool may need to finish');
      try { await request(`/api/companion/cancel/${active.id}`, {method: 'POST'}); }
      catch (exc) { error(`Could not confirm the stop: ${exc.message}`); }
    } else if (pendingRemote) {
      try {
        const result = await (await request(`/api/companion/cancel/${pendingRemote.turn_id}`, {method: 'POST'})).json();
        error(result.cancel_requested ? 'Stop requested. Reconnect to retrieve the final result.' : 'No running task found. Reconnect to check whether it finished.');
      } catch (exc) { error(exc.message); }
    }
  }
  $('stop').onclick = stop;
  $('spoken').onchange = () => { if (!$('spoken').checked) stopSpeech(); };
  $('composer').onsubmit = event => { event.preventDefault(); send($('message').value); };
  $('message').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send($('message').value); } };
  $('review').onclick = () => send('Look at the screen I just shared. What matters here, and what would you recommend? Distinguish what you see from what you infer.');
  root.querySelectorAll('[data-prompt]').forEach(button => { button.onclick = () => send(button.dataset.prompt); });
  $('mode').onchange = () => { $('mode-hint').textContent = $('mode').value === 'work'
    ? 'Work: Apex can use action tools on the computer running Apex, with its existing safety gates. Describe the task you want it to perform.'
    : 'Discuss: look, research, and reason together. Action tools are disabled.'; };
  $('mic').onclick = async () => {
    if (recorder) { if (recorder.state === 'recording') recorder.stop(); return; }
    if (speechBusy || speechDraining || active || pendingRemote) return;
    stopSpeech(); error();
    const epoch = ++recordingEpoch;
    recorder = {state: 'requesting'}; controls();
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Microphone recording is unavailable. Use HTTPS or localhost in a supported browser.');
      micStream = await navigator.mediaDevices.getUserMedia({audio: true});
      if (epoch !== recordingEpoch) { micStream.getTracks().forEach(track => track.stop()); recorder = null; controls(); return; }
      const chunks = []; const local = new MediaRecorder(micStream); recorder = local;
      local.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      local.onstop = async () => {
        // Tap mode: tapping "Send voice" IS the end of speech.
        if (epoch === recordingEpoch) timingStart('tap', performance.now());
        clearTimeout(recordingTimer); micStream?.getTracks().forEach(track => track.stop()); micStream = null;
        $('mic').setAttribute('aria-pressed', 'false'); $('mic').textContent = '● Talk';
        try {
          if (epoch !== recordingEpoch) return;
          state('thinking', 'Transcribing your message…');
          const transcript = {text: await transcribeBlob(new Blob(chunks, {type: local.mimeType || 'audio/webm'}))};
          if (epoch !== recordingEpoch) return;
          if (!transcript.text) { timing = null; throw new Error('No speech was detected. Try again.'); }
          recorder = null; $('message').value = transcript.text; await send(transcript.text);
        } catch (exc) { error(exc.message); }
        finally { recorder = null; if (!active && !root.classList.contains('speaking')) state('', 'Ready when you are.'); controls(); }
      };
      local.start(); recordingTimer = setTimeout(() => { if (local.state === 'recording') local.stop(); }, 60000);
      state('listening', 'Listening · tap Talk again to send'); $('mic').setAttribute('aria-pressed', 'true'); $('mic').textContent = '■ Send voice'; controls();
    } catch (exc) { micStream?.getTracks().forEach(track => track.stop()); recorder = null; error(exc.message); controls(); }
  };
  $('hands-free').onchange = async () => {
    if (!$('hands-free').checked) { disableHands(); state('', 'Hands-free off.'); return; }
    if (speechBusy || speechDraining || active || recorder || pendingRemote) { $('hands-free').checked=false; error('Wait for the current turn to finish, then enable hands-free.'); return; }
    error(); stopSpeech(); $('spoken').checked=true;
    if (!window.ApexHandsFree) { $('hands-free').checked=false; error('Reload the companion to load hands-free controls.'); return; }
    hands = new window.ApexHandsFree({
      threshold: () => Number($('mic-threshold').value),
      onState: text => {
        if (text === 'ready') { resumeHands(); return; }
        if (!active && !speechBusy) state(text.startsWith('Listening') ? 'listening' : 'thinking', text);
        if (text.startsWith('Listening · pause')) lastInteraction=Date.now();
        $('mic-note').textContent=text; controls();
      },
      onError: exc => { disableHands(); error(exc.message); state('', 'Hands-free stopped.'); },
      onSegment: async (blob, epoch) => {
        // Hands-free: your speech ended at the last voiced frame, not when the
        // 1.2 s silence timer fired — that wait is part of what you feel.
        timingStart('hands_free', hands.lastVoice || performance.now());
        handsRequest=new AbortController();
        const text=await transcribeBlob(blob,handsRequest.signal);
        if (!hands.enabled || hands.epoch!==epoch) return;
        if (text.trim()) { $('message').value=text; await send(text, false, null, true); }
        else timing = null;
      }
    });
    await hands.start(); controls();
  };
  $('check-in').onchange = () => {
    lastCheck=Date.now();
    if ($('check-in').checked && !shareStream) { $('check-in').checked = false; error('Share a screen first to enable proactive comments.'); }
    else if ($('check-in').checked) error('Proactive comments send periodic screen snapshots to your selected AI provider and use credits. Apex stays quiet when there is nothing useful to add.');
    controls();
  };
  setInterval(() => {
    const now=Date.now(), interval=Number($('check-frequency').value)*1000;
    if (!$('check-in').checked || !shareStream || active || recorder || hands?.busy || speechBusy || speechDraining || root.classList.contains('speaking')) return;
    if (now-lastCheck<interval || now-lastInteraction<20000 || $('message').value.trim()) return;
    lastCheck=now;
    send('Optional screen comment.', true);
  }, 1000);
  $('new').onclick = () => { stopSpeech(); threadId = null; localStorage.removeItem('apex_companion_thread'); $('messages').replaceChildren($('welcome')); error(); };
  $('float').onclick = async () => {
    error();
    if (pip) { pip.close(); return; }
    try {
      if (!window.documentPictureInPicture) throw new Error('Floating mode needs a desktop browser with Document Picture-in-Picture support. You can use this page beside your work.');
      pip = await window.documentPictureInPicture.requestWindow({width: 440, height: 760});
      const style = pip.document.createElement('link'); style.rel = 'stylesheet'; style.href = new URL('/static/companion.css', location.origin).href;
      pip.document.head.append(style); pip.document.title = 'Apex companion'; pip.document.body.append(root);
      $('float').textContent = 'Return ↙';
      pip.addEventListener('pagehide', () => { document.body.prepend(root); pip = null; $('float').textContent = 'Float ↗'; }, {once: true});
    } catch (exc) { error(exc.message); }
  };
  async function boot() {
    const status = await (await request('/api/status')).json();
    if ($('login').open) $('login').close();
    // A successful boot clears whatever the last failure was. Without this the
    // 401 raised before the token was entered leaves its red banner on screen
    // for the rest of the session — the login handler cleared #login-error and
    // not this one — so a page that is working correctly reads as broken.
    error('');
    if (threadId) {
      const data = await (await request(`/api/chat/threads/${threadId}`)).json();
      $('messages').replaceChildren();
      for (const item of data.messages || []) bubble(item.role === 'user' ? 'user' : 'agent', item.text);
      if (!data.messages?.length) { threadId = null; localStorage.removeItem('apex_companion_thread'); $('messages').append($('welcome')); }
    }
    state('', status.agent_ready === false ? 'Apex agent is not connected yet. Start Apex, then try a message.' : 'Ready when you are.'); controls();
    loadVoiceboxProfiles();
    if (drive) {
      await refreshJobs();
      if (pendingRemote) send(pendingRemote.message || 'Reconnected task', false, pendingRemote);
    }
  }
  $('login-form').onsubmit = async event => {
    event.preventDefault(); localStorage.setItem('apex_token', $('token').value.trim());
    try { await boot(); $('token').value = ''; $('login-error').textContent = ''; }
    catch (exc) { $('login-error').textContent = exc.message; }
  };
  $('login').addEventListener('cancel', event => event.preventDefault());
  window.addEventListener('pagehide', () => { disableHands(); stopShare(); stopSpeech(); recordingEpoch++; micStream?.getTracks().forEach(track => track.stop()); });
  $('recover').onclick = () => { if (pendingRemote) send(pendingRemote.message || 'Reconnected task', false, pendingRemote); };
  $('refresh-jobs').onclick = () => refreshJobs().catch(exc => error(exc.message));
  $('jobs').onchange = () => {
    if ($('jobs').value) send('Reconnected task', false, {turn_id: $('jobs').value, existing: true});
  };
  if (drive) {
    document.body.classList.add('drive'); document.title = 'Apex · Car companion';
    $('remote-panel').hidden = false;
    root.querySelector('h1').textContent = 'Your Apex, along for the ride.';
    root.querySelector('.eyebrow').textContent = 'CONNECTED TO YOUR COMPUTER';
    for (const id of ['share', 'review', 'float', 'check-in', 'screen-status']) $(id).hidden = true;
    $('check-in').parentElement.hidden = true;
    $('capabilities').textContent = `Microphone: ${navigator.mediaDevices?.getUserMedia && window.MediaRecorder ? 'available to request' : 'unavailable — use text or your phone'} · Device speech: ${window.speechSynthesis ? 'available' : 'unavailable'}`;
    $('welcome').querySelector('h2').textContent = 'What should we work on?';
    $('welcome').querySelector('p').textContent = 'Talk through a decision, or switch to Work and ask Apex to run a task on your computer.';
    root.querySelector('footer').firstChild.textContent = 'Tasks run on your Apex host. ';
  }
  if (workspace) {
    root.querySelector('h1').textContent = 'Let’s shape it together.';
    $('screen-status').textContent = 'Your selected board object is attached to each message.';
  }
  boot().catch(exc => { state('', 'Apex is not connected yet.'); error(exc.message); });
})();

