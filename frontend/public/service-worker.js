const CACHE_NAME = "audioflow-pwa-v10";
const RUNTIME_CACHE = "audioflow-runtime-v9";
const CORE_ASSETS = [
  "/",
  "/?source=pwa&v=m",
  "/offline.html",
  "/manifest.webmanifest",
  "/runtime-env.js",
  "/favicon.svg",
  "/favicon.ico",
  "/apple-touch-icon.png",
  "/pwa/icon-72.png",
  "/pwa/icon-96.png",
  "/pwa/icon-128.png",
  "/pwa/icon-144.png",
  "/pwa/icon-152.png",
  "/pwa/icon-180.png",
  "/pwa/icon-192.png",
  "/pwa/icon-384.png",
  "/pwa/icon-512.png",
  "/pwa/maskable-icon-192.png",
  "/pwa/maskable-icon-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.allSettled(CORE_ASSETS.map((asset) => cache.add(new Request(asset, {cache: "reload"}))))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => ![CACHE_NAME, RUNTIME_CACHE].includes(key)).map((key) => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ok: false, error: "当前离线，接口不可用"}), {
          headers: {"Content-Type": "application/json"}
        })
      )
    );
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(RUNTIME_CACHE).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(() => caches.match("/") || caches.match("/offline.html"))
    );
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/pwa/") || url.pathname.startsWith("/platform-logos/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
        const copy = response.clone();
        caches.open(RUNTIME_CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      }))
    );
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(RUNTIME_CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/offline.html")))
  );
});
