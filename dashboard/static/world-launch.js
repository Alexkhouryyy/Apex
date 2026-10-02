/* Earth is a portal; drag remains orbit navigation. Load World View on entry. */
(() => {
  const entry = document.getElementById('world-open');
  const commandEntry = document.getElementById('world-command-open');
  const globe = document.getElementById('globe');
  const portal = document.getElementById('world-portal');
  if (!entry || typeof HTMLDialogElement === 'undefined' || !HTMLDialogElement.prototype.showModal) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'apex-world-dialog';
  dialog.className = 'world-dialog';
  dialog.setAttribute('aria-label', 'World View');
  document.body.append(dialog);
  [entry, commandEntry].filter(Boolean).forEach(link => link.setAttribute('aria-controls', dialog.id));
  let frame = null, returnTo = entry, body = 'earth', gesture = null;
  const pointers = new Set();
  const visibility = open => window.dispatchEvent(new CustomEvent('apex:world-visibility', {detail:{open}}));
  // Capture the whole gesture: a dragged or multi-touch globe must never open.
  globe?.addEventListener('pointerdown', event => {
    if (!pointers.size) gesture = {id:event.pointerId, x:event.clientX, y:event.clientY, invalid:event.button !== 0, ended:0};
    else if (gesture) gesture.invalid = true;
    pointers.add(event.pointerId);
  }, true);
  function motion(event) {
    if (gesture?.id === event.pointerId && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > 8) gesture.invalid = true;
  }
  window.addEventListener('pointermove', motion, true);
  window.addEventListener('pointerup', event => {
    motion(event);
    if (pointers.delete(event.pointerId) && gesture) gesture.ended = Date.now();
  }, true);
  window.addEventListener('pointercancel', event => {
    if (pointers.delete(event.pointerId) && gesture) gesture.invalid = true;
  }, true);
  window.addEventListener('blur', () => {pointers.clear();if (gesture) gesture.invalid = true;});
  function close() { if (dialog.open) dialog.close(); }
  function open(source = entry) {
    if (dialog.open) return;
    returnTo = source === commandEntry ? commandEntry : entry;
    const rect = globe?.getBoundingClientRect();
    if (rect?.width && rect?.height) {
      dialog.style.setProperty('--world-portal-x', (rect.left + rect.width / 2) + 'px');
      dialog.style.setProperty('--world-portal-y', (rect.top + rect.height / 2) + 'px');
      dialog.style.setProperty('--world-portal-radius', Math.min(rect.width, rect.height) * .34 + 'px');
    }
    frame = document.createElement('iframe');
    frame.title = 'Apex World View'; frame.src = '/world';
    frame.allow = 'microphone; autoplay; fullscreen; usb';
    dialog.append(frame);
    try { dialog.showModal(); }
    catch (error) {frame.remove(); frame = null; location.assign('/world'); return;}
    visibility(true); frame.focus();
  }
  dialog.addEventListener('close', () => {
    // pagehide saves camera state synchronously before disposal.
    frame?.remove(); frame = null; visibility(false); returnTo.focus();
  });
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === frame?.contentWindow && event.data?.type === 'apex.world.close') close();
  });
  [entry, commandEntry].filter(Boolean).forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); open(link);
  }));
  window.ApexWorldView = {
    open,
    openFromGlobe(event) {
      if (body !== 'earth' || !gesture || gesture.invalid || pointers.size || !gesture.ended || Date.now() - gesture.ended > 750) return;
      if (event && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return;
      gesture.invalid = true; open(entry);
    },
    openFromEarthSelector() { open(entry); },
    setBody(key, label) {
      body = key; if (portal) portal.dataset.body = key;
      const hint = document.getElementById('world-entry-hint');
      if (hint) hint.textContent = key === 'earth' ? 'Click Earth to explore' : 'Explore Earth';
      globe?.setAttribute('aria-label', key === 'earth' ? 'Earth globe. Click Earth to enter World View; drag to rotate.' : (label || key) + ' globe. Drag to rotate.');
    },
  };
})();
