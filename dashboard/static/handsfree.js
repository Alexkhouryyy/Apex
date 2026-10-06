/* Browser-local energy detection. Only completed speech segments are uploaded.
   Capture pauses while Apex thinks and speaks. With `bargeWatch` the paused
   microphone is still measured: talking over the reply (louder than the
   normal threshold by BARGE_FACTOR, for BARGE_MS) starts a capture at once and
   calls onBarge, so the page can stop the reply and hear the rest of what you
   say. The higher bar is because her own voice leaks into the microphone
   through speakers; with headphones it does not.

   What counts as speech: loudness over the threshold, or, when `loadModel`
   gives a running speech model (speech-model.js), the model's probability.
   The model tells a voice from a fan, traffic or a keyboard, so the wait after
   you stop can be shorter (MODEL_SILENCE_MS instead of 1.2 s). Barge-in still
   also needs the 3x loudness, because her own voice is speech too. If the
   model fails or stalls, the same tick falls back to loudness. */
(() => {
  'use strict';
  // 200 ms of loud voice confirms an interruption. The level is read every
  // 50 ms, so speech may have begun up to 50 ms before it is first seen: the
  // reported onset counts that, and the worst case is 250 ms, inside
  // Pillar 1's 300 ms budget to stop.
  const BARGE_FACTOR=3, BARGE_MS=200, FRAME_MS=50;
  // Model thresholds: a higher bar to start (noise rarely reaches it) and a
  // lower one to keep going (soft word endings stay inside the turn). The model
  // reports every 32 ms; a gap over STALE_MS means it is not running.
  const START_P=.5, KEEP_P=.25, STALE_MS=300, SILENCE_MS=1200, MODEL_SILENCE_MS=700;
  class HandsFree {
    constructor({onSegment, onState, onError, threshold = () => .018, bargeWatch = () => false, onBarge = () => {}, loadModel = null}) {
      Object.assign(this, {onSegment, onState, onError, threshold, bargeWatch, onBarge, loadModel});
      this.detector='loudness'; this.modelProblem='';
      this.enabled=false; this.epoch=0; this.paused=true; this.heard=false;
    }
    async start() {
      this.stop(); this.enabled=true; const epoch=this.epoch;
      try {
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !(window.AudioContext || window.webkitAudioContext)) throw Error('Hands-free needs microphone recording and Web Audio on HTTPS or localhost.');
        this.onState('Requesting microphone…');
        const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
        if (!this.enabled || epoch!==this.epoch) { stream.getTracks().forEach(t=>t.stop()); return; }
        this.stream=stream;
        stream.getAudioTracks().forEach(t=>t.addEventListener('ended',()=>this.fail(Error('Microphone disconnected. Turn hands-free on again when ready.')), {once:true}));
        this.context=new (window.AudioContext || window.webkitAudioContext)();
        await this.context.resume();
        if (!this.enabled || epoch!==this.epoch) return;
        this.source=this.context.createMediaStreamSource(stream);
        this.analyser=this.context.createAnalyser(); this.analyser.fftSize=2048;
        this.source.connect(this.analyser); // Never connect the microphone to speakers.
        this.samples=new Float32Array(this.analyser.fftSize);
        this.detector='loudness'; this.modelProblem='';
        if (this.loadModel) {
          try {
            this.onState('Loading the speech model…');
            const model=await this.loadModel(this.context, stream);
            if (!this.enabled || epoch!==this.epoch) { model?.destroy(); return; }
            if (model) { this.model=model; this.detector='model'; }
          } catch(e) { this.modelProblem=e.message || 'The speech model could not start.'; }
        }
        this.paused=false; this.capture();
        this.timer=setInterval(()=>{try{this.tick();}catch(e){this.fail(e);}},50);
      } catch(e) { if (epoch===this.epoch) this.fail(e); }
    }
    get busy() { return this.heard || this.transcribing; }
    capture() {
      if (!this.enabled || this.paused || !this.stream || this.recording) return;
      const epoch=this.epoch, chunks=[];
      const record=new MediaRecorder(this.stream);
      this.recording=record; this.heard=false; this.voicedMs=0; this.started=performance.now(); this.last=this.started;
      record.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      record.onerror=()=>this.fail(Error('Microphone recording failed.'));
      record.onstop=async()=>{
        if (this.recording===record) this.recording=null;
        if (!this.enabled || epoch!==this.epoch || record.discard) { if(this.enabled&&!this.paused)this.capture(); return; }
        this.transcribing=true; this.paused=true; this.onState('Transcribing your message…');
        try {
          if(chunks.length) await this.onSegment(new Blob(chunks,{type:record.mimeType||'audio/webm'}), epoch);
        } catch(e) { if(epoch===this.epoch)this.fail(e); }
        finally { this.transcribing=false; if(epoch===this.epoch&&this.enabled)this.onState('ready'); }
      };
      record.start(); this.onState('Listening · speak naturally' + this.label());
    }
    level() {
      this.analyser.getFloatTimeDomainData(this.samples);
      return Math.sqrt(this.samples.reduce((n,v)=>n+v*v,0)/this.samples.length);
    }
    label() { return this.detector==='model' ? ' · speech model' : this.modelProblem ? ' · loudness (speech model off)' : ''; }
    // The model is in charge only while it is actually reporting.
    modelLive(now=performance.now()) { return Boolean(this.model) && now-this.model.at<STALE_MS; }
    voiced(bar, now=performance.now()) {
      const loud=this.level()>bar;
      if (!this.modelLive(now)) return loud;
      return this.model.take() >= (this.heard ? KEEP_P : START_P);
    }
    // Paused (Apex thinking or speaking): only listen for someone talking over it.
    watch() {
      if (this.transcribing || this.recording || !this.analyser || !this.bargeWatch()) return;
      if (this.context.state!=='running') return;
      const now=performance.now();
      const loud=this.level()>this.threshold()*BARGE_FACTOR;
      if (loud && (!this.modelLive(now) || this.model.take()>=START_P)) {
        // Record from the first loud frame, so the words that triggered this
        // are in the transcript; confirmed (or thrown away) in tick().
        this.paused=false; this.barging=true; this.bargeStart=now-FRAME_MS; this.capture();
        this.last=now; this.lastVoice=now;
      }
    }
    tick() {
      if (!this.enabled) return;
      if (this.paused) { this.watch(); return; }
      if (!this.recording || this.recording.state!=='recording') return;
      if(this.context.state!=='running') { this.fail(Error('Browser paused audio. Return to the companion and enable hands-free again.')); return; }
      const now=performance.now(), dt=Math.min(100,now-this.last); this.last=now;
      const bar=this.threshold()*(this.barging?BARGE_FACTOR:1);
      // Confirming a barge-in keeps the loudness bar: her voice is speech too.
      const speech=this.barging ? this.level()>bar && (!this.modelLive(now) || this.model.take()>=START_P) : this.voiced(bar, now);
      if(speech) {
        this.voicedMs+=dt; this.lastVoice=now;
        if(this.barging&&this.voicedMs>=BARGE_MS){this.barging=false;this.heard=true;this.onBarge(this.bargeStart);this.onState('Listening · pause to send');}
        else if(!this.barging&&this.voicedMs>=250&&!this.heard){this.heard=true;this.onState('Listening · pause to send');}
      } else if(this.barging) {
        // A cough, a door, a loud word of hers: not someone talking. Back to watching.
        if(now-this.lastVoice>150){this.barging=false;this.pause();}
        return;
      } else if(!this.heard) { this.voicedMs=0; }
      const quiet=this.modelLive(now) ? MODEL_SILENCE_MS : SILENCE_MS;
      if(this.heard && (now-this.lastVoice>quiet || now-this.started>30000)) {
        this.paused=true; this.recording.stop();
      } else if(!this.heard && now-this.started>8000) {
        this.recording.discard=true; this.recording.stop(); // Discard silence locally.
      }
    }
    pause() {
      this.paused=true; this.heard=false; this.barging=false;
      if(this.recording){this.recording.discard=true;if(this.recording.state==='recording')this.recording.stop();}
    }
    resume() { if(this.enabled&&!this.transcribing){this.paused=false;try{this.capture();}catch(e){this.fail(e);}} }
    fail(e) { this.stop(); this.onError(e); }
    stop() {
      this.enabled=false;this.epoch++;clearInterval(this.timer);this.pause();
      this.model?.destroy();this.model=null;
      this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;
      this.source?.disconnect();this.source=null;
      this.context?.close().catch(()=>{});this.context=null;
      this.recording=null;this.transcribing=false;
    }
  }
  window.ApexHandsFree=HandsFree;
})();
