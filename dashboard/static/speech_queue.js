/* Ordered sentence playback: at most one synthesis request in flight.
   `run` speaks a finished reply; `live` speaks one as it streams in. */
(function (root) {
  'use strict';
  // A full stop after one of these, or after a single capital (an initial,
  // "J. Smith"), does not end a sentence: "with Dr. Lee" was read as "with
  // Dr." … "Lee", a clip boundary in the middle of a name.
  const ABBREV = new Set(['mr', 'mrs', 'ms', 'dr', 'prof', 'st', 'jr', 'sr', 'vs', 'etc', 'eg', 'ie',
    'no', 'approx', 'dept', 'inc', 'ltd', 'co', 'mt', 'fig', 'jan', 'feb', 'mar', 'apr', 'jun',
    'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec']);
  function notAnEnd(text, index, mark) {
    if (mark[0] !== '.') return false;                 // ! and ? always end one
    const word = /([A-Za-z][A-Za-z.]*)$/.exec(text.slice(0, index));
    if (!word) return false;
    const w = word[1].replace(/\./g, '');
    return /^[A-Z]$/.test(word[1]) || ABBREV.has(w.toLowerCase());
  }
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
      if (/^(\d+|[A-Za-z])[.)]?$/.test(piece) || notAnEnd(clean, m.index, m[0])) continue;
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
      if (m[0][0] !== '\n' && notAnEnd(masked, m.index, m[0])) continue;
      end = stop; from = stop;
    }
    if (end < 0) return {ready: [], rest: buffer};
    return {ready: chunks(safe.slice(0, end)), rest: safe.slice(end) + held};
  }

  // The first phrase of a reply, if it is worth starting on before the first
  // sentence ends: a clause of at least MIN_CLAUSE characters ending at , ; :
  // or a dash followed by a space — and only when the sentence it opens is
  // long (over LONG_SENTENCE, or not finished yet), because splitting a short
  // sentence buys nothing and costs a break in the voice.
  const MIN_CLAUSE = 20, LONG_SENTENCE = 60;
  function firstClause(buffer) {
    let safe = buffer;
    const fence = buffer.indexOf('```');
    if (fence >= 0) safe = buffer.slice(0, fence);   // nothing is split near code
    const ends = /[.!?]+["”’')]?(?=\s)|\n/g;
    let sentence = null, e;
    while ((e = ends.exec(safe))) { if (e[0][0] === '\n' || !notAnEnd(safe, e.index, e[0])) { sentence = e; break; } }
    if (sentence && sentence.index + sentence[0].length <= LONG_SENTENCE) return null;
    const limit = sentence ? sentence.index : safe.length;
    const re = /(?:[,;:]|\s[—–-])(?=\s)/g;
    let m;
    while ((m = re.exec(safe)) && m.index < limit) {
      const stop = m.index + m[0].length;
      if (safe.slice(0, stop).trim().length < MIN_CLAUSE) continue;
      const part = chunks(safe.slice(0, stop));
      return part.length ? {ready: part, rest: buffer.slice(stop)} : null;
    }
    return null;
  }

  // `coalesce` (characters; 0 = off): after the first section, take every
  // sentence waiting at that moment as ONE section, up to that many
  // characters. For a streaming voice each section costs a fixed start-up
  // (about a second on the laptop) and only one can generate at a time, so
  // every boundary is a pause; fewer, longer sections mean fewer pauses. The
  // first section stays alone — it is the one the listener is waiting on.
  // `beforeTake` (optional async): awaited before each section is taken from
  // the waiting sentences — e.g. "the GPU is free". Taking first and waiting
  // after would merge only what was waiting when the PREVIOUS section went.
  function live(generate, play, {firstPhrase = false, coalesce = 0, beforeTake = null} = {}) {
    const parts = [];
    let buffer = '', ended = false, cancelled = false, waiters = [], started = false;
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
        if (beforeTake) { await beforeTake(); if (cancelled) break; }
        let text = parts.shift();
        if (coalesce > 0 && i > 0) {
          while (parts.length && text.length + 1 + parts[0].length <= coalesce) text += ' ' + parts.shift();
        }
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
        // Only the very first section is ever a phrase: that is the one the
        // listener is waiting on. After it, whole sentences sound better.
        if (firstPhrase && !started) {
          const clause = firstClause(buffer);
          if (clause) { buffer = clause.rest; parts.push(...clause.ready); started = true; wake(); }
        }
        const got = take(buffer); buffer = got.rest;
        if (got.ready.length) { parts.push(...got.ready); started = true; wake(); }
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

  const api = {chunks, run, take, live, firstClause};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ApexSpeechQueue = api;
})(globalThis);
