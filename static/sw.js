// Minimal service worker: cache the app shell for fast/offline loads, but always
// go to the network for live data (markets, prices, signals) so nothing is stale.
const SHELL = "kalshi-helper-shell-v2";
const SHELL_FILES = ["/", "/static/style.css", "/static/app.js",
                     "/static/icon-192.png", "/static/manifest.json",
                     "/static/img/action-1.jpg", "/static/img/action-2.jpg",
                     "/static/img/action-3.jpg", "/static/img/action-4.jpg",
                     "/static/img/action-5.jpg", "/static/img/action-6.jpg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Live data must never be cached.
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() => new Response("{}", { headers: { "Content-Type": "application/json" } })));
    return;
  }
  // App shell: serve from cache, fall back to network and update the cache.
  e.respondWith(
    caches.match(e.request).then((hit) =>
      hit || fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match("/")))
  );
});
