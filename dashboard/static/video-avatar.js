/* The photoreal video avatar in the companion (scripts/avatar_server.py).
   The character's idle loop plays while Apex is quiet; each spoken section is
   sent to the avatar server, which returns a clip of the face saying it, with
   the sound. That clip plays over the loop and fades back when it ends.

   new ApexVideoAvatar(host, request) -> await start() (throws with the reason
   if the avatar server isn't running) -> render(audioBlob) -> play(clip, onStart)
   -> stop() -> dispose().

   No jumps: the avatar server can start a clip on any frame of the idle loop
   and says where it ended. The first clip of a reply starts on the frame the
   loop will be showing when the clip is ready (predicted from recent render
   times); each clip after it starts where the one before ended; and when
   talking stops, the loop carries on from the last spoken frame. Anything failing is the caller's cue to just play
   the audio, so Apex is never silent because of the video. */
(() => {
  'use strict';
  class VideoAvatar {
    constructor(host, request) {
      this.host = host; this.request = request; this.ready = false; this.urls = [];
      // MP4 (H.264) where the browser plays it (Chrome, Edge, Safari); WebM otherwise.
      const probe = document.createElement('video');
      this.format = probe.canPlayType?.('video/mp4; codecs="avc1.42E01E, mp4a.40.2"') ? 'mp4' : 'webm';
    }
    video(cls) {
      const v = document.createElement('video');
      v.className = cls; v.playsInline = true; v.preload = 'auto'; v.setAttribute('playsinline', '');
      this.host.appendChild(v); return v;
    }
    async start() {
      const status = await (await this.request('/api/avatar/status')).json();
      if (!status.available) throw Error(status.reason || 'The video avatar is not running.');
      const idle = await (await this.request('/api/avatar/idle?format=' + this.format)).blob();
      this.idle = this.video('avatar-idle');
      this.idle.muted = true; this.idle.loop = true; this.idle.autoplay = true;
      this.idle.src = this.url(idle);
      this.idle.play?.().catch(() => {});                 // muted autoplay is allowed; if not, the first frame shows
      this.speaking = this.video('avatar-speaking');
      this.engine = status.engine;
      this.fps = Number(status.fps) || 25; this.frames = Number(status.frames) || 1;
      this.latency = 800;                 // ms from asking for a clip to having it; learned as clips arrive
      this.unplayed = 0; this.playing = false; this.lastEnded = -1e9; this.chain = Promise.resolve(null);
      this.ready = true;
    }
    url(blob) { const u = URL.createObjectURL(blob); this.urls.push(u); return u; }
    idleFrame() {
      return Math.floor((this.idle?.currentTime || 0) * this.fps) % this.frames;
    }
    render(audio) {
      const job = this.chain.then(async previousEnd => {
        // Talking continues: start where the last clip ends. A new reply: start
        // where the loop will be when this clip arrives.
        const ahead = Math.round(this.latency / 1000 * this.fps);
        // Still talking: a clip is playing, one is waiting to play, or one just ended.
        const talking = this.playing || this.unplayed > 0 || performance.now() - this.lastEnded < 500;
        const start = previousEnd != null && talking ? previousEnd : (this.idleFrame() + ahead) % this.frames;
        const asked = performance.now();
        {
          const response = await this.request(`/api/avatar/lipsync?format=${this.format}&start=${start}`, {method: 'POST',
            headers: {'Content-Type': audio.type || 'audio/wav'}, body: audio});
          const blob = await response.blob();
          const took = performance.now() - asked;
          this.latency = this.latency * 0.6 + took * 0.4;
          const end = Number(response.headers.get('X-End-Frame'));
          this.unplayed++;
          return {blob, start, end: Number.isFinite(end) ? end : null, renderMs: Number(response.headers.get('X-Render-Ms')) || null};
        }
      });
      this.chain = job.then(clip => clip.end, () => null);
      return job;
    }
    play(clip, onStart) {
      const blob = clip instanceof Blob ? clip : clip.blob;
      const end = clip instanceof Blob ? null : clip.end;
      this.unplayed = Math.max(0, this.unplayed - 1);
      return new Promise((resolve, reject) => {
        const v = this.speaking, url = this.url(blob);
        let settled = false;
        const finish = exc => {
          if (settled) return; settled = true;
          v.onplaying = v.onended = v.onerror = null;
          this.playing = false; this.lastEnded = performance.now();
          // The loop carries on from the last spoken frame, under the clip, before it fades out.
          if (end != null && this.idle) { try { this.idle.currentTime = end / this.fps; } catch (_) {} }
          this.host.classList.remove('avatar-talking');
          this.stopNow = null;
          URL.revokeObjectURL(url); this.urls = this.urls.filter(u => u !== url);
          if (exc) reject(exc); else resolve();
        };
        v.onplaying = () => { this.playing = true; this.host.classList.add('avatar-talking'); onStart?.(); };
        v.onended = () => finish();
        v.onerror = () => finish(Error('The avatar clip could not play.'));
        this.stopNow = () => { v.pause(); finish(); };
        v.muted = false; v.src = url;
        v.play().catch(exc => finish(exc.name === 'NotAllowedError'
          ? Error('Your browser blocked sound. Allow sound for this site, then send a short message.') : exc));
      });
    }
    stop() { this.unplayed = 0; this.stopNow?.(); }
    dispose() {
      this.stop(); this.ready = false;
      for (const v of [this.idle, this.speaking]) { if (v) { v.pause(); v.removeAttribute('src'); v.remove(); } }
      for (const u of this.urls) URL.revokeObjectURL(u);
      this.urls = [];
    }
  }
  window.ApexVideoAvatar = VideoAvatar;
})();
