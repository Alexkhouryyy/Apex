/* Ordered sentence playback: at most one synthesis request in flight. */
(function (root) {
  'use strict';
  function chunks(text, limit = 180) {
    const clean = String(text).replace(/```[\s\S]*?```/g, ' Code is shown on screen. ')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*#`]/g, '').replace(/\s+/g, ' ').trim();
    const parts = [];
    for (let sentence of clean.match(/[^.!?]+[.!?]+(?:["”’']|$)?|[^.!?]+$/g) || []) {
      sentence = sentence.trim();
      while (sentence.length > limit) {
        let cut = sentence.lastIndexOf(' ', limit);
        if (cut < 1) cut = limit;
        parts.push(sentence.slice(0, cut)); sentence = sentence.slice(cut).trim();
      }
      if (sentence) parts.push(sentence);
    }
    return parts;
  }
  async function run(parts, generate, play, cancelled) {
    // Turn rejection into a value immediately: the prefetch can fail while
    // playback is still running. Always drain it before releasing the UI guard.
    const fetchPart = text => Promise.resolve().then(() => generate(text))
      .then(value => ({value}), error => ({error}));
    let pending = null;
    try {
      if (!parts.length || cancelled()) return;
      pending = fetchPart(parts[0]);
      for (let i = 0; i < parts.length; i++) {
        const result = await pending; pending = null;
        if (cancelled()) return;
        if (result.error) throw result.error;
        // Playback starts before the next synthesis; the two then overlap.
        const playing = play(result.value, i);
        if (i + 1 < parts.length && !cancelled()) pending = fetchPart(parts[i + 1]);
        await playing;
        if (cancelled()) return;
      }
    } finally {
      if (pending) await pending;
    }
  }
  const api = {chunks, run};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ApexSpeechQueue = api;
})(globalThis);
