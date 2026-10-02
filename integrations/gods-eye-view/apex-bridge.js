import { GEV_ACTION_SCHEMAS } from './src/voice/actionSchemas.js';

const prefix = '/world/engine/';
const allowed = new Set(GEV_ACTION_SCHEMAS.map(item => item.name));
const lifetime = new AbortController();
let recognition, audio, audioUrl, turnController, active = false;
const toolbar = document.createElement('div');
for (const heading of document.querySelectorAll('h1,h2')) {
  if (heading.textContent.trim() === "GOD'S EYE VIEW") heading.textContent = 'APEX WORLD VIEW';
}
toolbar.id = 'apex-world-toolbar';
toolbar.innerHTML = `<button type="button" id="apex-world-back">← Command</button><button type="button" id="apex-world-celine">Ask Celine</button><a href="/setup" target="_top">Setup</a><small style="align-self:center;color:#b9d1db">Powered by God’s Eye View</small>`;
const panel = document.createElement('section');
panel.id = 'apex-world-celine-panel';
panel.hidden = true;
panel.setAttribute('aria-label', 'Ask Celine about World View');
panel.innerHTML = `<header><strong>Celine · World View</strong><button type="button" id="apex-celine-close" aria-label="Close Celine">×</button></header>
<div id="apex-celine-output" role="log" aria-live="polite">Ask about this scene or tell Celine what to do.</div>
<form id="apex-celine-form"><label for="apex-celine-question">Message</label><textarea id="apex-celine-question" maxlength="4000" required></textarea><button id="apex-celine-send">Send</button></form>
<footer><button type="button" id="apex-celine-mic">Microphone</button><button type="button" id="apex-celine-stop">Stop</button><label><input type="checkbox" id="apex-celine-speak">Speak replies</label></footer>
<small>Uses your Apex model and local Celine voice. GEV MIC retains the original realtime voice option.</small>`;
const style = document.createElement('style');
style.textContent = `#apex-world-toolbar{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:1500;display:flex;gap:8px}#apex-world-toolbar button,#apex-world-toolbar a,#apex-world-celine-panel button{background:#112535;color:#d7f0ff;border:1px solid #549cbd;border-radius:6px;padding:8px;cursor:pointer}#apex-world-celine-panel{position:fixed;bottom:70px;left:12px;width:min(400px,calc(100vw - 24px));max-height:75vh;overflow:auto;z-index:1600;background:#0d1822f5;color:#e6f2fa;border:1px solid #549cbd;border-radius:12px;padding:16px;box-sizing:border-box;font:14px system-ui}#apex-world-celine-panel[hidden]{display:none}#apex-world-celine-panel header,#apex-world-celine-panel footer{display:flex;gap:10px;align-items:center;justify-content:space-between}#apex-celine-output{white-space:pre-wrap;max-height:35vh;overflow:auto;margin:16px 0}#apex-celine-question{box-sizing:border-box;width:100%;min-height:70px;background:#09121c;color:#e6f2fa;border:1px solid #549cbd;border-radius:6px}#apex-world-celine-panel small{display:block;margin-top:12px;color:#9cb8cb}`;
document.head.append(style);
document.body.append(toolbar, panel);
const $ = id => document.getElementById(id);
const output = text => { $('apex-celine-output').textContent += '\n\n' + text; $('apex-celine-output').scrollTop = $('apex-celine-output').scrollHeight; };
function stopAudio() {
  audio?.pause(); audio = null;
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = null;
}
function stop() { turnController?.abort(); recognition?.abort(); stopAudio(); }
async function request(body, signal) {
  const response = await fetch(prefix + 'apex/assistant', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Celine is unavailable.');
  return result;
}
async function speak(text, signal) {
  const response = await fetch(prefix + 'apex/speak', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:text.slice(0,4000), engine:'voicebox'}), signal});
  if (!response.ok) throw new Error('Celine’s local voice service is unavailable. Start Apex with Celine to hear replies.');
  const blob = await response.blob();
  signal.throwIfAborted(); stopAudio();
  audioUrl = URL.createObjectURL(blob); audio = new Audio(audioUrl);
  audio.addEventListener('ended', stopAudio, {once:true});
  await audio.play();
}
async function ask(message) {
  if (active || !message.trim()) return;
  const voiceCommands = window.__godsEyeView?.voiceCommands;
  const runner = voiceCommands?.runner;
  if (typeof runner !== 'function') {output('The globe is still starting. Try again when it is ready.'); return;}
  voiceCommands.stop?.();
  active = true; $('apex-celine-send').disabled = true;
  turnController = new AbortController();
  const signal = AbortSignal.any([turnController.signal, lifetime.signal]);
  output('You: ' + message);
  try {
    const context = await runner('get_scene_context', {}, {signal});
    let result = await request({message, context}, signal);
    for (let round = 0; !result.done && round < 8; round++) {
      const results = [];
      for (const call of result.actions) {
        signal.throwIfAborted();
        if (!allowed.has(call.name)) throw new Error('Celine requested an unsupported world action.');
        let value;
        try {value = await runner(call.name, call.args, {signal, isCurrent:() => !signal.aborted});}
        catch (error) {value = {ok:false, error:error.message};}
        signal.throwIfAborted();
        results.push({id:call.id, result:value});
        output(call.name.replaceAll('_',' ') + ': ' + (value?.ok === false ? (value.error || 'unavailable') : 'result received'));
      }
      result = await request({turn_id:result.turn_id, results}, signal);
    }
    if (!result.done) throw new Error('World action limit reached. Ask a shorter follow-up.');
    output('Celine: ' + (result.text || 'No answer was returned.'));
    if ($('apex-celine-speak').checked && result.text) await speak(result.text, signal);
  } catch (error) {output(error.name === 'AbortError' ? 'Stopped.' : error.message);}
  finally {active = false; $('apex-celine-send').disabled = false;}
}
$('apex-world-back').onclick = () => {
  stop();
  if (window.parent !== window) window.parent.postMessage({type:'apex.world.close'}, location.origin);
  else location.assign('/');
};
$('apex-world-celine').onclick = () => {panel.hidden = false; $('apex-celine-question').focus();};
$('apex-celine-close').onclick = () => {stop(); panel.hidden = true;};
$('apex-celine-stop').onclick = stop;
$('apex-celine-form').onsubmit = event => {event.preventDefault(); const input = $('apex-celine-question'); const text = input.value; input.value = ''; ask(text);};
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!Recognition) {$('apex-celine-mic').disabled = true; $('apex-celine-mic').title = 'Speech recognition is unavailable in this browser.';}
else {
  recognition = new Recognition(); recognition.continuous = false; recognition.interimResults = false;
  recognition.onresult = event => ask(event.results[0][0].transcript);
  recognition.onerror = event => output('Microphone: ' + event.error);
  $('apex-celine-mic').onclick = () => {try {recognition.start();} catch (error) {output(error.message);}};
}
let engineVersion = null;
const versionTimer = setInterval(async () => {
  if (document.hidden || lifetime.signal.aborted) return;
  try {
    const response = await fetch(prefix + 'apex-version', {signal:lifetime.signal, cache:'no-store'});
    if (!response.ok) return;
    const {version} = await response.json();
    if (engineVersion !== null && version !== engineVersion) location.reload();
    engineVersion = version;
  } catch (_) {}
},3000);
window.addEventListener('pagehide', () => {stop(); lifetime.abort(); clearInterval(versionTimer);}, {once:true});
