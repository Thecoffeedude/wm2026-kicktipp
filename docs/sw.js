// Cache version — bump to bust old caches when shell changes
const CACHE_VERSION = 'wm26-v14';
const DATA_CACHE    = 'wm26-data-v4';
const FLAG_CACHE    = 'wm26-flags-v4';

// App shell: all files needed for offline render
const SHELL = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.json',
  './icons/icon.svg',
  './icons/apple-touch-icon.png',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-512-maskable.png',
];

// JSON data files served network-first (fresh online, cached offline)
const DATA_FILES = ['data.json', 'live.json', 'results.json'];

// ── Install: pre-cache shell ──────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())   // activate immediately
  );
});

// ── Activate: clean stale caches ──────────────────────────────────────────
self.addEventListener('activate', event => {
  const keep = new Set([CACHE_VERSION, DATA_CACHE, FLAG_CACHE]);
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => !keep.has(k)).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())  // take control of all tabs
  );
});

// ── Fetch routing ─────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET requests for our own origin + flagcdn
  if (request.method !== 'GET') return;
  const isImageCDN = url.hostname === 'flagcdn.com' || url.hostname === 'r2.thesportsdb.com';
  if (url.origin !== self.location.origin && !isImageCDN) return;

  if (DATA_FILES.some(f => url.pathname.endsWith(f))) {
    // JSON data: network-first → fresh predictions online, cached offline
    event.respondWith(networkFirst(request, DATA_CACHE));
    return;
  }

  if (isImageCDN) {
    // Flags/Wappen: stale-while-revalidate — serve cached, refresh in background
    event.respondWith(staleWhileRevalidate(request, FLAG_CACHE));
    return;
  }

  // Shell files: cache-first → instant load, background update
  event.respondWith(cacheFirst(request, CACHE_VERSION));
});

// ── Strategies ────────────────────────────────────────────────────────────

async function networkFirst(request, cacheName) {
  // Normalize the cache key: strip query strings (?_=<timestamp> cache
  // busters) so polling doesn't grow the cache by one entry per request.
  const url = new URL(request.url);
  const cacheKey = url.origin + url.pathname;
  try {
    const res = await fetch(request);
    if (res.ok) {
      const cache = await caches.open(cacheName);
      cache.put(cacheKey, res.clone());
    }
    return res;
  } catch {
    const cached = await caches.match(cacheKey);
    return cached ?? new Response(JSON.stringify({ error: 'offline' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) {
    // Refresh in background (stale shell gets updated next visit)
    fetch(request).then(res => {
      if (res?.ok) caches.open(cacheName).then(c => c.put(request, res));
    }).catch(() => {});
    return cached;
  }
  try {
    const res = await fetch(request);
    if (res.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, res.clone());
    }
    return res;
  } catch {
    // Offline and not in cache — navigation fallback
    if (request.mode === 'navigate') {
      const fallback = await caches.match('./index.html');
      if (fallback) return fallback;
    }
    return new Response('Offline', { status: 503 });
  }
}

async function staleWhileRevalidate(request, cacheName) {
  const cached = await caches.match(request);
  const networkPromise = fetch(request).then(res => {
    if (res?.ok) caches.open(cacheName).then(c => c.put(request, res.clone()));
    return res;
  }).catch(() => null);
  return cached ?? (await networkPromise) ?? new Response('', { status: 503 });
}

// ── "New version available" signal to clients ─────────────────────────────
// After activate, tell all open clients they might be on a fresh shell
self.addEventListener('activate', event => {
  event.waitUntil(
    self.clients.matchAll({ includeUncontrolled: true, type: 'window' })
      .then(clients => clients.forEach(c => c.postMessage({ type: 'SW_UPDATED' })))
  );
});
