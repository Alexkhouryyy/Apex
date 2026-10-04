/* The photoreal video avatar in the companion (scripts/avatar_server.py).
   The character's idle loop plays while Apex is quiet; each spoken section is
   sent to the avatar server, which returns a clip of the face saying it, with
   the sound. That clip plays over the loop and fades back when it ends.

   new ApexVideoAvatar(host, request) -> await start() (throws with the reason
   if the avatar server isn't running) -> render(audioBlob) -> play(clip, onStart)
   -> stop() -> dispose(). Anything failing is the caller's cue to just play
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
      this.ready = true;
    }
    url(blob) { const u = URL.createObjectURL(blob); this.urls.push(u); return u; }
    render(audio) {
      return this.request('/api/avatar/lipsync?format=' + this.format, {method: 'POST',
        headers: {'Content-Type': audio.type || 'audio/wav'}, body: audio}).then(r => r.blob());
    }
    play(clip, onStart) {
      return new Promise((resolve, reject) => {
        const v = this.speaking, url = this.url(clip);
        let settled = false;
        const finish = exc => {
          if (settled) return; settled = true;
          v.onplaying = v.onended = v.onerror = null;
          this.host.classList.remove('avatar-talking');
          this.stopNow = null;
          URL.revokeObjectURL(url); this.urls = this.urls.filter(u => u !== url);
          if (exc) reject(exc); else resolve();
        };
        v.onplaying = () => { this.host.classList.add('avatar-talking'); onStart?.(); };
        v.onended = () => finish();
        v.onerror = () => finish(Error('The avatar clip could not play.'));
        this.stopNow = () => { v.pause(); finish(); };
        v.muted = false; v.src = url;
        v.play().catch(exc => finish(exc.name === 'NotAllowedError'
          ? Error('Your browser blocked sound. Allow sound for this site, then send a short message.') : exc));
      });
    }
    stop() { this.stopNow?.(); }
    dispose() {
      this.stop(); this.ready = false;
      for (const v of [this.idle, this.speaking]) { if (v) { v.pause(); v.removeAttribute('src'); v.remove(); } }
      for (const u of this.urls) URL.revokeObjectURL(u);
      this.urls = [];
    }
  }
  window.ApexVideoAvatar = VideoAvatar;
})();
