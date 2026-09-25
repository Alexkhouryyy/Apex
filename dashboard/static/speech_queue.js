/* Ordered sentence playback: at most one synthesis request in flight.
   `run` speaks a finished reply; `live` speaks one as it streams in. */
(function (root) {
  'use strict';
  function chunks(text, limit = 180) {
    const clean = String(text).replace(/```[\s\S]*?```/g, ' Code is shown on screen. ')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*#`]/g, '').replace(/\s+/g, ' ').trim();
    // A sentence ends at punctuation followed by a space or the end — not at
    // every full stop, which read "3.5" as two clips ("3." … "5") with a gap.
    // A bare list marker ("1.") stays with the text after it.
    const sentences = [];
    const re = /[.!?]+["”’')]?(?=\s|$)/g;
    let from = 0, m;
    while ((m = re.exec(clean))) {
      const stop = m.index + m[0].length;
      const piece = clean.slice(from, stop).trim();
      if (/^(\d+|[A-Za-z])[.)]?$/.test(piece)) continue;
      if (piece) sentences.push(piece);
      from = stop;
    }
    if (clean.slice(from).trim()) sentences.push(clean.slice(from).trim());
    const parts = [];
    for (let sentence of sentences) {
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
  // --- Speaking while the reply is still being written ----------------------
  // `run` needs the whole reply first, so the first sound waited for the last
  // word — and on a turn with a tool call, for the tool as well. `live` takes
  // text as it streams and starts on the first complete sentence.

  // A boundary is sentence punctuation FOLLOWED BY WHITESPACE (so "3.5" is not
  // split mid-stream — the "5" has not arrived yet), or a line break (lists
  // have no full stops). Never inside a code block, and never after a bare
  // list marker like "1." — that is not a sentence to say on its own.
  function take(buffer) {
    let safe = buffer, held = '';
    const fences = buffer.split('```').length - 1;
    if (fences % 2) { const i = buffer.lastIndexOf('```'); safe = buffer.slice(0, i); held = buffer.slice(i); }
    const masked = safe.replace(/```[\s\S]*?```/g, m => ' '.repeat(m.length));
    const re = /[.!?]+["”’')]?(?=\s)|\n+/g;
    let end = -1, from = 0, m;
    while ((m = re.exec(masked))) {
      const stop = m.index + m[0].length;
      const piece = masked.slice(from, stop).trim();
      if (!piece || /^(\d+|[A-Za-z])[.)]?$/.test(piece)) continue;
      end = stop; from = stop;
    }
    if (end < 0) return {ready: [], rest: buffer};
    return {ready: chunks(safe.slice(0, end)), rest: safe.slice(end) + held};
  }

  function live(generate, play) {
    const parts = [];
    let buffer = '', ended = false, cancelled = false, waiters = [];
    const wake = () => { const w = waiters; waiters = []; w.forEach(f => f()); };
    const until = cond => new Promise(r => {
      const check = () => (cond() ? r() : waiters.push(check)); check(); });
    // Same contract as `run`: at most one synthesis in flight, and it may run
    // while the previous section plays — never more than one section ahead.
    const ready = [];
    let inFlight = false, producerDone = false;
    const producer = (async () => {
      let i = 0;
      while (true) {
        await until(() => cancelled || parts.length || ended);
        if (cancelled || (!parts.length && ended)) break;
        await until(() => cancelled || ready.length === 0);
        if (cancelled) break;
        const text = parts.shift();
        inFlight = true;
        const result = await Promise.resolve().then(() => generate(text, i++))
          .then(value => ({value}), error => ({error}));
        inFlight = false;
        ready.push(result); wake();
        if (result.error) break;
      }
      producerDone = true; wake();
    })();
    const consumer = (async () => {
      let i = 0;
      while (true) {
        await until(() => cancelled || ready.length || producerDone);
        if (cancelled || (!ready.length && producerDone)) return;
        const result = ready[0];
        if (result.error) throw result.error;
        const playing = play(result.value, i++);
        ready.shift(); wake();          // the next synthesis may start now
        await playing;
      }
    })();
    // Like `run`, do not resolve until an in-flight synthesis has drained:
    // aborting the request does not stop a local GPU generating.
    const done = Promise.all([producer, consumer.catch(e => { cancelled = true; wake(); throw e; })])
      .then(() => {});
    return {
      push(text) {
        if (ended || cancelled) return;
        buffer += text;
        const got = take(buffer); buffer = got.rest;
        if (got.ready.length) { parts.push(...got.ready); wake(); }
      },
      // `tail` is text the stream never carried (an error reply, a fallback):
      // it has to be spoken too, or that turn is silent.
      end(tail = '') {
        if (ended) return;
        parts.push(...chunks(buffer + (tail ? ' ' + tail : ''))); buffer = '';
        ended = true; wake();
      },
      cancel() { cancelled = true; wake(); },
      get busy() { return inFlight; },
      done,
    };
  }

  const api = {chunks, run, take, live};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ApexSpeechQueue = api;
})(globalThis);
