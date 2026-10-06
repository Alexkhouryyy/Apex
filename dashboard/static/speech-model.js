/* Speech detection with the Silero voice-activity model, in the browser.
   ricky0123/vad (ISC) on ONNX Runtime Web (MIT), installed and hash-checked by
   scripts/fetch_speech_model.py into /static/vendor/speech/. Nothing leaves
   the page: the model reads the microphone stream hands-free already has.

   attach() returns a handle hands-free reads every tick:
     take()  -> the highest speech probability since the last take (0..1);
                frames are 32 ms and ticks 50 ms, so a peak is never skipped
     at      -> performance.now() of the newest frame (stale = not running)
     destroy()
   The microphone and audio context belong to hands-free and are never stopped
   or closed here. */
(() => {
  'use strict';
  const BASE = '/static/vendor/speech/';
  let loading = null;
  const script = src => new Promise((resolve, reject) => {
    const tag = document.createElement('script');
    tag.src = src; tag.onload = resolve;
    tag.onerror = () => reject(Error('The speech model files did not load. Run Setup-Apex-Speech-Model.cmd again.'));
    document.head.appendChild(tag);
  });
  async function load(version) {
    loading = loading || (async () => {
      const v = encodeURIComponent(version || '');
      await script(BASE + 'ort.wasm.min.js?v=' + v);
      await script(BASE + 'bundle.min.js?v=' + v);
      if (!window.vad?.MicVAD) throw Error('The speech model did not start.');
    })();
    try { await loading; } catch (e) { loading = null; throw e; }
  }
  async function attach({context, stream, version}) {
    await load(version);
    let peak = 0;
    const handle = {at: 0, take() { const p = peak; peak = 0; return p; }};
    const mic = await window.vad.MicVAD.new({
      model: 'v5', baseAssetPath: BASE, onnxWASMBasePath: BASE, audioContext: context,
      getStream: async () => stream, pauseStream: async () => {}, resumeStream: async () => stream,
      startOnLoad: true,
      onFrameProcessed: p => { peak = Math.max(peak, p.isSpeech); handle.at = performance.now(); },
    });
    handle.destroy = () => { mic.destroy().catch(() => {}); };
    return handle;
  }
  window.ApexSpeechModel = {attach};
})();
