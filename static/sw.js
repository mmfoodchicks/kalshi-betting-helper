// Service worker: NETWORK-FIRST for the app shell so code/UI updates are always
// picked up immediately when online (the previous cache-first version could
// serve a stale app.js after an update). The cache is only an offline fallback.
// Live /api/ data is always network. Bumping SHELL purges every older cache.
const SHELL = "vigil-shell-v29";
const PRECACHE = ["/", "/static/style.css", "/static/app.js",
                  "/static/icon-192.png", "/static/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).catch(() => {})
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  // Live data: always network, never cached.
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response("{}", { headers: { "Content-Type": "application/json" } })));
    return;
  }
  // App shell: network-first (fresh code wins), fall back to cache when offline.
  e.respondWith(
    fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(e.request).then((hit) => hit || caches.match("/")))
  );
});
