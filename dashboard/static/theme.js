// Apex's look: "futuristic" (the hologram layer in theme.css) or "normal"
// (each page's own styles, untouched). One setting for every page, stored in
// this browser, applied before the page first paints so there is no flash,
// and kept in step across open tabs. Separate from the dashboard's colour
// palette (`data-theme`): the look sits on top of whichever palette is chosen.
(() => {
  'use strict';
  const KEY = 'apex.look', LOOKS = ['futuristic', 'normal'];
  const root = document.documentElement;
  let look = 'futuristic';
  try { const saved = localStorage.getItem(KEY); if (LOOKS.includes(saved)) look = saved; } catch (_) {}
  root.dataset.look = look;

  function sync() {
    for (const b of document.querySelectorAll('[data-look-toggle]')) {
      const futuristic = look === 'futuristic';
      b.textContent = futuristic ? '◈ Futuristic' : '◇ Normal';
      b.setAttribute('aria-pressed', String(futuristic));
      b.title = 'Apex look: ' + look + '. Click for the ' + (futuristic ? 'normal' : 'futuristic') + ' look (every page).';
    }
  }
  function apply(next, save) {
    if (!LOOKS.includes(next)) return;
    look = next; root.dataset.look = next;
    if (save) { try { localStorage.setItem(KEY, next); } catch (_) {} }
    sync();
    dispatchEvent(new CustomEvent('apex:look', {detail: next}));
  }
  window.ApexLook = {get: () => look, set: v => apply(v, true), toggle: () => apply(look === 'futuristic' ? 'normal' : 'futuristic', true)};
  // Another tab changed it: follow.
  addEventListener('storage', e => { if (e.key === KEY) apply(LOOKS.includes(e.newValue) ? e.newValue : 'futuristic', false); });

  function mount() {
    // A page can offer its own switch (the study's header button). Otherwise a
    // small one in the corner — but not inside embedded frames (the companion
    // inside the board), where the host page already has one.
    if (!document.querySelector('[data-look-toggle]') && window.top === window) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'apex-look-toggle'; b.setAttribute('data-look-toggle', '');
      document.body.append(b);
    }
    for (const b of document.querySelectorAll('[data-look-toggle]')) b.addEventListener('click', () => window.ApexLook.toggle());
    sync();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
