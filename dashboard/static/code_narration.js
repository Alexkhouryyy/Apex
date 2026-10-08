/* What the Code page says out loud as a session moves (dashboard/static/code.js).
   Milestones: done, checks, the second opinion, a message Celine drafted. Play-by-play
   adds a short phrase for what the plan is doing ("running pytest", "editing upload.py"),
   at most one every 6 seconds and none while something is being said. Fixed phrases,
   no model, and never code: a fenced block is dropped, and a command or a file is said
   by its name only. What the night shift built is said in counts, never titles. */
(function (root) {
  'use strict';
  const GAP = 6000;                                     // ms between two play-by-play phrases, at least
  const PLAN = {claude: 'Claude', chatgpt: 'ChatGPT'};
  const SAFE_NAME = /^[\w.@+-]{1,40}$/;                 // a name to say, not code
  // Text without code: fenced blocks (closed or not) go, an inline span stays only when it is a name.
  function noCode(text) {
    return String(text || '').replace(/```[\s\S]*?(```|$)/g, ' ')
      .replace(/`([^`\n]*)`/g, (_, inner) => SAFE_NAME.test(inner.trim()) ? inner.trim() : ' ')
      .replace(/`/g, '').replace(/[*#>_~|]/g, '').replace(/\s+/g, ' ').trim();
  }
  // The first sentence, for "Done" (a full stop inside 3.5 or upload.py does not end it).
  function firstSentence(text, limit = 200) {
    const clean = noCode(text);
    const m = /[.!?](?=\s|$)/.exec(clean);
    let s = m ? clean.slice(0, m.index + 1) : clean;
    if (s.length > limit) { const cut = s.lastIndexOf(' ', limit); s = s.slice(0, cut > 40 ? cut : limit) + '…'; }
    return s;
  }
  const plural = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;
  const DONE = {failed: 'It stopped with an error.', limited: 'Your plan hit its usage limit.', signed_out: 'Your plan is not signed in.',
    missing: 'Your plan is not set up on this PC.', interrupted: 'It was interrupted.'};
  // A milestone's phrase, or '' when this step isn't one.
  function milestone(e) {
    if (!e) return '';
    if (e.kind === 'done') {
      if (e.status === 'done' && e.plan) return 'The plan is ready. Nothing changed yet.';
      if (e.status !== 'done') return DONE[e.status] || '';
      const n = e.total != null ? e.total : (e.files || 0);
      const first = firstSentence(e.summary);
      return (n ? `Done. ${plural(n, 'file')} changed.` : 'Done. No files changed.') + (first ? ' ' + first : '');
    }
    if (e.kind === 'checks') {
      const state = e.state || (e.passed ? 'passed' : 'failed'), why = noCode(e.why).slice(0, 120);
      if (state === 'passed') return why && why !== 'exit 0' ? `Checks passed: ${why}.` : 'Checks passed.';
      if (state === 'failed') return 'Checks failed.';
      return why ? `Checks unknown: ${why}.` : 'Checks unknown.';
    }
    if (e.kind === 'review') {
      if (e.status !== 'done') return "The second opinion didn't finish.";
      const who = PLAN[e.engine] || 'The other plan';
      return e.rating != null ? `${who} rates it ${e.rating} out of 10.` : `${who} gave its second opinion.`;
    }
    if (e.kind === 'draft') return 'Celine drafted a message for you.';
    return '';
  }
  // The program a command runs, by name: "python -m pytest -q" is pytest, "bash -lc 'npm test'" is npm.
  function program(command) {
    let words = String(command || '').trim().split(/\s+/);
    for (let depth = 0; depth < 2 && words.length; depth++) {
      const name0 = words[0].replace(/^["']|["']$/g, '').split(/[\\/]/).pop().replace(/\.exe$/i, ''), head = name0.toLowerCase();
      if (/^(bash|sh|zsh|dash|cmd|powershell|pwsh)$/.test(head)) {
        const at = words.findIndex((w, i) => i > 0 && !/^[-/]/.test(w));
        words = at > 0 ? words.slice(at).join(' ').replace(/^["']|["']$/g, '').split(/\s+/) : [];
        continue;
      }
      const m = words.indexOf('-m');
      const name = /^python[\d.]*$|^py$/.test(head) && m > 0 && words[m + 1] ? words[m + 1] : name0;
      return SAFE_NAME.test(name) ? name : '';
    }
    return '';
  }
  const fileName = p => { const n = String(p || '').split(/[\\/]/).pop(); return SAFE_NAME.test(n) ? n : ''; };
  // A play-by-play phrase for a step, or ''.
  function playByPlay(e) {
    if (!e) return '';
    if (e.kind === 'file') { const n = fileName(e.path); return n ? `editing ${n}` : 'editing a file'; }
    if (e.kind !== 'tool') return '';
    if (e.tool === 'command') { const p = program(e.title || e.command); return p ? `running ${p}` : 'running a command'; }
    if (e.tool === 'read' || e.tool === 'search') return 'looking through the code';
    if (e.tool === 'memory') return "checking Apex's memory";
    if (e.tool === 'web') return 'looking something up';
    return '';
  }
  // At most one phrase every `gap` ms, and none while audio plays: a dropped phrase is gone, never queued.
  function pacer(gap = GAP) {
    let last = -Infinity;
    return {
      offer(phrase, now, playing) {
        if (!phrase || playing || now - last < gap) return '';
        last = now; return phrase;
      },
    };
  }
  // What the night shift built, in counts only: never a title, since anyone in the room can hear it.
  const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
  const count = n => n < WORDS.length ? WORDS[n] : String(n);
  function overnight(rows) {
    const list = rows || [], ready = list.filter(r => !r.working), n = ready.length, busy = list.length - n;
    const unproved = ready.filter(r => r.verdict !== 'proved').length;
    const parts = [];
    if (n) {
      let s = `${count(n)} session${n === 1 ? ' is' : 's are'} ready`;
      if (unproved) s += unproved === n ? (n === 1 ? "; it isn't verified" : '; none are verified')
        : `; ${count(unproved).toLowerCase()} ${unproved === 1 ? "isn't" : "aren't"} verified`;
      parts.push(s + '.');
    }
    if (busy) parts.push(`${count(busy)} ${busy === 1 ? 'is' : 'are'} still working.`);
    return parts.join(' ');
  }
  const api = {GAP, noCode, firstSentence, milestone, program, playByPlay, pacer, overnight};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ApexNarration = api;
})(typeof window !== 'undefined' ? window : globalThis);
