// Service worker: NETWORK-FIRST for the app shell so code/UI updates are always
// picked up immediately when online (the previous cache-first version could
// serve a stale app.js after an update). The cache is only an offline fallback.
// Live /api/ data is always network. Bumping SHELL purges every older cache.
const SHELL = "vigil-shell-v103";
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
  //
  // A failed API call used to be answered with a 200 and an empty body. That is
  // the single worst thing to hand a caller: `{}` is not an error, so every
  // reader sailed past its error check and died on the first field it touched
  // -- `d.games.length` on undefined -- and the page rendered the generic
  // "Failed to load slate" with no clue that the request had simply not
  // happened. Coming back to the app after a few minutes away (the phone drops
  // the socket while backgrounded) hit this basically every time.
  //
  // Now: one transparent retry, because the resume case is a single dropped
  // connection and the second attempt almost always lands. If that fails too,
  // answer with a REAL failure status and a labelled body so callers can tell
  // "the network blinked" from "the server said no" and retry instead of
  // painting a dead screen.
  if (url.pathname.startsWith("/api/")) {
    // Clone BEFORE the first fetch: a request body is single-use, so retrying
    // with the same object throws "body already used" on every POST -- the DFS
    // builds among them -- and the retry silently never happened for exactly
    // the requests that carry data.
    const retryReq = e.request.clone();
    e.respondWith(
      fetch(e.request)
        .catch(() => new Promise((r) => setTimeout(r, 700)).then(() => fetch(retryReq)))
        .catch(() => new Response(
          JSON.stringify({ error: "network unavailable", offline: true }),
          { status: 503, headers: { "Content-Type": "application/json" } })));
    return;
  }
  // App shell: network-first (fresh code wins), fall back to cache when offline.
  //
  // A gateway error is a RESOLVED fetch, not a rejected one, so the old version
  // treated a 502 as a good response: it cached the host's error page under "/"
  // and handed it back as the app. Restarting the server (every deploy does)
  // could therefore leave a 502 page pinned in the shell cache. A non-OK
  // response now falls back to the last good copy exactly like being offline
  // does, which is also what makes a redeploy invisible instead of a gateway
  // error -- the cached shell loads and its API calls hit the new server.
  const shellFallback = () =>
    caches.match(e.request).then((hit) => hit || caches.match("/"));
  e.respondWith(
    fetch(e.request).then((res) => {
      if (!res.ok) return shellFallback().then((hit) => hit || res);
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(shellFallback)
  );
});
