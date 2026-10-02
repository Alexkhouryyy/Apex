(() => {
  const loading = document.getElementById('loading');
  const retry = document.getElementById('retry');
  let frame, cancelled = false;
  async function open() {
    retry.hidden = true;
    document.getElementById('error').textContent = '';
    let token = '';
    try { token = localStorage.getItem('apex_token') || ''; } catch (_) {}
    try {
      const response = await fetch('/api/world/engine/session', {method:'POST', credentials:'same-origin',
        headers: token ? {Authorization:'Bearer ' + token} : {}});
      if (cancelled) return;
      if (response.status === 401) {
        document.getElementById('signin').style.display = 'block';
        throw new Error('Sign in with your Apex access token to continue.');
      }
      if (!response.ok) throw new Error('World View could not open. Retry or check Apex’s engine log.');
      frame = document.createElement('iframe');
      frame.title = 'Apex World View — powered by God’s Eye View';
      frame.allow = 'microphone; autoplay; fullscreen; usb';
      frame.src = '/world/engine/' + location.search + location.hash;
      loading.hidden = true;
      document.body.append(frame);
      frame.focus();
    } catch (error) {
      document.getElementById('error').textContent = error.message;
      retry.hidden = false;
    }
  }
  function close() {
    if (window.parent !== window) window.parent.postMessage({type:'apex.world.close'}, location.origin);
    else location.assign('/');
  }
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === frame?.contentWindow && event.data?.type === 'apex.world.close') close();
  });
  document.getElementById('signin').addEventListener('submit', event => {
    event.preventDefault();
    try { localStorage.setItem('apex_token', document.getElementById('token').value.trim()); } catch (_) {}
    open();
  });
  retry.addEventListener('click', open);
  window.addEventListener('pagehide', () => {cancelled = true; frame?.remove();});
  open();
})();
