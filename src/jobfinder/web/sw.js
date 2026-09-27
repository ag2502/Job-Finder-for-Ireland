// Sorted Place's service worker: what lets the site be installed, and what it can still
// show with no signal (on the Luas, underground, abroad without data).
//
// Three rules, in order of what matters:
//
// 1. Pages are always asked for from the network first. Job lists change every six hours
//    and an advert can close at any time, so a cached page is only ever a fallback, never
//    a shortcut. Nothing POSTed, and nothing from another site, is touched at all.
// 2. The site's own scripts and icons carry their version in their names, so they are
//    safe to serve from the cache without asking.
// 3. With no network, a page seen before (a search, a job, the saved list) comes back as
//    it was, and anything else gets the offline page, which lists what is kept.
//
// Signing out empties the kept pages (base.html), so the next person on the same phone
// does not find the last one's saved list waiting for them offline.

const PAGES = 'sp-pages-1';
const STATIC = 'sp-static-1';
const KEEP_PAGES = 40;
const PRECACHE = [
  '/offline',
  '/static/htmx-1.9.12.min.js',
  '/static/icon-192-1.png',
  '/static/apple-touch-icon-1.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== PAGES && k !== STATIC).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// Oldest first out, so the kept pages never grow past a phone's worth.
async function trim(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - KEEP_PAGES; i++) await cache.delete(keys[i]);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(STATIC).then((cache) => cache.put(request, copy));
        }
        return response;
      }))
    );
    return;
  }

  // Only whole pages. htmx's own requests (results, rows, the job panel) are parts of a
  // page and meaningless on their own, and the sign-in flow must never be replayed.
  const isPage = request.mode === 'navigate' && !request.headers.get('HX-Request');
  if (!isPage || url.pathname.startsWith('/auth') || url.pathname === '/login'
      || url.pathname === '/signup') return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && response.type === 'basic') {
          const copy = response.clone();
          caches.open(PAGES).then((cache) => cache.put(request, copy).then(() => trim(cache)));
        }
        return response;
      })
      .catch(() => caches.match(request, { cacheName: PAGES })
        .then((hit) => hit || caches.match('/offline')))
  );
});
