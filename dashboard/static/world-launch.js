/* Keep World View separate from Command; unload its renderer when closed. */
(() => {
  const entry = document.getElementById('world-open');
  if (!entry || typeof HTMLDialogElement === 'undefined') return;
  const dialog = document.createElement('dialog');
  dialog.className = 'world-dialog';
  dialog.setAttribute('aria-label', 'World View');
  document.body.append(dialog);
  let frame = null;
  function close() {
    if (!dialog.open) return;
    dialog.close();
  }
  dialog.addEventListener('close', () => {
    // pagehide saves camera state synchronously before disposal.
    frame?.remove();
    frame = null;
    entry.focus();
  });
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === frame?.contentWindow &&
        event.data?.type === 'apex.world.close') close();
  });
  entry.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (dialog.open) return;
    frame = document.createElement('iframe');
    frame.title = 'Apex World View';
    frame.src = '/world';
    dialog.append(frame);
    dialog.showModal();
    frame.focus();
  });
})();
