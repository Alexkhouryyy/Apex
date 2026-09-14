/* Browser-local energy detection. Only completed speech segments are uploaded.
   Turn-taking pauses capture during Apex's replies; this is not full duplex. */
(() => {
  'use strict';
  class HandsFree {
    constructor({onSegment, onState, onError, threshold = () => .018}) {
      Object.assign(this, {onSegment, onState, onError, threshold});
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
      record.start(); this.onState('Listening · speak naturally');
    }
    tick() {
      if (!this.enabled || this.paused || !this.recording || this.recording.state!=='recording') return;
      if(this.context.state!=='running') { this.fail(Error('Browser paused audio. Return to the companion and enable hands-free again.')); return; }
      this.analyser.getFloatTimeDomainData(this.samples);
      const rms=Math.sqrt(this.samples.reduce((n,v)=>n+v*v,0)/this.samples.length);
      const now=performance.now(), dt=Math.min(100,now-this.last); this.last=now;
      if(rms>this.threshold()) {
        this.voicedMs+=dt; this.lastVoice=now;
        if(this.voicedMs>=250&&!this.heard){this.heard=true;this.onState('Listening · pause to send');}
      } else if(!this.heard) { this.voicedMs=0; }
      if(this.heard && (now-this.lastVoice>1200 || now-this.started>30000)) {
        this.paused=true; this.recording.stop();
      } else if(!this.heard && now-this.started>8000) {
        this.recording.discard=true; this.recording.stop(); // Discard silence locally.
      }
    }
    pause() {
      this.paused=true; this.heard=false;
      if(this.recording){this.recording.discard=true;if(this.recording.state==='recording')this.recording.stop();}
    }
    resume() { if(this.enabled&&!this.transcribing){this.paused=false;try{this.capture();}catch(e){this.fail(e);}} }
    fail(e) { this.stop(); this.onError(e); }
    stop() {
      this.enabled=false;this.epoch++;clearInterval(this.timer);this.pause();
      this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;
      this.source?.disconnect();this.source=null;
      this.context?.close().catch(()=>{});this.context=null;
      this.recording=null;this.transcribing=false;
    }
  }
  window.ApexHandsFree=HandsFree;
})();
