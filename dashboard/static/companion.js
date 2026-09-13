/* The compact client uses Apex's existing provider, memory and safety gates. */
(() => {
  'use strict';
  const els = Object.fromEntries([...document.querySelectorAll('[id]')].map(el => [el.id, el]));
  const $ = id => els[id];
  const root = $('companion');
  let threadId = Number(localStorage.getItem('apex_companion_thread')) || null;
  let active = null, shareStream = null, recorder = null, micStream = null;
  let speechEpoch = 0, audio = null, audioUrl = null, speechRequest = null;
  let recordingTimer = null, checkins = 0, pip = null, recordingEpoch = 0;
  const token = () => localStorage.getItem('apex_token') || '';
  const headers = () => token() ? {Authorization: `Bearer ${token()}`} : {};
  function error(text = '') { $('error').textContent = text; $('error').hidden = !text; }
  function state(name, text) { root.className = name; $('status').textContent = text; }
  function controls() {
    const busy = Boolean(active) || Boolean(recorder);
    $('send').disabled = busy;
    $('new').disabled = busy;
    $('mode').disabled = busy;
    $('mic').disabled = Boolean(active);
    $('review').disabled = busy || !shareStream;
    $('stop').disabled = !(busy || audio || root.classList.contains('speaking'));
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
    speechEpoch++;
    speechRequest?.abort(); speechRequest = null;
    window.speechSynthesis?.cancel();
    if (audio) { audio.pause(); audio = null; }
    if (audioUrl) { URL.revokeObjectURL(audioUrl); audioUrl = null; }
    if (!active && !recorder) state('', 'Ready when you are.');
    controls();
  }
  async function speak(text) {
    stopSpeech();
    if (!$('spoken').checked || !text) return;
    const epoch = speechEpoch;
    const finish = () => { if (epoch === speechEpoch) stopSpeech(); };
    try {
      if ($('voice').value === 'openai') {
        speechRequest = new AbortController();
        const response = await request('/api/speak', {method: 'POST', signal: speechRequest.signal,
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
        const blob = await response.blob();
        if (epoch !== speechEpoch) return;
        audioUrl = URL.createObjectURL(blob); audio = new Audio(audioUrl);
        audio.onended = finish; audio.onerror = () => { error('Audio playback failed. Your reply is available as text.'); finish(); };
        state('speaking', 'Speaking · tap Stop or Talk to interrupt'); controls();
        await audio.play();
      } else {
        if (!window.speechSynthesis) throw new Error('Device speech is unavailable. Choose OpenAI voice or read the reply.');
        const utterance = new SpeechSynthesisUtterance(text); utterance.rate = 1.02;
        utterance.onend = finish;
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
  async function send(text, automatic = false) {
    if (active || recorder || !text.trim()) return;
    stopSpeech(); error();
    let image;
    try { image = snapshot(); } catch (exc) { error(exc.message); return; }
    const turn = {id: crypto.randomUUID(), stopped: false, done: false, text: ''};
    active = turn; controls(); state('thinking', automatic ? 'Checking in on your screen…' : 'Thinking with you…');
    if (!automatic) { bubble('user', text); $('message').value = ''; }
    const output = bubble('agent', automatic ? 'Checking your shared screen…' : 'Thinking…');
    function event(item) {
      if (item.type === 'start') {
        threadId = item.thread_id; localStorage.setItem('apex_companion_thread', String(threadId));
        if (turn.stopped) request(`/api/companion/cancel/${turn.id}`, {method: 'POST'}).catch(exc => error(exc.message));
      } else if (item.type === 'token' && !turn.stopped) {
        turn.text += item.text; if (!automatic) output.content.textContent = turn.text;
      } else if (item.type === 'tool') {
        let detail = document.createElement('details');
        let summary = document.createElement('summary');
        summary.textContent = `${item.phase === 'start' ? 'Running' : 'Result'} · ${item.name}`;
        detail.append(summary);
        if (item.result) { let result = document.createElement('pre'); result.textContent = item.result; detail.append(result); }
        output.wrapper.append(detail);
      } else if (item.type === 'done') {
        turn.done = true; turn.stopped = turn.stopped || item.interrupted; turn.text = item.text;
        output.content.textContent = item.text;
        if (automatic && item.text.trim() === 'NOTHING_TO_ADD') output.wrapper.remove();
      } else if (item.type === 'error') { throw new Error(item.text); }
      scroll();
    }
    try {
      const response = await request('/api/companion/chat', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text, thread_id: threadId, turn_id: turn.id,
          screen_image: image, mode: automatic ? 'discuss' : $('mode').value})});
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
      if (!turn.done) throw new Error('Connection ended before Apex finished. Completed actions may still have taken effect.');
    } catch (exc) {
      output.content.textContent = (turn.text ? `${turn.text}\n\n` : '') + exc.message;
      error(exc.message); turn.stopped = true;
      // Do not silently lose a message on a failed request.
      if (!automatic && !$('message').value) $('message').value = text;
    } finally {
      active = null; state('', turn.stopped ? 'Stopped. Ready for your next instruction.' : 'Ready when you are.'); controls();
    }
    if (!turn.stopped && turn.text.trim() !== 'NOTHING_TO_ADD') await speak(turn.text);
  }
  async function stop() {
    stopSpeech();
    if (recorder) { recordingEpoch++; if (recorder.state === 'recording') recorder.stop(); }
    if (active) {
      active.stopped = true; state('thinking', 'Stopping · an active tool may need to finish');
      try { await request(`/api/companion/cancel/${active.id}`, {method: 'POST'}); }
      catch (exc) { error(`Could not confirm the stop: ${exc.message}`); }
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
    if (active) return;
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
        clearTimeout(recordingTimer); micStream?.getTracks().forEach(track => track.stop()); micStream = null;
        $('mic').setAttribute('aria-pressed', 'false'); $('mic').textContent = '● Talk';
        try {
          if (epoch !== recordingEpoch) return;
          state('thinking', 'Transcribing your message…');
          const type = local.mimeType || 'audio/webm'; const data = new FormData();
          data.append('file', new Blob(chunks, {type}), type.includes('mp4') ? 'speech.mp4' : 'speech.webm');
          const response = await request('/api/transcribe', {method: 'POST', body: data});
          const transcript = await response.json();
          if (epoch !== recordingEpoch) return;
          if (!transcript.text) throw new Error('No speech was detected. Try again.');
          recorder = null; $('message').value = transcript.text; await send(transcript.text);
        } catch (exc) { error(exc.message); }
        finally { recorder = null; if (!active && !root.classList.contains('speaking')) state('', 'Ready when you are.'); controls(); }
      };
      local.start(); recordingTimer = setTimeout(() => { if (local.state === 'recording') local.stop(); }, 60000);
      state('listening', 'Listening · tap Talk again to send'); $('mic').setAttribute('aria-pressed', 'true'); $('mic').textContent = '■ Send voice'; controls();
    } catch (exc) { micStream?.getTracks().forEach(track => track.stop()); recorder = null; error(exc.message); controls(); }
  };
  $('check-in').onchange = () => {
    checkins = 0;
    if ($('check-in').checked && !shareStream) { $('check-in').checked = false; error('Share a screen first to enable check-ins.'); }
    else if ($('check-in').checked) error('Check-ins send one snapshot per minute, up to 10 checks. They use AI credits and always run in Discuss mode.');
  };
  setInterval(() => {
    if (!$('check-in').checked || !shareStream || active || recorder || root.classList.contains('speaking')) return;
    if (++checkins >= 10) $('check-in').checked = false;
    send('Optional screen check-in: mention only one new, concrete issue or useful next step visible in this snapshot and relevant to our conversation. Do not repeat previous advice. Do not use tools. If there is nothing useful to add, respond exactly NOTHING_TO_ADD.', true);
  }, 60000);
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
    if (threadId) {
      const data = await (await request(`/api/chat/threads/${threadId}`)).json();
      $('messages').replaceChildren();
      for (const item of data.messages || []) bubble(item.role === 'user' ? 'user' : 'agent', item.text);
      if (!data.messages?.length) { threadId = null; localStorage.removeItem('apex_companion_thread'); $('messages').append($('welcome')); }
    }
    state('', status.agent_ready === false ? 'Apex agent is not connected yet. Start Apex, then try a message.' : 'Ready when you are.'); controls();
  }
  $('login-form').onsubmit = async event => {
    event.preventDefault(); localStorage.setItem('apex_token', $('token').value.trim());
    try { await boot(); $('token').value = ''; $('login-error').textContent = ''; }
    catch (exc) { $('login-error').textContent = exc.message; }
  };
  $('login').addEventListener('cancel', event => event.preventDefault());
  window.addEventListener('pagehide', () => { stopShare(); stopSpeech(); recordingEpoch++; micStream?.getTracks().forEach(track => track.stop()); });
  boot().catch(exc => { state('', 'Apex is not connected yet.'); error(exc.message); });
})();
