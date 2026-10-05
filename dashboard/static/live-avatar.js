/* The live photoreal face (Simli) in the companion. Apex keeps its own brain
   and its own voice: every bit of speech Apex would play is sent to Simli
   instead, as 16 kHz audio, and Simli streams back a photoreal face saying it,
   with that sound, over WebRTC. Celine's streamed voice goes in as it arrives,
   so the face starts talking while the sentence is still being made.

   new ApexLiveAvatar(host, request) -> await start() (throws the reason if
   Simli isn't set up or can't connect) -> say(int16, rate) for each piece of
   speech -> await finished() -> interrupt() -> dispose(). park() hangs up
   (Simli bills while a session is open) and keeps the last frame on screen;
   ensure() reconnects, and is called as soon as you start typing or talking. The page's caller
   falls back to plain audio whenever any of this fails.

   The Simli key never reaches this page: Apex's server exchanges it for a
   short-lived session token (/api/avatar/live/session). */
(() => {
  'use strict';
  const RATE = 16000;                  // what Simli takes
  const BUNDLE = '/static/vendor/simli/simli.bundle.js?v=3.0.2';
  const QUIET = 0.01, QUIET_MS = 450;  // the face has stopped talking: this quiet, this long
  let loading = null;
  const loadBundle = () => loading = loading || new Promise((resolve, reject) => {
    if (window.ApexSimli) { resolve(); return; }
    const tag = document.createElement('script');
    tag.src = BUNDLE; tag.onload = resolve;
    tag.onerror = () => { loading = null; reject(Error('The live face library did not load.')); };
    document.head.appendChild(tag);
  });

  // Linear resampling to 16 kHz that carries its position across pieces, so a
  // stream cut into odd-sized pieces resamples exactly like one long buffer.
  class Resampler {
    constructor(from) { this.step = from / RATE; this.pos = 0; this.last = 0; }
    push(input) {
      const out = [];
      // `pos` is measured from the sample before input[0] (the previous piece's last).
      while (this.pos < input.length) {
        const i = Math.floor(this.pos), f = this.pos - i;
        const a = i === 0 ? this.last : input[i - 1], b = input[i];
        out.push(Math.round(a + (b - a) * f));
        this.pos += this.step;
      }
      this.pos -= input.length;
      if (input.length) this.last = input[input.length - 1];
      return Int16Array.from(out);
    }
  }

  class LiveAvatar {
    constructor(host, request) {
      this.host = host; this.request = request; this.ready = false; this.connecting = null;
      this.resamplers = new Map(); this.speechEnds = 0; this.heard = false; this.quietSince = 0;
    }
    // The first connection: throws the reason if the live face can't be used at all.
    async start() { await this.ensure(); }
    // Connected now, or as soon as possible (one attempt at a time). Simli bills
    // while a session is open, so the page hangs up when the face isn't needed
    // (park) and calls this again the moment you start typing or talking.
    ensure() {
      if (this.ready) return Promise.resolve();
      this.connecting = this.connecting || this.connect().finally(() => { this.connecting = null; });
      return this.connecting;
    }
    async connect() {
      const session = await (await this.request('/api/avatar/live/session', {method: 'POST'})).json();
      if (!session.available) throw Error(session.reason || 'The live face is not set up.');
      await loadBundle();
      if (!this.video) {
        this.video = document.createElement('video');
        this.video.className = 'avatar-live'; this.video.autoplay = true; this.video.playsInline = true;
        this.video.setAttribute('playsinline', ''); this.video.muted = true;    // the voice comes on the audio element
        this.audio = document.createElement('audio'); this.audio.autoplay = true;
        this.host.append(this.video, this.audio);
      }
      const {SimliClient, LogLevel} = window.ApexSimli;
      const client = new SimliClient(session.session_token, this.video, this.audio, session.ice_servers, LogLevel.ERROR, 'livekit');
      // Simli ending the session (its idle limit, a network blip) is a hang-up, not a failure:
      // the face waits, parked, and reconnects when needed.
      client.on?.('disconnected', () => { if (this.client === client) this.park(); });
      client.on?.('failed', reason => { if (this.client === client) this.lost(`The live face failed: ${reason || 'unknown'}`); });
      this.client = client;
      let timer;
      try {
        await Promise.race([client.start(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(Error('The live face did not connect within 20 s.')), 20000); })]);
      } catch (exc) {
        this.client = null; try { client.stop?.(); } catch (_) {}
        throw exc;
      } finally { clearTimeout(timer); }
      this.ready = true; this.parked = false;
      this.host.classList.remove('avatar-parked');
    }
    // Hang up and keep the last frame on screen, so the face doesn't vanish.
    park() {
      if (!this.client) return;
      const client = this.client; this.client = null; this.ready = false; this.parked = true;
      try {
        if (this.video?.videoWidth) {
          this.poster = this.poster || Object.assign(document.createElement('canvas'), {className: 'avatar-poster'});
          this.poster.width = this.video.videoWidth; this.poster.height = this.video.videoHeight;
          this.poster.getContext('2d').drawImage(this.video, 0, 0);
          if (!this.poster.isConnected) this.host.append(this.poster);
        }
      } catch (_) { /* no frame to keep: the background shows */ }
      this.host.classList.add('avatar-parked');
      this.resamplers.clear(); this.speechEnds = 0; this.heard = false;
      try { client.stop?.(); } catch (_) {}
    }
    lost(why) { if (!this.ready) return; this.ready = false; this.onLost?.(why); }
    // Loudness of the face's own voice, so the page knows when it starts and stops talking.
    level() {
      const stream = this.audio?.srcObject;
      if (!stream) return 0;
      if (this.stream !== stream) {
        this.stream = stream;
        const Ctx = window.AudioContext || window.webkitAudioContext;
        try {
          this.ctx = this.ctx || new Ctx();
          this.meter = this.ctx.createAnalyser(); this.meter.fftSize = 512;
          this.ctx.createMediaStreamSource(stream).connect(this.meter);  // measured only, not played twice
          this.samples = new Float32Array(this.meter.fftSize);
        } catch (_) { this.meter = null; }
      }
      if (!this.meter) return 0;
      this.meter.getFloatTimeDomainData(this.samples);
      let sum = 0; for (const v of this.samples) sum += v * v;
      return Math.sqrt(sum / this.samples.length);
    }
    say(pcm, rate, stream = 'default') {
      if (!this.ready || !pcm.length) return;
      let r = this.resamplers.get(stream);
      if (!r || r.step !== rate / RATE) { r = new Resampler(rate); this.resamplers.set(stream, r); }
      const out = rate === RATE ? pcm : r.push(pcm);
      if (!out.length) return;
      this.client.sendAudioData(new Uint8Array(out.buffer, out.byteOffset, out.byteLength));
      // Simli plays in real time: speech ends this long after what is already queued.
      const now = performance.now();
      this.speechEnds = Math.max(this.speechEnds, now) + out.length / RATE * 1000;
    }
    endStream(stream = 'default') { this.resamplers.delete(stream); }
    // Resolves when the face has said everything sent so far: after the queued
    // audio's length, once its voice has been quiet a moment (or at a hard cap,
    // if its audio can't be measured). onSound fires when the voice is first heard.
    finished(onSound) {
      return new Promise(resolve => {
        const cap = this.speechEnds + 4000;
        const check = () => {
          if (!this.ready) { resolve(); return; }
          const now = performance.now(), loud = this.level() > QUIET;
          if (loud && !this.heard) { this.heard = true; onSound?.(); }
          if (loud) this.quietSince = now;
          const said = now > this.speechEnds && now - this.quietSince > QUIET_MS && (this.heard || now > this.speechEnds + 1500);
          if (said || now > cap) { this.heard = false; resolve(); return; }
          this.timer = setTimeout(check, 50);
        };
        check();
      });
    }
    interrupt() {
      try { this.client?.ClearBuffer(); } catch (_) {}
      this.resamplers.clear(); this.speechEnds = 0; this.heard = false;
    }
    dispose() {
      this.ready = false; clearTimeout(this.timer);
      try { this.client?.stop?.(); } catch (_) {}
      this.client = null;
      this.ctx?.close?.().catch?.(() => {});
      this.video?.remove(); this.audio?.remove(); this.poster?.remove();
    }
  }
  window.ApexLiveAvatar = LiveAvatar;
  window.ApexLiveAvatar.Resampler = Resampler;
})();
