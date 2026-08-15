/* Apex service worker — offline app shell + Web Push receiver.
 * Served from the origin root (/sw.js) so its scope covers the whole app.
 */
const CACHE = 'apex-shell-v26';
const SHELL = [
  '/',
  '/static/styles.css?v=omni26',
  '/static/mobile.css?v=omni26',
  '/static/app.js?v=omni26',
  '/static/voice-mobile.js?v=omni26',
  '/static/cst3d.js?v=omni26',
  '/static/marked.min.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // Never cache API calls, websockets, or cross-origin CDN requests.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) return;

  if (req.mode === 'navigate') {
    // Network-first for the shell so updates land when online.
    event.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put('/', copy)).catch(() => {});
        return res;
      }).catch(() => caches.match('/'))
    );
    return;
  }
  // Cache-first for static assets.
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => hit))
  );
});

/* ---- Web Push ---- */
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_e) { data = { body: event.data && event.data.text() }; }
  const title = data.title || 'Apex';
  const options = {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: data.tag || data.dedup_key || undefined,
    renotify: !!data.renotify,
    data: { url: data.url || '/', kind: data.kind || 'info' },
    requireInteraction: data.priority === 'high',
  };
  // If a dashboard/PWA window is focused it already shows the WebSocket in-app
  // toast — skip the OS notification to avoid a double (unless high priority).
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      const focused = list.some((c) => c.focused);
      if (focused && data.priority !== 'high') return;
      return self.registration.showNotification(title, options);
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ('focus' in client) {
          client.navigate(target).catch(() => {});
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
