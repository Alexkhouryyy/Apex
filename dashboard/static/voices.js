// Voices page (dashboard/voices.py): record or upload ~25 s of someone
// speaking, let Apex measure it and listen to the words, then save it for
// Apex's own Qwen voice servers (scripts/voice_library.py).
(() => {
  const $ = id => document.getElementById(id);
  const MAX_SECONDS = 90;
  const SCRIPT = name => `Hey, it's ${name || 'me'}. I'm recording my voice for Apex, so I'm just going to talk the way I normally would. Today was a good day. I worked on a few ideas, fixed some things, and learned something new. Honestly, the best part is when an idea finally clicks. What do you think we should build next? Let's keep it simple, take our time, and make it great.`;
  let audio = null, recorder = null, stream = null, started = 0, tick = null, meterFrame = null, scriptEdited = false, existing = [];

  const token = () => { try { return localStorage.getItem('apex_token') || ''; } catch (_) { return ''; } };
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, headers: {...(options.headers || {}), ...(token() ? {Authorization: 'Bearer ' + token()} : {})}});
    if (response.status === 401) { $('login').showModal(); throw new Error('Enter your Apex token.'); }
    const body = response.headers.get('content-type')?.includes('json') ? await response.json() : null;
    if (!response.ok) throw new Error(body?.detail || body?.error || `Request failed (${response.status})`);
    return body ?? response;
  }
  const say = (text, kind = '') => { $('message').textContent = text; $('message').className = 'message ' + kind; };

  async function refresh() {
    const data = await api('/api/voices');
    existing = data.voices.map(v => v.id);
    $('folder').textContent = data.dir;
    const s = data.server, el = $('server');
    if (s.kind === 'apex') { el.className = 'server good'; el.textContent = `Apex voice server running${s.streaming ? ' (fast, streaming)' : ''} · ${s.voices.length} voice${s.voices.length === 1 ? '' : 's'} ready. New voices work straight away, no restart.`; }
    else if (s.kind === 'voicebox-app') { el.className = 'server warn'; el.textContent = 'Apex is using the Voicebox app, which keeps its own voices. To speak with the voices on this page, start Apex with Start-Apex-Celine-Fast.cmd.'; }
    else { el.className = 'server warn'; el.textContent = 'The voice server is not running. You can still record and save; start Apex with Start-Apex-Celine-Fast.cmd to hear them.'; }
    const list = $('list'); list.replaceChildren();
    if (!data.voices.length) { const li = document.createElement('li'); li.className = 'muted'; li.textContent = 'No voices yet. Record the first one below.'; list.append(li); }
    for (const v of data.voices) {
      const li = document.createElement('li'), who = document.createElement('div'), b = document.createElement('b'), small = document.createElement('span');
      who.className = 'who'; b.textContent = v.name; small.textContent = `${v.words} words${v.removable ? '' : ' · original recording in Downloads'}`; who.append(b, small);
      const tryIt = document.createElement('button'); tryIt.textContent = 'Try'; tryIt.onclick = () => speak(v, tryIt);
      li.append(who, tryIt);
      if (v.removable) { const rm = document.createElement('button'); rm.textContent = 'Remove'; rm.onclick = () => remove(v); li.append(rm); }
      list.append(li);
    }
    syncReplace();
  }

  async function speak(v, button) {
    button.disabled = true; say(`${v.name} is warming up…`);
    try {
      const r = await api('/api/speak', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({engine: 'voicebox', profile: v.id, text: `Hi, this is ${v.name}. This is how I sound in Apex.`})});
      const url = URL.createObjectURL(await r.blob()), player = new Audio(url);
      player.onended = () => URL.revokeObjectURL(url); await player.play(); say('');
    } catch (e) { say(e.message, 'error'); } finally { button.disabled = false; }
  }
  async function remove(v) {
    if (!confirm(`Remove ${v.name}? It's moved to the .trash folder, so you can put it back.`)) return;
    try { await api('/api/voices/' + encodeURIComponent(v.id), {method: 'DELETE'}); say(`${v.name} removed.`, 'good'); refresh(); }
    catch (e) { say(e.message, 'error'); }
  }

  const slug = n => n.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 32);
  function syncReplace() { const taken = existing.includes(slug($('name').value)); $('replace-row').hidden = !taken; if (!taken) $('replace').checked = false; }
  $('name').oninput = () => { if (!scriptEdited) $('script').value = SCRIPT($('name').value.trim()); syncReplace(); };
  $('script').oninput = () => { scriptEdited = true; $('recheck').disabled = !audio; };
  $('script').value = SCRIPT('');

  // Recording: the raw microphone. Echo cancellation, noise suppression and
  // auto gain all reshape a voice, and the clone would copy that.
  async function startRecording() {
    stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1}});
    const type = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(t => window.MediaRecorder?.isTypeSupported?.(t)) || '';
    recorder = new MediaRecorder(stream, {...(type ? {mimeType: type} : {}), audioBitsPerSecond: 128000});
    const chunks = [];
    recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
    recorder.onstop = () => { stream.getTracks().forEach(t => t.stop()); cancelAnimationFrame(meterFrame); $('level').style.width = '0';
      use(new Blob(chunks, {type: recorder.mimeType || 'audio/webm'})); };
    const ctx = new AudioContext(), analyser = ctx.createAnalyser(), data = new Float32Array(1024);
    ctx.createMediaStreamSource(stream).connect(analyser);
    const meter = () => { analyser.getFloatTimeDomainData(data); let peak = 0; for (const x of data) peak = Math.max(peak, Math.abs(x));
      $('level').style.width = Math.min(100, peak * 100) + '%'; $('level').classList.toggle('hot', peak > .97); meterFrame = requestAnimationFrame(meter); };
    meter();
    recorder.start(250); started = performance.now();
    $('record').textContent = '■ Stop'; $('record').classList.add('on'); say('Recording… read the script at your normal pace.');
    tick = setInterval(() => { const s = (performance.now() - started) / 1000;
      $('timer').textContent = `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
      if (s >= MAX_SECONDS) stopRecording(); }, 200);
  }
  function stopRecording() { clearInterval(tick); recorder?.state === 'recording' && recorder.stop(); $('record').textContent = '● Record again'; $('record').classList.remove('on'); }
  $('record').onclick = () => recorder?.state === 'recording' ? stopRecording()
    : startRecording().catch(e => say(e.name === 'NotAllowedError' ? 'Allow the microphone for this page, then try again.' : e.message, 'error'));
  $('upload').onchange = () => { const f = $('upload').files[0]; if (f) use(f); };

  function use(blob) {
    audio = blob; $('playback').src = URL.createObjectURL(blob); $('playback').hidden = false;
    check();
  }
  const base64 = blob => new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = fail; r.readAsDataURL(blob); });
  const payload = async extra => JSON.stringify({audio: await base64(audio), transcript: $('script').value, ...extra});

  async function check() {
    $('save').disabled = true; $('recheck').disabled = true; say('Checking the recording and listening to the words…');
    try { show(await api('/api/voices/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: await payload({})})); say(''); }
    catch (e) { $('report').hidden = true; say(e.message, 'error'); }
    finally { $('recheck').disabled = !audio; }
  }
  $('recheck').onclick = check;

  function show(r) {
    const box = $('report'); box.replaceChildren(); box.hidden = false;
    const head = document.createElement('div');
    head.className = r.problems.length ? 'bad' : r.warnings.length ? 'warn' : 'good';
    head.textContent = r.problems.length ? 'Not usable yet:' : r.warnings.length ? `Usable · ${r.seconds} s. Could be better:` :
      `Clean recording · ${r.seconds} s${r.match != null ? ` · words match ${Math.round(r.match * 100)}%` : ''}. Ready to save.`;
    box.append(head);
    const items = [...r.problems.map(t => ['bad', t]), ...r.warnings.map(t => ['warn', t])];
    if (items.length) { const ul = document.createElement('ul'); for (const [k, t] of items) { const li = document.createElement('li'); li.className = k; li.textContent = t; ul.append(li); } box.append(ul); }
    if (r.heard && r.match != null && r.match < .97) {
      const p = document.createElement('div'), q = document.createElement('q'), fix = document.createElement('button');
      p.className = 'heard'; p.append('Apex heard: ', q); q.textContent = r.heard;
      fix.textContent = 'Use these words'; fix.onclick = () => { $('script').value = r.heard; scriptEdited = true; check(); };
      p.append(' ', fix); box.append(p);
    }
    $('save').disabled = r.problems.length > 0;
  }

  $('save').onclick = async () => {
    const name = $('name').value.trim();
    if (!name) { say('Give the voice a name first, like Alex.', 'error'); $('name').focus(); return; }
    if (!$('consent').checked) { say('Tick the box to confirm the speaker agreed.', 'error'); return; }
    $('save').disabled = true; say(`Saving ${name}…`);
    try {
      const r = await api('/api/voices', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: await payload({name, consent: true, replace: $('replace').checked})});
      say(`${r.name} saved. Press Try to hear it, or pick it in the Companion's voice list.`, 'good'); await refresh();
    } catch (e) { say(e.message, 'error'); $('save').disabled = false; }
  };

  $('login-form').onsubmit = async e => { e.preventDefault(); try { localStorage.setItem('apex_token', $('token').value.trim()); } catch (_) {}
    $('login').close(); refresh().catch(err => say(err.message, 'error')); };
  refresh().catch(e => say(e.message, 'error'));
})();
